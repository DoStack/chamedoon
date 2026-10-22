from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.test import TestCase, override_settings

from home import bot, deeplink
from home.models import HomeChannel, HomeListing, HomeSettings, HomeSource
from home.publish import CAPTION_LIMIT, format_listing_text, post_buttons, price_band, term
from miniapp.auth import startapp_path
from tests.test_home_miniapp import MiniAppCase
from tests.test_home_publish import ca_listing, de_listing, turn_on

STARTAPP_JS = Path(__file__).resolve().parent.parent / "miniapp" / "static" / "miniapp" / "startapp.js"


class StartParamTests(TestCase):
    def test_the_grammar_builds_lower_case_ascii_within_64_characters(self) -> None:
        self.assertEqual(deeplink.build("CA", "Toronto"), "home_ca_toronto")
        self.assertEqual(deeplink.build("CA", "Toronto", listing=123, source="chtor"), "home_ca_toronto_l123_src-chtor")
        self.assertEqual(
            deeplink.build("CA", "Toronto", type="room", band=(700, 1100), source="chtor"),
            "home_ca_toronto_t-room_p700-1100_src-chtor",
        )
        self.assertEqual(deeplink.build("DE", "Köln"), "home_de_koln")
        self.assertEqual(deeplink.build("DE", ""), "home_de")
        longest = deeplink.build("CA", "North Vancouver Island Region", listing=10**11, type="lease_transfer", band=(99999, 99999), source="chvan")
        self.assertLessEqual(len(longest), 64)
        self.assertRegex(longest, r"^[A-Za-z0-9_-]+$")
        self.assertNotIn("src-", longest)

    def test_source_tags_name_the_channel(self) -> None:
        tags = {deeplink.source_tag(row.country, row.city) for row in HomeChannel.objects.all()}
        self.assertEqual(tags, {"chca", "chde", "chtor", "chmon", "chvan", "chber", "chham", "chcol"})

    def test_parse_reads_every_part(self) -> None:
        start = deeplink.parse("home_ca_toronto_t-lease-transfer_p700-1100_src-chtor")
        self.assertEqual(
            (start.country, start.city, start.type, start.min_price, start.max_price, start.source),
            ("CA", "toronto", "lease_transfer", 700, 1100, "chtor"),
        )
        self.assertEqual(deeplink.parse("home_de_berlin_l9").listing, 9)
        self.assertEqual(deeplink.parse("home_ca_l9").city, "")
        for bad in ("home", "home_42", "home_x", "home_ca_toronto_zz_9", "homes_ca", "home_ca_toronto_berlin"):
            self.assertIsNone(deeplink.parse(bad), bad)

    def test_start_params_open_the_feed_or_the_listing(self) -> None:
        self.assertEqual(startapp_path("home_ca_toronto"), "/app/home/list/?country=CA&city=toronto")
        self.assertEqual(startapp_path("home_ca_toronto_l123_src-chtor"), "/app/home/123/")
        self.assertEqual(
            startapp_path("home_ca_toronto_t-room_p700-1100_src-chtor"),
            "/app/home/list/?country=CA&city=toronto&type=room&min=700&max=1100",
        )
        self.assertEqual(startapp_path("home_de_p0-900"), "/app/home/list/?country=DE&max=900")
        # The old forms are unchanged.
        self.assertEqual(startapp_path("home"), "/app/home/")
        self.assertEqual(startapp_path("home_42"), "/app/home/42/")
        self.assertEqual(startapp_path("home_ca_toronto_zz"), "/app/")

    @skipUnless(shutil.which("node"), "node is not installed")
    def test_the_client_script_reads_the_same_grammar(self) -> None:
        values = [
            "home",
            "home_42",
            "home_ca_toronto",
            "home_ca_toronto_l123_src-chtor",
            "home_ca_toronto_t-lease-transfer_p700-1100_src-chtor",
            "home_de_p0-900",
            "home_ca_toronto_zz",
        ]
        runner = """
const vm = require("vm");
const fs = require("fs");
const code = fs.readFileSync(process.argv[1], "utf8");
const out = {};
for (const value of JSON.parse(process.argv[2])) {
  const href = "https://x.test/app/other/?startapp=" + value;
  const window = {location: new URL(href)};
  const context = {window, URL, URLSearchParams, performance: {getEntriesByType: () => []},
    sessionStorage: {getItem: () => "", setItem: () => {}}, history: {replaceState: () => {}}};
  vm.runInNewContext(code, context);
  out[value] = window.koolbarStartPath;
}
console.log(JSON.stringify(out));
"""
        result = subprocess.run(
            ["node", "-e", runner, str(STARTAPP_JS), json.dumps(values)], capture_output=True, text=True, check=True
        )
        paths = json.loads(result.stdout)
        for value in values:
            expected = startapp_path(value)
            self.assertEqual(paths[value] or "/app/", expected, value)


class PostTextTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()

    def test_a_suburb_shows_its_metro_then_the_suburb_and_the_neighbourhood(self) -> None:
        text = format_listing_text(ca_listing(), phone=False)
        self.assertIn("📍 <b>تورنتو</b> · North York", text)
        self.assertIn("🏘 محله: Yonge &amp; Finch", text)
        self.assertIn("#Toronto", text)

    def test_a_blank_city_routed_by_its_area_shows_the_area_as_the_suburb(self) -> None:
        listing = ca_listing(region="QC")
        listing.city, listing.area = "", "Laval"  # the review would fill the source's city; only the formatter sees this
        text = format_listing_text(listing, phone=False)
        self.assertIn("📍 <b>مونترال</b> · Laval", text)
        self.assertNotIn("محله", text)

    def test_the_term_comes_from_the_type_the_dates_and_the_flags(self) -> None:
        cases = [
            ({"property_type": "sublet"}, "sublet"),
            ({"property_type": "lease_transfer"}, "lease_transfer"),
            ({"property_type": "short_stay"}, "short_stay"),
            ({"price_period": "night"}, "short_stay"),
            ({"flags": {"untermiete": True}}, "sublet"),
            ({"available_to": date(2027, 4, 30)}, "temporary"),
            ({"min_term_months": 12}, "lease"),
            ({"flags": {"first_last": True}}, "lease"),
            ({}, ""),
        ]
        for values, expected in cases:
            listing = HomeListing(property_type="apartment", price_period="month", flags={})
            for key, value in values.items():
                setattr(listing, key, value)
            self.assertEqual(term(listing), expected, values)

    def test_the_header_names_the_type_and_the_term_once(self) -> None:
        text = format_listing_text(de_listing(property_type="apartment", available_to="2027-03-31"), phone=False)
        self.assertIn("🏠 <b>آپارتمان · اجاره‌ی موقت</b>", text)
        self.assertIn("#اجاره_ی_موقت", text)
        sublet = format_listing_text(de_listing(2, property_type="sublet"), phone=False)
        self.assertIn("🏠 <b>سابلت</b>", sublet)

    def test_amenities_and_conditions_from_the_review_flags(self) -> None:
        listing = ca_listing(flags={"parking": True, "pets_ok": False, "no_smoking": True, "credit_check": True, "first_last": True})
        text = format_listing_text(listing, phone=False)
        self.assertIn("✨ قبوض داخل قیمت · 🚗 پارکینگ · بدون حیوان خانگی · 🚭 سیگار ممنوع", text)
        self.assertIn("📋 شرایط: ماه اول و آخر · کردیت چک", text)

    def test_a_long_caption_keeps_the_city_price_contact_and_photo_count(self) -> None:
        listing = ca_listing()
        listing.summary_fa = "خلاصه " * 150
        text = format_listing_text(listing, limit=CAPTION_LIMIT, phone=False, photos=6)
        self.assertLessEqual(len(text), CAPTION_LIMIT)
        for kept in ("📍 <b>تورنتو</b>", "950 CA$", "📞 تماس", "🖼 ۶ عکس"):
            self.assertIn(kept, text)

    def test_no_photo_count_for_a_single_photo(self) -> None:
        self.assertNotIn("🖼", format_listing_text(ca_listing(), phone=False, photos=1))


class ButtonTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        HomeSettings.objects.update_or_create(pk=1, defaults={"bot_username": "chamedoon_home_bot"})

    def test_a_city_channel_post_links_to_its_metro_with_its_tag(self) -> None:
        listing = ca_listing()  # North York, 950 CAD, room
        rows = post_buttons(listing, HomeChannel.objects.get(city="Toronto"))
        self.assertEqual(rows[0][0], {"text": "جزئیات و عکس‌ها", "url": f"https://t.me/chamedoon_home_bot?startapp=home_ca_toronto_l{listing.pk}_src-chtor"})
        self.assertEqual(rows[1][0]["url"], "https://t.me/chamedoon_home_bot?startapp=home_ca_toronto_t-room_p750-1150_src-chtor")
        self.assertEqual(rows[1][1], {"text": "🔔 گوش به زنگ", "url": "https://t.me/chamedoon_home_bot?start=alert_home_ca_toronto"})

    def test_a_country_channel_post_still_names_the_metro(self) -> None:
        listing = ca_listing()
        rows = post_buttons(listing, HomeChannel.objects.get(country="CA", city=""))
        self.assertTrue(rows[0][0]["url"].endswith(f"home_ca_toronto_l{listing.pk}_src-chca"))

    def test_a_city_without_a_channel_and_a_listing_without_a_price(self) -> None:
        listing = de_listing(city="Frankfurt", area="", price_amount=None, property_type="other")
        rows = post_buttons(listing, HomeChannel.objects.get(country="DE", city=""))
        self.assertTrue(rows[1][0]["url"].endswith("startapp=home_de_frankfurt_src-chde"))
        self.assertTrue(rows[1][1]["url"].endswith("start=alert_home_de_frankfurt"))

    def test_the_price_band_of_offers_and_wanted_posts(self) -> None:
        self.assertEqual(price_band(HomeListing(kind="offer", price_amount=1000)), (800, 1200))
        self.assertEqual(price_band(HomeListing(kind="offer", price_amount=None)), None)
        self.assertEqual(price_band(HomeListing(kind="wanted", price_max=1230)), (0, 1250))

    def test_the_keyboard_payload(self) -> None:
        one = {"text": "a", "url": "https://t.me/x"}
        self.assertEqual(bot._keyboard(one), {"reply_markup": {"inline_keyboard": [[one]]}})
        self.assertEqual(bot._keyboard([one, one]), {"reply_markup": {"inline_keyboard": [[one, one]]}})
        self.assertEqual(bot._keyboard([[one], [one, one]]), {"reply_markup": {"inline_keyboard": [[one], [one, one]]}})
        self.assertEqual(bot._keyboard(None), {})

    @override_settings(TELEGRAM_CHANNEL_USERNAME="chamed0on")
    def test_every_post_goes_out_with_the_keyboard(self) -> None:
        turn_on()
        ca_listing()
        calls = []
        with patch("home.publish.bot.send_message", side_effect=lambda chat, text, **kwargs: calls.append(kwargs) or [1]):
            from home.publish import publish_pending

            publish_pending()
        self.assertEqual(len(calls), 2)  # the country channel and the Toronto channel
        for kwargs in calls:
            self.assertEqual(len(kwargs["buttons"]), 2)


class MiniAppStartTests(MiniAppCase):
    def test_a_city_start_param_opens_the_metro_feed_with_its_suburbs(self) -> None:
        north_york = self.ca_listing(1)
        downtown = self.ca_listing(2, city="Toronto")
        montreal = self.ca_listing(3, city="Montreal", region="QC")
        fragment = self.feed("?" + startapp_path("home_ca_toronto").split("?", 1)[1])
        self.assertIn(f"/app/home/{north_york.pk}/", fragment)
        self.assertIn(f"/app/home/{downtown.pk}/", fragment)
        self.assertNotIn(f"/app/home/{montreal.pk}/", fragment)

    def test_the_similar_link_applies_city_type_and_price_band(self) -> None:
        match = self.ca_listing(1, price_amount=900)
        cheap = self.ca_listing(2, price_amount=500)
        other_type = self.ca_listing(3, price_amount=900, property_type="apartment")
        start = deeplink.build("CA", "Toronto", type="room", band=price_band(match), source="chtor")
        query = startapp_path(start).split("?", 1)[1]
        fragment = self.feed("?" + query)
        self.assertIn(f"/app/home/{match.pk}/", fragment)
        self.assertNotIn(f"/app/home/{cheap.pk}/", fragment)
        self.assertNotIn(f"/app/home/{other_type.pk}/", fragment)
        page = self.client.get(startapp_path(start)).content.decode()
        self.assertIn('name="min" value="700"', page)
        self.assertIn('name="city" id="home_city" value="Toronto"', page)

    def test_a_logged_in_user_is_sent_to_the_start_path(self) -> None:
        response = self.client.get("/app/login/?startapp=home_ca_toronto_l5_src-chtor")
        self.assertEqual(response["Location"], "/app/home/5/")

    def test_the_chosen_city_is_remembered(self) -> None:
        north_york = self.ca_listing(1)
        montreal = self.ca_listing(2, city="Montreal", region="QC")
        self.client.get(startapp_path("home_ca_toronto"))
        bare = self.feed("")
        self.assertIn(f"/app/home/{north_york.pk}/", bare)
        self.assertNotIn(f"/app/home/{montreal.pk}/", bare)
        landing = self.client.get("/app/home/").content.decode()
        self.assertIn("/app/home/list/?country=CA&amp;kind=offer&amp;city=Toronto", landing)
        # Choosing "any city" in the filters (the form always sends the country) forgets it.
        self.feed("?country=CA&kind=offer")
        self.assertIn(f"/app/home/{montreal.pk}/", self.feed(""))
        # A remembered city belongs to its country only.
        self.client.get(startapp_path("home_ca_toronto"))
        self.assertIn('/app/home/list/?country=DE&amp;kind=offer"', self.client.get("/app/home/?country=DE").content.decode())
