"""Browser sign-in: «ورود با تلگرام» through the housing bot (webhook view), email codes, the cookie session, merges."""

from __future__ import annotations

import re
from datetime import timedelta
from unittest.mock import patch

from django.core import mail as django_mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from home.models import HomeAlert, HomeSaved, HomeSettings
from tests.helpers import bearer_auth, make_user
from users.identity import EMAIL_ID_MAX, EMAIL_ID_MIN, is_email_id, new_email_id, refuses
from users.models import User
from web import accounts
from web.models import EmailLoginCode, WebLoginCode, WebLoginStatus

HOOK = "/api/home/bot/webhook/"
SECRET = "hook-secret-123"
TG_A = {"id": 7_000_001, "first_name": "Ali", "username": "ali_a", "is_bot": False}
TG_B = {"id": 7_000_002, "first_name": "Bita", "is_bot": False}


class Sent:
    """Records every Bot API call instead of sending it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method, payload=None, **_kwargs):
        self.calls.append((method, payload or {}))
        return {"message_id": 1} if method == "sendMessage" else True

    def last(self, method: str) -> dict:
        return [payload for name, payload in self.calls if name == method][-1]

    def numbers(self) -> list[str]:
        rows = self.last("sendMessage")["reply_markup"]["inline_keyboard"]
        return [button["callback_data"] for row in rows for button in row if not button["callback_data"].endswith(":x")]


class Base(TestCase):
    def setUp(self) -> None:
        cache.clear()
        row = HomeSettings.load()
        row.web_login_on = True
        row.email_login_on = True
        row.miniapp_v2 = True
        row.webhook_secret = SECRET
        row.bot_username = "chamedoon_home_bot"
        row.save()
        self.sent = Sent()
        patcher = patch("home.bot.call", side_effect=self.sent)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, client: Client | None = None):
        response = (client or self.client).post("/api/v1/auth/web/start/", {}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def poll(self, token: str, client: Client | None = None):
        return (client or self.client).get("/api/v1/auth/web/poll/", HTTP_X_POLL_TOKEN=token)

    def hook(self, update: dict):
        response = self.client.post(HOOK, update, content_type="application/json", HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SECRET)
        self.assertEqual(response.status_code, 200)

    def open_in_bot(self, code: str, who: dict = TG_A):
        self.hook({"update_id": 1, "message": {"message_id": 5, "chat": {"id": who["id"], "type": "private"}, "from": who, "text": f"/start weblogin_{code}"}})

    def tap(self, data: str, who: dict = TG_A):
        self.hook({"update_id": 2, "callback_query": {"id": "cb1", "from": who, "data": data, "message": {"message_id": 9, "chat": {"id": who["id"], "type": "private"}}}})

    def right(self, match: int) -> str:
        return next(d for d in self.sent.numbers() if d.endswith(f":{match}"))

    def wrong(self, match: int) -> str:
        return next(d for d in self.sent.numbers() if not d.endswith(f":{match}"))


class StartTests(Base):
    def test_start_shape_and_secrets(self) -> None:
        body = self.start()
        self.assertRegex(body["code"], r"^[A-Za-z0-9_-]{32}$")
        self.assertTrue(10 <= body["match"] <= 99)
        self.assertEqual(body["bot_url"], f"https://t.me/chamedoon_home_bot?start=weblogin_{body['code']}")
        self.assertLessEqual(len(f"weblogin_{body['code']}"), 64)
        self.assertNotIn(body["poll_token"], body["bot_url"])
        self.assertEqual(body["expires_in"], 300)
        row = WebLoginCode.objects.get()
        self.assertNotIn(body["code"], (row.code_hash, row.poll_hash))
        self.assertNotIn(body["poll_token"], (row.code_hash, row.poll_hash))

    def test_flag_off(self) -> None:
        HomeSettings.objects.update(web_login_on=False)
        response = self.client.post("/api/v1/auth/web/start/", {}, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "feature_off")
        self.assertFalse(self.client.get("/api/v1/auth/web/session/").json()["web_login"])

    def test_needs_json(self) -> None:
        self.assertEqual(self.client.post("/api/v1/auth/web/start/", {"a": "b"}).status_code, 400)

    def test_rate_limited_per_ip(self) -> None:
        for _ in range(10):
            self.start()
        response = self.client.post("/api/v1/auth/web/start/", {}, content_type="application/json")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_poll_token_only_in_header(self) -> None:
        body = self.start()
        self.assertEqual(self.client.get(f"/api/v1/auth/web/poll/?poll_token={body['poll_token']}").status_code, 400)


class BotFlowTests(Base):
    def test_happy_path(self) -> None:
        body = self.start()
        self.assertEqual(self.poll(body["poll_token"]).json(), {"status": "pending", "opened": False})
        self.open_in_bot(body["code"])
        ask = self.sent.last("sendMessage")
        self.assertIn("عددی که روی صفحه می‌بینید را انتخاب کنید", ask["text"])
        self.assertEqual(ask["chat_id"], TG_A["id"])
        self.assertEqual(len(self.sent.numbers()), 6)
        self.assertEqual(self.poll(body["poll_token"]).json()["opened"], True)
        self.tap(self.right(body["match"]))
        self.assertIn("ورود تأیید شد", self.sent.last("editMessageText")["text"])
        response = self.poll(body["poll_token"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["user"]["telegram_user_id"], TG_A["id"])
        cookie = response.cookies["chm_web"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        user = User.objects.get(telegram_user_id=TG_A["id"])
        self.assertEqual((user.first_name, user.telegram_username), ("Ali", "ali_a"))
        # The cookie now signs API calls, as the Mini App's token does.
        self.assertEqual(self.client.get("/api/v1/me/prefs/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/web/session/").json()["user"]["id"], user.pk)
        # The courier API takes the same identity.
        self.assertNotEqual(self.client.get("/api/v1/courier/me/requests/").status_code, 401)

    def test_replay_is_refused(self) -> None:
        body = self.start()
        self.open_in_bot(body["code"])
        self.tap(self.right(body["match"]))
        self.assertEqual(self.poll(body["poll_token"]).status_code, 200)
        other = Client()
        response = self.poll(body["poll_token"], other)
        self.assertEqual(response.status_code, 410)
        self.assertNotIn("chm_web", response.cookies)
        self.open_in_bot(body["code"])  # the link again in the bot
        self.assertIn("منقضی", self.sent.last("sendMessage")["text"])

    def test_wrong_choice_kills(self) -> None:
        body = self.start()
        self.open_in_bot(body["code"])
        self.tap(self.wrong(body["match"]))
        self.assertIn("درست نبود", self.sent.last("editMessageText")["text"])
        self.assertEqual(self.poll(body["poll_token"]).json()["status"], "denied")
        self.tap(self.right(body["match"]))  # too late: the code is dead
        self.assertEqual(self.poll(body["poll_token"]).status_code, 410)
        self.assertFalse(User.objects.filter(telegram_user_id=TG_A["id"]).exists())

    def test_not_me_kills(self) -> None:
        body = self.start()
        self.open_in_bot(body["code"])
        self.tap(f"wl:{WebLoginCode.objects.get().pk}:x")
        self.assertEqual(self.poll(body["poll_token"]).json()["status"], "denied")

    def test_expiry_kills(self) -> None:
        body = self.start()
        self.open_in_bot(body["code"])
        WebLoginCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.tap(self.right(body["match"]))
        self.assertIn("منقضی", self.sent.last("editMessageText")["text"])
        self.assertEqual(self.poll(body["poll_token"]).json()["status"], "denied")
        self.assertEqual(WebLoginCode.objects.get().status, WebLoginStatus.DEAD)

    def test_expired_before_bot(self) -> None:
        body = self.start()
        WebLoginCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.poll(body["poll_token"]).json()["status"], "expired")
        self.open_in_bot(body["code"])
        self.assertIn("منقضی", self.sent.last("sendMessage")["text"])

    def test_confirmed_by_a_cannot_be_redeemed_without_poll_token(self) -> None:
        body = self.start()
        self.open_in_bot(body["code"])
        self.tap(self.right(body["match"]))
        attacker = Client()
        self.assertEqual(self.poll("guess-" + body["code"], attacker).status_code, 404)
        self.assertEqual(self.poll(body["code"], attacker).status_code, 404)  # the code itself is not the poll token
        self.assertEqual(attacker.get("/api/v1/auth/web/session/").json()["user"], None)
        self.assertEqual(self.poll(body["poll_token"]).json()["status"], "ok")

    def test_second_account_opening_the_link_kills_it(self) -> None:
        body = self.start()
        self.open_in_bot(body["code"], TG_A)
        self.open_in_bot(body["code"], TG_B)
        self.assertIn("منقضی", self.sent.last("sendMessage")["text"])
        self.assertEqual(self.poll(body["poll_token"]).json()["status"], "denied")

    def test_someone_else_cannot_tap_for_a(self) -> None:
        body = self.start()
        self.open_in_bot(body["code"], TG_A)
        self.tap(self.right(body["match"]), TG_B)
        self.assertEqual(self.poll(body["poll_token"]).json()["status"], "pending")

    def test_rate_limited_per_telegram_user(self) -> None:
        for _ in range(5):
            body = self.start(Client(REMOTE_ADDR="10.0.0.%d" % (len(self.sent.calls) % 200)))
            self.open_in_bot(body["code"])
        body = self.start(Client(REMOTE_ADDR="10.9.9.9"))
        self.open_in_bot(body["code"])
        self.assertIn("زیاد", self.sent.last("sendMessage")["text"])

    def test_blocked_person(self) -> None:
        make_user(telegram_user_id=TG_A["id"], first_name="Ali", is_active=False)
        body = self.start()
        self.open_in_bot(body["code"])
        self.tap(self.right(body["match"]))
        self.assertEqual(self.poll(body["poll_token"]).status_code, 410)

    def test_other_start_payloads_reach_the_bot(self) -> None:
        with patch("home.botchat.handle_message") as rest:
            self.hook({"update_id": 3, "message": {"message_id": 5, "chat": {"id": 1, "type": "private"}, "from": TG_A, "text": "/start alert_home_ca_toronto"}})
        rest.assert_called_once()


class CookieSessionTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.user = make_user(telegram_user_id=880077, first_name="Sara")
        self.browser = Client(enforce_csrf_checks=True)
        body = self.start(self.browser)
        self.open_in_bot(body["code"], {"id": 880077, "first_name": "Sara"})
        self.tap(self.right(body["match"]), {"id": 880077, "first_name": "Sara"})
        self.assertEqual(self.poll(body["poll_token"], self.browser).status_code, 200)

    def test_write_needs_csrf(self) -> None:
        response = self.browser.patch("/api/v1/me/prefs/", {"locale": "en"}, content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "auth_invalid")
        token = self.browser.cookies["csrftoken"].value
        response = self.browser.patch("/api/v1/me/prefs/", {"locale": "en"}, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200, response.content)

    def test_bearer_wins_over_cookie(self) -> None:
        other = make_user(telegram_user_id=880078, first_name="Omid")
        with patch("web.session.cookie_user") as cookie:
            response = self.browser.get("/api/v1/me/prefs/", **bearer_auth(other))
        self.assertEqual(response.status_code, 200)
        cookie.assert_not_called()

    def test_logout_needs_csrf_and_clears(self) -> None:
        self.assertEqual(self.browser.post("/api/v1/auth/web/logout/").status_code, 403)
        token = self.browser.cookies["csrftoken"].value
        response = self.browser.post("/api/v1/auth/web/logout/", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies["chm_web"].value, "")
        self.assertIsNone(self.browser.get("/api/v1/auth/web/session/").json()["user"])
        self.assertEqual(self.browser.get("/api/v1/me/prefs/").status_code, 401)

    def test_logout_voids_a_copied_cookie(self) -> None:
        import time

        copy = Client()
        copy.cookies["chm_web"] = self.browser.cookies["chm_web"].value
        self.assertEqual(copy.get("/api/v1/me/prefs/").status_code, 200)
        time.sleep(1.1)
        token = self.browser.cookies["csrftoken"].value
        self.browser.post("/api/v1/auth/web/logout/", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(copy.get("/api/v1/me/prefs/").status_code, 401)

    def test_deactivated_user_cookie_is_dropped(self) -> None:
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertIsNone(self.browser.get("/api/v1/auth/web/session/").json()["user"])


class EmailTests(Base):
    def send(self, email="Neda@Example.com", client=None):
        return (client or self.client).post("/api/v1/auth/email/start/", {"email": email}, content_type="application/json")

    def code(self) -> str:
        return re.search(r"\d{6}", django_mail.outbox[-1].body).group(0)

    def verify(self, code: str, email="neda@example.com", client=None):
        return (client or self.client).post("/api/v1/auth/email/verify/", {"email": email, "code": code}, content_type="application/json")

    def test_happy_path_creates_email_only_account(self) -> None:
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(django_mail.outbox[-1].to, ["neda@example.com"])
        response = self.verify(self.code())
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertFalse(body["user"]["has_telegram"])
        self.assertIsNone(body["user"]["telegram_user_id"])
        user = User.objects.get(email="neda@example.com")
        self.assertTrue(is_email_id(user.telegram_user_id))
        self.assertEqual(self.client.get("/api/v1/me/prefs/").status_code, 200)
        self.assertEqual(self.verify(self.code()).status_code, 400)  # one use

    def test_five_tries(self) -> None:
        self.send()
        code = self.code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(self.verify(wrong).json()["code"], "code_invalid")
        self.assertEqual(self.verify(code).status_code, 400)
        self.assertTrue(EmailLoginCode.objects.get().dead)

    def test_daily_failure_cap_per_address(self) -> None:
        for n in range(3):
            self.send(client=Client(REMOTE_ADDR=f"10.2.2.{n}"))
            for _ in range(5):
                self.verify("999999" if n else "999998", client=Client(REMOTE_ADDR=f"10.3.3.{n}"))
        EmailLoginCode.objects.update(created_at=timezone.now() - timedelta(minutes=11))
        self.send(client=Client(REMOTE_ADDR="10.2.2.9"))
        self.assertEqual(self.verify(self.code(), client=Client(REMOTE_ADDR="10.4.4.4")).status_code, 400)

    def test_expiry(self) -> None:
        self.send()
        EmailLoginCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.verify(self.code()).status_code, 400)

    def test_throttled_per_address(self) -> None:
        for _ in range(3):
            self.assertEqual(self.send(client=Client(REMOTE_ADDR=f"10.1.1.{_}")).status_code, 200)
        self.assertEqual(self.send(client=Client(REMOTE_ADDR="10.1.1.99")).status_code, 429)

    def test_throttled_per_ip(self) -> None:
        for n in range(10):
            self.assertEqual(self.send(f"p{n}@example.com").status_code, 200)
        self.assertEqual(self.send("p99@example.com").status_code, 429)

    def test_flag_off_or_no_mail(self) -> None:
        HomeSettings.objects.update(email_login_on=False)
        self.assertEqual(self.send().status_code, 403)
        HomeSettings.objects.update(email_login_on=True)
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="", WEB_EMAIL_FROM=""):
            self.assertEqual(self.send().status_code, 403)
            self.assertFalse(self.client.get("/api/v1/auth/web/session/").json()["email_login"])

    def test_bad_email(self) -> None:
        self.assertEqual(self.send("not-an-email").json()["code"], "validation_failed")

    def test_email_account_cannot_open_the_contact_gate(self) -> None:
        user = accounts.email_user("x@example.com")
        from home import gate
        from home.models import HomeChannel

        channel, _ = HomeChannel.objects.get_or_create(country="CA", city="Toronto", defaults={"username": "chamedoon_toronto"})
        self.assertEqual(gate._ask_telegram(user, channel), "not_member")
        self.assertFalse([name for name, _payload in self.sent.calls if name == "getChatMember"])


class IdentityTests(TestCase):
    def test_range(self) -> None:
        for _ in range(200):
            value = new_email_id()
            self.assertTrue(EMAIL_ID_MIN <= value < EMAIL_ID_MAX)
            self.assertTrue(is_email_id(value))
        self.assertFalse(is_email_id(7_000_001))
        self.assertFalse(is_email_id(-1001234567890))
        self.assertFalse(is_email_id(9_000_000_000_001))

    def test_bots_never_send_to_an_email_account(self) -> None:
        from home import bot
        from notifications.telegram import call_telegram_api_detailed

        value = new_email_id()
        self.assertTrue(refuses({"chat_id": value}))
        with patch("urllib.request.urlopen") as network:
            with self.assertRaises(bot.BotError):
                bot._call("sendMessage", {"chat_id": value, "text": "x"}, "token", 5)
            with self.settings(TELEGRAM_BOT_TOKEN="t"):
                body, error = call_telegram_api_detailed("sendMessage", {"chat_id": value, "text": "x"})
        network.assert_not_called()
        self.assertIsNone(body)


class MergeTests(Base):
    def test_email_account_connects_telegram(self) -> None:
        email_account = accounts.email_user("m@example.com")
        HomeAlert.objects.create(telegram_user_id=email_account.telegram_user_id, chat_id=email_account.telegram_user_id, user=email_account, country="CA", city="Toronto")
        browser = Client()
        browser.cookies["chm_web"] = __import__("users.tokens", fromlist=["x"]).issue_access_token(email_account)
        body = self.start(browser)
        self.assertEqual(WebLoginCode.objects.get().link_user_id, email_account.pk)
        self.open_in_bot(body["code"])
        self.assertIn("m***@example.com", self.sent.last("sendMessage")["text"])
        self.tap(self.right(body["match"]))
        response = self.poll(body["poll_token"], browser)
        self.assertEqual(response.status_code, 200)
        telegram_account = User.objects.get(telegram_user_id=TG_A["id"])
        self.assertEqual(response.json()["user"]["id"], telegram_account.pk)
        self.assertEqual(telegram_account.email, "m@example.com")
        alert = HomeAlert.objects.get()
        self.assertEqual((alert.telegram_user_id, alert.chat_id, alert.user_id), (TG_A["id"], TG_A["id"], telegram_account.pk))
        email_account.refresh_from_db()
        self.assertFalse(email_account.is_active)
        self.assertIsNone(email_account.email)

    def test_duplicates_stay_and_nothing_is_lost(self) -> None:
        from home.models import HomeListing

        source = accounts.email_user("d@example.com")
        target = make_user(telegram_user_id=7_000_009, first_name="T")
        from tests.test_web_stats import listing
        from tests.test_home import make_source

        shop = make_source(username="dup_src", country="CA", default_city="Toronto", default_currency="CAD")
        one, two = listing(shop, "اتاق یک"), listing(shop, "اتاق دو")
        HomeSaved.objects.create(user=source, listing=one)
        HomeSaved.objects.create(user=source, listing=two)
        HomeSaved.objects.create(user=target, listing=one)
        accounts.merge_into(source, target)
        self.assertEqual(set(HomeSaved.objects.filter(user=target).values_list("listing_id", flat=True)), {one.pk, two.pk})
        self.assertEqual(HomeListing.objects.count(), 2)


class RobotsTests(TestCase):
    def test_app_is_noindex(self) -> None:
        self.assertEqual(self.client.get("/app/home/")["X-Robots-Tag"], "noindex")
        self.assertEqual(self.client.get("/app/")["X-Robots-Tag"], "noindex")

    def test_browse_is_noindex_nofollow_and_names_nobody(self) -> None:
        from item_requests.models import ItemRequest, RequestType

        self.assertEqual(self.client.get("/browse/")["X-Robots-Tag"], "noindex, nofollow")
        owner = make_user(telegram_user_id=7_100_001, first_name="Zhalehname")
        item = ItemRequest.objects.create(
            user=owner, type=RequestType.SUPPLY, origin_country="CA", origin_city="toronto", destination_country="IR", destination_city="tehran",
            date_from=timezone.now().date(), date_to=timezone.now().date() + timedelta(days=9), expires_at=timezone.now() + timedelta(days=5),
        )
        detail = self.client.get(f"/browse/{item.pk}/")
        self.assertEqual(detail["X-Robots-Tag"], "noindex, nofollow")
        if detail.status_code == 200:
            self.assertNotContains(detail, "Zhalehname")
