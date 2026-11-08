from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from ai.openrouter import ChatResult
from outreach import guard, kindcheck
from outreach.models import OutreachKind
from tests.test_outreach_agent import AgentTestCase

LINK = "https://t.me/chamedoon_home_bot?startapp=home_de_berlin_l1"


class GuardTests(SimpleTestCase):
    def test_persian_only(self) -> None:
        with self.assertRaises(guard.Blocked) as caught:
            guard.check("Thanks for your reply, we renewed it.")
        self.assertEqual(caught.exception.reason, "not_persian")
        self.assertTrue(guard.check(f"سلام Reza جان، آگهی‌تون اینجاست: {LINK}"))

    def test_emoji_are_stripped(self) -> None:
        self.assertEqual(guard.check("خواهش می‌کنم 🙏 هر سؤالی بود بپرسید 🙂"), "خواهش می‌کنم هر سؤالی بود بپرسید")

    def test_a_link_is_one_plain_url(self) -> None:
        text = guard.check(f"آگهی‌تون اینجاست: [{LINK}]({LINK})")
        self.assertEqual(text.count(LINK), 1)
        self.assertNotIn("](", text)
        self.assertEqual(guard.check(f"ببینید: [دیدن آگهی]({LINK})"), f"ببینید: {LINK}")

    def test_the_same_url_twice_is_blocked(self) -> None:
        with self.assertRaises(guard.Blocked) as caught:
            guard.check(f"آگهی‌تون: {LINK}\nدوباره: {LINK}")
        self.assertEqual(caught.exception.reason, "url_twice")

    def test_the_same_text_twice_in_a_chat_is_blocked(self) -> None:
        with self.assertRaises(guard.Blocked) as caught:
            guard.check("ثبت شد، ممنون.", earlier=["ثبت  شد،\nممنون."])
        self.assertEqual(caught.exception.reason, "duplicate")

    def test_never_tells_the_team(self) -> None:
        with self.assertRaises(guard.Blocked) as caught:
            guard.check("این رو به تیم می‌گم و خبرتون می‌کنیم.")
        self.assertEqual(caught.exception.reason, "deferral")


class AgentGuardTests(AgentTestCase):
    def test_an_operator_text_already_sent_in_the_chat_is_not_sent_again(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        first = agent.queue_operator_reply(conversation, "آگهی‌تون رو تمدید کردیم.")
        handed = agent.next_reply("sina")
        agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=700)
        agent.queue_operator_reply(conversation, "آگهی‌تون رو  تمدید کردیم.")
        with patch("outreach.agent.tell_mohammad"):
            self.assertIsNone(agent.next_reply("sina"))
        self.assertEqual(first.pk, handed["turn_id"])


class StaleOperatorTextTests(AgentTestCase):
    def test_a_written_text_is_dropped_when_they_wrote_after_it(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        agent.queue_operator_reply(conversation, "فیلم هم کاملاً خوبه.")
        self.say("یه سؤال دیگه", 2)
        with patch("outreach.agent.tell_mohammad"), patch("outreach.agent.classify", return_value=None):
            self.assertIsNone(agent.next_reply("sina"))
        self.assertEqual(conversation.turns.get(action="operator").action_detail["blocked"], "stale")


class KindCheckTests(AgentTestCase):
    def _says(self, kind: str):
        return patch("ai.openrouter.complete", return_value=ChatResult(ok=True, text=f'{{"kind": "{kind}"}}', model="m"))

    def test_an_owner_message_goes_only_when_the_post_offers_a_place(self) -> None:
        self.message.kind = OutreachKind.P1Q
        with self._says("offer"):
            self.assertTrue(kindcheck.agrees(self.message))
        self.message.extra = {}
        with self._says("wanted"):
            self.assertFalse(kindcheck.agrees(self.message))

    def test_a_seeker_message_to_someone_offering_a_room_is_stopped(self) -> None:
        self.message.kind = OutreachKind.S1
        with self._says("offer"):
            self.assertFalse(kindcheck.agrees(self.message))

    def test_model_down_means_wait(self) -> None:
        self.message.kind = OutreachKind.S1B
        with patch("ai.openrouter.complete", return_value=ChatResult(ok=False, text="", model="m")):
            self.assertIsNone(kindcheck.agrees(self.message))

    def test_a_mismatch_sends_the_listing_to_review(self) -> None:
        kindcheck.flag(self.message)
        self.ad.refresh_from_db()
        self.assertTrue(self.ad.needs_review)
        self.assertEqual(self.message.extra["stored_kind"], self.ad.kind)


class ReplyWindowTests(AgentTestCase):
    def test_a_reply_waits_for_the_persons_local_hours(self) -> None:
        from datetime import datetime, timezone as tz

        from outreach import agent
        from outreach.models import OutreachConversation

        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        agent.queue_operator_reply(conversation, "آگهی‌تون رو تمدید کردیم.")
        city = conversation.first_message.city
        night = datetime(2026, 11, 6, 7, 0, tzinfo=tz.utc) if city in {"Toronto", "Montreal", "Vancouver"} else datetime(2026, 11, 6, 22, 0, tzinfo=tz.utc)
        self.assertFalse(agent.in_window(conversation, night))
        self.assertIsNone(agent.next_reply("sina", night))
