from __future__ import annotations

import os
from unittest.mock import patch

from django.contrib.auth.models import User as StaffUser
from django.test import TestCase

from config.startup import create_superuser_from_env, ensure_superuser
from config.settings import DB_CA_FILE, _database_config, _database_url, _first_env


class PooledDatabaseUrlTests(TestCase):
    URL = "postgres://app:secret@chamedoon-db:6432/chamedoon_prod?hostaddr=192.0.2.1&sslmode=verify-full"

    def test_pooled_url_wins_over_the_neon_integration_names(self) -> None:
        env = {
            "CHAMEDOON_DB_URL": self.URL,
            "POSTGRES_URL_NON_POOLING": "postgres://neon-direct/db",
            "DATABASE_URL_UNPOOLED": "postgres://neon-direct/db",
            "DATABASE_URL": "postgres://neon-pooled/db",
        }
        with patch.dict(os.environ, env, clear=False):
            url, source = _database_url()
        self.assertEqual(url, self.URL)
        self.assertEqual(source, "url")

    def test_neon_names_keep_their_order_when_pooled_url_is_unset(self) -> None:
        env = {"POSTGRES_URL_NON_POOLING": "postgres://neon-direct/db", "DATABASE_URL": "postgres://neon-pooled/db"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("CHAMEDOON_DB_URL", None)
            url, _source = _database_url()
        self.assertTrue(url.startswith("postgres://neon-direct/db"))

    def test_pooled_config_disables_server_side_cursors_and_verifies_the_server(self) -> None:
        config = _database_config(self.URL, pooled=True)
        self.assertIs(config["DISABLE_SERVER_SIDE_CURSORS"], True)
        self.assertEqual(config["HOST"], "chamedoon-db")
        self.assertEqual(config["PORT"], 6432)
        self.assertEqual(config["OPTIONS"]["sslmode"], "verify-full")
        self.assertEqual(config["OPTIONS"]["hostaddr"], "192.0.2.1")
        self.assertEqual(config["OPTIONS"]["sslrootcert"], str(DB_CA_FILE))
        self.assertTrue(DB_CA_FILE.is_file())

    def test_plain_config_is_unchanged(self) -> None:
        config = _database_config("postgres://u:p@neon-host/db?sslmode=require", pooled=False)
        self.assertFalse(config["DISABLE_SERVER_SIDE_CURSORS"])
        self.assertNotIn("sslrootcert", config["OPTIONS"])


class PrefixedDatabaseEnvTests(TestCase):
    def test_first_env_reads_chamedoon_prefix(self) -> None:
        with patch.dict(os.environ, {"chamedoon_POSTGRES_URL": "postgres://chamedoon-prefixed"}, clear=False):
            os.environ.pop("POSTGRES_URL", None)
            self.assertEqual(_first_env("POSTGRES_URL"), "postgres://chamedoon-prefixed")

    def test_first_env_still_reads_the_old_store_prefix(self) -> None:
        with patch.dict(
            os.environ,
            {"koolbar_POSTGRES_URL_NON_POOLING": "postgres://prefixed-non-pooling"},
            clear=False,
        ):
            os.environ.pop("POSTGRES_URL_NON_POOLING", None)
            self.assertEqual(
                _first_env("POSTGRES_URL_NON_POOLING"),
                "postgres://prefixed-non-pooling",
            )


class SuperuserFromEnvTests(TestCase):
    def test_create_superuser_from_env(self) -> None:
        env = {
            "DJANGO_SUPERUSER_USERNAME": "ops",
            "DJANGO_SUPERUSER_PASSWORD": "OpsLivePass-94821",
            "DJANGO_SUPERUSER_EMAIL": "ops@example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            create_superuser_from_env()

        user = StaffUser.objects.get(username="ops")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("OpsLivePass-94821"))
        self.assertEqual(user.email, "ops@example.com")

        with patch.dict(
            os.environ,
            {**env, "DJANGO_SUPERUSER_PASSWORD": "OpsLivePass-changed"},
            clear=False,
        ):
            create_superuser_from_env()

        user.refresh_from_db()
        self.assertTrue(user.check_password("OpsLivePass-changed"))
        self.assertEqual(StaffUser.objects.filter(username="ops").count(), 1)

    def test_ensure_superuser_skips_when_not_on_vercel(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DJANGO_SUPERUSER_USERNAME": "skipme",
                "DJANGO_SUPERUSER_PASSWORD": "SkipPass-12345",
                "VERCEL": "",
            },
            clear=False,
        ):
            os.environ.pop("VERCEL", None)
            os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)
            ensure_superuser()

        self.assertFalse(StaffUser.objects.filter(username="skipme").exists())
