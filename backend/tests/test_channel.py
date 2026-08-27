from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from item_requests.models import ChannelStatus, RequestStatus
from item_requests.seed import seed_catalog
from item_requests.services import (
    cancel_item_request,
    create_item_request,
    expire_if_needed,
    expire_user_requests,
    update_item_request,
)
from miniapp.auth import startapp_path
from notifications.channel import format_demand_message, format_supply_message
from tests.helpers import TEST_SECRET, make_user
from tests.test_requests import DEMAND_PAYLOAD, SUPPLY_PAYLOAD

CHANNEL_SETTINGS = {
    "SECRET_KEY": TEST_SECRET,
    "DEBUG": False,
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_BOT_USERNAME": "CB_koolbarbot",
    "TELEGRAM_CHANNEL_ENABLED": True,
    "TELEGRAM_CHANNEL_ID": "-100111",
    "TELEGRAM_CHANNEL_USERNAME": "koolbar_market",
    "TELEGRAM_MINI_APP_URL": "",
}


def _ok_send(_method, _payload):
    return {"ok": True, "result": {"message_id": 9001}}


def _ok_edit(_method, _payload):
    return {"ok": True, "result": {"message_id": 9001}}


@override_settings(**CHANNEL_SETTINGS)
class ChannelPublishTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.user = make_user(
            telegram_user_id=96001,
            first_name="Leila",
            telegram_username="leila_send",
        )

    @patch("notifications.telegram.call_telegram_api", side_effect=_ok_send)
    def test_create_active_demand_publishes_channel_post(self, mocked) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            demand = create_item_request(self.user, DEMAND_PAYLOAD)
        demand.refresh_from_db()
        self.assertEqual(demand.status, RequestStatus.ACTIVE)
        self.assertEqual(demand.channel_status, ChannelStatus.PUBLISHED)
        self.assertEqual(demand.channel_message_id, 9001)
        self.assertIsNotNone(demand.channel_published_at)
        self.assertEqual(mocked.call_args.args[0], "sendMessage")
        payload = mocked.call_args.args[1]
        self.assertEqual(payload["chat_id"], -100111)
        self.assertIn("DEMAND", payload["text"])
        self.assertIn("Tehran", payload["text"])
        self.assertIn("Toronto", payload["text"])
        self.assertIn("Clothes", payload["text"])
        url = payload["reply_markup"]["inline_keyboard"][0][0]["url"]
        self.assertEqual(url, f"https://t.me/CB_koolbarbot/app?startapp=request_{demand.id}")

    @patch("notifications.telegram.call_telegram_api", side_effect=_ok_send)
    def test_create_active_supply_publishes_channel_post(self, mocked) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            supply = create_item_request(self.user, SUPPLY_PAYLOAD)
        supply.refresh_from_db()
        self.assertEqual(supply.channel_status, ChannelStatus.PUBLISHED)
        text = mocked.call_args.args[1]["text"]
        self.assertIn("SUPPLY", text)
        self.assertIn("Can carry:", text)
        self.assertIn("Clothes", text)
        self.assertIn("Documents", text)
        self.assertIn("Will NOT carry:", text)
        self.assertIn("Cigarettes", text)
        self.assertIn("Medicine", text)
        url = mocked.call_args.args[1]["reply_markup"]["inline_keyboard"][0][0]["url"]
        self.assertEqual(url, f"https://t.me/CB_koolbarbot/app?startapp=request_{supply.id}")

    @patch("notifications.telegram.call_telegram_api", side_effect=_ok_send)
    @override_settings(TELEGRAM_MINI_APP_URL="https://koolbar.example")
    def test_channel_button_stays_on_telegram_when_https_host_is_set(self, mocked) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            demand = create_item_request(self.user, DEMAND_PAYLOAD)
        button = mocked.call_args.args[1]["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button["url"], f"https://t.me/CB_koolbarbot/app?startapp=request_{demand.id}")
        self.assertNotIn("koolbar.example", button["url"])
        self.assertNotIn("web_app", button)

    @patch("notifications.telegram.call_telegram_api")
    def test_update_edits_existing_channel_message(self, mocked) -> None:
        mocked.side_effect = [
            {"ok": True, "result": {"message_id": 9001}},
            {"ok": True, "result": {"message_id": 9001}},
        ]
        with self.captureOnCommitCallbacks(execute=True):
            demand = create_item_request(self.user, DEMAND_PAYLOAD)
        demand.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            update_item_request(demand, {**DEMAND_PAYLOAD, "weight_kg": "3.00"})
        demand.refresh_from_db()
        self.assertEqual(demand.channel_message_id, 9001)
        self.assertEqual(demand.channel_status, ChannelStatus.UPDATED)
        methods = [call.args[0] for call in mocked.call_args_list]
        self.assertEqual(methods, ["sendMessage", "editMessageText"])
        self.assertIn("3 kg", mocked.call_args_list[1].args[1]["text"])

    @patch("notifications.telegram.call_telegram_api")
    def test_cancel_marks_channel_post_unavailable(self, mocked) -> None:
        mocked.side_effect = [
            {"ok": True, "result": {"message_id": 9001}},
            {"ok": True, "result": {"message_id": 9001}},
        ]
        with self.captureOnCommitCallbacks(execute=True):
            demand = create_item_request(self.user, DEMAND_PAYLOAD)
        with self.captureOnCommitCallbacks(execute=True):
            cancel_item_request(demand)
        demand.refresh_from_db()
        self.assertEqual(demand.status, RequestStatus.CANCELLED)
        self.assertEqual(demand.channel_status, ChannelStatus.UPDATED)
        self.assertEqual(mocked.call_args_list[1].args[0], "editMessageText")
        self.assertIn("No longer available", mocked.call_args_list[1].args[1]["text"])

    @patch("notifications.telegram.call_telegram_api")
    def test_expire_marks_channel_post_unavailable(self, mocked) -> None:
        mocked.side_effect = [
            {"ok": True, "result": {"message_id": 9001}},
            {"ok": True, "result": {"message_id": 9001}},
        ]
        with self.captureOnCommitCallbacks(execute=True):
            demand = create_item_request(self.user, DEMAND_PAYLOAD)
        demand.expires_at = timezone.now() - timedelta(minutes=1)
        demand.save(update_fields=["expires_at"])
        with self.captureOnCommitCallbacks(execute=True):
            expire_if_needed(demand)
        demand.refresh_from_db()
        self.assertEqual(demand.status, RequestStatus.EXPIRED)
        self.assertEqual(mocked.call_args_list[1].args[0], "editMessageText")
        self.assertIn("No longer available", mocked.call_args_list[1].args[1]["text"])

    @patch("notifications.telegram.call_telegram_api")
    def test_expire_user_requests_updates_channel_post(self, mocked) -> None:
        mocked.side_effect = [
            {"ok": True, "result": {"message_id": 9001}},
            {"ok": True, "result": {"message_id": 9001}},
        ]
        with self.captureOnCommitCallbacks(execute=True):
            demand = create_item_request(self.user, DEMAND_PAYLOAD)
        demand.expires_at = timezone.now() - timedelta(minutes=1)
        demand.save(update_fields=["expires_at"])
        with self.captureOnCommitCallbacks(execute=True):
            expire_user_requests(self.user)
        demand.refresh_from_db()
        self.assertEqual(demand.status, RequestStatus.EXPIRED)
        self.assertEqual(mocked.call_args_list[1].args[0], "editMessageText")

    @patch("notifications.telegram.call_telegram_api", return_value=None)
    def test_telegram_failure_does_not_block_request_create(self, mocked) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            demand = create_item_request(self.user, DEMAND_PAYLOAD)
        demand.refresh_from_db()
        self.assertEqual(demand.status, RequestStatus.ACTIVE)
        self.assertEqual(demand.channel_status, ChannelStatus.FAILED)
        self.assertIsNone(demand.channel_message_id)
        mocked.assert_called()

    def test_channel_message_omits_private_information(self) -> None:
        demand = create_item_request(self.user, DEMAND_PAYLOAD)
        text = format_demand_message(demand)
        self.assertNotIn(str(self.user.telegram_user_id), text)
        self.assertNotIn("leila_send", text)
        self.assertNotIn("Leila", text)
        self.assertNotIn("Personal clothes", text)
        self.assertNotIn("phone", text.lower())
        self.assertNotIn("email", text.lower())
        self.assertNotIn("@", text)

    @override_settings(TELEGRAM_CHANNEL_ENABLED=False)
    def test_disabled_channel_does_not_publish(self) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            demand = create_item_request(self.user, DEMAND_PAYLOAD)
        demand.refresh_from_db()
        self.assertEqual(demand.channel_status, ChannelStatus.NOT_PUBLISHED)
        self.assertIsNone(demand.channel_message_id)

    def test_supply_message_omits_private_description(self) -> None:
        supply = create_item_request(self.user, SUPPLY_PAYLOAD)
        text = format_supply_message(supply)
        self.assertNotIn("Can carry personal items", text)
        self.assertNotIn(str(self.user.telegram_user_id), text)

    def test_rating_publishes_to_channel_without_private_details(self) -> None:
        from matching.acceptance import accept_match
        from matching.completion import complete_match, rate_match
        from matching.models import Match

        traveler = make_user(telegram_user_id=96002, first_name="Omar", telegram_username="omar_ops")
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(traveler, SUPPLY_PAYLOAD)
        match = Match.objects.get()
        accept_match(match, self.user)
        match.refresh_from_db()
        accept_match(match, traveler)
        match.refresh_from_db()
        complete_match(match, self.user)
        with patch("notifications.telegram.call_telegram_api", side_effect=_ok_send) as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                rate_match(match, self.user, 5, "Arrived on time and careful")
        rating_calls = [call for call in mocked.call_args_list if call.args[0] == "sendMessage"]
        self.assertTrue(rating_calls)
        text = rating_calls[-1].args[1]["text"]
        self.assertIn("Rating", text)
        self.assertIn("5/5", text)
        self.assertIn("Tehran", text)
        self.assertIn("Toronto", text)
        self.assertIn("✈️", text)
        self.assertIn("Sep 10", text)
        self.assertIn("Arrived on time and careful", text)
        self.assertNotIn("Leila", text)
        self.assertNotIn("omar_ops", text)
        self.assertNotIn(str(self.user.telegram_user_id), text)

    def test_low_ratings_are_not_published_to_channel(self) -> None:
        from matching.acceptance import accept_match
        from matching.completion import complete_match, rate_match
        from matching.models import Match

        traveler = make_user(telegram_user_id=96003, first_name="Nima")
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(traveler, SUPPLY_PAYLOAD)
        match = Match.objects.get()
        accept_match(match, self.user)
        match.refresh_from_db()
        accept_match(match, traveler)
        match.refresh_from_db()
        complete_match(match, self.user)
        with patch("notifications.telegram.call_telegram_api", side_effect=_ok_send) as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                rate_match(match, self.user, 2, "Too slow")
        rating_calls = [call for call in mocked.call_args_list if call.args[0] == "sendMessage"]
        self.assertEqual(rating_calls, [])

    def test_startapp_opens_request_detail(self) -> None:
        self.assertEqual(startapp_path("request_42"), "/app/requests/42/")
        self.assertEqual(startapp_path("matches"), "/app/matches/")
        self.assertEqual(startapp_path("unknown"), "/app/")

    @override_settings(TELEGRAM_CHANNEL_ID="1003954568602", TELEGRAM_CHANNEL_USERNAME="")
    def test_numeric_channel_id_without_minus_is_normalized(self) -> None:
        from notifications.channel import channel_chat_id

        self.assertEqual(channel_chat_id(), -1003954568602)
