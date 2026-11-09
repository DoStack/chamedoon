"""Operating plan v1: the health check, the daily report, the state log and the model writer."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from ai.openrouter import ChatResult
from outreach import health, writer
from outreach.accounts import STATE, note_state, record_session_invalid
from outreach.models import OutreachAccount, OutreachHealthEvent, OutreachKind, OutreachMessage, OutreachState, OutreachStatus
from tests.test_outreach_housing import HousingOutreachTestCase, berlin_noon


class HealthCheckTests(HousingOutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.now = berlin_noon()
        from outreach.models import OutreachSettings

        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": True})
        self.sina = OutreachAccount.objects.create(name="sina", last_heartbeat_at=self.now, last_sent_at=self.now - timedelta(minutes=5))

    def keys(self) -> list[str]:
        return [incident.key for incident in health.check(self.now)]

    def test_all_quiet_is_no_incident(self) -> None:
        self.assertEqual(self.keys(), [])

    def test_a_silent_worker_is_an_incident_and_asks_for_a_restart(self) -> None:
        OutreachAccount.objects.filter(pk=self.sina.pk).update(last_heartbeat_at=self.now - timedelta(minutes=20))
        [incident] = health.check(self.now)
        self.assertEqual(incident.key, "heartbeat_sina")
        self.assertTrue(incident.restart)

    def test_a_flood_pause_is_reported_as_a_pause_not_a_ban(self) -> None:
        OutreachAccount.objects.filter(pk=self.sina.pk).update(paused_until=self.now + timedelta(hours=3), pause_reason="PEER_FLOOD")
        [incident] = health.check(self.now)
        self.assertIn("بن نیست", incident.facts)
        self.assertNotIn("بن شده", incident.facts)

    def test_banned_only_on_a_real_ban_error(self) -> None:
        with patch("outreach.accounts.tell_mohammad") as told:
            record_session_invalid(self.sina, "AuthKeyDuplicatedError")
        self.assertNotIn("بن شد", told.call_args[0][0])
        self.assertNotIn("بن شده", health.check(self.now)[0].facts)
        other = OutreachAccount.objects.create(name="sara", last_heartbeat_at=self.now)
        with patch("outreach.accounts.tell_mohammad") as told:
            record_session_invalid(other, "UserDeactivatedBanError")
        self.assertIn("بن شد", told.call_args[0][0])

    def test_idle_with_work_in_the_window_is_an_incident_with_the_last_reason(self) -> None:
        self.listing()
        self.built()
        self.assertTrue(OutreachMessage.objects.filter(status=OutreachStatus.QUEUED).exists())
        OutreachAccount.objects.filter(pk=self.sina.pk).update(last_sent_at=self.now - timedelta(minutes=50), next_send_at=None)
        note_state(self.sina, "empty")
        [incident] = health.check(self.now)
        self.assertEqual(incident.key, "idle_sina")
        self.assertIn("«empty»", incident.facts)
        self.assertFalse(incident.restart)

    def test_idle_for_a_logged_reason_is_not_an_incident(self) -> None:
        self.listing()
        self.built()
        OutreachAccount.objects.filter(pk=self.sina.pk).update(last_sent_at=self.now - timedelta(minutes=50), daily_limit=0)
        OutreachAccount.objects.filter(pk=self.sina.pk).update(daily_limit=1)
        OutreachMessage.objects.create(kind=OutreachKind.S2, token="z" * 16, text="x", status=OutreachStatus.SENT, account=self.sina, claimed_at=self.now - timedelta(hours=1))
        self.assertEqual(self.keys(), [])

    def test_a_manual_global_stop_is_reported(self) -> None:
        OutreachState.objects.update_or_create(pk=1, defaults={"stopped": True, "pause_reason": "دستی"})
        self.assertIn("global_stop", self.keys())

    def test_an_incident_is_told_once_an_hour(self) -> None:
        OutreachAccount.objects.filter(pk=self.sina.pk).update(last_heartbeat_at=self.now - timedelta(minutes=20))
        with patch("outreach.health.tell_mohammad") as told:
            first = health.run(self.now)
            second = health.run(self.now + timedelta(minutes=5))
        self.assertEqual((len(first.told), len(second.told)), (1, 0))
        self.assertTrue(first.restart and second.restart)
        self.assertIn("اقدام خودکار: ری‌استارت worker", told.call_args_list[0][0][0])


class DailyReportTests(HousingOutreachTestCase):
    def test_the_report_is_the_db_counts_and_goes_once_after_20_30_toronto(self) -> None:
        now = timezone.now().astimezone(health.TORONTO).replace(hour=20, minute=31, second=0, microsecond=0)
        sina = OutreachAccount.objects.create(name="sina", last_heartbeat_at=now, last_sent_at=now)
        for index in range(3):
            OutreachMessage.objects.create(
                kind=OutreachKind.S2, token=f"r{index:015d}", text="x", status=OutreachStatus.SENT, account=sina, sent_at=now - timedelta(hours=2), claimed_at=now - timedelta(hours=2)
            )
        OutreachMessage.objects.filter(token="r" + "0" * 15).update(replied_at=now - timedelta(hours=1), opened_at=now - timedelta(minutes=30))
        start = now.replace(hour=0, minute=0)
        facts = health.day_facts(sina, start, now)
        self.assertEqual((facts["first"], facts["replied"], facts["opened"]), (3, 1, 1))
        with patch("outreach.health.tell_mohammad") as told:
            result = health.run(now)
            again = health.run(now + timedelta(minutes=5))
        self.assertIn("sina: پیام اول 3", result.report)
        self.assertEqual(again.report, "")
        self.assertEqual(OutreachHealthEvent.objects.filter(key="daily_report").count(), 1)
        told.assert_called_once()

    def test_idle_minutes_come_from_the_state_log(self) -> None:
        sina = OutreachAccount.objects.create(name="sina")
        start = timezone.now() - timedelta(hours=2)
        from outreach.models import OutreachAccountEvent

        for minutes, state in ((0, "outside_window"), (60, "sent"), (70, "account_gap")):
            event = OutreachAccountEvent.objects.create(account=sina, code=STATE, detail=state)
            OutreachAccountEvent.objects.filter(pk=event.pk).update(created_at=start + timedelta(minutes=minutes))
        idle = health.idle_minutes(sina, start, start + timedelta(minutes=100))
        self.assertEqual(idle, {"outside_window": 60, "account_gap": 30})


class StateLogTests(TestCase):
    def test_only_changes_are_logged(self) -> None:
        sina = OutreachAccount.objects.create(name="sina")
        for state in ("empty", "empty", "sent", "account_gap", "account_gap"):
            note_state(sina, state)
        self.assertEqual(list(sina.events.filter(code=STATE).order_by("created_at", "pk").values_list("detail", flat=True)), ["empty", "sent", "account_gap"])


class WriterTests(TestCase):
    LINK = "https://t.me/chamedoon_home_bot?startapp=home_de_berlin_l5_src-o-abc"
    DRAFT = f"آگهی‌تون رو تمدید کردیم، ۳ بار دیده شده:\n{LINK}"

    def says(self, text: str):
        return patch("ai.openrouter.complete", return_value=ChatResult(ok=True, text=text, model="m"))

    def test_the_models_wording_is_used_when_it_keeps_links_and_numbers(self) -> None:
        with self.says(f"آگهی‌تون تمدید شد و تا حالا ۳ بار دیده شده. از اینجا ببینیدش:\n{self.LINK}"):
            self.assertTrue(writer.rewrite(self.DRAFT).startswith("آگهی‌تون تمدید شد"))

    def test_a_changed_link_or_a_new_number_falls_back_to_the_draft(self) -> None:
        with self.says("آگهی‌تون تمدید شد:\nhttps://t.me/other"):
            self.assertEqual(writer.rewrite(self.DRAFT), self.DRAFT)
        with self.says(f"آگهی‌تون ۱۲ بار دیده شده:\n{self.LINK}"):
            self.assertEqual(writer.rewrite(self.DRAFT), self.DRAFT)
        with self.says(f"Your ad was renewed\n{self.LINK}"):
            self.assertEqual(writer.rewrite(self.DRAFT), self.DRAFT)

    def test_the_model_down_sends_the_draft(self) -> None:
        with patch("ai.openrouter.complete", return_value=ChatResult(ok=False, text="", model="m")):
            self.assertEqual(writer.rewrite(self.DRAFT), self.DRAFT)

    def test_emoji_are_removed(self) -> None:
        with self.says(f"آگهی‌تون تمدید شد 🙂 ۳ بار دیده شده\n{self.LINK}"):
            self.assertNotIn("🙂", writer.rewrite(self.DRAFT))


class FloodTests(HousingOutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.now = berlin_noon()
        self.sina = OutreachAccount.objects.create(name="sina", last_heartbeat_at=self.now, last_sent_at=self.now)

    def test_spambot_free_holds_first_messages_one_hour_and_replies_go_on(self) -> None:
        from outreach.accounts import account_gate, record_peer_flood

        with patch("outreach.accounts.tell_mohammad"):
            record_peer_flood(self.sina, self.now, "spambot:free")
        self.sina.refresh_from_db()
        self.assertEqual(self.sina.paused_until, self.now + timedelta(hours=1))
        self.assertEqual(self.sina.pause_reason, "PEER_FLOOD_FREE")
        self.assertTrue(self.sina.enabled)
        self.assertEqual(account_gate(self.sina, self.now + timedelta(minutes=30)), "account_paused")
        self.assertNotEqual(account_gate(self.sina, self.now + timedelta(minutes=61)), "account_paused")
        # A one-hour hold is not an incident; the third in a day is.
        self.assertEqual(health.check(self.now), [])
        with patch("outreach.accounts.tell_mohammad"):
            record_peer_flood(self.sina, self.now, "spambot:free")
            record_peer_flood(self.sina, self.now, "spambot:free")
        self.assertIn("repeat_flood_sina", [incident.key for incident in health.check(self.now)])

    def test_replies_go_on_while_first_messages_are_held(self) -> None:
        from outreach import agent
        from outreach.accounts import record_peer_flood
        from outreach.models import OutreachConversation, OutreachSettings, OutreachTurn, TurnDirection, TurnStatus

        OutreachSettings.objects.update_or_create(pk=1, defaults={"agent_enabled": True})
        with patch("outreach.accounts.tell_mohammad"):
            record_peer_flood(self.sina, timezone.now(), "spambot:free")
        first = OutreachMessage.objects.create(kind=OutreachKind.P1Q, token="h" * 16, text="x", status=OutreachStatus.SENT, account=self.sina, country="DE", city="Berlin", recipient_telegram_id=4242)
        conversation = OutreachConversation.objects.create(telegram_user_id=4242, username="p_x", account=self.sina, first_message=first)
        agent.queue_operator_reply(conversation, "آگهی‌تون رو تمدید کردیم.")
        handed = agent.next_reply("sina")
        self.assertIsNotNone(handed)

    def test_spambot_until_sets_the_pause_and_two_real_limits_switch_off(self) -> None:
        from outreach.accounts import record_peer_flood

        with patch("outreach.accounts.tell_mohammad"):
            record_peer_flood(self.sina, self.now, "spambot:until=2026-11-15T12:00:00+00:00")
        self.sina.refresh_from_db()
        self.assertEqual(self.sina.paused_until.isoformat(), "2026-11-15T12:00:00+00:00")
        with patch("outreach.accounts.tell_mohammad"):
            record_peer_flood(self.sina, self.now, "spambot:free")
        self.assertTrue(OutreachAccount.objects.get(pk=self.sina.pk).enabled)

    def test_a_flooded_message_is_not_handed_to_the_next_account(self) -> None:
        from outreach.services import record_result

        message = OutreachMessage.objects.create(kind=OutreachKind.P1, token="f" * 16, text="x", status=OutreachStatus.SENDING, account=self.sina, claimed_at=self.now)
        with patch("outreach.accounts.tell_mohammad"):
            record_result(message, outcome="retry", error_code="PEER_FLOOD", error_detail="spambot:free")
        message.refresh_from_db()
        self.assertEqual((message.status, message.error_code), (OutreachStatus.SKIPPED, "peer_flood"))

    def test_off_and_long_pauses_are_incidents_short_pauses_are_not(self) -> None:
        OutreachAccount.objects.filter(pk=self.sina.pk).update(paused_until=self.now + timedelta(minutes=30), pause_reason="FLOOD_WAIT")
        self.assertEqual(health.check(self.now), [])
        OutreachAccount.objects.filter(pk=self.sina.pk).update(paused_until=self.now + timedelta(hours=3))
        self.assertEqual([i.key for i in health.check(self.now)], ["paused_sina"])
        OutreachAccount.objects.filter(pk=self.sina.pk).update(enabled=False)
        self.assertEqual([i.key for i in health.check(self.now)], ["disabled_sina"])


class WriterFirstMessageTests(TestCase):
    DRAFT = "سلام وقت بخیر، آگهی خونه‌تون تو برلین رو تو چمدون گذاشتیم، فقط عکس نداره؛ آگهی‌های عکس‌دار معمولاً خیلی بیشتر پیام می‌گیرن.\nاز تیم چمدونم. اگه چند تا عکس همین‌جا بفرستید، خودم اضافه می‌کنم."
    SENT_136 = "آگهی خونه‌تون تو برلین تو چمدون گذاشتیم فقط عکس نداره آگهی‌های عکس‌دار معمولاً خیلی بیشتر پیام می‌گیرن\nاز تیم چمدونم\nاگه چند تا عکس همین‌جا بفرستید خودم اضافه می‌کنم"

    def test_no_greeting_or_no_punctuation_falls_back_to_the_draft(self) -> None:
        self.assertEqual(writer.valid(self.DRAFT, self.SENT_136), "greeting")
        self.assertEqual(writer.valid(self.DRAFT, "سلام " + self.SENT_136), "punctuation")
        with patch("ai.openrouter.complete", return_value=ChatResult(ok=True, text=self.SENT_136, model="m")):
            self.assertEqual(writer.rewrite(self.DRAFT), self.DRAFT)

    def test_a_greeting_with_punctuation_is_used(self) -> None:
        good = "سلام وقت بخیر، آگهی خونه‌تون تو برلین تو چمدون هست، فقط عکس نداره؛ با عکس خیلی بیشتر دیده می‌شه.\nاز تیم چمدونم. اگه عکس دارید همین‌جا بفرستید، خودم اضافه می‌کنم."
        self.assertEqual(writer.valid(self.DRAFT, good), "")


class SelfCheckTests(TestCase):
    def test_a_health_check_that_stopped_running_is_told_once_an_hour(self) -> None:
        now = timezone.now()
        with patch("outreach.health.tell_mohammad") as told:
            health.run(now)
            self.assertTrue(health.self_check(now + timedelta(minutes=10)))
            self.assertFalse(health.self_check(now + timedelta(minutes=20)))
            self.assertFalse(health.self_check(now + timedelta(minutes=25)))
        self.assertEqual(sum("health check" in call.args[0] for call in told.call_args_list), 1)

    def test_never_run_is_stale(self) -> None:
        with patch("outreach.health.tell_mohammad") as told:
            self.assertFalse(health.self_check(timezone.now()))
        told.assert_called_once()


class ProtectMonisTests(HousingOutreachTestCase):
    """Mohammad, 2026-11-09: monis at most 5 first messages a day, newest posts only, any flood holds to next morning."""

    def setUp(self) -> None:
        super().setUp()
        self.now = berlin_noon()
        self.monis = OutreachAccount.objects.create(name="monis", first_daily_cap=5, fresh_hours=24, flood_hold_next_morning=True)

    def test_five_first_messages_a_day(self) -> None:
        from outreach.accounts import account_gate

        for index in range(5):
            OutreachMessage.objects.create(kind=OutreachKind.P1, token=f"m{index:015d}", text="x", status=OutreachStatus.SENT, account=self.monis, claimed_at=self.now - timedelta(hours=3), replied_at=self.now)
        self.assertEqual(account_gate(self.monis, self.now), "account_first_cap")
        self.assertEqual(account_gate(self.monis, self.now, video=True), "")

    def test_any_flood_holds_until_ten_the_next_morning_toronto(self) -> None:
        from outreach.accounts import record_peer_flood

        with patch("outreach.accounts.tell_mohammad"):
            record_peer_flood(self.monis, self.now, "spambot:free")
        self.monis.refresh_from_db()
        local = self.monis.paused_until.astimezone(health.TORONTO)
        self.assertEqual((local.hour, local.minute), (10, 0))
        self.assertEqual(local.date(), (self.now.astimezone(health.TORONTO) + timedelta(days=1)).date())

    def test_only_posts_from_the_last_24_hours(self) -> None:
        from outreach.accounts import claim_for_account
        from outreach.models import OutreachSettings

        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": True})
        self.listing(days_ago=2)
        self.built()
        message, reason = claim_for_account("monis", self.now)
        self.assertIsNone(message)
        from tests.test_outreach_housing import person_post_fields

        self.listing(days_ago=0.2, poster=person_post_fields(user_id=4321, username="fresh_x", name="Fresh"))
        self.built()
        message, reason = claim_for_account("monis", self.now)
        self.assertIsNotNone(message)
