"""/api/v1/public/courier/: anonymous read-only board. Nothing about a person may leave, whatever the request holds."""

from __future__ import annotations

import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from courier.models import CourierSettings
from courier_api import public
from home.models import HomeSettings
from item_requests.models import ItemRequest, RequestStatus
from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from tests.helpers import TEST_SECRET, make_user
from tests.test_courier_api import package_payload, trip_payload

BASE = "/api/v1/public/courier"
PHONE = "+14165550199"
SECRETS = ("omar_secret", "Omar", "Karimi", PHONE, "call me at", "t.me/", "https://source.example", "7101", "photo.jpg")
CARD_KEYS = set(public.PUBLIC_FIELDS)
FORBIDDEN_KEYS = {
    "user", "user_id", "poster", "poster_id", "name", "first_name", "last_name", "username", "telegram_username",
    "telegram_user_id", "phone", "contact", "description", "excluded_other_text", "source_url", "photos", "photo",
    "reach", "intro_id", "intro_status", "my_request", "handle", "trust_count", "refused",
}


def walk_keys(value, found: set | None = None) -> set:
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, inner in value.items():
            found.add(key)
            walk_keys(inner, found)
    elif isinstance(value, list):
        for inner in value:
            walk_keys(inner, found)
    return found


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False, TELEGRAM_CHANNEL_ENABLED=False)
class PublicCourierTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        seed_catalog()
        CourierSettings.objects.filter(pk=1).delete()
        CourierSettings.objects.create(pk=1, courier_v2=True, intros_enabled=True)
        row = HomeSettings.load()
        row.web_login_on = True
        row.save()
        patcher = patch("notifications.telegram.call_telegram_api", return_value={"ok": True, "result": {"message_id": 1}})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.omar = make_user(telegram_user_id=7101, first_name="Omar", last_name="Karimi", telegram_username="omar_secret")
        self.sara = make_user(telegram_user_id=7001, first_name="Sara", telegram_username="sara_secret")
        note = f"call me at {PHONE} or t.me/omar_secret"
        self.trip = create_item_request(self.omar, trip_payload(description=note), sync_channel=False)
        ItemRequest.objects.filter(pk=self.trip.pk).update(source_url="https://source.example/post/1")
        self.package = create_item_request(self.sara, package_payload(description=note), sync_channel=False)

    def get(self, path: str, **params):
        return self.client.get(BASE + path, params, REMOTE_ADDR="203.0.113.9")

    def assert_clean(self, response) -> dict:
        self.assertEqual(response.status_code, 200, response.content)
        text = response.content.decode()
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        body = json.loads(text)
        self.assertFalse(walk_keys(body) & FORBIDDEN_KEYS, walk_keys(body) & FORBIDDEN_KEYS)
        return body

    def test_list_trips_without_account(self) -> None:
        body = self.assert_clean(self.get("/requests/", side="DEMAND", origin="CA:toronto", destination="IR:tehran"))
        self.assertEqual(body["total"], 1)
        card = body["items"][0]
        self.assertEqual(set(card), CARD_KEYS)
        self.assertEqual(card["id"], self.trip.pk)
        self.assertEqual(card["type"], "SUPPLY")
        self.assertEqual(card["origin"]["city"], "toronto")
        self.assertEqual(card["stops"][0]["city"], "tehran")
        self.assertEqual(card["capacity_kg"], "5.00")
        self.assertEqual({row["code"] for row in card["categories"]}, {"DOCUMENTS", "CLOTHES"})

    def test_list_requests_side(self) -> None:
        body = self.assert_clean(self.get("/requests/", side="SUPPLY"))
        self.assertEqual([row["id"] for row in body["items"]], [self.package.pk])
        self.assertEqual(body["items"][0]["weight_kg"], "2.00")

    def test_list_filters_and_paging(self) -> None:
        body = self.assert_clean(self.get("/requests/", side="DEMAND", origin="DE:berlin"))
        self.assertEqual(body["total"], 0)
        body = self.assert_clean(self.get("/requests/", side="DEMAND", limit=1, offset=0))
        self.assertIsNone(body["next_offset"])
        response = self.get("/requests/", side="DEMAND", origin="CA:toronto", destination="CA:toronto")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "same_city")
        self.assertEqual(self.get("/requests/", side="NOPE").status_code, 400)

    def test_ids_param_is_ignored(self) -> None:
        body = self.assert_clean(self.get("/requests/", side="DEMAND", ids=str(self.package.pk)))
        self.assertEqual([row["id"] for row in body["items"]], [self.trip.pk])

    def test_detail_has_no_contact(self) -> None:
        body = self.assert_clean(self.get(f"/requests/{self.trip.pk}/"))
        self.assertEqual(set(body), CARD_KEYS)
        self.assertEqual(body["id"], self.trip.pk)

    def test_detail_closed_or_missing(self) -> None:
        self.assertEqual(self.get("/requests/999999/").status_code, 404)
        ItemRequest.objects.filter(pk=self.package.pk).update(status=RequestStatus.CANCELLED)
        response = self.get(f"/requests/{self.package.pk}/")
        self.assertIn(response.json()["code"], {"request_inactive", "request_date_passed"})
        self.assertNotIn("omar_secret", response.content.decode())

    def test_facets_locations_guide(self) -> None:
        self.assert_clean(self.get("/requests/facets/", side="DEMAND", origin="CA:toronto"))
        body = self.assert_clean(self.get("/locations/"))
        self.assertIn("countries", body)
        self.assertIn("categories", body)
        self.assert_clean(self.get("/guide/"))

    def test_cached_and_marked(self) -> None:
        response = self.get("/requests/", side="DEMAND")
        self.assertEqual(response["Cache-Control"], public.CACHE_HEADER)
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        ItemRequest.objects.filter(pk=self.trip.pk).update(status=RequestStatus.CANCELLED)
        self.assertEqual(self.get("/requests/", side="DEMAND").json()["total"], 1)  # served from the cache
        cache.clear()
        self.assertEqual(self.get("/requests/", side="DEMAND").json()["total"], 0)

    def test_cache_key_ignores_unknown_params_and_offset_is_capped(self) -> None:
        self.get("/requests/", side="DEMAND")
        ItemRequest.objects.filter(pk=self.trip.pk).update(status=RequestStatus.CANCELLED)
        self.assertEqual(self.get("/requests/", side="DEMAND", junk="1").json()["total"], 1)  # same cache entry
        self.assertEqual(self.get("/requests/", side="DEMAND", offset=100000).status_code, 400)

    def test_rate_limited_per_address(self) -> None:
        with patch.object(public.PublicThrottle, "rate", "3/min"):
            codes = [self.get("/locations/").status_code for _ in range(4)]
            self.assertEqual(codes, [200, 200, 200, 429])
            other = self.client.get(BASE + "/locations/", REMOTE_ADDR="198.51.100.7")
            self.assertEqual(other.status_code, 200)

    def test_read_only(self) -> None:
        for method in ("post", "patch", "put", "delete"):
            response = getattr(self.client, method)(BASE + "/requests/", data="{}", content_type="application/json")
            self.assertEqual(response.status_code, 405, method)

    def test_closed_when_flags_off(self) -> None:
        HomeSettings.objects.update(web_login_on=False)
        self.assertEqual(self.get("/requests/", side="DEMAND").status_code, 404)
        HomeSettings.objects.update(web_login_on=True)
        CourierSettings.objects.update(courier_v2=False)
        cache.clear()
        self.assertEqual(self.get("/requests/", side="DEMAND").status_code, 404)

    def test_maintenance(self) -> None:
        CourierSettings.objects.update(maintenance_on=True)
        self.assertEqual(self.get("/requests/", side="DEMAND").json()["code"], "maintenance")

    def test_session_cookie_is_ignored(self) -> None:
        """A signed-in browser gets the same public shape: the caller's own request is not marked or hidden."""
        self.client.cookies["chm_web"] = "anything"
        body = self.assert_clean(self.get("/requests/", side="SUPPLY"))
        self.assertEqual(body["total"], 1)

    def test_serializer_field_list_is_pinned(self) -> None:
        self.assertEqual(set(public.PublicCardSerializer().fields), CARD_KEYS)
        self.assertEqual(
            CARD_KEYS,
            {"id", "type", "origin", "stops", "date", "date_kind", "capacity_kg", "weight_kg", "categories", "created_at"},
        )
