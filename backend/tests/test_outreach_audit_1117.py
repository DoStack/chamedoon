"""Audit 2026-11-17 10:15 UTC: conv 45 (media declined, «همه رو ثبت کردم», a request read as thanks), the video after
the media thanks in conv 45 and conv 46, #474's last_sent_at, and #490's P5 one day after his repost."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from home.models import HomeListing
from outreach import agent, completion, followups, videos
from outreach.models import (
    OutreachAccount,
    OutreachConversation,
    OutreachKind,
    OutreachMessage,
    OutreachSettings,
    OutreachStatus,
    OutreachTurn,
    TurnDirection,
)
from outreach.services import PEER_FLOOD, build_outreach_queue, record_result
from tests.test_outreach_agent import AgentTestCase, model_says
from tests.test_outreach_housing import POSTER_ID, HousingOutreachTestCase

T278 = (
    "سلام وقت بخیر ممنون بابت پیام ولی عکس نمیتونم بفرست چون بدون ملد است کسی خواست شخصی میفرستم عکس \n"
    "بازم یک جهان ممنون کرایه اش ۳۰۰ است چون خودم هم در اطاق زندگی می‌کنم یعنی دنبال هم خونه استم"
)


class Audit20261117MediaDeclinedTests(AgentTestCase):
    def out_texts(self) -> list[str]:
        return list(OutreachTurn.objects.filter(direction=TurnDirection.OUT).order_by("pk").values_list("text", flat=True))

    def test_conv_45_no_photos_is_accepted_and_never_asked_again(self) -> None:
        HomeListing.objects.filter(pk=self.ad.pk).update(price_amount=None, price_currency="EUR")
        self.say(T278)
        with model_says(intent="field", field="price", value="300", values={"price": "300"}):
            reply = self.answer(with_intro=True)
        agent.reply_sent(reply["turn_id"], ok=True, telegram_message_id=900)
        with model_says(intent="field", field="price", value="300", values={"price": "300"}):
            more = agent.next_reply("niloofar")
        if more:
            agent.reply_sent(more["turn_id"], ok=True, telegram_message_id=901)
        sent = self.out_texts()
        first_line = sent[0].split("\n")[0]
        self.assertIn("۳۰۰", first_line)
        self.assertNotIn("همه رو", first_line)
        self.assertIn(completion.MEDIA_DECLINED_OK, first_line)
        self.assertFalse(any("عکس نیومده" in text for text in sent), sent)
        conversation = OutreachConversation.objects.get()
        self.assertNotIn("media", conversation.awaiting or {})
        self.assertTrue(completion.media_declined(conversation))
        self.ad.refresh_from_db()
        self.assertNotIn("عکس", completion.next_line(conversation, self.ad, mark=False))

    def test_a_bare_no_photos_gets_the_line_alone(self) -> None:
        self.say("عکس ندارم")
        with model_says(intent="thanks"):
            reply = self.answer()
        self.assertIsNotNone(reply)
        self.assertIn(completion.MEDIA_DECLINED_OK, reply["text"])
        self.assertNotIn("عکس نیومده", reply["text"])

    def test_the_words_that_decline_and_the_ones_that_do_not(self) -> None:
        for text in (T278, "عکس ندارم", "عکس نمی‌فرستم", "عکس نمی‌تونم بفرستم", "کسی خواست شخصی میفرستم"):
            self.assertTrue(completion.declines_media(text), text)
        for text in ("عکس میفرستم", "اینم عکس", "اگر میشه عکس را با آگهی بگذارید ممنون", "عکس الان ندارم فردا میفرستم", "عکسش خیلی خوب نیست"):
            self.assertFalse(completion.declines_media(text), text)

    def test_t286_a_request_is_never_a_thanks(self) -> None:
        self.assertEqual(agent.settle_intent("thanks", "اگر میشه عکس را با آگهی بگذارید ممنون"), "other")
        self.assertEqual(agent.settle_intent("thanks", "ممنون 🙏🏻"), "thanks")


class Audit20261117SavedValuesTests(AgentTestCase):
    def test_every_saved_value_is_named_in_persian_digits(self) -> None:
        HomeListing.objects.filter(pk=self.ad.pk).update(price_amount=300, price_currency="EUR", rooms=2, property_type="apartment")
        self.ad.refresh_from_db()
        text = completion.saved_text(self.ad, ["price", "rooms"])
        self.assertNotIn("همه رو", text)
        self.assertIn("۳۰۰", text)
        self.assertIn("۲ خوابه بودنش", text)
        self.assertTrue(text.startswith("ممنون، قیمت"), text)

    def test_a_field_with_no_stored_value_is_not_claimed(self) -> None:
        HomeListing.objects.filter(pk=self.ad.pk).update(price_amount=300, price_currency="EUR", rooms=None, property_type="room")
        self.ad.refresh_from_db()
        text = completion.saved_text(self.ad, ["price", "rooms"])
        self.assertNotIn("خوابه", text)
        self.assertIn("۳۰۰", text)


class Audit20261117VideoWaitsTests(AgentTestCase):
    def chat(self) -> OutreachConversation:
        self.say("سلام", 1)
        conversation = OutreachConversation.objects.get()
        OutreachTurn.objects.filter(conversation=conversation).update(status="done")
        return conversation

    def test_conv_45_the_video_waits_after_the_media_thanks(self) -> None:
        conversation = self.chat()
        OutreachTurn.objects.create(
            conversation=conversation, direction=TurnDirection.OUT, status="done", action="media_ack", telegram_message_id=287,
            text="عکس‌ها رسید، ممنون. به آگهی‌تون اضافه‌شون می‌کنم.",
        )
        self.assertTrue(videos._waits_after_intro(conversation))
        self.say("ممنون", 2)
        self.assertFalse(videos._waits_after_intro(conversation))

    def test_conv_46_the_video_waits_after_the_intro_with_the_media_thanks(self) -> None:
        conversation = self.chat()
        # The stored text no longer holds the intro word for word: the action says what it was.
        OutreachTurn.objects.create(
            conversation=conversation, direction=TurnDirection.OUT, status="done", action="about", action_detail={"with": "media_ack", "intro": "HO3"},
            telegram_message_id=295, text="عکس‌ها رسید، ممنون.\nشاید چمدون رو نشناسید.",
        )
        self.assertTrue(videos._waits_after_intro(conversation))

    def test_the_video_is_not_claimed_after_the_media_thanks(self) -> None:
        conversation = self.chat()
        OutreachMessage.objects.filter(pk=self.message.pk).update(recipient_telegram_id=conversation.telegram_user_id)
        now = timezone.now()
        OutreachTurn.objects.create(
            conversation=conversation, direction=TurnDirection.OUT, status="done", action="media_ack", telegram_message_id=287,
            text="عکس‌ها رسید، ممنون. به آگهی‌تون اضافه‌شون می‌کنم.",
        )
        OutreachConversation.objects.filter(pk=conversation.pk).update(last_inbound_at=now - timedelta(minutes=4), last_outbound_at=now - timedelta(minutes=1))
        self.assertIsNone(videos.claim_video(self.account, now))


class Audit20261117LastSentTests(AgentTestCase):
    def test_a_claim_that_did_not_go_keeps_the_last_real_send(self) -> None:
        real = self.message.sent_at
        claimed_at = timezone.now()
        OutreachAccount.objects.filter(pk=self.account.pk).update(last_sent_at=claimed_at)
        message = OutreachMessage.objects.create(
            kind=OutreachKind.S1B, recipient_username="germanyyl", listing=self.ad, country="DE", city="Cologne", text="x",
            token="ffff0000ffff0000", status=OutreachStatus.SENDING, account=self.account, claimed_at=claimed_at,
        )
        record_result(message, outcome="retry", error_code=PEER_FLOOD, error_detail="spambot:free", now=claimed_at + timedelta(seconds=6))
        message.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(message.status, OutreachStatus.SKIPPED)
        self.assertEqual(self.account.last_sent_at, real)


class Audit20261117P5RepostTests(HousingOutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        OutreachSettings.objects.update_or_create(pk=1, defaults={"followups_enabled": True, "enabled_kinds": "S1"})
        self.account = OutreachAccount.objects.create(name="monis")
        self.ad = self.listing()
        first = OutreachMessage.objects.create(
            kind=OutreachKind.P1Q, recipient_username="sara_b", recipient_telegram_id=POSTER_ID, listing=self.ad, text="x",
            token="bb00bb00bb00bb00", status=OutreachStatus.SENT, sent_at=timezone.now() - timedelta(days=14), account=self.account,
        )
        self.conversation = OutreachConversation.objects.create(telegram_user_id=POSTER_ID, username="sara_b", account=self.account, first_message=first)

    def test_490_no_p5_one_day_after_his_repost(self) -> None:
        now = timezone.now()
        HomeListing.objects.filter(pk=self.ad.pk).update(first_seen=now - timedelta(days=14), last_seen=now - timedelta(days=1))
        build_outreach_queue()
        self.assertFalse(OutreachMessage.objects.filter(kind=OutreachKind.P5).exists())
        self.ad.refresh_from_db()
        self.assertEqual(followups.p5_skip(self.ad, now), "reposted")

    def test_a_repost_on_a_duplicate_counts(self) -> None:
        now = timezone.now()
        HomeListing.objects.filter(pk=self.ad.pk).update(first_seen=now - timedelta(days=14), last_seen=now - timedelta(days=12))
        copy = self.listing()
        HomeListing.objects.filter(pk=copy.pk).update(canonical=self.ad, last_seen=now - timedelta(hours=20))
        self.ad.refresh_from_db()
        self.assertEqual(followups.p5_skip(self.ad, now), "reposted")

    def test_p5_only_when_the_expiry_is_near_and_checked_again_at_send_time(self) -> None:
        now = timezone.now()
        HomeListing.objects.filter(pk=self.ad.pk).update(first_seen=now - timedelta(days=13), last_seen=now - timedelta(days=12))
        build_outreach_queue()
        p5 = OutreachMessage.objects.get(kind=OutreachKind.P5)
        HomeListing.objects.filter(pk=self.ad.pk).update(last_seen=now - timedelta(hours=2))
        p5.refresh_from_db()
        self.assertEqual(followups.skip_reason(p5, now), "reposted")

    def test_no_expiry_claim_when_the_ad_has_a_week_left(self) -> None:
        now = timezone.now()
        HomeListing.objects.filter(pk=self.ad.pk).update(first_seen=now - timedelta(days=13), last_seen=now - timedelta(days=5))
        self.ad.refresh_from_db()
        self.assertEqual(followups.p5_skip(self.ad, now), "not_expiring")
