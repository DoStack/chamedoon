"""`testdata/p4_fixtures.json` is what the frontend mocks answer with (frontend/src/mocks/posting.ts). Each entry fits the schema the client is typed on, and has the fields the running views send."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from home.models import HomeSettings
from tests.contract_shape import named
from tests.helpers import bearer_auth, make_user
from tests.test_home_p4 import LISTINGS, PHOTOS, TEXT, P4Case
from tests.test_home_smart_post import ACCEPT, model_says

FIXTURES = json.loads((Path(__file__).resolve().parent.parent / "home_api" / "testdata" / "p4_fixtures.json").read_text(encoding="utf-8"))


def body(name: str) -> dict:
    return FIXTURES[name]["body"]


class P4FixtureTests(P4Case):
    def assert_same_fields(self, sent: dict, fixture: dict, where: str) -> None:
        self.assertEqual(set(sent), set(fixture), where)

    def test_each_fixture_fits_its_schema(self) -> None:
        self.assertTrue(FIXTURES)
        for name, entry in FIXTURES.items():
            if entry["schema"] is None:
                continue
            with self.subTest(name=name):
                self.assertEqual(named(entry["body"], entry["schema"]), [])

    def test_every_p4_response_has_a_fixture(self) -> None:
        schemas = {entry["schema"] for entry in FIXTURES.values()}
        self.assertLessEqual({"Meta", "PostParse", "PhotoUploadTicket", "UploadedPhoto", "PostResult", "MineRow", "MineList", "MineForm", "CityAreas", "PostCreateRequestRequest"}, schemas)

    def test_meta_has_the_fields_the_server_sends(self) -> None:
        sent = self.client.get("/api/v1/public/meta/").json()
        for name in ("meta_on", "meta_manual", "meta_off"):
            self.assert_same_fields(sent, body(name), name)
            self.assert_same_fields(sent["features"], body(name)["features"], name)

    def test_requests_are_what_the_server_takes(self) -> None:
        for name in ("request_offer", "request_wanted"):
            with self.subTest(name=name):
                response = self.send(body(name))
                self.assertEqual(response.status_code, 201, response.content)
                self.assert_same_fields(response.json(), body("post_live"), name)

    def test_rows_and_form_have_the_fields_the_server_sends(self) -> None:
        listing = self.posted()
        listed = self.client.get(LISTINGS, **self.auth).json()
        self.assert_same_fields(listed, body("mine_list"), "list")
        row = listed["results"][0]
        for name in ("mine_live", "mine_review", "mine_rejected_price", "mine_rejected_other", "mine_closed", "mine_expired", "mine_check", "mine_wanted", "mine_wanted_check"):
            with self.subTest(name=name):
                self.assert_same_fields(row, body(name), name)
                self.assert_same_fields(row["card"], body(name)["card"], name)
        form = self.client.get(f"{LISTINGS}{listing.pk}/form/", **self.auth).json()
        self.assert_same_fields(form, body("form_offer"), "form_offer")
        self.assert_same_fields(form, body("form_wanted"), "form_wanted")

    def test_photo_steps_have_the_fields_the_server_sends(self) -> None:
        ticket = self.client.post(PHOTOS, {"content_type": "image/jpeg", "size": 1000}, content_type="application/json", **self.auth).json()
        self.assert_same_fields(ticket, body("photo_ticket"), "ticket")
        confirmed = self.confirmed()
        sent = self.client.post(f"{PHOTOS}{confirmed}/", content_type="application/json", **self.auth).json()
        self.assert_same_fields(sent, body("uploaded_photo"), "uploaded")

    def test_parse_has_the_fields_the_server_sends(self) -> None:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(smart_posting_on=True)
        with patch("home.smart_post.llm_enabled", return_value=True), patch("home.smart_post.ask_model", return_value=model_says(ACCEPT)):
            sent = self.client.post("/api/v1/me/posts/parse/", {"text": TEXT, "kind": "offer", "country": "CA", "city": "Toronto"}, content_type="application/json", **self.auth).json()
        for name in ("parse_full", "parse_incomplete", "parse_nothing", "parse_wanted", "parse_other_city", "parse_sublet", "parse_german", "parse_contact"):
            with self.subTest(name=name):
                self.assert_same_fields(sent, body(name), name)

    def test_errors_have_the_fields_the_server_sends(self) -> None:
        self.posted(description=TEXT)
        own = self.send(self.form(description=TEXT))
        self.assertEqual(own.status_code, 409, own.content)
        self.assert_same_fields(own.json(), body("error_duplicate_self"), "own")
        self.assertEqual(own.json()["duplicate_owner"], "self")
        other = make_user(telegram_user_id=770099, telegram_username="someone_else")
        seen = self.client.post(LISTINGS, self.form(description=TEXT), content_type="application/json", HTTP_IDEMPOTENCY_KEY="other-1", **bearer_auth(other))
        self.assertEqual(seen.status_code, 409, seen.content)
        self.assert_same_fields(seen.json(), body("error_duplicate_other"), "other")
        self.assertEqual(seen.json()["duplicate_owner"], "other")
        bad = self.send(self.form(description=TEXT + " 0416 555 0100 "))
        self.assert_same_fields(bad.json(), body("error_validation"), "validation")
        self.assertEqual(bad.json()["fields"], {"description": ["contact_in_text"]})

    def test_fixtures_hold_no_real_contact_or_token(self) -> None:
        text = json.dumps(FIXTURES, ensure_ascii=False)
        for word in ("contact_telegram", "contact_phone", "Bearer", "X-Amz-Credential"):
            self.assertNotIn(word, text)
