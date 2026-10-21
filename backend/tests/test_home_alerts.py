from __future__ import annotations

import io
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from home import bot
from home.alerts import listing_term, matches, metro_channel, notify_pending, sync_wanted_alerts
from home.botchat import handle_update, parse_start_payload
from home.job import run_home_job
from home.models import (
    AlertSendStatus,
    AlertTerm,
    HomeAlert,
    HomeAlertSent,
    HomeChannel,
    HomeListing,
    HomeSettings,
    HomeSource,
    ListingStatus,
)
from home.submit import create_listing
from tests.helpers import make_user
from tests.test_home_miniapp import MiniAppCase
from tests.test_home_publish import ca_listing, de_listing

TOKEN = "123456:TESTTOKEN"
SECRET = "test-webhook-secret"
USER_ID = 5550001
OTHER_ID = 5550002


class FakeBot:
    """Stands in for home.bot.call: records every call, answers like Telegram."""

    def __init__(self, fail: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail or {}
        self.next_id = 100

    def __call__(self, method: str, payload: dict | None = None, **_kwargs):
        payload = payload or {}
        self.calls.append((method, payload))
        error = self.fail.get((method, payload.get("chat_id"))) or self.fail.get(method)
        if error:
            raise error
        if method in {"sendMessage", "editMessageText"}:
            self.next_id += 1
            return {"message_id": self.next_id}
        return True

    def sent(self, method: str = "sendMessage") -> list[dict]:
        return [payload for name, payload in self.calls if name == method]

    def last_text(self) -> str:
        texts = [payload["text"] for name, payload in self.calls if name in {"sendMessage", "editMessageText"}]
        return texts[-1] if texts else ""

    def buttons(self, payload: dict | None = None) -> list[dict]:
        if payload is None:
            payload = [p for name, p in self.calls if name in {"sendMessage", "editMessageText"}][-1]
        rows = (payload.get("reply_markup") or {}).get("inline_keyboard") or []
        return [button for row in rows for button in row]


def turn_on(**extra) -> HomeSettings:
    values = {"bot_token": TOKEN, "alerts_enabled": True, "webhook_secret": SECRET, **extra}
    row, _ = HomeSettings.objects.update_or_create(pk=1, defaults=values)
    return row


def message(text: str, user_id: int = USER_ID, first_name: str = "Sara") -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": user_id, "first_name": first_name},
            "chat": {"id": user_id, "type": "private"},
            "text": text,
        },
    }


def tap(data: str, user_id: int = USER_ID, text: str = "") -> dict:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "from": {"id": user_id, "first_name": "Sara"},
            "data": data,
            "message": {"message_id": 77, "chat": {"id": user_id, "type": "private"}, "text": text},
        },
    }


def channel(city: str) -> HomeChannel:
    return HomeChannel.objects.get(city=city)


def alert_for(city: str = "Toronto", country: str = "CA", user_id: int = USER_ID, **values) -> HomeAlert:
    values.setdefault("currency", "CAD" if country == "CA" else "EUR")
    return HomeAlert.objects.create(telegram_user_id=user_id, chat_id=user_id, country=country, city=city, **values)


class ConversationTests(TestCase):
    def setUp(self) -> None:
        turn_on()
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_start_shows_the_six_cities_and_the_menu(self) -> None:
        handle_update(message("/start"))
        payload = self.fake.sent()[-1]
        self.assertEqual(payload["chat_id"], USER_ID)
        self.assertIn("گوش به زنگ", payload["text"])
        labels = [button["text"] for button in self.fake.buttons(payload)]
        for name in ("تورنتو", "مونترال", "ونکوور", "برلین", "هامبورگ", "کلن"):
            self.assertIn(name, labels)
        self.assertIn("📋 گوش به زنگ‌های من", labels)
        self.assertTrue(any("startapp=home" in button.get("url", "") for button in self.fake.buttons(payload)))

    def test_four_taps_save_an_alert_linked_to_the_user(self) -> None:
        user = make_user(telegram_user_id=USER_ID, first_name="Sara")
        toronto = channel("Toronto")
        handle_update(tap(f"al:p:{toronto.pk}:_:_:_"))
        self.assertIn("سقف اجاره", self.fake.last_text())
        budgets = [button["callback_data"] for button in self.fake.buttons() if "callback_data" in button]
        self.assertIn(f"al:p:{toronto.pk}:1500:_:_", budgets)
        self.assertIn(f"al:p:{toronto.pk}:0:_:_", budgets)
        handle_update(tap(f"al:p:{toronto.pk}:1500:_:_"))
        self.assertIn("چه نوع", self.fake.last_text())
        handle_update(tap(f"al:p:{toronto.pk}:1500:r:_"))
        self.assertIn("چه مدتی", self.fake.last_text())
        handle_update(tap(f"al:p:{toronto.pk}:1500:r:s"))
        alert = HomeAlert.objects.get()
        self.assertEqual((alert.country, alert.city, alert.max_price, alert.currency), ("CA", "Toronto", Decimal("1500"), "CAD"))
        self.assertEqual(alert.property_types, ["room"])
        self.assertEqual(alert.term, AlertTerm.SUBLET)
        self.assertEqual(alert.user, user)
        self.assertEqual(alert.chat_id, USER_ID)
        self.assertIn("ذخیره شد", self.fake.last_text())
        # Every step edits the same message, and each tap is answered.
        self.assertEqual(len(self.fake.sent("editMessageText")), 4)
        self.assertEqual(len(self.fake.sent("answerCallbackQuery")), 4)
        # The same choices again do not make a second alert.
        handle_update(tap(f"al:p:{toronto.pk}:1500:r:s"))
        self.assertEqual(HomeAlert.objects.count(), 1)

    def test_any_budget_and_type_store_no_limits(self) -> None:
        berlin = channel("Berlin")
        handle_update(tap(f"al:p:{berlin.pk}:0:x:x"))
        alert = HomeAlert.objects.get()
        self.assertIsNone(alert.max_price)
        self.assertEqual(alert.property_types, [])
        self.assertEqual(alert.term, AlertTerm.ANY)
        self.assertEqual(alert.currency, "EUR")
        self.assertIsNone(alert.user)

    def test_deep_link_from_a_channel_post_starts_at_the_budget(self) -> None:
        handle_update(message("/start alert_home_ca_toronto"))
        text = self.fake.last_text()
        self.assertIn("تورنتو", text)
        self.assertIn("سقف اجاره", text)
        toronto = channel("Toronto")
        self.assertIn(f"al:p:{toronto.pk}:2000:_:_", [b.get("callback_data") for b in self.fake.buttons()])

    def test_deep_link_payloads(self) -> None:
        self.assertEqual(parse_start_payload("alert_home_de_cologne")["channel"], channel("Cologne"))
        self.assertEqual(parse_start_payload("alert_home_de_koln")["channel"], channel("Cologne"))
        self.assertEqual(parse_start_payload("alert_home_CA_Montreal")["channel"], channel("Montreal"))
        found = parse_start_payload("alert_home_ca_toronto_m1500_tr_ss")
        self.assertEqual((found["budget"], found["type"], found["term"]), ("1500", "r", "s"))
        nowhere = parse_start_payload("alert_home_ca_ottawa")
        self.assertEqual(nowhere["country"], "CA")
        self.assertIsNone(nowhere["channel"])
        self.assertIsNone(parse_start_payload("home_12"))

    def test_deep_link_with_filters_skips_to_the_missing_step(self) -> None:
        handle_update(message("/start alert_home_ca_toronto_m1500_tr"))
        self.assertIn("چه مدتی", self.fake.last_text())
        handle_update(message("/start alert_home_ca_ottawa"))
        labels = [button["text"] for button in self.fake.buttons()]
        self.assertIn("تورنتو", labels)
        self.assertNotIn("برلین", labels)

    def test_alerts_list_pause_resume_and_delete(self) -> None:
        mine = alert_for(max_price=Decimal("1500"))
        theirs = alert_for(user_id=OTHER_ID)
        handle_update(message("/alerts"))
        self.assertIn("تورنتو · تا 1,500 CA$", self.fake.last_text())
        handle_update(tap(f"al:off:{mine.pk}", text="📋 گوش به زنگ‌های تو"))
        mine.refresh_from_db()
        self.assertFalse(mine.active)
        self.assertIn("⏸", self.fake.last_text())
        handle_update(tap(f"al:on:{mine.pk}"))
        mine.refresh_from_db()
        self.assertTrue(mine.active)
        # Someone else's alert cannot be touched.
        handle_update(tap(f"al:del:{theirs.pk}"))
        self.assertTrue(HomeAlert.objects.filter(pk=theirs.pk).exists())
        handle_update(tap(f"al:del:{mine.pk}"))
        self.assertFalse(HomeAlert.objects.filter(pk=mine.pk).exists())

    def test_stop_switches_every_alert_off(self) -> None:
        alert_for()
        alert_for(city="Montreal")
        handle_update(message("/stop"))
        self.assertFalse(HomeAlert.objects.filter(telegram_user_id=USER_ID, active=True).exists())
        self.assertIn("متوقف شد", self.fake.last_text())

    def test_a_neighbourhood_can_be_added_by_text(self) -> None:
        alert = alert_for()
        handle_update(tap(f"al:ar:{alert.pk}"))
        self.assertIn("اسم محله", self.fake.last_text())
        handle_update(message("North York"))
        alert.refresh_from_db()
        self.assertEqual(alert.area, "North York")
        self.assertFalse(alert.awaiting_area)
        self.assertIn("North York", self.fake.last_text())
        # A command cancels the wait instead of becoming a neighbourhood.
        handle_update(tap(f"al:ar:{alert.pk}"))
        handle_update(message("/cancel"))
        alert.refresh_from_db()
        self.assertEqual(alert.area, "North York")
        self.assertFalse(alert.awaiting_area)

    def test_search_lists_listings_with_links_and_pages(self) -> None:
        for number in range(1, 8):
            listing = ca_listing(number)
            listing.post.source_url = f"https://t.me/RentToronto/{number}"
            listing.post.save()
        toronto = channel("Toronto")
        handle_update(message("/search"))
        self.assertIn(f"sr:c:{toronto.pk}:0", [b.get("callback_data") for b in self.fake.buttons()])
        handle_update(tap(f"sr:c:{toronto.pk}:0"))
        text = self.fake.last_text()
        self.assertIn("۷ آگهی", text)
        self.assertEqual(text.count("🏠"), 5)
        self.assertIn('href="https://t.me/RentToronto/', text)
        self.assertIn("startapp=home_", text)
        self.assertIn(f"sr:c:{toronto.pk}:1", [b.get("callback_data") for b in self.fake.buttons()])
        handle_update(tap(f"sr:c:{toronto.pk}:1"))
        self.assertEqual(self.fake.last_text().count("🏠"), 2)

    def test_search_by_alert_uses_its_filters(self) -> None:
        cheap = ca_listing(1, price_amount=900)
        dear = ca_listing(2, price_amount=2500)
        alert = alert_for(max_price=Decimal("1000"))
        handle_update(tap(f"sr:a:{alert.pk}:0"))
        text = self.fake.last_text()
        self.assertIn(f"home_{cheap.pk}", text)
        self.assertNotIn(f"home_{dear.pk}", text)

    def test_groups_and_unknown_buttons_are_ignored_safely(self) -> None:
        group = message("/start")
        group["message"]["chat"]["type"] = "group"
        handle_update(group)
        self.assertEqual(self.fake.calls, [])
        handle_update(tap("al:p:999:_:_:_"))
        self.assertEqual(HomeAlert.objects.count(), 0)
        self.assertEqual(self.fake.sent("answerCallbackQuery")[-1].get("text"), "این دکمه قدیمیه.")


class WebhookTests(TestCase):
    URL = "/api/home/bot/webhook/"

    def post(self, body: dict, secret: str | None = SECRET):
        headers = {"HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN": secret} if secret is not None else {}
        return self.client.post(self.URL, data=json.dumps(body), content_type="application/json", **headers)

    def test_refused_without_a_configured_secret(self) -> None:
        turn_on(webhook_secret="")
        with patch("home.bot.call", FakeBot()) as fake:
            self.assertEqual(self.post(message("/start")).status_code, 403)
        self.assertEqual(fake.calls, [])

    def test_refused_with_a_wrong_secret(self) -> None:
        turn_on()
        with patch("home.bot.call", FakeBot()) as fake:
            self.assertEqual(self.post(message("/start"), secret="nope").status_code, 403)
            self.assertEqual(self.post(message("/start"), secret=None).status_code, 403)
        self.assertEqual(fake.calls, [])

    @override_settings(HOME_WEBHOOK_SECRET="from-env")
    def test_env_secret_works_when_the_admin_field_is_blank(self) -> None:
        turn_on(webhook_secret="")
        with patch("home.bot.call", FakeBot()) as fake:
            self.assertEqual(self.post(message("/start"), secret="from-env").status_code, 200)
        self.assertEqual(fake.calls[0][0], "sendMessage")

    def test_an_update_is_answered_and_telegram_errors_still_return_200(self) -> None:
        turn_on()
        fake = FakeBot(fail={"sendMessage": bot.BotError("Forbidden: bot was blocked by the user", code=403)})
        with patch("home.bot.call", fake):
            response = self.post(message("/start"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.calls[0][0], "sendMessage")
        self.assertEqual(self.client.get(self.URL).status_code, 405)


class MatchingTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()

    def check(self, alert: HomeAlert, listing: HomeListing) -> bool:
        return matches(alert, listing, metro_channel(alert.country, alert.city))

    def test_a_suburb_belongs_to_its_metro(self) -> None:
        listing = ca_listing(1)  # North York, 950 CAD, room
        self.assertEqual(listing.city, "North York")
        self.assertTrue(self.check(alert_for("Toronto"), listing))
        self.assertFalse(self.check(alert_for("Montreal"), listing))
        self.assertFalse(self.check(alert_for("Berlin", country="DE"), listing))

    def test_a_city_without_a_channel_matches_by_name(self) -> None:
        listing = de_listing(1, city="Frankfurt", area="Bockenheim")
        self.assertIsNone(metro_channel("DE", "Frankfurt"))
        self.assertTrue(self.check(alert_for("Frankfurt", country="DE"), listing))
        self.assertFalse(self.check(alert_for("Berlin", country="DE"), listing))

    def test_price(self) -> None:
        listing = ca_listing(1, price_amount=950)
        self.assertTrue(self.check(alert_for(max_price=Decimal("1000")), listing))
        self.assertTrue(self.check(alert_for(max_price=Decimal("950")), listing))
        self.assertFalse(self.check(alert_for(max_price=Decimal("900")), listing))
        self.assertTrue(self.check(alert_for(), listing))
        no_price = ca_listing(2, price_amount=None)
        self.assertFalse(self.check(alert_for(max_price=Decimal("1000")), no_price))
        self.assertTrue(self.check(alert_for(), no_price))
        weekly = ca_listing(3, price_amount=300, price_period="week")
        self.assertFalse(self.check(alert_for(max_price=Decimal("1000")), weekly))
        self.assertTrue(self.check(alert_for(max_price=Decimal("1500")), weekly))

    def test_type(self) -> None:
        room = ca_listing(1)
        flat = ca_listing(2, property_type="apartment")
        basement = ca_listing(3, property_type="basement")
        rooms_only = alert_for(property_types=["room"])
        flats = alert_for(property_types=["apartment", "basement"])
        self.assertTrue(self.check(rooms_only, room))
        self.assertFalse(self.check(rooms_only, flat))
        self.assertTrue(self.check(flats, flat))
        self.assertTrue(self.check(flats, basement))
        self.assertFalse(self.check(flats, room))

    def test_term(self) -> None:
        lease = ca_listing(1)
        sublet = ca_listing(2, available_to="2027-02-28")
        short = ca_listing(3, property_type="short_stay", price_period="night", price_amount=40)
        self.assertEqual([listing_term(x) for x in (lease, sublet, short)], ["lease", "sublet", "short"])
        leases = alert_for(term=AlertTerm.LEASE)
        sublets = alert_for(term=AlertTerm.SUBLET)
        self.assertTrue(self.check(leases, lease))
        self.assertFalse(self.check(leases, sublet))
        self.assertFalse(self.check(sublets, lease))
        self.assertTrue(self.check(sublets, sublet))
        self.assertTrue(self.check(sublets, short))

    def test_neighbourhood_text(self) -> None:
        listing = ca_listing(1)  # North York · Yonge & Finch
        self.assertTrue(self.check(alert_for(area="yonge"), listing))
        self.assertTrue(self.check(alert_for(area="North York"), listing))
        self.assertFalse(self.check(alert_for(area="Scarborough"), listing))

    def test_wanted_posts_are_never_matched(self) -> None:
        wanted = ca_listing(1, kind="wanted", price_amount=None, price_max=1000)
        self.assertFalse(self.check(alert_for(), wanted))


class NotifierTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on()
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_switched_off_by_default(self) -> None:
        HomeSettings.objects.filter(pk=1).update(alerts_enabled=False)
        self.assertFalse(HomeSettings.objects.model._meta.get_field("alerts_enabled").default)
        alert_for()
        ca_listing(1)
        result = notify_pending()
        self.assertIn("switched off", result["skipped"])
        self.assertEqual(self.fake.calls, [])
        job = run_home_job(step="alerts")
        self.assertIn("switched off", job["alerts"]["skipped"])

    def test_a_match_is_messaged_once_with_its_buttons(self) -> None:
        alert = alert_for(max_price=Decimal("1000"))
        listing = ca_listing(1)
        result = notify_pending()
        self.assertEqual(result["sent"], 1)
        payload = self.fake.sent()[0]
        self.assertEqual(payload["chat_id"], USER_ID)
        self.assertIn("گوش به زنگ", payload["text"])
        self.assertIn("950 CA$", payload["text"])
        buttons = self.fake.buttons(payload)
        datas = [button.get("callback_data") for button in buttons]
        self.assertIn(f"al:seen:{listing.pk}", datas)
        self.assertIn(f"al:off:{alert.pk}", datas)
        urls = [button.get("url", "") for button in buttons]
        self.assertIn(f"startapp=home_{listing.pk}", " ".join(urls))
        self.assertIn("https://t.me/chamedoon_toronto_khane", urls)
        # Second run: nothing new.
        self.assertEqual(notify_pending()["sent"], 0)
        self.assertEqual(len(self.fake.sent()), 1)
        sent = HomeAlertSent.objects.get()
        self.assertEqual(sent.status, AlertSendStatus.SENT)
        alert.refresh_from_db()
        self.assertIsNotNone(alert.last_notified_at)

    def test_two_matching_alerts_still_send_one_message(self) -> None:
        alert_for()
        alert_for(property_types=["room"])
        ca_listing(1)
        self.assertEqual(notify_pending()["sent"], 1)

    def test_listings_older_than_the_alert_are_not_sent(self) -> None:
        listing = ca_listing(1)
        HomeListing.objects.filter(pk=listing.pk).update(created_at=timezone.now() - timedelta(hours=2))
        alert_for()
        self.assertEqual(notify_pending()["sent"], 0)

    def test_hidden_and_non_matching_listings_are_not_sent(self) -> None:
        alert_for(max_price=Decimal("1000"))
        review = ca_listing(1)
        review.needs_review = True
        review.save()
        ca_listing(2, price_amount=1800)
        ca_listing(3, city="Montreal", region="QC")
        self.assertEqual(notify_pending()["sent"], 0)

    def test_daily_cap_per_user(self) -> None:
        turn_on(alerts_daily_cap=2)
        alert_for()
        alert_for(user_id=OTHER_ID)
        for number in range(1, 5):
            ca_listing(number)
        result = notify_pending()
        self.assertEqual(result["sent"], 4)
        self.assertEqual(HomeAlertSent.objects.filter(telegram_user_id=USER_ID).count(), 2)
        self.assertEqual(HomeAlertSent.objects.filter(telegram_user_id=OTHER_ID).count(), 2)
        self.assertEqual(result["over_daily_cap"], 4)
        # A day later the rest go out.
        HomeAlertSent.objects.update(sent_at=timezone.now() - timedelta(days=1, minutes=1))
        self.assertEqual(notify_pending()["sent"], 4)

    def test_a_user_who_blocked_the_bot_loses_their_alerts(self) -> None:
        self.fake.fail = {("sendMessage", USER_ID): bot.BotError("Forbidden: bot was blocked by the user", code=403)}
        first = alert_for()
        second = alert_for(city="Montreal")
        other = alert_for(user_id=OTHER_ID)
        ca_listing(1)
        ca_listing(2)
        result = notify_pending()
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(result["sent"], 2)
        for alert in (first, second):
            alert.refresh_from_db()
            self.assertFalse(alert.active)
        other.refresh_from_db()
        self.assertTrue(other.active)
        blocked = HomeAlertSent.objects.get(telegram_user_id=USER_ID)
        self.assertEqual(blocked.status, AlertSendStatus.BLOCKED)

    def test_rate_limit_stops_the_run_and_retries_later(self) -> None:
        self.fake.fail = {"sendMessage": bot.BotError("Too Many Requests", code=429, retry_after=5)}
        alert_for()
        ca_listing(1)
        result = notify_pending()
        self.assertEqual(result["retry_after"], 5)
        self.assertEqual(HomeAlertSent.objects.count(), 0)
        self.fake.fail = {}
        self.assertEqual(notify_pending()["sent"], 1)

    def test_seen_and_stop_buttons(self) -> None:
        alert = alert_for()
        listing = ca_listing(1)
        notify_pending()
        handle_update(tap(f"al:seen:{listing.pk}"))
        self.assertIsNotNone(HomeAlertSent.objects.get().seen_at)
        handle_update(tap(f"al:off:{alert.pk}", text="🔔 آگهی تازه"))
        alert.refresh_from_db()
        self.assertFalse(alert.active)
        ca_listing(2)
        self.assertEqual(notify_pending()["sent"], 0)

    def test_the_job_runs_the_step(self) -> None:
        alert_for()
        ca_listing(1)
        result = run_home_job(step="alerts")
        self.assertEqual(result["alerts"]["sent"], 1)
        HomeSettings.objects.filter(pk=1).update(alerts_lock_until=timezone.now() + timedelta(minutes=2))
        ca_listing(2)
        self.assertIn("already going out", run_home_job(step="alerts")["alerts"]["skipped"])


class SavedRequestTests(TestCase):
    """A wanted post made in the Mini App is a saved request: it gets the same alerts."""

    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on()
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = make_user(telegram_user_id=USER_ID, first_name="Sara", telegram_username="sara_t")

    def wanted(self, **form) -> HomeListing:
        values = {
            "country": "CA",
            "kind": "wanted",
            "property_type": "room",
            "city": "North York",
            "price": "1100",
            "description": "دنبال یک اتاق تمیز نزدیک مترو هستم از اول ماه بعد.",
        }
        values.update(form)
        return create_listing(self.user, values, [], locale="fa", countries=["CA", "DE"])

    def test_a_wanted_post_becomes_an_alert_and_is_notified(self) -> None:
        wanted = self.wanted()
        synced = sync_wanted_alerts()
        self.assertEqual(synced["created"], 1)
        alert = HomeAlert.objects.get(wanted_listing=wanted)
        self.assertEqual((alert.city, alert.max_price, alert.property_types), ("Toronto", Decimal("1100.00"), ["room"]))
        self.assertEqual(alert.user, self.user)
        ca_listing(1)  # North York room, 950 CAD
        ca_listing(2, price_amount=1500)
        result = notify_pending()
        self.assertEqual(result["sent"], 1)
        self.assertIn("درخواست شما", self.fake.sent()[0]["text"])
        self.assertEqual(sync_wanted_alerts()["created"], 0)

    def test_closing_the_wanted_post_stops_its_alert(self) -> None:
        wanted = self.wanted()
        sync_wanted_alerts()
        wanted.status = ListingStatus.EXPIRED
        wanted.save()
        self.assertEqual(sync_wanted_alerts()["closed"], 1)
        self.assertFalse(HomeAlert.objects.get(wanted_listing=wanted).active)

    def test_wanted_posts_read_from_telegram_groups_are_not_alerts(self) -> None:
        ca_listing(1, kind="wanted", price_amount=None, price_max=1000)
        self.assertEqual(sync_wanted_alerts()["created"], 0)

    def test_own_listings_are_not_sent_back(self) -> None:
        alert_for()
        listing = ca_listing(1)
        listing.post.author_id = USER_ID
        listing.post.save()
        self.assertEqual(notify_pending()["sent"], 0)


class WebhookCommandTests(TestCase):
    def test_dry_run_prints_without_calling_telegram(self) -> None:
        turn_on()
        out = io.StringIO()
        with patch("home.bot.call", FakeBot()) as fake:
            call_command("home_webhook", "--url", "https://example.test", stdout=out)
        text = out.getvalue()
        self.assertIn("https://example.test/api/home/bot/webhook/", text)
        self.assertIn("(set)", text)
        self.assertNotIn(SECRET, text)
        self.assertIn("Dry run", text)
        self.assertEqual(fake.calls, [])

    def test_apply_sets_the_webhook_with_the_secret(self) -> None:
        turn_on()
        with patch("home.bot.call", FakeBot()) as fake:
            call_command("home_webhook", "--url", "https://example.test", "--apply", stdout=io.StringIO())
        methods = [name for name, _payload in fake.calls]
        self.assertEqual(methods, ["setWebhook", "setMyCommands"])
        self.assertEqual(fake.calls[0][1]["secret_token"], SECRET)


class MiniAppFilterTests(MiniAppCase):
    def test_price_range_area_term_and_text_search(self) -> None:
        cheap = self.ca_listing(1, city="Toronto", area="Scarborough", price_amount=700)
        mid = self.ca_listing(2, city="Toronto", area="Yonge & Finch", price_amount=1200, summary_fa="اتاق مبله نزدیک مترو فینچ")
        dear = self.ca_listing(3, city="Toronto", area="Downtown", price_amount=2200, available_to="2027-01-31")
        short = self.ca_listing(4, city="Toronto", area="Downtown", property_type="short_stay", price_period="night", price_amount=60)

        def ids(query: str) -> set[int]:
            fragment = self.feed(query)
            return {item.pk for item in (cheap, mid, dear, short) if f"/app/home/{item.pk}/" in fragment}

        self.assertEqual(ids("?country=CA&min=1000&max=2000"), {mid.pk})
        self.assertEqual(ids("?country=CA&min=1000"), {mid.pk, dear.pk})
        self.assertEqual(ids("?country=CA&area=downtown"), {dear.pk, short.pk})
        self.assertEqual(ids("?country=CA&term=lease"), {cheap.pk, mid.pk})
        self.assertEqual(ids("?country=CA&term=sublet"), {dear.pk})
        self.assertEqual(ids("?country=CA&term=short"), {short.pk})
        self.assertEqual(ids("?country=CA&q=مترو"), {mid.pk})
        self.assertEqual(ids("?country=CA&q=scarb"), {cheap.pk})

    def test_filter_sheet_has_the_new_controls_and_keeps_values(self) -> None:
        self.ca_listing(1, city="Toronto", area="Scarborough")
        self.ca_listing(2, city="Toronto", area="Scarborough")
        page = self.client.get("/app/home/list/?country=CA&city=Toronto&min=500&max=1500&term=lease&q=x").content.decode()
        self.assertIn('name="min" value="500"', page)
        self.assertIn('name="max" value="1500"', page)
        self.assertIn('name="q" value="x"', page)
        self.assertIn('name="term" id="home_term" value="lease"', page)
        self.assertIn('data-area="Scarborough"', page)
        self.assertIn('data-term="sublet"', page)
        self.assertIn("گوش به زنگ", page)
        self.assertIn("https://t.me/chamedoon_home_bot?start=alert_home_ca_toronto_m1500_sl", page)
        self.assertIn('<span class="filter-count">4</span>', page)

    def test_alert_button_maps_a_suburb_to_its_metro_and_works_without_a_city(self) -> None:
        self.ca_listing(1)
        page = self.client.get("/app/home/list/?country=CA&city=North York&type=room").content.decode()
        self.assertIn("?start=alert_home_ca_toronto_tr", page)
        page = self.client.get("/app/home/list/?country=DE").content.decode()
        self.assertIn("?start=alert_home_de&quot;", page.replace('"', "&quot;"))

    def test_english_labels(self) -> None:
        self.login(self.user, "en")
        page = self.client.get("/app/home/list/?country=CA").content.decode()
        for label in ("Monthly rent", "Neighbourhood", "Short stay", "Get alerts"):
            self.assertIn(label, page)
