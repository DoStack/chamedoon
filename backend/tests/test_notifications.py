from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse
from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from matching.models import Match
from notifications.messages import connected_text, match_accepted_text, new_match_text
from notifications.services import notify_connected, notify_match_rejected, notify_new_match
from notifications.telegram import mini_app_in_chat_link, mini_app_link, mini_app_start_link, send_telegram_message
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
        self.assertIn("You received a new match request", text)
        self.assertIn("Tehran → Toronto", text)
        self.assertIn("September 7", text)
        self.assertIn("September 10", text)
        self.assertIn("September 1–15", text)
        self.assertIn("Ali wants to match", text)

    def test_accepted_and_connected_copy(self) -> None:
        self.assertIn("Your request has been accepted", match_accepted_text())
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
        self.assertEqual(
            mini_app_link("explore_9", mode="compact"),
            "https://t.me/CB_koolbarbot/app?startapp=explore_9&mode=compact",
        )
        self.assertEqual(
            mini_app_start_link("explore_9"),
            "https://t.me/CB_koolbarbot/app?startapp=explore_9",
        )
        self.assertEqual(
            mini_app_in_chat_link("explore_9"),
            "https://t.me/CB_koolbarbot?startapp=explore_9",
        )

    @override_settings(TELEGRAM_MINI_APP_URL="https://koolbar.example")
    def test_mini_app_link_never_uses_https_website(self) -> None:
        self.assertEqual(
            mini_app_link("request_9"),
            "https://t.me/CB_koolbarbot/app?startapp=request_9",
        )

    @override_settings(TELEGRAM_MINI_APP_URL="https://koolbar.example")
    def test_private_chat_open_button_uses_web_app(self) -> None:
        from notifications.telegram import open_koolbar_markup

        button = open_koolbar_markup("matches")["inline_keyboard"][0][0]
        self.assertEqual(button["web_app"]["url"], "https://koolbar.example/app?startapp=matches")
        self.assertNotIn("url", button)

    def test_send_without_token_is_skipped(self) -> None:
        self.assertFalse(send_telegram_message(94001, "hello"))

    @patch("notifications.services.send_telegram_message", return_value=True)
    def test_notify_new_match_sends_to_owner_only(self, mocked_send) -> None:
        notify_new_match(self.match)
        chats = {call.args[0] for call in mocked_send.call_args_list}
        self.assertEqual(chats, {self.match.owner_request().user.telegram_user_id})
        self.assertEqual(mocked_send.call_count, 1)
        markup = mocked_send.call_args.kwargs["reply_markup"]
        buttons = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row if "callback_data" in btn]
        self.assertIn(f"match:accept:{self.match.pk}", buttons)
        self.assertIn(f"match:reject:{self.match.pk}", buttons)

    @patch("notifications.services.send_telegram_message", return_value=True)
    def test_notify_match_rejected_prompts_owner_to_close(self, mocked_send) -> None:
        notify_match_rejected(self.match)
        chats = {call.args[0] for call in mocked_send.call_args_list}
        self.assertEqual(chats, {94001, 94002})
        owner_call = next(call for call in mocked_send.call_args_list if call.args[0] == 94001)
        buttons = [
            btn["callback_data"]
            for row in owner_call.kwargs["reply_markup"]["inline_keyboard"]
            for btn in row
            if "callback_data" in btn
        ]
        self.assertIn(f"listing:close:{self.match.pk}", buttons)

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
