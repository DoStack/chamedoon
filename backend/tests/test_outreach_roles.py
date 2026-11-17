"""PEER-FLOOD F2 and F3 (Mohammad, 2026-11-17: "Both F2 and F3"): a flood ends an account's first messages for the day;
only monis sends cold first messages; the fresh accounts answer chats until 7 clean days give them first messages back."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from outreach import accounts
from outreach.accounts import CLEAN_DAYS, claim_for_account, end_of_local_day, first_dm_from, record_flood_wait, record_peer_flood, reply_only
from outreach.models import OutreachAccount, OutreachAccountEvent, OutreachMessage, OutreachStatus
from tests.test_outreach_housing import HousingOutreachTestCase, berlin_noon


class FloodEndsTheDayTests(HousingOutreachTestCase):
    def test_a_free_peer_flood_holds_first_messages_until_the_end_of_the_day(self) -> None:
        now = berlin_noon()
        monis = OutreachAccount.objects.create(name="monis")
        record_peer_flood(monis, now, "spambot:free")
        monis.refresh_from_db()
        self.assertGreaterEqual(monis.paused_until, end_of_local_day(now))
        self.assertGreater(monis.paused_until, now + timedelta(hours=1))
        self.assertTrue(monis.enabled)

    def test_a_flood_wait_on_a_first_message_ends_the_day_and_on_a_reply_only_its_seconds(self) -> None:
        now = berlin_noon()
        monis = OutreachAccount.objects.create(name="monis")
        record_flood_wait(monis, 30, now)
        monis.refresh_from_db()
        self.assertEqual(monis.paused_until, now + timedelta(seconds=30))
        record_flood_wait(monis, 30, now, first_dm=True)
        monis.refresh_from_db()
        self.assertEqual(monis.paused_until, end_of_local_day(now))

    def test_two_real_floods_in_seven_days_still_switch_the_account_off(self) -> None:
        now = berlin_noon()
        monis = OutreachAccount.objects.create(name="monis")
        record_peer_flood(monis, now - timedelta(days=3), "limited")
        record_peer_flood(monis, now, "limited")
        monis.refresh_from_db()
        self.assertFalse(monis.enabled)


class AccountRoleTests(HousingOutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        from outreach.models import OutreachSettings

        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": True})

    def test_a_fresh_account_is_reply_only_for_seven_days_after_a_flood_and_then_comes_back(self) -> None:
        now = berlin_noon()
        niloofar = OutreachAccount.objects.create(name="niloofar", first_daily_cap=2)
        monis = OutreachAccount.objects.create(name="monis", first_daily_cap=5)
        accounts._log(niloofar, "PEER_FLOOD", "limited")
        accounts._log(monis, "PEER_FLOOD", "limited")
        self.assertTrue(reply_only(niloofar, now))
        self.assertFalse(reply_only(monis, now))
        self.assertIsNone(first_dm_from(monis))
        flood_at = niloofar.events.get(code="PEER_FLOOD").created_at
        self.assertEqual(first_dm_from(niloofar), flood_at + CLEAN_DAYS)
        self.assertFalse(reply_only(niloofar, flood_at + CLEAN_DAYS + timedelta(minutes=1)))

    def test_a_reply_only_account_claims_no_cold_message_and_monis_takes_it(self) -> None:
        self.listing()
        self.built()
        now = berlin_noon()
        niloofar = OutreachAccount.objects.create(name="niloofar", first_daily_cap=2)
        accounts._log(niloofar, "PEER_FLOOD", "limited")
        self.assertEqual(claim_for_account("niloofar", now), (None, "empty"))
        self.assertEqual(OutreachMessage.objects.get().status, OutreachStatus.QUEUED)
        self.assertEqual(list(niloofar.events.filter(code="ROLE").values_list("detail", flat=True)), ["reply_only"])
        message, reason = claim_for_account("monis", now)
        self.assertEqual((reason, message.status), ("", OutreachStatus.SENDING))

    def test_the_role_flip_back_is_logged(self) -> None:
        self.listing()
        self.built()
        now = berlin_noon()
        niloofar = OutreachAccount.objects.create(name="niloofar", first_daily_cap=2)
        accounts._log(niloofar, "PEER_FLOOD", "limited")
        claim_for_account("niloofar", now)
        OutreachAccountEvent.objects.filter(account=niloofar, code="PEER_FLOOD").update(created_at=now - CLEAN_DAYS - timedelta(hours=1))
        message, reason = claim_for_account("niloofar", now)
        self.assertEqual(reason, "")
        self.assertEqual(list(niloofar.events.filter(code="ROLE").order_by("created_at").values_list("detail", flat=True)), ["reply_only", "first_dm"])

    def test_a_fresh_account_with_first_messages_back_keeps_its_cap_of_two(self) -> None:
        for _ in range(3):
            self.listing(poster={"poster_user_id": 9000 + self.message_id, "poster_username": f"p_{self.message_id}", "poster_name": "P", "poster_kind": "person", "poster_via": "sender", "poster_read_at": timezone.now()})
        self.built()
        now = berlin_noon()
        OutreachAccount.objects.create(name="sara", first_daily_cap=2)
        sent = 0
        for step in range(3):
            message, reason = claim_for_account("sara", now + timedelta(hours=step))
            if message is not None:
                sent += 1
                OutreachMessage.objects.filter(pk=message.pk).update(status=OutreachStatus.SENT)
        self.assertEqual(sent, 2)
