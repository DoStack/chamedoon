from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.utils import timezone

from home import areas
from home.area_seed import SEED
from home.job import run_home_job
from home.models import HomeArea, HomeChannel, HomeListing, HomeSaved, HomeSettings, ListingStatus
from home_api import views
from tests.helpers import bearer_auth, make_user
from tests.test_home_api import ApiCase

AREAS_PATTERN = "/api/v1/public/areas/"
SEARCH_PATTERN = "/api/v1/public/areas/search/"
SAVED_PATTERN = "/api/v1/me/saved/"
ITEM_PATTERN = "/api/v1/me/saved/{id}/"


class AreaCase(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()

    def active(self, number: int, **data) -> HomeListing:
        data.setdefault("contact_telegram", f"person_{number}")
        listing = self.listing(number, **data)
        # A new look-alike can merge an earlier one; these tests want each to stand alone.
        HomeListing.objects.update(status=ListingStatus.ACTIVE, canonical=None, needs_review=False)
        listing.refresh_from_db()
        return listing

    def area(self, canonical: str, city: str = "Toronto") -> HomeArea:
        return HomeArea.objects.get(city=city, canonical=canonical)


class SeedTests(AreaCase):
    def test_every_metro_is_seeded_with_a_point_and_a_persian_name(self) -> None:
        for city, country, rows in SEED:
            self.assertEqual(HomeArea.objects.filter(city=city, country=country).count(), len(rows), city)
        for row in HomeArea.objects.all():
            self.assertTrue(row.fa and row.lat is not None and row.lng is not None, row)

    def test_a_seeded_name_folds_to_its_canonical(self) -> None:
        for row in HomeArea.objects.all():
            self.assertEqual(areas.fold(row.canonical), row.canonical, row)


class MatchTests(AreaCase):
    def match(self, **data):
        return areas.match_area(self.active(100 + HomeListing.objects.count(), **data))

    def test_by_name_alias_persian_and_part(self) -> None:
        for data, canonical in (
            ({"city": "North York", "area": ""}, "north york"),
            ({"city": "Toronto", "area": "Scarborough"}, "scarborough"),
            ({"city": "Toronto", "area": "نورث یورک"}, "north york"),
            ({"city": "Toronto", "area": "Yonge & Finch"}, "north york"),
            ({"city": "Toronto", "area": "Unionville, Markham"}, "markham"),
            ({"city": "Toronto", "area": "Near Square One"}, "mississauga"),
        ):
            with self.subTest(data=data):
                self.assertEqual(self.match(**data).canonical, canonical)

    def test_a_toronto_intersection_nobody_listed_reaches_its_district(self) -> None:
        self.assertEqual(self.match(city="Toronto", area="Bathurst & Sheppard").canonical, "north york")

    def test_no_match_or_no_channel_stays_unlinked(self) -> None:
        self.assertIsNone(self.match(city="Toronto", area="Somewhere Unknown"))
        self.assertIsNone(self.match(city="Ottawa", area="Kanata"))

    def test_a_short_alias_does_not_match_inside_a_word(self) -> None:
        self.assertIsNone(self.match(city="Toronto", area="Vanier"))

    def test_same_canonical_in_two_metros_stays_apart(self) -> None:
        HomeChannel.objects.create(country="CA", city="Montreal", username="mtl")
        found = self.match(city="Montreal", area="Downtown")
        self.assertEqual((found.canonical, found.city), ("downtown", "Montreal"))


class BackfillTests(AreaCase):
    def test_links_a_batch_and_marks_what_matched_nothing(self) -> None:
        linked = self.active(1, city="North York", area="")
        unlinked = self.active(2, city="Toronto", area="Somewhere Unknown")
        HomeListing.objects.update(home_area=None, area_checked_at=None)
        result = areas.backfill_listing_areas()
        self.assertEqual((result["checked"], result["linked"]), (2, 1))
        linked.refresh_from_db()
        unlinked.refresh_from_db()
        self.assertEqual(linked.home_area.canonical, "north york")
        self.assertIsNone(unlinked.home_area)
        self.assertIsNotNone(unlinked.area_checked_at)
        self.assertEqual(areas.backfill_listing_areas()["checked"], 0)

    def test_batch_size_is_respected(self) -> None:
        for number in range(1, 4):
            self.active(number)
        HomeListing.objects.update(area_checked_at=None)
        self.assertEqual(areas.backfill_listing_areas(limit=2)["checked"], 2)

    def test_a_failure_leaves_it_for_next_time(self) -> None:
        listing = self.active(1)
        HomeListing.objects.update(area_checked_at=None)
        with patch("home.areas.match_area", side_effect=RuntimeError("boom")), self.assertLogs("home.areas", level="ERROR"):
            areas.link_area(listing)
        listing.refresh_from_db()
        self.assertIsNone(listing.area_checked_at)

    def test_runs_in_a_whole_job_and_a_failure_does_not_end_it(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"ingest_enabled": False, "review_enabled": False, "media_enabled": False, "vision_enabled": False, "publish_enabled": False, "alerts_enabled": False, "freshness_enabled": False})
        with patch("home.job.backfill_listing_areas", return_value={"ok": True}) as backfill:
            self.assertEqual(run_home_job(step="all")["areas"], {"ok": True})
        backfill.assert_called_once()
        with patch("home.job.backfill_listing_areas", side_effect=RuntimeError("boom")), self.assertLogs("home.job", level="ERROR"):
            self.assertFalse(run_home_job(step="all")["areas"]["ok"])


class CityAreasTests(AreaCase):
    def get(self, **query):
        return self.client.get(AREAS_PATTERN, {"country": "CA", "city": "Toronto", **query})

    def link(self, listing: HomeListing, canonical: str | None) -> None:
        HomeListing.objects.filter(pk=listing.pk).update(home_area=self.area(canonical) if canonical else None)

    def test_ordered_by_this_weeks_count_and_cacheable(self) -> None:
        old = self.active(1)
        self.link(old, "scarborough")
        HomeListing.objects.filter(pk=old.pk).update(last_seen=timezone.now() - timedelta(days=10))
        for number in (2, 3):
            self.link(self.active(number), "markham")
        self.link(self.active(4), None)
        response = self.get()
        self.assertIn("s-maxage=300", response["Cache-Control"])
        self.assertEqual(len(response.cookies), 0)
        body = response.json()
        rows = {row["canonical"]: row for row in body["areas"]}
        self.assertEqual(body["areas"][0]["canonical"], "markham")
        self.assertEqual((rows["markham"]["listing_count"], rows["markham"]["week_count"]), (2, 2))
        self.assertEqual((rows["scarborough"]["listing_count"], rows["scarborough"]["week_count"]), (1, 0))
        self.assertEqual(body["areas"][1]["canonical"], "scarborough")
        self.assertEqual(body["no_area_count"], 1)
        self.assertEqual(rows["north york"]["group"], "شمال")
        self.assertIsNotNone(rows["north york"]["lat"])

    def test_city_by_slug_and_empty_areas_stay_listed(self) -> None:
        body = self.get(city="toronto", country="ca").json()
        self.assertEqual(body["city"], "Toronto")
        self.assertEqual(body["no_area_count"], 0)
        self.assertGreater(len(body["areas"]), 5)

    def test_validation_failed(self) -> None:
        got = self.assert_error(self.get(city="Nowhere"), "validation_failed", AREAS_PATTERN)
        self.assertEqual(got["fields"], {"city": ["invalid_choice"]})
        got = self.assert_error(self.client.get(AREAS_PATTERN), "validation_failed", AREAS_PATTERN)
        self.assertEqual(set(got["fields"]), {"country", "city"})

    def test_rate_limited_on_its_own_budget(self) -> None:
        with patch("home_api.views.AreasThrottle.rate", "1/min"):
            self.get()
            self.assert_error(self.get(), "rate_limited", AREAS_PATTERN)
            self.assertEqual(self.client.post("/api/v1/auth/exchange/", {}, content_type="application/json").status_code, 400)
        self.assertEqual(views.AreasThrottle.scope, "home_areas")


class AreaSearchTests(AreaCase):
    def search(self, q: str, **query):
        return self.client.get(SEARCH_PATTERN, {"q": q, "country": "CA", "city": "Toronto", **query})

    def test_intersection_shows_its_area_and_the_name_that_matched(self) -> None:
        body = self.search("Yonge و Shep").json()
        self.assertEqual(body["here"][0]["name_fa"], "نورث یورک")
        self.assertEqual(body["here"][0]["matched"], "yonge and sheppard")
        self.assertEqual(body["elsewhere"], [])

    def test_persian_and_other_name(self) -> None:
        self.assertEqual(self.search("ریچموند هیل").json()["here"][0]["city_slug"], "toronto")
        self.assertEqual(self.search("unionville").json()["here"][0]["name_fa"], "مارکهام")

    def test_another_citys_area_is_split_out(self) -> None:
        body = self.search("kreuzberg").json()
        self.assertEqual(body["here"], [])
        self.assertEqual((body["elsewhere"][0]["city"], body["elsewhere"][0]["country"]), ("Berlin", "DE"))

    def test_with_no_city_everything_is_elsewhere(self) -> None:
        body = self.client.get(SEARCH_PATTERN, {"q": "markham"}).json()
        self.assertEqual((body["here"], len(body["elsewhere"])), ([], 1))

    def test_no_hit_is_empty_not_an_error(self) -> None:
        response = self.search("پیرسون")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"here": [], "elsewhere": []})

    def test_an_unlisted_toronto_intersection_reaches_its_district(self) -> None:
        self.assertEqual(self.search("Bathurst & Sheppard").json()["here"][0]["name_fa"], "نورث یورک")

    def test_validation_failed(self) -> None:
        got = self.assert_error(self.client.get(SEARCH_PATTERN), "validation_failed", SEARCH_PATTERN)
        self.assertEqual(got["fields"], {"q": ["required"]})
        self.assert_error(self.search("x" * 81), "validation_failed", SEARCH_PATTERN)

    def test_rate_limited(self) -> None:
        with patch("home_api.views.AreasThrottle.rate", "1/min"):
            self.search("a")
            self.assert_error(self.search("a"), "rate_limited", SEARCH_PATTERN)


class AreaFilterTests(AreaCase):
    def test_card_and_feed_carry_the_link_and_filter_on_it(self) -> None:
        listing = self.active(1)
        area = self.area("markham")
        HomeListing.objects.filter(pk=listing.pk).update(home_area=area)
        cards = self.client.get("/api/v1/public/feed/", {"country": "CA", "city": "Toronto", "area_ids": [area.pk]}).json()
        self.assertEqual(cards["results"][0]["area_id"], area.pk)
        cache.clear()
        other = self.area("scarborough").pk
        self.assertEqual(self.client.get("/api/v1/public/feed/", {"country": "CA", "city": "Toronto", "area_ids": [other]}).json()["total"], 0)

    def test_at_most_six_areas(self) -> None:
        got = self.assert_error(self.client.get("/api/v1/public/feed/", {"country": "CA", "city": "Toronto", "area_ids": list(range(7))}), "validation_failed", "/api/v1/public/feed/")
        self.assertEqual(got["fields"], {"area_ids": ["max_length"]})


class SavedTests(AreaCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.active(1, contact_telegram="roomsbyali1")
        self.auth = bearer_auth(self.user)

    def put(self, pk: int | None = None, **body):
        return self.client.put(f"{SAVED_PATTERN}{pk or self.home.pk}/", body, content_type="application/json", **self.auth)

    def delete(self, pk: int | None = None):
        return self.client.delete(f"{SAVED_PATTERN}{pk or self.home.pk}/", **self.auth)

    def rows(self):
        return self.client.get(SAVED_PATTERN, **self.auth).json()

    def test_empty(self) -> None:
        self.assertEqual(self.rows(), {"results": [], "total": 0, "next_cursor": None})

    def test_mark_list_and_retry_makes_one_row(self) -> None:
        for _ in range(2):
            response = self.put()
            self.assertEqual(response.status_code, 200)
        self.assertEqual(HomeSaved.objects.count(), 1)
        row = self.rows()["results"][0]
        self.assertEqual((row["listing_id"], row["state"], row["note"], row["closed_reason"]), (self.home.pk, "active", "", None))
        self.assertEqual(row["card"]["id"], self.home.pk)

    def test_holds_no_contact(self) -> None:
        self.put()
        text = self.client.get(SAVED_PATTERN, **self.auth).content.decode()
        for leaked in ("roomsbyali1", "RentToronto", "source_url"):
            self.assertNotIn(leaked, text)

    def test_note_set_kept_blanked_and_limited(self) -> None:
        self.assertEqual(self.put(note=" شنبه بازدید ").json()["note"], "شنبه بازدید")
        self.assertEqual(self.put().json()["note"], "شنبه بازدید")
        self.assertEqual(self.put(note="").json()["note"], "")
        got = self.assert_error(self.put(note="x" * 201), "validation_failed", ITEM_PATTERN)
        self.assertEqual(got["fields"], {"note": ["max_length"]})

    def test_remove_is_idempotent_and_marking_again_restores(self) -> None:
        self.put(note="x")
        self.assertEqual(self.delete().status_code, 204)
        self.assertEqual(self.delete().status_code, 204)
        self.assertEqual(self.rows()["total"], 0)
        self.assertEqual(self.put().status_code, 200)
        self.assertEqual(self.put(note="x").json()["note"], "x")

    def test_closed_stays_with_its_reason(self) -> None:
        self.put()
        HomeListing.objects.filter(pk=self.home.pk).update(status=ListingStatus.RENTED)
        row = self.rows()["results"][0]
        self.assertEqual((row["state"], row["closed_reason"]), ("closed", "owner"))
        self.assertIsNotNone(row["card"])
        self.assertEqual(self.put(note="هنوز").status_code, 200)

    def test_gone_comes_without_a_card_and_can_be_removed(self) -> None:
        self.put()
        HomeListing.objects.filter(pk=self.home.pk).update(status=ListingStatus.REPORTED)
        row = self.rows()["results"][0]
        self.assertEqual((row["state"], row["card"]), ("gone", None))
        self.assertEqual(self.delete().status_code, 204)
        self.assertEqual(self.rows()["total"], 0)

    def test_merged_shows_the_listing_it_became(self) -> None:
        other = self.active(2)
        self.put(other.pk, note="یادداشت")
        HomeListing.objects.filter(pk=other.pk).update(status=ListingStatus.DUPLICATE, canonical=self.home)
        row = self.rows()["results"][0]
        self.assertEqual((row["listing_id"], row["state"], row["card"]["id"], row["note"]), (other.pk, "active", self.home.pk, "یادداشت"))
        self.assertIn(self.home.pk, self.client.get(f"{SAVED_PATTERN}ids/", **self.auth).json()["listing_ids"])
        self.assertEqual(self.delete(self.home.pk).status_code, 204)
        self.assertEqual(HomeSaved.objects.count(), 0)

    def test_saving_a_merged_id_saves_the_listing_it_became(self) -> None:
        other = self.active(2)
        HomeListing.objects.filter(pk=other.pk).update(status=ListingStatus.DUPLICATE, canonical=self.home)
        self.assertEqual(self.put(other.pk).json()["listing_id"], self.home.pk)

    def test_listing_gone_on_save(self) -> None:
        self.assert_error(self.put(999999), "listing_gone", ITEM_PATTERN)
        HomeListing.objects.filter(pk=self.home.pk).update(needs_review=True)
        self.assert_error(self.put(), "listing_gone", ITEM_PATTERN)

    def test_pages(self) -> None:
        for number in range(2, 5):
            self.put(self.active(number).pk)
        with patch("home_api.views.SAVED_PAGE", 2):
            first = self.rows()
            second = self.client.get(SAVED_PATTERN, {"cursor": first["next_cursor"]}, **self.auth).json()
        self.assertEqual((len(first["results"]), first["next_cursor"], first["total"]), (2, "2", 3))
        self.assertEqual((len(second["results"]), second["next_cursor"]), (1, None))

    def test_bad_cursor(self) -> None:
        self.assert_error(self.client.get(SAVED_PATTERN, {"cursor": "x"}, **self.auth), "validation_failed", SAVED_PATTERN)

    def test_each_person_has_their_own(self) -> None:
        self.put()
        other = make_user(telegram_user_id=880090, first_name="Ali")
        self.assertEqual(self.client.get(SAVED_PATTERN, **bearer_auth(other)).json()["total"], 0)
        self.client.delete(f"{SAVED_PATTERN}{self.home.pk}/", **bearer_auth(other))
        self.assertEqual(HomeSaved.objects.count(), 1)

    def test_needs_a_token_and_a_live_account(self) -> None:
        self.assert_error(self.client.get(SAVED_PATTERN), "auth_required", SAVED_PATTERN)
        self.assert_error(self.client.put(f"{SAVED_PATTERN}1/", {}, content_type="application/json"), "auth_required", ITEM_PATTERN)
        self.assert_error(self.client.delete(f"{SAVED_PATTERN}1/"), "auth_required", ITEM_PATTERN)
        self.user.is_active = False
        self.user.save()
        self.assert_error(self.client.get(SAVED_PATTERN, **self.auth), "user_blocked", SAVED_PATTERN)

    def test_rate_limited_and_bad_body(self) -> None:
        self.assert_error(self.client.put(f"{SAVED_PATTERN}{self.home.pk}/", "{nope", content_type="application/json", **self.auth), "bad_request", ITEM_PATTERN)
        with patch("home_api.views.WriteThrottle.rate", "1/min"):
            self.put()
            self.assert_error(self.put(), "rate_limited", ITEM_PATTERN)
            self.assert_error(self.delete(), "rate_limited", ITEM_PATTERN)
