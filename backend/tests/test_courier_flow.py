"""Courier route matcher: bot flow, results, two-sided intros, route alerts, prohibited list,
channel post template and the courier Mini App page. Telegram is never called for real."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from courier import alerts, conf, flow, search, texts
from courier.models import CourierAlertSent, CourierChat, CourierIntro, CourierRouteAlert, CourierSettings, IntroStatus
from courier.telegram import execute
from item_requests.models import City, CourierChannel, ItemRequest, RequestStatus, RequestType
from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from matching.models import Match, MatchStatus
from miniapp.auth import SESSION_USER_KEY, startapp_path
from miniapp.i18n import SESSION_LOCALE_KEY
from notifications.channel import _format_request_message
from tests.helpers import TEST_SECRET, make_user

SARA = 7001
SHADOW_ID = 9_000_000_000_123
OK = {"ok": True, "result": {"message_id": 777}}


def today():
    return timezone.localdate()


def message(text: str, uid: int = SARA, first: str = "Sara", username: str = "sara_t") -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "first_name": first, "username": username},
            "text": text,
        },
    }


def tap(data: str, uid: int = SARA, first: str = "Sara", username: str = "sara_t", message_id: int = 50) -> dict:
    return {
        "callback_query": {
            "id": f"cb-{data}",
            "from": {"id": uid, "first_name": first, "username": username},
            "message": {"message_id": message_id, "chat": {"id": uid, "type": "private"}},
            "data": data,
        }
    }


def screens(actions: list[dict]) -> list[dict]:
    return [action["payload"] for action in actions if action["method"] in {"sendMessage", "editMessageText"}]


def last(actions: list[dict]) -> dict:
    return screens(actions)[-1]


def buttons(payload: dict) -> list[dict]:
    rows = (payload.get("reply_markup") or {}).get("inline_keyboard") or []
    return [button for row in rows for button in row]


def labels(payload: dict) -> list[str]:
    return [button["text"] for button in buttons(payload)]


def data_of(payload: dict, label: str) -> str:
    for button in buttons(payload):
        if button["text"] == label:
            return button.get("callback_data") or button.get("url")
    raise AssertionError(f"{label!r} not in {labels(payload)}")


def supply(user, days: int, *, kg: str = "5", origin=("CA", "toronto"), dest=("IR", "tehran"), **extra) -> ItemRequest:
    flight = (today() + timedelta(days=days)).isoformat()
    payload = {
        "type": "SUPPLY",
        "origin_country": origin[0],
        "origin_city": origin[1],
        "destination_country": dest[0],
        "destination_city": dest[1],
        "flight_date": flight,
        "date_from": today().isoformat(),
        "date_to": flight,
        "capacity_kg": kg,
        "item_category_codes": ["CLOTHES", "DOCUMENTS", "PERSONAL_ITEMS", "OTHER"],
        **extra.pop("payload", {}),
    }
    return create_item_request(user, payload, **extra)


def demand(user, days: int, *, kg: str = "2", origin=("CA", "toronto"), dest=("IR", "tehran"), **extra) -> ItemRequest:
    payload = {
        "type": "DEMAND",
        "origin_country": origin[0],
        "origin_city": origin[1],
        "destination_country": dest[0],
        "destination_city": dest[1],
        "desired_date": (today() + timedelta(days=days)).isoformat(),
        "weight_kg": kg,
        "item_category_codes": ["CLOTHES"],
    }
    return create_item_request(user, payload, **extra)


@override_settings(SECRET_KEY=TEST_SECRET, TELEGRAM_BOT_USERNAME="Chamed0on_bot", TELEGRAM_CHANNEL_ENABLED=False)
class CourierCase(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.omar = make_user(telegram_user_id=7101, first_name="Omar", telegram_username="omar_fly")
        self.reza = make_user(telegram_user_id=7102, first_name="Reza", telegram_username="reza_trip")
        self.nima = make_user(telegram_user_id=7103, first_name="Nima")

    def run_flow(self, *updates) -> list[dict]:
        actions: list[dict] = []
        for update in updates:
            actions = flow.handle_update(update)
        return actions

    def demand_until_size(self, when: str = "") -> dict:
        """Role D, first frequent route (Toronto → Tehran), the given date chip."""
        self.run_flow(tap("cr:r:D"))
        actions = self.run_flow(tap("cr:rt:0"))
        key = when or texts.upcoming_months()[1][0]
        return last(self.run_flow(tap(f"cr:m:{key}")))

    def sara(self):
        from users.models import User

        return User.objects.get(telegram_user_id=SARA)


class StartAndStepsTests(CourierCase):
    def test_start_sends_one_line_and_the_two_roles_with_the_five_button_keyboard(self) -> None:
        actions = flow.handle_update(message("/start"))
        hello, question = screens(actions)
        self.assertIn("چمدون باربری", hello["text"])
        self.assertIn("رایگان، بدون کمیسیون", hello["text"])
        keyboard = [button["text"] for row in hello["reply_markup"]["keyboard"] for button in row]
        self.assertEqual(
            keyboard,
            ["📦 بسته دارم", "🧳 مسافرم", "🔔 گوش به زنگ مسیر", "📋 درخواست‌ها و سفرهای من", "❓ راهنما و ممنوعه‌ها"],
        )
        self.assertIn("بسته دارید یا مسافرید؟", question["text"])
        self.assertEqual(labels(question), ["📦 بسته دارم، دنبال مسافرم", "🧳 مسافرم، جا دارم"])
        self.assertNotIn("Send Package", str(actions))
        self.assertNotIn("Cancel", str(actions))

    def test_steps_edit_one_message_and_number_the_four_steps(self) -> None:
        role = self.run_flow(tap("cr:r:D"))
        self.assertEqual([action["method"] for action in role], ["answerCallbackQuery", "editMessageText"])
        route = last(role)
        self.assertEqual(route["message_id"], 50)
        self.assertIn("۲ از ۴ · مسیر بسته؟", route["text"])
        self.assertIn("✅ 📦 بسته دارم، دنبال مسافرم", route["text"])
        self.assertEqual(len(labels(route)), 5)  # 3 busy routes, «مسیر دیگه», back
        self.assertIn("تورنتو ← تهران", labels(route))
        self.assertIn("🗺 مسیر دیگه", labels(route))
        dates = last(self.run_flow(tap("cr:rt:0")))
        self.assertIn("۳ از ۴ · کِی باید برسه؟", dates["text"])
        self.assertIn("✅ مسیر: <b>تورنتو ← تهران</b>", dates["text"])
        self.assertIn(f"تا آخر {texts.month_name(today())}", labels(dates))
        self.assertIn("📅 تاریخ دقیق", labels(dates))
        self.assertIn("عجله ندارم", labels(dates))
        size = last(self.run_flow(tap(f"cr:m:{texts.upcoming_months()[1][0]}")))
        self.assertIn("۴ از ۴ · چی و چقدر؟", size["text"])
        self.assertEqual(labels(size)[:4], ["📄 مدرک / نامه", "تا ۱ کیلو", "۱ تا ۳ کیلو", "۳ کیلو +"])
        self.assertIn(texts.PROHIBITED_HINT, size["text"])
        self.assertIn("⚠️ چه چیزهایی ممنوعه؟", labels(size))

    def test_back_buttons_return_to_the_previous_step(self) -> None:
        self.demand_until_size()
        self.assertIn("۳ از ۴", last(self.run_flow(tap("cr:bk:3")))["text"])
        self.assertIn("۲ از ۴", last(self.run_flow(tap("cr:bk:2")))["text"])
        self.assertIn("بسته دارید یا مسافرید؟", last(self.run_flow(tap("cr:bk:1")))["text"])
        self.assertIn("۴ از ۴", last(self.run_flow(tap("cr:bk:4")))["text"])

    def test_keyboard_buttons_and_commands(self) -> None:
        self.assertIn("۲ از ۴ · مسیر سفرتون؟", last(flow.handle_update(message("🧳 مسافرم")))["text"])
        self.assertIn("۲ از ۴ · مسیر بسته؟", last(flow.handle_update(message("📦 بسته دارم")))["text"])
        self.assertIn("گوش به زنگ مسیر", last(flow.handle_update(message("🔔 گوش به زنگ مسیر")))["text"])
        self.assertIn("درخواست‌ها و سفرهای شما", last(flow.handle_update(message("📋 درخواست‌ها و سفرهای من")))["text"])
        self.assertIn("مسیر سفرتون؟", last(flow.handle_update(message("/carry")))["text"])
        self.assertIn("مسیر بسته؟", last(flow.handle_update(message("/send")))["text"])
        self.assertIn("گوش به زنگ مسیر", last(flow.handle_update(message("/alerts")))["text"])
        self.assertIn("درخواست‌ها و سفرهای شما", last(flow.handle_update(message("/mine")))["text"])
        self.assertIn("لغو شد", last(flow.handle_update(message("/cancel")))["text"])
        self.assertIn("بسته دارید یا مسافرید؟", last(flow.handle_update(message("سلام")))["text"])

    def test_groups_bots_and_foreign_buttons_are_ignored(self) -> None:
        group = message("/start")
        group["message"]["chat"]["type"] = "group"
        self.assertEqual(flow.handle_update(group), [])
        self.assertEqual(flow.handle_update(tap("match:accept:1"))[0]["method"], "answerCallbackQuery")
        self.assertEqual(flow.handle_update(tap("cr:zz:1"))[0]["payload"]["text"], flow.OLD_BUTTON)


class CreateRequestTests(CourierCase):
    def test_demand_flow_with_a_month_creates_an_open_date_package_request(self) -> None:
        month_key, _start, month_end = texts.upcoming_months()[1]
        self.demand_until_size(month_key)
        noted = last(flow.handle_update(message("دو جلد کتاب")))
        self.assertIn("✅ توضیح: دو جلد کتاب", noted["text"])
        confirm = last(self.run_flow(tap("cr:w:3")))
        self.assertIn("همه چیز درسته؟", confirm["text"])
        self.assertIn("✅ ۱ تا ۳ کیلو", confirm["text"])
        self.run_flow(tap("cr:ok"))
        item = ItemRequest.objects.get(user=self.sara())
        self.assertEqual(item.type, RequestType.DEMAND)
        self.assertEqual((item.origin_city, item.destination_city), ("toronto", "tehran"))
        self.assertIsNone(item.desired_date)
        self.assertEqual(item.date_to, month_end)
        self.assertEqual(item.weight_kg, Decimal("3.00"))
        self.assertEqual(item.description, "دو جلد کتاب")
        self.assertEqual({c.code for c in item.item_categories.all()}, {"PERSONAL_ITEMS", "CLOTHES"})

    def test_documents_open_date_demand(self) -> None:
        self.run_flow(tap("cr:r:D"), tap("cr:rt:0"), tap("cr:m:o"), tap("cr:w:doc"), tap("cr:ok"))
        item = ItemRequest.objects.get(user=self.sara())
        self.assertEqual({c.code for c in item.item_categories.all()}, {"DOCUMENTS"})
        self.assertEqual(item.date_to, today() + timedelta(days=flow.OPEN_DAYS))

    def test_typed_exact_date_in_persian_digits(self) -> None:
        self.run_flow(tap("cr:r:D"), tap("cr:rt:0"))
        asked = last(self.run_flow(tap("cr:m:x")))
        self.assertIn("تاریخ را بنویسید", asked["text"])
        self.assertIn("این تاریخ را نفهمیدم", last(flow.handle_update(message("فردا شب")))["text"])
        wanted = today() + timedelta(days=9)
        size = last(flow.handle_update(message(texts.fa(wanted.isoformat()))))
        self.assertIn(f"✅ زمان: {texts.day_month(wanted)}", size["text"])
        self.run_flow(tap("cr:w:1"), tap("cr:ok"))
        self.assertEqual(ItemRequest.objects.get(user=self.sara()).desired_date, wanted)

    def test_traveler_flow_picks_a_day_and_creates_a_trip(self) -> None:
        self.run_flow(tap("cr:r:S"), tap("cr:rt:0"))
        month_key, start, _end = texts.upcoming_months()[1]
        days = last(self.run_flow(tap(f"cr:m:{month_key}")))
        self.assertIn("پرواز چندم", days["text"])
        flight = start + timedelta(days=4)
        size = last(self.run_flow(tap(f"cr:dy:{flight.isoformat()}")))
        self.assertIn("۴ از ۴ · چند کیلو جا دارید؟", size["text"])
        self.assertIn("📄 فقط مدرک", labels(size))
        self.assertNotIn(texts.PROHIBITED_HINT, size["text"])
        self.run_flow(tap("cr:w:3p"), tap("cr:ok"))
        item = ItemRequest.objects.get(user=self.sara())
        self.assertEqual(item.type, RequestType.SUPPLY)
        self.assertEqual(item.flight_date, flight)
        self.assertEqual(item.date_from, today())
        self.assertEqual(item.capacity_kg, Decimal("10.00"))

    def test_past_day_is_refused(self) -> None:
        self.run_flow(tap("cr:r:S"), tap("cr:rt:0"))
        actions = self.run_flow(tap(f"cr:dy:{(today() - timedelta(days=1)).isoformat()}"))
        self.assertEqual(actions[0]["payload"]["text"], "این روز گذشته.")

    def test_other_route_by_buttons_and_by_typed_city(self) -> None:
        self.run_flow(tap("cr:r:D"))
        countries = last(self.run_flow(tap("cr:rt:x")))
        self.assertIn("از کدوم کشور؟", countries["text"])
        self.assertEqual(data_of(countries, "کانادا"), "cr:co:CA")
        cities = last(self.run_flow(tap("cr:co:CA")))
        montreal = City.objects.get(country__code="CA", slug="montreal")
        self.assertEqual(data_of(cities, "مونترال"), f"cr:io:{montreal.pk}")
        to_country = last(self.run_flow(tap(f"cr:io:{montreal.pk}")))
        self.assertIn("به کدوم کشور؟", to_country["text"])
        self.run_flow(tap("cr:cd:IR"))
        dates = last(flow.handle_update(message("شیراز")))
        self.assertIn("✅ مسیر: <b>مونترال ← شیراز</b>", dates["text"])

    def test_same_city_twice_is_refused(self) -> None:
        self.run_flow(tap("cr:r:D"), tap("cr:rt:x"), tap("cr:co:CA"))
        toronto = City.objects.get(country__code="CA", slug="toronto")
        self.run_flow(tap(f"cr:io:{toronto.pk}"), tap("cr:cd:CA"))
        self.assertIn("مقصد باید با مبدا فرق داشته باشد", last(self.run_flow(tap(f"cr:id:{toronto.pk}")))["text"])

    def test_submit_before_all_steps_does_not_save(self) -> None:
        self.run_flow(tap("cr:r:D"))
        self.assertEqual(self.run_flow(tap("cr:ok"))[0]["payload"]["text"], "چند قدم مانده.")
        self.assertFalse(ItemRequest.objects.exists())

    def test_frequent_routes_follow_the_data(self) -> None:
        for days in (5, 6):
            supply(self.omar, days, origin=("DE", "berlin"), dest=("IR", "tehran"))
        self.assertEqual(search.frequent_routes()[0], ("DE", "berlin", "IR", "tehran"))
        self.assertEqual(len(search.frequent_routes()), 3)


class ResultsTests(CourierCase):
    def exact_demand(self, days: int = 3) -> ItemRequest:
        wanted = today() + timedelta(days=days)
        self.run_flow(tap("cr:r:D"), tap("cr:rt:0"), tap("cr:m:x"))
        flow.handle_update(message(wanted.isoformat()))
        self.actions = self.run_flow(tap("cr:w:1"), tap("cr:ok"))
        return ItemRequest.objects.get(user=self.sara())

    def test_results_come_nearest_date_first_with_intro_buttons(self) -> None:
        far = supply(self.omar, 15)
        near = supply(self.reza, 4)
        middle = supply(self.nima, 9)
        supply(make_user(telegram_user_id=7104, first_name="Kian", telegram_username="kian_x"), 6, dest=("IR", "shiraz"))
        supply(make_user(telegram_user_id=7105, first_name="Cargo", telegram_username="toronto_cargo"), 5)
        self.exact_demand(3)
        result = last(self.actions)
        self.assertIn("<b>۳ مسافر</b> پیدا شد، نزدیک‌ترین تاریخ اول:", result["text"])
        self.assertLess(result["text"].index(texts.day_month(near.flight_date)), result["text"].index(texts.day_month(middle.flight_date)))
        self.assertLess(result["text"].index(texts.day_month(middle.flight_date)), result["text"].index(texts.day_month(far.flight_date)))
        self.assertEqual(data_of(result, "💬 ۱"), f"cr:i:{near.pk}")
        self.assertEqual(data_of(result, "💬 ۳"), f"cr:i:{far.pk}")
        self.assertIn("🔔 مسافر جدید اومد خبرم کن", labels(result))
        self.assertIn("⚠️ چه چیزهایی ممنوعه؟", labels(result))
        self.assertIn("💬 هزینه: توافقی", result["text"])
        self.assertNotIn("ارسال موفق", result["text"])

    def test_trust_line_only_with_completed_shipments(self) -> None:
        trip = supply(self.omar, 4)
        old_trip = supply(self.omar, 40, dest=("IR", "mashhad"))
        sender = make_user(telegram_user_id=7110, first_name="Lida", telegram_username="lida_s")
        old = demand(sender, 38, dest=("IR", "mashhad"))
        Match.objects.update_or_create(
            demand_request=old, supply_request=old_trip, defaults={"score": Decimal("90"), "status": MatchStatus.COMPLETED}
        )
        self.exact_demand(3)
        self.assertIn("✅ ۱ ارسال موفق در چمدون", last(self.actions)["text"])
        self.assertEqual(search.trust_line(self.reza), "")
        self.assertTrue(trip.pk)

    def test_no_results_and_paging(self) -> None:
        self.exact_demand(3)
        self.assertIn("هنوز مسافری برای این مسیر و زمان پیدا نشد", last(self.actions)["text"])
        users = [make_user(telegram_user_id=7200 + n, first_name=f"P{n}", telegram_username=f"p_user{n}") for n in range(4)]
        for n, user in enumerate(users):
            supply(user, 4 + n)
        page = last(self.run_flow(tap("cr:mo:0")))
        self.assertIn("بیشتر ›", labels(page))
        second = last(self.run_flow(tap("cr:mo:1")))
        self.assertIn("<b>۴.</b>", second["text"])
        self.assertIn("‹ اول", labels(second))

    def test_month_window_hides_later_flights(self) -> None:
        month_key, _start, month_end = texts.upcoming_months()[0]
        inside = supply(self.omar, max(0, (month_end - today()).days))
        later = supply(self.reza, (month_end - today()).days + 20)
        self.run_flow(tap("cr:r:D"), tap("cr:rt:0"), tap(f"cr:m:{month_key}"), tap("cr:w:1"))
        result = last(self.run_flow(tap("cr:ok")))
        self.assertIn(f"cr:i:{inside.pk}", str(result))
        self.assertNotIn(f"cr:i:{later.pk}", str(result))

    def test_traveler_sees_packages(self) -> None:
        package = demand(self.omar, 5)
        self.run_flow(tap("cr:r:S"), tap("cr:rt:0"), tap("cr:m:x"))
        flow.handle_update(message((today() + timedelta(days=6)).isoformat()))
        result = last(self.run_flow(tap("cr:w:3"), tap("cr:ok")))
        self.assertIn("<b>۱ بسته</b> پیدا شد", result["text"])
        self.assertIn("🔔 بسته‌ی جدید اومد خبرم کن", labels(result))
        self.assertEqual(data_of(result, "💬 ۱"), f"cr:i:{package.pk}")

    def test_mine_lists_requests_and_reopens_results(self) -> None:
        item = self.exact_demand(3)
        mine = last(flow.handle_update(message("📋 درخواست‌ها و سفرهای من")))
        self.assertIn("تورنتو ← تهران", mine["text"])
        reopened = self.run_flow(tap(f"cr:rs:{item.pk}"))
        self.assertEqual(reopened[-1]["method"], "sendMessage")
        self.assertIn("پیدا نشد", last(reopened)["text"])
        self.assertEqual(self.run_flow(tap("cr:rs:999999"))[0]["payload"]["text"], flow.OLD_BUTTON)

    def test_channel_button(self) -> None:
        CourierChannel.objects.update_or_create(country="CA", defaults={"username": "chamedoon_ca", "chat_id": None, "enabled": True})
        item = self.exact_demand(3)
        self.assertIn("📣 درخواستم رو در کانال کانادا بذار", labels(last(self.actions)))
        off = last(self.run_flow(tap(f"cr:ch:{item.pk}")))
        self.assertIn("فعلاً خاموش", off["text"])
        with override_settings(TELEGRAM_CHANNEL_ENABLED=True, TELEGRAM_CHANNEL_USERNAME="chamed0on"):
            on = last(self.run_flow(tap(f"cr:ch:{item.pk}")))
        self.assertIn("کانال کانادا", on["text"])
        self.assertIn("https://t.me/chamedoon_ca", str(on))


class IntroTests(CourierCase):
    def setUp(self) -> None:
        super().setUp()
        self.trip = supply(self.omar, 5)
        flow.handle_update(message("/start"))

    def test_switch_off_shows_the_published_contact_and_sends_nothing(self) -> None:
        shadow = make_user(telegram_user_id=SHADOW_ID, first_name="Ali", telegram_username="ali_public")
        imported = supply(shadow, 6, imported=True, source_url="https://t.me/some_channel/55")
        actions = self.run_flow(tap(f"cr:i:{imported.pk}"))
        reply = last(actions)
        self.assertIn("معرفی دوطرفه هنوز روشن نشده", reply["text"])
        self.assertIn("@ali_public", reply["text"])
        self.assertEqual(data_of(reply, "🔗 آگهی اصلی در کانال"), "https://t.me/some_channel/55")
        self.assertEqual([p["chat_id"] for p in screens(actions)], [SARA])
        native = last(self.run_flow(tap(f"cr:i:{self.trip.pk}")))
        self.assertIn("راه تماس عمومی ندارند", native["text"])
        self.assertNotIn("omar_fly", native["text"])
        self.assertFalse(CourierIntro.objects.exists())

    @override_settings(COURIER_INTROS_ENABLED=True)
    def test_consent_flow_swaps_usernames_only_on_accept(self) -> None:
        actions = self.run_flow(tap(f"cr:i:{self.trip.pk}"))
        mine, theirs = screens(actions)
        self.assertEqual(mine["chat_id"], SARA)
        self.assertIn("درخواست معرفی فرستاده شد", mine["text"])
        self.assertNotIn("omar_fly", mine["text"])
        self.assertEqual(theirs["chat_id"], 7101)
        self.assertIn("<b>Sara</b> می‌خواهد با شما در ارتباط باشد", theirs["text"])
        self.assertNotIn("sara_t", theirs["text"])
        intro = CourierIntro.objects.get()
        self.assertEqual(intro.status, IntroStatus.PENDING)
        self.assertEqual(labels(theirs), ["✅ قبول", "نه، ممنون"])
        self.assertIn("قبلاً رفته", last(self.run_flow(tap(f"cr:i:{self.trip.pk}")))["text"])
        stranger = self.run_flow(tap(f"cr:ia:{intro.pk}"))
        self.assertIn("قدیمیه", last(stranger)["text"])
        accepted = self.run_flow(tap(f"cr:ia:{intro.pk}", uid=7101, first="Omar", username="omar_fly"))
        omar_view, sara_view = screens(accepted)
        self.assertIn("@sara_t", omar_view["text"])
        self.assertEqual(sara_view["chat_id"], SARA)
        self.assertIn("@omar_fly", sara_view["text"])
        self.assertEqual(data_of(sara_view, "💬 پیام به Omar"), "https://t.me/omar_fly")
        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroStatus.ACCEPTED)
        self.assertIn("قبلاً وصل شدید", last(self.run_flow(tap(f"cr:i:{self.trip.pk}")))["text"])

    @override_settings(COURIER_INTROS_ENABLED=True)
    def test_decline_keeps_ids_private(self) -> None:
        self.run_flow(tap(f"cr:i:{self.trip.pk}"))
        intro = CourierIntro.objects.get()
        declined = self.run_flow(tap(f"cr:in:{intro.pk}", uid=7101, first="Omar", username="omar_fly"))
        omar_view, sara_view = screens(declined)
        self.assertIn("آیدی شما به ایشان داده نمی‌شود", omar_view["text"])
        self.assertIn("انجام نشد", sara_view["text"])
        self.assertNotIn("omar_fly", sara_view["text"])
        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroStatus.DECLINED)
        self.assertIn("ثبت شده", last(self.run_flow(tap(f"cr:ia:{intro.pk}", uid=7101, first="Omar", username="omar_fly")))["text"])

    @override_settings(COURIER_INTROS_ENABLED=True)
    def test_rate_limit_and_expiry(self) -> None:
        row = CourierSettings.load()
        row.intro_daily_limit = 1
        row.save()
        other = supply(self.reza, 7)
        self.run_flow(tap(f"cr:i:{self.trip.pk}"))
        self.assertIn("فردا دوباره", last(self.run_flow(tap(f"cr:i:{other.pk}")))["text"])
        intro = CourierIntro.objects.get()
        CourierIntro.objects.filter(pk=intro.pk).update(created_at=timezone.now() - timedelta(hours=conf.INTRO_EXPIRE_HOURS + 1))
        expired = self.run_flow(tap(f"cr:ia:{intro.pk}", uid=7101, first="Omar", username="omar_fly"))
        self.assertIn("منقضی", last(expired)["text"])

    @override_settings(COURIER_INTROS_ENABLED=True)
    def test_unreachable_owner_and_own_or_closed_requests(self) -> None:
        shadow = make_user(telegram_user_id=SHADOW_ID, first_name="Ali")
        imported = supply(shadow, 6, imported=True, source_url="https://t.me/some_channel/56")
        reply = last(self.run_flow(tap(f"cr:i:{imported.pk}")))
        self.assertIn("ربات چمدون را باز نکرده", reply["text"])
        self.assertIn("https://t.me/some_channel/56", str(reply))
        mine = supply(self.sara(), 8)
        self.assertIn("خودتونه", last(self.run_flow(tap(f"cr:i:{mine.pk}")))["text"])
        ItemRequest.objects.filter(pk=self.trip.pk).update(status=RequestStatus.CANCELLED)
        self.assertIn("فعال نیست", last(self.run_flow(tap(f"cr:i:{self.trip.pk}")))["text"])
        self.assertEqual(self.run_flow(tap("cr:i:999999"))[0]["payload"]["text"], flow.OLD_BUTTON)

    @override_settings(COURIER_INTROS_ENABLED=True)
    def test_intro_carries_the_requesters_own_request(self) -> None:
        demand(self.sara(), 4)
        actions = self.run_flow(tap(f"cr:i:{self.trip.pk}"))
        self.assertIn("📦 بسته‌ی ایشان:", screens(actions)[1]["text"])
        self.assertEqual(CourierIntro.objects.get().requester_request.user_id, self.sara().pk)


class RouteAlertTests(CourierCase):
    def make_request(self) -> ItemRequest:
        self.run_flow(tap("cr:r:D"), tap("cr:rt:0"), tap(f"cr:m:{texts.upcoming_months()[1][0]}"), tap("cr:w:3"), tap("cr:ok"))
        return ItemRequest.objects.get(user=self.sara())

    def test_bell_saves_a_route_alert_and_lists_it(self) -> None:
        item = self.make_request()
        saved = last(self.run_flow(tap(f"cr:al:n:{item.pk}")))
        alert = CourierRouteAlert.objects.get()
        self.assertEqual((alert.role, alert.origin_city, alert.destination_city), (RequestType.DEMAND, "toronto", "tehran"))
        self.assertEqual(alert.date_to, item.date_to)
        self.assertEqual(alert.weight_kg, Decimal("3.00"))
        self.assertIn("🔔 گوش به زنگ روشن شد", saved["text"])
        self.assertIn("(۱ از ۵)", saved["text"])
        self.assertIn("به‌زودی روشن می‌شود", saved["text"])
        again = last(self.run_flow(tap(f"cr:al:n:{item.pk}")))
        self.assertIn("دوباره روشن شد", again["text"])
        self.assertEqual(CourierRouteAlert.objects.count(), 1)

    def test_pause_resume_delete_and_cap(self) -> None:
        item = self.make_request()
        self.run_flow(tap(f"cr:al:n:{item.pk}"))
        alert = CourierRouteAlert.objects.get()
        self.assertIn("⏸ خاموش", last(self.run_flow(tap(f"cr:al:p:{alert.pk}")))["text"])
        alert.refresh_from_db()
        self.assertFalse(alert.active)
        self.run_flow(tap(f"cr:al:p:{alert.pk}"))
        alert.refresh_from_db()
        self.assertTrue(alert.active)
        for slug in ("vancouver", "montreal", "ottawa", "calgary"):
            alerts.create_alert(
                telegram_user_id=SARA, chat_id=SARA, user=None, role="DEMAND", origin=("CA", slug), destination=("IR", "tehran")
            )
        with self.assertRaises(alerts.AlertCapError):
            alerts.create_alert(
                telegram_user_id=SARA, chat_id=SARA, user=None, role="DEMAND", origin=("DE", "berlin"), destination=("IR", "tehran")
            )
        self.run_flow(tap(f"cr:al:d:{alert.pk}"))
        trip = supply(self.sara(), 9, origin=("DE", "berlin"))
        capped = last(self.run_flow(tap(f"cr:al:n:{trip.pk}")))
        self.assertIn("(۵ از ۵)", capped["text"])
        self.run_flow(tap(f"cr:al:d:{CourierRouteAlert.objects.first().pk}"))
        CourierRouteAlert.objects.create(telegram_user_id=SARA, chat_id=SARA, role="SUPPLY", origin_country="DE", origin_city="hamburg", destination_country="IR", destination_city="tehran")
        CourierRouteAlert.objects.create(telegram_user_id=SARA, chat_id=SARA, role="SUPPLY", origin_country="DE", origin_city="munich", destination_country="IR", destination_city="tehran")
        full = last(self.run_flow(tap(f"cr:al:n:{trip.pk}")))
        self.assertIn("حداکثر ۵ گوش به زنگ", full["text"])
        self.assertEqual(self.run_flow(tap("cr:al:p:999999"))[0]["payload"]["text"], flow.OLD_BUTTON)

    def test_new_alert_button_restarts_the_flow(self) -> None:
        reply = last(self.run_flow(tap("cr:al:new")))
        self.assertEqual(labels(reply), ["📦 بسته دارم، دنبال مسافرم", "🧳 مسافرم، جا دارم"])

    def alert(self, **extra) -> CourierRouteAlert:
        values = {
            "telegram_user_id": SARA,
            "chat_id": SARA,
            "role": "DEMAND",
            "origin": ("CA", "toronto"),
            "destination": ("IR", "tehran"),
            "date_from": today(),
            "date_to": today() + timedelta(days=20),
            "weight_kg": Decimal("3"),
            "user": None,
        }
        values.update(extra)
        return alerts.create_alert(**values)[0]

    def test_alerts_off_send_and_record_nothing(self) -> None:
        self.alert()
        with patch("notifications.telegram.call_telegram_api", return_value=OK) as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                supply(self.omar, 5)
        self.assertEqual([call for call in mocked.call_args_list if call.args[1].get("chat_id") == SARA], [])
        self.assertFalse(CourierAlertSent.objects.exists())

    @override_settings(COURIER_ALERTS_ENABLED=True)
    def test_matching_new_traveler_is_sent_once(self) -> None:
        alert = self.alert()
        with patch("notifications.telegram.call_telegram_api", return_value=OK) as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                trip = supply(self.omar, 5)
        sent = [call.args[1] for call in mocked.call_args_list if call.args[1].get("chat_id") == SARA]
        self.assertEqual(len(sent), 1)
        self.assertIn("🔔 <b>مسافر جدید</b> برای مسیر شما", sent[0]["text"])
        self.assertEqual(sent[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"], f"cr:i:{trip.pk}")
        self.assertTrue(CourierAlertSent.objects.filter(alert=alert, request=trip).exists())
        self.assertEqual(alerts.notify_new_request(trip.pk), 0)

    @override_settings(COURIER_ALERTS_ENABLED=True)
    def test_alert_matching_rules(self) -> None:
        alert = self.alert()
        cases = {
            "fits": supply(self.omar, 5),
            "too late": supply(self.reza, 25),
            "too small": supply(self.nima, 6, kg="2"),
            "other route": supply(make_user(telegram_user_id=7301, first_name="Ava", telegram_username="ava_t"), 5, dest=("IR", "shiraz")),
            "same side": demand(make_user(telegram_user_id=7302, first_name="Mo", telegram_username="mo_t"), 5),
            "own": supply(make_user(telegram_user_id=SARA, first_name="Sara", telegram_username="sara_t"), 5),
        }
        result = {name: alerts.matches(alert, item) for name, item in cases.items()}
        self.assertEqual(result, {"fits": True, "too late": False, "too small": False, "other route": False, "same side": False, "own": False})
        alert.active = False
        self.assertFalse(alerts.matches(alert, cases["fits"]))
        traveler_alert = self.alert(role="SUPPLY", telegram_user_id=7400, chat_id=7400, weight_kg=Decimal("1"))
        self.assertFalse(alerts.matches(traveler_alert, cases["same side"]))  # 2 kg package, 1 kg space
        self.assertTrue(alerts.matches(self.alert(role="SUPPLY", telegram_user_id=7401, chat_id=7401, weight_kg=None), cases["same side"]))

    @override_settings(COURIER_ALERTS_ENABLED=True)
    def test_daily_cap(self) -> None:
        row = CourierSettings.load()
        row.alert_daily_cap = 1
        row.save()
        self.alert()
        with patch("notifications.telegram.call_telegram_api", return_value=OK):
            with self.captureOnCommitCallbacks(execute=True):
                supply(self.omar, 5)
                supply(self.reza, 6)
        self.assertEqual(CourierAlertSent.objects.count(), 1)

    @override_settings(COURIER_ALERTS_ENABLED=True)
    def test_alert_message_buttons_work_from_the_dm(self) -> None:
        alert = self.alert()
        text, keyboard = alerts.alert_message(alert, supply(self.omar, 5))
        self.assertIn("✈️", text)
        self.assertEqual(keyboard[1][0]["callback_data"], f"cr:al:p:{alert.pk}")


class ProhibitedTests(CourierCase):
    def test_list_shown_from_the_button_and_in_help(self) -> None:
        shown = last(self.run_flow(tap("cr:pr")))
        self.assertIn("⚠️ <b>چه چیزهایی ممنوعه؟</b>", shown["text"])
        for item in ("دارو بدون نسخه", "مایعات", "مواد قابل اشتعال", "پول نقد", "مواد غذایی تازه", "ایران، کانادا، آلمان"):
            self.assertIn(item, shown["text"])
        self.assertIn("قوانین گمرک هر کشور را خودتان چک کنید", shown["text"])
        help_text = last(flow.handle_update(message("❓ راهنما و ممنوعه‌ها")))["text"]
        self.assertIn("چه چیزهایی ممنوعه؟", help_text)
        self.assertIn("قوانین گمرک", last(flow.handle_update(message("/help")))["text"])

    def test_admin_text_replaces_the_list(self) -> None:
        row = CourierSettings.load()
        row.prohibited_text = "• باتری لیتیومی\n\n- عطر"
        row.save()
        self.assertEqual(texts.prohibited_items(), ["باتری لیتیومی", "عطر"])
        self.assertIn("• عطر", texts.prohibited_text())


class ChannelTemplateTests(CourierCase):
    def test_traveler_post(self) -> None:
        trip = supply(self.omar, 5, payload={"excluded_category_codes": ["MEDICINE"], "item_category_codes": ["DOCUMENTS", "CLOTHES"]})
        lines = _format_request_message(trip).split("\n")
        flight = trip.flight_date
        self.assertEqual(lines[0], "🧳 مسافر · تورنتو ← تهران")
        self.assertEqual(lines[1], f"📅 {texts.day_month(flight)}")
        self.assertEqual(lines[2], "⚖️ جا: ۵ کیلو · مدارک و لباس")
        self.assertEqual(lines[3], "🚫 دارو نه")
        self.assertEqual(lines[4], "💬 هزینه: توافقی")
        self.assertEqual(lines[5], f"#تورنتو_تهران #مسافر #{texts.month_name(flight)}")
        self.assertNotIn("omar", "\n".join(lines).lower())

    def test_package_post_open_date_and_two_stops(self) -> None:
        payload = {
            "type": "DEMAND",
            "origin_country": "IR",
            "origin_city": "tehran",
            "destination_country": "CA",
            "destination_city": "montreal",
            "destination_cities": [{"country": "CA", "city": "toronto"}, {"country": "CA", "city": "montreal"}],
            "desired_date_open": True,
            "date_to": (today() + timedelta(days=20)).isoformat(),
            "weight_kg": "2",
            "item_category_codes": ["CLOTHES"],
        }
        item = create_item_request(self.omar, payload)
        text = _format_request_message(item)
        self.assertTrue(text.startswith("📦 بسته دارم · تهران ← تورنتو و مونترال\n"))
        self.assertIn(f"📅 تا {texts.day_month(item.date_to)}", text)
        self.assertIn("⚖️ ۲ کیلو · لباس", text)
        self.assertIn("🚫 اقلام ممنوعه نه", text)
        self.assertTrue(text.endswith(f"#تهران_تورنتو #تهران_مونترال #بسته #{texts.month_name(item.date_to)}"))
        self.assertTrue(_format_request_message(item, unavailable=True).startswith("❌"))

    def test_hashtag_words(self) -> None:
        self.assertEqual(texts.hashtag_word("لس‌آنجلس"), "لس_آنجلس")
        self.assertEqual(texts.hashtag_word("New York"), "New_York")


def login(client, user) -> None:
    session = client.session
    session[SESSION_USER_KEY] = user.pk
    session[SESSION_LOCALE_KEY] = "fa"
    session.save()


class MiniAppCourierTests(CourierCase):
    def setUp(self) -> None:
        super().setUp()
        self.me = make_user(telegram_user_id=SARA, first_name="Sara", telegram_username="sara_t")
        login(self.client, self.me)

    def test_page_lists_travelers_nearest_first_and_filters(self) -> None:
        far = supply(self.omar, 30)
        near = supply(self.reza, 4, kg="2")
        supply(self.nima, 6, dest=("IR", "shiraz"))
        package = demand(make_user(telegram_user_id=7501, first_name="Mina", telegram_username="mina_t"), 5)
        supply(self.me, 5)
        page = self.client.get("/app/courier/?role=D&from=CA:toronto&to=IR:tehran")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("۲ مسافر · نزدیک‌ترین تاریخ اول", html)
        self.assertLess(html.index(f"/app/explore/{near.pk}/"), html.index(f"/app/explore/{far.pk}/"))
        self.assertNotIn(f"/app/explore/{package.pk}/", html)
        self.assertIn("🧳 مسافر · تورنتو ← تهران", html)
        self.assertIn("💬 هزینه: توافقی", html)
        self.assertIn("چه چیزهایی ممنوعه؟", html)
        self.assertIn('href="/app/courier/alerts/"', html)
        sized = self.client.get("/app/courier/?role=D&from=CA:toronto&to=IR:tehran&size=3").content.decode()
        self.assertNotIn(f"/app/explore/{near.pk}/", sized)
        self.assertIn(f"/app/explore/{far.pk}/", sized)
        month_key = next(key for key, start, end in texts.upcoming_months(count=3) if start <= near.flight_date <= end)
        in_month = self.client.get(f"/app/courier/?role=D&from=CA:toronto&to=IR:tehran&month={month_key}").content.decode()
        self.assertIn(f"/app/explore/{near.pk}/", in_month)
        packages = self.client.get("/app/courier/?role=S").content.decode()
        self.assertIn(f"/app/explore/{package.pk}/", packages)
        self.assertIn("📦 بسته دارم · تورنتو ← تهران", packages)
        self.assertNotIn(f"/app/explore/{near.pk}/", packages)

    def test_size_filter_for_packages_and_documents(self) -> None:
        heavy = demand(self.omar, 5, kg="8")
        light = demand(self.reza, 5, kg="1")
        page = self.client.get("/app/courier/?role=S&size=1").content.decode()
        self.assertIn(f"/app/explore/{light.pk}/", page)
        self.assertNotIn(f"/app/explore/{heavy.pk}/", page)
        self.assertNotIn(f"/app/explore/{light.pk}/", self.client.get("/app/courier/?role=S&size=doc").content.decode())
        trip = supply(self.nima, 5)
        self.assertIn(f"/app/explore/{trip.pk}/", self.client.get("/app/courier/?role=D&size=doc").content.decode())

    def test_notify_me_saves_an_alert_and_the_alerts_page_manages_it(self) -> None:
        month_key, start, end = texts.upcoming_months()[1]
        response = self.client.post("/app/courier/alert/", {"role": "D", "from": "CA:toronto", "to": "IR:tehran", "month": month_key, "size": "3"})
        self.assertRedirects(response, "/app/courier/alerts/?saved=1", fetch_redirect_response=False)
        alert = CourierRouteAlert.objects.get()
        self.assertEqual((alert.telegram_user_id, alert.role, alert.date_from, alert.date_to, alert.weight_kg), (SARA, "DEMAND", start, end, Decimal("3.00")))
        page = self.client.get("/app/courier/alerts/?saved=1").content.decode()
        self.assertIn("📦 تورنتو ← تهران", page)
        self.assertIn("ذخیره شد", page)
        self.client.post("/app/courier/alerts/", {"alert": alert.pk, "action": "pause"})
        alert.refresh_from_db()
        self.assertFalse(alert.active)
        self.client.post("/app/courier/alerts/", {"alert": alert.pk, "action": "delete"})
        self.assertFalse(CourierRouteAlert.objects.exists())

    def test_notify_me_needs_a_route_and_respects_the_cap(self) -> None:
        response = self.client.post("/app/courier/alert/", {"role": "D", "from": "CA:toronto"})
        self.assertIn("need_route=1", response["Location"])
        self.assertIn("disabled", self.client.get("/app/courier/").content.decode())
        for slug in ("toronto", "vancouver", "montreal", "ottawa", "calgary"):
            self.client.post("/app/courier/alert/", {"role": "S", "from": f"CA:{slug}", "to": "IR:tehran"})
        self.assertEqual(CourierRouteAlert.objects.count(), 5)
        capped = self.client.post("/app/courier/alert/", {"role": "S", "from": "DE:berlin", "to": "IR:tehran"})
        self.assertRedirects(capped, "/app/courier/alerts/?cap=1", fetch_redirect_response=False)
        self.assertIn("حداکثر ۵", self.client.get("/app/courier/alerts/?cap=1").content.decode())

    def test_startapp_and_login(self) -> None:
        self.assertEqual(startapp_path("courier"), "/app/courier/")
        self.assertEqual(startapp_path("courier_alerts"), "/app/courier/alerts/")
        self.client.logout()
        self.assertEqual(self.client.get("/app/courier/").status_code, 302)


BOT_SECRET = "test-bot-secret"


@override_settings(BOT_SERVICE_SECRET=BOT_SECRET, TELEGRAM_WEBHOOK_SECRET="", TELEGRAM_BOT_TOKEN="123:courier")
class TransportTests(CourierCase):
    def test_polling_bot_relay_returns_actions(self) -> None:
        self.assertEqual(self.client.post("/api/bot/courier/update/", message("/start"), content_type="application/json").status_code, 403)
        response = self.client.post(
            "/api/bot/courier/update/", message("/start"), content_type="application/json", HTTP_X_BOT_SECRET=BOT_SECRET
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([action["method"] for action in response.json()["actions"]], ["sendMessage", "sendMessage"])

    def test_webhook_chat_is_off_by_default(self) -> None:
        with patch("notifications.telegram.call_telegram_api", return_value=OK) as mocked:
            self.client.post("/api/telegram/webhook/", message("/start"), content_type="application/json")
            self.client.post("/api/telegram/webhook/", tap("cr:r:D"), content_type="application/json")
        mocked.assert_not_called()
        self.assertFalse(CourierChat.objects.exists())

    @override_settings(COURIER_BOT_CHAT_ENABLED=True)
    def test_webhook_runs_the_flow_when_switched_on(self) -> None:
        with patch("notifications.telegram.call_telegram_api", return_value=OK) as mocked:
            self.client.post("/api/telegram/webhook/", message("/start"), content_type="application/json")
            self.client.post("/api/telegram/webhook/", tap("cr:r:S"), content_type="application/json")
        methods = [call.args[0] for call in mocked.call_args_list]
        self.assertEqual(methods, ["sendMessage", "sendMessage", "answerCallbackQuery", "editMessageText"])

    def test_execute_sends_a_new_message_when_an_edit_fails(self) -> None:
        def fake(method, payload):
            return None if method == "editMessageText" else OK

        with patch("notifications.telegram.call_telegram_api", side_effect=fake) as mocked:
            done = execute(flow.handle_update(tap("cr:r:D")))
        self.assertEqual(done, 2)
        self.assertEqual([call.args[0] for call in mocked.call_args_list], ["answerCallbackQuery", "editMessageText", "sendMessage"])
        self.assertNotIn("message_id", mocked.call_args_list[-1].args[1])


class TextHelperTests(TestCase):
    def test_typed_dates(self) -> None:
        base = today()
        self.assertIsNone(texts.parse_typed_date("2001-01-01"))
        self.assertIsNone(texts.parse_typed_date("2026-02-31"))
        self.assertIsNone(texts.parse_typed_date("hello"))
        later = base + timedelta(days=30)
        self.assertEqual(texts.parse_typed_date(f"{later.day}/{later.month}"), later)
        self.assertEqual(texts.parse_typed_date(f"{texts.fa(later.day)} {texts.month_name(later)}"), later)
        yesterday = base - timedelta(days=1)
        self.assertEqual(texts.parse_typed_date(f"{yesterday.day}/{yesterday.month}").year, base.year + 1)

    def test_sizes_and_relative_days(self) -> None:
        self.assertEqual(texts.relative_day(timezone.now()), "امروز ثبت شد")
        self.assertEqual(texts.relative_day(timezone.now() - timedelta(days=1)), "دیروز ثبت شد")
        self.assertEqual(texts.relative_day(timezone.now() - timedelta(days=3)), "۳ روز پیش")
        self.assertEqual(texts.relative_day(None), "")
        self.assertEqual(texts.kg_text(Decimal("2.50")), "۲.۵ کیلو")
        self.assertEqual(texts.day_month(None), "")
        self.assertEqual(texts.month_end(2026, 12).day, 31)

    def test_switches_default_off(self) -> None:
        self.assertFalse(conf.bot_chat_enabled())
        self.assertFalse(conf.intros_enabled())
        self.assertFalse(conf.alerts_enabled())
        row = CourierSettings.load()
        row.alerts_enabled = True
        row.save()
        self.assertTrue(conf.alerts_enabled())
        self.assertEqual(str(row), "Courier settings")
