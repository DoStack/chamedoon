from __future__ import annotations

import io
import re
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import jwt
import yaml
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from home import conf
from home.models import HomeChannel, HomeCityWaitlist, HomeListing, HomeSettings, HomeSource, HomeUserPrefs, ListingStatus
from home.contact_text import clean_place, has_contact, redact_contact
from home.public import public_card, public_listing
from home.review import _summary, apply_review
from home_api.errors import CODES
from home_api.serializers import PublicCardSerializer, PublicListingSerializer
from home_api.views import resolve_start
from matching.contact import SYNTHETIC_TELEGRAM_USER_ID
from miniapp.auth import SESSION_USER_KEY
from tests.helpers import TEST_SECRET, bearer_auth, make_user, readable_text
from tests.test_home import make_post
from tests.test_home_photos import CA_ACCEPT, CA_TEXT_NO_PHONE, ca_source
from tests.test_telegram_auth import TEST_BOT_TOKEN, build_init_data
from users.models import User

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "home_api" / "openapi.yaml"
SCHEMA = yaml.safe_load(SCHEMA_FILE.read_text(encoding="utf-8"))
NOT_RAISED_YET = {"not_served_yet"}  # Nothing answers it now; kept for later phases.
P3_PATHS = {
    "/api/v1/me/account/",
    "/api/v1/me/outreach/landing/",
    "/api/v1/me/outreach/alerts/{id}/confirm/",
    "/api/v1/me/outreach/write-access/",
    "/api/v1/me/alerts/",
    "/api/v1/me/alerts/{id}/",
    "/api/v1/me/alerts/{id}/listings/",
    "/api/v1/me/alerts/{id}/viewed/",
    "/api/v1/me/notifications/",
    "/api/v1/me/notifications/read/",
    "/api/v1/me/listings/",
    "/api/v1/me/listings/{id}/form/",
    "/api/v1/me/listings/{id}/status/",
    "/api/v1/me/photos/",
    "/api/v1/me/photos/{photo_id}/",
    "/api/v1/me/posts/parse/",
}
CONTACT_WORDS = ("contact_", "source_url", "owner_handle", "author", "post_text", "ca_user", "647", "RentToronto")


class ReadableTextTests(SimpleTestCase):
    def test_a_contact_in_any_field_is_seen(self) -> None:
        for body in ({"note": "call 647-555-0142"}, {"a": [{"b": "ca_user1"}]}, {"contact_phone": ""}, {"source_url": None}, {"id": 6475550142}, {"phone": "6475550142"}):
            with self.subTest(body=body):
                self.assertRegex(readable_text(body), "647|ca_user|contact_|source_url")

    def test_digits_in_a_date_or_a_hash_or_a_short_number_mean_nothing(self) -> None:
        body = {"first_seen": "2026-11-03T16:47:12.647000+00:00", "available_from": "2026-06-47", "version": "ab0647cd", "url": "/feeds/ca-toronto.0647abcd.json", "id": 647}
        self.assertNotIn("647", readable_text(body))


def _operation(path_pattern: str, method: str) -> dict:
    return SCHEMA["paths"][path_pattern][method.lower()]


@override_settings(SECRET_KEY=TEST_SECRET, TELEGRAM_BOT_TOKEN=TEST_BOT_TOKEN, DEBUG=False)
class ApiCase(TestCase):
    def setUp(self) -> None:
        cache.clear()
        HomeSource.objects.all().delete()
        HomeChannel.objects.all().delete()
        self.source = ca_source()
        self.channel = HomeChannel.objects.create(country="CA", city="Toronto", aliases="North York", username="chamedoon_toronto", title="Toronto")
        self.country_channel = HomeChannel.objects.create(country="CA", city="", username="chamedoon_ca", title="Canada")
        self.user = make_user(telegram_user_id=880001, first_name="Sara")
        self.set_flag(True)

    def set_flag(self, on: bool, allowlist: str = "") -> None:
        row = HomeSettings.load()
        row.miniapp_v2 = on
        row.miniapp_v2_allowlist = allowlist
        row.save()

    def listing(self, number: int = 1, **data) -> HomeListing:
        data.setdefault("contact_telegram", f"ca_user{number}")
        post = make_post(
            self.source,
            number,
            text=f"{CA_TEXT_NO_PHONE} {number} @{data['contact_telegram']}" if data["contact_telegram"] else f"{CA_TEXT_NO_PHONE} {number}",
            posted_at=timezone.now() - timedelta(hours=2, minutes=number),
            source_url=f"https://t.me/{self.source.username}/{number}",
        )
        apply_review(post, {**CA_ACCEPT, **data})
        return HomeListing.objects.get(post=post)

    def assert_error(self, response, code: str, path_pattern: str) -> dict:
        """The body is exactly the contract, and the contract lists this code for this operation and status."""
        method = response.request["REQUEST_METHOD"]
        self.assertEqual(response.status_code, CODES[code][0], response.content)
        self.assertEqual(response["Content-Type"], "application/json")
        body = response.json()
        self.assertEqual(body["code"], code)
        self.assertIsInstance(body["detail"], str)
        operation = _operation(path_pattern, method)
        self.assertIn(code, operation["x-error-codes"])
        enum = operation["responses"][str(response.status_code)]["content"]["application/json"]["schema"]["properties"]["code"]["enum"]
        self.assertIn(code, enum)
        return body


class ExchangeTests(ApiCase):
    url = "/api/v1/auth/exchange/"
    pattern = "/api/v1/auth/exchange/"

    def post(self, **body):
        return self.client.post(self.url, body, content_type="application/json")

    def test_exchange_returns_token_prefs_and_signs_the_old_pages_in(self) -> None:
        response = self.post(init_data=build_init_data(telegram_user_id=770001, first_name="Nima", username="nima"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"]["telegram_username"], "nima")
        self.assertEqual(body["prefs"]["locale"], "fa")
        self.assertTrue(body["miniapp_v2"])
        self.assertEqual(jwt.decode(body["access_token"], TEST_SECRET, algorithms=["HS256"])["telegram_user_id"], 770001)
        self.assertIn("sessionid", response.cookies)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_retry_gives_the_same_user(self) -> None:
        data = build_init_data(telegram_user_id=770002, first_name="Nima")
        first = self.post(init_data=data).json()["user"]["id"]
        self.assertEqual(self.post(init_data=data).json()["user"]["id"], first)

    def test_first_src_is_stored_once_and_a_bad_one_is_ignored(self) -> None:
        data = build_init_data(telegram_user_id=770003, first_name="Nima")
        self.post(init_data=data, src="NOT VALID!")
        self.assertFalse(HomeUserPrefs.objects.filter(user__telegram_user_id=770003).exists())
        self.assertEqual(self.post(init_data=data, src="chtor").json()["prefs"]["first_src"], "chtor")
        self.assertEqual(self.post(init_data=data, src="chmon").json()["prefs"]["first_src"], "chtor")

    def test_allowlist_opens_the_new_app_for_one_person(self) -> None:
        self.set_flag(False, "770004, 770005")
        self.assertTrue(self.post(init_data=build_init_data(telegram_user_id=770004, first_name="A")).json()["miniapp_v2"])
        self.assert_error(self.post(init_data=build_init_data(telegram_user_id=770006, first_name="B")), "miniapp_off", self.pattern)
        self.set_flag(True)
        self.assertTrue(conf.miniapp_v2_for(770006))

    def test_flag_off_exchange_leaves_nothing_behind(self) -> None:
        self.set_flag(False)
        response = self.post(init_data=build_init_data(telegram_user_id=770007, first_name="C"))
        self.assert_error(response, "miniapp_off", self.pattern)
        self.assertNotIn("access_token", response.json())
        self.assertNotIn("sessionid", response.cookies)
        self.assertFalse(User.objects.filter(telegram_user_id=770007).exists())
        self.assertFalse(HomeUserPrefs.objects.filter(user__telegram_user_id=770007).exists())

    def test_flag_off_exchange_still_records_the_first_src_and_nothing_else(self) -> None:
        self.set_flag(False)
        data = build_init_data(telegram_user_id=770010, first_name="E")
        response = self.post(init_data=data, src="chtor")
        self.assert_error(response, "miniapp_off", self.pattern)
        self.assertNotIn("access_token", response.json())
        self.assertNotIn("sessionid", response.cookies)
        self.assertEqual(HomeUserPrefs.objects.get(user__telegram_user_id=770010).first_src, "chtor")
        self.post(init_data=data, src="chmon")
        self.assertEqual(HomeUserPrefs.objects.get(user__telegram_user_id=770010).first_src, "chtor")

    def test_blocked_comes_before_the_flag(self) -> None:
        self.set_flag(False)
        self.user.is_active = False
        self.user.save()
        self.assert_error(self.post(init_data=build_init_data(telegram_user_id=880001, first_name="Sara")), "user_blocked", self.pattern)

    def test_auth_outside_when_init_data_is_missing_or_blank(self) -> None:
        self.assert_error(self.post(), "auth_outside", self.pattern)
        self.assert_error(self.post(init_data="   "), "auth_outside", self.pattern)

    def test_auth_invalid_for_a_bad_signature_or_junk(self) -> None:
        self.assert_error(self.post(init_data=build_init_data(telegram_user_id=7, first_name="A", tamper_hash=True)), "auth_invalid", self.pattern)
        self.assert_error(self.post(init_data="not-init-data"), "auth_invalid", self.pattern)

    def test_session_expired_for_init_data_older_than_a_day(self) -> None:
        old = build_init_data(telegram_user_id=7, first_name="A", auth_date=int(time.time()) - 90000)
        self.assert_error(self.post(init_data=old), "session_expired", self.pattern)

    def test_user_blocked(self) -> None:
        make_user(telegram_user_id=770007, first_name="B", is_active=False)
        self.assert_error(self.post(init_data=build_init_data(telegram_user_id=770007, first_name="B")), "user_blocked", self.pattern)

    def test_a_blocked_user_changes_nothing(self) -> None:
        blocked = make_user(telegram_user_id=770008, first_name="Old", telegram_username="old_name", is_active=False)
        shadow = make_user(telegram_user_id=SYNTHETIC_TELEGRAM_USER_ID + 8, first_name="Shadow", telegram_username="new_name")
        data = build_init_data(telegram_user_id=770008, first_name="New", username="new_name")
        self.assert_error(self.post(init_data=data), "user_blocked", self.pattern)
        blocked.refresh_from_db()
        self.assertEqual((blocked.first_name, blocked.telegram_username), ("Old", "old_name"))
        self.assertTrue(User.objects.filter(pk=shadow.pk).exists())
        with patch("home_api.views.upsert_telegram_user") as upsert:
            self.post(init_data=data)
        upsert.assert_not_called()

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_auth_unavailable_without_a_bot_token(self) -> None:
        with patch("home.conf.bot_token", return_value=""):
            self.assert_error(self.post(init_data="a=b"), "auth_unavailable", self.pattern)

    def test_bad_request_on_a_body_that_is_not_json(self) -> None:
        self.assert_error(self.client.post(self.url, "{not json", content_type="application/json"), "bad_request", self.pattern)
        self.assert_error(self.client.post(self.url, "a=b", content_type="text/plain"), "bad_request", self.pattern)

    def test_validation_failed_names_the_field(self) -> None:
        body = self.assert_error(self.post(init_data="x" * 9000), "validation_failed", self.pattern)
        self.assertEqual(body["fields"], {"init_data": ["max_length"]})

    def test_rate_limited_has_retry_after(self) -> None:
        with patch("home_api.views.ExchangeThrottle.rate", "1/min"), patch("home_api.views.ExchangeThrottle.num_requests", 1, create=True):
            self.post()
            response = self.post()
        self.assert_error(response, "rate_limited", self.pattern)
        self.assertTrue(response["Retry-After"])

    def test_each_client_has_its_own_bucket_and_cannot_pick_one(self) -> None:
        def attempt(**extra):
            return self.client.post(self.url, {}, content_type="application/json", **extra)

        with patch("home_api.views.ExchangeThrottle.rate", "1/min"):
            attempt(REMOTE_ADDR="10.0.0.1")
            self.assertEqual(attempt(REMOTE_ADDR="10.0.0.1").status_code, 429)
            self.assertEqual(attempt(REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="9.9.9.9").status_code, 429)
            self.assertEqual(attempt(REMOTE_ADDR="10.0.0.2").status_code, 400)

    def test_method_not_allowed(self) -> None:
        self.assertEqual(self.client.get(self.url).json()["code"], "method_not_allowed")


@override_settings(SECRET_KEY=TEST_SECRET, TELEGRAM_BOT_TOKEN=TEST_BOT_TOKEN, DEBUG=False)
class OldLoginBlockedTests(TestCase):
    def setUp(self) -> None:
        self.blocked = make_user(telegram_user_id=770020, first_name="Old", telegram_username="old_name", is_active=False)
        self.data = build_init_data(telegram_user_id=770020, first_name="New", username="new_name")

    def assert_nothing_changed(self) -> None:
        self.blocked.refresh_from_db()
        self.assertEqual((self.blocked.first_name, self.blocked.telegram_username), ("Old", "old_name"))

    def test_old_api_answers_the_same_and_writes_nothing(self) -> None:
        response = self.client.post("/api/auth/telegram/", {"init_data": self.data}, content_type="application/json")
        self.assertEqual((response.status_code, response.json()), (403, {"detail": "User is deactivated"}))
        self.assert_nothing_changed()

    def test_old_login_page_writes_nothing(self) -> None:
        self.client.post("/app/login/", {"init_data": self.data})
        self.assert_nothing_changed()
        self.assertNotIn(SESSION_USER_KEY, self.client.session)


class MeFlagTests(ApiCase):
    def test_me_refuses_a_valid_token_once_the_flag_is_off(self) -> None:
        self.set_flag(False)
        for method, url in (("get", "/api/v1/me/prefs/"), ("post", "/api/v1/me/waitlist/"), ("get", "/api/v1/me/listings/1/"), ("post", "/api/v1/me/listings/1/contact/"), ("post", "/api/v1/me/listings/1/availability/")):
            response = getattr(self.client, method)(url, **bearer_auth(self.user))
            self.assertEqual((response.status_code, response.json()["code"]), (403, "miniapp_off"), url)

    def test_me_answers_for_the_allowlist(self) -> None:
        self.set_flag(False, "880001")
        self.assertEqual(self.client.get("/api/v1/me/prefs/", **bearer_auth(self.user)).status_code, 200)

    def test_no_token_is_still_auth_required(self) -> None:
        self.set_flag(False)
        self.assertEqual(self.client.get("/api/v1/me/prefs/").json()["code"], "auth_required")

    def test_old_login_still_works_with_the_flag_off(self) -> None:
        self.set_flag(False)
        self.assertEqual(self.client.post("/app/login/", {"init_data": build_init_data(telegram_user_id=770009, first_name="D")}).status_code, 302)
        self.assertIn(SESSION_USER_KEY, self.client.session)


class AuthTests(ApiCase):
    pattern = "/api/v1/me/prefs/"
    url = "/api/v1/me/prefs/"

    def test_auth_required_without_a_token(self) -> None:
        self.assert_error(self.client.get(self.url), "auth_required", self.pattern)

    def test_auth_invalid_for_a_bad_token_or_header(self) -> None:
        self.assert_error(self.client.get(self.url, HTTP_AUTHORIZATION="Bearer nope"), "auth_invalid", self.pattern)
        self.assert_error(self.client.get(self.url, HTTP_AUTHORIZATION="Basic nope"), "auth_invalid", self.pattern)

    def test_auth_invalid_for_a_deleted_user(self) -> None:
        header = bearer_auth(self.user)["HTTP_AUTHORIZATION"]
        self.user.delete()
        self.assert_error(self.client.get(self.url, HTTP_AUTHORIZATION=header), "auth_invalid", self.pattern)

    def test_session_expired_for_an_old_token(self) -> None:
        with override_settings(JWT_ACCESS_TOKEN_HOURS=-1):
            header = bearer_auth(self.user)["HTTP_AUTHORIZATION"]
        self.assert_error(self.client.get(self.url, HTTP_AUTHORIZATION=header), "session_expired", self.pattern)

    def test_user_blocked_mid_session(self) -> None:
        header = bearer_auth(self.user)["HTTP_AUTHORIZATION"]
        self.user.is_active = False
        self.user.save()
        self.assert_error(self.client.get(self.url, HTTP_AUTHORIZATION=header), "user_blocked", self.pattern)

    def test_old_api_keeps_its_answers(self) -> None:
        response = self.client.get("/api/me/", HTTP_AUTHORIZATION="Bearer nope")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid token"})
        self.user.is_active = False
        self.user.save()
        response = self.client.get("/api/me/", **bearer_auth(self.user))
        self.assertEqual(response.json(), {"detail": "Invalid token"})


class PrefsTests(ApiCase):
    url = "/api/v1/me/prefs/"
    pattern = "/api/v1/me/prefs/"

    def patch_prefs(self, **body):
        return self.client.patch(self.url, body, content_type="application/json", **bearer_auth(self.user))

    def test_read_never_writes(self) -> None:
        response = self.client.get(self.url, **bearer_auth(self.user))
        self.assertEqual(response.json()["theme"], "auto")
        self.assertFalse(response.json()["city_active"])
        self.assertFalse(HomeUserPrefs.objects.exists())

    def test_saves_only_what_is_sent_and_retry_is_safe(self) -> None:
        for _ in range(2):
            response = self.patch_prefs(country="ca", city="Toronto", theme="dark")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(HomeUserPrefs.objects.count(), 1)
        body = response.json()
        self.assertEqual((body["country"], body["city"], body["theme"], body["locale"]), ("CA", "Toronto", "dark", "fa"))
        self.assertTrue(body["city_active"])
        self.assertEqual(self.patch_prefs(locale="de").json()["city"], "Toronto")

    def test_city_stops_being_active_when_its_channel_is_off(self) -> None:
        self.patch_prefs(country="CA", city="Toronto")
        self.channel.enabled = False
        self.channel.save()
        self.assertFalse(self.client.get(self.url, **bearer_auth(self.user)).json()["city_active"])

    def test_first_src_cannot_be_written_here(self) -> None:
        self.assertEqual(self.patch_prefs(first_src="chtor").json()["first_src"], "")

    def test_validation_failed_per_field(self) -> None:
        for body, field, code in (
            ({"locale": "xx"}, "locale", "invalid_choice"),
            ({"theme": "pink"}, "theme", "invalid_choice"),
            ({"country": "ZZ"}, "country", "invalid_choice"),
            ({"quiet_from": "23:00"}, "quiet_to", "quiet_incomplete"),
            ({"quiet_from": "23:00", "quiet_to": "23:00"}, "quiet_to", "quiet_same"),
        ):
            with self.subTest(field=field, code=code):
                got = self.assert_error(self.patch_prefs(**body), "validation_failed", self.pattern)
                self.assertEqual(got["fields"], {field: [code]})

    def test_rate_limited(self) -> None:
        with patch("home_api.views.WriteThrottle.rate", "1/min"):
            self.patch_prefs(theme="dark")
            self.assert_error(self.patch_prefs(theme="dark"), "rate_limited", self.pattern)


class WaitlistTests(ApiCase):
    url = "/api/v1/me/waitlist/"
    pattern = "/api/v1/me/waitlist/"

    def join(self, **body):
        return self.client.post(self.url, body, content_type="application/json", **bearer_auth(self.user))

    def test_join_once_and_already_on_it(self) -> None:
        first = self.join(country="CA", city_name="Ottawa")
        self.assertEqual((first.status_code, first.json()["created"]), (201, True))
        second = self.join(country="ca", city_name=" ottawa ")
        self.assertEqual((second.status_code, second.json()["created"]), (200, False))
        self.assertEqual(HomeCityWaitlist.objects.count(), 1)

    def test_a_listed_city_or_empty_name_is_a_field_error(self) -> None:
        got = self.assert_error(self.join(country="CA", city_name="Toronto"), "validation_failed", self.pattern)
        self.assertEqual(got["fields"], {"city_name": ["city_listed"]})
        got = self.assert_error(self.join(country="CA", city_name="  "), "validation_failed", self.pattern)
        self.assertEqual(got["fields"], {"city_name": ["blank"]})


class ListingDetailTests(ApiCase):
    pattern = "/api/v1/public/listings/{id}/"

    def get(self, pk: int):
        return self.client.get(f"/api/v1/public/listings/{pk}/")

    def test_active_listing_is_cacheable_with_no_session_cookie_or_vary(self) -> None:
        listing = self.listing()
        response = self.get(listing.pk)
        self.assertEqual(response.status_code, 200)
        self.assertIn("s-maxage=120", response["Cache-Control"])
        self.assertEqual(len(response.cookies), 0)
        self.assertNotIn("Set-Cookie", response)
        self.assertNotIn("Cookie", response.get("Vary", ""))
        body = response.json()
        self.assertEqual((body["state"], body["closed_reason"], body["metro"], body["metro_slug"]), ("active", None, "Toronto", "toronto"))
        self.assertEqual(body["channel"], {"username": "chamedoon_toronto", "title": "Toronto", "city": "Toronto"})

    def test_body_holds_no_contact_or_source(self) -> None:
        listing = self.listing(contact_telegram="ca_user1", contact_phone="647-555-0142")
        text = readable_text(self.get(listing.pk).json())
        for word in CONTACT_WORDS:
            with self.subTest(word=word):
                self.assertNotIn(word, text)
        body = self.get(listing.pk).json()
        self.assertTrue(body["has_telegram"] and body["has_phone"] and body["has_source"])

    def test_shape_matches_the_contract(self) -> None:
        listing = self.listing()
        self.assertEqual(set(public_listing(listing)), set(PublicListingSerializer().fields))
        self.assertEqual(set(public_card(listing)), set(PublicCardSerializer().fields))

    def test_card_thumbs_hold_the_first_photos_and_no_more(self) -> None:
        photos = [{"full": f"https://m.test/f{i}.jpg", "thumb": f"https://m.test/t{i}.jpg"} for i in range(8)]
        with patch("home.public.photos_for", return_value=photos):
            card = public_card(self.listing())
        self.assertEqual(card["thumbs"], [f"https://m.test/t{i}.jpg" for i in range(6)])
        self.assertEqual((card["thumb"], card["photo_count"]), (card["thumbs"][0], 8))

    def test_a_card_without_photos_has_no_thumbs(self) -> None:
        self.assertEqual(public_card(self.listing())["thumbs"], [])

    def test_missing_fields_stay_null_not_absent(self) -> None:
        listing = self.listing()
        HomeListing.objects.filter(pk=listing.pk).update(price_amount=None, area="", home_area=None, available_from=None, summary_fa="")
        body = self.get(listing.pk).json()
        self.assertIsNone(body["price_amount"])
        self.assertIsNone(body["price_monthly"])
        self.assertEqual((body["area"], body["thumb"], body["photos"], body["photo_count"]), ("", "", [], 0))
        self.assertIsNone(body["available_from"])

    def test_week_and_night_prices_carry_period_and_a_monthly_value(self) -> None:
        listing = self.listing(price_amount=90, price_period="night")
        body = self.get(listing.pk).json()
        self.assertEqual((body["price_amount"], body["price_period"], body["price_monthly"]), (90.0, "night", 2700.0))

    def test_summary_is_redacted(self) -> None:
        listing = self.listing()
        HomeListing.objects.filter(pk=listing.pk).update(summary_fa="تماس @someone یا +49 176 12345678 یا 0176 1234567، ۳ اتاق https://t.me/x")
        summary = self.get(listing.pk).json()["summary_fa"]
        for leaked in ("@someone", "176", "t.me"):
            self.assertNotIn(leaked, summary)
        self.assertIn("۳ اتاق", summary)

    def test_the_listings_own_handle_never_shows_in_any_text(self) -> None:
        listing = self.listing(contact_telegram="roomsbyali")
        HomeListing.objects.filter(pk=listing.pk).update(summary_fa="اتاق خوب، پیام به roomsbyali در نورث یورک", summary_en="Nice room, ask ROOMSBYALI", area="roomsbyali")
        text = self.get(listing.pk).content.decode().lower()
        self.assertNotIn("roomsbyali", text)

    def test_a_contact_in_the_area_or_city_drops_the_whole_field(self) -> None:
        listing = self.listing()
        HomeListing.objects.filter(pk=listing.pk).update(area="Yonge wa.me/14165550142", city="call 416-555-0142")
        body = self.get(listing.pk).json()
        self.assertEqual(body["city"], "")
        self.assertNotIn("wa.me", body["area"])
        HomeListing.objects.filter(pk=listing.pk).update(home_area=None)
        self.assertEqual(public_card(HomeListing.objects.get(pk=listing.pk))["area"], "")

    def test_a_clean_area_stays_whole(self) -> None:
        self.assertEqual(self.get(self.listing(area="Yonge & Finch").pk).json()["area"], "نورث یورک · Yonge & Finch")

    def test_no_source_post_says_so(self) -> None:
        listing = self.listing()
        for url in ("", "https://t.me/c/123/45"):
            type(listing.post).objects.filter(pk=listing.post_id).update(source_url=url)
            self.assertFalse(self.get(listing.pk).json()["has_source"])

    def test_closed_says_who_closed_it(self) -> None:
        listing = self.listing()
        HomeListing.objects.filter(pk=listing.pk).update(status=ListingStatus.EXPIRED)
        self.assertEqual(self.get(listing.pk).json()["closed_reason"], "expired")
        HomeListing.objects.filter(pk=listing.pk).update(status=ListingStatus.RENTED)
        body = self.get(listing.pk).json()
        self.assertEqual((body["state"], body["closed_reason"]), ("closed", "owner"))

    def test_merged_listing_shows_the_one_it_became(self) -> None:
        keeper, merged = self.listing(1), self.listing(2)
        HomeListing.objects.filter(pk=keeper.pk).update(status=ListingStatus.ACTIVE, canonical=None)
        HomeListing.objects.filter(pk=merged.pk).update(status=ListingStatus.DUPLICATE, canonical=keeper)
        self.assertEqual(self.get(merged.pk).json()["id"], keeper.pk)
        HomeListing.objects.filter(pk=merged.pk).update(canonical=None)
        self.assert_error(self.get(merged.pk), "listing_gone", self.pattern)

    def test_listing_gone_unknown_reported_or_in_review(self) -> None:
        self.assert_error(self.get(999999), "listing_gone", self.pattern)
        reported, review = self.listing(1), self.listing(2)
        HomeListing.objects.filter(pk=reported.pk).update(status=ListingStatus.REPORTED, published_at=timezone.now())
        HomeListing.objects.filter(pk=review.pk).update(needs_review=True)
        response = self.get(reported.pk)
        self.assert_error(response, "listing_gone", self.pattern)
        self.assertIn("s-maxage=30", response["Cache-Control"])
        self.assert_error(self.get(review.pk), "listing_gone", self.pattern)

    def test_outside_every_channel_uses_the_country_channel_or_none(self) -> None:
        listing = self.listing(city="Ottawa", area="")
        self.assertEqual(self.get(listing.pk).json()["channel"]["username"], "chamedoon_ca")
        self.assertEqual(self.get(listing.pk).json()["metro"], "")
        self.country_channel.delete()
        self.assertIsNone(self.get(listing.pk).json()["channel"])

    def test_server_error_is_json_with_a_code(self) -> None:
        with patch("home_api.views.visible_listing", side_effect=RuntimeError("boom")):
            body = self.assert_error(self.get(1), "server_error", self.pattern)
        self.assertNotIn("boom", body["detail"])


class ViewerTests(ApiCase):
    pattern = "/api/v1/me/listings/{id}/"

    def get(self, pk: int, user=None):
        return self.client.get(f"/api/v1/me/listings/{pk}/", **bearer_auth(user or self.user))

    def own(self, listing: HomeListing) -> None:
        # Ownership of a crawled post is what Telegram verified about its poster (home.ownership).
        type(listing.post).objects.filter(pk=listing.post_id).update(author_id=self.user.telegram_user_id, poster_user_id=self.user.telegram_user_id, poster_kind="person", poster_via="sender", poster_read_at=timezone.now())

    def test_says_who_owns_it(self) -> None:
        listing = self.listing()
        self.assertFalse(self.get(listing.pk).json()["is_owner"])
        self.own(listing)
        body = self.get(listing.pk).json()
        self.assertEqual((body["is_owner"], body["review_state"]), (True, "live"))
        self.assertEqual(self.client.get(f"/api/v1/me/listings/{listing.pk}/", **bearer_auth(self.user))["Cache-Control"], "no-store")

    def test_owner_opens_a_hidden_listing_and_nobody_else_does(self) -> None:
        listing = self.listing()
        HomeListing.objects.filter(pk=listing.pk).update(needs_review=True)
        self.assert_error(self.get(listing.pk), "listing_gone", self.pattern)
        self.own(listing)
        body = self.get(listing.pk).json()
        self.assertEqual((body["review_state"], body["listing"]["state"]), ("in_review", "active"))
        HomeListing.objects.filter(pk=listing.pk).update(needs_review=False, status=ListingStatus.REPORTED)
        self.assertEqual(self.get(listing.pk).json()["review_state"], "hidden")

    def test_unknown_listing(self) -> None:
        self.assert_error(self.get(999999), "listing_gone", self.pattern)


class MetaTests(ApiCase):
    url = "/api/v1/public/meta/"

    def test_all_flags_default_off(self) -> None:
        self.set_flag(False)
        response = self.client.get(self.url)
        body = response.json()
        self.assertIn("s-maxage=30", response["Cache-Control"])
        self.assertEqual(
            (body["miniapp_v2"], body["contact_gate_on"], body["snapshots_on"], body["alerts_on"], body["maintenance"]["on"]),
            (False, False, False, False, False),
        )
        self.assertEqual(body["api_version"], 1)
        self.assertEqual(len(response.cookies), 0)

    def test_maintenance_closes_everything_except_meta(self) -> None:
        until = timezone.now() + timedelta(minutes=30)
        HomeSettings.objects.update_or_create(pk=1, defaults={"maintenance_on": True, "maintenance_until": until})
        self.assertTrue(self.client.get(self.url).json()["maintenance"]["on"])
        self.assertTrue(self.client.get(self.url).json()["maintenance"]["until"])
        self.assert_error(self.client.get("/api/v1/public/listings/1/"), "maintenance", "/api/v1/public/listings/{id}/")
        self.assert_error(self.client.post("/api/v1/auth/exchange/", {}, content_type="application/json"), "maintenance", "/api/v1/auth/exchange/")
        self.assert_error(self.client.get("/api/v1/me/prefs/", **bearer_auth(self.user)), "maintenance", "/api/v1/me/prefs/")


class CitiesTests(ApiCase):
    def test_lists_enabled_channels_with_counts(self) -> None:
        self.listing()
        HomeChannel.objects.create(country="CA", city="Hidden", username="hidden", enabled=False)
        response = self.client.get("/api/v1/public/cities/")
        self.assertIn("s-maxage=300", response["Cache-Control"])
        country = response.json()["countries"][0]
        self.assertEqual((country["code"], country["channel"]["username"]), ("CA", "chamedoon_ca"))
        self.assertEqual([(row["slug"], row["listing_count"]) for row in country["cities"]], [("toronto", 1)])


class DeepLinkTests(ApiCase):
    def resolve(self, value: str) -> dict:
        response = self.client.get("/api/v1/public/deeplink/", {"startapp": value})
        self.assertEqual(response.status_code, 200)
        self.assertIn("s-maxage=60", response["Cache-Control"])
        return response.json()

    def test_empty_and_plain_home_are_a_normal_open(self) -> None:
        for value in ("", "home"):
            body = self.resolve(value)
            self.assertEqual((body["kind"], body["malformed"]), ("landing", False))

    def test_listing_links_old_and_new(self) -> None:
        listing = self.listing()
        for value in (f"home_{listing.pk}", f"home_ca_toronto_l{listing.pk}_src-chtor"):
            with self.subTest(value=value):
                body = self.resolve(value)
                self.assertEqual((body["kind"], body["listing_id"], body["listing_state"]), ("listing", listing.pk, "active"))
                self.assertEqual((body["country"], body["city"], body["city_known"]), ("CA", "Toronto", True))
        self.assertEqual(self.resolve(f"home_ca_toronto_l{listing.pk}_src-chtor")["src"], "chtor")

    def test_listing_from_another_city_uses_the_listings_city(self) -> None:
        listing = self.listing()
        HomeChannel.objects.create(country="CA", city="Montreal", username="mtl")
        body = self.resolve(f"home_ca_montreal_l{listing.pk}")
        self.assertEqual((body["city"], body["listing_state"]), ("Toronto", "active"))

    def test_gone_closed_and_merged_listings(self) -> None:
        self.assertEqual(self.resolve("home_ca_toronto_l999999")["listing_state"], "gone")
        self.assertEqual(self.resolve("home_ca_toronto_l999999")["country"], "CA")
        closed, merged = self.listing(1), self.listing(2)
        HomeListing.objects.filter(pk=closed.pk).update(status=ListingStatus.RENTED)
        self.assertEqual(self.resolve(f"home_{closed.pk}")["listing_state"], "closed")
        HomeListing.objects.filter(pk=merged.pk).update(status=ListingStatus.DUPLICATE, canonical=closed)
        self.assertEqual(self.resolve(f"home_{merged.pk}")["listing_id"], closed.pk)

    def test_city_country_and_filter_links(self) -> None:
        body = self.resolve("home_ca_toronto_t-room_p800-1200_src-chtor")
        self.assertEqual((body["kind"], body["city"], body["city_known"]), ("city", "Toronto", True))
        self.assertEqual(body["filters"], {"types": ["room"], "min": 800, "max": 1200})
        self.assertEqual(self.resolve("home_ca")["kind"], "country")

    def test_city_without_a_channel_asks_again(self) -> None:
        body = self.resolve("home_ca_ottawa")
        self.assertEqual((body["kind"], body["city_known"], body["city"]), ("city", False, ""))

    def test_a_share_link_keeps_its_tag(self) -> None:
        listing = self.listing()
        body = self.resolve(f"home_ca_toronto_l{listing.pk}_src-share")
        self.assertEqual((body["listing_id"], body["src"]), (listing.pk, "share"))

    def test_a_bad_src_keeps_the_listing(self) -> None:
        listing = self.listing()
        body = self.resolve(f"home_ca_toronto_l{listing.pk}_src-a!b")
        self.assertEqual((body["listing_id"], body["src"], body["malformed"]), (listing.pk, "", False))

    def test_an_unknown_type_is_dropped(self) -> None:
        self.assertEqual(self.resolve("home_ca_toronto_t-castle")["filters"]["types"], [])

    def test_malformed_is_a_normal_open_and_logged(self) -> None:
        for value in ("%%%", "home_", "home_ca_toronto_zzz_", "x" * 200, "home_zz_toronto"):
            with self.subTest(value=value[:20]), self.assertLogs("home_api", level="INFO") as logs:
                body = self.resolve(value)
                self.assertEqual((body["kind"], body["malformed"]), ("landing", True))
                self.assertIn("deeplink_bad", logs.output[0])

    def test_old_links_to_template_pages_go_there(self) -> None:
        body = resolve_start("courier")
        self.assertEqual((body["kind"], body["legacy_path"]), ("legacy", "/app/courier/"))
        # Housing screens are pages of the app now (the bot's buttons open them).
        self.assertEqual((resolve_start("home_alerts")["kind"], resolve_start("home_alerts")["path"]), ("page", "/saved?tab=alerts"))
        self.assertEqual(resolve_start("home_mine")["path"], "/mine")


class RoutingTests(ApiCase):
    def test_unknown_path_is_json_with_a_code(self) -> None:
        for method in (self.client.get, self.client.post):
            response = method("/api/v1/nope/")
            self.assertEqual((response.status_code, response.json()["code"]), (404, "not_found"))
        self.assertEqual(self.client.get("/api/v1/public/listings/abc/").json()["code"], "not_found")

    def test_old_api_is_untouched(self) -> None:
        self.assertEqual(self.client.post("/api/auth/telegram/", {}, content_type="application/json").status_code, 400)


class RedactTests(SimpleTestCase):
    def test_keeps_prices_and_plain_digits(self) -> None:
        self.assertEqual(redact_contact("اتاق ۱۲۰۰ دلار، 2 خوابه"), "اتاق ۱۲۰۰ دلار، 2 خوابه")

    def test_removes_every_contact_form(self) -> None:
        for leaked in (
            "@name_here",
            "https://t.me/x",
            "t.me/x",
            "T.ME/x",
            "telegram.me/x",
            "tg://resolve?domain=x",
            "wa.me/14165550142",
            "https://wa.me/14165550142",
            "whatsapp.com/send?phone=1",
            "chat.whatsapp.com/abc",
            "ca_user1",
            "sara_home",
            "sara123",
            "416-555-0142",
            "۴۱۶۵۵۵۰۱۴۲",
            "+49 176 12345678",
            "0176 1234567",
            "030/1234567",
        ):
            with self.subTest(leaked=leaked):
                self.assertEqual(redact_contact(f"خانه {leaked} لطفا"), "خانه لطفا")
                self.assertTrue(has_contact(f"خانه {leaked} لطفا"))

    def test_a_word_that_announces_a_handle_takes_the_next_word(self) -> None:
        for text, gone in (
            ("آیدی alirezahome پیام بدهید", "alirezahome"),
            ("ایدی: alirezahome", "alirezahome"),
            ("تلگرام alirezahome", "alirezahome"),
            ("واتساپ alirezahome", "alirezahome"),
            ("Telegram: alirezahome please", "alirezahome"),
            ("id alirezahome", "alirezahome"),
            ("contact sarahhome", "sarahhome"),
        ):
            with self.subTest(text=text):
                self.assertNotIn(gone, redact_contact(text))

    def test_known_handles_go_in_any_case_and_only_as_whole_words(self) -> None:
        self.assertEqual(redact_contact("پیام به RoomsByAli بدهید", ("roomsbyali",)), "پیام به بدهید")
        self.assertEqual(redact_contact("RoomsByAlireza", ("roomsbyali",)), "RoomsByAlireza")
        self.assertEqual(redact_contact("Finch", ("",)), "Finch")

    def test_real_looking_text_keeps_its_latin_words(self) -> None:
        for text in (
            "آپارتمان ۱ خوابه نزدیک Yonge و Finch، ۵ دقیقه تا مترو",
            "اتاق در North York نزدیک Yonge & Finch، از اول نوامبر",
            "Basement in Liberty Village, Queen St W, near TTC",
            "اتاق در Neukölln نزدیک U8، Warmmiete، Anmeldung ممکن است",
            "Zwischenmiete in Prenzlauer Berg, 2-Zimmer, WBS nötig",
            "Apt 302, Apt302, Unit12, Room2, M5V 2T6, 2BR, 3+1, WiFi6",
            "اجاره 1200 CAD یا CAD1200 از Nov25 تا Feb2026",
            "Côte-des-Neiges, NDG, Plateau-Mont-Royal, ۴½",
            "Mississauga, Square One, GO Train",
        ):
            with self.subTest(text=text):
                self.assertEqual(redact_contact(text), text)
                self.assertFalse(has_contact(text))

    def test_a_place_goes_out_whole_or_not_at_all(self) -> None:
        self.assertEqual(clean_place("Yonge & Finch", ()), "Yonge & Finch")
        self.assertEqual(clean_place("Finch wa.me/1", ()), "")
        self.assertEqual(clean_place("roomsbyali", ("roomsbyali",)), "")

    def test_stored_summaries_get_the_same_cleaning(self) -> None:
        self.assertEqual(_summary("اتاق wa.me/1416 در Yonge، پیام tg://x یا ca_user1"), "اتاق در Yonge، پیام یا")


class ContractTests(SimpleTestCase):
    def test_openapi_file_matches_the_code(self) -> None:
        out = io.StringIO()
        call_command("spectacular", urlconf="home_api.schema_urls", stdout=out)
        self.assertEqual(
            out.getvalue().replace("\r\n", "\n").strip(),
            SCHEMA_FILE.read_text(encoding="utf-8").replace("\r\n", "\n").strip(),
            "openapi.yaml drifted. Run: python manage.py spectacular --urlconf home_api.schema_urls --file home_api/openapi.yaml",
        )

    def test_every_operation_lists_its_codes_and_every_code_is_known(self) -> None:
        for path, methods in SCHEMA["paths"].items():
            for method, operation in methods.items():
                with self.subTest(path=path, method=method):
                    self.assertTrue(operation.get("x-error-codes"))
                    self.assertLessEqual(set(operation["x-error-codes"]), set(CODES))

    def test_the_contract_covers_every_p1_endpoint(self) -> None:
        expected = {
            "/api/v1/auth/exchange/",
            "/api/v1/me/prefs/",
            "/api/v1/me/waitlist/",
            "/api/v1/me/listings/{id}/",
            "/api/v1/me/listings/{id}/contact/",
            "/api/v1/me/join-check/",
            "/api/v1/me/listings/{id}/availability/",
            "/api/v1/me/saved/",
            "/api/v1/me/saved/ids/",
            "/api/v1/me/saved/{id}/",
            "/api/v1/me/search/parse/",
            "/api/v1/me/search/recent/",
            "/api/v1/me/search/recent/restore/",
            "/api/v1/public/areas/",
            "/api/v1/public/areas/search/",
            "/api/v1/public/meta/",
            "/api/v1/public/cities/",
            "/api/v1/public/listings/{id}/",
            "/api/v1/public/deeplink/",
            "/api/v1/public/feed/",
            "/feeds/manifest.json",
            "/feeds/{city_key}.{version}.json",
        }
        self.assertEqual(set(SCHEMA["paths"]), expected | P3_PATHS)
        for path in ("/feeds/manifest.json", "/feeds/{city_key}.{version}.json"):
            self.assertIn("x-served", next(iter(SCHEMA["paths"][path].values())))

    def test_every_p3_and_p4_operation_is_live(self) -> None:
        for path in P3_PATHS:
            for method, operation in SCHEMA["paths"][path].items():
                with self.subTest(path=path, method=method):
                    self.assertNotIn("x-served", operation)

    def test_public_schemas_have_no_contact_field(self) -> None:
        for name in ("PublicCard", "PublicListing", "SnapshotFile", "ManifestCity"):
            properties = SCHEMA["components"]["schemas"][name]["properties"]
            self.assertFalse({"contact_telegram", "contact_phone", "telegram", "phone", "source_url", "author_username"} & set(properties), name)


class CodeCoverageTests(SimpleTestCase):
    """A code nobody can trigger, or that nothing tests, fails here. Named so it runs after the classes above."""

    def test_each_code_has_a_pytest(self) -> None:
        here = Path(__file__).resolve().parent
        names = ("test_home_api.py", "test_contact_gate.py", "test_snapshots.py", "test_home_p3.py", "test_home_p4.py", "test_home_smart_post.py")
        text = "".join((here / name).read_text(encoding="utf-8") for name in names)
        tested = set(re.findall(r'"([a-z_]+)"', text)) & set(CODES)
        self.assertEqual(set(CODES) - NOT_RAISED_YET - tested, set())
        self.assertFalse(NOT_RAISED_YET - set(CODES))
