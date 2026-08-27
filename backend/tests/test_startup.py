from __future__ import annotations

import os
from unittest.mock import patch

from django.contrib.auth.models import User as StaffUser
from django.test import TestCase

from config.startup import create_superuser_from_env, ensure_superuser
from config.settings import _first_env


class PrefixedDatabaseEnvTests(TestCase):
    def test_first_env_reads_koolbar_prefix(self) -> None:
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
