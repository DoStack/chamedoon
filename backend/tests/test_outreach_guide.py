"""G1: a question under our repost in a group gets one guide DM, written by the model, into the Mini App ad."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from ai.openrouter import ChatResult
from home.models import HomeChannel, HomePublication
from outreach import agent, guide
from outreach.accounts import claim_for_account
from outreach.models import OutreachAccount, OutreachConversation, OutreachKind, OutreachMessage, OutreachSettings, OutreachStatus, OutreachTurn
from tests.test_outreach_agent import SECRET
from tests.test_outreach_housing import HousingOutreachTestCase, berlin_noon, person_post_fields

GROUP = -1009990001112
ASKER = 777001
DM = "سلام، سؤالتون درباره این آگهی تو گروه چمدون رو دیدم. اون پست بازنشر ماست و آگهی‌دهنده ما نیستیم.\nاجاره‌ش ۵۲۰ یوروئه. لینک رو بزنید، «شروع» رو بزنید، آگهی باز می‌شه و راه تماس همون‌جاست.\n{link}"


def model_writes(text: str):
    return patch("outreach.guide._complete", return_value=ChatResult(ok=True, text=text, model="m"))


@override_settings(OUTREACH_SERVICE_SECRET=SECRET)
class GuideTestCase(HousingOutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": True, "agent_enabled": True, "enabled_kinds": "S1,S1B,S2,P1,P1Q,G1"})
        self.account = OutreachAccount.objects.create(name="sina")
        self.channel, _ = HomeChannel.objects.update_or_create(country="DE", city="Berlin", defaults={"chat_id": -1001234, "group_chat_id": GROUP})
        self.ad = self.listing()
        HomePublication.objects.create(listing=self.ad, channel=self.channel, message_ids=[55], published_at=timezone.now())

    def ask(self, text: str = "سلام قیمتش چنده؟ هنوز هست؟", *, asker: int = ASKER, message_id: int = 9001, ask: bool = True, **extra) -> str:
        with patch("outreach.guide.is_question", return_value=ask):
            return guide.record_question(
                account_name="sina",
                group_chat_id=GROUP,
                group_message_id=message_id,
                channel_post_id=extra.pop("post", 55),
                asker_id=asker,
                asker_username="asker_x",
                asker_name="Reza",
                is_bot=extra.pop("is_bot", False),
                text=text,
            )


class RecordQuestionTests(GuideTestCase):
    def test_a_question_under_our_repost_queues_one_guide(self) -> None:
        self.assertEqual(self.ask(), "")
        message = OutreachMessage.objects.get(kind=OutreachKind.G1)
        self.assertEqual((message.listing_id, message.recipient_telegram_id, message.account, message.city), (self.ad.pk, ASKER, self.account, "Berlin"))
        self.assertEqual(message.extra["question"], "سلام قیمتش چنده؟ هنوز هست؟")

    def test_one_guide_per_asker_ever(self) -> None:
        self.assertEqual(self.ask(), "")
        self.assertEqual(self.ask(message_id=9002), "guided_before")

    def test_the_same_group_message_seen_by_two_accounts_queues_once(self) -> None:
        self.assertEqual(self.ask(), "")
        OutreachMessage.objects.update(kind=OutreachKind.S1)  # as if not a guide, to reach the dedupe by message
        self.assertEqual(self.ask(), "seen")

    def test_no_guide_for_bots_the_poster_non_questions_or_other_posts(self) -> None:
        self.assertEqual(self.ask(is_bot=True), "bot")
        from tests.test_outreach_housing import POSTER_ID

        self.assertEqual(self.ask(asker=POSTER_ID), "poster")
        self.assertEqual(self.ask("ممنون از گروه خوبتون", ask=False), "not_a_question")
        self.assertEqual(self.ask(post=999), "not_our_post")
        self.assertFalse(OutreachMessage.objects.exists())

    def test_no_guide_into_a_chat_or_on_top_of_an_unanswered_message(self) -> None:
        OutreachConversation.objects.create(telegram_user_id=ASKER, username="asker_x", account=self.account)
        self.assertEqual(self.ask(), "has_chat")
        OutreachConversation.objects.all().delete()
        OutreachMessage.objects.create(kind=OutreachKind.S2, recipient_telegram_id=ASKER, token="t" * 16, text="x", status=OutreachStatus.SENT, account=self.account)
        self.assertEqual(self.ask(), "unanswered")

    def test_switched_off_unless_g1_is_an_enabled_kind(self) -> None:
        OutreachSettings.objects.update(enabled_kinds="S1,S1B,S2")
        self.assertEqual(self.ask(), "kind_off")


class GuideSendTests(GuideTestCase):
    def test_the_model_writes_the_dm_with_our_one_link_and_no_poster(self) -> None:
        self.ask()
        with model_writes(DM):
            message, reason = claim_for_account("sina", berlin_noon())
        self.assertEqual(reason, "")
        self.assertEqual(message.kind, OutreachKind.G1)
        link = guide.ad_link(message)
        self.assertEqual(message.text.count("https://"), 1)
        self.assertTrue(message.text.endswith(link))
        self.assertIn(f"l{self.ad.pk}", link)
        self.assertNotIn("@", message.text)

    def test_a_dm_that_names_the_poster_is_not_sent(self) -> None:
        self.ask()
        with model_writes("سلام، با آیدی @sara_b در ارتباط باشید.\n{link}"):
            self.assertIsNone(claim_for_account("sina", berlin_noon())[0])
        self.assertEqual(OutreachMessage.objects.get().error_code, "blocked_mention")

    def test_a_phone_number_is_not_sent(self) -> None:
        self.assertEqual(guide.reveals("شماره‌ش ۰۹۱۲ ۳۴۵ ۶۷۸۹ هست"), "phone")
        self.assertEqual(guide.reveals("یه لینک دیگه https://t.me/some_group/12"), "foreign_link")
        self.assertEqual(guide.reveals("اجاره ۵۲۰ یورو از ۱ دسامبر"), "")

    def test_the_model_down_leaves_it_waiting(self) -> None:
        self.ask()
        with patch("outreach.guide._complete", return_value=ChatResult(ok=False, text="", model="m", error="down")):
            self.assertIsNone(claim_for_account("sina", berlin_noon())[0])
        self.assertEqual(OutreachMessage.objects.get().status, OutreachStatus.QUEUED)

    def test_a_question_older_than_a_day_is_dropped(self) -> None:
        self.ask()
        OutreachMessage.objects.update(source_at=berlin_noon() - timedelta(hours=25))
        with model_writes(DM):
            self.assertIsNone(claim_for_account("sina", berlin_noon())[0])
        self.assertEqual(OutreachMessage.objects.get().error_code, "stale")


class GuideReplyTests(GuideTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.ask()
        with model_writes(DM):
            message, _ = claim_for_account("sina", berlin_noon())
        OutreachMessage.objects.filter(pk=message.pk).update(status=OutreachStatus.SENT, sent_at=timezone.now(), recipient_telegram_id=ASKER)

    def reply(self, text: str):
        found = agent.record_inbound("sina", ASKER, "asker_x", 50, text, False)
        OutreachTurn.objects.filter(pk=found.turn.pk).update(reply_due_at=timezone.now() - timedelta(seconds=1))
        return found

    def test_their_reply_is_answered_by_the_model_without_the_intro(self) -> None:
        self.reply("لینک رو زدم ولی نمی‌دونم شماره کجاست")
        with model_writes("تو صفحه‌ی آگهی دکمه‌ی تماس هست؛ اگه گفت اول عضو کانال بشید، عضو بشید و دوباره بزنید."):
            handed = agent.next_reply("sina")
        self.assertIn("دکمه‌ی تماس", handed["text"])
        self.assertFalse(OutreachTurn.objects.filter(action="about").exists())
        self.assertEqual(OutreachTurn.objects.get(pk=handed["turn_id"]).action, "guide")

    def test_a_reply_that_would_give_the_poster_away_is_not_sent(self) -> None:
        self.reply("آیدیش رو بدید")
        with model_writes("آیدیش @sara_b هست"), patch("outreach.agent.tell_mohammad") as told:
            self.assertIsNone(agent.next_reply("sina"))
        told.assert_called_once()

    def test_a_reply_frees_a_budget_slot(self) -> None:
        from outreach.accounts import unreplied_since

        since = timezone.now() - timedelta(hours=24)
        before = unreplied_since(self.account, since - timedelta(days=1))
        self.reply("ممنون")
        self.assertEqual(unreplied_since(self.account, since - timedelta(days=1)), before - 1)


class BudgetTests(HousingOutreachTestCase):
    def test_twenty_unreplied_messages_stop_the_account_and_a_reply_frees_one(self) -> None:
        from outreach.accounts import account_gate

        account = OutreachAccount.objects.create(name="sara")
        now = timezone.now()
        rows = [
            OutreachMessage.objects.create(kind=OutreachKind.S2, token=f"b{index:015d}", text="x", status=OutreachStatus.SENT, account=account, claimed_at=now - timedelta(hours=2))
            for index in range(20)
        ]
        self.assertEqual(account_gate(account, now), "account_daily_limit")
        OutreachMessage.objects.filter(pk=rows[0].pk).update(replied_at=now)
        self.assertNotEqual(account_gate(account, now), "account_daily_limit")
