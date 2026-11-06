from __future__ import annotations

from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from outreach.accounts import account_gate, claim_for_account
from outreach.models import OutreachAccount, OutreachKind, OutreachMessage, OutreachOptOut, OutreachSettings, OutreachStatus
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

    def test_a_message_sent_before_videos_gets_its_video_once(self) -> None:
        message = self.sent()
        video, reason = claim_for_account("sina", berlin_noon())
        self.assertEqual((video.pk, reason, video.video_only), (message.pk, "", True))
        record_video(message.pk, ok=True)
        message.refresh_from_db()
        self.assertIsNotNone(message.video_sent_at)
        OutreachAccount.objects.filter(pk=self.account.pk).update(next_send_at=None, last_sent_at=None)
        again, _ = claim_for_account("sina", berlin_noon() + timedelta(minutes=20))
        self.assertTrue(again is None or not getattr(again, "video_only", False))

    def test_a_video_counts_toward_the_hourly_limit(self) -> None:
        message = self.sent(minutes_ago=20)
        record_video(message.pk, ok=True, now=berlin_noon() - timedelta(minutes=19))
        OutreachAccount.objects.filter(pk=self.account.pk).update(hourly_limit=3)
        self.account.refresh_from_db()
        self.assertEqual(account_gate(self.account, berlin_noon()), "")
        self.assertEqual(account_gate(self.account, berlin_noon(), need=2), "account_hourly_limit")

    def test_no_video_for_someone_who_stopped(self) -> None:
        message = self.sent()
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

        OutreachSettings.objects.filter(pk=1).update(daily_limit=10, sending_enabled=True)
        OutreachMessage.objects.create(
            kind=OutreachKind.P1, recipient_username="x_user", recipient_telegram_id=99, text="x", token="dd" * 8,
            status=OutreachStatus.SENT, sent_at=timezone.now(), claimed_at=timezone.now(), account=self.account,
        )
        out = StringIO()
        call_command("outreach_daily", "--step", stdout=out)
        self.assertIn("از 10 به 30", out.getvalue())
        OutreachOptOut.objects.create(telegram_user_id=98)
        out = StringIO()
        call_command("outreach_daily", "--step", stdout=out)
        self.assertIn("بدون تغییر", out.getvalue())
        self.assertEqual(OutreachSettings.objects.get(pk=1).daily_limit, 30)
