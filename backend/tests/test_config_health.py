"""Missing configuration must be loud: cron 503 without CRON_SECRET, named `skipped` reasons,
the admin System health page, the dashboard banner and /api/health. Names only, never values."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User as StaffUser
from django.test import TestCase, override_settings
from django.utils import timezone

from api import config_check
from api.config_check import MISSING, OFF, OK, config_report, freshness_report, health_summary, missing_names
from home.job import run_home_job
from home.models import HomeChannel, HomeListing, HomePost, HomePublication, HomeSettings, HomeSource
from outreach.models import OutreachSettings
from tests.test_home import make_post, make_source

SENTINEL = "SENTINEL-do-not-leak-7f3a9c"
CRON = "cron-health-secret"

# Every setting the report reads, blank: a CI runner and a laptop with a .env behave the same.
BLANK = {
    "CRON_SECRET": "",
    "BOT_SERVICE_SECRET": "",
    "MARKET_INGEST_ENABLED": True,
    "MARKET_CHANNEL_USERNAMES": "koolbar_international",
    "MARKET_SOURCE_INVITES": "",
    "MARKET_TG_API_ID": 0,
    "MARKET_TG_API_HASH": "",
    "MARKET_TG_SESSION": "",
    "OUTREACH_TG_API_ID": 0,
    "OUTREACH_TG_API_HASH": "",
    "OUTREACH_TG_SESSION": "",
    "OUTREACH_SENDING_ENABLED": True,
    "OPENROUTER_API_KEY": "",
    "OPENAI_API_KEY": "",
    "HOME_ENABLED": True,
    "HOME_BOT_TOKEN": "",
    "HOME_WEBHOOK_SECRET": "",
    "HOME_MEDIA_BACKEND": "",
    "HOME_MEDIA_BUCKET": "",
    "HOME_MEDIA_PUBLIC_BASE": "",
    "HOME_MEDIA_LOCAL_DIR": "",
    "R2_ACCOUNT_ID": "",
    "R2_ACCESS_KEY_ID": "",
    "R2_SECRET_ACCESS_KEY": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_WEBHOOK_SECRET": "",
    "TELEGRAM_CHANNEL_ENABLED": False,
}

# Everything set to the sentinel (numbers where the code wants a number), CRON_SECRET included.
FULL = {
    **BLANK,
    "CRON_SECRET": CRON,
    "MARKET_TG_API_ID": 12345,
    "MARKET_TG_API_HASH": SENTINEL,
    "MARKET_TG_SESSION": SENTINEL,
    "OUTREACH_TG_API_ID": 12345,
    "OUTREACH_TG_API_HASH": SENTINEL,
    "OUTREACH_TG_SESSION": SENTINEL,
    "OPENROUTER_API_KEY": SENTINEL,
    "HOME_BOT_TOKEN": SENTINEL,
    "HOME_WEBHOOK_SECRET": SENTINEL,
    "HOME_MEDIA_BACKEND": "r2",
    "HOME_MEDIA_BUCKET": SENTINEL,
    "HOME_MEDIA_PUBLIC_BASE": "https://media.example",
    "R2_ACCOUNT_ID": SENTINEL,
    "R2_ACCESS_KEY_ID": SENTINEL,
    "R2_SECRET_ACCESS_KEY": SENTINEL,
    "TELEGRAM_BOT_TOKEN": SENTINEL,
    "TELEGRAM_WEBHOOK_SECRET": SENTINEL,
}


def feature(key: str):
    return next(item for item in config_report() if item.key == key)


def bearer() -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {CRON}"}


@override_settings(**BLANK)
class MissingNamesTests(TestCase):
    def test_cron_auth(self) -> None:
        self.assertEqual((feature("cron_auth").state, feature("cron_auth").missing), (MISSING, ["CRON_SECRET"]))
        with self.settings(CRON_SECRET=CRON):
            self.assertEqual(feature("cron_auth").state, OK)

    def test_market_crawl_names_each_missing_session_setting(self) -> None:
        crawl = feature("market_crawl")
        self.assertEqual(crawl.state, MISSING)
        self.assertEqual(crawl.missing, ["MARKET_TG_API_ID", "MARKET_TG_API_HASH", "MARKET_TG_SESSION"])
        self.assertIn("missing env: MARKET_TG_API_ID", crawl.message)
        with self.settings(MARKET_TG_API_ID=1, MARKET_TG_API_HASH="h"):
            self.assertEqual(feature("market_crawl").missing, ["MARKET_TG_SESSION"])
        with self.settings(MARKET_TG_API_ID=1, MARKET_TG_API_HASH="h", MARKET_TG_SESSION="s"):
            self.assertEqual(feature("market_crawl").state, OK)
        with self.settings(MARKET_INGEST_ENABLED=False):
            self.assertEqual(feature("market_crawl").state, OFF)

    def test_outreach_session_also_serves_the_reader(self) -> None:
        with self.settings(OUTREACH_TG_API_ID=1, OUTREACH_TG_API_HASH="h", OUTREACH_TG_SESSION="s"):
            self.assertEqual(feature("market_crawl").state, OK)
            self.assertEqual(feature("housing_ingest").state, OK)

    def test_housing_ingest(self) -> None:
        self.assertEqual(feature("housing_ingest").missing, list(config_check.TELEGRAM_READER_NAMES))
        HomeSettings.objects.update_or_create(pk=1, defaults={"ingest_enabled": False})
        self.assertEqual(feature("housing_ingest").state, OFF)

    def test_ai_review(self) -> None:
        self.assertEqual(feature("ai_review").missing, ["OPENROUTER_API_KEY"])
        with self.settings(OPENAI_API_KEY="k"):
            self.assertEqual(feature("ai_review").state, OK)
        HomeSettings.objects.update_or_create(pk=1, defaults={"review_enabled": False})
        with self.settings(MARKET_INGEST_ENABLED=False):
            self.assertEqual(feature("ai_review").state, OFF)

    def test_housing_publish_reads_the_admin_row(self) -> None:
        self.assertEqual(feature("housing_publish").state, OFF)
        HomeSettings.objects.update_or_create(pk=1, defaults={"publish_enabled": True})
        self.assertEqual(feature("housing_publish").missing, ["HOME_BOT_TOKEN"])
        HomeSettings.objects.filter(pk=1).update(bot_token="from-admin-row")
        HomeChannel.objects.update(enabled=False)
        publish = feature("housing_publish")
        self.assertEqual((publish.state, publish.missing), (MISSING, []))
        self.assertIn("no housing channel", publish.note)
        HomeChannel.objects.update_or_create(country="DE", city="", defaults={"username": "de_homes", "enabled": True})
        self.assertEqual(feature("housing_publish").state, OK)

    def test_photo_storage(self) -> None:
        self.assertEqual(feature("photo_storage").state, OFF)
        with self.settings(HOME_MEDIA_BACKEND="r2", R2_ACCOUNT_ID="a"):
            self.assertEqual(
                feature("photo_storage").missing,
                ["R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "HOME_MEDIA_BUCKET", "HOME_MEDIA_PUBLIC_BASE"],
            )
            HomeSettings.objects.update_or_create(
                pk=1, defaults={"r2_access_key_id": "k", "r2_secret_access_key": "s", "r2_bucket": "b", "r2_public_base": "https://m"}
            )
            self.assertEqual(feature("photo_storage").state, OK)
        HomeSettings.objects.filter(pk=1).update(media_backend="")
        with self.settings(HOME_MEDIA_BACKEND="local"):
            self.assertEqual(feature("photo_storage").missing, ["HOME_MEDIA_LOCAL_DIR"])
        with self.settings(HOME_MEDIA_BACKEND="s3"):
            self.assertEqual(feature("photo_storage").missing, ["HOME_MEDIA_BACKEND"])
        HomeSettings.objects.filter(pk=1).update(media_enabled=False)
        self.assertEqual(feature("photo_storage").state, OFF)

    def test_courier_bot(self) -> None:
        self.assertEqual(feature("courier_bot").missing, ["TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET"])
        with self.settings(TELEGRAM_BOT_TOKEN="t", TELEGRAM_WEBHOOK_SECRET="w", TELEGRAM_CHANNEL_ENABLED=True):
            self.assertEqual((feature("courier_bot").state, feature("courier_bot").note), (OK, ""))

    def test_outreach_sender(self) -> None:
        self.assertEqual(feature("outreach_sender").missing, list(config_check.OUTREACH_SENDER_NAMES))
        OutreachSettings.load()
        OutreachSettings.objects.filter(pk=1).update(sending_enabled=False)
        self.assertEqual(feature("outreach_sender").state, OFF)

    def test_alerts_webhook(self) -> None:
        self.assertEqual(feature("alerts_webhook").state, OFF)
        HomeSettings.objects.update_or_create(pk=1, defaults={"alerts_enabled": True})
        self.assertEqual(feature("alerts_webhook").missing, ["HOME_BOT_TOKEN", "HOME_WEBHOOK_SECRET"])
        HomeSettings.objects.filter(pk=1).update(bot_token="t", webhook_secret="w")
        self.assertEqual(feature("alerts_webhook").state, OK)

    def test_missing_names_are_unique_and_only_from_missing_features(self) -> None:
        names = missing_names()
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("CRON_SECRET", names)
        self.assertIn("OPENROUTER_API_KEY", names)
        self.assertNotIn("R2_ACCOUNT_ID", names)
        self.assertEqual(config_check.Feature("x", "X", OK).message, "")


@override_settings(**FULL)
class FreshnessTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
    def test_everything_set_and_recent_is_healthy(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"publish_enabled": True, "alerts_enabled": True})
        HomeChannel.objects.update_or_create(country="DE", city="", defaults={"username": "de_homes", "enabled": True})
        now = timezone.now()
        source = make_source(last_run_at=now)
        make_post(source, 1, reviewed_at=now)
        from outreach.models import OutreachMessage

        with patch.object(OutreachMessage.objects, "aggregate", return_value={"at": now}), patch(
            "market.models.MarketIngestState.objects.filter"
        ) as states, patch("home.models.HomeListing.objects.aggregate", return_value={"at": now}):
            states.return_value.aggregate.return_value = {"at": now}
            summary = health_summary()
        self.assertEqual([f.key for f in summary["missing"]], [])
        self.assertEqual([row.key for row in summary["stale"]], [])
        self.assertFalse(summary["problem"])

    def test_old_and_never_are_stale_and_off_features_are_not(self) -> None:
        source = make_source(last_run_at=timezone.now() - timedelta(hours=8))
        HomeSource.objects.filter(pk=source.pk).update(last_run_at=timezone.now() - timedelta(hours=8))
        rows = {row.key: row for row in freshness_report()}
        self.assertTrue(rows["housing_ingest"].stale)
        self.assertEqual(rows["housing_ingest"].max_age_label, "7 h")
        self.assertTrue(rows["outreach_send"].stale)
        self.assertFalse(rows["housing_publish"].stale)  # publishing is off by default
        self.assertFalse(rows["courier_channel"].active)  # channel posting is off

    def test_a_source_with_an_error_does_not_count_as_a_clean_run(self) -> None:
        make_source(last_run_at=timezone.now(), last_error="boom")
        self.assertIsNone({row.key: row for row in freshness_report()}["housing_ingest"].at)

    def test_publish_time_is_the_newest_of_listing_and_city_post(self) -> None:
        older, newer = timezone.now() - timedelta(hours=3), timezone.now() - timedelta(hours=1)
        with patch.object(HomeListing.objects, "aggregate", return_value={"at": older}), patch.object(
            HomePublication.objects, "aggregate", return_value={"at": newer}
        ):
            row = {row.key: row for row in freshness_report()}["housing_publish"]
        self.assertEqual(row.at, newer)


@override_settings(**BLANK)
class CronWithoutSecretTests(TestCase):
    PATHS = (
        "/api/cron/home/",
        "/api/cron/market-extract/",
        "/api/cron/market-publish/",
        "/api/cron/outreach-send/",
        "/api/cron/expire-requests/",
    )

    def test_every_cron_answers_503_and_names_the_setting(self) -> None:
        for path in self.PATHS:
            with self.assertLogs("chamedoon.config", "ERROR") as logs:
                response = self.client.get(path, **bearer())
            self.assertEqual(response.status_code, 503, path)
            self.assertEqual(response.json(), {"ok": False, "error": "missing env: CRON_SECRET"})
            self.assertIn("missing env: CRON_SECRET", logs.output[0])

    def test_the_job_never_runs(self) -> None:
        with patch("api.cron_views.run_home_job") as job, self.assertLogs("chamedoon.config", "ERROR"):
            self.client.post("/api/cron/home/", **bearer())
        job.assert_not_called()

    def test_with_the_secret_auth_still_applies(self) -> None:
        with self.settings(CRON_SECRET=CRON):
            self.assertEqual(self.client.get("/api/cron/expire-requests/").status_code, 403)
            self.assertEqual(self.client.get("/api/cron/expire-requests/", **bearer()).status_code, 200)


@override_settings(**{**BLANK, "CRON_SECRET": CRON})
class SkippedNamesTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
    def test_housing_ingest_names_the_session_settings(self) -> None:
        make_source()
        with self.assertLogs("chamedoon.config", "ERROR") as logs:
            body = self.client.get("/api/cron/home/?step=ingest", **bearer()).json()
        self.assertEqual(body["ingest"]["skipped"], "missing env: MARKET_TG_API_ID, MARKET_TG_API_HASH, MARKET_TG_SESSION")
        self.assertIn("housing ingest skipped", logs.output[0])

    def test_review_without_a_model_names_the_key(self) -> None:
        make_post(make_source(), 1)
        with self.assertLogs("chamedoon.config", "ERROR"):
            review = run_home_job(step="review")["review"]
        self.assertEqual(review["skipped"], "missing env: OPENROUTER_API_KEY")
        self.assertEqual(review["errors"], 1)
        self.assertEqual(HomePost.objects.get().status, "new")

    def test_publish_and_alerts_name_the_bot_token(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"publish_enabled": True, "alerts_enabled": True})
        with self.assertLogs("home.publish", "ERROR"):
            publish = run_home_job(step="publish")["publish"]
        with self.assertLogs("home.alerts", "ERROR"):
            alerts = run_home_job(step="alerts")["alerts"]
        self.assertIn("missing env: HOME_BOT_TOKEN", publish["skipped"])
        self.assertIn("missing env: HOME_BOT_TOKEN", alerts["skipped"])

    def test_photos_name_the_r2_settings(self) -> None:
        with self.settings(HOME_MEDIA_BACKEND="r2"), self.assertLogs("chamedoon.config", "ERROR"):
            media = run_home_job(step="media")["media"]
        self.assertIn("not configured", media["skipped"])
        self.assertIn("missing env: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID", media["skipped"])

    def test_outreach_send_names_the_session_settings(self) -> None:
        with self.settings(OUTREACH_TG_API_ID=7), self.assertLogs("chamedoon.config", "ERROR"):
            response = self.client.get("/api/cron/outreach-send/", **bearer())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["skipped"], "missing env: OUTREACH_TG_API_HASH, OUTREACH_TG_SESSION")

    def test_market_extract_says_the_reader_is_missing(self) -> None:
        run = {"ok": True, "days": 1}
        with patch("api.cron_views.extract_channels_only", return_value=run), self.assertLogs("chamedoon.config", "ERROR"):
            body = self.client.get("/api/cron/market-extract/", **bearer()).json()
        self.assertEqual(body["degraded"], "missing env: MARKET_TG_API_ID, MARKET_TG_API_HASH, MARKET_TG_SESSION")
        with patch("api.cron_views.ingest_all_market_channels", return_value={"ok": True}), self.assertLogs(
            "chamedoon.config", "ERROR"
        ):
            body = self.client.get("/api/cron/market-channel/", **bearer()).json()
        self.assertIn("MARKET_TG_SESSION", body["degraded"])

    def test_market_migrate_alias_still_runs_extract(self) -> None:
        with self.settings(MARKET_TG_API_ID=1, MARKET_TG_API_HASH="h", MARKET_TG_SESSION="s"), patch(
            "api.cron_views.extract_channels_only", return_value={"ok": True}
        ) as extract:
            response = self.client.get("/api/cron/market-migrate/", **bearer())
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("degraded", response.json())
        extract.assert_called_once()


@override_settings(**BLANK)
class HealthPageTests(TestCase):
    def setUp(self) -> None:
        self.staff = StaffUser.objects.create_user("ops", password="pw", is_staff=True, is_superuser=True)

    def test_staff_sees_features_and_missing_names(self) -> None:
        self.client.force_login(self.staff)
        page = self.client.get("/admin/ops/health/")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("System health", html)
        self.assertIn("CRON_SECRET", html)
        self.assertIn("MARKET_TG_SESSION", html)
        self.assertIn('data-freshness="housing_ingest"', html)
        self.assertIn("hs-missing", html)

    def test_dashboard_shows_the_red_banner(self) -> None:
        self.client.force_login(self.staff)
        html = self.client.get("/admin/").content.decode()
        self.assertIn('id="health-banner"', html)
        self.assertIn("Cron auth: missing env: CRON_SECRET", html)
        self.assertIn("/admin/ops/health/", html)

    def test_no_banner_when_healthy(self) -> None:
        from config import dashboard

        with patch("api.config_check.health_summary", return_value={"problem": False}):
            self.assertIsNone(dashboard.health_banner())
        with patch("api.config_check.health_summary", side_effect=RuntimeError("db")):
            self.assertIsNone(dashboard.health_banner())

    def test_others_are_sent_to_the_login(self) -> None:
        self.assertEqual(self.client.get("/admin/ops/health/").status_code, 302)
        plain = StaffUser.objects.create_user("plain", password="pw", is_staff=False)
        self.client.force_login(plain)
        response = self.client.get("/admin/ops/health/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])


@override_settings(**BLANK)
class HealthEndpointConfigTests(TestCase):
    def test_health_lists_missing_names_and_keeps_its_fields(self) -> None:
        payload = self.client.get("/api/health/").json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["database"], "ok")
        self.assertIn("CRON_SECRET", payload["config"]["missing"])
        self.assertIn("OPENROUTER_API_KEY", payload["config"]["missing"])

    def test_health_still_answers_when_the_check_fails(self) -> None:
        with patch("api.config_check.missing_names", side_effect=RuntimeError("db")):
            payload = self.client.get("/api/health/").json()
        self.assertEqual(payload["config"], {"missing": [], "error": "config check unavailable"})


@override_settings(**FULL)
@patch("notifications.channel.refresh_recent_channel_buttons", return_value=0)
class NoValuesLeakTests(TestCase):
    """Fill every secret with a sentinel, leave one setting missing, and read every surface."""

    def test_sentinel_never_appears(self, _refresh) -> None:
        HomeSource.objects.all().delete()
        HomeSettings.objects.update_or_create(
            pk=1, defaults={"publish_enabled": True, "alerts_enabled": True, "bot_token": SENTINEL, "webhook_secret": SENTINEL}
        )
        staff = StaffUser.objects.create_user("ops", password="pw", is_staff=True, is_superuser=True)
        self.client.force_login(staff)
        bodies = []
        with self.settings(OPENROUTER_API_KEY=""):
            bodies.append(self.client.get("/api/health/").content.decode())
            bodies.append(self.client.get("/admin/ops/health/").content.decode())
            bodies.append(self.client.get("/admin/").content.decode())
            with self.assertLogs("chamedoon.config", "ERROR") as logs:
                make_post(make_source(), 1)
                with patch("home.job.ingest_sources", return_value={"ok": True}):
                    bodies.append(self.client.get("/api/cron/home/?step=review", **bearer()).content.decode())
            bodies.extend(logs.output)
        with self.settings(CRON_SECRET=""), self.assertLogs("chamedoon.config", "ERROR") as logs:
            bodies.append(self.client.get("/api/cron/home/", HTTP_AUTHORIZATION=f"Bearer {SENTINEL}").content.decode())
        bodies.extend(logs.output)
        bodies.append(repr([f.as_dict() for f in config_report()]))
        joined = "\n".join(bodies)
        self.assertIn("OPENROUTER_API_KEY", joined)
        self.assertNotIn(SENTINEL, joined)
        self.assertNotIn(SENTINEL[:8], joined)
