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

    def test_human_sends_count_on_the_agent_cap_but_never_stop_the_human(self) -> None:
        self.queue(3)
        OutreachAccount.objects.filter(pk=self.sara.pk).update(first_daily_cap=1)
        first, _ = tasks.claim("sara", self.noon)
        tasks.report(first.pk, "sara", "sent", now=self.noon)
        OutreachAccount.objects.filter(pk=self.sara.pk).update(next_send_at=None, last_sent_at=None)
        # The agent backs off: the human's send took the account's one slot.
        self.assertEqual(claim_for_account("sara", self.noon + timedelta(minutes=1)), (None, "account_first_cap"))
        second, reason = tasks.claim("sara", self.noon + timedelta(minutes=1))
        self.assertEqual(reason, "")
        self.assertIsNotNone(second)

    def test_no_pace_gap_and_no_send_window_for_the_human(self) -> None:
        self.queue(2)
        tasks.claim("sara", self.noon)
        self.assertIsNotNone(tasks.claim("sara", self.noon + timedelta(minutes=1))[0])
        self.assertEqual(claim_for_account("sara", self.noon.replace(hour=7)), (None, "account_gap"))

    def test_night_and_global_pause_do_not_stop_the_human(self) -> None:
        self.queue()
        from outreach.services import pause_sending

        pause_sending(self.noon + timedelta(days=1), reason="test")
        self.assertIsNotNone(tasks.claim("sara", self.noon.replace(hour=3))[0])

    def test_shared_pool_planned_for_another_account(self) -> None:
        self.queue()
        niloofar = OutreachAccount.objects.create(name="niloofar")
        OutreachMessage.objects.update(account=niloofar)
        message, _ = tasks.claim("sara", self.noon)
        self.assertEqual(message.account, self.sara)

    def test_person_owned_by_another_account_is_never_handed_out(self) -> None:
        self.queue()
        niloofar = OutreachAccount.objects.create(name="niloofar")
        target = OutreachMessage.objects.get()
        OutreachConversation.objects.create(telegram_user_id=target.recipient_telegram_id or POSTER_ID, username=target.recipient_username, account=niloofar)
        with mock.patch("outreach.services.build_outreach_queue"):
            self.assertIsNone(tasks.claim("sara", self.noon)[0])

    def test_empty_queue_is_built_once_and_tried_again(self) -> None:
        with mock.patch("outreach.services.build_outreach_queue", side_effect=lambda **_kw: self.queue()) as build:
            message, reason = tasks.claim("sara", self.noon)
        build.assert_called_once()
        self.assertEqual(reason, "")
        self.assertIsNotNone(message)

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

    def test_limited_and_flood_stop_only_the_agent(self) -> None:
        self.queue(3)
        until = tasks.mark_limited("sara", self.noon)
        self.assertGreater(until, self.noon)
        self.assertEqual(claim_for_account("sara", self.noon), (None, "account_paused"))
        message, _ = tasks.claim("sara", self.noon)
        tasks.report(message.pk, "sara", "flood", now=self.noon)
        self.assertIsNotNone(tasks.claim("sara", self.noon + timedelta(minutes=1))[0])


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
        self.assertIn("@poster_", texts[-1])  # the next one follows at once
        self.assertEqual(OutreachMessage.objects.filter(status=OutreachStatus.SENT, sent_by=SentBy.HUMAN).count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="result", result="ok").exists())

    def test_pressing_again_shows_the_open_claim(self) -> None:
        self.queue(2)
        first = self.press(ybot.NEXT_PERSON)["text"]
        self.assertEqual(self.press(ybot.NEXT_PERSON)["text"], first)

    def test_limited_button(self) -> None:
        reply = self.press(ybot.LIMITED)
        self.assertIn("ادامه بدهید", reply["text"])
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

    def test_empty_queue_never_says_limit(self) -> None:
        with mock.patch("outreach.services.build_outreach_queue"):
            reply = self.press(ybot.NEXT_PERSON)
        self.assertEqual(reply["text"], ybot.T_SOON_NEXT)
        for word in ("محدود", "سقف", "متوقف"):
            self.assertNotIn(word, reply["text"])


class TakeoverTests(HumanQueueTestCase):
    """Mohammad, 2026-11-12: in a chat the human admin handles, 3 minutes for the admin, then the agent answers on the
    same account and keeps the chat; never both on one inbound."""

    def _human_chat(self):
        self.queue()
        message, _ = tasks.claim("sara", self.noon)
        tasks.report(message.pk, "sara", "sent", now=self.noon)
        OutreachSettings.objects.filter(pk=1).update(agent_enabled=True)
        found = agent.record_inbound("sara", POSTER_ID, "poster_0", 900, "سلام، هنوز هست؟", False, now=self.noon)
        return OutreachConversation.objects.get(telegram_user_id=POSTER_ID), found.turn

    def test_the_agent_answers_after_three_minutes_on_the_same_account(self) -> None:
        from tests.test_outreach_agent import model_says

        chat, turn = self._human_chat()
        self.assertEqual(chat.state, ConversationState.HUMAN)
        with model_says(intent="available", listing="L1"):
            self.assertIsNone(agent.next_reply("sara", self.noon + timedelta(minutes=2, seconds=50)))
            handed = agent.next_reply("sara", self.noon + timedelta(minutes=3, seconds=1))
        self.assertIsNotNone(handed)
        chat.refresh_from_db()
        self.assertEqual((chat.state, chat.account.name), (ConversationState.OPEN, "sara"))
        self.assertIsNone(agent.next_reply("niloofar", self.noon + timedelta(minutes=4)))

    def test_an_admin_answer_inside_three_minutes_wins(self) -> None:
        from tests.test_outreach_agent import model_says

        chat, turn = self._human_chat()
        agent.record_outbound("sara", POSTER_ID, 901, "بله هنوز هست", now=self.noon + timedelta(minutes=1))
        with model_says(intent="available", listing="L1"):
            self.assertIsNone(agent.next_reply("sara", self.noon + timedelta(minutes=5)))
        turn.refresh_from_db()
        self.assertEqual(turn.action, "answered_by_person")
        chat.refresh_from_db()
        self.assertEqual(chat.state, ConversationState.HUMAN)


class OnePersonRaceTests(HumanQueueTestCase):
    """Two claims on one person at the same moment (human and agent): exactly one goes."""

    def test_a_person_another_claim_holds_is_never_handed_out(self) -> None:
        import threading

        from django.db import connections

        from outreach.services import _person_keys

        self.queue()
        row = OutreachMessage.objects.get(status=OutreachStatus.QUEUED)
        holding, release = threading.Event(), threading.Event()

        def other_claim() -> None:
            connection = connections.create_connection("default")
            try:
                with connection.cursor() as cursor:
                    cursor.execute("BEGIN")
                    for key in _person_keys(row):
                        cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [key])
                    holding.set()
                    release.wait(10)
                    cursor.execute("COMMIT")
            finally:
                connection.close()

        thread = threading.Thread(target=other_claim)
        thread.start()
        try:
            self.assertTrue(holding.wait(10))
            message, _ = claim_for_account("sara", self.noon)
            self.assertIsNone(message)
            row.refresh_from_db()
            self.assertEqual(row.status, OutreachStatus.QUEUED)
        finally:
            release.set()
            thread.join()
        message, _ = tasks.claim("sara", self.noon)
        self.assertEqual(message.pk, row.pk)


@mock.patch.dict("os.environ", ENV)
class StatusNoticeTests(HumanQueueTestCase):
    def test_a_limited_account_is_shown_on_the_card_and_the_claim_still_goes(self) -> None:
        sent = []
        bot = ybot.Bot("x", api=lambda method, payload: sent.append((method, payload)) or {}, fetch=lambda _f: None)
        AdminUser.objects.create(telegram_user_id=SARA, name="سارا", team_account="sara")
        self.queue()
        OutreachAccount.objects.filter(pk=self.sara.pk).update(enabled=False, paused_until=self.noon + timedelta(days=2), pause_reason="PEER_FLOOD")
        with mock.patch("adminyar.tasks.timezone.now", return_value=self.noon):
            bot.handle({"update_id": 1, "message": {"chat": {"id": SARA, "type": "private"}, "from": {"id": SARA}, "text": ybot.NEXT_PERSON}})
        card = sent[-1][1]["text"]
        self.assertTrue(card.startswith("⚠️ این اکانت خاموش است؛ محدودیت (PEER_FLOOD) تا "), card)
        self.assertIn("به وقت تورنتو", card)
        self.assertIn("@poster_0", card)
        self.assertEqual(tasks.account_status("sara", self.noon + timedelta(days=3)), "⚠️ این اکانت خاموش است. فقط برای اطلاع است؛ اگه خواستید بفرستید.")
