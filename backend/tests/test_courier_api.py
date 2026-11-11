"""/api/v1/courier/: every `code` of the design's matrices, the contract, and no contact before acceptance.

Telegram is never called for real: `notifications.telegram.call_telegram_api` is patched for every test.
"""

from __future__ import annotations

import io
import json
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import jwt
import yaml
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from courier import conf, search, texts
from courier.models import CourierIdempotencyKey, CourierIntro, CourierRouteAlert, CourierSettings, CourierUserPrefs, IntroStatus
from courier_api.errors import CLIENT_ONLY, CODES, IN_RESPONSE_FIELDS
from item_requests.explore import public_open_requests_queryset
from item_requests.models import UNMATCHABLE_TEST_POST, ItemRequest, RequestStatus, RequestType
from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from matching.contact import contact_for_match
from matching.models import Match, MatchRating, MatchStatus
from outreach.models import OutreachMessage
from support.models import TicketStatus
from tests.helpers import TEST_SECRET, bearer_auth, make_user
from tests.test_telegram_auth import TEST_BOT_TOKEN, build_init_data
from users.tokens import issue_access_token

BASE = "/api/v1/courier"
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "courier_api" / "openapi.yaml"
SCHEMA = yaml.safe_load(SCHEMA_FILE.read_text(encoding="utf-8"))
SHADOW_ID = 9_000_000_000_555
# Declared in the contract, raised only with courier_intro_everywhere on.
NOT_RAISED_YET = {"intro_undelivered"}
# Every outcome the design's matrices name (COURIER-FLOWS.md, 2026-11-02).
MATRIX_CODES = """
already_rated alert_cap alert_exists alert_not_found alerts_sending_off auth_invalid auth_required category_required
channel_off city_invalid contact_no_username contact_not_accepted copy_failed counts_failed date_invalid date_past
duplicate_request facets_failed feature_missing flag_off flight_date_required intro_cap_self intro_cap_target
intro_closed intro_declined intro_expired intro_no_public_contact intro_send_retry intro_undelivered intro_unreachable
intros_off link_failed locations_failed maintenance match_expired match_not_open matches_failed network_error not_editable
not_found ok ok_empty outreach_other_person own_request pending pref_save_failed rate_limited rating_invalid
request_date_passed request_expired request_inactive route_required same_city server_error server_rejected session_expired
start_invalid stops_one_country ticket_closed ticket_invalid token_expired unknown_code user_blocked version_mismatch
""".split()


def days(count: int) -> str:
    return (timezone.localdate() + timedelta(days=count)).isoformat()


def trip_payload(**changes) -> dict:
    return {
        "type": RequestType.SUPPLY,
        "origin_country": "CA",
        "origin_city": "toronto",
        "destination_country": "IR",
        "destination_city": "tehran",
        "date_from": days(0),
        "date_to": days(12),
        "flight_date": days(12),
        "capacity_kg": "5.00",
        "item_category_codes": ["DOCUMENTS", "CLOTHES"],
        **changes,
    }


def package_payload(**changes) -> dict:
    return {
        "type": RequestType.DEMAND,
        "origin_country": "CA",
        "origin_city": "toronto",
        "destination_country": "IR",
        "destination_city": "tehran",
        "desired_date": days(20),
        "weight_kg": "2.00",
        "item_category_codes": ["CLOTHES"],
        **changes,
    }


def form(**changes) -> dict:
    return {
        "origin": {"country": "CA", "city": "toronto"},
        "destinations": [{"country": "IR", "city": "tehran"}],
        "when": {"kind": "exact", "date": days(15)},
        "size": "3",
        **changes,
    }


@override_settings(SECRET_KEY=TEST_SECRET, TELEGRAM_BOT_TOKEN=TEST_BOT_TOKEN, DEBUG=False, TELEGRAM_CHANNEL_ENABLED=False)
class ApiCase(TestCase):
    def setUp(self) -> None:
        cache.clear()
        seed_catalog()
        CourierSettings.objects.filter(pk=1).delete()
        CourierSettings.objects.create(pk=1, courier_v2=True, intros_enabled=True)
        patcher = patch("notifications.telegram.call_telegram_api", return_value={"ok": True, "result": {"message_id": 1}})
        self.telegram = patcher.start()
        self.addCleanup(patcher.stop)
        self.sara = make_user(telegram_user_id=7001, first_name="Sara", telegram_username="sara_secret")
        self.omar = make_user(telegram_user_id=7101, first_name="Omar", telegram_username="omar_secret")
        self.auth = bearer_auth(self.sara)

    def trip(self, user=None, **changes) -> ItemRequest:
        return create_item_request(user or self.omar, trip_payload(**changes), sync_channel=False)

    def package(self, user=None, **changes) -> ItemRequest:
        return create_item_request(user or self.sara, package_payload(**changes), sync_channel=False)

    def get(self, path: str, auth: dict | None = None, **params):
        return self.client.get(BASE + path, params, **(self.auth if auth is None else auth))

    def send(self, method: str, path: str, body: dict | None = None, auth: dict | None = None, **headers):
        call = getattr(self.client, method)
        return call(BASE + path, data=json.dumps(body) if body is not None else None, content_type="application/json", **(self.auth if auth is None else auth), **headers)

    def post(self, path: str, body: dict | None = None, auth: dict | None = None, **headers):
        return self.send("post", path, body, auth, **headers)

    def patch_(self, path: str, body: dict | None = None, auth: dict | None = None):
        return self.send("patch", path, body, auth)

    def operation(self, pattern: str, method: str) -> dict:
        return SCHEMA["paths"][BASE + pattern][method.lower()]

    def assert_error(self, response, code: str, pattern: str) -> dict:
        """The body is exactly the contract, and the contract lists this code for this operation and status."""
        method = response.request["REQUEST_METHOD"]
        self.assertEqual(response.status_code, CODES[code][0], response.content)
        self.assertEqual(response["Content-Type"], "application/json")
        body = response.json()
        self.assertEqual(body["code"], code)
        self.assertIsInstance(body["detail"], str)
        operation = self.operation(pattern, method)
        self.assertIn(code, operation["x-error-codes"])
        enum = operation["responses"][str(response.status_code)]["content"]["application/json"]["schema"]["properties"]["code"]["enum"]
        self.assertIn(code, enum)
        return body

    def connected_match(self, status: str = MatchStatus.CONNECTED, *, before_api_opened: bool = True) -> Match:
        mine = self.package(desired_date=days(30))
        other = self.trip(flight_date=days(25), date_to=days(25))
        Match.objects.filter(demand_request=mine, supply_request=other).delete()
        match = Match.objects.create(demand_request=mine, supply_request=other, score=80, status=status, initiated_by=self.sara)
        if before_api_opened:
            Match.objects.filter(pk=match.pk).update(created_at=CourierSettings.load().courier_api_opened_at - timedelta(minutes=1))
            match.refresh_from_db()
        return match


class ContractTests(ApiCase):
    def test_openapi_file_matches_the_code(self) -> None:
        out = io.StringIO()
        call_command("courier_openapi", "--check", stdout=out)
        self.assertIn("matches", out.getvalue())

    def test_drift_fails_the_check(self) -> None:
        with patch("courier_api.management.commands.courier_openapi.build_schema", return_value="changed"):
            with self.assertRaises(CommandError):
                call_command("courier_openapi", "--check", stdout=io.StringIO())

    def test_every_code_in_the_contract_exists_and_each_is_used(self) -> None:
        declared = set()
        for methods in SCHEMA["paths"].values():
            for operation in methods.values():
                declared |= set(operation["x-error-codes"])
        self.assertEqual(declared - set(CODES), set())
        self.assertEqual(set(CODES) - declared, set())

    def test_every_matrix_outcome_has_one_home(self) -> None:
        known = set(CODES) | set(IN_RESPONSE_FIELDS) | set(CLIENT_ONLY)
        self.assertEqual([code for code in MATRIX_CODES if code not in known], [])
        self.assertEqual(set(CODES) & set(IN_RESPONSE_FIELDS), set())
        self.assertEqual(set(CODES) & set(CLIENT_ONLY), set())

    def test_not_served_yet_codes_say_so(self) -> None:
        served = json.dumps(SCHEMA["paths"], ensure_ascii=False)
        for code in NOT_RAISED_YET:
            self.assertIn(f"`{code}` only with `courier_intro_everywhere` on", served)

    def test_unknown_route_and_wrong_method_are_coded(self) -> None:
        response = self.get("/nope/")
        self.assertEqual((response.status_code, response.json()["code"]), (404, "not_found"))
        response = self.client.delete(f"{BASE}/guide/", **self.auth)
        self.assertEqual(response.json()["code"], "method_not_allowed")

    def test_a_failure_inside_a_view_is_server_error_with_no_trace(self) -> None:
        with patch("courier_api.views.texts.prohibited_items", side_effect=RuntimeError("secret path")):
            self.client.raise_request_exception = False
            response = self.get("/guide/")
        body = self.assert_error(response, "server_error", "/guide/")
        self.assertNotIn("secret", json.dumps(body))

    def test_rate_limited_has_retry_after(self) -> None:
        with patch("courier_api.views.ReadThrottle.allow_request", return_value=False), patch("courier_api.views.ReadThrottle.wait", return_value=30):
            response = self.get("/guide/")
        self.assert_error(response, "rate_limited", "/guide/")
        self.assertEqual(response["Retry-After"], "30")

    def test_bad_json_is_bad_request(self) -> None:
        response = self.client.post(f"{BASE}/cities/", data="{nope", content_type="application/json", **self.auth)
        self.assert_error(response, "bad_request", "/cities/")


class FlagAndAuthTests(ApiCase):
    def test_flag_is_off_by_default_for_everyone(self) -> None:
        CourierSettings.objects.filter(pk=1).delete()
        self.assertFalse(CourierSettings.load().courier_v2)
        self.assertFalse(conf.courier_v2_for(self.sara.telegram_user_id))
        self.assert_error(self.get("/guide/"), "flag_off", "/guide/")

    def test_flag_off_closes_writes_and_nothing_happens(self) -> None:
        CourierSettings.objects.filter(pk=1).update(courier_v2=False)
        before = ItemRequest.objects.count()
        self.assert_error(self.post("/me/requests/", {**form(), "type": "DEMAND"}), "flag_off", "/me/requests/")
        self.assertEqual(ItemRequest.objects.count(), before)
        self.telegram.assert_not_called()

    def test_allowlist_opens_it_for_one_person(self) -> None:
        CourierSettings.objects.filter(pk=1).update(courier_v2=False, courier_v2_allowlist="1234, 7001\n99")
        self.assertEqual(self.get("/guide/").status_code, 200)
        self.assert_error(self.get("/guide/", auth=bearer_auth(self.omar)), "flag_off", "/guide/")

    @override_settings(COURIER_V2=True)
    def test_env_turns_it_on(self) -> None:
        CourierSettings.objects.filter(pk=1).update(courier_v2=False)
        self.assertEqual(self.get("/guide/").status_code, 200)

    def test_meta_is_open_and_cached(self) -> None:
        CourierSettings.objects.filter(pk=1).update(courier_v2=False, maintenance_on=True)
        response = self.client.get(f"{BASE}/meta/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"api_version": 1, "min_api_version": 1, "courier_v2": False, "maintenance": True, "features": conf.v4_features()})
        self.assertIn("s-maxage", response["Cache-Control"])

    def test_maintenance_closes_everything_else(self) -> None:
        CourierSettings.objects.filter(pk=1).update(maintenance_on=True)
        self.assert_error(self.get("/guide/"), "maintenance", "/guide/")

    def test_no_token(self) -> None:
        self.assert_error(self.get("/guide/", auth={}), "auth_required", "/guide/")

    def test_bad_token(self) -> None:
        self.assert_error(self.get("/guide/", auth={"HTTP_AUTHORIZATION": "Bearer nope"}), "auth_invalid", "/guide/")
        self.assert_error(self.get("/guide/", auth={"HTTP_AUTHORIZATION": "Bearer a b"}), "auth_invalid", "/guide/")

    def test_token_for_someone_gone(self) -> None:
        token = jwt.encode({"user_id": 999999, "telegram_user_id": 1}, TEST_SECRET, algorithm="HS256")
        self.assert_error(self.get("/guide/", auth={"HTTP_AUTHORIZATION": f"Bearer {token}"}), "auth_invalid", "/guide/")

    def test_expired_token(self) -> None:
        token = jwt.encode({"user_id": self.sara.pk, "telegram_user_id": 7001, "exp": int(time.time()) - 10}, TEST_SECRET, algorithm="HS256")
        self.assert_error(self.get("/guide/", auth={"HTTP_AUTHORIZATION": f"Bearer {token}"}), "token_expired", "/guide/")

    def test_blocked_user(self) -> None:
        token = {"HTTP_AUTHORIZATION": f"Bearer {issue_access_token(self.sara)}"}
        self.sara.is_active = False
        self.sara.save(update_fields=["is_active"])
        self.assert_error(self.get("/guide/", auth=token), "user_blocked", "/guide/")

    @patch("courier_api.views.MIN_API_VERSION", 2)
    def test_old_bundle(self) -> None:
        response = self.get("/guide/", auth={**self.auth, "HTTP_X_COURIER_API_VERSION": "1"})
        self.assert_error(response, "version_mismatch", "/guide/")
        self.assertEqual(self.get("/guide/").status_code, 200)

    def test_exchange(self) -> None:
        init_data = build_init_data(telegram_user_id=7001, first_name="Sara")
        response = self.client.post(f"{BASE}/auth/exchange/", data=json.dumps({"init_data": init_data}), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["courier_v2"])
        self.assertEqual(self.get("/guide/", auth={"HTTP_AUTHORIZATION": f"Bearer {body['access_token']}"}).status_code, 200)

    def test_exchange_outcomes(self) -> None:
        def exchange(init_data: str):
            return self.client.post(f"{BASE}/auth/exchange/", data=json.dumps({"init_data": init_data}), content_type="application/json")

        self.assert_error(exchange(""), "auth_required", "/auth/exchange/")
        self.assert_error(exchange(build_init_data(telegram_user_id=7001, first_name="Sara", tamper_hash=True)), "auth_invalid", "/auth/exchange/")
        self.assert_error(exchange(build_init_data(telegram_user_id=7001, first_name="Sara", auth_date=int(time.time()) - 90000)), "session_expired", "/auth/exchange/")
        with override_settings(TELEGRAM_BOT_TOKEN="", HOME_BOT_TOKEN="", TELEGRAM_MINI_APP_BOT_TOKENS=""):
            with patch("users.telegram.mini_app_bot_tokens", return_value=[]):
                self.assert_error(exchange("x=1"), "auth_unavailable", "/auth/exchange/")

    def test_blocked_user_replaying_init_data_writes_nothing(self) -> None:
        self.sara.is_active = False
        self.sara.save(update_fields=["is_active"])
        shadow = make_user(telegram_user_id=SHADOW_ID, first_name="Shadow", telegram_username="sara_blocked")
        demand = create_item_request(shadow, package_payload(), imported=True, source_url="https://t.me/src/9", sync_channel=False)
        init_data = build_init_data(telegram_user_id=7001, first_name="Renamed", username="sara_blocked")
        with patch("courier_api.views.upsert_telegram_user") as upsert, CaptureQueriesContext(connection) as queries:
            response = self.client.post(f"{BASE}/auth/exchange/", data=json.dumps({"init_data": init_data}), content_type="application/json")
        self.assert_error(response, "user_blocked", "/auth/exchange/")
        upsert.assert_not_called()
        self.assertEqual([q["sql"] for q in queries if q["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}], [])
        response = self.client.post(f"{BASE}/auth/exchange/", data=json.dumps({"init_data": init_data}), content_type="application/json")
        self.assert_error(response, "user_blocked", "/auth/exchange/")
        self.sara.refresh_from_db()
        demand.refresh_from_db()
        self.assertEqual((self.sara.first_name, demand.user_id), ("Sara", shadow.pk))


class BrowseTests(ApiCase):
    def test_locations_and_frequent_routes_have_no_flags(self) -> None:
        body = self.get("/locations/").json()
        self.assertTrue(body["countries"][0]["cities"])
        self.assertNotIn("flag", body["countries"][0])
        self.assertEqual(len(body["frequent"]), 3)
        self.assertTrue(body["categories"])

    def test_typed_city(self) -> None:
        response = self.post("/cities/", {"country": "CA", "name": "Halifax"})
        self.assertEqual((response.status_code, response.json()["slug"]), (200, "halifax"))
        self.assert_error(self.post("/cities/", {"country": "ZZ", "name": "Nowhere"}), "city_invalid", "/cities/")
        self.assert_error(self.post("/cities/", {"country": "CA"}), "validation_failed", "/cities/")

    def test_list_is_nearest_date_first_without_own(self) -> None:
        self.trip(flight_date=days(30), date_to=days(30))
        near = self.trip(user=make_user(telegram_user_id=7102, first_name="Ali"), flight_date=days(5), date_to=days(5))
        self.trip(user=self.sara, flight_date=days(4), date_to=days(4))
        body = self.get("/requests/", side="DEMAND", origin="CA:toronto", destination="IR:tehran").json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["items"][0]["id"], near.pk)
        self.assertIsNone(body["next_offset"])
        self.assertEqual(body["items"][0]["date_kind"], "flight")

    def test_empty_route_is_ok(self) -> None:
        response = self.get("/requests/", side="DEMAND", origin="DE:berlin", destination="IR:tehran")
        self.assertEqual((response.status_code, response.json()["items"]), (200, []))

    def test_pages(self) -> None:
        for index in range(3):
            self.trip(user=make_user(telegram_user_id=7200 + index, first_name="T"), flight_date=days(5 + index), date_to=days(5 + index))
        first = self.get("/requests/", limit=2).json()
        self.assertEqual((len(first["items"]), first["total"], first["next_offset"]), (2, 3, 2))
        second = self.get("/requests/", limit=2, offset=first["next_offset"]).json()
        self.assertEqual((len(second["items"]), second["next_offset"]), (1, None))

    def test_route_errors(self) -> None:
        self.assert_error(self.get("/requests/", origin="CA:toronto", destination="CA:toronto"), "same_city", "/requests/")
        self.assert_error(self.get("/requests/", origin="toronto"), "validation_failed", "/requests/")
        self.assert_error(self.get("/requests/", limit=500), "validation_failed", "/requests/")

    def test_by_ids_keeps_order_and_drops_closed(self) -> None:
        one, two = self.trip(flight_date=days(5), date_to=days(5)), self.trip(flight_date=days(6), date_to=days(6))
        gone = self.trip(flight_date=days(7), date_to=days(7))
        ItemRequest.objects.filter(pk=gone.pk).update(status=RequestStatus.CLOSED)
        body = self.get("/requests/", ids=f"{two.pk},{gone.pk},{one.pk},x").json()
        self.assertEqual([row["id"] for row in body["items"]], [two.pk, one.pk])

    def test_facets_count_each_section_from_the_other(self) -> None:
        self.trip(capacity_kg="5.00", flight_date=days(5), date_to=days(5))
        self.trip(user=make_user(telegram_user_id=7103, first_name="B"), capacity_kg="0.50", flight_date=days(6), date_to=days(6))
        all_sizes = self.get("/requests/facets/", origin="CA:toronto", destination="IR:tehran").json()
        self.assertEqual(all_sizes["total"], 2)
        by_code = {row["code"]: row["count"] for row in all_sizes["sizes"]}
        self.assertEqual(by_code["3"], 1)
        narrowed = self.get("/requests/facets/", origin="CA:toronto", destination="IR:tehran", size="3").json()
        self.assertEqual(narrowed["total"], 1)
        self.assertEqual(len(narrowed["months"]), 3)
        self.assertEqual({row["code"]: row["count"] for row in narrowed["sizes"]}["3"], 1)
        self.assert_error(self.get("/requests/facets/", origin="CA:toronto", destination="CA:toronto"), "same_city", "/requests/facets/")

    def test_category_filter_and_facets(self) -> None:
        self.trip(flight_date=days(5), date_to=days(5))
        self.trip(user=make_user(telegram_user_id=7104, first_name="C"), item_category_codes=["FOOD"], flight_date=days(6), date_to=days(6))
        facets = self.get("/requests/facets/", origin="CA:toronto", destination="IR:tehran").json()
        by_code = {row["code"]: row["count"] for row in facets["categories"]}
        self.assertEqual((by_code["DOCUMENTS"], by_code["FOOD"]), (1, 1))
        narrowed = self.get("/requests/facets/", origin="CA:toronto", destination="IR:tehran", category="food").json()
        self.assertEqual(narrowed["total"], 1)
        listed = self.get("/requests/", side="DEMAND", origin="CA:toronto", destination="IR:tehran", category="DOCUMENTS").json()
        self.assertEqual(listed["total"], 1)
        self.assert_error(self.get("/requests/", category="NOPE"), "validation_failed", "/requests/")

    def test_guide(self) -> None:
        CourierSettings.objects.filter(pk=1).update(prohibited_text="یک\nدو")
        body = self.get("/guide/").json()
        self.assertEqual(body["prohibited"], ["یک", "دو"])
        self.assertEqual(body["prohibited_footer"], texts.PROHIBITED_FOOTER)


class PostAnywayTests(ApiCase):
    def test_posting_the_same_twice_is_refused_until_the_person_chooses(self) -> None:
        form = {
            "type": "DEMAND",
            "origin": {"country": "CA", "city": "toronto"},
            "destinations": [{"country": "IR", "city": "tehran"}],
            "when": {"kind": "exact", "date": days(20)},
            "size": "1",
        }
        first = self.post("/me/requests/", form)
        self.assertEqual(first.status_code, 201, first.content)
        body = self.assert_error(self.post("/me/requests/", form), "duplicate_request", "/me/requests/")
        self.assertEqual(body["request_id"], first.json()["id"])
        self.assertEqual(self.post("/me/requests/", {**form, "allow_duplicate": True}).status_code, 201)


class PublishedContactTests(ApiCase):
    def test_only_what_the_author_published_and_nothing_is_sent(self) -> None:
        shadow = make_user(telegram_user_id=SHADOW_ID, first_name="Imported", telegram_username="public_handle")
        imported = create_item_request(shadow, trip_payload(), imported=True, source_url="https://t.me/src/1", sync_channel=False)
        body = self.get(f"/requests/{imported.pk}/published/").json()
        self.assertEqual(body, {"handle": "public_handle", "source_url": "https://t.me/src/1"})
        self.assertFalse(CourierIntro.objects.exists())

    def test_someone_reachable_shows_nothing(self) -> None:
        item = self.trip()
        self.assert_error(self.get(f"/requests/{item.pk}/published/"), "contact_not_accepted", "/requests/{id}/published/")

    def test_nothing_published(self) -> None:
        none = make_user(telegram_user_id=SHADOW_ID + 1, first_name="X")
        silent = create_item_request(none, trip_payload(), imported=True, sync_channel=False)
        self.assert_error(self.get(f"/requests/{silent.pk}/published/"), "intro_no_public_contact", "/requests/{id}/published/")

    def test_not_a_web_link_stays_off_the_button(self) -> None:
        shadow = make_user(telegram_user_id=SHADOW_ID + 2, first_name="Y", telegram_username="h_andle")
        imported = create_item_request(shadow, trip_payload(), imported=True, source_url="tg://resolve?domain=x", sync_channel=False)
        self.assertIsNone(self.get(f"/requests/{imported.pk}/published/").json()["source_url"])

    def test_switch_off(self) -> None:
        shadow = make_user(telegram_user_id=SHADOW_ID + 3, first_name="Z", telegram_username="z_handle")
        imported = create_item_request(shadow, trip_payload(), imported=True, source_url="https://t.me/src/2", sync_channel=False)
        CourierSettings.objects.filter(pk=1).update(intros_enabled=False)
        self.assert_error(self.get(f"/requests/{imported.pk}/published/"), "intros_off", "/requests/{id}/published/")


class RequestDetailTests(ApiCase):
    def test_no_contact_or_name_on_any_public_shape(self) -> None:
        item = self.trip(description="از تورنتو می‌روم @omar_secret یا t.me/omar_secret یا ۰۹۱۲۳۴۵۶۷۸۹")
        for response in (self.get(f"/requests/{item.pk}/"), self.get("/requests/", origin="CA:toronto")):
            self.assertEqual(response.status_code, 200)
            raw = response.content.decode()
            for word in ("omar_secret", "Omar", "7101", "source_url", "t.me", "912345"):
                self.assertNotIn(word, raw)
        self.assertIn("از تورنتو", self.get(f"/requests/{item.pk}/").json()["description"])

    def test_own_request(self) -> None:
        own = self.trip(user=self.sara)
        body = self.assert_error(self.get(f"/requests/{own.pk}/"), "own_request", "/requests/{id}/")
        self.assertEqual(body["request_id"], own.pk)

    def test_unknown(self) -> None:
        self.assert_error(self.get("/requests/999999/"), "not_found", "/requests/{id}/")

    def test_inactive_offers_the_route(self) -> None:
        item = self.trip()
        ItemRequest.objects.filter(pk=item.pk).update(status=RequestStatus.CLOSED)
        body = self.assert_error(self.get(f"/requests/{item.pk}/"), "request_inactive", "/requests/{id}/")
        self.assertEqual(body["route"], {"side": "DEMAND", "origin": "CA:toronto", "destination": "IR:tehran"})

    def test_date_passed(self) -> None:
        item = self.trip()
        ItemRequest.objects.filter(pk=item.pk).update(flight_date=timezone.localdate() - timedelta(days=1), date_to=timezone.localdate() - timedelta(days=1))
        self.assert_error(self.get(f"/requests/{item.pk}/"), "request_date_passed", "/requests/{id}/")

    def test_reach_says_what_the_button_does(self) -> None:
        item = self.trip()
        self.assertEqual(self.get(f"/requests/{item.pk}/").json()["reach"], "intro")
        shadow = make_user(telegram_user_id=SHADOW_ID, first_name="Imported", telegram_username="public_handle")
        imported = create_item_request(shadow, trip_payload(), imported=True, source_url="https://t.me/src/1", sync_channel=False)
        body = self.get(f"/requests/{imported.pk}/").json()
        self.assertEqual(body["reach"], "published")
        self.assertNotIn("public_handle", json.dumps(body))
        self.assertNotIn("t.me/src", json.dumps(body))
        none = make_user(telegram_user_id=SHADOW_ID + 1, first_name="X")
        silent = create_item_request(none, trip_payload(), imported=True, sync_channel=False)
        self.assertEqual(self.get(f"/requests/{silent.pk}/").json()["reach"], "none")
        CourierSettings.objects.filter(pk=1).update(intros_enabled=False)
        self.assertEqual(self.get(f"/requests/{item.pk}/").json()["reach"], "off")

    def test_my_request_on_the_route_fills_the_sheet(self) -> None:
        item = self.trip()
        self.assertIsNone(self.get(f"/requests/{item.pk}/").json()["my_request"])
        mine = self.package()
        self.assertEqual(self.get(f"/requests/{item.pk}/").json()["my_request"]["id"], mine.pk)

    def test_thin_data_has_nulls_not_errors(self) -> None:
        package = self.package(user=self.omar, desired_date=None, desired_date_open=True, date_to=days(20))
        body = self.get(f"/requests/{package.pk}/").json()
        self.assertEqual(body["date_kind"], "by")
        self.assertIsNone(body["capacity_kg"])


class IntroTests(ApiCase):
    def test_send_then_retry_gives_one_intro_and_one_message(self) -> None:
        item = self.trip()
        first = self.post(f"/requests/{item.pk}/intro/")
        self.assertEqual(first.status_code, 201, first.content)
        body = first.json()
        self.assertEqual((body["status"], body["role"]), ("PENDING", "requester"))
        self.assertIsNone(body["contact"])
        self.assertIsNone(body["requester_first_name"])
        self.assertEqual(self.telegram.call_count, 1)
        self.assertEqual(self.telegram.call_args.args[0], "sendMessage")
        self.assertEqual(self.post(f"/requests/{item.pk}/intro/").status_code, 200)
        self.assertEqual(CourierIntro.objects.count(), 1)
        self.assertEqual(self.telegram.call_count, 1)

    def test_no_contact_before_accept_for_either_side(self) -> None:
        item = self.trip()
        intro_id = self.post(f"/requests/{item.pk}/intro/").json()["id"]
        for auth in (self.auth, bearer_auth(self.omar)):
            raw = self.get(f"/me/intros/{intro_id}/", auth=auth).content.decode()
            self.assertNotIn("omar_secret", raw)
            self.assertNotIn("sara_secret", raw)
        target_view = self.get(f"/me/intros/{intro_id}/", auth=bearer_auth(self.omar)).json()
        self.assertEqual(target_view["requester_first_name"], "Sara")

    def test_accept_gives_both_contact(self) -> None:
        item = self.trip()
        intro_id = self.post(f"/requests/{item.pk}/intro/").json()["id"]
        answered = self.post(f"/me/intros/{intro_id}/respond/", {"accept": True}, auth=bearer_auth(self.omar))
        self.assertEqual(answered.status_code, 200, answered.content)
        self.assertEqual(answered.json()["contact"]["telegram_username"], "sara_secret")
        mine = self.get(f"/me/intros/{intro_id}/").json()
        self.assertEqual(mine["status"], "ACCEPTED")
        self.assertEqual(mine["contact"]["telegram_username"], "omar_secret")
        self.assertIsNone(mine["contact"]["telegram_user_id"])

    def test_accept_with_no_username_shows_id_only_then(self) -> None:
        nameless = make_user(telegram_user_id=7104, first_name="Reza")
        item = self.trip(user=nameless)
        intro_id = self.post(f"/requests/{item.pk}/intro/").json()["id"]
        self.post(f"/me/intros/{intro_id}/respond/", {"accept": True}, auth=bearer_auth(nameless))
        contact = self.get(f"/me/intros/{intro_id}/").json()["contact"]
        self.assertTrue(contact["no_username"])
        self.assertEqual(contact["telegram_user_id"], 7104)

    def test_decline_keeps_ids_hidden_and_closes_the_pair(self) -> None:
        item = self.trip()
        intro_id = self.post(f"/requests/{item.pk}/intro/").json()["id"]
        answered = self.post(f"/me/intros/{intro_id}/respond/", {"accept": False}, auth=bearer_auth(self.omar)).json()
        self.assertEqual(answered["status"], "DECLINED")
        self.assertIsNone(answered["contact"])
        self.assertEqual(self.get(f"/me/intros/{intro_id}/").json()["status"], "DECLINED")
        self.assert_error(self.post(f"/requests/{item.pk}/intro/"), "intro_closed", "/requests/{id}/intro/")

    def test_answering_twice_keeps_the_first(self) -> None:
        item = self.trip()
        intro_id = self.post(f"/requests/{item.pk}/intro/").json()["id"]
        self.post(f"/me/intros/{intro_id}/respond/", {"accept": True}, auth=bearer_auth(self.omar))
        again = self.post(f"/me/intros/{intro_id}/respond/", {"accept": False}, auth=bearer_auth(self.omar)).json()
        self.assertEqual(again["status"], "ACCEPTED")

    def test_late_answer(self) -> None:
        item = self.trip()
        intro_id = self.post(f"/requests/{item.pk}/intro/").json()["id"]
        CourierIntro.objects.filter(pk=intro_id).update(created_at=timezone.now() - timedelta(hours=conf.INTRO_EXPIRE_HOURS + 1))
        response = self.post(f"/me/intros/{intro_id}/respond/", {"accept": True}, auth=bearer_auth(self.omar))
        self.assert_error(response, "intro_expired", "/me/intros/{id}/respond/")
        self.assertEqual(CourierIntro.objects.get(pk=intro_id).status, IntroStatus.EXPIRED)

    def test_answer_after_the_request_closed(self) -> None:
        item = self.trip()
        intro_id = self.post(f"/requests/{item.pk}/intro/").json()["id"]
        ItemRequest.objects.filter(pk=item.pk).update(status=RequestStatus.CLOSED)
        response = self.post(f"/me/intros/{intro_id}/respond/", {"accept": True}, auth=bearer_auth(self.omar))
        self.assert_error(response, "request_inactive", "/me/intros/{id}/respond/")
        self.assertEqual(CourierIntro.objects.get(pk=intro_id).status, IntroStatus.PENDING)

    def test_answer_after_the_date_passed(self) -> None:
        item = self.trip()
        intro_id = self.post(f"/requests/{item.pk}/intro/").json()["id"]
        yesterday = timezone.localdate() - timedelta(days=1)
        ItemRequest.objects.filter(pk=item.pk).update(flight_date=yesterday, date_to=yesterday)
        response = self.post(f"/me/intros/{intro_id}/respond/", {"accept": True}, auth=bearer_auth(self.omar))
        self.assert_error(response, "request_date_passed", "/me/intros/{id}/respond/")

    def test_only_the_target_answers_and_strangers_see_nothing(self) -> None:
        item = self.trip()
        intro_id = self.post(f"/requests/{item.pk}/intro/").json()["id"]
        self.assert_error(self.post(f"/me/intros/{intro_id}/respond/", {"accept": True}), "not_found", "/me/intros/{id}/respond/")
        stranger = bearer_auth(make_user(telegram_user_id=7105, first_name="Z"))
        self.assert_error(self.get(f"/me/intros/{intro_id}/", auth=stranger), "not_found", "/me/intros/{id}/")
        self.assert_error(self.post(f"/me/intros/{intro_id}/respond/", {"accept": True}, auth=stranger), "not_found", "/me/intros/{id}/respond/")
        self.assert_error(self.post(f"/me/intros/{intro_id}/respond/", {}, auth=bearer_auth(self.omar)), "validation_failed", "/me/intros/{id}/respond/")

    def test_own_inactive_and_passed_requests(self) -> None:
        own = self.trip(user=self.sara)
        self.assert_error(self.post(f"/requests/{own.pk}/intro/"), "own_request", "/requests/{id}/intro/")
        self.assert_error(self.post("/requests/999999/intro/"), "not_found", "/requests/{id}/intro/")
        closed = self.trip()
        ItemRequest.objects.filter(pk=closed.pk).update(status=RequestStatus.CLOSED)
        self.assert_error(self.post(f"/requests/{closed.pk}/intro/"), "request_inactive", "/requests/{id}/intro/")
        passed = self.trip(flight_date=days(9), date_to=days(9))
        yesterday = timezone.localdate() - timedelta(days=1)
        ItemRequest.objects.filter(pk=passed.pk).update(flight_date=yesterday, date_to=yesterday)
        self.assert_error(self.post(f"/requests/{passed.pk}/intro/"), "request_date_passed", "/requests/{id}/intro/")

    def test_intro_switch_off_shows_no_contact_and_sends_nothing(self) -> None:
        item = self.trip()
        CourierSettings.objects.filter(pk=1).update(intros_enabled=False)
        body = self.assert_error(self.post(f"/requests/{item.pk}/intro/"), "intros_off", "/requests/{id}/intro/")
        self.assertEqual(set(body), {"code", "detail"})
        self.telegram.assert_not_called()
        self.assertEqual(CourierIntro.objects.count(), 0)

    def test_imported_author_never_joined(self) -> None:
        shadow = make_user(telegram_user_id=SHADOW_ID, first_name="Imported", telegram_username="public_handle")
        imported = create_item_request(shadow, trip_payload(), imported=True, source_url="https://t.me/src/1", sync_channel=False)
        body = self.assert_error(self.post(f"/requests/{imported.pk}/intro/"), "intro_unreachable", "/requests/{id}/intro/")
        self.assertEqual(set(body), {"code", "detail"})
        self.telegram.assert_not_called()
        none = make_user(telegram_user_id=SHADOW_ID + 1, first_name="X")
        silent = create_item_request(none, trip_payload(), imported=True, sync_channel=False)
        self.assert_error(self.post(f"/requests/{silent.pk}/intro/"), "intro_no_public_contact", "/requests/{id}/intro/")
        self.assertEqual(CourierIntro.objects.count(), 0)

    def test_daily_caps(self) -> None:
        CourierSettings.objects.filter(pk=1).update(intro_daily_limit=1)
        first, second = self.trip(flight_date=days(5), date_to=days(5)), self.trip(flight_date=days(6), date_to=days(6))
        self.assertEqual(self.post(f"/requests/{first.pk}/intro/").status_code, 201)
        self.assert_error(self.post(f"/requests/{second.pk}/intro/"), "intro_cap_self", "/requests/{id}/intro/")
        CourierSettings.objects.filter(pk=1).update(intro_daily_limit=5)
        for index in range(conf.INTRO_TARGET_DAILY_LIMIT - 1):
            asker = make_user(telegram_user_id=7300 + index, first_name="A")
            CourierIntro.objects.create(requester=asker, target=self.omar, target_request=second)
        self.assert_error(self.post(f"/requests/{second.pk}/intro/"), "intro_cap_target", "/requests/{id}/intro/")

    def test_bot_replies_are_unchanged_by_the_shared_outcomes(self) -> None:
        from courier.intros import request_intro

        item = self.trip()
        self.assertIn("فرستاده شد", request_intro(self.sara, item).text)
        self.assertIn("قبلاً رفته", request_intro(self.sara, item).text)
        self.assertIn("خودتونه", request_intro(self.omar, item).text)


class PostingTests(ApiCase):
    def test_post_a_package_like_the_bot(self) -> None:
        response = self.post("/me/requests/", {**form(), "type": "DEMAND"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["status"], "ACTIVE")
        self.assertEqual(body["weight_kg"], "3.00")
        self.assertEqual(body["date"], days(15))
        self.assertFalse(body["channel_on"])
        self.assertEqual(ItemRequest.objects.get(pk=body["id"]).user, self.sara)

    def test_post_a_package_by_month_or_open(self) -> None:
        month = texts.upcoming_months(count=2)[1][0]
        by_month = self.post("/me/requests/", {**form(when={"kind": "month", "month": month}), "type": "DEMAND"})
        self.assertEqual(by_month.status_code, 201, by_month.content)
        self.assertEqual(by_month.json()["date_kind"], "by")
        open_date = self.post("/me/requests/", {**form(when={"kind": "open"}, destinations=[{"country": "DE", "city": "berlin"}]), "type": "DEMAND"})
        self.assertEqual(open_date.status_code, 201, open_date.content)

    def test_post_a_trip_with_stops_and_refusals(self) -> None:
        body = {
            **form(destinations=[{"country": "IR", "city": "tehran"}, {"country": "IR", "city": "shiraz"}]),
            "type": "SUPPLY",
            "category_codes": ["DOCUMENTS"],
            "refused_other": "مایعات",
        }
        response = self.post("/me/requests/", body)
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(len(data["stops"]), 2)
        self.assertEqual(data["excluded_other_text"], "مایعات")
        self.assertIn("CLOTHES", {row["code"] for row in data["refused"]})
        item = ItemRequest.objects.get(pk=data["id"])
        self.assertEqual(item.date_from, timezone.localdate())
        self.assertEqual(item.flight_date.isoformat(), days(15))

    def test_every_refusal_has_its_code(self) -> None:
        cases = [
            ({**form(destinations=[{"country": "CA", "city": "toronto"}]), "type": "DEMAND"}, "same_city"),
            ({**form(when={"kind": "exact", "date": "nope"}), "type": "DEMAND"}, "date_invalid"),
            ({**form(when={"kind": "exact", "date": "2020-01-01"}), "type": "DEMAND"}, "date_invalid"),
            ({**form(when={"kind": "month", "month": "2020-01"}), "type": "DEMAND"}, "date_invalid"),
            ({**form(when={"kind": "exact", "date": "2020-01-01"}), "type": "SUPPLY"}, "date_past"),
            ({**form(when={"kind": "exact", "date": ""}), "type": "SUPPLY"}, "flight_date_required"),
            ({**form(when={"kind": "open"}), "type": "SUPPLY"}, "flight_date_required"),
            ({**form(category_codes=[]), "type": "SUPPLY"}, "category_required"),
            ({**form(destinations=[{"country": "IR", "city": "tehran"}, {"country": "DE", "city": "berlin"}]), "type": "SUPPLY"}, "stops_one_country"),
            ({**form(destinations=[{"country": "IR", "city": "tehran"}, {"country": "IR", "city": "shiraz"}]), "type": "DEMAND"}, "server_rejected"),
            ({**form(category_codes=["NOPE"]), "type": "DEMAND"}, "server_rejected"),
            ({**form(), "type": "DEMAND", "description": "x" * 501}, "validation_failed"),
            ({"type": "DEMAND"}, "validation_failed"),
        ]
        for body, code in cases:
            with self.subTest(code=code):
                self.assert_error(self.post("/me/requests/", body), code, "/me/requests/")
        self.assertEqual(ItemRequest.objects.count(), 0)

    def test_duplicate(self) -> None:
        first = self.post("/me/requests/", {**form(), "type": "DEMAND"}).json()
        body = self.assert_error(self.post("/me/requests/", {**form(), "type": "DEMAND"}), "duplicate_request", "/me/requests/")
        self.assertEqual(body["request_id"], first["id"])

    def test_retry_with_the_key_gets_the_first_answer(self) -> None:
        headers = {"HTTP_IDEMPOTENCY_KEY": "retry-key-0001"}
        first = self.post("/me/requests/", {**form(), "type": "DEMAND"}, **headers)
        second = self.post("/me/requests/", {**form(), "type": "DEMAND"}, **headers)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(ItemRequest.objects.count(), 1)
        self.assertEqual(CourierIdempotencyKey.objects.count(), 1)

    def test_key_is_per_person_and_failures_are_not_kept(self) -> None:
        headers = {"HTTP_IDEMPOTENCY_KEY": "retry-key-0002"}
        self.assert_error(self.post("/me/requests/", {"type": "DEMAND"}, **headers), "validation_failed", "/me/requests/")
        self.assertEqual(CourierIdempotencyKey.objects.count(), 0)
        self.assertEqual(self.post("/me/requests/", {**form(), "type": "DEMAND"}, **headers).status_code, 201)
        other = self.post("/me/requests/", {**form(), "type": "DEMAND"}, auth=bearer_auth(self.omar), **headers)
        self.assertEqual(other.status_code, 201)
        self.assertEqual(ItemRequest.objects.count(), 2)
        self.assert_error(self.post("/me/requests/", {**form(), "type": "DEMAND"}, HTTP_IDEMPOTENCY_KEY="bad key!"), "bad_request", "/me/requests/")


class MyRequestTests(ApiCase):
    def test_lists_split_active_and_archive(self) -> None:
        active, closed = self.package(), self.package(desired_date=days(21))
        ItemRequest.objects.filter(pk=closed.pk).update(status=RequestStatus.CANCELLED)
        self.assertEqual([row["id"] for row in self.get("/me/requests/").json()["items"]], [active.pk])
        archived = self.get("/me/requests/", state="archive").json()["items"]
        self.assertEqual([row["id"] for row in archived], [closed.pk])
        self.assertIsNone(archived[0]["suggestions"])
        self.assert_error(self.get("/me/requests/", state="nope"), "validation_failed", "/me/requests/")

    def test_empty(self) -> None:
        self.assertEqual(self.get("/me/requests/").json(), {"items": []})

    def test_a_failed_count_never_takes_down_the_screen(self) -> None:
        self.package()
        with patch("courier_api.views.search.counterparts", side_effect=RuntimeError):
            self.assertIsNone(self.get("/me/requests/").json()["items"][0]["suggestions"])

    def test_detail_not_mine(self) -> None:
        other = self.trip()
        self.assert_error(self.get(f"/me/requests/{other.pk}/"), "not_found", "/me/requests/{id}/")

    def test_passed_date_expires_and_is_read_only(self) -> None:
        mine = self.package()
        yesterday = timezone.localdate() - timedelta(days=1)
        ItemRequest.objects.filter(pk=mine.pk).update(desired_date=yesterday, date_to=yesterday)
        body = self.get(f"/me/requests/{mine.pk}/").json()
        self.assertEqual(body["status"], "EXPIRED")
        self.assertTrue(body["date_passed"])
        self.assertFalse(body["editable"])
        self.assert_error(self.patch_(f"/me/requests/{mine.pk}/", form()), "not_editable", "/me/requests/{id}/")

    def test_edit_keeps_the_type(self) -> None:
        mine = self.package()
        response = self.patch_(f"/me/requests/{mine.pk}/", form(size="1", description="تازه"))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["type"], body["weight_kg"], body["description"]), ("DEMAND", "1.00", "تازه"))

    def test_edit_refusals(self) -> None:
        mine = self.package()
        self.assert_error(self.patch_(f"/me/requests/{mine.pk}/", form(when={"kind": "exact", "date": "nope"})), "date_invalid", "/me/requests/{id}/")
        self.assert_error(self.patch_(f"/me/requests/{mine.pk}/", form(category_codes=["NOPE"])), "server_rejected", "/me/requests/{id}/")
        self.assert_error(self.patch_("/me/requests/999999/", form()), "not_found", "/me/requests/{id}/")

    def test_close_a_trip_and_cancel_a_package(self) -> None:
        trip = self.trip(user=self.sara)
        body = self.post(f"/me/requests/{trip.pk}/close/", {}).json()
        self.assertEqual(body["status"], "CANCELLED")
        package = self.package()
        self.assertEqual(self.post(f"/me/requests/{package.pk}/close/", {}).json()["status"], "CANCELLED")
        self.assert_error(self.post(f"/me/requests/{package.pk}/close/", {}), "not_editable", "/me/requests/{id}/close/")
        self.assert_error(self.post("/me/requests/999999/close/", {}), "not_found", "/me/requests/{id}/close/")

    def test_close_a_trip_as_sent(self) -> None:
        trip = self.trip(user=self.sara)
        body = self.post(f"/me/requests/{trip.pk}/close/", {"package_sent": True}).json()
        self.assertEqual((body["status"], body["package_sent"]), ("COMPLETED", True))

    def test_counterparts_are_cards_with_no_contact(self) -> None:
        mine = self.package(desired_date=days(10))
        self.trip(flight_date=days(10), date_to=days(10))
        response = self.get(f"/me/requests/{mine.pk}/counterparts/")
        self.assertEqual(len(response.json()["items"]), 1)
        self.assertNotIn("omar_secret", response.content.decode())

    def test_counterparts_failure_and_closed(self) -> None:
        mine = self.package()
        with patch("courier_api.views.search.counterparts", side_effect=RuntimeError):
            self.assert_error(self.get(f"/me/requests/{mine.pk}/counterparts/"), "matches_failed", "/me/requests/{id}/counterparts/")
        ItemRequest.objects.filter(pk=mine.pk).update(status=RequestStatus.CLOSED)
        self.assertEqual(self.get(f"/me/requests/{mine.pk}/counterparts/").json(), {"items": []})
        self.assert_error(self.get("/me/requests/999999/counterparts/"), "not_found", "/me/requests/{id}/counterparts/")


class ContactReleaseTests(ApiCase):
    def new_match(self) -> Match:
        return self.connected_match(before_api_opened=False)

    def assert_no_contact_in(self, response) -> None:
        for word in ("omar_secret", "7101", "t.me"):
            self.assertNotIn(word, response.content.decode())

    def test_match_with_no_acceptance_shows_nothing_with_every_switch_off(self) -> None:
        self.assertFalse(CourierSettings.load().courier_intro_everywhere)
        self.assertIsNone(CourierSettings.load().intro_everywhere_since)
        match = self.new_match()
        body = self.assert_error(self.get(f"/me/matches/{match.pk}/"), "contact_not_accepted", "/me/matches/{id}/")
        self.assertIsNone(body["intro_id"])
        self.assert_no_contact_in(self.get(f"/me/matches/{match.pk}/"))
        inbox = self.get("/me/matches/")
        self.assertEqual(inbox.json()["matches"], [])
        self.assert_no_contact_in(inbox)
        self.assertEqual(self.get("/me/").json()["counts"]["open_matches"], 0)
        self.assert_error(self.post(f"/me/matches/{match.pk}/complete/"), "match_not_open", "/me/matches/{id}/complete/")

    def test_the_intro_switch_does_not_change_the_api(self) -> None:
        match = self.new_match()
        opened = CourierSettings.load().courier_api_opened_at
        for on in (False, True):
            CourierSettings.objects.filter(pk=1).update(courier_intro_everywhere=on, intro_everywhere_since=timezone.now() if on else None)
            with self.subTest(on=on):
                Match.objects.filter(pk=match.pk).update(created_at=opened + timedelta(minutes=1))
                self.assert_error(self.get(f"/me/matches/{match.pk}/"), "contact_not_accepted", "/me/matches/{id}/")
                Match.objects.filter(pk=match.pk).update(created_at=opened - timedelta(minutes=1))
                self.assertEqual(self.get(f"/me/matches/{match.pk}/").json()["contact"]["telegram_username"], "omar_secret")

    def test_the_old_paths_keep_todays_contact_for_the_same_match(self) -> None:
        match = self.new_match()
        self.assertEqual(contact_for_match(match, self.sara)["telegram_username"], "omar_secret")

    def test_api_never_stamped_releases_only_an_accepted_intro(self) -> None:
        match = self.new_match()
        CourierSettings.objects.filter(pk=1).update(courier_api_opened_at=None)
        self.assert_error(self.get(f"/me/matches/{match.pk}/"), "contact_not_accepted", "/me/matches/{id}/")
        CourierIntro.objects.create(requester=self.sara, target=self.omar, target_request=match.supply_request, status=IntroStatus.ACCEPTED)
        self.assertEqual(self.get(f"/me/matches/{match.pk}/").json()["contact"]["telegram_username"], "omar_secret")

    def test_pending_or_declined_intro_does_not_release_and_names_the_intro(self) -> None:
        match = self.new_match()
        intro = CourierIntro.objects.create(requester=self.sara, target=self.omar, target_request=match.supply_request)
        body = self.assert_error(self.get(f"/me/matches/{match.pk}/"), "contact_not_accepted", "/me/matches/{id}/")
        self.assertEqual(body["intro_id"], intro.pk)
        CourierIntro.objects.filter(pk=intro.pk).update(status=IntroStatus.DECLINED)
        self.assert_error(self.get(f"/me/matches/{match.pk}/"), "contact_not_accepted", "/me/matches/{id}/")

    def test_accepted_intro_releases_for_both_sides(self) -> None:
        match = self.new_match()
        CourierIntro.objects.create(requester=self.sara, target=self.omar, target_request=match.supply_request, status=IntroStatus.ACCEPTED)
        self.assertEqual(self.get(f"/me/matches/{match.pk}/").json()["contact"]["telegram_username"], "omar_secret")
        self.assertEqual(self.get(f"/me/matches/{match.pk}/", auth=bearer_auth(self.omar)).json()["contact"]["telegram_username"], "sara_secret")
        self.assertEqual(len(self.get("/me/matches/").json()["matches"]), 1)

    def test_match_made_before_the_api_opened_keeps_its_contact(self) -> None:
        match = self.connected_match()
        self.assertEqual(self.get(f"/me/matches/{match.pk}/").json()["contact"]["telegram_username"], "omar_secret")

    def test_opening_stamps_the_first_time_only_and_the_allowlist_counts(self) -> None:
        CourierSettings.objects.filter(pk=1).delete()
        row = CourierSettings.load()
        self.assertIsNone(row.courier_api_opened_at)
        row.courier_v2_allowlist = "  "
        row.save()
        self.assertIsNone(row.courier_api_opened_at)
        row.courier_v2_allowlist = "7001"
        row.save()
        first = row.courier_api_opened_at
        self.assertIsNotNone(first)
        row.courier_v2_allowlist = ""
        row.courier_v2 = True
        row.save()
        self.assertEqual(row.courier_api_opened_at, first)

    def test_the_switch_stamps_its_first_time_only(self) -> None:
        row = CourierSettings.load()
        self.assertIsNone(row.intro_everywhere_since)
        row.courier_intro_everywhere = True
        row.save()
        first = row.intro_everywhere_since
        self.assertIsNotNone(first)
        row.courier_intro_everywhere = False
        row.save()
        row.courier_intro_everywhere = True
        row.save()
        self.assertEqual(row.intro_everywhere_since, first)

    def test_api_post_creates_no_match_and_tells_nobody(self) -> None:
        self.trip(flight_date=days(15), date_to=days(15))
        self.telegram.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post("/me/requests/", {**form(), "type": "DEMAND"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Match.objects.filter(demand_request_id=response.json()["id"]).count(), 0)
        self.telegram.assert_not_called()
        with self.captureOnCommitCallbacks(execute=True):
            self.patch_(f"/me/requests/{response.json()['id']}/", form(size="1"))
        self.assertEqual(Match.objects.filter(demand_request_id=response.json()["id"]).count(), 0)
        self.telegram.assert_not_called()


class AllowlistTesterTests(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        CourierSettings.objects.filter(pk=1).update(courier_v2=False, courier_v2_allowlist="7001", alerts_enabled=True)
        CourierRouteAlert.objects.create(telegram_user_id=7101, chat_id=7101, user=self.omar, role="SUPPLY", origin_country="CA", origin_city="toronto", destination_country="IR", destination_city="tehran")

    @override_settings(TELEGRAM_CHANNEL_ENABLED=True)
    def test_post_reaches_no_channel_no_counterpart_no_alert(self) -> None:
        trip = self.trip(flight_date=days(15), date_to=days(15))
        self.telegram.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post("/me/requests/", {**form(), "type": "DEMAND"})
        self.assertEqual(response.status_code, 201, response.content)
        item = ItemRequest.objects.get(pk=response.json()["id"])
        self.assertEqual(item.unmatchable_reason, UNMATCHABLE_TEST_POST)
        self.telegram.assert_not_called()
        self.assertEqual(Match.objects.filter(demand_request=item).count(), 0)
        with self.captureOnCommitCallbacks(execute=True):
            self.patch_(f"/me/requests/{item.pk}/", form(size="1"))
            self.post(f"/me/requests/{item.pk}/close/", {})
        self.telegram.assert_not_called()
        self.assertEqual(trip.status, RequestStatus.ACTIVE)

    def test_a_real_user_posting_later_never_meets_a_test_post(self) -> None:
        response = self.post("/me/requests/", {**form(when={"kind": "exact", "date": days(15)}), "type": "DEMAND"})
        item = ItemRequest.objects.get(pk=response.json()["id"])
        self.telegram.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            trip = self.trip(flight_date=days(15), date_to=days(15))
        self.assertEqual(Match.objects.filter(supply_request=trip).count(), 0)
        self.assertNotIn(item, search.route_search(role="SUPPLY", origin=("CA", "toronto"), destination=("IR", "tehran")))
        self.assertFalse(public_open_requests_queryset().filter(pk=item.pk).exists())
        self.telegram.assert_not_called()

    def test_a_test_post_cannot_be_read_or_asked_for_by_id(self) -> None:
        CourierSettings.objects.filter(pk=1).update(courier_v2_allowlist="7001 7101")
        item = ItemRequest.objects.get(pk=self.post("/me/requests/", {**form(), "type": "DEMAND"}).json()["id"])
        omar = bearer_auth(self.omar)
        self.telegram.reset_mock()
        self.assert_error(self.get(f"/requests/{item.pk}/", auth=omar), "not_found", "/requests/{id}/")
        self.assertEqual(self.get("/requests/", auth=omar, ids=str(item.pk)).json()["items"], [])
        with self.captureOnCommitCallbacks(execute=True):
            self.assert_error(self.post(f"/requests/{item.pk}/intro/", {}, auth=omar), "not_found", "/requests/{id}/intro/")
        self.assertFalse(CourierIntro.objects.filter(target_request=item).exists())
        self.telegram.assert_not_called()
        self.assert_error(self.get(f"/requests/{item.pk}/"), "own_request", "/requests/{id}/")
        self.assertEqual(self.get(f"/me/requests/{item.pk}/").status_code, 200)

    def test_once_on_for_everyone_a_post_goes_to_the_channel_but_still_matches_nobody(self) -> None:
        CourierSettings.objects.filter(pk=1).update(courier_v2=True)
        with patch("item_requests.services.schedule_channel_sync") as channel:
            response = self.post("/me/requests/", {**form(), "type": "DEMAND"})
        channel.assert_called_once()
        self.assertEqual(ItemRequest.objects.get(pk=response.json()["id"]).unmatchable_reason, "")


class ReadsDoNotWriteTests(ApiCase):
    def test_no_get_writes_or_schedules_anything(self) -> None:
        yesterday = timezone.localdate() - timedelta(days=1)
        match = self.connected_match()
        mine = self.package(desired_date=days(40))
        trip = self.trip(flight_date=days(9), date_to=days(9))
        ItemRequest.objects.filter(pk=mine.pk).update(desired_date=yesterday, date_to=yesterday)
        old = CourierIntro.objects.create(requester=self.sara, target=self.omar, target_request=trip)
        CourierIntro.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=conf.INTRO_EXPIRE_HOURS + 1))
        ticket = self.post("/me/tickets/", {"subject": "OTHER", "message": "x"}).json()["id"]
        CourierSettings.load()
        paths = [
            "/guide/", "/locations/", "/me/", "/me/prefs/", "/me/requests/", "/me/requests/?state=archive", f"/me/requests/{mine.pk}/",
            f"/me/requests/{mine.pk}/counterparts/", f"/me/requests/{match.demand_request_id}/counterparts/", "/me/matches/", "/me/matches/?state=archive",
            f"/me/matches/{match.pk}/", f"/me/intros/{old.pk}/", "/me/alerts/", "/me/tickets/", f"/me/tickets/{ticket}/", f"/requests/{trip.pk}/",
            "/requests/?origin=CA:toronto", "/requests/facets/?origin=CA:toronto",
        ]  # fmt: skip
        for path in paths:
            with self.subTest(path=path):
                with self.captureOnCommitCallbacks() as scheduled, CaptureQueriesContext(connection) as queries:
                    response = self.client.get(BASE + path, **self.auth)
                self.assertEqual(response.status_code, 200, response.content)
                writes = [q["sql"] for q in queries if q["sql"].lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}]
                self.assertEqual(writes, [])
                self.assertEqual(scheduled, [])
        mine.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual((mine.status, old.status), (RequestStatus.ACTIVE, IntroStatus.PENDING))

    def test_reads_still_show_what_is_due_as_expired(self) -> None:
        yesterday = timezone.localdate() - timedelta(days=1)
        mine = self.package()
        ItemRequest.objects.filter(pk=mine.pk).update(desired_date=yesterday, date_to=yesterday)
        self.assertEqual(self.get("/me/requests/").json()["items"], [])
        self.assertEqual([row["id"] for row in self.get("/me/requests/", state="archive").json()["items"]], [mine.pk])
        self.assertEqual(self.get("/me/").json()["counts"]["active_requests"], 0)
        self.assertEqual(self.get(f"/me/requests/{mine.pk}/counterparts/").json(), {"items": []})

    def test_a_due_match_moves_to_the_archive_with_no_contact(self) -> None:
        match = self.connected_match()
        Match.objects.exclude(pk=match.pk).delete()
        yesterday = timezone.localdate() - timedelta(days=1)
        ItemRequest.objects.filter(pk=match.supply_request_id).update(flight_date=yesterday, date_to=yesterday)
        self.assertEqual(self.get("/me/matches/").json()["matches"], [])
        archived = self.get("/me/matches/", state="archive").json()["matches"]
        self.assertEqual(archived[0]["status"], "EXPIRED")
        self.assertIsNone(archived[0]["contact"])
        self.assert_error(self.post(f"/me/matches/{match.pk}/complete/"), "match_not_open", "/me/matches/{id}/complete/")

    def test_a_pending_intro_past_72_hours_reads_expired(self) -> None:
        trip = self.trip()
        intro = CourierIntro.objects.create(requester=self.sara, target=self.omar, target_request=trip)
        CourierIntro.objects.filter(pk=intro.pk).update(created_at=timezone.now() - timedelta(hours=conf.INTRO_EXPIRE_HOURS + 1))
        self.assertEqual(self.get(f"/me/intros/{intro.pk}/").json()["status"], "EXPIRED")
        self.assertEqual(self.get(f"/requests/{trip.pk}/").json()["intro_status"], "EXPIRED")
        self.assertEqual(self.get("/me/matches/").json()["outgoing"], [])
        self.assertEqual([row["id"] for row in self.get("/me/matches/", state="archive").json()["closed"]], [intro.pk])
        self.assertEqual(self.get("/me/", auth=bearer_auth(self.omar)).json()["counts"]["pending_intros"], 0)


class MatchTests(ApiCase):
    def test_connected_match_shows_contact(self) -> None:
        match = self.connected_match()
        body = self.get(f"/me/matches/{match.pk}/").json()
        self.assertEqual(body["contact"]["telegram_username"], "omar_secret")
        self.assertTrue(body["contact"]["draft"])
        self.assertEqual(body["role"], "demand")
        self.assertTrue(body["rating"]["can_complete"])

    def test_not_mine(self) -> None:
        match = self.connected_match()
        stranger = bearer_auth(make_user(telegram_user_id=7106, first_name="Z"))
        self.assert_error(self.get(f"/me/matches/{match.pk}/", auth=stranger), "not_found", "/me/matches/{id}/")

    def test_expired(self) -> None:
        match = self.connected_match(status=MatchStatus.EXPIRED)
        self.assert_error(self.get(f"/me/matches/{match.pk}/"), "match_expired", "/me/matches/{id}/")

    def test_taken_by_the_other_side(self) -> None:
        match = self.connected_match()
        ItemRequest.objects.filter(pk=match.supply_request_id).update(status=RequestStatus.CLOSED)
        self.assert_error(self.get(f"/me/matches/{match.pk}/"), "request_inactive", "/me/matches/{id}/")
        Match.objects.filter(pk=match.pk).update(status=MatchStatus.EXPIRED)
        self.assert_error(self.get(f"/me/matches/{match.pk}/"), "request_inactive", "/me/matches/{id}/")

    def test_date_passed_after_connecting(self) -> None:
        match = self.connected_match()
        yesterday = timezone.localdate() - timedelta(days=1)
        ItemRequest.objects.filter(pk=match.supply_request_id).update(flight_date=yesterday, date_to=yesterday, expires_at=timezone.now() + timedelta(days=3))
        self.assert_error(self.get(f"/me/matches/{match.pk}/"), "match_expired", "/me/matches/{id}/")

    def test_finish_and_rate(self) -> None:
        match = self.connected_match()
        done = self.post(f"/me/matches/{match.pk}/complete/")
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(done.json()["status"], "COMPLETED")
        self.assertEqual(self.post(f"/me/matches/{match.pk}/complete/").status_code, 200)
        self.assertTrue(self.get(f"/me/matches/{match.pk}/").json()["rating"]["can_rate"])
        rated = self.post(f"/me/matches/{match.pk}/rate/", {"score": 5, "comment": "خوب"})
        self.assertEqual(rated.status_code, 201, rated.content)
        self.assertEqual(rated.json()["rating"]["my_score"], 5)
        self.assert_error(self.post(f"/me/matches/{match.pk}/rate/", {"score": 4}), "already_rated", "/me/matches/{id}/rate/")

    def test_rate_refusals(self) -> None:
        match = self.connected_match()
        self.assert_error(self.post(f"/me/matches/{match.pk}/rate/", {"score": 5}), "match_not_open", "/me/matches/{id}/rate/")
        Match.objects.filter(pk=match.pk).update(status=MatchStatus.COMPLETED)
        self.assert_error(self.post(f"/me/matches/{match.pk}/rate/", {"score": 9}), "rating_invalid", "/me/matches/{id}/rate/")
        self.assert_error(self.post(f"/me/matches/{match.pk}/rate/", {"score": 3, "comment": "x" * 501}), "rating_invalid", "/me/matches/{id}/rate/")
        self.assert_error(self.post(f"/me/matches/{match.pk}/rate/", {"comment": "x"}), "validation_failed", "/me/matches/{id}/rate/")
        self.assert_error(self.post("/me/matches/999999/rate/", {"score": 3}), "not_found", "/me/matches/{id}/rate/")
        self.assertEqual(MatchRating.objects.count(), 0)

    def test_complete_refused_when_not_connected(self) -> None:
        match = self.connected_match(status=MatchStatus.EXPIRED)
        self.assert_error(self.post(f"/me/matches/{match.pk}/complete/"), "match_not_open", "/me/matches/{id}/complete/")
        self.assert_error(self.post("/me/matches/999999/complete/"), "not_found", "/me/matches/{id}/complete/")

    def test_inbox_sorts_people_into_sections(self) -> None:
        match = self.connected_match()
        Match.objects.exclude(pk=match.pk).delete()
        asked = self.trip(user=make_user(telegram_user_id=7107, first_name="Out"), flight_date=days(8), date_to=days(8))
        outgoing = self.post(f"/requests/{asked.pk}/intro/").json()["id"]
        asker = make_user(telegram_user_id=7108, first_name="In")
        my_trip = self.trip(user=self.sara, flight_date=days(9), date_to=days(9))
        incoming = self.post(f"/requests/{my_trip.pk}/intro/", auth=bearer_auth(asker)).json()["id"]
        accepted_on = self.trip(user=make_user(telegram_user_id=7109, first_name="Acc"), flight_date=days(11), date_to=days(11))
        accepted = CourierIntro.objects.create(requester=self.sara, target=accepted_on.user, target_request=accepted_on, status=IntroStatus.ACCEPTED)
        declined_on = self.trip(user=make_user(telegram_user_id=7110, first_name="Dec"), flight_date=days(13), date_to=days(13))
        declined = CourierIntro.objects.create(requester=self.sara, target=declined_on.user, target_request=declined_on, status=IntroStatus.DECLINED)
        active = self.get("/me/matches/").json()
        self.assertEqual([row["id"] for row in active["incoming"]], [incoming])
        self.assertEqual([row["id"] for row in active["outgoing"]], [outgoing])
        self.assertEqual([row["id"] for row in active["accepted"]], [accepted.pk])
        self.assertEqual([row["id"] for row in active["matches"]], [match.pk])
        self.assertEqual(active["closed"], [])
        archive = self.get("/me/matches/", state="archive").json()
        self.assertEqual([row["id"] for row in archive["closed"]], [declined.pk])
        self.assertEqual(archive["matches"], [])
        self.assert_error(self.get("/me/matches/", state="nope"), "validation_failed", "/me/matches/")

    def test_inbox_empty(self) -> None:
        self.assertEqual(self.get("/me/matches/").json(), {"incoming": [], "outgoing": [], "accepted": [], "closed": [], "matches": []})


class AlertTests(ApiCase):
    route = {"side": "DEMAND", "origin": "CA:toronto", "destination": "IR:tehran"}

    def test_save_reports_the_sending_switch(self) -> None:
        response = self.post("/me/alerts/", self.route)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["created"])
        self.assertFalse(body["sending_on"])
        self.assertEqual(body["alert"]["origin"]["city"], "toronto")

    def test_same_route_switches_back_on(self) -> None:
        first = self.post("/me/alerts/", self.route).json()["alert"]["id"]
        self.patch_(f"/me/alerts/{first}/", {"active": False})
        again = self.post("/me/alerts/", {**self.route, "size": "3"})
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["alert"]["active"])
        self.assertEqual(again.json()["alert"]["weight_kg"], "3.00")
        self.assertEqual(CourierRouteAlert.objects.count(), 1)

    def test_refusals(self) -> None:
        self.assert_error(self.post("/me/alerts/", {"side": "DEMAND", "origin": "CA:toronto"}), "route_required", "/me/alerts/")
        self.assert_error(self.post("/me/alerts/", {**self.route, "destination": "CA:toronto"}), "same_city", "/me/alerts/")
        self.assert_error(self.post("/me/alerts/", {"origin": "x"}), "validation_failed", "/me/alerts/")
        for index in range(conf.MAX_ALERTS_PER_USER):
            CourierRouteAlert.objects.create(telegram_user_id=7001, chat_id=7001, role="DEMAND", origin_country="CA", origin_city=f"c{index}", destination_country="IR", destination_city="tehran")
        self.assert_error(self.post("/me/alerts/", self.route), "alert_cap", "/me/alerts/")

    def test_month_window(self) -> None:
        key, start, end = texts.upcoming_months(count=2)[1]
        body = self.post("/me/alerts/", {**self.route, "month": key}).json()["alert"]
        self.assertEqual((body["date_from"], body["date_to"]), (start.isoformat(), end.isoformat()))

    def test_list_edit_pause_delete(self) -> None:
        pk = self.post("/me/alerts/", self.route).json()["alert"]["id"]
        listed = self.get("/me/alerts/").json()
        self.assertEqual((len(listed["alerts"]), listed["cap"], listed["sending_on"]), (1, 5, False))
        edited = self.patch_(f"/me/alerts/{pk}/", {"origin": "CA:montreal", "destination": "IR:tehran", "size": "doc"}).json()["alert"]
        self.assertEqual((edited["origin"]["city"], edited["weight_kg"]), ("montreal", "0.50"))
        self.assertFalse(self.patch_(f"/me/alerts/{pk}/", {"active": False}).json()["alert"]["active"])
        self.assertEqual(self.send("delete", f"/me/alerts/{pk}/").status_code, 204)
        self.assert_error(self.send("delete", f"/me/alerts/{pk}/"), "alert_not_found", "/me/alerts/{id}/")

    def test_edit_refusals(self) -> None:
        first = self.post("/me/alerts/", self.route).json()["alert"]["id"]
        second = self.post("/me/alerts/", {**self.route, "origin": "CA:montreal"}).json()["alert"]["id"]
        self.assert_error(self.patch_(f"/me/alerts/{second}/", {"origin": "CA:toronto", "destination": "IR:tehran"}), "alert_exists", "/me/alerts/{id}/")
        self.assert_error(self.patch_(f"/me/alerts/{first}/", {"origin": "CA:toronto"}), "route_required", "/me/alerts/{id}/")
        self.assert_error(self.patch_(f"/me/alerts/{first}/", {"origin": "CA:toronto", "destination": "CA:toronto"}), "same_city", "/me/alerts/{id}/")
        self.assert_error(self.patch_(f"/me/alerts/{first}/", {"origin": "bad"}), "validation_failed", "/me/alerts/{id}/")
        self.assert_error(self.patch_(f"/me/alerts/{first}/", {"active": True}, auth=bearer_auth(self.omar)), "alert_not_found", "/me/alerts/{id}/")

    def test_alerts_never_send_from_the_api(self) -> None:
        self.post("/me/alerts/", self.route)
        self.telegram.assert_not_called()


class AccountTests(ApiCase):
    def test_me_counts_what_waits(self) -> None:
        self.package()
        my_trip = self.trip(user=self.sara, flight_date=days(9), date_to=days(9))
        CourierIntro.objects.create(requester=self.omar, target=self.sara, target_request=my_trip)
        body = self.get("/me/").json()
        self.assertEqual(body["counts"]["active_requests"], 2)
        self.assertEqual(body["counts"]["pending_intros"], 1)
        self.assertEqual(body["first_name"], "Sara")

    def test_counts_can_fail_alone(self) -> None:
        from django.db import DatabaseError

        with patch("courier_api.views.matches_for_user", side_effect=DatabaseError):
            response = self.get("/me/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["counts"])

    def test_prefs_default_then_save_only_what_is_sent(self) -> None:
        self.assertEqual(self.get("/me/prefs/").json()["side"], "")
        self.assertEqual(CourierUserPrefs.objects.count(), 0)
        saved = self.patch_("/me/prefs/", {"side": "SUPPLY", "last_origin": "CA:toronto"})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.patch_("/me/prefs/", {"locale": "en"})
        body = self.get("/me/prefs/").json()
        self.assertEqual((body["side"], body["last_origin"], body["locale"]), ("SUPPLY", "CA:toronto", "en"))

    def test_prefs_refusals(self) -> None:
        self.assert_error(self.patch_("/me/prefs/", {"locale": "de"}), "validation_failed", "/me/prefs/")
        self.assert_error(self.patch_("/me/prefs/", {"last_origin": "toronto"}), "validation_failed", "/me/prefs/")
        self.assert_error(self.patch_("/me/prefs/", {"last_destination": "toronto"}), "validation_failed", "/me/prefs/")


class SupportTests(ApiCase):
    def test_open_reply_close(self) -> None:
        opened = self.post("/me/tickets/", {"subject": "OTHER", "message": "سلام"})
        self.assertEqual(opened.status_code, 201, opened.content)
        pk = opened.json()["id"]
        self.assertEqual(self.get("/me/tickets/").json()["items"][0]["last_message"], "سلام")
        replied = self.post(f"/me/tickets/{pk}/messages/", {"message": "دوباره"})
        self.assertEqual(len(replied.json()["messages"]), 2)
        self.assertEqual(self.get(f"/me/tickets/{pk}/").json()["status"], "OPEN")
        closed = self.post(f"/me/tickets/{pk}/close/")
        self.assertEqual(closed.json()["status"], TicketStatus.CLOSED)
        self.assertEqual(self.post(f"/me/tickets/{pk}/close/").status_code, 200)
        self.assert_error(self.post(f"/me/tickets/{pk}/messages/", {"message": "x"}), "ticket_closed", "/me/tickets/{id}/messages/")

    def test_report_opens_a_report_ticket_only_with_its_switch(self) -> None:
        trip = self.trip()
        self.assert_error(self.post(f"/requests/{trip.pk}/report/", {"reason": "SCAM"}), "not_found", "/requests/{id}/report/")
        CourierSettings.objects.filter(pk=1).update(v4_report=True)
        response = self.post(f"/requests/{trip.pk}/report/", {"reason": "SCAM", "text": "پول خواست"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["subject"], "REPORT_USER")
        self.assertIn(f"#{trip.pk}", body["messages"][0]["message"])
        self.assertIn("پول خواست", body["messages"][0]["message"])
        own = self.package()
        self.assert_error(self.post(f"/requests/{own.pk}/report/", {"reason": "SCAM"}), "not_found", "/requests/{id}/report/")
        self.assert_error(self.post(f"/requests/{trip.pk}/report/", {"reason": "NOPE"}), "validation_failed", "/requests/{id}/report/")

    def test_meta_lists_v4_switches_off(self) -> None:
        features = self.client.get(BASE + "/meta/").json()["features"]
        self.assertEqual(set(features), set(conf.V4_FEATURES))
        self.assertFalse(any(features.values()))

    def test_empty_list(self) -> None:
        self.assertEqual(self.get("/me/tickets/").json(), {"items": []})

    def test_refusals(self) -> None:
        self.assert_error(self.post("/me/tickets/", {"subject": "NOPE", "message": "x"}), "ticket_invalid", "/me/tickets/")
        self.assert_error(self.post("/me/tickets/", {"subject": "OTHER", "message": ""}), "ticket_invalid", "/me/tickets/")
        pk = self.post("/me/tickets/", {"subject": "OTHER", "message": "x"}).json()["id"]
        self.assert_error(self.post(f"/me/tickets/{pk}/messages/", {"message": ""}), "ticket_invalid", "/me/tickets/{id}/messages/")
        other = bearer_auth(self.omar)
        self.assert_error(self.get(f"/me/tickets/{pk}/", auth=other), "not_found", "/me/tickets/{id}/")
        self.assert_error(self.post(f"/me/tickets/{pk}/messages/", {"message": "x"}, auth=other), "not_found", "/me/tickets/{id}/messages/")
        self.assert_error(self.post(f"/me/tickets/{pk}/close/", auth=other), "not_found", "/me/tickets/{id}/close/")


class DeepLinkTests(ApiCase):
    def resolve(self, start: str, auth: dict | None = None):
        return self.post("/deeplink/", {"start": start}, auth=auth)

    def test_screens_and_housing(self) -> None:
        self.assertEqual(self.resolve("").json()["screen"], "courier")
        self.assertEqual(self.resolve("courier_alerts").json()["screen"], "courier_alerts")
        self.assertEqual(self.resolve("home_12").json()["kind"], "housing")

    def test_malformed_and_unknown(self) -> None:
        for start in ("nope", "explore_abc", "match_", "o_short"):
            with self.subTest(start=start):
                self.assert_error(self.resolve(start), "start_invalid", "/deeplink/")
        for start in ("explore_999999", "request_999999", "match_999999", "ticket_999999", "intro_999999"):
            with self.subTest(start=start):
                self.assert_error(self.resolve(start), "not_found", "/deeplink/")

    def test_board_button_opens_exactly_that_board(self) -> None:
        from datetime import date as _date

        from courier.models import CourierDigestPost

        trip, package = self.trip(), self.package()
        CourierDigestPost.objects.create(scope="de", day=_date(2026, 11, 10), payload={"ids": [trip.pk, package.pk]})
        self.assertEqual(
            self.resolve("board_de_20261110").json(),
            {"kind": "travelers", "id": None, "ids": [trip.pk, package.pk], "screen": None, "read_only": True},
        )
        self.assertEqual(self.resolve("board_ca_20261110").json()["screen"], "courier")  # no board that day: the list
        for start in ("board_xx_20261110", "board_de_2026111", "board_de_20261340"):
            with self.subTest(start=start):
                self.assertNotEqual(self.resolve(start).status_code, 500)

    def test_requests(self) -> None:
        other, mine = self.trip(), self.package()
        self.assertEqual(self.resolve(f"explore_{other.pk}").json(), {"kind": "request", "id": other.pk, "ids": [], "screen": None, "read_only": False})
        self.assertEqual(self.resolve(f"explore_{mine.pk}").json()["kind"], "own_request")
        self.assertEqual(self.resolve(f"request_{mine.pk}").json()["kind"], "own_request")
        self.assertEqual(self.resolve(f"request_{other.pk}").json()["kind"], "request")
        ItemRequest.objects.filter(pk=other.pk).update(status=RequestStatus.CLOSED)
        self.assert_error(self.resolve(f"request_{other.pk}"), "not_found", "/deeplink/")
        self.assertEqual(self.resolve(f"explore_{other.pk}").json()["kind"], "request")

    def test_match_ticket_intro_only_for_their_people(self) -> None:
        match = self.connected_match()
        ticket = self.post("/me/tickets/", {"subject": "OTHER", "message": "x"}).json()["id"]
        trip = self.trip(flight_date=days(8), date_to=days(8))
        intro = self.post(f"/requests/{trip.pk}/intro/").json()["id"]
        for start, kind in ((f"match_{match.pk}", "match"), (f"ticket_{ticket}", "ticket"), (f"intro_{intro}", "intro")):
            self.assertEqual(self.resolve(start).json()["kind"], kind)
        stranger = bearer_auth(make_user(telegram_user_id=7111, first_name="Z"))
        for start in (f"match_{match.pk}", f"ticket_{ticket}", f"intro_{intro}"):
            self.assert_error(self.resolve(start, auth=stranger), "not_found", "/deeplink/")

    def _outreach(self, token: str) -> OutreachMessage:
        shadow = make_user(telegram_user_id=SHADOW_ID, first_name="Shadow", telegram_username="sara_secret2")
        demand = create_item_request(shadow, package_payload(), imported=True, source_url="https://t.me/src/2", sync_channel=False)
        return OutreachMessage.objects.create(token=token, recipient=shadow, demand_request=demand)

    def test_outreach_for_someone_else_is_read_only_and_records_nothing(self) -> None:
        message = self._outreach("tokentoken1")
        trip = self.trip()
        Match.objects.get_or_create(demand_request=message.demand_request, supply_request=trip, defaults={"score": 90, "status": MatchStatus.CONNECTED})
        body = self.resolve("o_tokentoken1").json()
        self.assertEqual((body["kind"], body["id"], body["read_only"]), ("request", trip.pk, True))
        message.refresh_from_db()
        self.assertIsNone(message.opened_at)

    def test_outreach_unknown_token(self) -> None:
        self.assert_error(self.resolve("o_unknowntoken"), "not_found", "/deeplink/")

    def test_outreach_for_someone_else_with_no_travelers(self) -> None:
        self._outreach("tokentoken2")
        body = self.resolve("o_tokentoken2").json()
        self.assertEqual((body["kind"], body["screen"], body["read_only"]), ("screen", "courier", True))

    def test_outreach_with_many_travelers(self) -> None:
        message = self._outreach("tokentoken3")
        one, two = self.trip(flight_date=days(5), date_to=days(5)), self.trip(flight_date=days(6), date_to=days(6))
        for trip in (one, two):
            Match.objects.get_or_create(demand_request=message.demand_request, supply_request=trip, defaults={"score": 90, "status": MatchStatus.CONNECTED})
        body = self.resolve("o_tokentoken3").json()
        self.assertEqual((body["kind"], sorted(body["ids"]), body["read_only"]), ("travelers", sorted([one.pk, two.pk]), True))


class DomainTests(ApiCase):
    def test_public_text_masks_contact_and_keeps_words(self) -> None:
        self.assertEqual(search.public_text("سه کیلو لباس"), "سه کیلو لباس")
        self.assertEqual(search.public_text(""), "")
        masked = search.public_text("پیام به @someone یا https://t.me/someone یا tg://user?id=1 یا +1 (416) 555-0100")
        for word in ("someone", "tg://", "555"):
            self.assertNotIn(word, masked)

    def test_public_text_masks_bare_handles_after_announcing_words(self) -> None:
        for text in ("آیدی someone", "ایدی: someone", "تلگرام someone", "telegram someone", "واتس someone", "واتس اپ someone", "واتساپ someone", "whatsapp someone", "id: someone", "someone_12", "someone12", "IG: someone", "ig someone", "insta someone", "instagram: someone", "snap someone", "snapchat: someone", "اینستا someone", "اینستاگرام: someone", "اسنپ someone", "اسنپ چت someone"):
            with self.subTest(text=text):
                self.assertNotIn("someone", search.public_text(text))
        self.assertNotIn("omar", search.public_text("پیام به omar بدهید", ("omar",)))
        self.assertEqual(search.public_text("پرواز از Toronto به Tehran"), "پرواز از Toronto به Tehran")
        self.assertEqual(search.public_text("عکس Instagram نیست"), "عکس Instagram نیست")

    def test_a_request_free_text_never_shows_an_instagram_handle(self) -> None:
        trip = self.trip(description="IG: roomsbyali")
        response = self.get(f"/requests/{trip.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("roomsbyali", response.content.decode())

    def test_a_post_with_no_way_to_reach_is_not_listed(self) -> None:
        none = make_user(telegram_user_id=SHADOW_ID + 1, first_name="X")
        silent = create_item_request(none, trip_payload(), imported=True, sync_channel=False)
        linked = make_user(telegram_user_id=SHADOW_ID + 2, first_name="Y")
        listed = create_item_request(linked, trip_payload(flight_date=days(13), date_to=days(13)), imported=True, source_url="https://t.me/src/3", sync_channel=False)
        ids = [row["id"] for row in self.get("/requests/", origin="CA:toronto", destination="IR:tehran").json()["items"]]
        self.assertNotIn(silent.pk, ids)
        self.assertIn(listed.pk, ids)
        self.assertEqual(self.get("/requests/facets/", origin="CA:toronto", destination="IR:tehran").json()["total"], len(ids))
        self.assert_error(self.post(f"/requests/{silent.pk}/intro/"), "intro_no_public_contact", "/requests/{id}/intro/")

    def test_a_city_with_no_row_still_has_a_name(self) -> None:
        from courier_api.serializers import Names

        self.assertEqual(Names().place("CA", "new-town")["name_en"], "New Town")

    def test_posting_refuses_nothing_the_domain_would_take(self) -> None:
        from courier import posting

        payload = posting.build_payload("DEMAND", form())
        self.assertEqual(payload["destination_cities"], [{"country": "IR", "city": "tehran"}])
        self.assertIsNone(posting.find_duplicate(self.sara, payload))
        with self.assertRaises(posting.PostingError):
            posting.build_payload("SUPPLY", form(when={"kind": "month", "month": "2030-01"}))
        try:
            create_item_request(self.sara, payload, sync_channel=False)
        except ValidationError as exc:  # pragma: no cover
            self.fail(str(exc))
