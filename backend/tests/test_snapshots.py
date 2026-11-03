from __future__ import annotations

import gzip
import json
import tempfile
import time
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from home import card_filters, snapshots
from home.alerts import monthly_price
from home.job import run_home_job
from home.models import HomeChannel, HomeListing, HomeSettings, ListingStatus
from home.public import public_card
from home.storage import IMMUTABLE_CACHE, R2Storage, StorageError
from home_api import views
from home_api.serializers import ManifestCitySerializer, ManifestSerializer, PublicCardSerializer, SnapshotFileSerializer
from tests.test_home_api import ApiCase

VECTORS = json.loads((Path(__file__).resolve().parent.parent / "home_api" / "testdata" / "filter_vectors.json").read_text(encoding="utf-8"))
FEED_PATTERN = "/api/v1/public/feed/"
HANDLE = "roomsbyali1"


class FakeStorage:
    timeout = 20

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


class RewriteTests(SimpleTestCase):
    def test_feeds_path_goes_to_the_bucket_through_a_rewrite(self) -> None:
        config = json.loads((Path(__file__).resolve().parent.parent / "vercel.json").read_text(encoding="utf-8"))
        rule = next(rule for rule in config["rewrites"] if rule["source"] == "/feeds/:path*")
        self.assertRegex(rule["destination"], r"^https://[^/]+/feeds/:path\*$")


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


class VectorDerivationTests(TestCase):
    """The file's numbers come from the real functions, so a hand-typed price or ratio cannot drift."""

    def test_price_monthly_comes_from_monthly_price(self) -> None:
        for card in VECTORS["cards"]:
            with self.subTest(card=card["id"]):
                amount = None if card["price_amount"] is None else Decimal(str(card["price_amount"]))
                listing = HomeListing(price_amount=amount, price_period=card["price_period"])
                monthly = monthly_price(listing)
                self.assertEqual(card["price_monthly"], None if monthly is None else float(monthly))

    def test_value_ratio_comes_from_add_value_ratio(self) -> None:
        stored = {card["id"]: card["value_ratio"] for card in VECTORS["cards"]}
        for metro in {card["metro"] for card in VECTORS["cards"]}:
            cards = [dict(card) for card in VECTORS["cards"] if card["metro"] == metro]
            card_filters.add_value_ratio(cards)
            for card in cards:
                with self.subTest(card=card["id"]):
                    self.assertEqual(card["value_ratio"], stored[card["id"]])

    def test_the_sort_case_is_not_flat(self) -> None:
        self.assertGreater(len({card["value_ratio"] for card in VECTORS["cards"] if card["value_ratio"] is not None}), 2)


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
        return self.storage.json(next(key for key in self.storage.files if key.startswith("feeds/ca-toronto.")))


class WriterTests(SnapshotCase):
    def file_key(self, version: str = "") -> str:
        return next(key for key in self.storage.files if key.startswith(f"feeds/ca-toronto.{version}"))

    def test_off_writes_nothing(self) -> None:
        HomeSettings.objects.update(snapshots_on=False)
        self.assertEqual(snapshots.write_snapshots()["skipped"], "snapshots are off in Home settings")
        self.assertEqual(self.storage.puts, [])

    def test_no_storage_writes_nothing(self) -> None:
        with patch("home.snapshots.get_storage", return_value=None):
            self.assertIn("skipped", snapshots.write_snapshots())

    def test_files_and_manifest_have_their_headers_and_shape(self) -> None:
        self.assertTrue(snapshots.write_snapshots()["ok"])
        key = self.file_key()
        stored = self.storage.files[key]
        self.assertEqual((stored["encoding"], stored["cache"]), ("gzip", snapshots.FILE_CACHE))
        self.assertTrue(stored["type"].startswith("application/json"))
        manifest_file = self.storage.files[snapshots.MANIFEST_KEY]
        self.assertEqual((manifest_file["encoding"], manifest_file["cache"]), ("gzip", snapshots.MANIFEST_CACHE))
        manifest = self.manifest()
        self.assertEqual(set(manifest), set(ManifestSerializer().fields))
        self.assertEqual(manifest["schema_version"], snapshots.SCHEMA_VERSION)
        self.assertEqual(manifest["maintenance"], {"on": False, "until": None})
        city = manifest["cities"][0]
        self.assertEqual(set(city), set(ManifestCitySerializer().fields))
        self.assertEqual((city["city"], city["city_slug"], city["listing_count"]), ("Toronto", "toronto", 1))
        self.assertEqual(city["url"], f"/{key}")
        self.assertEqual(city["bytes"], len(stored["data"]))
        body = self.city_file()
        self.assertEqual(set(body), set(SnapshotFileSerializer().fields))
        self.assertEqual(set(body["listings"][0]), set(PublicCardSerializer().fields))

    def test_same_slug_in_two_countries_stays_apart(self) -> None:
        HomeChannel.objects.create(country="DE", city="Toronto", username="de_toronto")
        snapshots.write_snapshots()
        self.assertEqual(sorted(HomeSettings.load().snapshot_state["cities"]), ["ca-toronto", "de-toronto"])
        self.assertTrue(any(key.startswith("feeds/de-toronto.") for key in self.storage.files))
        self.assertEqual(sorted(city["country"] for city in self.manifest()["cities"]), ["CA", "DE"])

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
        kept = [key for key in self.storage.files if key.startswith("feeds/ca-toronto.")]
        self.assertEqual(len(kept), snapshots.KEEP_VERSIONS)
        self.assertEqual(len(self.storage.deleted), 2)
        self.assertIn(f"feeds/ca-toronto.{versions[-1]}.json", kept)

    def test_a_failed_manifest_prunes_nothing_and_saves_no_state(self) -> None:
        for price in (900, 950, 1000, 1050):
            HomeListing.objects.filter(pk=self.home.pk).update(price_amount=price)
            snapshots.write_snapshots()
        before = HomeSettings.load().snapshot_state["cities"]["ca-toronto"]
        deleted = list(self.storage.deleted)
        HomeListing.objects.filter(pk=self.home.pk).update(price_amount=1100)
        self.storage.fail_on = "manifest"
        self.assertFalse(snapshots.write_snapshots()["ok"])
        self.assertEqual(self.storage.deleted, deleted)
        row = HomeSettings.load()
        self.assertEqual(row.snapshot_state["cities"]["ca-toronto"], before)
        self.assertIn("manifest", row.snapshot_error)
        self.assertIn(f"feeds/ca-toronto.{before['version']}.json", self.storage.files)

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
        self.storage.fail_on = "feeds/ca-toronto"
        result = snapshots.write_snapshots()
        self.assertEqual((result["ok"], result["failed"]), (False, 1))
        self.assertEqual(self.manifest()["cities"][0]["version"], version)
        row = HomeSettings.load()
        self.assertIn("ca-toronto", row.snapshot_error)
        self.assertEqual(row.snapshots_written_at, written_at)
        self.storage.fail_on = ""
        self.assertTrue(snapshots.write_snapshots()["ok"])
        self.assertEqual(HomeSettings.load().snapshot_error, "")

    def test_any_exception_is_caught_and_logged(self) -> None:
        with patch("home.snapshots.city_cards", side_effect=ValueError("boom")), self.assertLogs("home.snapshots", level="WARNING"):
            result = snapshots.write_snapshots()
        self.assertEqual((result["ok"], result["failed"]), (False, 1))
        with patch("home.snapshots._write", side_effect=KeyError("boom")), self.assertLogs("home.snapshots", level="ERROR") as logs:
            self.assertFalse(snapshots.write_snapshots()["ok"])
        self.assertIn("snapshot_run_failed", logs.output[0])
        self.storage.put = lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("boom"))
        with self.assertLogs("home.snapshots", level="WARNING"):
            self.assertFalse(snapshots.write_snapshots()["ok"])

    def test_the_job_survives_a_writer_that_raises(self) -> None:
        HomeSettings.objects.update(ingest_enabled=False, review_enabled=False, media_enabled=False, vision_enabled=False, publish_enabled=False, alerts_enabled=False, freshness_enabled=False)
        with patch("home.job.write_snapshots", side_effect=RuntimeError("boom")), self.assertLogs("home.job", level="ERROR"):
            out = run_home_job(step="all")
        self.assertFalse(out["snapshots"]["ok"])

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
        self.assertEqual(url, f"/{snapshots.MANIFEST_KEY}")
        HomeSettings.objects.update(snapshots_on=False)
        self.assertEqual(self.client.get("/api/v1/public/meta/").json()["manifest_url"], "")

    def test_health_shows_a_stale_writer_only_while_on(self) -> None:
        from api.config_check import freshness_report

        row = next(item for item in freshness_report() if item.key == "housing_snapshots")
        self.assertTrue(row.active)
        HomeSettings.objects.update(snapshots_on=False)
        row = next(item for item in freshness_report() if item.key == "housing_snapshots")
        self.assertFalse(row.active)


class SingleCityWriteTests(SnapshotCase):
    def montreal(self) -> HomeChannel:
        return HomeChannel.objects.create(country="CA", city="Montreal", username="mtl")

    def test_no_manifest_until_every_city_has_a_file(self) -> None:
        self.montreal()
        result = snapshots.write_snapshots(only=[self.channel])
        self.assertFalse(result["manifest"])
        self.assertNotIn(snapshots.MANIFEST_KEY, self.storage.files)
        self.assertEqual(HomeSettings.load().snapshot_state.get("cities"), None)
        self.assertTrue(snapshots.write_snapshots()["manifest"])
        self.assertEqual(len(self.manifest()["cities"]), 2)

    def test_a_failed_city_on_the_first_run_means_no_manifest(self) -> None:
        self.montreal()
        self.storage.fail_on = "feeds/ca-montreal"
        self.assertFalse(snapshots.write_snapshots()["manifest"])
        self.assertNotIn(snapshots.MANIFEST_KEY, self.storage.files)

    def test_does_not_stamp_fresh_or_move_generated_at(self) -> None:
        snapshots.write_snapshots()
        row = HomeSettings.load()
        generated_at = self.manifest()["generated_at"]
        HomeListing.objects.filter(pk=self.home.pk).update(price_amount=1234)
        self.assertTrue(snapshots.write_snapshots(only=[self.channel])["manifest"])
        self.assertEqual(HomeSettings.load().snapshots_written_at, row.snapshots_written_at)
        self.assertEqual(self.manifest()["generated_at"], generated_at)
        self.assertEqual(self.city_file_price(), 1234)

    def city_file_price(self) -> float:
        version = self.manifest()["cities"][0]["version"]
        return self.storage.json(f"feeds/ca-toronto.{version}.json")["listings"][0]["price_amount"]

    def test_clears_only_its_own_error(self) -> None:
        montreal = self.montreal()
        snapshots.write_snapshots()
        HomeListing.objects.filter(pk=self.home.pk).update(price_amount=1234)
        self.storage.fail_on = "feeds/ca-toronto"
        snapshots.write_snapshots()
        self.assertIn("ca-toronto", HomeSettings.load().snapshot_error)
        self.storage.fail_on = ""
        snapshots.write_snapshots(only=[montreal])
        self.assertIn("ca-toronto", HomeSettings.load().snapshot_error)
        snapshots.write_snapshots(only=[self.channel])
        self.assertEqual(HomeSettings.load().snapshot_error, "")

    def test_prunes_nothing_so_a_post_does_not_wait_on_deletes(self) -> None:
        for price in (900, 950, 1000, 1050, 1100):
            HomeListing.objects.filter(pk=self.home.pk).update(price_amount=price)
            snapshots.write_snapshots(only=[self.channel])
        self.assertEqual(self.storage.deleted, [])
        snapshots.write_snapshots()
        self.assertEqual(len(self.storage.deleted), 2)

    def test_a_submit_writes_its_own_city_briefly_and_never_fails_the_post(self) -> None:
        with patch("home.snapshots.write_snapshots") as writer:
            snapshots.after_submit(self.home)
        self.assertEqual(writer.call_args.kwargs["only"], [self.channel])
        self.assertEqual(writer.call_args.kwargs["timeout"], snapshots.SUBMIT_TIMEOUT_SECONDS)
        snapshots.write_snapshots(only=[self.channel], timeout=snapshots.SUBMIT_TIMEOUT_SECONDS)
        self.assertEqual(self.storage.timeout, snapshots.SUBMIT_TIMEOUT_SECONDS)
        with patch("home.snapshots.write_snapshots", side_effect=RuntimeError("boom")), self.assertLogs("home.snapshots", level="ERROR"):
            snapshots.after_submit(self.home)
        HomeListing.objects.filter(pk=self.home.pk).update(needs_review=True)
        self.home.refresh_from_db()
        with patch("home.snapshots.write_snapshots") as writer:
            snapshots.after_submit(self.home)
        writer.assert_not_called()


class RunBudgetTests(SnapshotCase):
    def test_no_time_means_no_upload_and_no_manifest(self) -> None:
        result = snapshots.write_snapshots(stop_at=time.monotonic() + 1)
        self.assertTrue(result["out_of_time"])
        self.assertEqual(self.storage.puts, [])

    def test_time_for_a_city_but_not_the_manifest_is_not_a_new_state(self) -> None:
        snapshots.write_snapshots()
        before = HomeSettings.load().snapshot_state
        HomeListing.objects.filter(pk=self.home.pk).update(price_amount=1234)
        self.storage.puts.clear()
        margin = 2 * snapshots.PER_CALL_RESERVE_SECONDS

        def slow(*args, **kwargs):
            clock.return_value += margin
            return original(*args, **kwargs)

        original = snapshots.city_cards
        with patch("home.snapshots.time.monotonic") as clock, patch("home.snapshots.city_cards", side_effect=slow):
            clock.return_value = 100.0
            result = snapshots.write_snapshots(stop_at=100.0 + margin + 1)
        self.assertTrue(result["out_of_time"])
        self.assertNotIn(snapshots.MANIFEST_KEY, self.storage.puts)
        self.assertEqual(HomeSettings.load().snapshot_state["cities"], before["cities"])

    def test_deletes_stop_at_the_budget_and_finish_next_time(self) -> None:
        for price in (900, 950, 1000, 1050, 1100):
            HomeListing.objects.filter(pk=self.home.pk).update(price_amount=price)
            snapshots.write_snapshots(only=[self.channel])
        self.storage.delete = patch.object(self.storage, "delete", wraps=self.storage.delete).start()
        cities = HomeSettings.load().snapshot_state["cities"]
        with patch("home.snapshots.time.monotonic", return_value=100.0):
            snapshots._prune(self.storage, cities, stop_at=100.0)
        self.storage.delete.assert_not_called()
        self.assertEqual(len(cities["ca-toronto"]["keys"]), 5)
        snapshots.write_snapshots()
        self.assertEqual(self.storage.delete.call_count, 2)


class TakedownTests(SnapshotCase):
    def test_purge_deletes_all_but_the_current_file(self) -> None:
        for price in (900, 950, 1000):
            HomeListing.objects.filter(pk=self.home.pk).update(price_amount=price)
            snapshots.write_snapshots()
        current = self.manifest()["cities"][0]["version"]
        out = StringIO()
        call_command("home_snapshots_purge", stdout=out)
        self.assertIn("'deleted': 2", out.getvalue())
        kept = [key for key in self.storage.files if key.startswith("feeds/ca-toronto.")]
        self.assertEqual(kept, [f"feeds/ca-toronto.{current}.json"])
        self.assertEqual(HomeSettings.load().snapshot_state["cities"]["ca-toronto"]["keys"], kept)

    def test_a_failed_delete_is_kept_for_next_time(self) -> None:
        for price in (900, 950):
            HomeListing.objects.filter(pk=self.home.pk).update(price_amount=price)
            snapshots.write_snapshots()
        with patch.object(self.storage, "delete", side_effect=StorageError("R2 answered 500")), self.assertLogs("home.snapshots", level="WARNING"):
            self.assertEqual(snapshots.purge_old_versions()["failed"], 1)
        self.assertEqual(len(HomeSettings.load().snapshot_state["cities"]["ca-toronto"]["keys"]), 2)


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
        cache.clear()
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

    def test_feed_and_login_have_separate_budgets(self) -> None:
        with patch("home_api.views.FeedThrottle.rate", "1/min"):
            self.get()
            self.assertEqual(self.get().status_code, 429)
            self.assertEqual(self.client.post("/api/v1/auth/exchange/", {}, content_type="application/json").status_code, 400)
        cache.clear()
        with patch("home_api.views.ExchangeThrottle.rate", "1/min"):
            self.client.post("/api/v1/auth/exchange/", {}, content_type="application/json")
            self.assertEqual(self.client.post("/api/v1/auth/exchange/", {}, content_type="application/json").status_code, 429)
            self.assertEqual(self.get().status_code, 200)

    def test_every_throttle_has_its_own_scope(self) -> None:
        scopes = [views.ExchangeThrottle.scope, views.WriteThrottle.scope, views.ContactThrottle.scope, views.FeedThrottle.scope]
        self.assertEqual(len(set(scopes)), 4)
        for scope in scopes:
            self.assertTrue(scope.startswith("home_"))

    def test_builds_cards_for_this_city_only(self) -> None:
        montreal = HomeChannel.objects.create(country="CA", city="Montreal", username="mtl")
        other = self.listing(2, city="Montreal", area="Plateau", contact_telegram="person_two")
        HomeListing.objects.filter(pk=other.pk).update(status=ListingStatus.ACTIVE, canonical=None, needs_review=False)
        self.assertEqual(snapshots._shown_listings(montreal).count(), 1)
        with patch("home.snapshots.public_card", wraps=public_card) as built:
            self.get()
        self.assertEqual(built.call_count, 1)

    def test_the_card_list_is_bounded_and_reused_across_query_strings(self) -> None:
        with patch("home.snapshots.public_card", wraps=public_card) as built:
            self.get()
            self.get(max=5000)
            self.get(sort="dear")
        self.assertEqual(built.call_count, 1)
        cache.clear()
        for number in range(2, 5):
            listing = self.listing(number, contact_telegram=f"person_{number}")
            HomeListing.objects.filter(pk=listing.pk).update(status=ListingStatus.ACTIVE, canonical=None, needs_review=False)
        HomeListing.objects.filter(pk=self.home.pk).update(status=ListingStatus.ACTIVE, canonical=None)
        with patch("home.snapshots.FEED_CARD_LIMIT", 2):
            self.assertEqual(self.get().json()["total"], 2)

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
        with patch("home_api.views.feed_cards", side_effect=RuntimeError("boom")):
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

    def test_contract_says_a_file_is_not_immutable(self) -> None:
        self.assertEqual(snapshots.FILE_CACHE, "public, max-age=86400")


