"""Adminyar phase 2: the human admin shares the outreach queue, caps and PEER_FLOOD lock with the agent."""

from datetime import timedelta
from unittest import mock

from adminyar import bot as ybot
from adminyar import tasks
from adminyar.models import AdminUser, AuditLog
from outreach import agent
from outreach.accounts import claim_for_account
from outreach.models import ConversationState, OutreachAccount, OutreachConversation, OutreachMessage, OutreachSettings, OutreachStatus, SentBy
from outreach.services import _expire_stale_claims
from tests.test_outreach_housing import POSTER_ID, HousingOutreachTestCase, berlin_noon, person_post_fields

SARA = 8854699370
ENV = {"ADMINYAR_ALLOWED_IDS": str(SARA), "ADMINYAR_OWNER_IDS": "77830141", "ADMINYAR_BOT_TOKEN": "x"}


class HumanQueueTestCase(HousingOutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": True})
        self.sara = OutreachAccount.objects.create(name="sara")
        self.noon = berlin_noon()
        notify = mock.patch("outreach.accounts.tell_mohammad")
        notify.start()
        self.addCleanup(notify.stop)

    def queue(self, n: int = 1) -> None:
        for index in range(n):
            self.listing(poster=person_post_fields(user_id=POSTER_ID + index, username=f"poster_{index}", name="سارا"))
        rows = self.built()
        assert rows and all(row.status == OutreachStatus.QUEUED for row in rows), [(r.kind, r.status, r.error_code) for r in rows]


class HumanClaimTests(HumanQueueTestCase):
    def test_human_claim_uses_the_queue_and_marks_the_row(self) -> None:
        self.queue()
        message, reason = tasks.claim("sara", self.noon)
        self.assertEqual(reason, "")
        self.assertEqual((message.status, message.sent_by, message.account, message.attempts), (OutreachStatus.SENDING, SentBy.HUMAN, self.sara, 1))

    def test_the_model_never_rewords_the_human_template(self) -> None:
        self.queue()
        with mock.patch("outreach.writer.rewrite", side_effect=AssertionError("rewritten")):
            message, _reason = tasks.claim("sara", self.noon)
        self.assertIsNotNone(message)

    def test_one_recipient_one_claim(self) -> None:
        self.queue()
        tasks.claim("sara", self.noon)
        OutreachAccount.objects.create(name="niloofar")
        self.assertEqual(claim_for_account("niloofar", self.noon + timedelta(minutes=1))[0], None)

    def test_shared_first_message_cap(self) -> None:
        self.queue(3)
        OutreachAccount.objects.filter(pk=self.sara.pk).update(first_daily_cap=2)
        first, _ = tasks.claim("sara", self.noon)
        tasks.report(first.pk, "sara", "sent", now=self.noon)
        # The agent's next send on the same account takes the second slot; the human then hits the cap.
        OutreachAccount.objects.filter(pk=self.sara.pk).update(next_send_at=None, last_sent_at=None)
        agent_message, reason = claim_for_account("sara", self.noon + timedelta(minutes=1))
        self.assertEqual(reason, "")
        self.assertEqual(agent_message.sent_by, SentBy.AGENT)
        OutreachAccount.objects.filter(pk=self.sara.pk).update(next_send_at=None, last_sent_at=None)
        self.assertEqual(tasks.claim("sara", self.noon + timedelta(minutes=2)), (None, "account_first_cap"))

    def test_pace_gap_applies_to_the_human(self) -> None:
        self.queue(2)
        tasks.claim("sara", self.noon)
        self.assertEqual(tasks.claim("sara", self.noon + timedelta(minutes=1)), (None, "account_gap"))

    def test_human_peer_flood_locks_the_account_for_the_agent_too(self) -> None:
        self.queue(2)
        message, _ = tasks.claim("sara", self.noon)
        self.assertEqual(tasks.report(message.pk, "sara", "flood", now=self.noon), "ok")
        message.refresh_from_db()
        self.assertEqual((message.status, message.error_code), (OutreachStatus.SKIPPED, "peer_flood"))
        self.sara.refresh_from_db()
        self.assertGreater(self.sara.paused_until, self.noon)
        self.assertEqual(claim_for_account("sara", self.noon + timedelta(minutes=30)), (None, "account_paused"))

    def test_report_twice_is_gone(self) -> None:
        self.queue()
        message, _ = tasks.claim("sara", self.noon)
        self.assertEqual(tasks.report(message.pk, "sara", "notfound", now=self.noon), "ok")
        self.assertEqual(OutreachMessage.objects.get(pk=message.pk).status, OutreachStatus.FAILED)
        self.assertEqual(tasks.report(message.pk, "sara", "sent", now=self.noon), "gone")

    def test_limited_locks_first_messages_until_next_local_day(self) -> None:
        self.queue()
        until = tasks.mark_limited("sara", self.noon)
        self.assertGreater(until, self.noon)
        self.assertEqual(tasks.claim("sara", self.noon), (None, "account_paused"))
        self.assertEqual(claim_for_account("sara", self.noon), (None, "account_paused"))


class LeaseTests(HumanQueueTestCase):
    def test_agent_lease_unchanged(self) -> None:
        self.queue()
        message, _ = claim_for_account("sara", self.noon)
        _expire_stale_claims(self.noon + timedelta(minutes=16))
        message.refresh_from_db()
        self.assertEqual((message.status, message.error_code), (OutreachStatus.FAILED, "lease_expired"))

    def test_unreported_human_claim_fails_as_contacted_never_back_to_the_agent(self) -> None:
        self.queue()
        message, _ = tasks.claim("sara", self.noon)
        _expire_stale_claims(self.noon + timedelta(minutes=16))
        message.refresh_from_db()
        self.assertEqual(message.status, OutreachStatus.SENDING)
        _expire_stale_claims(self.noon + timedelta(hours=7))
        message.refresh_from_db()
        self.assertEqual((message.status, message.error_code), (OutreachStatus.FAILED, "human_unreported"))
        from outreach.services import already_contacted

        self.assertTrue(already_contacted(username=message.recipient_username, telegram_user_id=message.recipient_telegram_id))
        OutreachAccount.objects.create(name="niloofar")
        self.assertEqual(claim_for_account("niloofar", self.noon + timedelta(hours=7))[0], None)

    def test_reminder_once_after_an_hour(self) -> None:
        self.queue()
        message, _ = tasks.claim("sara", self.noon)
        self.assertEqual(tasks.due_reminders(self.noon + timedelta(minutes=30)), [])
        self.assertEqual([m.pk for m in tasks.due_reminders(self.noon + timedelta(hours=2))], [message.pk])
        tasks.mark_reminded(message)
        self.assertEqual(tasks.due_reminders(self.noon + timedelta(hours=2)), [])


class ManualChatTests(HumanQueueTestCase):
    def test_reply_to_a_human_first_message_opens_a_human_chat(self) -> None:
        self.queue()
        message, _ = tasks.claim("sara", self.noon)
        tasks.report(message.pk, "sara", "sent", now=self.noon)
        agent.record_inbound("sara", POSTER_ID, "poster_0", 900, "سلام", False)
        self.assertEqual(OutreachConversation.objects.get(telegram_user_id=POSTER_ID).state, ConversationState.HUMAN)

    def test_reply_to_an_agent_first_message_stays_open(self) -> None:
        self.queue()
        message, _ = claim_for_account("sara", self.noon)
        from outreach.services import record_result

        record_result(message, outcome="sent", recipient_telegram_id=POSTER_ID, telegram_message_id=5, now=self.noon)
        agent.record_inbound("sara", POSTER_ID, "poster_0", 900, "سلام", False)
        self.assertEqual(OutreachConversation.objects.get(telegram_user_id=POSTER_ID).state, ConversationState.OPEN)

    def _agent_chat(self, *, admin: bool = True) -> OutreachConversation:
        from outreach.services import record_result

        self.queue()
        message, _ = claim_for_account("sara", self.noon)
        record_result(message, outcome="sent", recipient_telegram_id=POSTER_ID, telegram_message_id=5, now=self.noon)
        agent.record_inbound("sara", POSTER_ID, "poster_0", 900, "سلام", False, now=self.noon)
        if admin:
            AdminUser.objects.create(telegram_user_id=SARA, name="سارا", team_account="sara")
        return OutreachConversation.objects.get(telegram_user_id=POSTER_ID)

    def age_lines(self, chat, minutes=0):
        from outreach.models import OutreachTurn

        OutreachTurn.objects.filter(conversation=chat, action="by_person").update(created_at=self.noon + timedelta(minutes=minutes))

    def _next_inbound(self, chat, minutes=10):
        agent.record_inbound("sara", POSTER_ID, "poster_0", 990 + minutes, "باشه", False, now=self.noon + timedelta(minutes=minutes))
        chat.refresh_from_db()
        return chat.state

    def test_agent_reply_on_an_admin_account_keeps_the_chat_open(self) -> None:
        from outreach.models import OutreachTurn, TurnDirection, TurnStatus

        chat = self._agent_chat()
        reply = OutreachTurn.objects.create(conversation=chat, direction=TurnDirection.OUT, text="**جواب ایجنت**", status=TurnStatus.ANSWERING, action="answer")
        # The worker's own outgoing event arrives first, with markdown-stripped text, before the turn's result.
        agent.record_outbound("sara", POSTER_ID, 950, "جواب ایجنت", now=self.noon + timedelta(minutes=1))
        self.age_lines(chat)
        agent.reply_sent(reply.pk, ok=True, telegram_message_id=950, now=self.noon + timedelta(minutes=1))
        self.assertEqual(self._next_inbound(chat), ConversationState.OPEN)
        self.assertEqual(chat.turns.filter(telegram_message_id=950).count(), 1)

    def test_agent_reply_with_the_same_text_is_never_hand_written(self) -> None:
        from outreach.models import OutreachTurn, TurnDirection, TurnStatus

        chat = self._agent_chat()
        OutreachTurn.objects.create(conversation=chat, direction=TurnDirection.OUT, text="جواب ایجنت", status=TurnStatus.ANSWERING, action="answer")
        self.assertIsNone(agent.record_outbound("sara", POSTER_ID, 951, "جواب ایجنت", now=self.noon + timedelta(minutes=1)))
        self.assertEqual(self._next_inbound(chat), ConversationState.OPEN)

    def test_video_caption_from_the_worker_keeps_the_chat_open(self) -> None:
        from outreach.models import OutreachTurn
        from outreach.videos import _video_turn

        chat = self._agent_chat()
        agent.record_outbound("sara", POSTER_ID, 960, "کپشن ویدیو", now=self.noon + timedelta(minutes=1))
        self.age_lines(chat)
        _video_turn(chat.first_message, 960, self.noon + timedelta(minutes=1))
        self.assertEqual(OutreachTurn.objects.get(telegram_message_id=960).action, "video")
        self.assertEqual(self._next_inbound(chat), ConversationState.OPEN)

    def test_hand_written_line_on_an_admin_account_makes_the_chat_human_at_the_next_inbound(self) -> None:
        chat = self._agent_chat()
        agent.record_outbound("sara", POSTER_ID, 901, "جواب دستی", now=self.noon + timedelta(minutes=1))
        self.age_lines(chat, minutes=1)
        chat.refresh_from_db()
        self.assertEqual(chat.state, ConversationState.OPEN)  # not before it settles
        self.assertEqual(self._next_inbound(chat, minutes=2), ConversationState.OPEN)
        self.assertEqual(self._next_inbound(chat, minutes=10), ConversationState.HUMAN)

    def test_hand_written_line_without_admin_keeps_old_behaviour(self) -> None:
        chat = self._agent_chat(admin=False)
        agent.record_outbound("sara", POSTER_ID, 901, "جواب دستی", now=self.noon + timedelta(minutes=1))
        self.age_lines(chat)
        self.assertEqual(self._next_inbound(chat), ConversationState.OPEN)


@mock.patch.dict("os.environ", ENV)
class BotFlowTests(HumanQueueTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sent = []
        self.bot = ybot.Bot("x", api=lambda method, payload: self.sent.append((method, payload)) or {}, fetch=lambda _f: None)
        clock = mock.patch("adminyar.tasks.timezone.now", return_value=self.noon)
        clock.start()
        self.addCleanup(clock.stop)

    def press(self, text):
        self.bot.handle({"update_id": 1, "message": {"chat": {"id": SARA, "type": "private"}, "from": {"id": SARA}, "text": text}})
        return self.sent[-1][1]

    def test_next_person_card_then_sent_gives_the_next(self) -> None:
        self.queue(2)
        card = self.press(ybot.NEXT_PERSON)
        self.assertIn("@poster_", card["text"])
        data = card["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        self.bot.handle({"update_id": 2, "callback_query": {"id": "c", "data": data, "from": {"id": SARA}, "message": {"chat": {"id": SARA}}}})
        texts = [p["text"] for m, p in self.sent if m == "sendMessage"]
        self.assertEqual(texts[-2], ybot.T_RESULT["sent"])
        self.assertIn("فاصله", texts[-1])  # the pace gap holds the next one
        self.assertEqual(OutreachMessage.objects.filter(status=OutreachStatus.SENT, sent_by=SentBy.HUMAN).count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="result", result="ok").exists())

    def test_pressing_again_shows_the_open_claim(self) -> None:
        self.queue(2)
        first = self.press(ybot.NEXT_PERSON)["text"]
        self.assertEqual(self.press(ybot.NEXT_PERSON)["text"], first)

    def test_limited_button(self) -> None:
        reply = self.press(ybot.LIMITED)
        self.assertIn("متوقف", reply["text"])
        self.sara.refresh_from_db()
        self.assertEqual(self.sara.pause_reason, tasks.LIMITED)

    def test_reminder_goes_once_to_the_account_admin(self) -> None:
        self.queue()
        self.press(ybot.NEXT_PERSON)
        self.sent.clear()
        with mock.patch("adminyar.tasks.timezone.now", return_value=self.noon + timedelta(hours=2)):
            self.assertEqual(self.bot.remind(), 1)
            self.assertEqual(self.bot.remind(), 0)
        method, payload = self.sent[-1]
        self.assertEqual(payload["chat_id"], SARA)
        self.assertIn("یادآوری", payload["text"])
