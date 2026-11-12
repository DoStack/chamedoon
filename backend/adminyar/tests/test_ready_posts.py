from unittest import mock

from django.test import TestCase
from django.utils import timezone

from adminyar import bot as ybot
from adminyar import posts
from adminyar.models import AuditLog, ReadyPost
from home.bot import BotError
from home.models import ContentKind, HomeChannel, HomeContentPost

SARA = 8854699370
ENV = {"ADMINYAR_ALLOWED_IDS": f"{SARA}", "ADMINYAR_OWNER_IDS": "1", "ADMINYAR_BOT_TOKEN": "x"}


def cb(data):
    return {"update_id": 2, "callback_query": {"id": "q", "data": data, "from": {"id": SARA}, "message": {"chat": {"id": SARA}}}}


@mock.patch.dict("os.environ", ENV)
class ReadyPostTests(TestCase):
    def setUp(self):
        self.sent = []
        self.fail_forward = False
        self.bot = ybot.Bot("x", api=self.api)
        HomeContentPost.objects.all().delete()
        HomeChannel.objects.update(enabled=False)
        self.toronto, _ = HomeChannel.objects.update_or_create(country="CA", city="Toronto", defaults={"username": "Chamedoon_Toronto", "chat_id": -1001, "enabled": True})
        self.berlin, _ = HomeChannel.objects.update_or_create(country="DE", city="Berlin", defaults={"username": "Chamedoon_Berlin", "chat_id": -1002, "enabled": True})
        now = timezone.now()
        HomeContentPost.objects.create(kind=ContentKind.WEEKLY, chat_id=-1001, channel=self.toronto, period_key="2026-W45", message_id=300, sent_at=now - timezone.timedelta(days=7))
        self.weekly = HomeContentPost.objects.create(
            kind=ContentKind.WEEKLY, chat_id=-1001, channel=self.toronto, period_key="2026-W46", message_id=412, message_ids=[412, 413], sent_at=now, views=1240, views_at=now
        )
        HomeContentPost.objects.create(kind=ContentKind.WEEKLY, chat_id=-1002, channel=self.berlin, period_key="2026-W46")  # claimed, never sent

    def api(self, method, payload):
        if method == "forwardMessages" and self.fail_forward:
            raise BotError("Bad Request: chat not found")
        self.sent.append((method, payload))
        return {}

    def texts(self):
        return [p["text"] for m, p in self.sent if m == "sendMessage"]

    def last_markup(self):
        return [p for m, p in self.sent if m == "sendMessage"][-1]["reply_markup"]

    def test_menu_button_shows_categories(self):
        self.bot.handle({"update_id": 1, "message": {"chat": {"id": SARA, "type": "private"}, "from": {"id": SARA}, "text": ybot.READY}})
        self.assertEqual(self.texts()[-1], ybot.T_READY_CATEGORY)
        codes = [b["callback_data"] for row in self.last_markup()["inline_keyboard"] for b in row]
        self.assertEqual(codes, ["pc:intro", "pc:guide", "pc:weekly", "pc:monthly", "pc:country"])

    def test_weekly_lists_only_channels_with_a_sent_post_and_newest_card(self):
        self.bot.handle(cb("pc:weekly"))
        places = [b["callback_data"] for row in self.last_markup()["inline_keyboard"] for b in row]
        self.assertEqual(places, [f"pp:weekly:{self.toronto.pk}"])
        self.bot.handle(cb(f"pp:weekly:{self.toronto.pk}"))
        text = self.texts()[-1]
        self.assertIn("t.me/Chamedoon_Toronto/412", text)
        self.assertIn("۱٬۲۴۰ بازدید", text)
        self.assertIn("۹ تا ۱۵ نوامبر ۲۰۲۶", text)
        self.assertIn("آبان", text)
        buttons = self.last_markup()["inline_keyboard"][0]
        self.assertEqual(buttons[0]["callback_data"], f"pf:c{self.weekly.pk}")
        self.assertEqual(buttons[1]["url"], "https://t.me/Chamedoon_Toronto/412")

    def test_send_me_forwards_album_to_admin_only(self):
        self.bot.handle(cb(f"pf:c{self.weekly.pk}"))
        forwards = [p for m, p in self.sent if m == "forwardMessages"]
        self.assertEqual(forwards, [{"chat_id": SARA, "from_chat_id": -1001, "message_ids": [412, 413]}])
        self.assertFalse([m for m, p in self.sent if m in ("sendMessage", "copyMessage") and p.get("chat_id") in (-1001, -1002)])
        self.assertTrue(AuditLog.objects.filter(action="ready_forward", result="forwarded").exists())

    def test_old_weekly_ref_is_gone(self):
        old = HomeContentPost.objects.get(period_key="2026-W45")
        self.bot.handle(cb(f"pf:c{old.pk}"))
        self.assertEqual(self.texts()[-1], ybot.T_READY_GONE)

    def test_forward_failure_falls_back_to_link(self):
        self.fail_forward = True
        self.bot.handle(cb(f"pf:c{self.weekly.pk}"))
        self.assertEqual(self.texts()[-1], ybot.T_READY_FWD_FAIL)

    def test_intro_from_admin_list_and_scam_tips_never_offered(self):
        ReadyPost.objects.create(category="intro", channel=self.berlin, message_id=5, title="معرفی چمدون")
        ReadyPost.objects.create(category="intro", channel=self.berlin, message_id=6, title="نکات کلاهبرداری")
        self.bot.handle(cb(f"pp:intro:{self.berlin.pk}"))
        self.assertEqual(len(self.texts()), 1)
        self.assertIn("t.me/Chamedoon_Berlin/5", self.texts()[0])
        self.assertIn("بازدید هنوز خوانده نشده", self.texts()[0])
        bad = ReadyPost.objects.get(message_id=6)
        self.assertIsNone(posts.by_ref(f"r{bad.pk}"))

    def test_empty_category(self):
        self.bot.handle(cb("pc:monthly"))
        self.assertEqual(self.texts()[-1], ybot.T_READY_NONE)

    def test_monthly_period_label(self):
        self.assertEqual(posts.period_label("202611"), "نوامبر ۲۰۲۶ (آبان ۱۴۰۵)")
