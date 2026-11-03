"""Smart posting: a pasted post fills F18, with the manual form as the way out. The model is mocked. Also proves the shared mock fixtures match the server."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from django.utils import timezone

from ai.openrouter import ChatResult
from home.models import HomeListing, HomeParseUse, HomePost, HomeSettings
from tests.contract_shape import SCHEMA, named
from tests.helpers import bearer_auth
from tests.test_home_api import ApiCase

URL = "/api/v1/me/posts/parse/"
TEXT = "آپارتمان یک خوابه مبله نزدیک مترو نورث یورک، ۱۸۵۰ دلار با قبوض، از اول دسامبر."
ACCEPT = {
    "accept": True,
    "kind": "offer",
    "property_type": "apartment",
    "country": "CA",
    "city": "North York",
    "area": "Yonge & Finch",
    "price_amount": 1850,
    "price_period": "month",
    "furnished": "yes",
    "bills_included": "yes",
    "available_from": "2026-12-01",
    "contact_phone": "416-555-0142",
    "contact_telegram": "someone",
    "summary_fa": "آپارتمان یک خوابه مبله",
    "confidence": {"overall": 0.9},
}
FIXTURES = Path(__file__).resolve().parent.parent / "home_api" / "testdata" / "p3p4_fixtures.json"


def model_says(body: dict) -> ChatResult:
    return ChatResult(ok=True, text=json.dumps(body), model="test")


class SmartPostCase(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(posting_api_on=True, smart_posting_on=True, parse_daily_cap=3)
        self.auth = bearer_auth(self.user)
        patcher = patch("home.smart_post.llm_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, answer: object = None, **body):
        request = {"text": TEXT, "kind": "offer", "country": "CA", "city": "Toronto", **body}
        result = answer if isinstance(answer, ChatResult) else model_says(ACCEPT if answer is None else answer)
        with patch("home.smart_post.ask_model", return_value=result) as self.model:
            return self.client.post(URL, request, content_type="application/json", **self.auth)


class ParseTests(SmartPostCase):
    def test_fills_the_form_in_the_contract_shape(self) -> None:
        response = self.send()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(named(body, "PostParse"), [])
        self.assertTrue(body["understood"])
        parsed = body["parsed"]
        self.assertEqual((parsed["price"], parsed["property_type"], parsed["furnished"], parsed["available_from"]), (1850.0, "apartment", "yes", "2026-12-01"))
        self.assertEqual(body["missing"], [])
        self.assertEqual(body["kind_guess"], "offer")
        self.assertEqual(body["parses_left"], 2)

    def test_saves_nothing(self) -> None:
        self.send()
        self.assertFalse(HomeListing.objects.exists())
        self.assertFalse(HomePost.objects.filter(text=TEXT).exists())

    def test_never_holds_a_contact(self) -> None:
        raw = json.dumps(self.send().json()["parsed"])
        self.assertNotIn("416-555", raw)
        self.assertNotIn("someone", raw)

    def test_a_contact_in_the_text_is_flagged(self) -> None:
        self.assertFalse(self.send().json()["contact_in_text"])
        self.assertTrue(self.send(text=TEXT + " تماس 416-555-0142").json()["contact_in_text"])

    def test_what_is_missing_is_named(self) -> None:
        answer = {key: value for key, value in ACCEPT.items() if key not in {"price_amount", "area"}}
        body = self.send(answer).json()
        self.assertEqual(body["missing"], ["price", "area"])
        self.assertNotIn("price", body["parsed"])

    def test_the_chosen_city_fills_in(self) -> None:
        answer = {key: value for key, value in ACCEPT.items() if key != "city"}
        body = self.send(answer).json()
        self.assertEqual(body["parsed"]["city"], "Toronto")
        self.assertIsNone(body["city_guess"])

    def test_text_about_another_city(self) -> None:
        guess = self.send({**ACCEPT, "city": "Calgary"}).json()["city_guess"]
        self.assertEqual((guess["city"], guess["known"]), ("Calgary", False))
        known = self.send().json()["city_guess"]
        self.assertEqual((known["city"], known["known"]), ("Toronto", True))

    def test_text_that_reads_as_wanted(self) -> None:
        body = self.send({**ACCEPT, "kind": "wanted", "price_max": 1600}).json()
        self.assertEqual(body["kind_guess"], "wanted")

    def test_wanted_budget_arrives_as_price(self) -> None:
        answer = {**ACCEPT, "kind": "wanted", "price_max": 1600}
        answer.pop("price_amount")
        self.assertEqual(self.send(answer, kind="wanted").json()["parsed"]["price"], 1600.0)

    def test_not_understood(self) -> None:
        body = self.send({"accept": False, "reject_reason": "noise"}).json()
        self.assertEqual(named(body, "PostParse"), [])
        self.assertEqual((body["understood"], body["parsed"], body["kind_guess"]), (False, {}, None))

    def test_understood_but_no_place_at_all(self) -> None:
        answer = {key: value for key, value in ACCEPT.items() if key not in {"city", "area"}}
        body = self.send(answer, city="").json()
        self.assertFalse(body["understood"])


class ManualFormTests(SmartPostCase):
    def test_the_model_fails(self) -> None:
        self.assert_error(self.send(ChatResult(ok=False, error="timeout")), "parse_unavailable", URL)

    def test_the_model_answers_nonsense(self) -> None:
        self.assert_error(self.send(ChatResult(ok=True, text="no json here")), "parse_unavailable", URL)

    def test_the_call_raises(self) -> None:
        with patch("home.smart_post.ask_model", side_effect=TimeoutError("slow")):
            response = self.client.post(URL, {"text": TEXT, "kind": "offer", "country": "CA"}, content_type="application/json", **self.auth)
        self.assert_error(response, "parse_unavailable", URL)

    def test_no_model_is_configured_and_nothing_counts(self) -> None:
        with patch("home.smart_post.llm_enabled", return_value=False):
            self.assert_error(self.send(), "parse_unavailable", URL)
        self.assertFalse(HomeParseUse.objects.exists())

    def test_daily_cap_per_person(self) -> None:
        for left in (2, 1, 0):
            self.assertEqual(self.send().json()["parses_left"], left)
        self.assert_error(self.send(), "parse_cap", URL)
        self.model.assert_not_called()
        self.assertEqual(HomeParseUse.objects.get(user=self.user, day=timezone.localdate()).count, 3)

    def test_too_short_costs_nothing(self) -> None:
        body = self.assert_error(self.send(text="کوتاه"), "validation_failed", URL)
        self.assertEqual(body["fields"], {"text": ["too_short"]})
        self.assertFalse(HomeParseUse.objects.exists())

    def test_bad_country_and_kind(self) -> None:
        self.assert_error(self.send(country="ZZ"), "validation_failed", URL)
        self.assert_error(self.send(kind="nothing"), "validation_failed", URL)

    def test_a_body_that_cannot_be_read(self) -> None:
        self.assert_error(self.client.post(URL, "{", content_type="application/json", **self.auth), "bad_request", URL)

    def test_closed_unless_both_flags_are_on(self) -> None:
        for values in ({"smart_posting_on": False}, {"smart_posting_on": True, "posting_api_on": False}):
            HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(**values)
            self.assert_error(self.send(), "feature_off", URL)


class FixtureTests(ApiCase):
    """`testdata/p3p4_fixtures.json` is what frontend mocks import. Each entry must fit the schema the server is held to."""

    def test_each_fixture_fits_its_schema(self) -> None:
        fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertTrue(fixtures)
        for name, entry in fixtures.items():
            with self.subTest(name=name):
                self.assertIn(entry["schema"], SCHEMA["components"]["schemas"])
                self.assertEqual(named(entry["body"], entry["schema"]), [])

    def test_every_new_response_has_a_fixture(self) -> None:
        schemas = {entry["schema"] for entry in json.loads(FIXTURES.read_text(encoding="utf-8")).values()}
        self.assertEqual(
            {"Account", "AlertList", "AlertSave", "AlertRow", "AlertListings", "NotificationList", "NotificationRead", "MineList", "MineRow", "MineForm", "PostResult", "PostParse", "PhotoUploadTicket", "UploadedPhoto"} - schemas,
            set(),
        )

    def test_fixtures_hold_no_real_contact_or_token(self) -> None:
        text = FIXTURES.read_text(encoding="utf-8")
        for word in ("contact_telegram", "contact_phone", "Bearer", "X-Amz-Credential"):
            self.assertNotIn(word, text)
