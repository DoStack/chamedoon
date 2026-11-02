from __future__ import annotations

import os
import runpy
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase

from config.startup import run_startup, run_startup_migrations

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "config" / "settings.py"
INSECURE_KEY = "insecure-dev-key-do-not-use-in-production"
ENV_NAMES = ("VERCEL", "VERCEL_ENV", "VERCEL_URL", "DEBUG", "SECRET_KEY", "DJANGO_SECRET_KEY")


def load_settings(**env: str) -> dict:
    """Run settings.py with only these Vercel and key variables set, leaving the live settings alone."""
    clean = {name: value for name, value in os.environ.items() if name not in ENV_NAMES}
    with patch.dict(os.environ, {**clean, **env}, clear=True), patch("dotenv.load_dotenv"):
        return runpy.run_path(str(SETTINGS_FILE))


class DebugDefaultTests(SimpleTestCase):
    def test_debug_stays_on_by_default_off_vercel(self) -> None:
        self.assertTrue(load_settings()["DEBUG"])

    def test_debug_defaults_off_on_every_vercel_environment(self) -> None:
        for vercel_env in ("preview", "production", "development"):
            with self.subTest(vercel_env=vercel_env):
                self.assertFalse(load_settings(VERCEL="1", VERCEL_ENV=vercel_env)["DEBUG"])

    def test_explicit_debug_still_wins_on_vercel(self) -> None:
        self.assertTrue(load_settings(VERCEL="1", VERCEL_ENV="preview", DEBUG="1")["DEBUG"])


class SecretKeyTests(SimpleTestCase):
    def test_preview_without_a_key_gets_a_random_one(self) -> None:
        first = load_settings(VERCEL="1", VERCEL_ENV="preview")["SECRET_KEY"]
        second = load_settings(VERCEL="1", VERCEL_ENV="preview")["SECRET_KEY"]
        self.assertNotEqual(first, INSECURE_KEY)
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 50)

    def test_preview_uses_a_configured_key(self) -> None:
        settings = load_settings(VERCEL="1", VERCEL_ENV="preview", DJANGO_SECRET_KEY="configured")
        self.assertEqual(settings["SECRET_KEY"], "configured")

    def test_production_and_local_keep_their_key_order(self) -> None:
        settings = load_settings(VERCEL="1", VERCEL_ENV="production", SECRET_KEY="prod", DJANGO_SECRET_KEY="other")
        self.assertEqual(settings["SECRET_KEY"], "prod")
        self.assertEqual(load_settings()["SECRET_KEY"], INSECURE_KEY)


class StartupMigrationTests(TestCase):
    def run_startup(self, **env: str) -> bool:
        with patch.dict(os.environ, env), patch("django.core.management.call_command") as migrate, patch(
            "django.db.migrations.executor.MigrationExecutor"
        ) as executor:
            executor.return_value.migration_plan.return_value = ["pending"]
            run_startup_migrations()
        return migrate.called

    def test_preview_never_migrates_even_when_the_variable_asks(self) -> None:
        self.assertFalse(self.run_startup(VERCEL="1", VERCEL_ENV="preview", AUTO_MIGRATE_ON_STARTUP="1"))

    def test_production_still_migrates_by_default(self) -> None:
        with patch.dict(os.environ):
            os.environ.pop("AUTO_MIGRATE_ON_STARTUP", None)
            self.assertTrue(self.run_startup(VERCEL="1", VERCEL_ENV="production"))

    def test_production_still_obeys_the_variable(self) -> None:
        self.assertFalse(self.run_startup(VERCEL="1", VERCEL_ENV="production", AUTO_MIGRATE_ON_STARTUP="0"))


class StartupFailureTests(TestCase):
    def pending(self):
        return patch("django.db.migrations.executor.MigrationExecutor", **{"return_value.migration_plan.return_value": ["pending"]})

    @skipUnless(connection.vendor == "postgresql", "lock_timeout is a Postgres setting")
    def test_migrate_runs_under_a_short_lock_timeout_and_resets_it(self) -> None:
        seen = []

        def migrate(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SHOW lock_timeout")
                seen.append(cursor.fetchone()[0])

        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            before = cursor.fetchone()[0]
        with patch.dict(os.environ, {"VERCEL": "1", "VERCEL_ENV": "production", "AUTO_MIGRATE_ON_STARTUP": "1"}), self.pending(), patch(
            "django.core.management.call_command", side_effect=migrate
        ):
            run_startup_migrations()
        self.assertEqual(seen, ["5s"])
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            self.assertEqual(cursor.fetchone()[0], before)

    def test_a_failed_migrate_is_logged_and_the_app_still_starts_and_retries(self) -> None:
        env = {"VERCEL": "1", "VERCEL_ENV": "production", "AUTO_MIGRATE_ON_STARTUP": "1"}
        with patch.dict(os.environ, env), self.pending(), patch("config.startup.ensure_superuser") as superuser:
            with patch("django.core.management.call_command", side_effect=RuntimeError("lock timeout")) as first:
                with self.assertLogs("config.startup", level="ERROR") as logs:
                    run_startup()
            self.assertIn("startup_migrate_failed", logs.output[0])
            first.assert_called_once()
            superuser.assert_called_once()
            with patch("django.core.management.call_command") as second:
                run_startup()
            second.assert_called_once()


class FrameHeaderTests(TestCase):
    def test_mini_app_pages_can_be_framed_by_telegram(self) -> None:
        response = self.client.get("/app/login/")
        self.assertNotIn("X-Frame-Options", response)
        self.assertIn("https://web.telegram.org", response["Content-Security-Policy"])

    def test_other_pages_still_refuse_framing(self) -> None:
        response = self.client.get("/admin/login/")
        self.assertEqual(response["X-Frame-Options"], "DENY")
