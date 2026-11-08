"""The housing bot v2 behind `HomeSettings.bot_v2`. Every Telegram call goes to a fake; nothing leaves the machine."""

from __future__ import annotations

import io
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from ai.openrouter import ChatResult
from home import alerts, botchat, botlog, botparse, botv2, conf, freshness
from home.bot import BotError
from home.models import (
    ContactReveal,
    HomeAiUsage,
    HomeAlert,
    HomeAlertSent,
    HomeArea,
    HomeBotMessage,
    HomeChannel,
    HomeChatState,
    HomeCityWaitlist,
    HomeSaved,
    HomeSearchQuery,
    HomeSettings,
    ListingStatus,
)
from tests.test_home_alerts import OTHER_ID, USER_ID, FakeBot, message, tap
from tests.test_home_publish import ca_listing

TOKEN = "123456:TESTTOKEN"
BIDI = str.maketrans("", "", "‎‏")


def plain(text: str) -> str:
    return text.translate(BIDI)


def turn_on(**extra) -> HomeSettings:
    values = {"bot_token": TOKEN, "bot_username": "chamedoon_home_bot", "bot_v2": True, "contact_gate_on": True, **extra}
    row, _ = HomeSettings.objects.update_or_create(pk=1, defaults=values)
    return row


def city(name: str = "Toronto", country: str = "CA", aliases: str = "North York, Richmond Hill, Scarborough") -> HomeChannel:
    row, _ = HomeChannel.objects.update_or_create(
        country=country, city=name, defaults={"username": f"chamedoon_home_{name.lower()}", "aliases": aliases, "enabled": True, "title": f"چمدون خانه · {name}"}
    )
    return row


def areas() -> None:
    for canonical, persian, group, aliases in (
        ("north york", "نورث یورک", "شمال", "north york"),
        ("richmond hill", "ریچموند هیل", "شمال", ""),
        ("scarborough", "اسکاربرو", "شرق", ""),
    ):
        HomeArea.objects.update_or_create(country="CA", city="Toronto", canonical=canonical, defaults={"fa": persian, "group": group, "aliases": aliases})


class InlinePool:
    """Runs the model call on the test's own thread, so it can read the test's transaction."""

    def __init__(self, **_kwargs) -> None:
        pass

    def submit(self, function, *args, **kwargs):
        future = Future()
        future.set_result(function(*args, **kwargs))
        return future

    def shutdown(self, **_kwargs) -> None:
        pass


class BotCase(TestCase):
    def setUp(self) -> None:
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        turn_on()
        self.toronto = city()
        city("Berlin", "DE", "Neukölln")
        areas()

    def say(self, text: str, user_id: int = USER_ID) -> None:
        botchat.handle_update(message(text, user_id))

    def press(self, data: str, user_id: int = USER_ID) -> None:
        botchat.handle_update(tap(data, user_id))

    def lives_in_toronto(self, user_id: int = USER_ID) -> None:
        HomeChatState.objects.update_or_create(telegram_user_id=user_id, defaults={"channel": self.toronto})

    def texts(self) -> list[str]:
        return [plain(payload["text"]) for name, payload in self.fake.calls if name in {"sendMessage", "editMessageText"}]

    def labels(self) -> list[str]:
        return [plain(button["text"]) for button in self.fake.buttons()]

    def toast(self) -> str:
        answers = [payload for name, payload in self.fake.calls if name == "answerCallbackQuery"]
        return answers[-1].get("text", "") if answers else ""

    def reply_keyboard(self) -> list[str]:
        for name, payload in reversed(self.fake.calls):
            if name == "sendMessage" and "keyboard" in (payload.get("reply_markup") or {}):
                return [button["text"] for row in payload["reply_markup"]["keyboard"] for button in row]
        return []


class FlagOff(TestCase):
    """With the flag off the bot does what it did: botv2 never runs, the old copy and buttons go out."""

    def setUp(self) -> None:
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        turn_on(bot_v2=False)
        city()

    def test_the_flag_defaults_to_off(self) -> None:
        HomeSettings.objects.all().delete()
        row = HomeSettings.load()
        self.assertFalse(row.bot_v2)
        self.assertFalse(row.ai_search_enabled)
        self.assertFalse(conf.bot_v2_for(USER_ID))

    def test_v2_does_not_run(self) -> None:
        with patch("home.botv2.handle_update") as v2:
            botchat.handle_update(message("/start"))
            botchat.handle_update(tap("mn"))
        v2.assert_not_called()

    def test_start_is_the_conversation_welcome(self) -> None:
        from home import assistant

        botchat.handle_update(message("/start"))
        (first,) = self.fake.sent()
        self.assertEqual(first["text"], assistant.WELCOME)
        self.assertEqual(first["reply_markup"], {"remove_keyboard": True})

    def test_free_text_goes_to_the_conversation_and_nothing_is_searched_without_the_model(self) -> None:
        from home import assistant

        botchat.handle_update(message("اتاق نورث یورک"))
        self.assertEqual(self.fake.last_text(), assistant.FALLBACK)
        self.assertFalse(HomeSearchQuery.objects.exists())

    def test_the_match_message_is_the_old_one(self) -> None:
        listing = ca_listing()
        alert = HomeAlert.objects.create(telegram_user_id=USER_ID, chat_id=USER_ID, country="CA", city="Toronto", currency="CAD", answered=["type", "budget", "area"])
        alerts.send_alert(alert, listing, None, number=1)
        payload = self.fake.sent()[-1]
        self.assertEqual(payload["text"], alerts.alert_text(alert, listing, number=1))
        self.assertEqual(payload["reply_markup"], alerts.alert_keyboard(alert, listing, None))

    def test_the_summary_is_the_old_one(self) -> None:
        listing = ca_listing()
        alert = HomeAlert.objects.create(telegram_user_id=USER_ID, chat_id=USER_ID, country="CA", city="Toronto", currency="CAD", answered=["type", "budget", "area"])
        alerts.send_summary([(alert, listing)], {alert.pk: 1})
        payload = self.fake.sent()[-1]
        self.assertEqual(payload["text"], alerts.summary_text([(alert, listing)], {alert.pk: 1}))
        self.assertEqual(payload["reply_markup"], alerts.summary_keyboard(alert))

    def test_the_allowlist_opens_v2_for_one_person_only(self) -> None:
        turn_on(bot_v2=False, bot_v2_allowlist=f"{USER_ID}, 99")
        self.assertTrue(conf.bot_v2_for(USER_ID))
        self.assertFalse(conf.bot_v2_for(OTHER_ID))
        self.assertFalse(conf.bot_v2_for(None))


class StartAndCity(BotCase):
    def test_first_start_asks_the_country_then_the_city_then_the_prompt(self) -> None:
        self.say("/start")
        self.assertIn("<b>کشور شما؟</b>", self.texts()[-1])
        self.assertEqual(self.labels(), ["🇨🇦 کانادا", "🇩🇪 آلمان"])
        self.press("v2:cc:CA")
        self.assertEqual(self.texts()[-1], "<b>کدام شهر؟</b>")
        self.assertIn("🇨🇦 Toronto".replace("Toronto", "تورنتو"), self.labels())
        self.assertNotIn("🇩🇪 برلین", self.labels())
        self.press(f"v2:ct:{self.toronto.pk}")
        self.assertIn("شهر شما: <b>تورنتو</b>", self.texts()[-2])
        self.assertIn("«اتاق نورث یورک تا ۱۲۰۰ از دسامبر»", self.texts()[-1])
        self.assertEqual(
            self.reply_keyboard(),
            ["🔎 جستجوی خانه", "🔔 گوش به زنگ‌های من", "➕ ثبت آگهی", "🔖 نشان‌شده‌ها", "📍 تورنتو · تغییر شهر", "❓ راهنما"],
        )

    def test_a_returning_person_gets_hello_and_the_latest(self) -> None:
        self.lives_in_toronto()
        ca_listing(price_amount=1150, area="North York")
        self.say("/start")
        self.assertEqual(self.texts()[-2], "سلام Sara 👋")
        self.assertIn("<b>تورنتو</b> · تازه‌ترین‌ها", self.texts()[-1])
        self.assertIn("<b>۱.</b> <b>۱٬۱۵۰ CA$</b> · اتاق · نورث یورک", self.texts()[-1])

    def test_a_channel_deep_link_opens_the_alert_setup(self) -> None:
        self.say("/start alert_home_ca_toronto")
        self.assertIn("من ربات <b>چمدون خانه</b> هستم", self.texts()[-2])
        self.assertIn("<b>۱ از ۳</b>", self.texts()[-1])

    def test_a_malformed_deep_link_falls_back_to_the_normal_start(self) -> None:
        self.say("/start ☃☃☃")
        self.assertIn("کشور شما؟", self.texts()[-1])

    def test_another_city_joins_the_waitlist_once(self) -> None:
        self.lives_in_toronto()
        self.press("v2:other")
        self.say("اتاوا")
        self.assertIn("<b>اتاوا</b> هنوز در چمدون نیست", self.texts()[-1])
        self.assertIn("📍 تورنتو را ببینید", self.labels())
        self.press("v2:other")
        self.say("اتاوا")
        self.assertEqual(HomeCityWaitlist.objects.count(), 1)

    def test_a_photo_is_not_understood_and_an_old_button_says_so(self) -> None:
        botchat.handle_update({"message": {"message_id": 1, "from": {"id": USER_ID, "first_name": "Sara"}, "chat": {"id": USER_ID, "type": "private"}, "photo": [{}]}})
        self.assertEqual(self.texts()[-1], botv2.NOT_TEXT)
        self.press("v2:d:999999")
        self.assertEqual(self.toast(), botv2.OLD_BUTTON)
        self.press("v2:nonsense")
        self.assertEqual(self.toast(), botv2.OLD_BUTTON)

    def test_buttons_from_before_the_switch_still_work(self) -> None:
        self.lives_in_toronto()
        self.press("al:list")
        self.assertIn("گوش به زنگی ندارید", self.texts()[-1])

    def test_a_group_is_ignored(self) -> None:
        update = message("سلام")
        update["message"]["chat"] = {"id": -100, "type": "supergroup"}
        botchat.handle_update(update)
        self.assertEqual(self.fake.calls, [])


class SimpleParser(BotCase):
    def parse(self, text: str, today: date = date(2026, 11, 3)) -> botparse.Parsed:
        return botparse.parse_simple(text, self.toronto, today)

    def test_the_board_sentence(self) -> None:
        parsed = self.parse("یه اتاق نورث یورک زیر ۱۲۰۰ از دسامبر فقط خانم")
        self.assertEqual(parsed.fields["types"], ["room"])
        self.assertEqual(parsed.fields["areas"], ["north york"])
        self.assertEqual(parsed.fields["max"], 1200)
        self.assertEqual(parsed.fields["move"], "2026-12-01")
        self.assertEqual(parsed.fields["gender"], "female")
        self.assertEqual(parsed.not_understood, [])

    def test_bedrooms_and_english(self) -> None:
        parsed = self.parse("2 bedroom richmond hill under 2000 from january")
        self.assertEqual(parsed.fields["rooms"], 2)
        self.assertEqual(parsed.fields["types"], ["apartment"])
        self.assertEqual(parsed.fields["areas"], ["richmond hill"])
        self.assertEqual(parsed.fields["max"], 2000)
        self.assertEqual(parsed.fields["move"], "2027-01-01")

    def test_a_past_month_means_next_year_and_persian_digits_with_a_day(self) -> None:
        self.assertEqual(self.parse("از ۵ مارس").fields["move"], "2027-03-05")

    def test_a_place_the_city_does_not_have_is_unmapped(self) -> None:
        parsed = self.parse("اتاق نزدیک پیرسون زیر ۱۰۰۰")
        self.assertEqual(parsed.unmapped, ["پیرسون"])
        self.assertNotIn("areas", parsed.fields)

    def test_a_small_number_is_not_a_budget_and_empty_text_finds_nothing(self) -> None:
        self.assertNotIn("max", self.parse("اتاق 2 نفره").fields)
        self.assertEqual(self.parse("   ").fields, {})


class AiParser(BotCase):
    GOOD = '{"types":["room"],"rooms":null,"max":1000,"areas":["Pearson","North York"],"move":"2026-12-01","gender":null,"term":null,"furnished":null,"bills":null,"confidence":{"area":0.38},"overall":0.8}'

    def test_it_is_not_called_while_switched_off_or_stopped(self) -> None:
        for values in ({"ai_search_enabled": False}, {"ai_search_enabled": True, "ai_search_emergency_stop": True}):
            turn_on(**values)
            with patch("home.botparse.review_completion") as model:
                parsed = botparse.parse("اتاق", self.toronto, USER_ID)
            model.assert_not_called()
            self.assertEqual(parsed.parser, botparse.SIMPLE)

    def test_a_good_answer_keeps_only_names_the_city_has(self) -> None:
        turn_on(ai_search_enabled=True)
        with patch("home.botparse.review_completion", return_value=ChatResult(ok=True, text=self.GOOD, model="gpt-5-mini")):
            parsed = botparse.parse("اتاق", self.toronto, USER_ID)
        self.assertEqual(parsed.parser, "gpt-5-mini")
        self.assertEqual(parsed.fields["areas"], ["north york"])
        self.assertEqual(parsed.unmapped, ["Pearson"])
        self.assertEqual(parsed.field_confidence["area"], 0.38)

    def test_late_wrong_or_refused_falls_back(self) -> None:
        turn_on(ai_search_enabled=True)
        for outcome in ({"side_effect": FutureTimeout()}, {"return_value": ChatResult(ok=False, error="boom")}, {"return_value": ChatResult(ok=True, text="not json")}):
            with patch("home.botparse.review_completion", **outcome):
                parsed = botparse.parse("اتاق نورث یورک", self.toronto, USER_ID)
            self.assertTrue(parsed.ai_failed)
            self.assertEqual(parsed.parser, botparse.SIMPLE)
            self.assertEqual(parsed.fields["areas"], ["north york"])

    def test_the_cap_stops_the_ai_and_the_person_is_told(self) -> None:
        turn_on(ai_search_enabled=True, ai_search_user_cap=1)
        self.lives_in_toronto()
        HomeAiUsage.objects.create(telegram_user_id=USER_ID, day=timezone.localdate(), attempts=1)
        with patch("home.botparse.review_completion") as model:
            self.say("اتاق نورث یورک")
        model.assert_not_called()
        self.assertIn("امروز سقف جستجوی متنی پر شد", self.texts()[-1])
        self.assertIn("🎛 جستجو با سؤال‌ها", self.labels())
        self.assertEqual(HomeSearchQuery.objects.order_by("-id").first().status, "capped")


class AiBudget(BotCase):
    def attempts(self, telegram_id: int = USER_ID) -> int:
        row = HomeAiUsage.objects.filter(telegram_user_id=telegram_id, day=timezone.localdate()).first()
        return row.attempts if row is not None else 0

    def test_a_call_is_counted_before_it_goes_out(self) -> None:
        turn_on(ai_search_enabled=True)
        seen = []

        def model(*_args, **_kwargs):
            seen.append((self.attempts(), self.attempts(botparse.ALL_PEOPLE)))
            return ChatResult(ok=True, text='{"overall":0.9}', model="gpt-5-mini")

        with patch("home.botparse.review_completion", side_effect=model), patch("home.botparse.ThreadPoolExecutor", InlinePool):
            botparse.parse("اتاق", self.toronto, USER_ID)
        self.assertEqual(seen, [(1, 1)])

    def test_late_wrong_and_refused_calls_all_count_against_both_caps(self) -> None:
        turn_on(ai_search_enabled=True)
        for number, outcome in enumerate(
            ({"side_effect": FutureTimeout()}, {"return_value": ChatResult(ok=False, error="boom")}, {"return_value": ChatResult(ok=True, text="not json")}), start=1
        ):
            with patch("home.botparse.review_completion", **outcome):
                parsed = botparse.parse("اتاق", self.toronto, USER_ID)
            self.assertEqual(parsed.parser, botparse.SIMPLE)
            self.assertEqual(self.attempts(), number)
            self.assertEqual(self.attempts(botparse.ALL_PEOPLE), number)

    def test_failed_calls_use_up_the_cap(self) -> None:
        turn_on(ai_search_enabled=True, ai_search_user_cap=2)
        with patch("home.botparse.review_completion", side_effect=FutureTimeout()) as model:
            for _ in range(3):
                parsed = botparse.parse("اتاق", self.toronto, USER_ID)
        self.assertEqual(model.call_count, 2)
        self.assertTrue(parsed.capped)

    def test_forget_does_not_reset_the_budget(self) -> None:
        turn_on(ai_search_enabled=True, ai_search_user_cap=1)
        self.lives_in_toronto()
        with patch("home.botparse.review_completion", return_value=ChatResult(ok=True, text='{"overall":0.9}', model="gpt-5-mini")) as model:
            self.say("اتاق نورث یورک")
            self.press("v2:fy")
            self.assertFalse(HomeSearchQuery.objects.exists())
            self.say("اتاق نورث یورک")
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.attempts(), 1)
        self.assertIn("امروز سقف جستجوی متنی پر شد", self.texts()[-1])

    def test_the_global_cap_refuses_and_leaves_the_person_uncounted(self) -> None:
        turn_on(ai_search_enabled=True, ai_search_global_cap=1)
        HomeAiUsage.objects.create(telegram_user_id=botparse.ALL_PEOPLE, day=timezone.localdate(), attempts=1)
        with patch("home.botparse.review_completion") as model:
            parsed = botparse.parse("اتاق", self.toronto, USER_ID)
        model.assert_not_called()
        self.assertTrue(parsed.capped)
        self.assertEqual(self.attempts(), 0)

    def test_nothing_is_counted_while_off_or_stopped(self) -> None:
        for values in ({"ai_search_enabled": False}, {"ai_search_enabled": True, "ai_search_emergency_stop": True}):
            turn_on(**values)
            botparse.parse("اتاق", self.toronto, USER_ID)
        self.assertFalse(HomeAiUsage.objects.exists())

    def test_the_counter_holds_no_text(self) -> None:
        self.assertEqual({field.name for field in HomeAiUsage._meta.get_fields()}, {"id", "telegram_user_id", "day", "attempts"})

    def test_a_new_day_starts_a_new_budget(self) -> None:
        turn_on(ai_search_enabled=True, ai_search_user_cap=1)
        HomeAiUsage.objects.create(telegram_user_id=USER_ID, day=timezone.localdate() - timedelta(days=1), attempts=1)
        with patch("home.botparse.review_completion", return_value=ChatResult(ok=True, text='{"overall":0.9}', model="gpt-5-mini")) as model:
            botparse.parse("اتاق", self.toronto, USER_ID)
        model.assert_called_once()


class TextSearch(BotCase):
    def setUp(self) -> None:
        super().setUp()
        self.lives_in_toronto()

    def test_understood_text_shows_what_was_understood_with_chips_and_logs_it(self) -> None:
        listing = ca_listing(price_amount=1150, area="North York", property_type="room", roommate_gender="any")
        self.say("اتاق نورث یورک تا ۱۲۰۰")
        text = self.texts()[-1]
        self.assertTrue(text.startswith("فهمیدم:\n<b>تورنتو</b> · اتاق · نورث یورک · تا ۱٬۲۰۰ CA$"), text)
        self.assertIn("۱ آگهی · ۱ تا ۱", text)
        self.assertEqual(
            self.labels(),
            ["✕ اتاق", "✕ نورث یورک", "✕ تا ۱٬۲۰۰ CA$", "۱", "🔔 گوش به زنگ همین", "✏️ اصلاح"],
        )
        query = HomeSearchQuery.objects.get()
        self.assertEqual(query.raw_text, "اتاق نورث یورک تا ۱۲۰۰")
        self.assertEqual(query.parser, "regex")
        self.assertEqual(query.results_count, 1)
        self.assertEqual(list(HomeBotMessage.objects.order_by("id").values_list("direction", "kind")), [("in", "text"), ("out", "results")])
        self.press(f"v2:d:{listing.pk}")
        query.refresh_from_db()
        self.assertEqual(query.clicked_listing_ids, [listing.pk])

    def test_a_chip_removes_its_filter(self) -> None:
        ca_listing(price_amount=1150, area="Scarborough", city="Scarborough", property_type="room")
        self.say("اتاق نورث یورک تا ۱۲۰۰")
        self.assertIn("آگهی‌ای با این شرایط نیست", self.texts()[-1])
        self.press("v2:rm:areas")
        self.assertIn("۱ آگهی", self.texts()[-1])
        self.assertNotIn("نورث یورک", self.texts()[-1].split("\n")[0])

    def test_leftover_words_get_the_simple_search_copy(self) -> None:
        ca_listing(price_amount=1150, area="North York", property_type="room")
        self.say("اتاق نورث یورک تا ۱۲۰۰ قشنگ")
        text = self.texts()[-1]
        self.assertTrue(text.startswith("فعلاً نمی‌توانم متن را کامل بخوانم؛ <b>جستجوی ساده</b> انجام شد:"), text)
        self.assertIn("«قشنگ» را متوجه نشدم.", text)
        self.assertIn("🎛 جستجو با سؤال‌ها", self.labels())

    def test_a_failed_ai_is_logged_as_a_fallback(self) -> None:
        turn_on(ai_search_enabled=True)
        ca_listing(price_amount=1150, area="North York", property_type="room")
        with patch("home.botparse.review_completion", side_effect=FutureTimeout()):
            self.say("اتاق نورث یورک")
        self.assertIn("جستجوی ساده", self.texts()[-1])
        self.assertEqual(HomeSearchQuery.objects.get().status, "fallback")

    def test_an_unknown_place_asks_with_a_way_out(self) -> None:
        self.say("اتاق نزدیک پیرسون زیر ۱۰۰۰")
        self.assertEqual(self.texts()[-1], "«پیرسون» را در محله‌های تورنتو پیدا نکردم. منظورتان کدام است؟")
        self.assertIn("📍 همه‌ی تورنتو", self.labels())
        self.assertIn("همین را جستجو کنید", self.labels())
        self.assertEqual(HomeBotMessage.objects.filter(direction="out").first().kind, "clarify")
        self.press("v2:aw")
        self.assertIn("تا ۱٬۰۰۰ CA$", self.texts()[-1])

    def test_text_with_nothing_in_it_asks_again(self) -> None:
        self.say("سلام")
        self.assertIn("متوجه نشدم", self.texts()[-1])

    def test_no_city_asks_the_country_first(self) -> None:
        HomeChatState.objects.all().delete()
        self.say("اتاق")
        self.assertIn("کشور شما؟", self.texts()[-1])

    def test_html_in_a_search_is_escaped(self) -> None:
        self.say("<b>اتاق</b> نزدیک <script>")
        for text in self.texts():
            self.assertNotIn("<script>", text)


class Results(BotCase):
    def setUp(self) -> None:
        super().setUp()
        self.lives_in_toronto()

    def test_pages_of_five_with_previous_and_next(self) -> None:
        for number in range(1, 8):
            ca_listing(number, price_amount=1000 + number, area="North York")
        self.say("🔎 جستجوی خانه")
        self.say("نورث یورک")
        self.assertIn("۷ آگهی · ۱ تا ۵", self.texts()[-1])
        self.assertIn("بعدی ›", self.labels())
        self.assertNotIn("‹ قبلی", self.labels())
        self.press("v2:p:1")
        self.assertIn("۷ آگهی · ۶ تا ۷", self.texts()[-1])
        self.assertIn("‹ قبلی", self.labels())
        self.assertNotIn("بعدی ›", self.labels())

    def test_a_page_past_the_end_shows_the_last_one(self) -> None:
        ca_listing(1, area="North York")
        self.say("نورث یورک")
        self.press("v2:p:40")
        self.assertIn("۱ آگهی · ۱ تا ۱", self.texts()[-1])

    def test_zero_results_offer_what_would_match(self) -> None:
        ca_listing(price_amount=1800, area="North York", property_type="room")
        self.say("اتاق نورث یورک تا ۱۵۰۰")
        text = self.texts()[-1]
        self.assertIn("آگهی‌ای با این شرایط نیست. اگر کمی بازتر کنید:", text)
        self.assertIn("• بودجه تا ۲٬۰۰۰ CA$: ۱ آگهی", text)
        self.assertIn("بودجه تا ۲٬۰۰۰ CA$", self.labels())
        self.assertEqual(HomeSearchQuery.objects.get().status, "zero")

    def test_a_rescue_button_shows_the_results_it_promised(self) -> None:
        ca_listing(price_amount=1800, area="North York", property_type="room")
        self.say("اتاق نورث یورک تا ۱۵۰۰")
        rescue = next(button for button in self.fake.buttons() if plain(button["text"]) == "بودجه تا ۲٬۰۰۰ CA$")
        self.press(rescue["callback_data"])
        self.assertIn("۱ آگهی · ۱ تا ۱", self.texts()[-1])
        self.assertIn("تا ۲٬۰۰۰ CA$", self.texts()[-1])

    def test_zero_results_with_nothing_wider_says_so_and_offers_the_alert(self) -> None:
        self.say("بیسمنت نورث یورک تا ۸۰۰")
        self.assertIn("هنوز آگهی‌ای با این شرایط نیامده، حتی با شرط‌های بازتر.", self.texts()[-1])
        self.assertIn("🔔 گوش به زنگ همین", self.labels())

    def test_a_listing_with_no_price_says_ask(self) -> None:
        ca_listing(price_amount=None, area="North York")
        self.say("نورث یورک")
        self.assertIn("<b>قیمت: بپرسید</b>", self.texts()[-1])

    def test_the_fix_menu_changes_and_clears_the_search(self) -> None:
        ca_listing(price_amount=1150, area="North York", property_type="room")
        self.say("اتاق نورث یورک تا ۱۲۰۰")
        self.press("v2:fix")
        self.assertIn("✏️ <b>اصلاح جستجو</b>", self.texts()[-1])
        self.assertEqual(len(self.labels()), 10)
        self.press("v2:fx:max")
        self.press("v2:fs:max:2000")
        self.assertIn("تا ۲٬۰۰۰ CA$", self.texts()[-1])
        self.press("v2:clear")
        self.assertEqual(HomeChatState.objects.get(telegram_user_id=USER_ID).search, {"ch": self.toronto.pk})
        self.press("v2:fs:max:notanumber")
        self.assertEqual(self.toast(), botv2.OLD_BUTTON)

    def test_every_callback_fits_telegram(self) -> None:
        for number in range(1, 8):
            ca_listing(number, area="North York", price_amount=900)
        self.say("اتاق نورث یورک تا ۱۲۰۰ مبله از دسامبر فقط خانم")
        self.press("v2:fix")
        for name, payload in self.fake.calls:
            for row in (payload.get("reply_markup") or {}).get("inline_keyboard") or []:
                for button in row:
                    self.assertLessEqual(len(button.get("callback_data", "").encode()), 64)


class ListingCard(BotCase):
    def setUp(self) -> None:
        super().setUp()
        self.lives_in_toronto()
        self.listing = ca_listing(area="North York", contact_telegram="landlord_a")

    def test_the_card_has_no_contact_at_all(self) -> None:
        self.press(f"v2:d:{self.listing.pk}")
        text = self.texts()[-1]
        self.assertNotIn("landlord_a", text)
        self.assertNotIn("t.me", text)
        self.assertIn("· موجود", text)
        self.assertEqual(self.labels()[:2], ["📞 تماس", "🔖 نشان کردن"])
        self.assertIn("🚫 بسته شد؟ خبر دهید", self.labels())
        # Decision 5, 2026-11-02: reporting is out of v1, whatever the board shows.
        self.assertFalse([label for label in self.labels() if "🚩" in label])

    def test_a_photo_goes_as_a_photo_and_a_refused_photo_as_text(self) -> None:
        with patch("home.botv2.photo_urls", return_value=["https://x/1.jpg"]):
            self.press(f"v2:d:{self.listing.pk}")
        photo = self.fake.sent("sendPhoto")[-1]
        self.assertLessEqual(len(photo["caption"]), 1024)
        self.fake.fail["sendPhoto"] = BotError("wrong file", code=400)
        with patch("home.botv2.photo_urls", return_value=["https://x/1.jpg"]):
            self.press(f"v2:d:{self.listing.pk}")
        self.assertIn("· موجود", self.texts()[-1])

    def test_a_rate_limit_on_the_photo_is_not_swallowed(self) -> None:
        self.fake.fail["sendPhoto"] = BotError("Too Many Requests", code=429)
        with patch("home.botv2.photo_urls", return_value=["https://x/1.jpg"]), self.assertRaises(BotError):
            self.press(f"v2:d:{self.listing.pk}")

    def test_a_closed_listing_shows_the_closed_card_and_its_ways_on(self) -> None:
        self.listing.status = ListingStatus.RENTED
        self.listing.rented_at = timezone.now()
        self.listing.save()
        self.press(f"v2:d:{self.listing.pk}")
        self.assertIn("🚫 <b>این آگهی بسته شد.</b>", self.texts()[-1])
        self.assertIn("<s>", self.texts()[-1])
        self.assertIn("🔔 گوش به زنگ مشابه", self.labels())

    def test_a_listing_held_for_review_is_hidden(self) -> None:
        self.listing.needs_review = True
        self.listing.save()
        for action in ("d", "c", "s", "t"):
            self.press(f"v2:{action}:{self.listing.pk}")
            self.assertEqual(self.toast(), botv2.OLD_BUTTON)
        self.assertNotIn("landlord_a", " ".join(self.texts()))

    def test_a_reported_listing_warns(self) -> None:
        self.listing.status = ListingStatus.REPORTED
        self.listing.save()
        self.press(f"v2:d:{self.listing.pk}")
        self.assertIn("این آگهی گزارش شده و در حال بررسی است", self.texts()[-1])

    def test_the_bookmark_toggles_and_lists(self) -> None:
        self.press(f"v2:s:{self.listing.pk}")
        self.assertEqual(self.toast(), "🔖 نشان شد")
        self.assertEqual(HomeSaved.objects.count(), 1)
        self.say("🔖 نشان‌شده‌ها")
        self.assertIn("۱ آگهی · ۱ تا ۱", self.texts()[-1])
        self.assertIn("🗑 پاک کردن فهرست", self.labels())
        self.press(f"v2:s:{self.listing.pk}")
        self.assertEqual(HomeSaved.objects.count(), 0)
        self.say("🔖 نشان‌شده‌ها")
        self.assertIn("هنوز آگهی‌ای نشان نکرده‌اید", self.texts()[-1])

    def test_closing_a_listing_needs_two_people_unless_it_is_the_owner(self) -> None:
        self.press(f"v2:t:{self.listing.pk}")
        self.assertEqual(self.toast(), "ممنون! ثبت شد.")
        self.press("v2:t:999999")
        self.assertEqual(self.toast(), botv2.OLD_BUTTON)


class Contact(BotCase):
    def setUp(self) -> None:
        super().setUp()
        self.lives_in_toronto()
        self.listing = ca_listing(area="North York", contact_telegram="landlord_a")
        self.listing.contact_phone = "+14165550100"
        self.listing.save()

    def member(self, status: str = "member") -> None:
        original = self.fake

        def answer(method, payload=None, **kwargs):
            if method == "getChatMember":
                original.calls.append((method, payload or {}))
                return {"status": status}
            return original(method, payload, **kwargs)

        patcher = patch("home.bot.call", answer)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_not_a_member_is_asked_to_join_and_sees_no_contact(self) -> None:
        self.member("left")
        self.press(f"v2:c:{self.listing.pk}")
        text = self.texts()[-1]
        self.assertIn("📢 برای دیدن راه تماس، لطفاً اول عضو کانال", text)
        self.assertNotIn("landlord_a", text)
        # Since 2026-11-08 only the channel is asked for, even though Toronto has a public group.
        labels = self.labels()
        self.assertEqual(len(labels), 2, labels)
        self.assertTrue(labels[0].startswith("📢 "), labels)
        self.assertEqual(labels[1], "✅ عضو شدم")
        self.assertNotIn("گروه", text)
        self.assertFalse(ContactReveal.objects.exists())

    def test_a_member_sees_the_warning_and_every_way_to_reach_and_the_reveal_counts(self) -> None:
        self.member()
        self.press(f"v2:c:{self.listing.pk}")
        text = self.texts()[-1]
        self.assertTrue(text.startswith("⚠️ قبل از دیدن خانه پولی نفرستید."), text)
        self.assertIn("@landlord_a", text)
        self.assertIn("<code>+14165550100</code>", text)
        self.assertIn("💬 پیام در تلگرام", self.labels())
        self.assertIn("📋 کپی شماره", self.labels())
        self.assertEqual(self.toast(), "✅ عضویت تأیید شد")
        self.assertEqual(ContactReveal.objects.count(), 1)

    def test_the_contact_never_reaches_the_log(self) -> None:
        self.member()
        self.press(f"v2:c:{self.listing.pk}")
        logged = " ".join(HomeBotMessage.objects.values_list("text", flat=True))
        self.assertNotIn("landlord_a", logged)
        self.assertNotIn("+1416", logged)

    def test_telegram_down_fails_open_as_the_gate_says(self) -> None:
        self.fake.fail["getChatMember"] = BotError("Bad Gateway", code=502)
        self.press(f"v2:c:{self.listing.pk}")
        self.assertIn("@landlord_a", self.texts()[-1])
        self.assertTrue(ContactReveal.objects.get().failed_open)

    def test_a_banned_person_sees_no_contact(self) -> None:
        self.member("kicked")
        self.press(f"v2:c:{self.listing.pk}")
        self.assertIn("مسدود", self.texts()[-1])
        self.assertNotIn("landlord_a", self.texts()[-1])

    def test_the_daily_cap_stops_new_reveals(self) -> None:
        self.member()
        with patch("home.gate.REVEAL_CAP", 0):
            self.press(f"v2:c:{self.listing.pk}")
        self.assertIn("امروز به سقف", self.texts()[-1])

    def test_a_listing_that_closed_says_so(self) -> None:
        self.listing.status = ListingStatus.EXPIRED
        self.listing.save()
        self.press(f"v2:c:{self.listing.pk}")
        self.assertIn("این آگهی دیگر فعال نیست.", self.texts()[-1])

    def test_a_blocked_person_sees_no_contact(self) -> None:
        from users.models import User

        User.objects.create(telegram_user_id=USER_ID, first_name="Sara", is_active=False)
        self.press(f"v2:c:{self.listing.pk}")
        self.assertIn("حساب شما مسدود است", self.texts()[-1])

    def test_gate_off_shows_the_contact_without_asking_telegram(self) -> None:
        turn_on(contact_gate_on=False)
        self.press(f"v2:c:{self.listing.pk}")
        self.assertIn("@landlord_a", self.texts()[-1])
        self.assertEqual([call for call in self.fake.calls if call[0] == "getChatMember"], [])


class AlertSetup(BotCase):
    def setUp(self) -> None:
        super().setUp()
        self.lives_in_toronto()

    def alert(self) -> HomeAlert:
        return HomeAlert.objects.get(telegram_user_id=USER_ID)

    def test_three_questions_then_saved(self) -> None:
        self.press("v2:anew")
        self.assertIn("<b>۱ از ۳</b> · دنبال چه جایی هستید؟", self.texts()[-1])
        pk = self.alert().pk
        self.press(f"v2:ac:{pk}")
        self.assertEqual(self.toast(), "یکی را بزنید، یا «همه».")
        self.press(f"v2:at:{pk}:r")
        self.assertIn("✓ اتاق", self.labels())
        self.press(f"v2:ac:{pk}")
        self.assertIn("<b>۲ از ۳</b> · سقف اجاره‌ی ماهانه (دلار کانادا)؟", self.texts()[-1])
        self.assertIn("تا ۱٬۵۰۰", self.labels())
        self.say("خیلی ارزون")
        self.assertEqual(self.texts()[-1], "یک عدد بنویسید، مثلاً ۱۱۰۰. یا یکی از دکمه‌ها را بزنید («همه» هم هست).")
        self.say("۱۲۰۰")
        self.assertIn("<b>۳ از ۳</b> · کدام محله‌ها؟", self.texts()[-1])
        self.assertEqual(self.alert().max_price, Decimal("1200"))
        self.say("Yonge و Finch")
        self.assertIn("انتخاب شما:", self.texts()[-1])
        self.assertIn("ذخیره با ۱ محله", self.labels())
        self.press(f"v2:as:{pk}")
        text = self.texts()[-1]
        self.assertIn("🔔 <b>گوش به زنگ شما ثبت شد</b>", text)
        self.assertIn("💶 تا ۱٬۲۰۰ CA$", text)
        self.assertIn("🌙 از ۲۳ تا ۸ (وقت تورنتو)", text)
        self.assertIn("🎯 دقیق‌ترش کنید", self.labels())
        alert = self.alert()
        self.assertTrue(alert.complete)
        self.assertFalse(alert.awaiting_area)

    def test_a_saved_alert_says_what_it_still_holds_strict(self) -> None:
        self.press("v2:anew")
        pk = self.alert().pk
        self.press(f"v2:at:{pk}:x")
        self.press(f"v2:ab:{pk}:0")
        self.press(f"v2:aw2:{pk}")
        self.assertIn("🎯 تا «برای چه کسی» را جواب نداده‌اید", self.texts()[-1])
        self.press(f"v2:rf:{pk}")
        self.assertIn("سؤال اختیاری ۱ از ۲", self.texts()[-1])
        self.press(f"v2:ra:{pk}:g:f")
        self.assertIn("سؤال اختیاری ۲ از ۲", self.texts()[-1])
        self.press(f"v2:ra:{pk}:m:l")
        self.assertNotIn("جواب نداده‌اید", self.texts()[-1])
        alert = self.alert()
        self.assertEqual(alert.gender, "female")
        self.assertEqual(alert.term, "lease")

    def test_leaving_a_question_lets_the_next_text_be_a_search(self) -> None:
        self.press("v2:anew")
        pk = self.alert().pk
        self.press(f"v2:at:{pk}:x")
        self.press(f"v2:ab:{pk}:0")
        self.assertTrue(self.alert().awaiting_area)
        self.press("v2:saved")
        self.assertFalse(self.alert().awaiting_area)
        self.say("اتاق نورث یورک")
        self.assertEqual(self.alert().area, "")
        self.assertEqual(HomeSearchQuery.objects.count(), 1)

    def test_a_card_button_in_the_middle_of_a_question_does_not_end_it(self) -> None:
        listing = ca_listing(area="North York")
        self.press("v2:anew")
        pk = self.alert().pk
        self.press(f"v2:at:{pk}:x")
        self.press(f"v2:ab:{pk}:0")
        self.press(f"v2:d:{listing.pk}")
        self.assertTrue(self.alert().awaiting_area)
        self.say("Yonge و Finch")
        self.assertEqual(self.alert().area, "Yonge و Finch")

    def test_germany_asks_in_euro_and_about_anmeldung(self) -> None:
        berlin = city("Berlin", "DE", "Neukölln")
        HomeChatState.objects.filter(telegram_user_id=USER_ID).update(channel=berlin)
        self.press("v2:anew")
        self.assertEqual(self.labels()[:2], ["اتاق WG", "آپارتمان"])
        pk = self.alert().pk
        self.press(f"v2:at:{pk}:r")
        self.press(f"v2:ac:{pk}")
        self.assertIn("(یورو، اجاره‌ی گرم)", self.texts()[-1])
        self.assertIn("تا ۷۰۰ €", self.labels())
        self.press(f"v2:ab:{pk}:700")
        self.assertIn("کد پستی", self.texts()[-1])
        self.press(f"v2:aw2:{pk}")
        self.press(f"v2:rf:{pk}")
        self.assertIn("سؤال اختیاری ۱ از ۳", self.texts()[-1])
        self.press(f"v2:ra:{pk}:g:x")
        self.assertIn("Zwischenmiete", self.labels()[1])
        self.press(f"v2:ra:{pk}:m:s")
        self.assertIn("ملد (Anmeldung)", self.texts()[-1])
        self.press(f"v2:ra:{pk}:d:y")
        self.assertTrue(self.alert().needs_registration)

    def test_the_sixth_alert_is_refused_with_the_way_on(self) -> None:
        for _ in range(alerts.MAX_ALERTS_PER_USER):
            HomeAlert.objects.create(telegram_user_id=USER_ID, chat_id=USER_ID, country="DE", city="Berlin", complete=True, answered=["type", "budget", "area"])
        self.press("v2:anew")
        self.assertIn("سقف است", self.texts()[-1])
        self.assertEqual(self.labels(), ["📋 گوش به زنگ‌های من"])

    def test_a_search_becomes_an_alert_in_one_tap(self) -> None:
        ca_listing(price_amount=1000, area="North York", property_type="room")
        self.say("اتاق نورث یورک تا ۱۲۰۰")
        self.press("v2:asearch")
        alert = self.alert()
        self.assertEqual(alert.property_types, ["room"])
        self.assertEqual(alert.max_price, Decimal("1200"))
        self.assertEqual(alert.area, "north york")
        self.assertTrue(alert.complete)

    def test_more_areas_groups_and_picks(self) -> None:
        self.press("v2:anew")
        pk = self.alert().pk
        self.press(f"v2:at:{pk}:x")
        self.press(f"v2:ab:{pk}:0")
        self.press(f"v2:am:{pk}")
        self.assertIn("محله‌های بیشتر", self.texts()[-1])
        row = HomeArea.objects.get(canonical="scarborough")
        self.press(f"v2:ao:{pk}:{row.pk}")
        self.assertEqual(self.alert().area, "scarborough")

    def test_someone_elses_alert_is_an_old_button(self) -> None:
        other = HomeAlert.objects.create(telegram_user_id=OTHER_ID, chat_id=OTHER_ID, country="CA", city="Toronto")
        self.press(f"v2:ax2:{other.pk}")
        self.assertEqual(self.toast(), botv2.OLD_BUTTON)
        self.assertTrue(HomeAlert.objects.filter(pk=other.pk).exists())


class AlertScreens(BotCase):
    def setUp(self) -> None:
        super().setUp()
        self.lives_in_toronto()
        self.alert = HomeAlert.objects.create(
            telegram_user_id=USER_ID, chat_id=USER_ID, country="CA", city="Toronto", currency="CAD", max_price=Decimal("1200"), property_types=["room"],
            area="North York", complete=True, answered=["type", "budget", "area", "gender", "rooms", "term"],
        )

    def test_list_detail_pause_resume_delete(self) -> None:
        self.say("🔔 گوش به زنگ‌های من")
        self.assertIn("۱ مورد از حداکثر ۵", self.texts()[-1])
        self.assertIn("🏠 مدیریت در مینی‌اپ", self.labels())
        self.press(f"v2:ad:{self.alert.pk}")
        self.assertIn("🔔 <b>گوش به زنگ شما</b>", self.texts()[-1])
        self.assertIn("⏸ مکث", self.labels())
        self.press(f"v2:ap:{self.alert.pk}")
        self.assertIn("پیامی نمی‌فرستم", self.toast())
        self.press(f"v2:ad:{self.alert.pk}")
        self.assertIn("⏸ مکث تا", self.texts()[-1])
        self.assertIn("▶️ ادامه", self.labels())
        self.press(f"v2:ay:{self.alert.pk}")
        self.assertIn("⏸ مکث", self.labels())
        self.press(f"v2:ax:{self.alert.pk}")
        self.assertIn("حذف شود؟", self.texts()[-1])
        self.press(f"v2:ax2:{self.alert.pk}")
        self.assertFalse(HomeAlert.objects.filter(pk=self.alert.pk).exists())
        self.assertIn("هنوز گوش به زنگی ندارید", self.texts()[-1])

    def test_stop_and_back_on(self) -> None:
        self.say("/stop")
        self.assertEqual(self.texts()[-1], "🔕 همه‌ی گوش به زنگ‌ها خاموش شد. دیگر پیامی نمی‌فرستم.")
        self.alert.refresh_from_db()
        self.assertFalse(self.alert.active)
        self.press("v2:re:1")
        self.alert.refresh_from_db()
        self.assertTrue(self.alert.active)
        HomeAlert.objects.all().update(active=False)
        self.say("/stop")
        self.assertIn("گوش به زنگ روشنی نداشتید", self.texts()[-1])

    def test_the_match_message_for_a_person_on_v2(self) -> None:
        listing = ca_listing(area="North York", contact_telegram="landlord_a")
        alerts.send_alert(self.alert, listing, None, number=1)
        payload = self.fake.sent()[-1]
        self.assertIn("🔔 <b>آگهی تازه برای گوش به زنگ شما</b>", payload["text"])
        self.assertIn("می‌خورد: نوع، محله، بودجه", payload["text"])
        self.assertNotIn("landlord_a", payload["text"])
        labels = [button["text"] for row in payload["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual(labels, ["📞 جزئیات و تماس", "🔖 نشان کردن", "👍 به کارم آمد", "👎 به کارم نمی‌آید", "⏸ یک هفته مکث", "🔕 خاموش"])

    def test_the_summary_for_a_person_on_v2(self) -> None:
        items = [(self.alert, ca_listing(number, area="North York", price_amount=1000 + number)) for number in range(1, 6)]
        alerts.send_summary(items, {self.alert.pk: 1})
        text = plain(self.fake.sent()[-1]["text"])
        self.assertIn("🔔 <b>۵ آگهی تازه برای گوش به زنگ شما</b>", text)
        self.assertIn("… و ۲ آگهی دیگر در «🔎 آگهی‌های فعلی».", text)

    def test_feedback_up_and_down_with_a_reason(self) -> None:
        listing = ca_listing(area="North York")
        sent = HomeAlertSent.objects.create(telegram_user_id=USER_ID, listing=listing, alert=self.alert)
        self.press(f"v2:fu:{listing.pk}")
        sent.refresh_from_db()
        self.assertEqual(sent.feedback, "up")
        self.press(f"v2:fd:{listing.pk}")
        self.assertEqual(self.toast(), "چرا به کارتان نیامد؟ یکی را بزنید.")
        self.press(f"v2:fr:{listing.pk}:p")
        sent.refresh_from_db()
        self.assertEqual((sent.feedback, sent.feedback_reason), ("down", "price"))
        self.assertEqual(self.texts()[-1], "ممنون، ثبت شد. تطبیق‌ها دقیق‌تر می‌شود.")
        self.press("v2:fu:999999")
        self.assertEqual(self.toast(), botv2.OLD_BUTTON)


class HelpAndMisc(BotCase):
    def setUp(self) -> None:
        super().setUp()
        self.lives_in_toronto()

    def test_help_and_safety_per_city(self) -> None:
        self.say("❓ راهنما")
        self.assertIn("/forget آن‌ها را پاک می‌کند", self.texts()[-1])
        self.press("v2:safety:0")
        text = self.texts()[-1]
        self.assertIn("🛡 <b>ایمنی در تورنتو</b>", text)
        self.assertIn("هیچ پولی", text)
        self.assertNotIn("انتاریو", text)
        self.assertNotIn("🚩", text)
        berlin = HomeChannel.objects.get(city="Berlin")
        self.press(f"v2:safety:{berlin.pk}")
        self.assertIn("هیچ پولی", self.texts()[-1])
        self.assertNotIn("Kaution", self.texts()[-1])

    def test_english_changes_the_menu(self) -> None:
        self.say("/lang")
        self.assertEqual(self.labels(), ["✓ فارسی", "English"])
        self.press("v2:lang:en")
        self.assertIn("Language: <b>English</b>", self.texts()[-2])
        self.assertEqual(
            self.reply_keyboard(),
            ["🔎 Find a home", "🔔 My alerts", "➕ Post a listing", "🔖 Bookmarks", "📍 Toronto · Change city", "❓ Help"],
        )
        self.assertEqual(HomeChatState.objects.get(telegram_user_id=USER_ID).locale, "en")
        self.say("🔔 My alerts")
        self.assertIn("گوش به زنگ", self.texts()[-1])

    def test_forget_asks_then_deletes_the_log_and_keeps_alerts(self) -> None:
        alert = HomeAlert.objects.create(telegram_user_id=USER_ID, chat_id=USER_ID, country="CA", city="Toronto")
        self.say("اتاق نورث یورک")
        self.assertTrue(HomeSearchQuery.objects.exists())
        self.say("/forget")
        self.assertIn("گوش به زنگ‌ها و نشان‌شده‌ها می‌مانند", self.texts()[-1])
        self.assertTrue(HomeSearchQuery.objects.exists())
        self.press("v2:fy")
        self.assertEqual(self.toast(), "پاک شد")
        self.assertFalse(HomeSearchQuery.objects.exists())
        self.assertFalse(HomeBotMessage.objects.filter(kind="text").exists())
        self.assertTrue(HomeAlert.objects.filter(pk=alert.pk).exists())

    def test_someone_elses_log_stays_after_forget(self) -> None:
        HomeBotMessage.objects.create(telegram_user_id=OTHER_ID, direction="in", kind="text", text="x")
        botlog.forget(USER_ID)
        self.assertEqual(HomeBotMessage.objects.count(), 1)

    def test_posting_opens_the_mini_app(self) -> None:
        self.say("➕ ثبت آگهی")
        self.assertIn("بعد از یک بررسی کوتاه در کانال تورنتو", self.texts()[-1])
        self.assertIn("🏠 خانه دارم", self.labels())

    def test_my_listings_empty(self) -> None:
        self.press("v2:mine")
        self.assertIn("هنوز آگهی‌ای ثبت نکرده‌اید", self.texts()[-1])


class Log(TestCase):
    def test_a_long_message_is_cut_and_tags_go(self) -> None:
        botlog.record(1, "out", "results", "<b>" + "x" * 600 + "</b>")
        row = HomeBotMessage.objects.get()
        self.assertEqual(len(row.text), botlog.TEXT_LIMIT)
        self.assertNotIn("<b>", row.text)

    def test_a_failed_write_never_reaches_the_person(self) -> None:
        from django.db import DatabaseError

        with patch("home.botlog.HomeBotMessage.objects.create", side_effect=DatabaseError("down")):
            botlog.record(1, "in", "text", "x")

    def test_a_log_write_does_not_purge(self) -> None:
        with patch("home.botlog.purge") as purge:
            botlog.record(1, "in", "text", "x")
        purge.assert_not_called()

    def test_purge_keeps_what_is_inside_the_retention(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"search_log_retention_days": 30})
        old, new = [HomeBotMessage.objects.create(telegram_user_id=1, direction="in", kind="text") for _ in range(2)]
        HomeBotMessage.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=31))
        query = HomeSearchQuery.objects.create(telegram_user_id=1, raw_text="x")
        HomeSearchQuery.objects.filter(pk=query.pk).update(created_at=timezone.now() - timedelta(days=31))
        botlog.purge()
        self.assertEqual(list(HomeBotMessage.objects.values_list("pk", flat=True)), [new.pk])
        self.assertFalse(HomeSearchQuery.objects.exists())


@override_settings(CRON_SECRET="cron-test-secret")
class Retention(TestCase):
    """Personal text leaves on its retention days, with no bot traffic and whatever the switches say."""

    def setUp(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"search_log_retention_days": 30, "bot_token": TOKEN})
        self.aged()

    def aged(self) -> None:
        past = timezone.now() - timedelta(days=31)
        message = HomeBotMessage.objects.create(telegram_user_id=1, direction="in", kind="text", text="room near Pearson")
        query = HomeSearchQuery.objects.create(telegram_user_id=1, raw_text="room near Pearson")
        HomeBotMessage.objects.filter(pk=message.pk).update(created_at=past)
        HomeSearchQuery.objects.filter(pk=query.pk).update(created_at=past)

    def assert_gone(self) -> None:
        self.assertFalse(HomeBotMessage.objects.exists())
        self.assertFalse(HomeSearchQuery.objects.exists())

    def test_the_freshness_step_purges(self) -> None:
        kept = HomeBotMessage.objects.create(telegram_user_id=1, direction="in", kind="text")
        HomeAiUsage.objects.create(telegram_user_id=1, day=timezone.localdate() - timedelta(days=3), attempts=1)
        today = HomeAiUsage.objects.create(telegram_user_id=1, day=timezone.localdate(), attempts=1)
        out = freshness.run_freshness()
        self.assertEqual(out["bot_log"]["messages"], 1)
        self.assertEqual(list(HomeBotMessage.objects.values_list("pk", flat=True)), [kept.pk])
        self.assertFalse(HomeSearchQuery.objects.exists())
        self.assertEqual(list(HomeAiUsage.objects.values_list("pk", flat=True)), [today.pk])

    def test_freshness_off_still_purges_and_answers_as_before(self) -> None:
        HomeSettings.objects.filter(pk=1).update(freshness_enabled=False)
        self.assertEqual(freshness.run_freshness(), {"ok": True, "skipped": freshness.FRESHNESS_OFF})
        self.assert_gone()

    def test_the_cron_purges_when_the_job_skips_the_freshness_step(self) -> None:
        HomeSettings.objects.filter(pk=1).update(freshness_enabled=False)
        with patch("api.cron_views.run_home_job", return_value={"ok": True, "step": "all"}):
            self.client.get("/api/cron/home/", HTTP_AUTHORIZATION="Bearer cron-test-secret")
        self.assert_gone()

    def test_the_cron_purges_with_home_switched_off(self) -> None:
        HomeSettings.objects.filter(pk=1).update(enabled=False)
        response = self.client.get("/api/cron/home/", HTTP_AUTHORIZATION="Bearer cron-test-secret")
        self.assertEqual(response.status_code, 503)
        self.assert_gone()

    def test_the_command_purges_with_home_switched_off(self) -> None:
        HomeSettings.objects.filter(pk=1).update(enabled=False)
        call_command("home_job", stdout=io.StringIO())
        self.assert_gone()

    def test_a_cron_without_the_secret_purges_nothing(self) -> None:
        self.client.get("/api/cron/home/")
        self.assertTrue(HomeBotMessage.objects.exists())

    def test_it_runs_once_a_day(self) -> None:
        self.assertEqual(botlog.purge_daily()["messages"], 1)
        self.assertEqual(HomeSettings.load().bot_log_purged_on, timezone.localdate())
        self.aged()
        with patch("home.botlog.purge") as purge:
            self.assertEqual(botlog.purge_daily(), {"skipped": "already ran today"})
            freshness.run_freshness()
        purge.assert_not_called()
        self.assertTrue(HomeBotMessage.objects.exists())

    def test_the_next_day_it_runs_again(self) -> None:
        HomeSettings.objects.filter(pk=1).update(bot_log_purged_on=timezone.localdate() - timedelta(days=1))
        botlog.purge_daily()
        self.assert_gone()

    def test_a_failed_purge_fails_nothing_and_tries_again(self) -> None:
        with patch("home.botlog.purge", side_effect=DatabaseError("down")):
            self.assertEqual(freshness.run_freshness()["bot_log"], {"error": "purge failed"})
        self.assertIsNone(HomeSettings.load().bot_log_purged_on)
        botlog.purge_daily()
        self.assert_gone()
