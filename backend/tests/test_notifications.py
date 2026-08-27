from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse
from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from matching.models import Match
from notifications.messages import connected_text, match_accepted_text, new_match_text
from notifications.services import notify_connected, notify_match_accepted, notify_new_match
from notifications.telegram import mini_app_link, send_telegram_message
from tests.helpers import TEST_SECRET, make_user
from tests.test_requests import DEMAND_PAYLOAD, SUPPLY_PAYLOAD


@override_settings(
    SECRET_KEY=TEST_SECRET,
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_BOT_USERNAME="CB_koolbarbot",
    TELEGRAM_MINI_APP_URL="",
)
class NotificationTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.demand_user = make_user(telegram_user_id=94001, first_name="Sara")
        self.supply_user = make_user(
            telegram_user_id=94002,
            first_name="Ali",
            telegram_username="ali_bot",
        )
        create_item_request(self.demand_user, DEMAND_PAYLOAD)
        create_item_request(self.supply_user, SUPPLY_PAYLOAD)
        self.match = Match.objects.select_related("demand_request", "supply_request").get()

    def test_new_match_message_uses_city_names_and_travel_date(self) -> None:
        text = new_match_text(self.match)
        self.assertIn("You have a potential match", text)
        self.assertIn("Tehran → Toronto", text)
        self.assertIn("September 7", text)
        self.assertIn("Open Koolbar to review", text)

    def test_accepted_and_connected_copy(self) -> None:
        self.assertIn("Your match has been accepted", match_accepted_text())
        text = connected_text(self.match, self.demand_user)
        self.assertIn("You are connected", text)
        self.assertIn("Telegram ID: 94002", text)
        self.assertIn("@ali_bot", text)
        self.assertIn("https://t.me/ali_bot", text)

    def test_mini_app_link_uses_bot_username_when_no_https_url(self) -> None:
        self.assertEqual(
            mini_app_link("matches"),
            "https://t.me/CB_koolbarbot/app?startapp=matches",
        )

    def test_send_without_token_is_skipped(self) -> None:
        self.assertFalse(send_telegram_message(94001, "hello"))

    @patch("notifications.services.send_telegram_message", return_value=True)
    def test_notify_new_match_sends_to_both_users(self, mocked_send) -> None:
        notify_new_match(self.match)
        chats = {call.args[0] for call in mocked_send.call_args_list}
        self.assertEqual(chats, {94001, 94002})
        self.assertEqual(mocked_send.call_count, 2)

    @patch("notifications.services.send_telegram_message", return_value=True)
    def test_notify_match_accepted_sends_to_waiting_party(self, mocked_send) -> None:
        self.match.status = "ACCEPTED_BY_DEMAND"
        self.match.save(update_fields=["status"])
        notify_match_accepted(self.match)
        self.assertEqual(mocked_send.call_count, 1)
        self.assertEqual(mocked_send.call_args.args[0], 94002)

    @patch("notifications.services.send_telegram_message", return_value=True)
    def test_notify_connected_sends_to_both_users(self, mocked_send) -> None:
        notify_connected(self.match)
        chats = {call.args[0] for call in mocked_send.call_args_list}
        self.assertEqual(chats, {94001, 94002})
        demand_call = next(call for call in mocked_send.call_args_list if call.args[0] == 94001)
        self.assertIn("@ali_bot", demand_call.args[1])
        demand_urls = [btn["url"] for row in demand_call.kwargs["reply_markup"]["inline_keyboard"] for btn in row]
        demand_dm = next(url for url in demand_urls if url.startswith("https://t.me/ali_bot"))
        demand_draft = unquote(parse_qs(urlparse(demand_dm).query)["text"][0])
        self.assertIn("من یه بسته دارم", demand_draft)
        self.assertIn("👕 لباس", demand_draft)
        self.assertIn("Ali", demand_draft)
