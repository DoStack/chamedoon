"""`testdata/p3_fixtures.json` is what the frontend mocks answer with (frontend/src/mocks). Each entry fits the schema the client is typed on, and has the fields the running views send."""

from __future__ import annotations

import json
from pathlib import Path

from home.models import HomeNotification, NotificationKind
from tests.contract_shape import SCHEMA, named
from tests.test_home_p3 import P3Case

FIXTURES = json.loads((Path(__file__).resolve().parent.parent / "home_api" / "testdata" / "p3_fixtures.json").read_text(encoding="utf-8"))


def body(name: str) -> dict:
    return FIXTURES[name]["body"]


class P3FixtureTests(P3Case):
    def assert_same_fields(self, sent: dict, fixture: dict, where: str) -> None:
        self.assertEqual(set(sent), set(fixture), where)

    def test_each_fixture_fits_its_schema(self) -> None:
        self.assertTrue(FIXTURES)
        for name, entry in FIXTURES.items():
            with self.subTest(name=name):
                self.assertEqual(named(entry["body"], entry["schema"]), [])

    def test_every_p3_response_has_a_fixture(self) -> None:
        schemas = {entry["schema"] for entry in FIXTURES.values()}
        self.assertLessEqual({"Account", "AlertList", "AlertRow", "AlertSave", "AlertListings", "NotificationList", "NotificationRead", "Meta"}, schemas)

    def test_meta_has_the_fields_the_server_sends(self) -> None:
        sent = self.client.get("/api/v1/public/meta/").json()
        self.assert_same_fields(sent, body("meta_on"), "meta")
        self.assert_same_fields(sent["features"], body("meta_on")["features"], "features")

    def test_account_has_the_fields_the_server_sends(self) -> None:
        sent = self.get("/api/v1/me/account/").json()
        fixture = body("account")
        self.assert_same_fields(sent, fixture, "account")
        for part in ("user", "prefs", "counts"):
            self.assert_same_fields(sent[part], fixture[part], part)

    def test_alert_rows_have_the_fields_the_server_sends(self) -> None:
        saved = self.create(types=["bed1"], max=2000, furnished=True).json()
        self.assert_same_fields(saved, body("alert_save"), "save")
        self.assertIn(saved["save_outcome"], {"created", "duplicate", "reactivated"})
        listed = self.get("/api/v1/me/alerts/").json()
        self.assert_same_fields(listed, body("alert_list"), "list")
        row = listed["results"][0]
        for name, fixture in (("list", body("alert_list")["results"][0]), ("paused", body("alert_paused")), ("wanted", body("alert_wanted")), ("gone", body("alert_city_gone"))):
            self.assert_same_fields(row, fixture, name)
        # The filters the server sends are all ones the client reads; every key a fixture uses is one it can send.
        allowed = set(SCHEMA["components"]["schemas"]["AlertFilters"]["properties"])
        self.assertLessEqual(set(row["filters"]), allowed)
        for fixture in (*body("alert_list")["results"], body("alert_paused"), body("alert_wanted"), body("alert_city_gone")):
            self.assertLessEqual(set(fixture["filters"]), allowed)
        self.assertEqual(row["filters"]["types"], ["bed1"])

    def test_alert_listings_have_the_fields_the_server_sends(self) -> None:
        alert_id = self.create(max=2000).json()["alert"]["id"]
        self.assert_same_fields(self.get(f"/api/v1/me/alerts/{alert_id}/listings/").json(), body("alert_listings"), "listings")

    def test_notification_rows_have_the_fields_the_server_sends(self) -> None:
        HomeNotification.objects.create(user=self.user, kind=NotificationKind.LISTING_LIVE, title="x")
        sent = self.get("/api/v1/me/notifications/").json()
        self.assert_same_fields(sent, body("notifications"), "list")
        for fixture in body("notifications")["results"]:
            self.assert_same_fields(sent["results"][0], fixture, str(fixture["id"]))
        self.assert_same_fields(self.post("/api/v1/me/notifications/read/").json(), body("notifications_read"), "read")
