"""PostHog on the server: nothing personal leaves, and a capture failure never reaches a caller. None of this needs a database."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

import telemetry
from telemetry import capture, capture_user, events, scrub, scrub_text


def on():
    return patch("telemetry.enabled", return_value=True)


def sent(put) -> dict:
    return put.call_args.args[0]


def updates_of(update: dict, bot=events.HOUSING) -> dict:
    with on(), patch.object(telemetry._sender, "put") as put:
        events.capture_update(update, bot)
    put.assert_called_once()
    return sent(put)


def message(**fields) -> dict:
    return {"update_id": 1, "message": {"message_id": 5, "from": {"id": 77, "first_name": "Sara", "username": "sara_tg"}, "chat": {"id": 77, "type": "private"}, **fields}}


class ScrubTests(SimpleTestCase):
    def test_phone_in_latin_and_persian_digits(self) -> None:
        for text in ("call +1 416 555 0199 now", "زنگ بزنید ۰۹۱۲۳۴۵۶۷۸۹", "٠٩١٢٣٤٥٦٧٨٩"):
            self.assertIn("[phone]", scrub_text(text), text)
        self.assertNotIn("555", scrub_text("call +1 416 555 0199 now"))
        self.assertNotIn("۰۹۱۲", scrub_text("زنگ بزنید ۰۹۱۲۳۴۵۶۷۸۹"))

    def test_username_link_jwt_and_init_data(self) -> None:
        self.assertEqual(scrub_text("ask @sara_tg please"), "ask [username] please")
        self.assertEqual(scrub_text("https://t.me/sara_tg/12"), "[telegram-link]")
        self.assertEqual(scrub_text("eyJhbGciOiJI.eyJzdWIiOiIx.abcDEF123"), "[token]")
        cleaned = scrub_text("x?tgWebAppData=query_id%3DAAH&hash=deadbeef")
        self.assertNotIn("query_id%3DAAH", cleaned)
        self.assertNotIn("deadbeef", cleaned)

    def test_banned_keys_are_dropped_whatever_the_value(self) -> None:
        banned = {key: "x" for key in ("phone", "contact_phone", "telegram_username", "first_name", "init_data", "initData", "access_token", "secret", "password", "text", "body", "raw", "message", "cookie")}
        self.assertEqual(scrub({**banned, "listing_id": 4, "text_length": 9, "message_type": "text"}), {"listing_id": 4, "text_length": 9, "message_type": "text"})

    def test_nested_values_are_scrubbed(self) -> None:
        self.assertEqual(scrub({"a": {"phone": "1", "b": ["@sara_tg"]}}), {"a": {"b": ["[username]"]}})


class CaptureTests(SimpleTestCase):
    def test_no_key_is_a_no_op(self) -> None:
        with patch.dict(os.environ, {"POSTHOG_HOST": "https://us.i.posthog.com"}), patch.object(telemetry._sender, "put") as put:
            os.environ.pop("POSTHOG_KEY", None)
            os.environ.pop("PYTEST_CURRENT_TEST", None)
            self.assertFalse(telemetry.enabled())
            capture("x", telegram_id=1)
        put.assert_not_called()

    def test_off_under_pytest_even_with_a_key(self) -> None:
        with patch.dict(os.environ, {"POSTHOG_KEY": "k", "POSTHOG_HOST": "h"}):
            self.assertFalse(telemetry.enabled())

    def test_distinct_id_is_the_mini_apps(self) -> None:
        with on(), patch.object(telemetry._sender, "put") as put:
            capture("x", telegram_id=42, source="bot_housing", side="housing", properties={"a": 1, "phone": "0912"})
        event = sent(put)
        self.assertEqual(event["distinct_id"], "tg_42")
        self.assertEqual(event["properties"]["source"], "bot_housing")
        self.assertEqual(event["properties"]["side"], "housing")
        self.assertNotIn("phone", event["properties"])

    def test_capture_user_reads_the_row(self) -> None:
        with on(), patch.object(telemetry._sender, "put") as put:
            capture_user("x", SimpleNamespace(telegram_user_id=42))
        self.assertEqual(sent(put)["distinct_id"], "tg_42")

    def test_no_telegram_id_makes_no_person(self) -> None:
        with on(), patch.object(telemetry._sender, "put") as put:
            capture("review_cycle", source="job", properties={"reviewed": 3})
        event = sent(put)
        self.assertEqual(event["distinct_id"], "system_job")
        self.assertIs(event["properties"]["$process_person_profile"], False)

    def test_a_raising_sender_does_not_raise(self) -> None:
        with on(), patch.object(telemetry._sender, "put", side_effect=RuntimeError("boom")):
            capture("x", telegram_id=42)
            capture_user("x", SimpleNamespace(telegram_user_id=42))


class UpdateTests(SimpleTestCase):
    def test_command_with_a_start_payload_shape_only(self) -> None:
        event = updates_of(message(text="/start alert_home_ca_toronto_m1100"))
        self.assertEqual(event["event"], "bot_command")
        self.assertEqual(event["distinct_id"], "tg_77")
        props = event["properties"]
        self.assertEqual((props["command"], props["has_payload"], props["payload_kind"]), ("start", True, "alert_home"))
        self.assertNotIn("toronto", json.dumps(event))

    def test_start_payload_kinds_never_echo_the_payload(self) -> None:
        self.assertEqual(events.payload_kind(""), "none")
        self.assertEqual(events.payload_kind("home"), "home")
        self.assertEqual(events.payload_kind("home_ca_toronto_l55_src-chtor"), "home_listing")
        self.assertEqual(events.payload_kind("home_ca_toronto"), "home")
        self.assertEqual(events.payload_kind("Zm9vYmFyc2VjcmV0dG9rZW4"), "other")

    def test_an_unknown_command_is_not_echoed(self) -> None:
        event = updates_of(message(text="/mohammad_bigdeli"))
        self.assertEqual(event["properties"]["command"], "other")
        self.assertNotIn("mohammad", json.dumps(event))

    def test_callback_prefix(self) -> None:
        self.assertEqual(events.callback_prefix("hx:listing:123"), "hx:listing")
        self.assertEqual(events.callback_prefix("v2:c:123"), "v2:c")
        self.assertEqual(events.callback_prefix("v2:fs:areas:ریچموند"), "v2:fs")
        self.assertEqual(events.callback_prefix("ct:5"), "ct")
        self.assertEqual(events.callback_prefix("mn"), "mn")
        self.assertEqual(events.callback_prefix("123:456"), "other")
        self.assertEqual(events.callback_prefix(None), "other")
        event = updates_of({"callback_query": {"id": "1", "from": {"id": 77}, "data": "hx:listing:123"}})
        self.assertEqual((event["event"], event["properties"]["prefix"], event["distinct_id"]), ("bot_button", "hx:listing", "tg_77"))

    def test_keyboard_buttons_by_code(self) -> None:
        from courier.texts import KB_MINE
        from home.botchat import KB_SEARCH
        from home.botv2 import KB

        self.assertEqual(updates_of(message(text=KB_SEARCH))["properties"]["code"], "search")
        self.assertEqual(updates_of(message(text=KB["en"]["saved"]))["properties"]["code"], "saved")
        city = updates_of(message(text=f"📍 تورنتو · {KB['fa']['city']}"))
        self.assertEqual((city["event"], city["properties"]["code"]), ("bot_button", "city"))
        self.assertNotIn("تورنتو", json.dumps(city, ensure_ascii=False))
        self.assertEqual(updates_of(message(text=KB_MINE), events.COURIER)["properties"]["code"], "mine")

    def test_message_types_carry_a_length_and_never_the_text(self) -> None:
        text = "۱ خوابه تا ۲۰۰۰ زنگ بزنید ۰۹۱۲۳۴۵۶۷۸۹"
        event = updates_of(message(text=text))
        self.assertEqual(event["event"], "bot_message")
        self.assertEqual(event["properties"]["message_type"], "text")
        self.assertEqual(event["properties"]["text_length"], len(text))
        self.assertNotIn("خوابه", json.dumps(event, ensure_ascii=False))
        for field, kind in (("photo", "photo"), ("document", "document"), ("location", "location"), ("contact", "contact"), ("voice", "voice"), ("video", "video"), ("sticker", "sticker"), ("dice", "other")):
            self.assertEqual(updates_of(message(**{field: [{"x": 1}]}))["properties"]["message_type"], kind)
        forwarded = updates_of(message(text="hello", forward_origin={"type": "user"}))
        self.assertEqual(forwarded["properties"]["message_type"], "forward")
        captioned = updates_of(message(photo=[{}], caption="secret caption"))
        self.assertEqual(captioned["properties"]["text_length"], 14)
        self.assertNotIn("secret", json.dumps(captioned))

    def test_the_sender_details_never_leave(self) -> None:
        dumped = json.dumps(updates_of(message(text="سلام")))
        self.assertNotIn("Sara", dumped)
        self.assertNotIn("sara_tg", dumped)

    def test_member_blocked_and_unblocked(self) -> None:
        def member(old: str, new: str) -> dict:
            return {"my_chat_member": {"from": {"id": 77}, "old_chat_member": {"status": old}, "new_chat_member": {"status": new}}}

        blocked = updates_of(member("member", "kicked"))
        self.assertEqual((blocked["event"], blocked["properties"]["action"], blocked["distinct_id"]), ("bot_member", "blocked", "tg_77"))
        self.assertEqual(updates_of(member("kicked", "member"))["properties"]["action"], "unblocked")

    def test_anything_else(self) -> None:
        event = updates_of({"update_id": 1, "channel_post": {"text": "x"}})
        self.assertEqual((event["event"], event["properties"]["update_type"]), ("bot_update_other", "channel_post"))
        self.assertEqual(updates_of({"update_id": 1})["properties"]["update_type"], "none")

    def test_a_malformed_update_does_not_raise(self) -> None:
        with on(), patch.object(telemetry._sender, "put"):
            for update in (None, [], "x", {"message": "x"}, {"message": {"chat": None, "from": None}}, {"callback_query": {"from": None}}, {"my_chat_member": {}}):
                events.capture_update(update, events.HOUSING)


@override_settings(BOT_SERVICE_SECRET="bot-secret")
class WebhookTests(SimpleTestCase):
    """A capture that raises changes nothing about what Telegram hears."""

    def courier_webhook(self):
        from api.telegram_webhook import telegram_webhook

        request = APIRequestFactory().post("/api/telegram/webhook/", message(text="/start"), format="json")
        with patch("api.telegram_webhook._secret_ok", return_value=True), patch("api.telegram_webhook._record_bot_start"), patch("api.telegram_webhook._courier_chat") as chat:
            response = telegram_webhook(request)
        chat.assert_called_once()
        return response

    def housing_webhook(self):
        from home.webhook import home_bot_webhook

        request = RequestFactory().post("/api/home/bot/webhook/", json.dumps(message(text="/start")), content_type="application/json")
        with patch("home.webhook.secret_ok", return_value=True), patch("home.webhook.conf.home_enabled", return_value=True), patch("home.botchat.handle_update") as handle:
            response = home_bot_webhook(request)
        handle.assert_called_once()
        return response

    def polling_relay(self):
        from api.bot_views import bot_courier_update

        request = APIRequestFactory().post("/api/bot/courier/update/", message(text="/start"), format="json", HTTP_X_BOT_SECRET="bot-secret")
        with patch("courier.flow.handle_update", return_value=[]):
            return bot_courier_update(request)

    def test_a_raising_capture_still_answers_200(self) -> None:
        for hook in (self.courier_webhook, self.housing_webhook, self.polling_relay):
            with self.subTest(hook.__name__), on(), patch("telemetry.capture", side_effect=RuntimeError("boom")) as raising:
                self.assertEqual(hook().status_code, 200)
                raising.assert_called_once()

    def test_a_raising_sender_still_answers_200(self) -> None:
        for hook in (self.courier_webhook, self.housing_webhook, self.polling_relay):
            with self.subTest(hook.__name__), on(), patch.object(telemetry._sender, "put", side_effect=RuntimeError("boom")):
                self.assertEqual(hook().status_code, 200)

    def test_a_raising_reader_still_answers_200(self) -> None:
        with on(), patch("telemetry.events._describe", side_effect=RuntimeError("boom")):
            self.assertEqual(self.courier_webhook().status_code, 200)
            self.assertEqual(self.housing_webhook().status_code, 200)

    def test_each_entry_tags_its_bot(self) -> None:
        for hook, source, side in ((self.courier_webhook, "bot_courier", "courier"), (self.housing_webhook, "bot_housing", "housing"), (self.polling_relay, "bot_courier", "courier")):
            with self.subTest(hook.__name__), on(), patch.object(telemetry._sender, "put") as put:
                hook()
                props = sent(put)["properties"]
                self.assertEqual((sent(put)["event"], props["source"], props["side"]), ("bot_command", source, side))


class SendFailureTests(SimpleTestCase):
    def test_housing_call_reports_the_code_and_raises_as_before(self) -> None:
        from home import bot

        with on(), patch.object(telemetry._sender, "put") as put, patch("home.bot._call", side_effect=bot.BotError("Forbidden: bot was blocked by Sara", code=403)):
            with self.assertRaises(bot.BotError):
                bot.call("sendMessage", {"chat_id": 77, "text": "private words"})
        event = sent(put)
        self.assertEqual((event["event"], event["distinct_id"], event["properties"]["error_code"], event["properties"]["method"]), ("bot_send_failed", "tg_77", 403, "sendMessage"))
        self.assertNotIn("Sara", json.dumps(event))
        self.assertNotIn("private", json.dumps(event))

    def test_courier_call_reports_the_code_and_answers_as_before(self) -> None:
        from notifications import telegram

        failure = (None, {"ok": False, "error_code": 403, "description": "Forbidden: Sara"})
        with on(), patch.object(telemetry._sender, "put") as put, patch("notifications.telegram._call_detailed", return_value=failure):
            self.assertEqual(telegram.call_telegram_api_detailed("sendMessage", {"chat_id": 77, "text": "private"}), failure)
        event = sent(put)
        self.assertEqual((event["distinct_id"], event["properties"]["source"], event["properties"]["error_code"]), ("tg_77", "bot_courier", 403))
        self.assertNotIn("Sara", json.dumps(event))

    def test_only_sends_count_and_a_channel_is_no_person(self) -> None:
        with on(), patch.object(telemetry._sender, "put") as put:
            for method in ("editMessageText", "answerCallbackQuery", "getChatMember", "sendChatAction"):
                events.capture_send_failure(method, 77, 400, events.HOUSING)
            put.assert_not_called()
            events.capture_send_failure("sendPhoto", "@chamedoon_toronto", 429, events.HOUSING)
        self.assertEqual(sent(put)["distinct_id"], "system_bot_housing")

    def test_a_network_failure_has_code_zero(self) -> None:
        with on(), patch.object(telemetry._sender, "put") as put:
            events.capture_send_failure("sendMessage", -100123, None, events.COURIER)
        self.assertEqual(sent(put)["properties"]["error_code"], 0)


class GateTests(SimpleTestCase):
    def test_reveal_reports_the_result_never_the_contact(self) -> None:
        from home import gate

        found = gate.Reveal(telegram="sara_tg", phone="09123456789", source_url="", gate="member", reveals_left=7)
        with on(), patch.object(telemetry._sender, "put") as put, patch("home.gate._reveal", return_value=found):
            self.assertIs(gate.reveal(SimpleNamespace(telegram_user_id=42), SimpleNamespace(pk=9), source="bot_housing"), found)
        event = sent(put)
        self.assertEqual((event["event"], event["distinct_id"], event["properties"]["gate"], event["properties"]["listing_id"], event["properties"]["source"]), ("contact_revealed", "tg_42", "member", 9, "bot_housing"))
        self.assertNotIn("sara", json.dumps(event))
        self.assertNotIn("0912", json.dumps(event))

    def test_a_refusal_reports_the_reason_and_still_raises(self) -> None:
        from home import gate

        with on(), patch.object(telemetry._sender, "put") as put, patch("home.gate._reveal", side_effect=gate.GateRefused("not_member")):
            with self.assertRaises(gate.GateRefused):
                gate.reveal(SimpleNamespace(telegram_user_id=42), SimpleNamespace(pk=9))
        event = sent(put)
        self.assertEqual((event["event"], event["properties"]["reason"], event["properties"]["source"]), ("gate_denied", "not_member", "api"))


class GuardTests(SimpleTestCase):
    def test_once_a_day_per_person_and_event(self) -> None:
        with on(), patch.object(telemetry._sender, "put") as put, patch.object(events, "_said", set()), patch.object(events, "_said_day", None):
            for _run in range(50):
                events.capture_once_a_day("alert_quiet", 1, properties={"alert_id": 1}, bot=events.HOUSING)
            self.assertEqual(put.call_count, 1)
            events.capture_once_a_day("alert_quiet", 2, properties={}, bot=events.HOUSING)
            events.capture_once_a_day("alert_capped", 1, properties={}, bot=events.HOUSING)
            self.assertEqual(put.call_count, 3)

    def test_an_imported_person_is_not_a_profile(self) -> None:
        from matching.contact import SYNTHETIC_TELEGRAM_USER_ID

        self.assertEqual(events._SYNTHETIC_FROM, SYNTHETIC_TELEGRAM_USER_ID)
        self.assertEqual(events.real_telegram_id(SimpleNamespace(telegram_user_id=42)), 42)
        self.assertIsNone(events.real_telegram_id(SimpleNamespace(telegram_user_id=SYNTHETIC_TELEGRAM_USER_ID + 1)))
        self.assertIsNone(events.real_telegram_id(None))

    def test_only_an_app_post_has_a_person(self) -> None:
        def listing(username: str):
            return SimpleNamespace(post=SimpleNamespace(author_id=42, source=SimpleNamespace(username=username)))

        with on():
            self.assertEqual(events.person_of_listing(listing("app_ca")), 42)
            self.assertIsNone(events.person_of_listing(listing("toronto_rentals")))
            self.assertIsNone(events.person_of_listing(SimpleNamespace()))
