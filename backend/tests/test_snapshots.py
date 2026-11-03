from __future__ import annotations

import gzip
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from home import card_filters, snapshots
from home.job import run_home_job
from home.models import HomeChannel, HomeListing, HomeSettings, ListingStatus
from home.public import public_card
from home.storage import IMMUTABLE_CACHE, R2Storage, StorageError
from home_api.serializers import ManifestCitySerializer, ManifestSerializer, PublicCardSerializer, SnapshotFileSerializer
from tests.test_home_api import ApiCase

VECTORS = json.loads((Path(__file__).resolve().parent.parent / "home_api" / "testdata" / "filter_vectors.json").read_text(encoding="utf-8"))
FEED_PATTERN = "/api/v1/public/feed/"
HANDLE = "roomsbyali1"


class FakeStorage:
    def __init__(self) -> None:
        self.files: dict[str, dict] = {}
        self.puts: list[str] = []
        self.deleted: list[str] = []
        self.fail_on = ""

    def put(self, key, data, content_type="image/jpeg", *, content_encoding="", cache_control=IMMUTABLE_CACHE):
        if self.fail_on and self.fail_on in key:
            raise StorageError("R2 answered 500")
        self.files[key] = {"data": data, "type": content_type, "encoding": content_encoding, "cache": cache_control}
        self.puts.append(key)

    def delete(self, key):
        self.deleted.append(key)
        self.files.pop(key, None)

    def url(self, key):
        return f"https://media.example.invalid/{key}"

    def json(self, key):
        return json.loads(gzip.decompress(self.files[key]["data"]))


class VectorTests(SimpleTestCase):
    def test_every_case(self) -> None:
        for case in VECTORS["cases"]:
            with self.subTest(case=case["name"]):
                found = [card for card in VECTORS["cards"] if card_filters.matches(card, case["filters"])]
                ordered = card_filters.sort_cards(found, case.get("sort", "new"), move=case.get("move"))
                self.assertEqual([card["id"] for card in ordered], case["expected_ids"])

    def test_every_facet_case(self) -> None:
        for case in VECTORS["facet_cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(card_filters.facets(VECTORS["cards"], case["filters"]), case["facets"])

    def test_cards_have_the_contract_shape(self) -> None:
        names = set(PublicCardSerializer().fields)
        for card in VECTORS["cards"]:
            self.assertEqual(set(card), names, card["id"])

    def test_cases_cover_what_the_audit_named(self) -> None:
        text = json.dumps(VECTORS, ensure_ascii=False)
        for needed in ('"price_period": "night"', '"price_period": "week"', '"price_currency": "EUR"', '"size_m2": null', '"available_from": null', '"city": "North York"'):
            self.assertIn(needed, text)


class ValueRatioTests(SimpleTestCase):
    def card(self, pk, area, price, size, kind="offer"):
        return {"id": pk, "kind": kind, "area": area, "price_monthly": price, "size_m2": size}

    def test_against_the_median_of_its_own_area(self) -> None:
        cards = [self.card(1, "A", 1000, 100), self.card(2, "a", 2000, 100), self.card(3, "A", 3000, 100), self.card(4, "B", 5000, 100)]
        card_filters.add_value_ratio(cards)
        self.assertEqual([card["value_ratio"] for card in cards], [0.5, 1.0, 1.5, 1.0])

    def test_no_price_size_or_a_wanted_post_has_none(self) -> None:
        cards = [self.card(1, "A", None, 100), self.card(2, "A", 1000, None), self.card(3, "A", 1000, 100, kind="wanted")]
        card_filters.add_value_ratio(cards)
        self.assertEqual([card["value_ratio"] for card in cards], [None, None, None])


class SnapshotCase(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        HomeSettings.objects.update_or_create(pk=1, defaults={"snapshots_on": True})
        self.storage = FakeStorage()
        patcher = patch("home.snapshots.get_storage", return_value=self.storage)
        patcher.start()
        self.addCleanup(patch.stopall)
        self.home = self.listing(contact_telegram=HANDLE, contact_phone="416-555-0142")

    def manifest(self) -> dict:
        return self.storage.json(snapshots.MANIFEST_KEY)

    def city_file(self) -> dict:
        return self.storage.json(next(key for key in self.storage.files if key.startswith("feeds/toronto.")))


class WriterTests(SnapshotCase):
    def test_off_writes_nothing(self) -> None:
        HomeSettings.objects.update(snapshots_on=False)
        self.assertEqual(snapshots.write_snapshots()["skipped"], "snapshots are off in Home settings")
        self.assertEqual(self.storage.puts, [])

    def test_no_storage_writes_nothing(self) -> None:
        with patch("home.snapshots.get_storage", return_value=None):
            self.assertIn("skipped", snapshots.write_snapshots())

    def test_files_and_manifest_have_their_headers_and_shape(self) -> None:
        self.assertTrue(snapshots.write_snapshots()["ok"])
        key = next(key for key in self.storage.files if key.startswith("feeds/toronto."))
        stored = self.storage.files[key]
        self.assertEqual((stored["encoding"], stored["cache"]), ("gzip", IMMUTABLE_CACHE))
        self.assertTrue(stored["type"].startswith("application/json"))
        manifest_file = self.storage.files[snapshots.MANIFEST_KEY]
        self.assertEqual((manifest_file["encoding"], manifest_file["cache"]), ("gzip", snapshots.MANIFEST_CACHE))
        manifest = self.manifest()
        self.assertEqual(set(manifest), set(ManifestSerializer().fields))
        self.assertEqual(manifest["schema_version"], snapshots.SCHEMA_VERSION)
        self.assertFalse(manifest["alerts_on"])
        self.assertEqual(manifest["maintenance"], {"on": False, "until": None})
        city = manifest["cities"][0]
        self.assertEqual(set(city), set(ManifestCitySerializer().fields))
        self.assertEqual((city["city"], city["city_slug"], city["listing_count"]), ("Toronto", "toronto", 1))
        self.assertTrue(city["url"].endswith(key))
        self.assertEqual(city["bytes"], len(stored["data"]))
        body = self.city_file()
        self.assertEqual(set(body), set(SnapshotFileSerializer().fields))
        self.assertEqual(set(body["listings"][0]), set(PublicCardSerializer().fields))

    def test_same_content_uploads_no_file_but_refreshes_the_manifest(self) -> None:
        snapshots.write_snapshots()
        first = self.manifest()["generated_at"]
        self.storage.puts.clear()
        result = snapshots.write_snapshots()
        self.assertEqual((result["written"], result["unchanged"]), (0, 1))
        self.assertEqual(self.storage.puts, [snapshots.MANIFEST_KEY])
        self.assertGreater(self.manifest()["generated_at"], first)

    def test_a_change_gives_a_new_file_and_old_versions_are_pruned(self) -> None:
        versions = []
        for price in (900, 950, 1000, 1050, 1100):
            HomeListing.objects.filter(pk=self.home.pk).update(price_amount=price)
            snapshots.write_snapshots()
            versions.append(self.manifest()["cities"][0]["version"])
        self.assertEqual(len(set(versions)), 5)
        kept = [key for key in self.storage.files if key.startswith("feeds/toronto.")]
        self.assertEqual(len(kept), snapshots.KEEP_VERSIONS)
        self.assertEqual(len(self.storage.deleted), 2)
        self.assertIn(f"feeds/toronto.{versions[-1]}.json", kept)

    def test_only_shown_listings_go_in(self) -> None:
        for number, change in ((2, {"status": ListingStatus.RENTED}), (3, {"needs_review": True}), (4, {"status": ListingStatus.REPORTED}), (5, {"last_seen": timezone.now() - timedelta(days=40)})):
            listing = self.listing(number, contact_telegram=f"person_{number}")
            HomeListing.objects.filter(pk=listing.pk).update(**{"status": ListingStatus.ACTIVE, "canonical": None, "needs_review": False, **change})
        HomeListing.objects.filter(pk=self.home.pk).update(status=ListingStatus.ACTIVE, canonical=None)
        snapshots.write_snapshots()
        self.assertEqual([card["id"] for card in self.city_file()["listings"]], [self.home.pk])

    def test_metro_by_alias_and_wanted_posts_go_in(self) -> None:
        self.assertEqual(self.home.city, "North York")
        wanted = self.listing(2, kind="wanted", price_max=1200, contact_telegram="person_two")
        HomeListing.objects.filter(pk__in=[self.home.pk, wanted.pk]).update(status=ListingStatus.ACTIVE, canonical=None, needs_review=False)
        snapshots.write_snapshots()
        cards = {card["id"]: card for card in self.city_file()["listings"]}
        self.assertEqual(cards[self.home.pk]["metro"], "Toronto")
        self.assertEqual(cards[wanted.pk]["kind"], "wanted")

    def test_a_city_with_no_listing_still_gets_a_file(self) -> None:
        HomeChannel.objects.create(country="CA", city="Montreal", username="mtl")
        snapshots.write_snapshots()
        counts = {city["city_slug"]: city["listing_count"] for city in self.manifest()["cities"]}
        self.assertEqual(counts["montreal"], 0)

    def test_a_disabled_channel_is_not_in_the_manifest(self) -> None:
        HomeChannel.objects.create(country="CA", city="Hidden", username="hidden", enabled=False)
        snapshots.write_snapshots()
        self.assertNotIn("hidden", {city["city_slug"] for city in self.manifest()["cities"]})

    def test_holds_no_contact_or_source(self) -> None:
        HomeListing.objects.filter(pk=self.home.pk).update(summary_fa=f"پیام {HANDLE} یا wa.me/1416 یا 0176 1234567", area=f"Yonge {HANDLE}")
        snapshots.write_snapshots()
        text = json.dumps(self.city_file(), ensure_ascii=False) + json.dumps(self.manifest(), ensure_ascii=False)
        for leaked in (HANDLE, "4165550142", "416-555", "RentToronto", "wa.me", "0176", "author", "source_url", "contact_"):
            self.assertNotIn(leaked, text)

    def test_a_failed_city_keeps_the_old_file_and_reports_it(self) -> None:
        snapshots.write_snapshots()
        version = self.manifest()["cities"][0]["version"]
        written_at = HomeSettings.load().snapshots_written_at
        HomeListing.objects.filter(pk=self.home.pk).update(price_amount=1234)
        self.storage.fail_on = "feeds/toronto"
        result = snapshots.write_snapshots()
        self.assertEqual((result["ok"], result["failed"]), (False, 1))
        self.assertEqual(self.manifest()["cities"][0]["version"], version)
        row = HomeSettings.load()
        self.assertIn("toronto", row.snapshot_error)
        self.assertEqual(row.snapshots_written_at, written_at)
        self.storage.fail_on = ""
        self.assertTrue(snapshots.write_snapshots()["ok"])
        self.assertEqual(HomeSettings.load().snapshot_error, "")

    def test_a_failed_manifest_is_reported(self) -> None:
        self.storage.fail_on = "manifest"
        self.assertFalse(snapshots.write_snapshots()["ok"])
        self.assertIn("manifest", HomeSettings.load().snapshot_error)

    def test_over_budget_is_logged_and_still_written(self) -> None:
        with patch("home.snapshots.BUDGET_BYTES", 10), self.assertLogs("home.snapshots", level="WARNING") as logs:
            self.assertTrue(snapshots.write_snapshots()["ok"])
        self.assertIn("snapshot_over_budget", logs.output[0])

    def test_runs_at_the_end_of_a_whole_run_only(self) -> None:
        HomeSettings.objects.update(ingest_enabled=False, review_enabled=False, media_enabled=False, vision_enabled=False, publish_enabled=False, alerts_enabled=False, freshness_enabled=False)
        with patch("home.job.write_snapshots", return_value={"ok": True}) as writer:
            out = run_home_job(step="all")
            self.assertEqual(out["snapshots"], {"ok": True})
            writer.assert_called_once()
            writer.reset_mock()
            self.assertNotIn("snapshots", run_home_job(step="publish"))
        writer.assert_not_called()

    def test_meta_says_where_the_manifest_is_only_while_on(self) -> None:
        url = self.client.get("/api/v1/public/meta/").json()["manifest_url"]
        self.assertEqual(url, f"https://media.example.invalid/{snapshots.MANIFEST_KEY}")
        HomeSettings.objects.update(snapshots_on=False)
        self.assertEqual(self.client.get("/api/v1/public/meta/").json()["manifest_url"], "")

    def test_a_submit_writes_its_own_city_and_never_fails_the_post(self) -> None:
        with patch("home.snapshots.write_snapshots") as writer:
            snapshots.after_submit(self.home)
        self.assertEqual(writer.call_args.kwargs["only"], [self.channel])
        with patch("home.snapshots.write_snapshots", side_effect=RuntimeError("boom")), self.assertLogs("home.snapshots", level="ERROR"):
            snapshots.after_submit(self.home)
        HomeListing.objects.filter(pk=self.home.pk).update(needs_review=True)
        self.home.refresh_from_db()
        with patch("home.snapshots.write_snapshots") as writer:
            snapshots.after_submit(self.home)
        writer.assert_not_called()

    def test_health_shows_a_stale_writer_only_while_on(self) -> None:
        from api.config_check import freshness_report

        row = next(item for item in freshness_report() if item.key == "housing_snapshots")
        self.assertTrue(row.active)
        HomeSettings.objects.update(snapshots_on=False)
        row = next(item for item in freshness_report() if item.key == "housing_snapshots")
        self.assertFalse(row.active)


class StorageTests(SimpleTestCase):
    def storage(self) -> R2Storage:
        return R2Storage(account_id="acc", access_key="key", secret_key="secret", bucket="bucket", public_base="https://media.example.invalid")

    def sent(self, call):
        return call.args[0]

    def test_put_sends_encoding_and_cache_and_photos_stay_as_they_were(self) -> None:
        response = patch("home.storage.urllib.request.urlopen").start()
        self.addCleanup(patch.stopall)
        response.return_value.__enter__.return_value.status = 200
        self.storage().put("feeds/a.json", b"x", "application/json", content_encoding="gzip", cache_control="public, max-age=60")
        request = self.sent(response.call_args)
        self.assertEqual((request.get_header("Content-encoding"), request.get_header("Cache-control")), ("gzip", "public, max-age=60"))
        self.storage().put("home/a.jpg", b"x")
        request = self.sent(response.call_args)
        self.assertIsNone(request.get_header("Content-encoding"))
        self.assertEqual(request.get_header("Cache-control"), IMMUTABLE_CACHE)

    def test_delete_is_signed(self) -> None:
        response = patch("home.storage.urllib.request.urlopen").start()
        self.addCleanup(patch.stopall)
        response.return_value.__enter__.return_value.status = 204
        self.storage().delete("feeds/a.json")
        request = self.sent(response.call_args)
        self.assertEqual(request.get_method(), "DELETE")
        self.assertIn("AWS4-HMAC-SHA256", request.get_header("Authorization"))

    def test_local_storage_round_trip(self) -> None:
        from home.storage import LocalStorage

        with tempfile.TemporaryDirectory() as root:
            storage = LocalStorage(root, "https://x")
            storage.put("feeds/a.json", b"x", "application/json", content_encoding="gzip", cache_control="c")
            self.assertTrue((Path(root) / "feeds/a.json").exists())
            storage.delete("feeds/a.json")
            storage.delete("feeds/a.json")
            self.assertFalse((Path(root) / "feeds/a.json").exists())


class FeedTests(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        self.home = self.listing(contact_telegram=HANDLE, contact_phone="416-555-0142")

    def get(self, **query):
        return self.client.get(FEED_PATTERN, {"country": "CA", "city": "Toronto", **query})

    def test_gives_cards_with_the_contract_shape_and_is_cacheable(self) -> None:
        response = self.get()
        self.assertIn("s-maxage=60", response["Cache-Control"])
        self.assertEqual(len(response.cookies), 0)
        body = response.json()
        self.assertEqual((body["total"], body["next_cursor"]), (1, None))
        self.assertEqual(set(body["results"][0]), set(PublicCardSerializer().fields))

    def test_city_by_slug_or_lower_case_country(self) -> None:
        self.assertEqual(self.get(city="toronto", country="ca").json()["total"], 1)

    def test_holds_no_contact_or_source(self) -> None:
        text = self.get().content.decode()
        for leaked in (HANDLE, "416-555", "RentToronto", "source_url"):
            self.assertNotIn(leaked, text)

    def test_runs_the_same_filters_on_the_monthly_value(self) -> None:
        HomeListing.objects.filter(pk=self.home.pk).update(price_amount=90, price_period="night")
        self.assertEqual(self.get(max=1000).json()["total"], 0)
        self.assertEqual(self.get(min=2000, max=3000).json()["total"], 1)
        self.assertEqual(self.get(types=["studio"]).json()["total"], 0)
        self.assertEqual(self.get(types=["room", "studio"]).json()["total"], 1)
        self.assertEqual(self.get(kind="wanted").json()["total"], 0)

    def test_sort_and_pages(self) -> None:
        ids = []
        for number in range(2, 5):
            listing = self.listing(number, contact_telegram=f"person_{number}", price_amount=900 + number * 100)
            ids.append(listing.pk)
        HomeListing.objects.update(status=ListingStatus.ACTIVE, canonical=None, needs_review=False)
        with patch("home_api.views.FEED_PAGE", 2):
            first = self.get(sort="dear").json()
            second = self.get(sort="dear", cursor=first["next_cursor"]).json()
        self.assertEqual([card["id"] for card in first["results"]], [ids[2], ids[1]])
        self.assertEqual(first["next_cursor"], "2")
        self.assertEqual(second["next_cursor"], None)
        self.assertEqual(second["total"], 4)

    def test_validation_failed(self) -> None:
        for query, field, code in (
            ({"country": ""}, "country", "blank"),
            ({"city": "Nowhere"}, "city", "invalid_choice"),
            ({"sort": "best"}, "sort", "invalid_choice"),
            ({"max": "abc"}, "max", "invalid"),
            ({"move": "soon"}, "move", "invalid"),
            ({"cursor": "-1"}, "cursor", "invalid"),
        ):
            with self.subTest(field=field):
                got = self.assert_error(self.get(**query), "validation_failed", FEED_PATTERN)
                self.assertEqual(got["fields"], {field: [code]})

    def test_feed_failed_when_it_cannot_be_built(self) -> None:
        with patch("home_api.views.city_cards", side_effect=RuntimeError("boom")):
            body = self.assert_error(self.get(), "feed_failed", FEED_PATTERN)
        self.assertNotIn("boom", body["detail"])

    def test_rate_limited(self) -> None:
        with patch("home_api.views.FeedThrottle.rate", "1/min"):
            self.get()
            self.assert_error(self.get(), "rate_limited", FEED_PATTERN)

    def test_snapshot_and_feed_agree(self) -> None:
        channel = self.channel
        cards = snapshots.city_cards(channel)
        self.assertEqual(cards[0]["id"], self.home.pk)
        self.assertEqual(cards[0], {**public_card(self.home), "value_ratio": cards[0]["value_ratio"]})
        self.assertEqual(self.get().json()["results"], json.loads(json.dumps(cards)))


