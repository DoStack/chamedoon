"""The area catalog, the rules that read it, and `manage.py home_areas_sync`."""

from __future__ import annotations

import io
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import CommandError, call_command

from home import areas
from home.area_catalog import CATALOG
from home.area_seed import SEED
from home.city_names import CITY_FA, city_fa, clean_city
from home.models import HomeArea, HomeChannel, HomeListing, ListingStatus
from home.places import fold
from home.public import card_area, public_card
from home.review import normalize_listing
from tests.test_home_api import ApiCase

CHANNELS = (
    ("CA", "Toronto", "North York, Richmond Hill, Newmarket, Aurora, Scarborough, Etobicoke, Thornhill, Markham, Vaughan, Holland Landing"),
    ("CA", "Montreal", "Montréal, Longueuil, Laval, Brossard, Côte Saint-Luc, Westmount, Verdun"),
    ("CA", "Vancouver", "North Vancouver, West Vancouver, Coquitlam, Port Coquitlam, Port Moody, Burnaby, Richmond, Surrey, New Westminster, Delta, Langley"),
    ("DE", "Berlin", "Neukölln, Kreuzberg, Spandau, Marzahn"),
    ("DE", "Hamburg", "Norderstedt, Altona"),
    ("DE", "Cologne", "Köln, Hürth, Frechen, Pulheim, Bergisch Gladbach, Porz"),
)


def report(*options: str) -> str:
    out = io.StringIO()
    call_command("home_areas_sync", *options, stdout=out)
    return out.getvalue()


class CatalogShapeTests(ApiCase):
    def test_every_existing_row_stays_under_its_canonical(self) -> None:
        for city, country, rows in SEED:
            names = {row[0] for row in CATALOG[(country, city)]}
            self.assertLessEqual({row[0] for row in rows}, names, city)

    def test_each_row_is_complete_and_folds_to_its_canonical(self) -> None:
        for metro, rows in CATALOG.items():
            self.assertEqual(len({row[0] for row in rows}), len(rows), metro)
            for canonical, fa, _group, lat, lng, _aliases in rows:
                self.assertEqual(fold(canonical), canonical)
                self.assertTrue(fa and lat is not None and lng is not None, canonical)
                self.assertTrue(any("؀" <= char <= "ۿ" for char in fa), canonical)

    def test_no_name_means_two_rows_of_a_metro(self) -> None:
        for metro, rows in CATALOG.items():
            seen: dict[str, str] = {}
            for canonical, fa, _group, _lat, _lng, aliases in rows:
                for name in {fold(name) for name in (canonical, fa, *aliases.split(",")) if name}:
                    self.assertEqual(seen.setdefault(name, canonical), canonical, (metro, name))

    def test_no_persian_name_sits_inside_another(self) -> None:
        # The client filters by substring, so a chip must not catch another area's cards.
        # One pair is allowed: Coquitlam's chip also catches Port Coquitlam, its neighbour.
        for metro, rows in CATALOG.items():
            names = [row[1] for row in rows]
            for name in names:
                caught = [other for other in names if name in other and other != name and (name, other) != ("کوکیتلام", "پورت کوکیتلام")]
                self.assertEqual(caught, [], (metro, name))

    def test_the_removed_street_names_stay_out(self) -> None:
        names = {name for row in CATALOG[("CA", "Toronto")] for name in row[5].split(",")}
        self.assertFalse(names & {"queen st", "king st", "maple", "ajax", "aurora"})


class SyncedCase(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        HomeChannel.objects.all().delete()
        for country, city, aliases in CHANNELS:
            HomeChannel.objects.create(country=country, city=city, aliases=aliases, username=f"home_{city.lower()}")
        report("--apply")

    def active(self, number: int, **data) -> HomeListing:
        data.setdefault("contact_telegram", f"person_{number}")
        listing = self.listing(number, **data)
        HomeListing.objects.update(status=ListingStatus.ACTIVE, canonical=None, needs_review=False)
        listing.refresh_from_db()
        return listing

    def place(self, area: str, city: str, country: str) -> str | None:
        found = areas.match_area(self.active(100 + HomeListing.objects.count(), city=city, area=area, country=country))
        return found.canonical if found else None


class RuleTests(SyncedCase):
    def test_real_post_texts(self) -> None:
        for area, city, country, canonical in (
            ("Yonge & Steeles", "Toronto", "CA", "north york"),
            ("Steeles & Yonge", "Toronto", "CA", "north york"),
            ("Yang and Finch", "Toronto", "CA", "north york"),
            ("Young and Stiles", "Toronto", "CA", "north york"),
            ("Dan Mills and Shepherd", "Toronto", "CA", "don mills"),
            ("Eglinton & Yonge", "Toronto", "CA", "midtown"),
            ("Yonge - Eglinton", "Toronto", "CA", "midtown"),
            ("Aurora - Henderson (Yonge & Bathurst area)", "Toronto", "CA", "aurora"),
            ("Liberty Village", "Toronto", "CA", "liberty village"),
            ("Yonge & Steeles", "Thornhill", "CA", "thornhill"),
            ("Downtown Aurora", "Aurora", "CA", "aurora"),
            ("NDG", "Montreal", "CA", "ndg"),
            ("Côte-des-Neiges", "Montreal", "CA", "cote des neiges"),
            ("Île-des-Sœurs", "Montreal", "CA", "nuns island"),
            ("Le Plateau-Mont-Royal", "Montreal", "CA", "plateau"),
            ("Kokitlam", "Vancouver", "CA", "coquitlam"),
            ("Upper Lonsdale", "North Vancouver", "CA", "north vancouver"),
            ("Shaughnessy", "Coquitlam", "CA", "coquitlam"),
            ("Port Moody (lower)", "Coquitlam", "CA", "port moody"),
            ("Haselhorst (Spandau)", "Berlin", "DE", "haselhorst"),
            ("Steglitz-Zehlendorf", "Berlin", "DE", "steglitz"),
            ("Rodow - Neukölln", "Berlin", "DE", "rudow"),
            ("Mertsan", "Berlin", "DE", "marzahn"),
            ("Wandsbek Markt, Niendorf", "Hamburg", "DE", "wandsbek"),
            ("Norderstedt Mitte", "Hamburg", "DE", "norderstedt"),
            ("Köln-Porz", "Cologne", "DE", "porz"),
            ("Weiden Zentrum", "Cologne", "DE", "weiden"),
            ("Niel 50735", "Cologne", "DE", "niehl"),
        ):
            with self.subTest(area=area, city=city):
                self.assertEqual(self.place(area, city, country), canonical)

    def test_what_matches_nothing_stays_without_an_area(self) -> None:
        for area, city, country in (
            ("unknown", "Toronto", "CA"),
            ("Yonge", "Toronto", "CA"),
            ("9 Bogert Ave", "Toronto", "CA"),
            ("2525 Cavendish", "Montreal", "CA"),
            ("Grand Floor", "Vancouver", "CA"),
            ("Heilbronner Str", "Berlin", "DE"),
            ("Frankfurter Allee", "Berlin", "DE"),
            ("Krefeld city center", "Hamburg", "DE"),
            ("Porzer Str", "Cologne", "DE"),
        ):
            with self.subTest(area=area):
                self.assertIsNone(self.place(area, city, country))

    def test_a_place_of_one_other_city_moves_the_listing_there(self) -> None:
        # The area outranks the group's city (audit 2026-11-09, #308): NDG is Montreal's.
        self.assertEqual(self.place("NDG", "Vancouver", "CA"), "ndg")

    def test_a_street_after_a_german_name_is_a_street(self) -> None:
        self.assertIsNone(self.place("Kalk Str 5", "Cologne", "DE"))
        self.assertEqual(self.place("Kalk Post", "Cologne", "DE"), "kalk")


class CardTests(SyncedCase):
    def test_a_linked_card_shows_the_persian_name_then_the_post_text(self) -> None:
        listing = self.active(1, city="Toronto", area="Yonge & Steeles")
        areas.link_area(listing)
        listing.refresh_from_db()
        self.assertEqual(card_area(listing), "نورث یورک · Yonge & Steeles")

    def test_the_name_alone_adds_nothing_to_the_persian_name(self) -> None:
        listing = self.active(1, city="Toronto", area="North York")
        areas.link_area(listing)
        listing.refresh_from_db()
        self.assertEqual(card_area(listing), "نورث یورک")

    def test_an_owners_address_after_the_separator_stays(self) -> None:
        listing = self.active(1, city="Montreal", country="CA", area="کت د نژ · 3501 Ave")
        areas.link_area(listing)
        listing.refresh_from_db()
        self.assertEqual(card_area(listing), "کت د نژ · 3501 Ave")

    def test_an_unknown_place_shows_no_area_and_the_city_keeps_its_persian_name(self) -> None:
        listing = self.active(1, city="Toronto", area="Somewhere Unknown")
        areas.link_area(listing)
        listing.refresh_from_db()
        card = public_card(listing)
        self.assertEqual((card["area"], card["area_id"], card["city"], card["city_fa"]), ("", None, "Toronto", "تورنتو"))

    def test_a_contact_in_the_area_text_never_reaches_the_card(self) -> None:
        listing = self.active(1, city="Toronto", area="North York @someone_here")
        areas.link_area(listing)
        listing.refresh_from_db()
        self.assertNotIn("someone_here", card_area(listing))

    def test_the_full_list_stays_for_the_post_form(self) -> None:
        got = self.client.get("/api/v1/public/areas/", {"country": "CA", "city": "Toronto"}).json()
        self.assertEqual(len(got["areas"]), len(CATALOG[("CA", "Toronto")]))
        self.assertTrue(any(row["listing_count"] == 0 for row in got["areas"]))


class CityTests(ApiCase):
    def test_every_city_seen_on_production_has_a_persian_name(self) -> None:
        for name in ("Richmond Hill", "Coquitlam", "Munich", "Frankfurt", "Düsseldorf", "Dusseldorf", "Würzburg", "Gießen", "Cologne"):
            self.assertTrue(city_fa(name), name)
        self.assertIn("munich", CITY_FA)

    def test_dirty_values(self) -> None:
        for given, wanted in (
            (("Düsseldorf", "", "DE"), ("Dusseldorf", "", "DE")),
            (("Branenburg", "", "DE"), ("Brannenburg", "", "DE")),
            (("Giessen", "Mitte", "DE"), ("Gießen", "Mitte", "DE")),
            (("null", "null", "DE"), ("", "", "DE")),
            (("", "", "DE"), ("", "", "DE")),
            (("Innsbruck", "", "DE"), ("Innsbruck", "", "AT")),
            (("Spandau", "", "DE"), ("Berlin", "Spandau", "DE")),
            (("Oak Ridges", "Yonge St", "CA"), ("Richmond Hill", "Yonge St", "CA")),
            (("Cologne", "Düsseldorf", "DE"), ("Dusseldorf", "", "DE")),
            (("", "Paderborn", "DE"), ("Paderborn", "", "DE")),
            (("Toronto", "unknown", "CA"), ("Toronto", "", "CA")),
            (("Toronto", "Mill Pond", "CA"), ("Toronto", "Mill Pond", "CA")),
            (("Some Town", "Some Place", "DE"), ("Some Town", "Some Place", "DE")),
        ):
            with self.subTest(given=given):
                self.assertEqual(clean_city(*given), wanted)

    def test_an_empty_city_takes_the_sources(self) -> None:
        self.assertEqual(clean_city("null", "Mitte", "DE", "Berlin"), ("Berlin", "Mitte", "DE"))


class ReviewPathTests(SyncedCase):
    def test_a_new_post_gets_a_clean_city_and_its_area(self) -> None:
        listing = self.active(1, city="Toronto", area="Liberty Village")
        fields, _notes = normalize_listing(listing.post, {"kind": "offer", "city": "Düsseldorf", "area": "null", "summary_fa": "خانه"})
        self.assertEqual((fields["city"], fields["area"]), ("Dusseldorf", ""))

    def test_apply_review_links_at_once(self) -> None:
        from home.review import apply_review

        listing = self.active(1, city="Toronto", area="")
        post = listing.post
        HomeListing.objects.filter(pk=listing.pk).delete()
        type(post).objects.filter(pk=post.pk).update(status="new")
        post.refresh_from_db()
        outcome = apply_review(post, {"accept": True, "kind": "offer", "city": "Toronto", "area": "Liberty Village", "summary_fa": "خانه"})
        self.assertEqual(outcome.listing.home_area.canonical, "liberty village")
        self.assertIsNotNone(outcome.listing.area_checked_at)


class CommandTests(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        HomeChannel.objects.all().delete()
        for country, city, aliases in CHANNELS:
            HomeChannel.objects.create(country=country, city=city, aliases=aliases, username=f"home_{city.lower()}")

    def active(self, number: int, **data) -> HomeListing:
        data.setdefault("contact_telegram", f"person_{number}")
        listing = self.listing(number, **data)
        HomeListing.objects.update(status=ListingStatus.ACTIVE, canonical=None, needs_review=False)
        listing.refresh_from_db()
        return listing

    def test_needs_a_mode(self) -> None:
        with self.assertRaises(CommandError):
            call_command("home_areas_sync")

    def test_dry_run_writes_nothing(self) -> None:
        listing = self.active(1, city="Toronto", area="Liberty Village")
        rows = HomeArea.objects.count()
        text = report("--dry-run")
        listing.refresh_from_db()
        self.assertEqual(HomeArea.objects.count(), rows)
        self.assertIsNone(listing.home_area)
        self.assertIn("Toronto", text)
        self.assertIn("liberty village", text)

    def test_apply_upserts_links_repairs_and_deletes_nothing(self) -> None:
        linked = self.active(1, city="Toronto", area="Liberty Village")
        dirty = self.active(2, city="Düsseldorf", area="", country="DE")
        wrong = self.active(3, city="Toronto", area="Somewhere Unknown")
        HomeListing.objects.filter(pk=wrong.pk).update(home_area=HomeArea.objects.get(city="Toronto", canonical="downtown"))
        kept = HomeArea.objects.create(country="CA", city="Toronto", canonical="an admin row", fa="ردیف ادمین")
        count = HomeListing.objects.count()
        with patch("home.areas.mark_areas_changed") as marked:
            report("--apply")
        marked.assert_not_called()
        self.assertEqual(HomeListing.objects.count(), count)
        self.assertTrue(HomeArea.objects.filter(pk=kept.pk).exists())
        for listing in (linked, dirty, wrong):
            listing.refresh_from_db()
        self.assertEqual(linked.home_area.canonical, "liberty village")
        self.assertEqual(dirty.city, "Dusseldorf")
        self.assertIsNone(wrong.home_area)
        self.assertIsNotNone(wrong.area_checked_at)
        total = sum(len(rows) for rows in CATALOG.values())
        self.assertEqual(HomeArea.objects.count(), total + 1)

    def test_a_second_apply_changes_nothing(self) -> None:
        self.active(1, city="Toronto", area="Liberty Village")
        self.active(2, city="Cologne", area="Düsseldorf", country="DE")
        report("--apply")
        second = report("--apply")
        self.assertIn("0 new, 0 corrected", second)
        self.assertIn("repaired city or area text: 0. Links changed: 0.", second)
