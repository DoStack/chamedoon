"""Mini App housing search: filters, removable chips, live count, saved searches and the «گوش به زنگ» page."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.test import RequestFactory, TestCase
from django.utils import timezone

from home import search as home_search
from home.alerts import ALL_FIELDS, MAX_ALERTS_PER_USER, matches, metro_channel
from home.models import AlertTerm, HomeAlert, HomeListing, ListingStatus
from miniapp import home_views
from miniapp.auth import startapp_from_request, startapp_path
from tests.test_home_miniapp import MiniAppCase

TODAY = date.today()


class SearchCase(MiniAppCase):
    def ca_home(self, number: int, **fields) -> HomeListing:
        listing = self.ca_listing(number, city="Toronto", area=fields.pop("area", "Downtown"))
        return self.set(listing, **fields)

    def de_home(self, number: int, **fields) -> HomeListing:
        listing = self.de_listing(number, city="Berlin", area=fields.pop("area", "Neukölln"))
        return self.set(listing, **fields)

    def set(self, listing: HomeListing, **fields) -> HomeListing:
        if fields:
            HomeListing.objects.filter(pk=listing.pk).update(**fields)
            listing.refresh_from_db()
        return listing

    def ids(self, query: str, *listings: HomeListing) -> set[int]:
        fragment = self.feed(query)
        return {item.pk for item in listings if f'href="/app/home/{item.pk}/"' in fragment}

    def page(self, query: str) -> str:
        return self.client.get(f"/app/home/list/{query}").content.decode()

    def bot_alert(self, **fields) -> HomeAlert:
        values = {
            "telegram_user_id": self.user.telegram_user_id,
            "chat_id": self.user.telegram_user_id,
            "country": "CA",
            "city": "Toronto",
            "answered": list(ALL_FIELDS),
            "complete": True,
        }
        values.update(fields)
        return HomeAlert.objects.create(**values)


class FilterTests(SearchCase):
    def test_home_type_budget_and_several_neighbourhoods(self) -> None:
        room = self.ca_home(1, property_type="room", price_amount=900, area="Downtown")
        studio = self.ca_home(2, property_type="studio", price_amount=1400, area="Scarborough")
        flat = self.ca_home(3, property_type="apartment", price_amount=2100, area="Yonge & Finch")
        everyone = (room, studio, flat)
        self.assertEqual(self.ids("?country=CA&type=room&type=studio", *everyone), {room.pk, studio.pk})
        self.assertEqual(self.ids("?country=CA&min=1000&max=2000", *everyone), {studio.pk})
        self.assertEqual(self.ids("?country=CA&area=Downtown&area=finch", *everyone), {room.pk, flat.pk})
        self.assertEqual(self.ids("?country=CA&area=downtown&area=Downtown", *everyone), {room.pk})

    def test_rental_mode_is_its_own_filter_per_country(self) -> None:
        lease = self.ca_home(1, rental_mode="long_term")
        sublet = self.ca_home(2, rental_mode="sublet", available_to=TODAY + timedelta(days=90))
        transfer = self.ca_home(3, rental_mode="lease_transfer")
        short = self.ca_home(4, rental_mode="short_term", price_period="night", price_amount=70)
        everyone = (lease, sublet, transfer, short)
        self.assertEqual(self.ids("?country=CA&term=sublet", *everyone), {sublet.pk})
        self.assertEqual(self.ids("?country=CA&term=lease_transfer", *everyone), {transfer.pk})
        self.assertEqual(self.ids("?country=CA&term=short", *everyone), {short.pk})
        self.assertEqual(self.ids("?country=CA&term=lease", *everyone), {lease.pk, transfer.pk})
        canada = self.page("?country=CA&city=Toronto")
        for label in ("اجاره‌ی بلندمدت", "سابلت", "انتقال لیز", "کوتاه‌مدت"):
            self.assertIn(label, canada)
        germany = self.page("?country=DE&city=Berlin")
        self.assertIn("موقت (Zwischenmiete)", germany)
        self.assertIn("Nachmieter", germany)
        self.assertNotIn('data-term="short"', germany)

    def test_furnished_and_owner_only(self) -> None:
        furnished = self.ca_home(1, furnished="yes")
        partial = self.ca_home(2, furnished="partial", lister_type="agent")
        bare = self.ca_home(3, furnished="no")
        everyone = (furnished, partial, bare)
        self.assertEqual(self.ids("?country=CA&furnished=1", *everyone), {furnished.pk, partial.pk})
        self.assertEqual(self.ids("?country=CA&owner=1", *everyone), {furnished.pk, bare.pk})

    def test_canada_utilities_and_roommate_gender(self) -> None:
        bills = self.ca_home(1, bills_included="yes", price_basis="unknown", roommate_gender="female")
        warm = self.ca_home(2, bills_included="unknown", price_basis="warm", roommate_gender="male")
        extra = self.ca_home(3, bills_included="no", price_basis="cold", roommate_gender="couple")
        open_flat = self.ca_home(4, bills_included="no", property_type="apartment", roommate_gender="any")
        single = self.ca_home(5, bills_included="no", property_type="apartment", roommate_gender="any", occupants_wanted=1)
        flagged = self.ca_home(6, bills_included="no", property_type="room", roommate_gender="any", flags={"couples_ok": True})
        room = self.ca_home(7, bills_included="no", property_type="room", roommate_gender="any")
        everyone = (bills, warm, extra, open_flat, single, flagged, room)
        self.assertEqual(self.ids("?country=CA&bills=1", *everyone), {bills.pk, warm.pk})
        self.assertEqual(self.ids("?country=CA&gender=female", *everyone), {bills.pk})
        self.assertEqual(self.ids("?country=CA&gender=male", *everyone), {warm.pk})
        self.assertEqual(self.ids("?country=CA&gender=couple", *everyone), {extra.pk, open_flat.pk, flagged.pk})

    def test_germany_anmeldung_warm_rent_and_wbs(self) -> None:
        anmeldung = self.de_home(1, registration="yes", price_basis="warm")
        cold = self.de_home(2, registration="no", price_basis="cold")
        unknown = self.de_home(3, registration="unknown", price_basis="unknown", flags={"wgb": True})
        plain = self.de_home(4, registration="unknown", price_basis="unknown", flags={})
        everyone = (anmeldung, cold, unknown, plain)
        self.assertEqual(self.ids("?country=DE&reg=1", *everyone), {anmeldung.pk})
        # «warm only» leaves out listings priced cold.
        self.assertEqual(self.ids("?country=DE&warm=1", *everyone), {anmeldung.pk, unknown.pk, plain.pk})
        self.assertEqual(self.ids("?country=DE&nowbs=1", *everyone), {anmeldung.pk, cold.pk, plain.pk})

    def test_country_fields_only_apply_in_their_country(self) -> None:
        canada = self.page("?country=CA&city=Toronto")
        self.assertIn('name="bills"', canada)
        self.assertIn('name="gender"', canada)
        for name in ("reg", "warm", "nowbs"):
            self.assertNotIn(f'name="{name}"', canada)
        germany = self.page("?country=DE&city=Berlin")
        for name in ("reg", "warm", "nowbs"):
            self.assertIn(f'name="{name}"', germany)
        self.assertNotIn('name="bills"', germany)
        self.assertNotIn('name="gender"', germany)
        cold = self.de_home(1, bills_included="no", price_basis="cold", roommate_gender="male")
        self.assertEqual(self.ids("?country=DE&bills=1&gender=female", cold), {cold.pk})
        listing = self.ca_home(2, registration="no", price_basis="cold")
        self.assertEqual(self.ids("?country=CA&reg=1&warm=1&nowbs=1", listing), {listing.pk})

    def test_move_in_date_has_a_one_week_window(self) -> None:
        move = TODAY + timedelta(days=30)
        early = self.ca_home(1, available_from=move - timedelta(days=7))
        late = self.ca_home(2, available_from=move + timedelta(days=7))
        too_early = self.ca_home(3, available_from=move - timedelta(days=8))
        too_late = self.ca_home(4, available_from=move + timedelta(days=8))
        undated = self.ca_home(5, available_from=None)
        everyone = (early, late, too_early, too_late, undated)
        self.assertEqual(self.ids(f"?country=CA&move={move.isoformat()}", *everyone), {early.pk, late.pk})
        self.assertEqual(len(self.ids("?country=CA&move=not-a-date", *everyone)), 5)
        page = self.page(f"?country=CA&move={move.isoformat()}")
        self.assertIn(f'name="move" aria-label="تاریخ ورود" value="{move.isoformat()}"', page)
        self.assertIn("± ۱ هفته", page)

    def test_old_available_by_links_still_work(self) -> None:
        soon = self.ca_home(1, available_from=TODAY + timedelta(days=5))
        later = self.ca_home(2, available_from=TODAY + timedelta(days=60))
        self.assertEqual(self.ids(f"?country=CA&by={(TODAY + timedelta(days=10)).isoformat()}", soon, later), {soon.pk})

    def test_sort_newest_first_or_cheapest_first(self) -> None:
        dear = self.ca_home(1, price_amount=2000)
        cheap = self.ca_home(2, price_amount=800)
        unpriced = self.ca_home(3, price_amount=None)
        HomeListing.objects.filter(pk=dear.pk).update(last_seen=timezone.now())
        newest = self.feed("?country=CA")
        self.assertLess(newest.index(f"/app/home/{dear.pk}/"), newest.index(f"/app/home/{cheap.pk}/"))
        cheapest = self.feed("?country=CA&sort=price")
        self.assertLess(cheapest.index(f"/app/home/{cheap.pk}/"), cheapest.index(f"/app/home/{dear.pk}/"))
        self.assertLess(cheapest.index(f"/app/home/{dear.pk}/"), cheapest.index(f"/app/home/{unpriced.pk}/"))
        page = self.page("?country=CA&sort=price")
        self.assertIn("ارزان‌ترین اول", page)
        self.assertIn('<input type="hidden" name="sort" value="price">', page)

    def test_expired_and_rented_listings_stay_out_and_cards_show_relative_time(self) -> None:
        shown = self.ca_home(1)
        expired = self.ca_home(2, status=ListingStatus.EXPIRED)
        rented = self.ca_home(3, status=ListingStatus.RENTED)
        fragment = self.feed("?country=CA")
        self.assertEqual(self.ids("?country=CA", shown, expired, rented), {shown.pk})
        self.assertIn("ساعت پیش", fragment)
        self.assertEqual(json.loads(self.client.get("/app/home/list/count/?country=CA").content)["count"], 1)

    def test_search_rules_match_the_saved_search_rules(self) -> None:
        """`search_q` (the feed) and `fits` (alerts) agree on every filter."""
        move = TODAY + timedelta(days=20)
        rows = [
            self.ca_home(1, furnished="yes", bills_included="yes", roommate_gender="female", available_from=move),
            self.ca_home(2, furnished="no", lister_type="agent", price_basis="warm", roommate_gender="male"),
            self.ca_home(3, property_type="apartment", roommate_gender="any", available_from=move + timedelta(days=9)),
            self.ca_home(4, property_type="room", roommate_gender="couple", rental_mode="lease_transfer"),
            self.ca_home(5, rental_mode="sublet", available_to=TODAY + timedelta(days=60), summary_fa="نزدیک مترو"),
            self.de_home(6, registration="yes", price_basis="cold", flags={"wgb": True}),
            self.de_home(7, registration="no", price_basis="warm", flags={}),
        ]
        cases = [
            {"furnished": True},
            {"owner": True},
            {"bills": True},
            {"warm": True},
            {"reg": True},
            {"nowbs": True},
            {"gender": "female"},
            {"gender": "male"},
            {"gender": "couple"},
            {"move": move},
            {"by": move},
            {"q": "مترو"},
            {"term": "lease_transfer"},
            {"term": "sublet"},
            {"term": "lease"},
        ]
        for case in cases:
            for country in ("CA", "DE"):
                with self.subTest(case=case, country=country):
                    filters = {**home_views.CLEARED, "country": country, "kind": "offer", "sort": "new", **case}
                    in_feed = set(home_views.feed_queryset(filters).values_list("pk", flat=True))
                    fitting = {row.pk for row in rows if row.country == country and home_search.fits(home_search.to_search(case), row)}
                    self.assertEqual(in_feed, fitting)

    def test_saved_search_json_round_trips(self) -> None:
        filters = {"types": ["room"], "min": 500, "max": 1500, "areas": ["Downtown"], "term": "sublet", "furnished": True, "move": TODAY, "gender": "female", "owner": False}
        saved = home_search.to_search(filters)
        self.assertEqual(saved["move"], TODAY.isoformat())
        self.assertNotIn("owner", saved)
        back = home_search.from_search(saved)
        self.assertEqual(back["move"], TODAY)
        self.assertEqual(back["areas"], ["Downtown"])
        self.assertTrue(back["furnished"])
        junk = home_search.from_search({"min": "x", "term": "bogus", "gender": "robot", "move": "soon"})
        self.assertEqual((junk["min"], junk["term"], junk["gender"], junk["move"]), (None, "", "", None))
        self.assertTrue(home_search.fits({}, HomeListing()))


class ChipTests(SearchCase):
    def chip_hrefs(self, page: str) -> dict[str, list[str]]:
        import re

        found: dict[str, list[str]] = {}
        for href, key in re.findall(r'class="house-filter-chip" role="listitem" href="([^"]+)" data-chip="([a-z]+)"', page):
            found.setdefault(key, []).append(href.replace("&amp;", "&"))
        return found

    def test_each_chip_removes_only_its_filter(self) -> None:
        self.ca_home(1)
        move = (TODAY + timedelta(days=14)).isoformat()
        query = f"?country=CA&city=Toronto&type=room&max=1200&area=Downtown&area=Scarborough&term=sublet&furnished=1&bills=1&gender=female&move={move}&q=metro&owner=1"
        page = self.page(query)
        chips = self.chip_hrefs(page)
        self.assertEqual(
            set(chips), {"city", "type", "price", "area", "term", "furnished", "bills", "gender", "move", "q", "owner"}
        )
        self.assertEqual(len(chips["area"]), 2)
        self.assertIn('<span class="filter-count">12</span>', page)
        self.assertIn("+ فیلتر", page)
        self.assertIn("ورود", page)

        def params(href: str) -> dict[str, list[str]]:
            return parse_qs(urlparse(href).query)

        full = params(f"/app/home/list/{query}")
        for key, hrefs in chips.items():
            for href in hrefs:
                left = params(href)
                self.assertTrue(href.startswith("/app/home/list/?"))
                self.assertEqual(left["country"], ["CA"])
                gone = {name for name in full if name not in left or left[name] != full[name]}
                expected = {"city": {"city", "area"}, "type": {"type"}, "price": {"max"}, "area": {"area"}, "term": {"term"}, "q": {"q"}}.get(key, {key})
                self.assertEqual(gone, expected, key)
        removed_one = params(chips["area"][0])
        self.assertEqual(len(removed_one["area"]), 1)

    def test_germany_chips_and_price_range(self) -> None:
        self.de_home(1)
        page = self.page("?country=DE&city=Berlin&min=400&max=900&reg=1&warm=1&nowbs=1&term=lease_transfer")
        chips = self.chip_hrefs(page)
        self.assertEqual(set(chips), {"city", "price", "reg", "warm", "nowbs", "term"})
        self.assertIn("400 €", page)
        self.assertIn("ملد (Anmeldung)", page)
        self.assertIn("بدون WBS", page)
        self.assertIn("Nachmieter", page)

    def test_no_filters_no_chips_but_the_add_button(self) -> None:
        page = self.page("?country=CA&city=")
        self.assertEqual(self.chip_hrefs(page), {})
        self.assertNotIn('class="filter-count"', page)
        self.assertIn("house-filter-chip is-add", page)

    def test_english_chips(self) -> None:
        self.login(self.user, "en")
        page = self.page(f"?country=CA&city=Toronto&max=1000&move={TODAY.isoformat()}&gender=couple&bills=1")
        for label in ("Up to", "Move in", "± 1 week", "Utilities included", "Suits a couple", "Show 0 listings", "Newest first"):
            self.assertIn(label, page)


class CountTests(SearchCase):
    def test_count_endpoint_follows_the_filters(self) -> None:
        self.ca_home(1, price_amount=900, furnished="yes")
        self.ca_home(2, price_amount=1900, furnished="no")
        self.ca_home(3, price_amount=1300, furnished="yes")
        data = json.loads(self.client.get("/app/home/list/count/?country=CA&furnished=1&max=1500").content)
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["label"], "نمایش ۲ آگهی")
        everything = self.client.get("/app/home/list/count/?country=CA")
        self.assertEqual(json.loads(everything.content)["count"], 3)
        self.assertEqual(everything["Cache-Control"], "private, no-store")
        page = self.page("?country=CA&furnished=1")
        self.assertIn("نمایش ۲ آگهی", page)
        self.assertIn('data-count-url="/app/home/list/count/"', page)
        self.assertIn("۲ آگهی", page)

    def test_count_needs_a_login(self) -> None:
        self.client.logout()
        self.assertEqual(self.client.get("/app/home/list/count/?country=CA").status_code, 302)

    def test_count_is_cached_per_request(self) -> None:
        self.ca_home(1)
        request = RequestFactory().get("/app/home/list/?country=CA")
        filters = {**home_views.CLEARED, "country": "CA", "kind": "offer", "sort": "new"}
        with self.assertNumQueries(1):
            self.assertEqual(home_views.feed_count(request, filters), 1)
        with self.assertNumQueries(0):
            self.assertEqual(home_views.feed_count(request, filters), 1)

    def test_price_histogram_of_this_week(self) -> None:
        for number, price in enumerate((700, 800, 900, 1000, 1100, 1300, 1500), start=1):
            self.ca_home(number, price_amount=price)
        page = self.page("?country=CA&city=Toronto&max=1000")
        self.assertIn('class="house-hist"', page)
        self.assertIn('class="is-in"', page)
        self.assertIn("تعداد آگهی‌های این هفته", page)
        self.assertEqual(home_search.histogram([Decimal(500)] * 3, None, None), [])
        bars = home_search.histogram([Decimal(value) for value in (100, 200, 300, 400, 500, 5000)], 150, 250)
        self.assertEqual(len(bars), home_search.HISTOGRAM_BINS)
        self.assertTrue(any(bar["in"] for bar in bars))
        self.assertEqual(max(bar["height"] for bar in bars), 100)
        self.assertEqual(home_search.histogram([Decimal(5)] * 6, None, None), [])

    def test_no_histogram_for_wanted_posts(self) -> None:
        self.assertEqual(home_views.price_bars({"kind": "wanted"}), [])


class SaveAlertTests(SearchCase):
    def save(self, query: str):
        return self.client.post(f"/app/home/alerts/save/{query}")

    def test_the_floating_button_saves_the_search_as_an_alert(self) -> None:
        self.ca_home(1)
        move = (TODAY + timedelta(days=21)).isoformat()
        query = f"?country=CA&city=North York&type=room&max=1200&area=Downtown&term=sublet&furnished=1&gender=female&move={move}"
        page = self.page(query)
        self.assertIn("خبرم کنید از آگهی‌های جدید این جستجو", page)
        self.assertIn('action="/app/home/alerts/save/?country=CA', page)
        response = self.save(query)
        alert = HomeAlert.objects.get(telegram_user_id=self.user.telegram_user_id)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"saved={alert.pk}", response["Location"])
        self.assertEqual(alert.city, "Toronto")
        self.assertEqual(alert.chat_id, self.user.telegram_user_id)
        self.assertEqual(alert.user, self.user)
        self.assertEqual(alert.property_types, ["room"])
        self.assertEqual(alert.max_price, Decimal("1200"))
        self.assertEqual(alert.currency, "CAD")
        self.assertEqual(alert.area, "Downtown")
        self.assertEqual(alert.term, AlertTerm.SUBLET)
        self.assertEqual(alert.gender, "female")
        # Set filters are answers and the rest are «مهم نیست»: every question is answered.
        self.assertEqual(sorted(alert.answered), sorted(ALL_FIELDS))
        self.assertTrue(alert.complete)
        self.assertEqual(alert.search["furnished"], True)
        self.assertEqual(alert.search["move"], move)
        self.assertIsNotNone(alert.viewed_at)
        banner = self.client.get(response["Location"]).content.decode()
        self.assertIn("گوش به زنگ ذخیره شد", banner)
        self.assertIn('href="/app/home/alerts/"', banner)

    def test_saving_the_same_search_twice_keeps_one_alert(self) -> None:
        self.save("?country=CA&city=Toronto&max=1500")
        self.save("?country=CA&city=Toronto&max=1500")
        self.assertEqual(HomeAlert.objects.count(), 1)
        alert = HomeAlert.objects.get()
        alert.active = False
        alert.save()
        self.save("?country=CA&city=Toronto&max=1500")
        alert.refresh_from_db()
        self.assertTrue(alert.active)

    def test_the_five_alert_cap_is_respected(self) -> None:
        for number in range(MAX_ALERTS_PER_USER):
            self.bot_alert(max_price=1000 + number)
        response = self.save("?country=CA&city=Toronto&max=900")
        self.assertIn("note=cap", response["Location"])
        self.assertEqual(HomeAlert.objects.count(), MAX_ALERTS_PER_USER)
        page = self.client.get(response["Location"]).content.decode()
        self.assertIn("۵ گوش به زنگ روشن دارید", page)
        # An alert switched off does not count; a saved request (wanted post) never does.
        HomeAlert.objects.filter(max_price=1000).update(active=False)
        self.assertIn("saved=", self.save("?country=CA&city=Toronto&max=900")["Location"])
        off = HomeAlert.objects.get(max_price=1000)
        self.assertIn("note=cap", self.save("?country=CA&city=Toronto&max=1000")["Location"])
        off.refresh_from_db()
        self.assertFalse(off.active)

    def test_a_city_is_needed_and_wanted_posts_cannot_be_alerted(self) -> None:
        page = self.page("?country=CA&city=")
        self.assertIn("?start=alert_home_ca", page)
        self.assertNotIn("/app/home/alerts/save/", page)
        response = self.save("?country=CA&city=")
        self.assertIn("note=city", response["Location"])
        self.assertIn("اول شهر را انتخاب کنید", self.client.get(response["Location"]).content.decode())
        self.assertIn("note=kind", self.save("?country=CA&city=Toronto&kind=wanted")["Location"])
        self.assertNotIn("house-fab", self.page("?country=CA&city=Toronto&kind=wanted"))
        self.assertFalse(HomeAlert.objects.exists())

    def test_users_without_telegram_cannot_save(self) -> None:
        user, error = home_views.save_search(type("Web", (), {"telegram_user_id": None})(), {"kind": "offer", "city": "Toronto"})
        self.assertEqual((user, error), (None, "account"))
        self.assertEqual(self.client.get("/app/home/alerts/save/?country=CA").status_code, 405)

    def test_a_saved_search_only_messages_what_the_feed_shows(self) -> None:
        self.save("?country=CA&city=Toronto&furnished=1&bills=1&max=1500")
        alert = HomeAlert.objects.get()
        channel = metro_channel("CA", "Toronto")
        fits = self.ca_home(1, furnished="yes", bills_included="yes", price_amount=1000)
        bare = self.ca_home(2, furnished="no", bills_included="yes", price_amount=1000)
        extra = self.ca_home(3, furnished="yes", bills_included="no", price_basis="cold", price_amount=1000)
        dear = self.ca_home(4, furnished="yes", bills_included="yes", price_amount=1900)
        self.assertTrue(matches(alert, fits, channel))
        for listing in (bare, extra, dear):
            self.assertFalse(matches(alert, listing, channel), listing.pk)
        self.assertEqual(self.ids("?country=CA&city=Toronto&furnished=1&bills=1&max=1500", fits, bare, extra, dear), {fits.pk})

    def test_saved_search_with_min_price_and_lease_transfer(self) -> None:
        self.save("?country=CA&city=Toronto&min=1000&term=lease_transfer")
        alert = HomeAlert.objects.get()
        self.assertEqual(alert.term, AlertTerm.ANY)
        channel = metro_channel("CA", "Toronto")
        transfer = self.ca_home(1, rental_mode="lease_transfer", price_amount=1500)
        cheap = self.ca_home(2, rental_mode="lease_transfer", price_amount=700)
        lease = self.ca_home(3, rental_mode="long_term", price_amount=1500)
        self.assertTrue(matches(alert, transfer, channel))
        self.assertFalse(matches(alert, cheap, channel))
        self.assertFalse(matches(alert, lease, channel))


class AlertsPageTests(SearchCase):
    def test_lists_bot_and_mini_app_alerts_with_their_state(self) -> None:
        bot = self.bot_alert(property_types=["room"], max_price=Decimal("1200"), area="North York, Richmond Hill", term=AlertTerm.LEASE)
        self.client.post("/app/home/alerts/save/?country=CA&city=Toronto&furnished=1")
        paused = self.bot_alert(country="DE", city="Berlin", paused_until=timezone.now() + timedelta(days=3), needs_registration=True)
        self.bot_alert(telegram_user_id=self.other.telegram_user_id, chat_id=self.other.telegram_user_id, city="Montreal")
        page = self.client.get("/app/home/alerts/").content.decode()
        self.assertIn("۳ از ۵ روشن", page)
        self.assertIn("North York", page)
        self.assertIn("Richmond Hill", page)
        self.assertIn("اتاق در خانه مشترک", page)
        self.assertIn("مبله", page)
        self.assertIn("⚡ همون لحظه", page)
        self.assertIn("⏸ مکث تا", page)
        self.assertIn("ساخته‌شده در ربات", page)
        self.assertIn(f'href="/app/home/alerts/{bot.pk}/open/"', page)
        self.assertIn(f'href="/app/home/alerts/{paused.pk}/open/"', page)
        self.assertNotIn("مونترال", page)
        self.assertIn('role="switch" aria-checked="true"', page)

    def test_toggle_and_delete(self) -> None:
        alert = self.bot_alert()
        response = self.client.post("/app/home/alerts/", {"alert": alert.pk, "action": "toggle"})
        self.assertEqual(response["Location"], "/app/home/alerts/?note=off")
        alert.refresh_from_db()
        self.assertFalse(alert.active)
        self.assertIn('aria-checked="false"', self.client.get("/app/home/alerts/").content.decode())
        self.client.post("/app/home/alerts/", {"alert": alert.pk, "action": "toggle"})
        alert.refresh_from_db()
        self.assertTrue(alert.active)
        self.assertIn("روشن شد", self.client.get("/app/home/alerts/?note=on").content.decode())
        paused = self.bot_alert(paused_until=timezone.now() + timedelta(days=2))
        self.client.post("/app/home/alerts/", {"alert": paused.pk, "action": "toggle"})
        paused.refresh_from_db()
        self.assertTrue(paused.active)
        self.assertIsNone(paused.paused_until)
        response = self.client.post("/app/home/alerts/", {"alert": alert.pk, "action": "delete"})
        self.assertEqual(response["Location"], "/app/home/alerts/?note=deleted")
        self.assertFalse(HomeAlert.objects.filter(pk=alert.pk).exists())

    def test_turning_one_on_respects_the_cap(self) -> None:
        for number in range(MAX_ALERTS_PER_USER):
            self.bot_alert(max_price=1000 + number)
        off = self.bot_alert(active=False)
        response = self.client.post("/app/home/alerts/", {"alert": off.pk, "action": "toggle"})
        self.assertEqual(response["Location"], "/app/home/alerts/?note=cap")
        off.refresh_from_db()
        self.assertFalse(off.active)

    def test_other_peoples_alerts_cannot_be_touched(self) -> None:
        theirs = self.bot_alert(telegram_user_id=self.other.telegram_user_id, chat_id=self.other.telegram_user_id)
        self.client.post("/app/home/alerts/", {"alert": theirs.pk, "action": "delete"})
        self.client.post("/app/home/alerts/", {"alert": "x", "action": "delete"})
        self.assertTrue(HomeAlert.objects.filter(pk=theirs.pk).exists())
        self.assertEqual(self.client.get(f"/app/home/alerts/{theirs.pk}/open/").status_code, 404)

    def test_new_since_last_view_and_opening_resets_it(self) -> None:
        alert = self.bot_alert(viewed_at=timezone.now() - timedelta(days=2))
        self.ca_home(1)
        self.ca_home(2)
        old = self.ca_home(3)
        HomeListing.objects.filter(pk=old.pk).update(first_seen=timezone.now() - timedelta(days=5))
        page = self.client.get("/app/home/alerts/").content.decode()
        self.assertIn("۲ جدید", page)
        response = self.client.get(f"/app/home/alerts/{alert.pk}/open/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/app/home/list/?country=CA&kind=offer&city=Toronto"))
        alert.refresh_from_db()
        self.assertGreater(alert.viewed_at, timezone.now() - timedelta(minutes=1))
        self.assertNotIn('class="house-alert-new"', self.client.get("/app/home/alerts/").content.decode())

    def test_opening_an_alert_reopens_the_feed_with_its_filters(self) -> None:
        self.client.post(f"/app/home/alerts/save/?country=CA&city=Toronto&type=studio&max=1400&furnished=1&move={TODAY.isoformat()}")
        saved = HomeAlert.objects.get()
        location = self.client.get(f"/app/home/alerts/{saved.pk}/open/")["Location"]
        query = parse_qs(urlparse(location).query)
        self.assertEqual(query["type"], ["studio"])
        self.assertEqual(query["max"], ["1400"])
        self.assertEqual(query["furnished"], ["1"])
        self.assertEqual(query["move"], [TODAY.isoformat()])
        bot = self.bot_alert(country="DE", city="Berlin", property_types=["room"], max_price=Decimal("700"), area="Neukölln یا Kreuzberg", term=AlertTerm.SHORT, needs_registration=True)
        query = parse_qs(urlparse(self.client.get(f"/app/home/alerts/{bot.pk}/open/")["Location"]).query)
        self.assertEqual(query["area"], ["Neukölln", "Kreuzberg"])
        self.assertEqual(query["term"], ["short"])
        self.assertEqual(query["reg"], ["1"])
        self.assertEqual(query["max"], ["700"])

    def test_empty_page_and_english(self) -> None:
        page = self.client.get("/app/home/alerts/").content.decode()
        self.assertIn("هنوز گوش به زنگی ندارید", page)
        self.assertIn("+ گوش به زنگ جدید", page)
        self.login(self.user, "en")
        self.bot_alert()
        page = self.client.get("/app/home/alerts/").content.decode()
        self.assertIn("1 of 5 on", page)
        self.assertIn("⚡ Instant", page)
        self.assertIn("Delete this alert?", page)

    def test_a_saved_request_shows_as_such(self) -> None:
        wanted = self.ca_home(1, kind="wanted", price_max=Decimal("1000"))
        self.bot_alert(wanted_listing=wanted)
        page = self.client.get("/app/home/alerts/").content.decode()
        self.assertIn("از درخواست شما", page)
        self.assertIn("۰ از ۵ روشن", page)

    def test_links_into_the_page(self) -> None:
        self.assertEqual(startapp_path("home_alerts"), "/app/home/alerts/")
        self.assertEqual(startapp_from_request(RequestFactory().get("/app/home/alerts/")), "home_alerts")
        self.assertIn('href="/app/home/alerts/"', self.client.get("/app/home/").content.decode())
        self.assertIn('href="/app/home/alerts/"', self.page("?country=CA"))
        from home.botchat import alerts_message

        self.bot_alert()
        _text, keyboard = alerts_message(self.user.telegram_user_id)
        urls = [button.get("url", "") for row in keyboard for button in row]
        self.assertTrue(any(url.endswith("?startapp=home_alerts") for url in urls))
        self.client.logout()
        self.assertEqual(self.client.get("/app/home/alerts/")["Location"], "/app/login/?startapp=home_alerts")
