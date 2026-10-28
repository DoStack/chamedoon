"""Admin → Telegram webhooks, the courier_webhook command and the courier webhook's secret check.

Every Telegram call is mocked: `notifications.telegram.call_telegram_api_detailed` for the courier
bot and `home.bot.call` for the housing bot. Nothing here reaches the real Bot API.
"""

from __future__ import annotations

import io
import logging
import re
import time
from unittest.mock import patch

from django.contrib.auth.models import User as StaffUser
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from api import config_check, webhooks
from courier.flow import COMMANDS as COURIER_COMMANDS
from courier.models import CourierSettings
from home.bot import BotError
from home.botchat import COMMANDS as HOUSING_COMMANDS
from home.models import HomeSettings

SENTINEL = "SENTINEL_secret_do_not_leak_9q4w"
COURIER_TOKEN = "111111:COURIER_TOKEN_SENTINEL_x1"
HOUSING_TOKEN = "222222:HOUSING_TOKEN_SENTINEL_y2"
PAGE = "/admin/ops/webhooks/"
COURIER_HOOK = "https://testserver/api/telegram/webhook/"
HOUSING_HOOK = "https://testserver/api/home/bot/webhook/"
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{48}$")

BOTH = {
    "TELEGRAM_BOT_TOKEN": COURIER_TOKEN,
    "TELEGRAM_WEBHOOK_SECRET": "",
    "HOME_BOT_TOKEN": HOUSING_TOKEN,
    "HOME_WEBHOOK_SECRET": "",
}


class FakeCourier:
    """Stands in for notifications.telegram.call_telegram_api_detailed."""

    def __init__(self, results: dict | None = None, errors: dict | None = None) -> None:
        self.results = results or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method, payload, *, timeout=10):
        self.calls.append((method, payload))
        if method in self.errors:
            error = self.errors[method]
            if isinstance(error, Exception):
                raise error
            return None, {"ok": False, "description": error}
        return {"ok": True, "result": self.results.get(method, True)}, {}

    @property
    def methods(self) -> list[str]:
        return [name for name, _payload in self.calls]


class FakeHousing:
    """Stands in for home.bot.call."""

    def __init__(self, results: dict | None = None, errors: dict | None = None) -> None:
        self.results = results or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method, payload=None, *, token=None, timeout=20):
        self.calls.append((method, payload))
        if method in self.errors:
            raise BotError(self.errors[method])
        return self.results.get(method, True)

    @property
    def methods(self) -> list[str]:
        return [name for name, _payload in self.calls]


def old_info(**extra) -> dict:
    return {
        "url": "https://chamedoon-old.vercel.app/api/telegram/webhook/?leak=" + SENTINEL,
        "has_custom_certificate": False,
        "pending_update_count": 7,
        "last_error_date": int(time.time()) - 3 * 3600,
        "last_error_message": "Wrong response from the webhook: 404 Not Found",
        "max_connections": 40,
        "allowed_updates": ["message", "callback_query"],
        **extra,
    }


class LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record) -> None:
        self.lines.append(record.getMessage() + (self.format(record) if record.exc_info else ""))


@override_settings(**BOTH)
class WebhookCase(TestCase):
    def setUp(self) -> None:
        self.staff = StaffUser.objects.create_user("ops", password="pw", is_staff=True, is_superuser=True)
        self.client.force_login(self.staff)

    def patched(self, courier: FakeCourier | None = None, housing: FakeHousing | None = None):
        self.courier = courier or FakeCourier()
        self.housing = housing or FakeHousing()
        return (
            patch("notifications.telegram.call_telegram_api_detailed", self.courier),
            patch("home.bot.call", self.housing),
        )

    def run_page(self, method: str = "get", data: dict | None = None, *, follow: bool = True, courier=None, housing=None):
        one, two = self.patched(courier, housing)
        with one, two:
            if method == "get":
                return self.client.get(PAGE)
            return self.client.post(PAGE, data or {}, follow=follow)

    @staticmethod
    def card(html: str, key: str) -> str:
        start = html.index(f'data-bot="{key}"')
        end = html.find("data-bot=", start + 10)
        return html[start : end if end != -1 else len(html)]

    def save_courier_secret(self, value: str = SENTINEL) -> None:
        row = CourierSettings.load()
        row.webhook_secret = value
        row.save()

    def save_housing_secret(self, value: str = SENTINEL) -> None:
        row = HomeSettings.load()
        row.webhook_secret = value
        row.save()


class StatusPageTests(WebhookCase):
    def test_old_url_is_red_and_this_site_is_green(self) -> None:
        courier = FakeCourier({"getMe": {"username": "Chamed0on_bot"}, "getWebhookInfo": old_info()})
        housing = FakeHousing(
            {
                "getMe": {"username": "chamedoon_home_bot"},
                "getWebhookInfo": {"url": HOUSING_HOOK, "pending_update_count": 0, "max_connections": 40},
            }
        )
        response = self.run_page(courier=courier, housing=housing)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        courier_card, housing_card = self.card(html, "courier"), self.card(html, "housing")
        self.assertIn('data-state="mismatch"', courier_card)
        self.assertIn("chamedoon-old.vercel.app/api/telegram/webhook/", courier_card)
        self.assertIn("404 Not Found", courier_card)
        self.assertIn("hours ago", courier_card)
        self.assertIn("@Chamed0on_bot", courier_card)
        self.assertIn("message, callback_query", courier_card)
        self.assertIn('data-state="match"', housing_card)
        self.assertIn("all (default)", housing_card)
        # Host + path only: the query string never reaches the page.
        self.assertNotIn(SENTINEL, html)
        self.assertNotIn("leak=", html)
        self.assertEqual(courier.methods, ["getMe", "getWebhookInfo"])
        self.assertEqual(housing.methods, ["getMe", "getWebhookInfo"])

    def test_unset_webhook_is_red(self) -> None:
        housing = FakeHousing({"getWebhookInfo": {"url": "", "pending_update_count": 0}})
        html = self.run_page(housing=housing).content.decode()
        self.assertIn("not set", self.card(html, "housing"))
        self.assertIn('data-state="mismatch"', self.card(html, "housing"))

    def test_telegram_errors_are_shown_not_raised(self) -> None:
        courier = FakeCourier(errors={"getWebhookInfo": "Unauthorized"})
        housing = FakeHousing(errors={"getMe": "Telegram unreachable: timed out"})
        response = self.run_page(courier=courier, housing=housing)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('data-state="info-error"', self.card(html, "courier"))
        self.assertIn("Unauthorized", self.card(html, "courier"))
        self.assertIn("timed out", self.card(html, "housing"))

    def test_unexpected_exception_is_shown_with_the_token_scrubbed(self) -> None:
        courier = FakeCourier(errors={"getMe": RuntimeError(f"boom bot{COURIER_TOKEN}/getMe")})
        response = self.run_page(courier=courier)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("RuntimeError: boom", html)
        self.assertNotIn(COURIER_TOKEN, html)

    @override_settings(TELEGRAM_BOT_TOKEN="", HOME_BOT_TOKEN="")
    def test_missing_tokens_make_no_calls(self) -> None:
        html = self.run_page().content.decode()
        self.assertIn('data-state="token-missing"', self.card(html, "courier"))
        self.assertIn('data-state="token-missing"', self.card(html, "housing"))
        self.assertEqual(self.courier.calls, [])
        self.assertEqual(self.housing.calls, [])

    def test_sidebar_links_the_page(self) -> None:
        html = self.run_page().content.decode()
        self.assertIn("Telegram webhooks", html)
        self.assertIn(PAGE, html)


class SetWebhookTests(WebhookCase):
    def test_courier_set_sends_url_secret_and_updates(self) -> None:
        self.save_courier_secret()
        response = self.run_page("post", {"bot": "courier", "action": "set"})
        self.assertEqual(self.courier.methods[:2], ["setWebhook", "setMyCommands"])
        payload = self.courier.calls[0][1]
        self.assertEqual(payload["url"], COURIER_HOOK)
        self.assertEqual(payload["secret_token"], SENTINEL)
        self.assertEqual(payload["allowed_updates"], ["message", "callback_query"])
        self.assertIs(payload["drop_pending_updates"], False)
        self.assertEqual(self.courier.calls[1][1], {"commands": COURIER_COMMANDS})
        html = response.content.decode()
        self.assertIn("setWebhook: ok", html)
        self.assertNotIn(SENTINEL, html)
        self.assertEqual(self.housing.calls[:0], [])

    def test_env_secret_is_used_when_settings_row_is_blank(self) -> None:
        with self.settings(TELEGRAM_WEBHOOK_SECRET="env-secret-value"):
            self.run_page("post", {"bot": "courier", "action": "set"})
        self.assertEqual(self.courier.calls[0][1]["secret_token"], "env-secret-value")

    def test_housing_set_uses_home_bot_and_its_commands(self) -> None:
        self.save_housing_secret()
        self.run_page("post", {"bot": "housing", "action": "set"})
        self.assertEqual(self.housing.methods[:2], ["setWebhook", "setMyCommands"])
        payload = self.housing.calls[0][1]
        self.assertEqual(payload["url"], HOUSING_HOOK)
        self.assertEqual(payload["secret_token"], SENTINEL)
        self.assertEqual(payload["allowed_updates"], ["message", "callback_query"])
        self.assertIs(payload["drop_pending_updates"], False)
        self.assertEqual(self.housing.calls[1][1], {"commands": HOUSING_COMMANDS})
        self.assertNotIn("setWebhook", self.courier.methods)

    def test_set_without_a_secret_refuses_and_calls_nothing(self) -> None:
        html = self.run_page("post", {"bot": "courier", "action": "set"}).content.decode()
        self.assertIn("no webhook secret yet", html)
        self.assertNotIn("setWebhook", self.courier.methods)

    def test_failed_set_webhook_is_reported(self) -> None:
        self.save_courier_secret()
        courier = FakeCourier(errors={"setWebhook": "Bad Request: bad webhook"})
        html = self.run_page("post", {"bot": "courier", "action": "set"}, courier=courier).content.decode()
        self.assertIn("setWebhook: failed (Bad Request: bad webhook)", html)
        self.assertNotIn("setMyCommands", courier.methods)

    def test_failed_commands_still_count_the_webhook_as_set(self) -> None:
        self.save_courier_secret()
        outcome = None
        courier = FakeCourier(errors={"setMyCommands": "Too Many Requests"})
        with patch("notifications.telegram.call_telegram_api_detailed", courier):
            outcome = webhooks.set_webhook("courier", "https://testserver")
        self.assertTrue(outcome.ok)
        self.assertIn("setMyCommands: failed", outcome.summary)

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_actions_without_token_refuse(self) -> None:
        for action in ("set", "generate"):
            outcome = webhooks.set_webhook("courier", "https://x") if action == "set" else webhooks.generate_secret("courier", "https://x")
            self.assertFalse(outcome.ok)
            self.assertIn("token missing", outcome.summary)
        self.assertFalse(webhooks.delete_webhook("courier").ok)
        self.assertEqual(CourierSettings.load().webhook_secret, "")

    def test_unknown_bot_or_action(self) -> None:
        html = self.run_page("post", {"bot": "nope", "action": "set"}).content.decode()
        self.assertIn("Unknown bot or action.", html)
        html = self.run_page("post", {"bot": "courier", "action": "explode"}).content.decode()
        self.assertIn("Unknown bot or action.", html)
        with self.assertRaises(webhooks.WebhookError):
            webhooks.get_bot("nope")


class GenerateSecretTests(WebhookCase):
    def test_courier_generate_saves_and_resets_the_webhook(self) -> None:
        self.save_courier_secret("old-secret-value")
        html = self.run_page("post", {"bot": "courier", "action": "generate"}).content.decode()
        saved = CourierSettings.load().webhook_secret
        self.assertRegex(saved, SECRET_RE)
        self.assertNotEqual(saved, "old-secret-value")
        self.assertEqual(self.courier.calls[0][0], "setWebhook")
        self.assertEqual(self.courier.calls[0][1]["secret_token"], saved)
        self.assertNotIn(saved, html)
        self.assertIn("Courier bot: setWebhook: ok", html)

    def test_housing_generate_saves_and_resets_the_webhook(self) -> None:
        html = self.run_page("post", {"bot": "housing", "action": "generate"}).content.decode()
        saved = HomeSettings.load().webhook_secret
        self.assertRegex(saved, SECRET_RE)
        self.assertEqual(self.housing.calls[0], ("setWebhook", webhooks.set_payload(webhooks.BOTS["housing"], "https://testserver", saved)))
        self.assertNotIn(saved, html)

    def test_generate_reports_when_telegram_refuses(self) -> None:
        housing = FakeHousing(errors={"setWebhook": "Unauthorized"})
        html = self.run_page("post", {"bot": "housing", "action": "generate"}, housing=housing).content.decode()
        self.assertIn("new secret saved, but setting the webhook failed", html)
        self.assertRegex(HomeSettings.load().webhook_secret, SECRET_RE)

    def test_secrets_are_random(self) -> None:
        values = {webhooks.new_secret() for _ in range(20)}
        self.assertEqual(len(values), 20)
        self.assertTrue(all(SECRET_RE.match(value) for value in values))


class DeleteWebhookTests(WebhookCase):
    def test_delete_asks_first(self) -> None:
        response = self.run_page("post", {"bot": "courier", "action": "delete"})
        html = response.content.decode()
        self.assertIn("Yes, delete webhook", html)
        self.assertEqual(self.courier.calls, [])

    def test_delete_after_confirm(self) -> None:
        html = self.run_page("post", {"bot": "housing", "action": "delete", "confirm": "yes"}).content.decode()
        self.assertEqual(self.housing.calls[0], ("deleteWebhook", {"drop_pending_updates": False}))
        self.assertNotIn("setWebhook", self.housing.methods)
        self.assertIn("Housing bot: deleteWebhook: ok", html)


class NoSecretLeakTests(WebhookCase):
    def test_secret_and_tokens_never_in_responses_or_logs(self) -> None:
        self.save_courier_secret()
        self.save_housing_secret()
        capture = LogCapture()
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(capture)
        root.setLevel(logging.DEBUG)
        bodies = []
        try:
            courier = FakeCourier({"getWebhookInfo": old_info()}, errors={"setMyCommands": "Too Many Requests"})
            housing = FakeHousing({"getWebhookInfo": {"url": HOUSING_HOOK}}, errors={"deleteWebhook": "Unauthorized"})
            bodies.append(self.run_page(courier=courier, housing=housing).content.decode())
            for bot in ("courier", "housing"):
                for data in (
                    {"action": "set"},
                    {"action": "delete"},
                    {"action": "delete", "confirm": "yes"},
                ):
                    bodies.append(self.run_page("post", {"bot": bot, **data}, courier=courier, housing=housing).content.decode())
            new = []
            for bot in ("courier", "housing"):
                bodies.append(self.run_page("post", {"bot": bot, "action": "generate"}, courier=courier, housing=housing).content.decode())
            new = [CourierSettings.load().webhook_secret, HomeSettings.load().webhook_secret]
            out = io.StringIO()
            with patch("notifications.telegram.call_telegram_api_detailed", courier):
                call_command("courier_webhook", "--url", "https://example.test", stdout=out)
                call_command("courier_webhook", "--url", "https://example.test", "--apply", stdout=out)
                call_command("courier_webhook", "--info", stdout=out)
            bodies.append(out.getvalue())
            bodies.append(self.client.get("/admin/courier/couriersettings/1/change/").content.decode())
        finally:
            root.removeHandler(capture)
            root.setLevel(old_level)
        text = "\n".join(bodies + capture.lines)
        for value in [SENTINEL, COURIER_TOKEN, HOUSING_TOKEN, *new]:
            self.assertNotIn(value, text)


class AccessTests(WebhookCase):
    def test_anonymous_and_non_staff_are_denied(self) -> None:
        self.client.logout()
        response = self.run_page()
        self.assertEqual(response.status_code, 302)
        plain = StaffUser.objects.create_user("plain", password="pw", is_staff=False)
        self.client.force_login(plain)
        self.assertEqual(self.run_page().status_code, 302)
        self.run_page("post", {"bot": "courier", "action": "generate"}, follow=False)
        self.assertEqual(self.courier.calls, [])
        self.assertEqual(CourierSettings.load().webhook_secret, "")


@override_settings(TELEGRAM_BOT_TOKEN=COURIER_TOKEN)
class CourierWebhookSecretTests(TestCase):
    def post(self, secret: str | None = None):
        headers = {"HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN": secret} if secret is not None else {}
        with patch("notifications.telegram.call_telegram_api") as mocked:
            response = self.client.post(
                "/api/telegram/webhook/",
                {"update_id": 1, "message": {"text": "hi", "chat": {"id": 5, "type": "private"}}},
                content_type="application/json",
                **headers,
            )
        mocked.assert_not_called()
        return response

    @override_settings(TELEGRAM_WEBHOOK_SECRET="")
    def test_no_secret_configured_refuses_everything(self) -> None:
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.post("").status_code, 403)
        self.assertEqual(self.post("anything").status_code, 403)

    @override_settings(TELEGRAM_WEBHOOK_SECRET="")
    def test_settings_row_secret(self) -> None:
        row = CourierSettings.load()
        row.webhook_secret = SENTINEL
        row.save()
        self.assertEqual(self.post(SENTINEL).status_code, 200)
        self.assertEqual(self.post("wrong").status_code, 403)
        self.assertEqual(self.post().status_code, 403)

    @override_settings(TELEGRAM_WEBHOOK_SECRET="env-secret")
    def test_env_secret_and_row_wins(self) -> None:
        self.assertEqual(self.post("env-secret").status_code, 200)
        row = CourierSettings.load()
        row.webhook_secret = SENTINEL
        row.save()
        self.assertEqual(self.post("env-secret").status_code, 403)
        self.assertEqual(self.post(SENTINEL).status_code, 200)

    @override_settings(TELEGRAM_WEBHOOK_SECRET="")
    def test_health_counts_the_row_secret(self) -> None:
        feature = next(f for f in config_check.config_report() if f.key == "courier_bot")
        self.assertIn("TELEGRAM_WEBHOOK_SECRET", feature.missing)
        row = CourierSettings.load()
        row.webhook_secret = SENTINEL
        row.save()
        feature = next(f for f in config_check.config_report() if f.key == "courier_bot")
        self.assertNotIn("TELEGRAM_WEBHOOK_SECRET", feature.missing)


@override_settings(TELEGRAM_BOT_TOKEN=COURIER_TOKEN, TELEGRAM_WEBHOOK_SECRET="")
class CourierWebhookCommandTests(TestCase):
    def setUp(self) -> None:
        row = CourierSettings.load()
        row.webhook_secret = SENTINEL
        row.save()
        self.fake = FakeCourier({"getWebhookInfo": old_info()})

    def run_command(self, *args) -> str:
        out = io.StringIO()
        with patch("notifications.telegram.call_telegram_api_detailed", self.fake):
            call_command("courier_webhook", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_masks_the_secret_and_sends_nothing(self) -> None:
        text = self.run_command("--url", "https://example.test")
        self.assertIn("https://example.test/api/telegram/webhook/", text)
        self.assertIn("(set)", text)
        self.assertIn("Dry run", text)
        self.assertNotIn(SENTINEL, text)
        self.assertEqual(self.fake.calls, [])

    def test_apply_sets_the_webhook(self) -> None:
        self.run_command("--url", "https://example.test/", "--apply")
        self.assertEqual(self.fake.methods, ["setWebhook", "setMyCommands"])
        self.assertEqual(self.fake.calls[0][1]["url"], "https://example.test/api/telegram/webhook/")
        self.assertEqual(self.fake.calls[0][1]["secret_token"], SENTINEL)

    def test_apply_failure_raises(self) -> None:
        self.fake = FakeCourier(errors={"setWebhook": "Unauthorized"})
        with self.assertRaises(CommandError):
            self.run_command("--url", "https://example.test", "--apply")

    def test_info_shows_host_and_path_only(self) -> None:
        text = self.run_command("--info")
        self.assertIn("chamedoon-old.vercel.app/api/telegram/webhook/", text)
        self.assertNotIn(SENTINEL, text)
        self.assertEqual(self.fake.methods, ["getWebhookInfo"])

    def test_delete_is_a_dry_run_without_apply(self) -> None:
        self.assertIn("Dry run", self.run_command("--delete"))
        self.assertEqual(self.fake.calls, [])
        self.run_command("--delete", "--apply")
        self.assertEqual(self.fake.calls, [("deleteWebhook", {"drop_pending_updates": False})])

    def test_needs_https_url_and_a_secret(self) -> None:
        with self.assertRaises(CommandError):
            self.run_command("--url", "http://example.test")
        row = CourierSettings.load()
        row.webhook_secret = ""
        row.save()
        with self.assertRaises(CommandError):
            self.run_command("--url", "https://example.test", "--apply")
        self.assertEqual(self.fake.calls, [])

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_needs_a_token(self) -> None:
        with self.assertRaises(CommandError):
            self.run_command("--url", "https://example.test", "--apply")
        with self.assertRaises(CommandError):
            self.run_command("--info")


class HelperTests(TestCase):
    def test_site_base_forces_https(self) -> None:
        self.assertEqual(webhooks.site_base(url="http://chamedoon.example/x"), "https://chamedoon.example")
        self.assertEqual(webhooks.site_base(url="chamedoon.example"), "https://chamedoon.example")
        with self.settings(SITE_URL="https://site.example/"):
            self.assertEqual(webhooks.site_base(), "https://site.example")
        with self.assertRaises(webhooks.WebhookError):
            webhooks.site_base()
        with self.assertRaises(webhooks.WebhookError):
            webhooks.site_base(url="https://")

    def test_url_helpers(self) -> None:
        self.assertEqual(webhooks.display_url("https://a.example/p/?secret=x"), "a.example/p/")
        self.assertEqual(webhooks.display_url(""), "")
        self.assertTrue(webhooks.same_url("https://A.example/p/", "https://a.example/p/"))
        self.assertFalse(webhooks.same_url("https://a.example/p/?x=1", "https://a.example/p/"))
        self.assertFalse(webhooks.same_url("http://a.example/p/", "https://a.example/p/"))
        self.assertFalse(webhooks.same_url("", "https://a.example/p/"))

    def test_scrub_and_mask(self) -> None:
        self.assertEqual(webhooks.scrub("x TOKEN123 y", "TOKEN123", "", "ab"), "x *** y")
        self.assertEqual(webhooks.masked({"secret_token": ""})["secret_token"], "(MISSING)")
        self.assertEqual(webhooks.masked({"url": "u"}), {"url": "u"})
