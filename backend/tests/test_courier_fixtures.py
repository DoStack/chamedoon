"""The frontend's mock answers are what the server sends: every fixture fits its serializer, key for key, and real
responses of /api/v1/courier/ have the same keys.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.test import SimpleTestCase, TestCase
from rest_framework import serializers as drf

from courier.models import CourierSettings
from courier_api import serializers as s
from item_requests.seed import seed_catalog
from tests.helpers import bearer_auth, make_user
from users.tokens import issue_access_token

FIXTURES = json.loads((Path(__file__).resolve().parents[2] / "frontend" / "src" / "mocks" / "courier-fixtures.json").read_text(encoding="utf-8"))
BASE = "/api/v1/courier"


def keys_differ(serializer, body, where: str = "") -> list[str]:
    """Every field of the serializer is in the body and no other key is, all the way down."""
    if isinstance(serializer, drf.ListSerializer):
        return [line for index, row in enumerate(body) for line in keys_differ(serializer.child, row, f"{where}[{index}]")]
    if not isinstance(serializer, drf.Serializer) or body is None:
        return []
    fields = serializer.fields
    problems = [f"{where}.{name} is missing" for name in fields if name not in body]
    problems += [f"{where}.{name} is not in the contract" for name in body if name not in fields]
    for name, field in fields.items():
        if name in body:
            problems += keys_differ(field, body[name], f"{where}.{name}")
    return problems


def same_keys(fixture, real, where: str = "") -> list[str]:
    if isinstance(real, dict) and isinstance(fixture, dict):
        problems = [f"{where}.{name} only in the server's answer" for name in real if name not in fixture]
        problems += [f"{where}.{name} only in the fixture" for name in fixture if name not in real]
        return problems + [line for name in real if name in fixture for line in same_keys(fixture[name], real[name], f"{where}.{name}")]
    if isinstance(real, list) and isinstance(fixture, list) and real and fixture:
        return same_keys(fixture[0], real[0], f"{where}[0]")
    return []


class FixturesFitTheSerializers(SimpleTestCase):
    def test_every_fixture_fits_its_serializer_key_for_key(self) -> None:
        for name, fixture in FIXTURES.items():
            with self.subTest(name):
                serializer = getattr(s, fixture["schema"])
                self.assertEqual(keys_differ(serializer(), fixture["body"]), [])
                parsed = serializer(data=fixture["body"])
                self.assertTrue(parsed.is_valid(), parsed.errors)

    def test_a_key_out_of_step_fails(self) -> None:
        self.assertEqual(keys_differ(s.MetaSerializer(), {"api_version": 1, "min_api_version": 1, "courier_v2": True}), [".maintenance is missing"])
        self.assertIn(".extra is not in the contract", keys_differ(s.MetaSerializer(), {**FIXTURES["meta"]["body"], "extra": 1}))


class RealAnswersHaveTheFixturesKeys(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()
        cls.user = make_user(telegram_user_id=8101, first_name="Sara")
        row = CourierSettings.load()
        row.courier_v2 = True
        row.save()

    def get(self, path: str):
        response = self.client.get(f"{BASE}/{path}", **bearer_auth(self.user))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_reads(self) -> None:
        for path, name in (("meta/", "meta"), ("me/", "me"), ("guide/", "guide")):
            with self.subTest(path):
                self.assertEqual(same_keys(FIXTURES[name]["body"], self.get(path)), [])
