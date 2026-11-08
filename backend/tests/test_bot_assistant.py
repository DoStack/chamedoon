"""The housing bot's conversation (home/assistant.py): the model only reads, the code asks, searches, saves and posts."""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from ai.openrouter import ChatResult
from home import assistant
from home.botchat import handle_update
from home.models import HomeAlert, HomeChannel, HomeChatState, HomeListing, HomeSearchQuery, HomeSettings
from home_api.views import resolve_start
from tests.test_home_alerts import USER_ID, FakeBot, message, tap, turn_on
from tests.test_home_publish import ca_listing


def said(topic: str = "search", *, city: str = "", search: dict | None = None, post: dict | None = None, yes=None, done: bool = False, ack: str = "") -> ChatResult:
    """What the model answers: a JSON object, as review_completion returns it."""
    body = {"topic": topic, "yes": yes, "done": done, "city": city, "search": search or {}, "post": post or {}, "ack": ack}
    return ChatResult(ok=True, text=json.dumps(body))


def press(data: str, username: str = "sara_t") -> dict:
    update = tap(data)
    update["callback_query"]["from"]["username"] = username
    return update


def from_user(text: str, username: str = "sara_t") -> dict:
    update = message(text)
    update["message"]["from"]["username"] = username
    return update


class AssistantCase(TestCase):
    def setUp(self) -> None:
        turn_on()
        self.fake = FakeBot()
        for target in ("home.bot.call",):
            patcher = patch(target, self.fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.model = patch("home.assistant.review_completion").start()
        self.addCleanup(patch.stopall)

    def say(self, text: str, answer: ChatResult, **kwargs) -> str:
        self.model.return_value = answer
        handle_update(from_user(text, **kwargs))
        return self.fake.last_text()

    def urls(self) -> list[str]:
        return [button.get("url", "") for button in self.fake.buttons()]


class Welcome(AssistantCase):
    def test_start_asks_what_they_want_and_takes_the_old_keyboard_away(self) -> None:
        handle_update(message("/start"))
        (welcome,) = self.fake.sent()
        self.assertEqual(welcome["text"], assistant.WELCOME)
        self.assertEqual(welcome["reply_markup"], {"remove_keyboard": True})
        self.model.assert_not_called()

    def test_the_commands_open_their_mini_app_screens(self) -> None:
        for command, start in (("/alerts", "home_alerts"), ("/saved", "home_saved"), ("/mine", "home_mine"), ("/help", "home")):
            with self.subTest(command=command):
                handle_update(message(command))
                self.assertIn(f"startapp={start}", " ".join(self.urls()))
        self.assertEqual([command["command"] for command in assistant.COMMANDS], ["start", "search", "post", "alerts", "saved", "mine", "help", "stop"])

    def test_off_topic_gets_one_fixed_answer(self) -> None:
        text = self.say("یه شعر برام بگو", said("other", ack="حتما!"))
        self.assertEqual(text, assistant.OFF_TOPIC)
        self.assertFalse(HomeSearchQuery.objects.exists())

    def test_model_off_or_broken_sends_people_to_the_mini_app(self) -> None:
        HomeSettings.objects.update(assistant_on=False)
        handle_update(message("اتاق تورنتو"))
        self.assertEqual(self.fake.last_text(), assistant.FALLBACK)
        self.assertIn("startapp=home", " ".join(self.urls()))
        self.model.assert_not_called()
        HomeSettings.objects.update(assistant_on=True)
        self.assertEqual(self.say("اتاق تورنتو", ChatResult(ok=True, text="not json")), assistant.FALLBACK)
        self.assertEqual(self.say("اتاق تورنتو", ChatResult(ok=False, error="down")), assistant.FALLBACK)

    def test_unknown_values_from_the_model_are_dropped(self) -> None:
        cleaned = assistant.clean(
            {"topic": "hack", "city": "Paris", "search": {"types": ["castle", "room"], "max": "-5", "gender": "x", "skip": ["max", "evil"]}, "post": {"phone": "+1 (647) 555-0142"}},
            list(HomeChannel.objects.filter(enabled=True)),
        )
        self.assertEqual(cleaned["topic"], "other")
        self.assertIsNone(cleaned["city"])
        self.assertEqual(cleaned["search"]["types"], ["room"])
        self.assertIsNone(cleaned["search"]["max"])
        self.assertEqual(cleaned["search"]["gender"], "")
        self.assertEqual(cleaned["search"]["skip"], ["max"])
        self.assertEqual(cleaned["post"]["phone"], "+16475550142")


class Search(AssistantCase):
    def test_all_questions_in_one_message_then_results_then_an_alert(self) -> None:
        cheap = ca_listing(1, price_amount=950)
        ca_listing(2, price_amount=2500)
        text = self.say("سلام، یه خونه اجاره‌ای می‌خوام", said("search", ack="حتماً"))
        for question in ("کدوم شهر", "ماهی تا چقدر", "چه مدلی", "محله", "از کِی"):
            self.assertIn(question, text)
        self.assertEqual(len(self.fake.sent()), 1)
        # The answer fills what it can; what is still open is not asked again, the search runs.
        text = self.say("تورنتو، تا ۱۰۰۰، فرقی نداره", said("search", city="Toronto", search={"max": 1000, "skip": ["types", "areas", "move"]}))
        self.assertIn("۱ آگهی", text)
        self.assertIn("خبرتون کنم", text)
        query = HomeSearchQuery.objects.get()
        self.assertEqual((query.city, query.parser, query.results_count), ("Toronto", "assistant", 1))
        self.assertEqual(self.urls(), [f"https://t.me/chamedoon_home_bot?startapp=home_q{query.pk}"])
        opened = resolve_start(f"home_q{query.pk}")
        self.assertEqual((opened["kind"], opened["city_slug"]), ("city", "toronto"))
        self.assertEqual(opened["path"], "/?city=toronto&max=1000")
        self.assertEqual(HomeChatState.objects.get(telegram_user_id=USER_ID).channel.city, "Toronto")
        # «آره» makes the alert, the Mini App's own way.
        text = self.say("آره حتماً", said("search", yes=True))
        alert = HomeAlert.objects.get()
        self.assertEqual((alert.city, alert.max_price, alert.delivery), ("Toronto", Decimal("1000"), "now"))
        self.assertIn("خبرتون می‌کنم", text)
        self.assertTrue(cheap.pk)

    def test_only_the_city_is_asked_again(self) -> None:
        self.say("اتاق می‌خوام", said("search", search={"types": ["room"]}))
        text = self.say("فرقی نداره", said("search", search={"skip": ["max", "areas", "move"]}))
        self.assertIn("فقط بگید کدوم شهر", text)
        self.assertIn("تورنتو", text)

    def test_areas_and_dates_reach_the_mini_app(self) -> None:
        self.say("خونه", said("search"))
        self.say("اتاق تورنتو نورث یورک تا ۱۲۰۰ از اول دسامبر", said("search", city="Toronto", search={"types": ["room"], "max": 1200, "areas": ["North York"], "move": "2026-12-01"}))
        path = resolve_start(f"home_q{HomeSearchQuery.objects.get().pk}")["path"]
        self.assertEqual(path, "/?city=toronto&types=room&max=1200&areas=north+york&move=2026-12-01")
        self.assertIn("الان آگهی‌ای با این شرایط نیست", self.fake.last_text())

    def test_no_thanks_keeps_quiet(self) -> None:
        self.say("خونه", said("search"))
        self.say("تورنتو، بقیه فرقی نداره", said("search", city="Toronto", search={"skip": ["max", "types", "areas", "move"]}))
        self.assertIn("باشه", self.say("نه", said("search", yes=False)))
        self.assertFalse(HomeAlert.objects.exists())

    def test_a_channel_alert_link_knows_the_city_and_saves_after_one_answer(self) -> None:
        handle_update(message("/start alert_home_ca_toronto"))
        first = self.fake.sent()[0]
        self.assertEqual(first["reply_markup"], {"remove_keyboard": True})
        self.assertIn("گوش به زنگ تورنتو", first["text"])
        self.assertNotIn("کدوم شهر", first["text"])
        self.say("اتاق تا ۹۰۰", said("search", search={"types": ["room"], "max": 900, "skip": ["areas", "move"]}))
        alert = HomeAlert.objects.get()
        self.assertEqual((alert.city, alert.max_price, alert.property_types), ("Toronto", Decimal("900"), ["room"]))
        self.assertIn("خبرتون می‌کنم", self.fake.last_text())

    def test_a_bad_query_link_is_a_normal_open(self) -> None:
        self.assertTrue(resolve_start("home_q999999")["malformed"])


class Posting(AssistantCase):
    def test_a_post_from_words_to_the_mini_app_and_the_channel(self) -> None:
        text = self.say("می‌خوام اتاقمو اجاره بدم", said("post", post={"kind": "offer"}))
        for question in ("شهر و محله", "نوع", "اجاره‌ی ماهانه"):
            self.assertIn(question, text)
        details = {"kind": "offer", "property_type": "room", "price": 850, "area": "Côte-des-Neiges", "available_from": "2027-01-01", "roommate_gender": "female", "description": "یک اتاق روشن نزدیک مترو، قبوض با صاحبخانه، فقط خانم."}
        text = self.say("مونترال کوت‌دنژ ۸۵۰ از ژانویه فقط خانم", said("post", city="Montreal", post=details))
        self.assertIn("عکس", text)
        text = self.say("عکس ندارم", said("post", done=True))
        self.assertIn("پیش‌نمایش", text)
        self.assertIn("۸۵۰", text)
        self.assertEqual([button["callback_data"] for button in self.fake.buttons()], ["as:send"])
        handle_update(press("as:send"))
        listing = HomeListing.objects.get()
        self.assertEqual((listing.city, listing.property_type, listing.price_amount, listing.contact_telegram), ("Montreal", "room", Decimal("850"), "sara_t"))
        self.assertIn("ثبت شد", self.fake.last_text())
        self.assertEqual(len(self.fake.buttons()), 1)
        # The same button twice posts nothing new.
        handle_update(press("as:send"))
        self.assertEqual(HomeListing.objects.count(), 1)
        # Published: the poster hears it once, with the post's link.
        montreal = HomeChannel.objects.get(city="Montreal")
        assistant.tell_published(listing, montreal, [55])
        assistant.tell_published(listing, montreal, [56])
        told = [payload for payload in self.fake.sent() if "پست شد" in payload["text"]]
        self.assertEqual(len(told), 1)
        self.assertEqual(self.fake.buttons(told[0])[0]["url"], f"https://t.me/{montreal.username}/55")

    def test_typed_yes_sends_and_a_correction_shows_the_preview_again(self) -> None:
        details = {"kind": "offer", "property_type": "studio", "price": 1500, "description": "استودیوی مبله در مرکز شهر، نزدیک مترو و دانشگاه."}
        self.say("آگهی", said("post"))
        self.say("استودیو تورنتو ۱۵۰۰", said("post", city="Toronto", post=details))
        self.say("عکس ندارم", said("post", done=True))
        text = self.say("قیمت ۱۴۰۰ باشه", said("post", post={"price": 1400}))
        self.assertIn("۱٬۴۰۰", text)
        self.say("بفرستید", said("post", yes=True))
        self.assertEqual(HomeListing.objects.get().price_amount, Decimal("1400"))

    def test_without_a_username_a_phone_is_asked(self) -> None:
        details = {"kind": "offer", "property_type": "room", "price": 700, "description": "اتاق مبله در خانه‌ی دانشجویی، قبوض با صاحبخانه."}
        self.say("آگهی", said("post"), username="")
        text = self.say("اتاق تورنتو ۷۰۰", said("post", city="Toronto", post=details), username="")
        self.assertIn("شماره تماس", text)

    def test_the_join_gate_comes_first(self) -> None:
        HomeSettings.objects.update(join_gate_poster=True)
        details = {"kind": "offer", "property_type": "room", "price": 700, "description": "اتاق مبله در خانه‌ی دانشجویی، قبوض با صاحبخانه."}
        self.say("آگهی", said("post"))
        with patch("home.gate.join_check", return_value={"chats": [{"title": "چمدون - خانه | تورنتو", "join_url": "https://t.me/chamedoon_toronto_khane"}], "gate": "not_member"}):
            text = self.say("اتاق تورنتو ۷۰۰", said("post", city="Toronto", post=details))
        self.assertIn("عضو کانال چمدون", text)
        self.assertNotIn("گروه", text)
        self.assertEqual(self.urls(), ["https://t.me/chamedoon_toronto_khane"])
        self.assertFalse(HomeListing.objects.exists())

    def test_photos_are_kept_while_posting_and_a_stray_photo_is_nudged(self) -> None:
        photo = message("")
        photo["message"]["photo"] = [{"file_id": "small"}, {"file_id": "big"}]
        handle_update(photo)
        self.assertIn("آگهی می‌خوام بذارم", self.fake.last_text())
        self.say("آگهی", said("post"))
        album = [dict(photo["message"], media_group_id="g1", photo=[{"file_id": f"p{n}"}]) for n in range(3)]
        for item in album:
            handle_update({"update_id": 3, "message": item})
        self.assertEqual(HomeChatState.objects.get(telegram_user_id=USER_ID).data["photos"], ["p0", "p1", "p2"])
        self.assertEqual(sum("📸 گرفتم" in payload["text"] for payload in self.fake.sent()), 1)


class DoorCommand(AssistantCase):
    def test_dry_run_calls_nothing_and_apply_sets_the_menu_and_removes_keyboards(self) -> None:
        from io import StringIO

        from django.core.management import call_command

        HomeChatState.objects.create(telegram_user_id=111)
        HomeChatState.objects.create(telegram_user_id=222)
        out = StringIO()
        call_command("home_bot_door", "--url", "https://chamedoon.example/app/home/", "--keyboards", stdout=out)
        self.assertEqual(self.fake.calls, [])
        self.assertIn("for 2 people", out.getvalue())
        call_command("home_bot_door", "--url", "https://chamedoon.example/app/home/", "--keyboards", "--only", "222", "--apply", stdout=out)
        menu = self.fake.sent("setChatMenuButton")[0]["menu_button"]
        self.assertEqual((menu["type"], menu["text"], menu["web_app"]["url"]), ("web_app", "باز کردن چمدون", "https://chamedoon.example/app/home/"))
        self.assertEqual(self.fake.sent("setMyCommands")[0]["commands"], assistant.COMMANDS)
        self.assertEqual([payload["chat_id"] for payload in self.fake.sent()], [222])
        self.assertEqual(len(self.fake.sent("deleteMessage")), 1)
        self.assertIn("removed for 1 of 1", out.getvalue())
