from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from ai.openrouter import ChatResult
from outreach import guard, kindcheck
from outreach.models import OutreachKind
from tests.test_outreach_agent import AgentTestCase
from tests.test_outreach_housing import HousingOutreachTestCase, berlin_noon, person_post_fields

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


class AuditRuleTests(AgentTestCase):
    def test_rule_19_names_that_are_not_first_names(self) -> None:
        from outreach import templates

        for name in ("Legend", "Just", "Ma", "Mo", "Mir", "Mac", "HJ", "Myprofile", "Ms", "Sa", "Du", "Sh", "Boss", "Green", "HMD", "SZR", "jockr"):
            self.assertEqual(templates.valid_name(name), "", name)
        for name in ("Reza", "Ali", "Maryam", "مریم"):
            self.assertEqual(templates.valid_name(name), name)

    def test_rule_17_no_deposit_in_quebec_or_on_a_lease_transfer(self) -> None:
        from outreach import completion

        self.assertTrue(completion.deposit_applies(self.ad))
        self.ad.country = "CA"
        with patch("outreach.completion.metro_city", return_value="Montreal"):
            self.assertFalse(completion.deposit_applies(self.ad))
            self.assertNotIn("deposit", completion.gaps(self.ad))
        self.ad.country = "DE"
        self.ad.post.text = "#انتقال_لیز آپارتمان دو خوابه"
        self.ad.post.save(update_fields=["text"])
        self.assertFalse(completion.deposit_applies(self.ad))

    def test_rule_18_no_proactive_message_on_top_of_our_unanswered_one(self) -> None:
        from outreach import agent, followups
        from outreach.models import OutreachConversation

        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        self.assertTrue(followups.answered_last(conversation))
        turn = agent.queue_operator_reply(conversation, "آگهی‌تون رو تمدید کردیم.")
        agent.reply_sent(turn.pk, ok=True, telegram_message_id=900)
        self.assertFalse(followups.answered_last(conversation))

    def test_rule_18_p5_never_introduces_us_again(self) -> None:
        import random

        from outreach import templates

        text, _variant = templates.p5(random.Random(1), "Reza", "Toronto")
        self.assertNotIn("از تیم چمدون", text)
        self.assertTrue(text.startswith("سلام Reza جان،"))

    def test_rule_20_a_past_move_in_date_is_left_out(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from outreach import housing

        self.ad.available_from = timezone.localdate() - timedelta(days=1)
        self.assertNotIn("خالیه", housing.teaser(self.ad))
        self.ad.available_from = timezone.localdate() + timedelta(days=5)
        self.assertIn("خالیه", housing.teaser(self.ad))


class WatchdogTests(AgentTestCase):
    def test_a_quiet_account_is_stale_and_a_fresh_one_is_not(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from outreach.management.commands.outreach_watchdog import stale_accounts
        from outreach.models import OutreachAccount

        now = timezone.now()
        OutreachAccount.objects.update(enabled=True, session_ok=True, last_heartbeat_at=now)
        self.assertEqual(stale_accounts(now), [])
        account = OutreachAccount.objects.order_by("name").first()
        OutreachAccount.objects.filter(pk=account.pk).update(last_heartbeat_at=now - timedelta(minutes=16))
        self.assertEqual([a.name for a in stale_accounts(now)], [account.name])


class SmallNumberTests(AgentTestCase):
    def test_a_count_under_three_is_blocked_and_facts_are_not(self) -> None:
        with self.assertRaises(guard.Blocked) as caught:
            guard.check("آگهی‌تون این هفته ۳ بازدید داشت و ۲ نفر اطلاعات تماستون رو گرفتن")
        self.assertEqual(caught.exception.reason, "small_number")
        for text in ("این هفته ۸۶ تا آگهی خونه‌ی برلین", "آگهی ۱ خوابه‌تون (۱۵۷۰ دلار)", "۱۲ نفر دنبال خونه‌اند"):
            self.assertTrue(guard.check(text), text)

    def test_an_operator_text_with_two_people_is_not_sent(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        written = agent.queue_operator_reply(conversation, "آگهی‌تون این هفته ۳ بازدید داشت و ۲ نفر اطلاعات تماستون رو گرفتن.")
        with patch("outreach.agent.tell_mohammad"):
            self.assertIsNone(agent.next_reply("sina"))
        written.refresh_from_db()
        self.assertEqual(written.action_detail["blocked"], "small_number")


class MediaAckTests(AgentTestCase):
    def test_a_held_photo_gets_a_short_ack(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation, OutreachTurn

        self.say("سلام")
        handed = self.answer()
        if handed:
            agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=900)
        conversation = OutreachConversation.objects.get()
        conversation.awaiting = {"listing": self.ad.pk, "media": "video"}
        conversation.save(update_fields=["awaiting"])
        found = agent.record_inbound("sina", self.message.recipient_telegram_id, "sara_b", 5, "", True)
        OutreachTurn.objects.filter(pk=found.turn.pk).update(reply_due_at=timezone.now() - timedelta(seconds=1), action_detail={"photo_held": "unsure"})
        with patch("outreach.agent.transcript"):
            handed = self.answer()
        turn = OutreachTurn.objects.get(pk=handed["turn_id"])
        self.assertEqual((turn.action, turn.text), ("media_ack", "عکس رسید، ممنون. به آگهی‌تون اضافه‌ش می‌کنم."))
        conversation.refresh_from_db()
        self.assertFalse(conversation.awaiting)

    def _media(self, message_id: int, *, video: bool = False):
        from outreach import agent
        from outreach.models import OutreachTurn

        found = agent.record_inbound("sina", self.message.recipient_telegram_id, "sara_b", message_id, "", not video, has_video=video)
        OutreachTurn.objects.filter(pk=found.turn.pk).update(reply_due_at=timezone.now() - timedelta(seconds=1))
        return found.turn

    def _opened(self):
        from outreach import agent

        self.say("سلام")
        handed = self.answer()
        if handed:
            agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=900)

    def test_an_album_gets_one_plural_ack(self) -> None:
        from outreach.models import OutreachTurn, TurnDirection

        self._opened()
        for message_id in (11, 12, 13):
            self._media(message_id)
        with patch("outreach.agent.transcript"):
            handed = self.answer()
            self.assertEqual(handed["text"], "عکس‌ها رسید، ممنون. به آگهی‌تون اضافه‌شون می‌کنم.")
            self.assertIsNone(self.answer())
        acks = OutreachTurn.objects.filter(direction=TurnDirection.OUT, action="media_ack")
        self.assertEqual(acks.count(), 1)

    def test_a_video_is_called_a_video(self) -> None:
        self._opened()
        self._media(21, video=True)
        with patch("outreach.agent.transcript"):
            handed = self.answer()
        self.assertEqual(handed["text"], "ویدیو رسید، ممنون. به آگهی‌تون اضافه‌ش می‌کنم.")


class OnReplyTests(AgentTestCase):
    """A correction waits for the person's first reply (rule 18), recounted then with the link's own filters."""

    TEXT = "ببخشید، الان تو نویکلن {n} تا اتاق هست، ارزون‌ترینش {min} یورو:\n{link}"
    SMALL = "ببخشید، اتاق‌های نویکلن اینجاست:\n{link}"

    def _wait(self, **spec) -> None:
        self.message.extra = {"on_reply": {"text": self.TEXT, "text_small": self.SMALL, **spec}}
        self.message.save(update_fields=["extra"])

    def _rooms(self, count: int) -> None:
        from home.models import HomeListing

        HomeListing.objects.filter(pk=self.ad.pk).update(status="rented")
        rooms = [
            self.listing(area="Neukölln", price_amount=500 + price * 10, property_type="room", poster=person_post_fields(user_id=900 + price, username=f"p{price}x", name="Other"))
            for price in range(count)
        ]
        # Review would merge these look-alike test ads; they are different rooms here.
        HomeListing.objects.filter(pk__in=[room.pk for room in rooms]).update(status="active")

    def test_the_correction_answers_their_first_reply_with_a_fresh_count(self) -> None:
        from outreach.models import OutreachTurn

        self._rooms(3)
        self._wait(filters={"country": "DE", "city": "Berlin", "type": "room", "area": ["Neukölln"], "max": 900})
        self.say("سلام، بله")
        turn = OutreachTurn.objects.get(action="operator")
        self.assertIn("۳ تا اتاق", turn.text)
        self.assertIn("۵۰۰ یورو", turn.text)
        self.assertIn("startapp=home_q", turn.text)
        self.message.refresh_from_db()
        self.assertTrue(self.message.extra["on_reply"]["done"])

    def test_under_three_drops_the_count(self) -> None:
        from outreach.models import OutreachTurn

        self._rooms(2)
        self._wait(filters={"country": "DE", "city": "Berlin", "type": "room", "area": ["Neukölln"], "max": 900})
        self.say("بله")
        self.assertTrue(OutreachTurn.objects.get(action="operator").text.startswith("ببخشید، اتاق‌های نویکلن اینجاست:"))

    def test_a_named_listing_that_is_gone_sends_nothing(self) -> None:
        from home.models import HomeListing
        from outreach.models import OutreachTurn

        HomeListing.objects.filter(pk=self.ad.pk).update(status="rented")
        self.message.extra = {"on_reply": {"text": "یه اتاق هست.", "listing": self.ad.pk}}
        self.message.save(update_fields=["extra"])
        with patch("outreach.onreply.tell_mohammad") as told:
            self.say("بله")
        self.assertFalse(OutreachTurn.objects.filter(action="operator").exists())
        told.assert_called_once()


class SeekerClaimTests(HousingOutreachTestCase):
    """Audit 2026-11-08 (#126, #127): «nothing fits» and the area are checked before a seeker message goes."""

    def setUp(self) -> None:
        super().setUp()
        from outreach.models import OutreachSettings

        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": True})

    def test_s2_turns_into_the_budget_question_when_something_fits_now(self) -> None:
        from outreach.accounts import claim_for_account
        from outreach.models import OutreachMessage

        self.listing(poster=person_post_fields(user_id=8, username="nima_s", name="Nima"), kind="wanted", price_amount=None, price_max=None, area="Berlin")
        self.built()
        self.assertEqual(OutreachMessage.objects.get(recipient_username="nima_s").kind, OutreachKind.S2)
        self.listing(poster=person_post_fields(user_id=7, username="owner_x", name="Owner"))
        OutreachMessage.objects.filter(recipient_username="owner_x").delete()
        message, _reason = claim_for_account("sina", berlin_noon())
        self.assertEqual(message.kind, OutreachKind.S1B)
        self.assertIn("بودجه", message.text)

    def test_an_area_the_post_does_not_name_is_not_sent(self) -> None:
        from home.models import HomeListing
        from outreach.accounts import claim_for_account
        from outreach.models import OutreachMessage

        for number in range(3):
            self.listing(poster=person_post_fields(user_id=70 + number, username=f"owner_{number}", name="Owner"))
        wanted = self.listing(poster=person_post_fields(user_id=8, username="maryam_s", name="مریم"), kind="wanted", price_amount=None, price_max=700, area="Berlin")
        self.built()
        OutreachMessage.objects.exclude(recipient_username="maryam_s").delete()
        self.assertEqual(OutreachMessage.objects.get().kind, OutreachKind.S1)
        with patch("outreach.kindcheck.area_ok", return_value=False):
            self.assertIsNone(claim_for_account("sina", berlin_noon())[0])
        self.assertEqual(OutreachMessage.objects.get().error_code, "area_mismatch")
        self.assertTrue(HomeListing.objects.get(pk=wanted.pk).needs_review)
