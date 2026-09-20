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
        self.assertIn("You have a new match", text)
        self.assertIn("Tehran → Toronto", text)
        self.assertIn("September 7", text)
        self.assertIn("September 10", text)
        self.assertIn("September 1–15", text)
        self.assertIn("Ali is a match", text)

    def test_accepted_and_connected_copy(self) -> None:
        self.assertIn("Your request has been accepted", match_accepted_text())
        text = connected_text(self.match, self.demand_user)
        self.assertIn("You are connected", text)
        self.assertIn("Telegram ID: 94002", text)
        self.assertIn("@ali_bot", text)
        self.assertIn("https://t.me/ali_bot", text)
        self.assertIn("من یه بار دارم", text)
        supply_text = connected_text(self.match, self.supply_user)
        self.assertIn("من ظرفیت دارم", supply_text)
        self.assertIn("Sara", supply_text)

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

    @override_settings(TELEGRAM_BOT_TOKEN="tok", TELEGRAM_MINI_APP_URL="https://chamedoon.example")
    @patch("notifications.telegram.call_telegram_api", side_effect=[None, {"ok": True}])
    def test_send_retries_glass_buttons_as_url_buttons(self, mocked_api) -> None:
        from notifications.telegram import open_koolbar_markup

        markup = open_koolbar_markup("matches")
        self.assertTrue(send_telegram_message(94001, "hello", reply_markup=markup))
        self.assertEqual(mocked_api.call_count, 2)
        first = mocked_api.call_args_list[0].args[1]
        second = mocked_api.call_args_list[1].args[1]
        self.assertIn("web_app", first["reply_markup"]["inline_keyboard"][0][0])
        self.assertNotIn("web_app", second["reply_markup"]["inline_keyboard"][0][0])
        self.assertTrue(
            second["reply_markup"]["inline_keyboard"][0][0]["url"].startswith(
                "https://t.me/CB_koolbarbot/app?startapp=matches"
            )
        )

    @patch("notifications.services.send_telegram_message", return_value=True)
    def test_notify_new_match_sends_telegram_to_both_users(self, mocked_send) -> None:
        self.demand_user.telegram_username = "sara_send"
        self.demand_user.save(update_fields=["telegram_username"])
        notify_new_match(self.match)
        chats = {call.args[0] for call in mocked_send.call_args_list}
        self.assertEqual(chats, {94001, 94002})
        self.assertEqual(mocked_send.call_count, 2)
        demand_call = next(call for call in mocked_send.call_args_list if call.args[0] == 94001)
        demand_urls = [btn["url"] for row in demand_call.kwargs["reply_markup"]["inline_keyboard"] for btn in row]
        self.assertTrue(any(url.startswith("https://t.me/ali_bot") for url in demand_urls))
        self.assertNotIn(
            f"match:accept:{self.match.pk}",
            [btn.get("callback_data") for row in demand_call.kwargs["reply_markup"]["inline_keyboard"] for btn in row],
        )

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
    def test_notify_connected_skips_synthetic_imported_ids(self, mocked_send) -> None:
        from matching.contact import SYNTHETIC_TELEGRAM_USER_ID

        self.supply_user.telegram_user_id = SYNTHETIC_TELEGRAM_USER_ID + 173707118
        self.supply_user.telegram_username = "Mjvr13"
        self.supply_user.first_name = "Mjvr13"
        self.supply_user.save(update_fields=["telegram_user_id", "telegram_username", "first_name"])
        notify_connected(self.match)
        chats = {call.args[0] for call in mocked_send.call_args_list}
        self.assertEqual(chats, {94001})
        demand_call = mocked_send.call_args_list[0]
        self.assertIn("Match accepted.", demand_call.args[1])
        self.assertIn("You are connected", demand_call.args[1])
        self.assertIn("@Mjvr13", demand_call.args[1])
        self.assertIn("https://t.me/Mjvr13", demand_call.args[1])
        self.assertIn("از چمدون به شما پیام میدم", demand_call.args[1])
        labels = [
            btn["text"]
            for row in demand_call.kwargs["reply_markup"]["inline_keyboard"]
            for btn in row
        ]
        self.assertIn("Open Chamedoon", labels)
        self.assertIn("Message on Telegram", labels)

    @patch("notifications.services.send_telegram_message", return_value=True)
    def test_notify_connected_sends_to_both_users(self, mocked_send) -> None:
        self.demand_user.telegram_username = "sara_send"
        self.demand_user.save(update_fields=["telegram_username"])
        notify_connected(self.match)
        chats = {call.args[0] for call in mocked_send.call_args_list}
        self.assertEqual(chats, {94001, 94002})
        demand_call = next(call for call in mocked_send.call_args_list if call.args[0] == 94001)
        self.assertIn("@ali_bot", demand_call.args[1])
        self.assertIn("من یه بار دارم", demand_call.args[1])
        demand_urls = [btn["url"] for row in demand_call.kwargs["reply_markup"]["inline_keyboard"] for btn in row]
        demand_dm = next(url for url in demand_urls if url.startswith("https://t.me/ali_bot"))
        demand_draft = unquote(parse_qs(urlparse(demand_dm).query)["text"][0])
        self.assertIn("من یه بار دارم", demand_draft)
        self.assertIn("👕 لباس", demand_draft)
        self.assertIn("Ali", demand_draft)

        supply_call = next(call for call in mocked_send.call_args_list if call.args[0] == 94002)
        self.assertIn("من ظرفیت دارم", supply_call.args[1])
        self.assertIn("Sara", supply_call.args[1])
        supply_urls = [btn["url"] for row in supply_call.kwargs["reply_markup"]["inline_keyboard"] for btn in row]
        supply_dm = next(url for url in supply_urls if url.startswith("https://t.me/sara_send"))
        supply_draft = unquote(parse_qs(urlparse(supply_dm).query)["text"][0])
        self.assertIn("من ظرفیت دارم", supply_draft)
        self.assertIn("می‌تونم ببرم", supply_draft)
        self.assertIn("Sara", supply_draft)
