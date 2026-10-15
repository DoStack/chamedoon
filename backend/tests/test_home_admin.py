from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth.models import User as StaffUser
from django.test import TestCase, override_settings

from ai.openrouter import ChatResult
from home import conf
from home.job import run_home_job
from home.models import HomeListing, HomeMedia, HomePost, HomeSettings, HomeSource, MediaStatus
from home.review import apply_review
from home.status import home_status
from home.storage import LocalStorage, R2Storage, get_storage
from home.telegram import HomeHistoryReader, database_invites, invite_hash
from tests.test_home import make_post, make_source
from tests.test_home_photos import CA_ACCEPT, CA_TEXT, ca_source

CRON = "cron-test-secret"


class SettingsPrecedenceTests(TestCase):
    def test_defaults(self) -> None:
        self.assertTrue(conf.home_enabled())
        self.assertEqual((conf.lookback_days(), conf.review_batch(), conf.media_batch()), (30, 60, 30))
        self.assertTrue(conf.step_enabled("ingest"))
        self.assertFalse(conf.step_enabled("vision"))

    def test_the_master_switch_turns_everything_off(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"enabled": False})
        self.assertFalse(conf.home_enabled())
        self.assertFalse(any(conf.step_enabled(step) for step in ("ingest", "review", "media", "vision")))

    def test_batches_are_clamped(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"lookback_days": 90, "review_batch": 500, "media_batch": 99})
        self.assertEqual((conf.lookback_days(), conf.review_batch(), conf.media_batch()), (30, 100, 60))

    def test_the_admin_value_wins_over_the_environment(self) -> None:
        with override_settings(HOME_VISION_MODEL="env/model", HOME_VISION_ENABLED=True):
            self.assertEqual(conf.vision_model(), "env/model")
            self.assertTrue(conf.step_enabled("vision"))
            HomeSettings.objects.update_or_create(pk=1, defaults={"vision_model": "admin/model"})
            self.assertEqual(conf.vision_model(), "admin/model")

    def test_storage_comes_from_the_admin_row_without_any_environment(self) -> None:
        self.assertIsNone(get_storage())
        HomeSettings.objects.update_or_create(
            pk=1,
            defaults={
                "media_backend": "r2",
                "r2_account_id": "acct",
                "r2_bucket": "bucket",
                "r2_public_base": "https://media.test",
                "r2_access_key_id": "AK",
            },
        )
        self.assertEqual(conf.missing_storage_values(), ["R2 secret access key"])
        self.assertIsNone(get_storage())
        HomeSettings.objects.filter(pk=1).update(r2_secret_access_key="SK")
        self.assertIsInstance(get_storage(), R2Storage)
        self.assertEqual(get_storage().url("a.jpg"), "https://media.test/a.jpg")

    @override_settings(
        HOME_MEDIA_BACKEND="r2",
        R2_ACCOUNT_ID="envacct",
        R2_ACCESS_KEY_ID="ENVAK",
        R2_SECRET_ACCESS_KEY="ENVSK",
        HOME_MEDIA_BUCKET="envbucket",
        HOME_MEDIA_PUBLIC_BASE="https://env.test",
    )
    def test_the_environment_still_works_as_a_fallback(self) -> None:
        self.assertIsInstance(get_storage(), R2Storage)
        HomeSettings.objects.update_or_create(pk=1, defaults={"r2_bucket": "adminbucket"})
        self.assertEqual(conf.storage_values()["bucket"], "adminbucket")
        self.assertEqual(conf.storage_values()["account_id"], "envacct")


class JobSwitchTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        make_source()

    def test_master_off_runs_nothing(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"enabled": False})
        with patch("home.job.ingest_sources") as ingest:
            result = run_home_job(step="all")
        ingest.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("switched off", result["skipped"])

    def test_a_single_step_switch_skips_only_that_step(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"ingest_enabled": False})
        with patch("home.job.ingest_sources") as ingest, patch("home.job.review_pending", return_value={"reviewed": 0}) as review:
            result = run_home_job(step="all")
        ingest.assert_not_called()
        review.assert_called_once()
        self.assertIn("switched off", result["ingest"]["skipped"])

    def test_batch_sizes_come_from_settings_when_not_given(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"review_batch": 7})
        with patch("home.job.review_pending", return_value={"reviewed": 0}) as review:
            run_home_job(step="review")
        self.assertEqual(review.call_args.kwargs["limit"], 7)


@override_settings(CRON_SECRET=CRON)
class CronSettingsTests(TestCase):
    def test_master_switch_stops_the_cron(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"enabled": False})
        response = self.client.get("/api/cron/home/", HTTP_AUTHORIZATION=f"Bearer {CRON}")
        self.assertEqual(response.status_code, 503)

    @patch("api.cron_views.run_home_job", return_value={"ok": True, "step": "all"})
    def test_without_a_limit_the_job_uses_the_settings(self, job) -> None:
        self.client.get("/api/cron/home/", HTTP_AUTHORIZATION=f"Bearer {CRON}")
        self.assertIsNone(job.call_args.kwargs["review_limit"])
        self.assertIsNone(job.call_args.kwargs["days"])


class InviteTests(TestCase):
    def test_every_link_shape_gives_the_hash(self) -> None:
        for link in (
            "https://t.me/+AbC123xyz",
            "t.me/joinchat/AbC123xyz",
            "https://t.me/AbC123xyz?start=1",
            "AbC123xyz",
            "  +AbC123xyz/ ",
            "http://telegram.me/joinchat/AbC123xyz",
        ):
            self.assertEqual(invite_hash(link), "AbC123xyz", link)
        self.assertEqual(invite_hash(""), "")

    def test_invites_saved_on_sources_reach_the_reader(self) -> None:
        HomeSource.objects.all().delete()
        make_source(username="khane_montreal", invite_link="https://t.me/+SecretHash1")
        make_source(username="open_channel")
        self.assertEqual(database_invites(), {"khane_montreal": "SecretHash1"})
        reader = HomeHistoryReader()
        try:
            self.assertEqual(reader._invites()["khane_montreal"], "SecretHash1")
            self.assertNotIn("open_channel", reader._invites())
        finally:
            reader.close()

    @override_settings(HOME_SOURCE_INVITES="khane_montreal:EnvHash, other:EnvOther")
    def test_the_database_wins_over_the_environment(self) -> None:
        HomeSource.objects.all().delete()
        make_source(username="khane_montreal", invite_link="t.me/+DbHash")
        reader = HomeHistoryReader()
        try:
            self.assertEqual(reader._invites()["khane_montreal"], "DbHash")
            self.assertEqual(reader._invites()["other"], "EnvOther")
        finally:
            reader.close()

    def test_the_seeded_private_group_starts_without_a_link(self) -> None:
        self.assertEqual(HomeSource.objects.get(username="khane_montreal").invite_link, "")


class AdminPageTests(TestCase):
    def setUp(self) -> None:
        StaffUser.objects.create_superuser("ops", "ops@example.com", "ops-pass")
        self.client.login(username="ops", password="ops-pass")

    def test_run_page_shows_status_and_sources(self) -> None:
        page = self.client.get("/admin/home/run/")
        self.assertEqual(page.status_code, 200)
        text = page.content.decode()
        self.assertIn("Chamedoon Home", text)
        self.assertIn("@RentToronto", text)
        self.assertIn("Read sources", text)
        self.assertIn("Photo storage", text)

    def test_sidebar_links_to_the_home_pages(self) -> None:
        text = self.client.get("/admin/").content.decode()
        self.assertIn("/admin/home/run/", text)
        self.assertIn("/admin/home/homesettings/", text)

    @patch("home.admin_views.run_home_job", return_value={"ok": True, "step": "review", "review": {"reviewed": 2, "accepted": 1, "rejected": 1, "reposts": 0, "errors": 0, "remaining": 5}})
    def test_run_page_runs_one_step(self, job) -> None:
        page = self.client.post("/admin/home/run/", {"action": "review", "days": "10", "limit": "4"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(job.call_args.kwargs["step"], "review")
        self.assertEqual(job.call_args.kwargs["review_limit"], 4)
        self.assertEqual(job.call_args.kwargs["days"], 10)
        self.assertIn("2 reviewed", page.content.decode())
        self.assertIn("5 still waiting", page.content.decode())

    @patch("home.admin_views.run_home_job", return_value={"ok": True, "step": "ingest", "ingest": {"created": 3}})
    def test_a_single_source_can_be_read(self, job) -> None:
        self.client.post("/admin/home/run/", {"action": "ingest", "source": "RentToronto"})
        self.assertEqual(job.call_args.kwargs["usernames"], ["RentToronto"])

    def test_unknown_action_is_refused_politely(self) -> None:
        page = self.client.post("/admin/home/run/", {"action": "drop_tables"})
        self.assertEqual(page.status_code, 200)
        self.assertIn("Unknown action", page.content.decode())

    def test_run_page_needs_a_staff_login(self) -> None:
        self.client.logout()
        self.assertEqual(self.client.get("/admin/home/run/").status_code, 302)

    def test_settings_changelist_opens_the_single_row(self) -> None:
        response = self.client.get("/admin/home/homesettings/")
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertIn("Cloudflare R2", page.content.decode())

    def test_saving_settings_keeps_the_secret_when_the_field_is_left_blank(self) -> None:
        row = HomeSettings.load()
        row.r2_secret_access_key = "KEEP-ME"
        row.save()
        url = f"/admin/home/homesettings/{row.pk}/change/"
        page = self.client.get(url)
        self.assertNotIn("KEEP-ME", page.content.decode())
        self.assertIn("A secret is saved", page.content.decode())
        data = {
            "enabled": "on",
            "ingest_enabled": "on",
            "review_enabled": "on",
            "media_enabled": "on",
            "lookback_days": "20",
            "review_batch": "9",
            "media_batch": "6",
            "media_backend": "r2",
            "r2_account_id": "acct",
            "r2_bucket": "b",
            "r2_public_base": "https://m.test",
            "r2_access_key_id": "AK",
            "r2_secret_access_key": "",
            "vision_model": "",
            "bot_token": "",
            "publish_batch": "5",
            "publish_window_days": "3",
            "publish_daily_cap": "40",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertEqual((row.r2_secret_access_key, row.review_batch, row.lookback_days), ("KEEP-ME", 9, 20))
        self.client.post(url, {**data, "r2_secret_access_key": "NEW-SECRET"})
        row.refresh_from_db()
        self.assertEqual(row.r2_secret_access_key, "NEW-SECRET")

    def test_source_admin_has_the_invite_field_and_actions(self) -> None:
        source = HomeSource.objects.get(username="khane_montreal")
        page = self.client.get(f"/admin/home/homesource/{source.pk}/change/")
        self.assertIn("invite_link", page.content.decode())
        self.client.post(
            "/admin/home/homesource/",
            {"action": "disable_selected", "_selected_action": [source.pk]},
        )
        source.refresh_from_db()
        self.assertFalse(source.enabled)
        source.newest_message_id = 5
        source.backfill_complete = True
        source.save()
        self.client.post(
            "/admin/home/homesource/",
            {"action": "reread_from_start", "_selected_action": [source.pk]},
        )
        source.refresh_from_db()
        self.assertEqual((source.newest_message_id, source.backfill_complete), (None, False))

    def test_post_listing_and_media_actions(self) -> None:
        HomeSource.objects.all().delete()
        source = ca_source()
        post = make_post(source, text=CA_TEXT)
        apply_review(post, dict(CA_ACCEPT))
        listing = HomeListing.objects.get(post=post)
        listing.needs_review = True
        listing.save()
        self.client.post("/admin/home/homelisting/", {"action": "approve_selected", "_selected_action": [listing.pk]})
        listing.refresh_from_db()
        self.assertFalse(listing.needs_review)
        self.client.post("/admin/home/homelisting/", {"action": "mark_expired", "_selected_action": [listing.pk]})
        listing.refresh_from_db()
        self.assertEqual(listing.status, "expired")
        rejected = make_post(source, 2, text=CA_TEXT + " 2")
        apply_review(rejected, {"accept": False, "reject_reason": "ad"})
        self.client.post("/admin/home/homepost/", {"action": "review_again", "_selected_action": [rejected.pk]})
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, "new")
        media = HomeMedia.objects.create(source=source, post=post, telegram_message_id=9, status=MediaStatus.FAILED, error="x")
        self.client.post("/admin/home/homemedia/", {"action": "retry_failed", "_selected_action": [media.pk]})
        media.refresh_from_db()
        self.assertEqual((media.status, media.error), ("pending", ""))
        for name in ("homepost", "homelisting", "homemedia", "homesource"):
            self.assertEqual(self.client.get(f"/admin/home/{name}/").status_code, 200)


class StatusTests(TestCase):
    def test_counts_add_up(self) -> None:
        HomeSource.objects.all().delete()
        source = ca_source()
        accepted = make_post(source, 1, text=CA_TEXT)
        apply_review(accepted, dict(CA_ACCEPT))
        ad = make_post(source, 2, text=CA_TEXT + " ad")
        apply_review(ad, {"accept": False, "reject_reason": "ad"})
        make_post(source, 3, text=CA_TEXT + " waiting")
        status = home_status()
        self.assertEqual(status["posts"]["accepted"], 1)
        self.assertEqual(status["posts"]["rejected"], 1)
        self.assertEqual(status["posts"]["waiting"], 1)
        self.assertEqual(status["listings"]["active"], 1)
        self.assertEqual(status["sources"][0]["username"], "RentToronto")
        self.assertEqual(status["storage"]["backend"], "off")
        self.assertFalse(status["storage"]["ready"])


class CourierChannelIsolationTests(TestCase):
    """Housing posts must never reach the courier (چمدون باربری) channel or group."""

    def test_home_code_never_touches_the_courier_channel(self) -> None:
        from pathlib import Path

        import home

        root = Path(home.__file__).parent
        forbidden = ("notifications", "channel_chat", "publish_unpublished", "send_telegram", "mini_app_in_chat", "TELEGRAM_BOT_TOKEN")
        for path in root.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for word in forbidden:
                self.assertNotIn(word, text, f"{path.name} mentions {word}")
            if path.name != "guard.py":
                self.assertNotIn("TELEGRAM_CHANNEL", text, f"{path.name} mentions the courier channel")
                self.assertNotIn("TELEGRAM_GROUP", text, f"{path.name} mentions the courier group")

    def test_the_courier_job_never_reads_or_publishes_housing(self) -> None:
        from pathlib import Path

        import market

        root = Path(market.__file__).parent
        for path in root.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("from home", text, path.name)
            self.assertNotIn("import home", text, path.name)

    def test_housing_sources_are_not_courier_sources(self) -> None:
        from market.ingest import market_ingest_usernames

        courier = {name.lower() for name in market_ingest_usernames()}
        housing = {source.username.lower() for source in HomeSource.objects.all()}
        self.assertEqual(courier & housing, set())


class HomeCronScheduleTests(TestCase):
    """Six daily housing runs, kept away from the hours other jobs open the same Telegram session."""

    def test_six_slots_in_both_vercel_files_and_routes(self) -> None:
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        backend = Path(__file__).resolve().parents[1]
        for vercel_json in (root / "vercel.json", backend / "vercel.json"):
            crons = json.loads(vercel_json.read_text(encoding="utf-8"))["crons"]
            home = {c["path"]: c["schedule"] for c in crons if c["path"].startswith("/api/cron/home")}
            self.assertEqual(len(home), 6, vercel_json)
            busy = {2, 7, 10, 13, 16}
            for schedule in home.values():
                minute, hour = schedule.split()[:2]
                self.assertNotIn(int(hour), busy, schedule)
        for slot in ("home", "home-2", "home-3", "home-4", "home-5", "home-6"):
            response = self.client.get(f"/api/cron/{slot}/")
            self.assertEqual(response.status_code, 403, slot)


class JobLockTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        make_source()

    def test_a_second_run_waits_while_one_is_in_progress(self) -> None:
        from home.job import acquire_lock

        self.assertTrue(acquire_lock())
        with patch("home.job.ingest_sources") as ingest:
            result = run_home_job(step="ingest")
        ingest.assert_not_called()
        self.assertIn("still in progress", result["skipped"])

    def test_the_lock_is_released_after_a_run_even_when_it_fails(self) -> None:
        with patch("home.job.ingest_sources", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                run_home_job(step="ingest")
        self.assertIsNone(HomeSettings.load().job_lock_until)
        with patch("home.job.review_pending", return_value={"reviewed": 0}) as review:
            run_home_job(step="review")
        review.assert_called_once()

    def test_a_stale_lock_expires(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from home.job import acquire_lock

        HomeSettings.objects.update_or_create(pk=1, defaults={"job_lock_until": timezone.now() - timedelta(seconds=1)})
        self.assertTrue(acquire_lock())
