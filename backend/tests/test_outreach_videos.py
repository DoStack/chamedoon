from __future__ import annotations

from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from outreach.accounts import account_gate, claim_for_account
from outreach.models import (
    OutreachAccount,
    OutreachConversation,
    OutreachKind,
    OutreachMessage,
    OutreachOptOut,
    OutreachSettings,
    OutreachStatus,
    OutreachTurn,
    TurnDirection,
)
from outreach.videos import record_video
from tests.test_outreach_housing import POSTER_ID, HousingOutreachTestCase, berlin_noon


class VideoTests(HousingOutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": True})
        self.account = OutreachAccount.objects.create(name="sina")
        self.ad = self.listing()

    def sent(self, kind=OutreachKind.P1, minutes_ago: int = 30, **extra) -> OutreachMessage:
        at = berlin_noon() - timedelta(minutes=minutes_ago)
        return OutreachMessage.objects.create(
            kind=kind, recipient_username="sara_b", recipient_telegram_id=POSTER_ID, listing=self.ad, country="DE", city="Berlin",
            text="x", token=f"t{minutes_ago:015d}", status=OutreachStatus.SENT, sent_at=at, claimed_at=at, account=self.account, **extra,
        )

    def exchange(self, *, they_said: str = "سلام، بله", answered_minutes_ago: int = 2) -> OutreachConversation:
        at = berlin_noon() - timedelta(minutes=answered_minutes_ago)
        conversation = OutreachConversation.objects.create(
            telegram_user_id=POSTER_ID, username="sara_b", account=self.account, last_inbound_at=at - timedelta(minutes=3), last_outbound_at=at
        )
        OutreachTurn.objects.create(conversation=conversation, direction=TurnDirection.IN, text=they_said)
        return conversation

    def test_no_video_before_they_answer(self) -> None:
        self.sent()
        self.assertIsNone(claim_for_account("sina", berlin_noon())[0])

    def test_the_video_follows_our_answer_once_with_its_line(self) -> None:
        from api.outreach_views import _message_json

        message = self.sent()
        self.exchange()
        video, reason = claim_for_account("sina", berlin_noon())
        self.assertEqual((video.pk, reason, video.video_only), (message.pk, "", True))
        payload = _message_json(video)
        self.assertEqual(payload["video"], "owner")
        self.assertIn("چمدون چطور کار", payload["caption"])
        record_video(message.pk, ok=True)
        OutreachAccount.objects.filter(pk=self.account.pk).update(next_send_at=None, last_sent_at=None)
        again, _ = claim_for_account("sina", berlin_noon() + timedelta(minutes=5))
        self.assertTrue(again is None or not getattr(again, "video_only", False))

    def test_the_line_is_persian_even_for_english_writers(self) -> None:
        from api.outreach_views import _message_json

        self.sent()
        self.exchange(they_said="Yes, it's available")
        video, _ = claim_for_account("sina", berlin_noon())
        self.assertIn("چمدون چطور کار", _message_json(video)["caption"])

    def test_an_old_exchange_waits_for_the_next_answer(self) -> None:
        self.sent(minutes_ago=600)
        conversation = self.exchange(answered_minutes_ago=300)
        self.assertIsNone(claim_for_account("sina", berlin_noon())[0])
        # They wrote again and we have not answered yet: still nothing.
        OutreachConversation.objects.filter(pk=conversation.pk).update(last_inbound_at=berlin_noon() - timedelta(minutes=1))
        self.assertIsNone(claim_for_account("sina", berlin_noon())[0])
        OutreachConversation.objects.filter(pk=conversation.pk).update(last_outbound_at=berlin_noon())
        video, _ = claim_for_account("sina", berlin_noon())
        self.assertTrue(video.video_only)

    def test_a_first_message_carries_no_video(self) -> None:
        from api.outreach_views import _message_json

        message = self.sent()
        message.status = OutreachStatus.QUEUED
        self.assertEqual(_message_json(message)["video"], "")

    def test_a_video_counts_toward_the_hourly_limit(self) -> None:
        message = self.sent(minutes_ago=20)
        record_video(message.pk, ok=True, now=berlin_noon() - timedelta(minutes=19))
        OutreachAccount.objects.filter(pk=self.account.pk).update(hourly_limit=3)
        self.account.refresh_from_db()
        self.assertEqual(account_gate(self.account, berlin_noon()), "")
        self.assertEqual(account_gate(self.account, berlin_noon(), need=2), "account_hourly_limit")

    def test_no_video_for_someone_who_stopped(self) -> None:
        message = self.sent()
        self.exchange()
        OutreachOptOut.objects.create(telegram_user_id=POSTER_ID)
        self.assertEqual(claim_for_account("sina", berlin_noon())[0], None)
        message.refresh_from_db()
        self.assertEqual(message.video_error, "opted_out")

    def test_a_flood_on_a_video_pauses_the_account(self) -> None:
        message = self.sent()
        record_video(message.pk, ok=False, error="PEER_FLOOD")
        self.account.refresh_from_db()
        self.assertEqual(self.account.pause_reason, "PEER_FLOOD")

    def test_courier_messages_get_no_video(self) -> None:
        self.sent(kind=OutreachKind.C1)
        self.assertEqual(claim_for_account("sina", berlin_noon())[1], "empty")


class DailyNoteTests(VideoTests):
    def test_step_goes_up_only_on_a_clean_day(self) -> None:
        from io import StringIO

        from django.core.management import call_command

        OutreachSettings.objects.filter(pk=1).update(daily_limit=2, sending_enabled=True)
        out = StringIO()
        call_command("outreach_daily", "--step", stdout=out)
        self.assertIn("بدون تغییر", out.getvalue())
        OutreachMessage.objects.create(
            kind=OutreachKind.P1, recipient_username="y_user", recipient_telegram_id=97, text="x", token="ee" * 8,
            status=OutreachStatus.SENT, sent_at=timezone.now(), claimed_at=timezone.now(), account=self.account,
        )
        OutreachMessage.objects.create(
            kind=OutreachKind.P1, recipient_username="x_user", recipient_telegram_id=99, text="x", token="dd" * 8,
            status=OutreachStatus.SENT, sent_at=timezone.now(), claimed_at=timezone.now(), account=self.account,
        )
        out = StringIO()
        call_command("outreach_daily", "--step", stdout=out)
        self.assertIn("از 2 به 5", out.getvalue())
        OutreachOptOut.objects.create(telegram_user_id=98)
        out = StringIO()
        call_command("outreach_daily", "--step", stdout=out)
        self.assertIn("بدون تغییر", out.getvalue())
        self.assertEqual(OutreachSettings.objects.get(pk=1).daily_limit, 5)
