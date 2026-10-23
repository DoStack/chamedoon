"""Per-country courier channels: routing, one post per pair, active_from, cap, retries, guards, admin."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import User as StaffUser
from django.test import TestCase, override_settings
from django.utils import timezone

from home.guard import courier_channel_names, is_courier_channel
from home.models import HomeChannel
from item_requests.models import (
    CourierChannel,
    CourierPublication,
    ItemRequest,
    RequestStatus,
    RequestType,
)
from item_requests.seed import seed_catalog
from notifications import country_channels as cc
from notifications.channel import _format_request_message, view_on_chamedoon_markup
from tests.helpers import TEST_SECRET, make_user

CA_CHAT = -1004470852135
DE_CHAT = -1004412131660
MAIN_CHAT = -100111

SETTINGS = {
    "SECRET_KEY": TEST_SECRET,
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_BOT_USERNAME": "chamedoon_test_bot",
    "TELEGRAM_CHANNEL_ENABLED": True,
    "TELEGRAM_CHANNEL_ID": str(MAIN_CHAT),
    "TELEGRAM_CHANNEL_USERNAME": "chamedoon_market",
    "TELEGRAM_GROUP_USERNAME": "",
    "TELEGRAM_MINI_APP_URL": "",
    "COURIER_COUNTRY_CHANNEL_DAILY_CAP": 40,
    "COURIER_COUNTRY_CHANNEL_LOOKBACK_DAYS": 10,
    "COURIER_COUNTRY_CHANNEL_BATCH": 10,
}


class FakeTelegram:
    """Records every Bot API call. `fail` maps chat id -> error body for sendMessage."""

    def __init__(self, fail: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail or {}
        self.next_id = 500

    def __call__(self, method: str, payload: dict):
        self.calls.append((method, payload))
        if method == "sendMessage":
            error = self.fail.get(payload["chat_id"])
            if error:
                return None, error
            self.next_id += 1
            return {"ok": True, "result": {"message_id": self.next_id}}, {}
        if method == "editMessageText":
            return {"ok": True, "result": {"message_id": payload["message_id"]}}, {}
        return {"ok": True, "result": True}, {}

    def sends(self, chat_id=None) -> list[dict]:
        return [p for m, p in self.calls if m == "sendMessage" and (chat_id is None or p["chat_id"] == chat_id)]


def make_request(user, *, origin="IR", destination="CA", stops=None, age_days: float = 0, **extra) -> ItemRequest:
    item = ItemRequest.objects.create(
        user=user,
        type=RequestType.DEMAND,
        origin_country=origin,
        origin_city="tehran" if origin == "IR" else "toronto" if origin == "CA" else "berlin",
        destination_country=destination,
        destination_city="toronto" if destination == "CA" else "berlin" if destination == "DE" else "tehran",
        destination_cities=stops or [],
        date_from=date(2027, 9, 1),
        date_to=date(2027, 9, 7),
        desired_date=date(2027, 9, 7),
        weight_kg="2.00",
        expires_at=timezone.now() + timedelta(days=60),
        **extra,
    )
    ItemRequest.objects.filter(pk=item.pk).update(created_at=timezone.now() - timedelta(days=age_days))
    item.refresh_from_db()
    return item


@override_settings(**SETTINGS)
class CourierCountryChannelTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.user = make_user(telegram_user_id=97001, first_name="Sara")
        self.past = timezone.now() - timedelta(days=30)
        CourierChannel.objects.update(active_from=self.past)
        self.ca = CourierChannel.objects.get(country="CA")
        self.de = CourierChannel.objects.get(country="DE")

    def run_publish(self, fake=None, **kwargs) -> tuple[dict, FakeTelegram]:
        fake = fake or FakeTelegram()
        with patch("notifications.telegram.call_telegram_api_detailed", fake):
            return cc.publish_country_channels(**kwargs), fake

    # Seed -------------------------------------------------------------

    def test_the_migration_seeds_canada_and_germany_with_a_fixed_start(self) -> None:
        from importlib import import_module

        seed = import_module("item_requests.migrations.0019_seed_courier_channels")
        self.assertEqual(seed.ACTIVE_FROM, datetime(2026, 10, 23, 12, 0, tzinfo=dt_timezone.utc))
        CourierChannel.objects.all().delete()
        from django.apps import apps

        seed.seed(apps, None)
        rows = {row.country: row for row in CourierChannel.objects.all()}
        self.assertEqual((rows["CA"].chat_id, rows["CA"].title), (CA_CHAT, "چمدون - باربری · کانادا"))
        self.assertEqual((rows["DE"].chat_id, rows["DE"].title), (DE_CHAT, "چمدون - باربری · آلمان"))
        for row in rows.values():
            self.assertEqual(row.username, "")
            self.assertTrue(row.enabled)
            self.assertEqual(row.active_from, seed.ACTIVE_FROM)
            self.assertEqual(row.target(), row.chat_id)
        seed.unseed(apps, None)
        self.assertFalse(CourierChannel.objects.exists())

    # Routing ----------------------------------------------------------

    def test_routing_by_origin_destination_and_stops(self) -> None:
        channels = cc.enabled_channels()
        self.assertEqual([c.country for c in cc.route(make_request(self.user, origin="IR", destination="CA"), channels)], ["CA"])
        self.assertEqual([c.country for c in cc.route(make_request(self.user, origin="CA", destination="IR"), channels)], ["CA"])
        both = make_request(self.user, origin="CA", destination="DE")
        self.assertEqual(sorted(c.country for c in cc.route(both, channels)), ["CA", "DE"])
        stop = make_request(self.user, origin="IR", destination="CA", stops=[{"country": "de", "city": "berlin"}])
        self.assertEqual(sorted(c.country for c in cc.route(stop, channels)), ["CA", "DE"])
        self.assertEqual(cc.route(make_request(self.user, origin="IR", destination="IT"), channels), [])
        self.de.enabled = False
        self.de.save()
        self.assertEqual([c.country for c in cc.route(both, cc.enabled_channels())], ["CA"])

    def test_a_request_goes_to_its_country_channel_with_the_main_post_text_and_button(self) -> None:
        item = make_request(self.user, origin="CA", destination="IR")
        result, fake = self.run_publish()
        self.assertEqual(result["published"], 1)
        [sent] = fake.sends(CA_CHAT)
        self.assertEqual(sent["text"], _format_request_message(item))
        self.assertEqual(sent["reply_markup"], view_on_chamedoon_markup(item))
        self.assertEqual(fake.sends(DE_CHAT), [])
        row = CourierPublication.objects.get(request=item, channel=self.ca)
        self.assertEqual(row.message_ids, [501])
        self.assertIsNotNone(row.published_at)
        self.assertEqual((row.attempts, row.error, row.claimed_at), (0, "", None))

    def test_a_canada_to_germany_request_reaches_both_country_channels(self) -> None:
        item = make_request(self.user, origin="CA", destination="DE")
        result, fake = self.run_publish()
        self.assertEqual(result["published"], 2)
        self.assertEqual(len(fake.sends(CA_CHAT)), 1)
        self.assertEqual(len(fake.sends(DE_CHAT)), 1)
        self.assertEqual(CourierPublication.objects.filter(request=item, published_at__isnull=False).count(), 2)

    def test_the_main_channel_and_the_country_channel_both_get_it(self) -> None:
        from market.extract import publish_stored_requests

        item = make_request(self.user, origin="IR", destination="CA", imported=True)
        fake = FakeTelegram()
        with patch("notifications.telegram.call_telegram_api_detailed", fake):
            run = publish_stored_requests()
        self.assertTrue(run["ok"])
        self.assertEqual(run["publish"]["country_channels"]["published"], 1)
        self.assertEqual(len(fake.sends(MAIN_CHAT)), 1)
        self.assertEqual(len(fake.sends(CA_CHAT)), 1)
        self.assertEqual(fake.sends(MAIN_CHAT)[0]["text"], fake.sends(CA_CHAT)[0]["text"])
        item.refresh_from_db()
        self.assertIsNotNone(item.channel_message_id)

    # No double post ---------------------------------------------------

    def test_nothing_posts_twice(self) -> None:
        item = make_request(self.user, origin="CA", destination="IR")
        _result, fake = self.run_publish()
        result, fake = self.run_publish(fake)
        self.assertEqual(result["published"], 0)
        self.assertEqual(len(fake.sends(CA_CHAT)), 1)
        self.assertEqual(result["remaining"], 0)
        self.assertFalse(cc.publish_to_country(item, self.ca))
        self.assertEqual(CourierPublication.objects.filter(request=item).count(), 1)

    def test_a_pair_another_run_is_sending_is_skipped_until_the_claim_is_stale(self) -> None:
        item = make_request(self.user, origin="CA", destination="IR")
        CourierPublication.objects.create(request=item, channel=self.ca, claimed_at=timezone.now())
        result, fake = self.run_publish()
        self.assertEqual((result["published"], fake.sends()), (0, []))
        CourierPublication.objects.update(claimed_at=timezone.now() - timedelta(seconds=cc.CLAIM_SECONDS + 5))
        result, fake = self.run_publish()
        self.assertEqual(result["published"], 1)

    # Window -----------------------------------------------------------

    def test_only_requests_created_after_active_from(self) -> None:
        old = make_request(self.user, origin="CA", destination="IR", age_days=2)
        new = make_request(self.user, origin="CA", destination="IR", age_days=0)
        self.ca.active_from = timezone.now() - timedelta(days=1)
        self.ca.save()
        result, fake = self.run_publish()
        self.assertEqual(result["published"], 1)
        self.assertTrue(CourierPublication.objects.filter(request=new, published_at__isnull=False).exists())
        self.assertFalse(CourierPublication.objects.filter(request=old).exists())

    def test_lookback_window_and_inactive_requests(self) -> None:
        make_request(self.user, origin="CA", destination="IR", age_days=11)
        make_request(self.user, origin="CA", destination="IR", status=RequestStatus.CANCELLED)
        fresh = make_request(self.user, origin="CA", destination="IR", age_days=9)
        result, _fake = self.run_publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual(list(CourierPublication.objects.values_list("request_id", flat=True)), [fresh.pk])
        self.assertFalse(cc.eligible(make_request(self.user, status=RequestStatus.CLOSED), self.ca))

    # Cap and batch ----------------------------------------------------

    @override_settings(COURIER_COUNTRY_CHANNEL_DAILY_CAP=2)
    def test_per_channel_daily_cap(self) -> None:
        for _ in range(3):
            make_request(self.user, origin="CA", destination="IR")
        make_request(self.user, origin="DE", destination="IR")
        result, fake = self.run_publish()
        self.assertEqual(result["published"], 3)
        self.assertEqual(result["over_daily_cap"], 1)
        self.assertEqual(len(fake.sends(CA_CHAT)), 2)
        self.assertEqual(len(fake.sends(DE_CHAT)), 1)
        self.assertEqual(result["remaining"], 1)

    def test_batch_limit_and_time_budget(self) -> None:
        for _ in range(3):
            make_request(self.user, origin="CA", destination="IR")
        result, _fake = self.run_publish(limit=2)
        self.assertEqual(result["published"], 2)
        import time

        result, fake = self.run_publish(stop_at=time.monotonic())
        self.assertEqual((result["published"], fake.sends()), (0, []))

    # Failures ---------------------------------------------------------

    def test_a_failed_send_is_recorded_and_retried_up_to_the_limit(self) -> None:
        item = make_request(self.user, origin="CA", destination="IR")
        broken = FakeTelegram(fail={CA_CHAT: {"description": "Forbidden: bot is not a member"}})
        result, _ = self.run_publish(broken)
        self.assertEqual((result["published"], result["failed"]), (0, 1))
        row = CourierPublication.objects.get(request=item, channel=self.ca)
        self.assertEqual((row.attempts, row.error, row.published_at, row.claimed_at), (1, "Forbidden: bot is not a member", None, None))
        self.ca.refresh_from_db()
        self.assertEqual(self.ca.last_error, "Forbidden: bot is not a member")
        result, _ = self.run_publish()
        self.assertEqual(result["published"], 1)
        row.refresh_from_db()
        self.assertEqual((row.attempts, row.error), (1, ""))
        self.assertIsNotNone(row.published_at)

    def test_three_failures_stop_the_retries(self) -> None:
        item = make_request(self.user, origin="CA", destination="IR")
        broken = FakeTelegram(fail={CA_CHAT: {"description": "Bad Request: chat not found"}})
        for _ in range(cc.MAX_ATTEMPTS + 1):
            self.run_publish(broken)
        self.assertEqual(len(broken.sends(CA_CHAT)), cc.MAX_ATTEMPTS)
        self.assertEqual(CourierPublication.objects.get(request=item).attempts, cc.MAX_ATTEMPTS)
        result, fake = self.run_publish()
        self.assertEqual((result["published"], fake.sends()), (0, []))

    def test_flood_wait_stops_the_run(self) -> None:
        make_request(self.user, origin="CA", destination="IR")
        make_request(self.user, origin="CA", destination="IR")
        flood = FakeTelegram(fail={CA_CHAT: {"description": "Too Many Requests", "parameters": {"retry_after": 30}}})
        result, _ = self.run_publish(flood)
        self.assertEqual((result["failed"], result["retry_after"]), (1, 30))
        self.assertEqual(len(flood.sends(CA_CHAT)), 1)

    def test_skipped_when_publishing_is_off_or_the_bot_token_is_missing(self) -> None:
        make_request(self.user, origin="CA", destination="IR")
        with override_settings(TELEGRAM_CHANNEL_ENABLED=False):
            result, fake = self.run_publish()
        self.assertIn("TELEGRAM_CHANNEL_ENABLED", result["skipped"])
        with override_settings(TELEGRAM_BOT_TOKEN=""):
            result, fake = self.run_publish()
        self.assertIn("token", result["skipped"])
        self.assertEqual(fake.sends(), [])
        self.assertFalse(CourierPublication.objects.exists())

    def test_a_request_that_is_no_longer_active_is_marked_unavailable_once(self) -> None:
        item = make_request(self.user, origin="CA", destination="IR")
        self.run_publish()
        ItemRequest.objects.filter(pk=item.pk).update(status=RequestStatus.CLOSED)
        result, fake = self.run_publish()
        self.assertEqual(result["closed"], 1)
        [(method, payload)] = [call for call in fake.calls if call[0] == "editMessageText"]
        self.assertEqual((payload["chat_id"], payload["message_id"]), (CA_CHAT, 501))
        self.assertEqual(payload["reply_markup"], {"inline_keyboard": []})
        result, fake = self.run_publish()
        self.assertEqual(result["closed"], 0)

    # Guards -----------------------------------------------------------

    def test_courier_never_posts_to_a_housing_channel(self) -> None:
        HomeChannel.objects.create(country="CA", city="Testville", username="chamedoon_ca_khane", chat_id=-1009990001111)
        self.ca.chat_id = -1009990001111
        self.ca.save()
        self.assertIn("housing channel", cc.blocked_reason(self.ca))
        make_request(self.user, origin="CA", destination="IR")
        result, fake = self.run_publish()
        self.assertEqual((result["published"], fake.sends(-1009990001111)), (0, []))
        self.ca.chat_id = None
        self.ca.username = "Chamedoon_CA_Khane"
        self.ca.save()
        self.assertIn("housing channel", cc.blocked_reason(self.ca))
        item = make_request(self.user, origin="CA", destination="IR")
        with self.assertRaises(cc.SendError):
            cc._send(item, self.ca)

    def test_the_main_channel_is_not_a_country_channel(self) -> None:
        self.ca.chat_id = MAIN_CHAT
        self.ca.save()
        self.assertIn("main courier channel", cc.blocked_reason(self.ca))
        self.ca.chat_id = None
        self.ca.username = "chamedoon_market"
        self.assertIn("main courier channel", cc.blocked_reason(self.ca))
        self.ca.username = ""
        self.assertIn("No chat id", cc.blocked_reason(self.ca))

    def test_housing_refuses_the_courier_country_channels(self) -> None:
        names = courier_channel_names()
        self.assertTrue({"4470852135", "4412131660"} <= names)
        self.assertTrue(is_courier_channel("", CA_CHAT))
        self.assertTrue(is_courier_channel("", DE_CHAT))
        self.de.username = "chamedoon_de_barbari"
        self.de.save()
        self.assertTrue(is_courier_channel("@Chamedoon_DE_Barbari"))
        housing = HomeChannel.objects.create(country="DE", city="Testburg", username="housing_de", chat_id=DE_CHAT)
        from home.publish import verify_channel

        with patch("home.bot.call") as call:
            self.assertIn("courier channel", verify_channel(housing))
        call.assert_not_called()

    # Verify (admin action) --------------------------------------------

    def answers(self, member: dict, chat: dict | None = None):
        def fake(method, payload):
            body = {
                "getChat": chat or {"id": CA_CHAT, "title": "چمدون - باربری · کانادا", "username": "chamedoon_ca_bar"},
                "getMe": {"id": 7, "username": "chamedoon_test_bot"},
                "getChatMember": member,
            }[method]
            return {"ok": True, "result": body}, {}

        return fake

    def test_check_bot_can_post(self) -> None:
        with patch("notifications.telegram.call_telegram_api_detailed", self.answers({"status": "administrator", "can_post_messages": True})):
            self.assertEqual(cc.verify_courier_channel(self.ca), "")
        self.ca.refresh_from_db()
        self.assertEqual((self.ca.chat_id, self.ca.username, self.ca.last_error), (CA_CHAT, "chamedoon_ca_bar", ""))
        self.assertIsNotNone(self.ca.verified_at)
        with patch("notifications.telegram.call_telegram_api_detailed", self.answers({"status": "administrator"})):
            self.assertIn("cannot post", cc.verify_courier_channel(self.ca))
        with patch("notifications.telegram.call_telegram_api_detailed", self.answers({"status": "member"})):
            self.assertIn("not an admin", cc.verify_courier_channel(self.ca))
        self.ca.refresh_from_db()
        self.assertIn("not an admin", self.ca.last_error)

    def test_check_reports_telegram_errors_and_housing_chats(self) -> None:
        def broken(method, payload):
            return None, {"description": "Bad Request: chat not found"}

        with patch("notifications.telegram.call_telegram_api_detailed", broken):
            self.assertEqual(cc.verify_courier_channel(self.de), "Bad Request: chat not found")
        HomeChannel.objects.create(country="DE", city="Berlin2", username="berlin_housing", chat_id=-1001231231234)
        fake = self.answers({"status": "creator"}, chat={"id": -1001231231234, "title": "x"})
        with patch("notifications.telegram.call_telegram_api_detailed", fake):
            self.assertIn("housing channel", cc.verify_courier_channel(self.de))
        self.de.refresh_from_db()
        self.assertEqual(self.de.chat_id, DE_CHAT)
        self.de.chat_id = -1001231231234
        with patch("notifications.telegram.call_telegram_api_detailed") as call:
            self.assertIn("housing channel", cc.verify_courier_channel(self.de))
        call.assert_not_called()

    # Admin ------------------------------------------------------------

    def test_admin_list_toggle_and_check_action(self) -> None:
        StaffUser.objects.create_superuser("ops", "ops@example.com", "ops-pass")
        self.client.login(username="ops", password="ops-pass")
        page = self.client.get("/admin/item_requests/courierchannel/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "چمدون - باربری · کانادا")
        self.assertContains(page, "Check that the courier bot can post here")
        fake = self.answers({"status": "administrator", "can_post_messages": True})
        with patch("notifications.telegram.call_telegram_api_detailed", fake):
            done = self.client.post(
                "/admin/item_requests/courierchannel/",
                {"action": "check_access", "_selected_action": [self.ca.pk]},
                follow=True,
            )
        self.assertContains(done, "can post")
        self.client.post(
            "/admin/item_requests/courierchannel/",
            {"action": "disable_selected", "_selected_action": [self.ca.pk, self.de.pk]},
        )
        self.assertFalse(CourierChannel.objects.filter(enabled=True).exists())

    def test_admin_form_refuses_a_housing_or_main_channel(self) -> None:
        from item_requests.admin import CourierChannelForm

        HomeChannel.objects.create(country="FR", city="", username="fr_khane", chat_id=-1005550001111)
        form = CourierChannelForm(data={"country": "FR", "chat_id": -1005550001111, "enabled": True})
        self.assertFalse(form.is_valid())
        self.assertIn("housing channel", str(form.errors))
        form = CourierChannelForm(data={"country": "FR", "username": "@chamedoon_market", "enabled": True})
        self.assertFalse(form.is_valid())
        form = CourierChannelForm(data={"country": "fr", "chat_id": -1005550002222, "enabled": True})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().country, "FR")
        self.assertIn("FR", str(CourierChannel.objects.get(country="FR")))

    def test_home_cron_also_posts_to_country_channels(self) -> None:
        from api.cron_views import home_job_cron  # noqa: F401  (the hook lives there)

        make_request(self.user, origin="CA", destination="IR")
        fake = FakeTelegram()
        with override_settings(CRON_SECRET="cron-secret"), patch("api.cron_views.home_enabled", return_value=True), patch(
            "api.cron_views.run_home_job", return_value={"ok": True}
        ), patch("notifications.telegram.call_telegram_api_detailed", fake), patch(
            "notifications.channel.refresh_recent_channel_buttons", return_value=0
        ):
            response = self.client.get("/api/cron/home/", HTTP_AUTHORIZATION="Bearer cron-secret")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["courier_country_channels"]["published"], 1)
        self.assertEqual(len(fake.sends(CA_CHAT)), 1)


class TelegramDetailedCallTests(TestCase):
    def test_error_bodies_are_returned(self) -> None:
        import io
        import urllib.error

        from notifications.telegram import call_telegram_api, call_telegram_api_detailed

        with override_settings(TELEGRAM_BOT_TOKEN=""):
            self.assertEqual(call_telegram_api_detailed("getMe", {}), (None, {"description": "bot token missing"}))
        error = urllib.error.HTTPError("u", 403, "x", {}, io.BytesIO(b'{"ok": false, "description": "Forbidden"}'))
        with override_settings(TELEGRAM_BOT_TOKEN="t"), patch("urllib.request.urlopen", side_effect=error):
            self.assertEqual(call_telegram_api_detailed("getMe", {})[1]["description"], "Forbidden")
        with override_settings(TELEGRAM_BOT_TOKEN="t"), patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertIsNone(call_telegram_api("getMe", {}))
            self.assertIn("network error", call_telegram_api_detailed("getMe", {})[1]["description"])
