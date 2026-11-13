from __future__ import annotations

import re
from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from ai.openrouter import ChatResult
from outreach import guard, kindcheck
from outreach.models import OutreachKind, OutreachTurn, TurnDirection
from tests.test_outreach_agent import AgentTestCase, model_says
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
        handed = agent.next_reply("niloofar")
        agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=700)
        agent.queue_operator_reply(conversation, "آگهی‌تون رو  تمدید کردیم.")
        with patch("outreach.agent.tell_mohammad"):
            self.assertIsNone(agent.next_reply("niloofar"))
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
            self.assertIsNone(agent.next_reply("niloofar"))
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

    def _reads(self, text: str):
        return patch("ai.openrouter.complete", return_value=ChatResult(ok=True, text=text, model="m"))

    def test_a_post_with_several_towns_or_doubt_gets_no_first_message(self) -> None:
        self.message.kind = OutreachKind.S1
        with self._reads('{"kind": "wanted", "places": 3, "sure": true}'):
            self.assertTrue(kindcheck.agrees(self.message))
        self.assertFalse(kindcheck.clear(self.message))
        self.message.extra = {}
        with self._reads('{"kind": "wanted", "places": 1, "sure": false}'):
            kindcheck.agrees(self.message)
        self.assertFalse(kindcheck.clear(self.message))
        self.message.extra = {}
        with self._reads('{"kind": "wanted", "places": 1, "sure": true}'):
            kindcheck.agrees(self.message)
        self.assertTrue(kindcheck.clear(self.message))

    def test_an_unclear_post_is_skipped_at_claim_and_the_listing_left_alone(self) -> None:
        from outreach.accounts import claim_for_account
        from outreach.models import OutreachMessage, OutreachStatus
        from tests.test_outreach_housing import berlin_noon

        OutreachMessage.objects.filter(pk=self.message.pk).update(status=OutreachStatus.QUEUED, sent_at=None, kind=OutreachKind.P1Q)
        with self._reads('{"kind": "offer", "places": 2, "sure": true}'), patch("outreach.kindcheck.area_ok", return_value=True):
            self.assertIsNone(claim_for_account("niloofar", berlin_noon())[0])
        self.message.refresh_from_db()
        self.assertEqual(self.message.error_code, "unclear_post")
        self.assertIn("places=2", self.message.error_detail)
        self.assertEqual(self.message.extra["post_places"], 2)
        self.ad.refresh_from_db()
        self.assertFalse(self.ad.needs_review)

    def test_the_group_metro_hashtag_is_not_a_second_place(self) -> None:
        # #342, post 111381: «#نورث_ونکوور», «#اجاره_ونکوور», «#VancouverRent», «نورث ونکوور(بلوریج)».
        self.message.kind = OutreachKind.P1
        self.ad.city, self.ad.country = "Vancouver", "CA"
        reply = '{"kind": "offer", "places": 2, "names": ["نورث ونکوور(بلوریج)", "#اجاره_ونکوور"], "sure": true, "housing": true}'
        with self._reads(reply):
            self.assertTrue(kindcheck.agrees(self.message))
        self.assertEqual(self.message.extra["post_places"], 1)
        self.assertEqual(self.message.extra["post_metro_tags"], ["#اجاره_ونکوور"])
        self.assertTrue(kindcheck.clear(self.message))
        self.message.extra = {}
        with self._reads('{"kind": "offer", "places": 2, "names": ["North Vancouver", "#VancouverRent"], "sure": true}'):
            kindcheck.agrees(self.message)
        self.assertTrue(kindcheck.clear(self.message))

    def test_two_real_towns_stay_unclear_and_a_lone_metro_tag_is_kept(self) -> None:
        self.message.kind = OutreachKind.P1
        self.ad.city, self.ad.country = "Vancouver", "CA"
        with self._reads('{"kind": "offer", "places": 2, "names": ["برنابی", "کوکیتلام"], "sure": true}'):
            kindcheck.agrees(self.message)
        self.assertFalse(kindcheck.clear(self.message))
        self.message.extra = {}
        with self._reads('{"kind": "offer", "places": 3, "names": ["برنابی", "کوکیتلام", "#اجاره_ونکوور"], "sure": true}'):
            kindcheck.agrees(self.message)
        self.assertEqual(self.message.extra["post_places"], 2)
        self.assertFalse(kindcheck.clear(self.message))
        self.message.extra = {}
        with self._reads('{"kind": "offer", "places": 1, "names": ["#اجاره_ونکوور"], "sure": true}'):
            kindcheck.agrees(self.message)
        self.assertEqual(self.message.extra["post_places"], 1)
        self.assertNotIn("post_metro_tags", self.message.extra)

    def test_a_skip_at_claim_writes_its_facts(self) -> None:
        from outreach.accounts import claim_for_account
        from outreach.models import OutreachMessage, OutreachStatus
        from tests.test_outreach_housing import berlin_noon

        OutreachMessage.objects.filter(pk=self.message.pk).update(status=OutreachStatus.QUEUED, sent_at=None, kind=OutreachKind.P1Q)
        with self._reads('{"kind": "wanted", "places": 1, "sure": true}'), patch("outreach.kindcheck.area_ok", return_value=True):
            self.assertIsNone(claim_for_account("niloofar", berlin_noon())[0])
        self.message.refresh_from_db()
        self.assertEqual(self.message.error_code, "kind_mismatch")
        self.assertIn("wanted", self.message.error_detail)
        self.assertEqual(self.message.extra["kind_checked"], "wanted")

    def test_a_mismatch_sends_the_listing_to_review(self) -> None:
        kindcheck.flag(self.message)
        self.ad.refresh_from_db()
        self.assertTrue(self.ad.needs_review)
        self.assertEqual(self.message.extra["stored_kind"], self.ad.kind)


class ReplyWindowTests(AgentTestCase):
    def test_a_reply_never_waits_for_the_window(self) -> None:
        from datetime import datetime, timezone as tz

        from outreach import agent
        from outreach.models import OutreachConversation

        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        agent.queue_operator_reply(conversation, "آگهی‌تون رو تمدید کردیم.")
        city = conversation.first_message.city
        night = datetime(2026, 11, 6, 7, 0, tzinfo=tz.utc) if city in {"Toronto", "Montreal", "Vancouver"} else datetime(2026, 11, 6, 22, 0, tzinfo=tz.utc)
        self.assertFalse(agent.in_window(conversation, night))
        # Someone who wrote to us is answered at once, day or night (Mohammad, 2026-11-09).
        self.assertIsNotNone(agent.next_reply("niloofar", night))


class AuditRuleTests(AgentTestCase):
    def test_rule_19_names_that_are_not_first_names(self) -> None:
        from outreach import templates

        for name in ("Legend", "Just", "Ma", "Mo", "Mir", "Mac", "HJ", "Myprofile", "Ms", "Sa", "Du", "Sh", "Boss", "Green", "HMD", "SZR", "jockr"):
            self.assertEqual(templates.valid_name(name), "", name)
        # 12 or more Latin letters: first and last name joined, e.g. «Farimirshamsi» (audit 2026-11-08, #109).
        self.assertEqual(templates.valid_name("Farimirshamsi"), "")
        for name in ("Reza", "Ali", "Maryam", "Christopher", "مریم"):
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
            self.assertIsNone(agent.next_reply("niloofar"))
        written.refresh_from_db()
        self.assertEqual(written.action_detail["blocked"], "small_number")


class MediaAckTests(AgentTestCase):
    def test_a_held_photo_gets_a_short_ack(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation, OutreachTurn

        self.say("سلام")
        from tests.test_outreach_agent import model_says

        with model_says(intent="hello"):
            handed = self.answer()
        if handed:
            agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=900)
        conversation = OutreachConversation.objects.get()
        conversation.awaiting = {"listing": self.ad.pk, "media": "video"}
        conversation.save(update_fields=["awaiting"])
        found = agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 5, "", True)
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

        found = agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", message_id, "", not video, has_video=video)
        OutreachTurn.objects.filter(pk=found.turn.pk).update(reply_due_at=timezone.now() - timedelta(seconds=1))
        return found.turn

    def _opened(self):
        from outreach import agent

        self.say("سلام")
        from tests.test_outreach_agent import model_says

        with model_says(intent="hello"):
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
        from tests.test_outreach_agent import model_says

        with model_says(intent="available"):
            self.answer()
        turn = OutreachTurn.objects.get(action="correction", direction="out")
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
        from tests.test_outreach_agent import model_says

        with model_says(intent="available"):
            self.answer()
        self.assertTrue(OutreachTurn.objects.get(action="correction", direction="out").text.startswith("ببخشید، اتاق‌های نویکلن اینجاست:"))

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
        message, _reason = claim_for_account("niloofar", berlin_noon())
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
            self.assertIsNone(claim_for_account("niloofar", berlin_noon())[0])
        self.assertEqual(OutreachMessage.objects.get().error_code, "area_mismatch")
        self.assertFalse(HomeListing.objects.get(pk=wanted.pk).needs_review)  # skipped, no work on the listing


class KindCheckAuditTests(AgentTestCase):
    """Audit 2026-11-08: not a housing lead, cancelled corrections, repost claim."""

    def test_a_registration_address_only_post_is_not_housing(self) -> None:
        from home.models import HomePost
        from outreach.models import OutreachMessage

        wanted = self.listing(kind="wanted", price_amount=None, price_max=None, area="Berlin", poster=person_post_fields(user_id=91, username="sp_x", name="Sp"))
        HomePost.objects.filter(pk=wanted.post_id).update(text="ملده بدون سکونت برلین نیازه دارم ۲ ۳ ماه")
        wanted.refresh_from_db()
        message = OutreachMessage(kind=OutreachKind.S2, listing=wanted, extra={})
        self.assertTrue(kindcheck.not_housing(message))
        HomePost.objects.filter(pk=wanted.post_id).update(text="دنبال اتاق در برلین هستم با امکان ملده، تا ۶۰۰ یورو")
        wanted.refresh_from_db()
        self.assertFalse(kindcheck.not_housing(OutreachMessage(kind=OutreachKind.S2, listing=wanted, extra={})))
        # The model can say it too.
        self.assertTrue(kindcheck.not_housing(OutreachMessage(kind=OutreachKind.S2, listing=wanted, extra={"post_housing": False})))

    def test_registration_ask_without_a_home_word_is_not_housing(self) -> None:
        from home.models import HomeListing, HomePost
        from outreach import housing
        from outreach.models import OutreachMessage

        wanted = self.listing(kind="wanted", price_amount=None, price_max=100, area="", poster=person_post_fields(user_id=92, username="mj_x", name="Mj"))
        HomeListing.objects.filter(pk=wanted.pk).update(country="DE", city="Berlin")
        # #315, word for word.
        HomePost.objects.filter(pk=wanted.post_id).update(text="سلام کسی ملده برای ۳ تا ۴ ماه با ۷۰ تا ۱۰۰ یورو داره؟ لطفا پیام بدین اگه جایی دارین")
        wanted.refresh_from_db()
        self.assertTrue(kindcheck.not_housing(OutreachMessage(kind=OutreachKind.S1B, listing=wanted, extra={})))
        # A low German budget alone, with no room named, is not housing either.
        HomePost.objects.filter(pk=wanted.post_id).update(text="کسی جایی تو برلین داره؟ تا ۱۰۰ یورو")
        wanted.refresh_from_db()
        self.assertTrue(kindcheck.not_housing(OutreachMessage(kind=OutreachKind.S1B, listing=wanted, extra={})))
        # A room with registration and a real budget is housing.
        HomeListing.objects.filter(pk=wanted.pk).update(price_max=600)
        HomePost.objects.filter(pk=wanted.post_id).update(text="دنبال اتاق در برلین با Anmeldung هستم تا ۶۰۰ یورو")
        wanted.refresh_from_db()
        self.assertFalse(kindcheck.not_housing(OutreachMessage(kind=OutreachKind.S1B, listing=wanted, extra={})))
        # Once marked, the listing stays out of the seeker counts and every later check.
        before = housing.week_seekers("DE", "Berlin", timezone.now())
        kindcheck.mark_not_housing(wanted)
        self.assertTrue(kindcheck.not_housing(OutreachMessage(kind=OutreachKind.S1B, listing=wanted, extra={})))
        self.assertEqual(housing.week_seekers("DE", "Berlin", timezone.now()), max(before - 1, 0))

    def test_a_cancelled_correction_never_goes(self) -> None:
        from outreach.models import OutreachTurn

        self.message.extra = {"on_reply": {"text": "یه اتاق هست.", "cancelled": "Mohammad"}}
        self.message.save(update_fields=["extra"])
        self.say("بله")
        self.assertFalse(OutreachTurn.objects.filter(action="operator").exists())

    def test_repost_counts_only_this_ad_in_fourteen_days(self) -> None:
        from home.models import HomePost
        from outreach import housing

        now = timezone.now()
        HomePost.objects.filter(pk=self.ad.post_id).update(text_key="same-ad")
        others = [self.listing(poster=person_post_fields()) for _ in range(4)]
        # Four other ads of the same person: not this ad, so no repost.
        self.assertFalse(housing._reposter(self.message.recipient_telegram_id or 5551001, now, self.ad))
        HomePost.objects.filter(pk__in=[item.post_id for item in others]).update(text_key="same-ad")
        # One post a day: one cross-post burst would count once (rule 68).
        for days, item in enumerate(others, start=2):
            HomePost.objects.filter(pk=item.post_id).update(posted_at=now - timedelta(days=days))
        self.assertTrue(housing._reposter(5551001, now, self.ad))
        HomePost.objects.filter(pk__in=[item.post_id for item in others]).update(posted_at=now - timedelta(days=20))
        self.assertFalse(housing._reposter(5551001, now, self.ad))


class KindCheckClaimTests(HousingOutreachTestCase):
    """2026-11-08: the post check's answer was cut at 20 tokens, every row read as model-down, the claim timed out."""

    def setUp(self) -> None:
        super().setUp()
        from outreach.models import OutreachSettings

        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": True})

    def test_the_full_answer_fits_and_model_down_stops_the_claim_at_once(self) -> None:
        from outreach.accounts import claim_for_account

        for number in range(3):
            self.listing(poster=person_post_fields(user_id=80 + number, username=f"own_{number}x", name="Owner"))
        self.built()
        with patch("ai.openrouter.complete", return_value=ChatResult(ok=False, text="", model="m")) as model:
            self.assertEqual(claim_for_account("niloofar", berlin_noon()), (None, "model_down"))
        self.assertEqual(model.call_count, 1)
        answer = '{"kind": "offer", "places": 1, "sure": true, "housing": true}'
        with patch("ai.openrouter.complete", return_value=ChatResult(ok=True, text=answer, model="m")) as model:
            message, reason = claim_for_account("niloofar", berlin_noon() + timedelta(minutes=30))
        self.assertEqual(reason, "")
        self.assertGreaterEqual(model.call_args.kwargs["max_tokens"], 100)


class Rule29Tests(AgentTestCase):
    """Audit 2026-11-08, conv 12: area filters stay, one link, no repeated thanks, a stored correction answers next."""

    FILTERS = {"country": "DE", "city": "Berlin", "type": "room", "area": ["Neukölln"], "max": 900}

    def _with_filters(self) -> None:
        self.message.kind = OutreachKind.S2
        self.message.extra = {"on_reply": {"text": "x", "done": True, "filters": self.FILTERS}}
        self.message.save(update_fields=["kind", "extra"])

    def test_a_later_help_link_keeps_the_chats_area_filters_and_is_the_only_link(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        self._with_filters()
        self.say("سلام")
        self.answer()
        conversation = OutreachConversation.objects.get()
        reply = agent._seeker_help(conversation, __import__("random").Random(1))
        reply = agent._one_link(conversation, reply)
        self.assertIn("startapp=home_q", reply)
        self.assertIn(f"_src-o-{self.message.token}", reply)
        self.assertNotIn("p0-", reply)
        self.assertEqual(reply.count("https://"), 1)

    def test_no_thanks_for_replying_after_the_first_exchange(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation, OutreachTurn, TurnDirection

        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        self.assertTrue(agent._thanks(conversation))
        OutreachTurn.objects.create(conversation=conversation, direction=TurnDirection.OUT, text="x", action="help")
        self.assertEqual(agent._thanks(conversation), "")

    def test_the_bot_invitation_is_dropped_from_a_reply_that_already_has_a_link(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        reply = "آگهی‌ها اینجاست:\nhttps://t.me/x?startapp=home_de" + agent._hook_bot(conversation, seeker=True)
        self.assertEqual(agent._one_link(conversation, reply).count("https://"), 1)
        self.assertNotIn("bot", conversation.hooks_done)

    def test_a_stored_correction_answers_their_next_message_or_is_dropped_on_a_new_topic(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        text = "ببخشید، لینک قبلی کل شهر رو نشون می‌داد. اونی که عکس داره اینجاست:\nhttps://t.me/x?startapp=home_de_berlin_l1_src-o-abc"
        self.message.extra = {"on_next_reply": {"text": text}}
        self.message.save(update_fields=["extra"])
        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        self.assertEqual(agent._take_correction(conversation, "other"), text)
        self.assertEqual(agent._take_correction(conversation, "other"), "")
        self.message.extra = {"on_next_reply": {"text": text}}
        self.message.save(update_fields=["extra"])
        conversation.first_message.refresh_from_db()
        self.assertEqual(agent._take_correction(conversation, "remove"), "")
        self.message.refresh_from_db()
        self.assertEqual(self.message.extra["on_next_reply"]["outcome"], "dropped:remove")


class SearchLinkTests(AgentTestCase):
    def test_an_outreach_search_link_opens_with_its_filters_and_tracks_the_opener(self) -> None:
        from home.models import HomeSearchQuery
        from home_api.views import resolve_start

        query = HomeSearchQuery.objects.create(telegram_user_id=1, raw_text="x", country="DE", city="Berlin", parser="outreach", parsed={"types": ["room"], "max": 900, "areas": ["Neukölln"]})
        found = resolve_start(f"home_q{query.pk}_src-o-abcdef0123456789")
        self.assertEqual((found["kind"], found["src"]), ("city", "o-abcdef0123456789"))
        self.assertIn("areas=Neuk", found["path"])
        self.assertFalse(found.get("malformed"))


class Audit1815Tests(AgentTestCase):
    """Audit 2026-11-08 18:15 UTC: #138 named an unpriced ad outside the city and lost «از تیم چمدونم»."""

    def test_a_written_first_message_keeps_who_writes(self) -> None:
        from outreach import writer

        draft = "سلام Melika جان، ۸ تا خونه تو هامبورگ پیدا کردیم که به بودجه‌ی ۵۰۰ یوروی‌تون می‌خوره.\nاز تیم چمدونم. نیازی نیست همه‌ی گروه‌ها رو بگردید."
        sent = "سلام Melika جان، ۸ تا خونه تو هامبورگ پیدا کردیم که به بودجه‌ی ۵۰۰ یوروی‌تون می‌خوره.\nنیازی نیست همه‌ی گروه‌ها رو بگردید."
        self.assertEqual(writer.valid(draft, sent), "intro")
        self.assertEqual(writer.valid(draft, draft), "")

    def test_only_priced_fits_are_counted_and_the_named_one_is_in_a_known_area(self) -> None:
        from home.models import HomeListing
        from outreach import housing

        priced = self.listing(price_amount=450)
        unpriced = self.listing(price_amount=None)
        too_dear = self.listing(price_amount=700)
        fits = housing.priced_fits([unpriced, too_dear, priced], 500)
        self.assertEqual([item.pk for item in fits], [priced.pk])
        HomeListing.objects.filter(pk=priced.pk).update(home_area=None)
        priced.refresh_from_db()
        self.assertIsNone(housing.teaser_pick([priced]))

    def test_an_appended_correction_follows_the_answer_to_another_question(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        text = "ببخشید، اون اتاق که گفتم قیمتش تو آگهی نیومده. اتاق‌های هامبورگ که قیمت دارن اینجان: https://t.me/x?startapp=home_de_hamburg"
        self.message.extra = {"on_next_reply": {"text": text, "append": True}}
        self.message.save(update_fields=["extra"])
        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        taken = agent._take_correction(conversation, "budget")
        self.assertTrue(taken.startswith(agent.APPEND))
        self.message.refresh_from_db()
        self.assertEqual(self.message.extra["on_next_reply"]["outcome"], "appended:budget")


class Rule14FollowUpTests(AgentTestCase):
    def test_the_s1_follow_up_link_names_no_number_and_drops_unpriced_ads(self) -> None:
        import re

        from django.http import QueryDict

        from home.feed import feed_queryset, parse_filters
        from outreach import agent
        from outreach.models import OutreachConversation

        self.message.kind = OutreachKind.S1
        self.message.extra = {"budget": "500", "currency": "EUR", "count": 3}
        self.message.save(update_fields=["kind", "extra"])
        self.say("بله بفرستید")
        conversation = OutreachConversation.objects.get()
        reply = agent._seeker_help(conversation, __import__("random").Random(1))
        self.assertIn("p0-500", reply)
        self.assertFalse(re.search(r"[0-9۰-۹]", re.sub(r"https?://\S+", "", reply)), reply)
        # The p0-<budget> link itself never lists an ad without a price.
        self.listing(price_amount=None)
        get = QueryDict(mutable=True)
        get["country"], get["city"], get["max"] = "DE", "Berlin", "500"
        self.assertFalse(feed_queryset(parse_filters(get, ["DE"])).filter(price_amount__isnull=True).exists())


class Audit1915Tests(AgentTestCase):
    """Audit 2026-11-08 19:15 UTC: trailing team line, echoed greeting, direct answers, prefixed corrections."""

    def test_the_team_line_never_ends_a_reply(self) -> None:
        from outreach import agent

        text = "آگهی‌تون تو مونترال اینجاست:\nhttps://t.me/x?startapp=home_ca\nاز تیم چمدونم"
        self.assertEqual(agent.clean_reply(text), "آگهی‌تون تو مونترال اینجاست:\nhttps://t.me/x?startapp=home_ca")

    def test_the_writer_may_not_add_a_greeting_or_the_team_line(self) -> None:
        from outreach import writer

        draft = "آگهی‌تون تو ونکوور اینجاست:\nhttps://t.me/x?startapp=home_ca"
        self.assertEqual(writer.valid(draft, "سلام عزیز\n" + draft), "added_greeting")
        self.assertEqual(writer.valid(draft, draft + "\nاز تیم چمدونم"), "added_intro")

    def test_who_are_you_is_answered_in_line_one(self) -> None:
        from outreach import agent
        from outreach.models import OutreachTurn

        from tests.test_outreach_agent import model_says

        self.say("گروه چمدون کدومه؟")
        with model_says(intent="who"):
            handed = agent.next_reply("niloofar")
        turn = OutreachTurn.objects.get(pk=handed["turn_id"])
        self.assertEqual(turn.action, "about")
        self.assertTrue(turn.text.startswith(agent.lead_for_intro(agent.WHO_FIRST)))
        self.assertEqual(turn.text.count("جمع می"), 1, turn.text)
        self.assertEqual(agent.who_lead("who", "شما کی هستید؟"), agent.WHO_PLAIN)
        self.assertEqual(agent.who_lead("hello", "سلام؟"), "")

    def test_a_prefixed_correction_comes_first_then_the_answer(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        self.message.extra = {"on_next_reply": {"prefix": "چمدون گروه نیست.", "prefix_on": "*"}}
        self.message.save(update_fields=["extra"])
        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        taken = agent._take_correction(conversation, "photo")
        self.assertEqual(taken, agent.PREFIX + "چمدون گروه نیست.")

    def test_conv13_shape_full_text_when_neutral_prefix_when_they_answered(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        spec = {"text": "حق با شماست. هنوز موجودن؟", "prefix": "حق با شماست.", "prefix_on": ["available"]}
        self.message.extra = {"on_next_reply": spec}
        self.message.save(update_fields=["extra"])
        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        self.assertEqual(agent._take_correction(conversation, "other"), spec["text"])
        self.message.extra = {"on_next_reply": spec}
        self.message.save(update_fields=["extra"])
        conversation.first_message.refresh_from_db()
        self.assertEqual(agent._take_correction(conversation, "available"), agent.PREFIX + spec["prefix"])

    def test_a_short_stay_is_asked_its_price_plainly(self) -> None:
        from home.models import HomeListing
        from outreach import completion

        HomeListing.objects.filter(pk=self.ad.pk).update(rental_mode="short_term")
        self.ad.refresh_from_db()
        text = completion.ask_text(["price"], self.ad)
        self.assertIn("اجاره‌ش چقدره؟", text)
        self.assertNotIn("ماهانه", text)
        self.assertNotIn("گوش‌به‌زنگ", text)


class Audit2015Tests(AgentTestCase):
    """Audit 2026-11-08 20:15 UTC: the ask kept word for word; late backfills keep Telegram's time; a video is a turn."""

    PRICE = "سلام وقت بخیر، آگهی خونه‌تون تو مونترال رو تو چمدون گذاشتیم، فقط قیمت نداره؛ آگهی بی‌قیمت به کسایی که با بودجه‌ی مشخص دنبال خونه‌ان نشون داده نمی‌شه.\nاز تیم چمدونم. قیمتش چنده؟ خودم ثبتش می‌کنم."
    PRICE_105 = "سلام وقت بخیر، آگهی خونه‌تون تو مونترال تو چمدون هست، فقط قیمت نداره؛ آگهی بی‌قیمت به کسایی که با بودجه‌ی مشخص دنبال خونه‌ان نشون داده نمی‌شه.\nاز تیم چمدونم."
    DEMAND = "سلام وقت بخیر، آگهی ۱ خوابه‌تون تو تورنتو رو رایگان تو چمدون هم گذاشتیم؛ این هفته ۱۲ نفر دنبال همچین خونه‌ای بودن.\nاز تیم چمدونم. هنوز موجوده؟ اگه آره لینکش رو بفرستم که خودتون هم ببینید."
    DEMAND_106 = "سلام وقت بخیر، آگهی ۱ خوابه‌تون تو تورنتو رایگان تو چمدون هم هست؛ این هفته ۱۲ نفر دنبال همچین خونه‌ای بودن.\nاز تیم چمدونم.\nآره لینکش رو بفرستم که خودتون هم ببینید."

    def test_a_first_message_without_its_ask_falls_back_to_the_draft(self) -> None:
        from outreach import writer

        for draft, written in ((self.PRICE, self.PRICE_105), (self.DEMAND, self.DEMAND_106)):
            with patch("ai.openrouter.complete", return_value=ChatResult(ok=True, text=written, model="m")):
                self.assertEqual(writer.rewrite(draft, first=True), draft)
        self.assertIn("قیمتش چنده؟", writer.asks(self.PRICE))
        self.assertIn("هنوز موجوده؟", writer.asks(self.DEMAND))

    def test_a_late_backfilled_message_keeps_telegrams_time(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from outreach import agent
        from outreach.models import OutreachConversation, OutreachTurn, TurnStatus

        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        sent = (timezone.now() - timedelta(minutes=30)).isoformat()
        OutreachTurn.objects.filter(conversation=conversation).update(created_at=timezone.now() - timedelta(minutes=10))
        turn = agent.record_outbound("niloofar", conversation.telegram_user_id, 421, "این ویدیو کوتاه", sent_at=sent)
        turn.refresh_from_db()
        self.assertLess(turn.created_at, timezone.now() - timedelta(minutes=25))
        # Their later message is still waiting for our answer.
        self.assertTrue(conversation.turns.filter(direction="in", status=TurnStatus.PENDING).exists())

    def test_a_sent_video_is_a_turn_of_the_chat(self) -> None:
        from outreach.models import OutreachConversation, OutreachMessage, OutreachTurn
        from outreach.videos import record_video

        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        OutreachMessage.objects.filter(pk=self.message.pk).update(recipient_telegram_id=conversation.telegram_user_id)
        record_video(self.message.pk, ok=True, telegram_message_id=421)
        self.assertTrue(OutreachTurn.objects.filter(conversation=conversation, action="video", telegram_message_id=421).exists())


class Audit2250Tests(AgentTestCase):
    """Audit 2026-11-08 22:50 UTC: a stored text is checked against the reply (rule 34); photos on a first reply (rule 35)."""

    def test_a_stored_text_is_not_sent_when_their_reply_changed_the_facts(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation, OutreachTurn

        from tests.test_outreach_agent import model_says

        self.message.extra = {"on_reply": {"text": "اگه اتاق کوتاه‌مدت با امکان ملده به کارتون میاد، بگید تا براتون بفرستم."}}
        self.message.save(update_fields=["extra"])
        self.say("سلام وقتتون بخیر ممنونم خونه گرفتم دنبال ملده هستم")
        self.assertFalse(OutreachTurn.objects.filter(action="operator").exists())
        with model_says(intent="rented"):
            self.answer()
        self.assertFalse(OutreachTurn.objects.filter(action="correction").exists())
        self.message.refresh_from_db()
        self.assertEqual(self.message.extra["on_next_reply"]["outcome"], "dropped:rented")

    def test_photos_on_a_first_reply_get_one_message_thanks_first_then_the_intro(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from outreach import agent
        from outreach.models import OutreachTurn

        found = agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 7, "", True)
        OutreachTurn.objects.filter(pk=found.turn.pk).update(reply_due_at=timezone.now() - timedelta(seconds=1), action_detail={"photo_held": "unsure"})
        with patch("outreach.agent.transcript"):
            handed = agent.next_reply("niloofar")
        self.assertTrue(handed["text"].startswith("عکس رسید"))
        self.assertIn("\n", handed["text"])
        self.assertEqual(OutreachTurn.objects.filter(direction="out").count(), 1)


class ExtendBurstTests(AgentTestCase):
    """Audit 2026-11-09, conv 20: «بله هنوز موجوده» on a first reply: thanks first with the intro, then only the link."""

    def test_a_yes_is_thanked_with_the_intro_and_the_link_follows_without_repeats(self) -> None:
        from outreach import agent
        from outreach.models import OutreachTurn

        from tests.test_outreach_agent import model_says

        self.say("بله هنوز موجوده")
        with model_says(intent="available", listing="L1"):
            intro = agent.next_reply("niloofar")
            agent.reply_sent(intro["turn_id"], ok=True, telegram_message_id=901)
            reply = agent.next_reply("niloofar")
        self.assertEqual(OutreachTurn.objects.get(pk=intro["turn_id"]).action, "about")
        self.assertTrue(intro["text"].startswith("ممنون، آگهی‌تون"))
        self.assertTrue(reply["text"].startswith("اینم لینک آگهی‌تون تو چمدون:"))
        self.assertNotIn("رایگان", reply["text"])
        self.assertNotIn("می‌بینتش", reply["text"])
        self.assertEqual(reply["text"].count("https://"), 1)


class NoWaitingTests(AgentTestCase):
    """Mohammad, 2026-11-09: people waited hours for an answer."""

    def test_an_answer_after_a_long_wait_opens_with_a_sorry(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from outreach import agent
        from outreach.models import OutreachTurn

        from tests.test_outreach_agent import model_says

        found = self.say("سلام، چمدون چی هست؟")
        OutreachTurn.objects.filter(pk=found.turn.pk).update(created_at=timezone.now() - timedelta(hours=4))
        with model_says(intent="who"):
            handed = agent.next_reply("niloofar")
            agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=990)
            reply = agent.next_reply("niloofar")
        # The sorry goes in the first message out, here the intro.
        self.assertTrue(handed["text"].startswith(agent.LATE_APOLOGY))
        # They asked what Chamedoon is: intro and link go as one message, so nothing follows (audit 2026-11-11).
        self.assertIsNone(reply)
        self.assertEqual(handed["text"].count(agent.LATE_APOLOGY), 1)

    def test_an_inbound_unanswered_for_ten_minutes_is_an_incident(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from outreach import health
        from outreach.models import OutreachAccount, OutreachTurn

        OutreachAccount.objects.filter(pk=self.account.pk).update(last_heartbeat_at=timezone.now(), last_sent_at=timezone.now())
        found = self.say("سلام")
        OutreachTurn.objects.filter(pk=found.turn.pk).update(created_at=timezone.now() - timedelta(minutes=11))
        self.assertIn(f"unanswered_{found.turn.pk}", [incident.key for incident in health.check(timezone.now())])


class Conv21Tests(AgentTestCase):
    """2026-11-09, conv 21: five photos, a video, «سلام خیلی ممنونم», «بفرمایید»: everything marked done, no answer."""

    def test_media_in_a_burst_is_thanked_even_when_the_last_text_is_thanks(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from outreach import agent
        from outreach.models import OutreachTurn

        from tests.test_outreach_agent import model_says

        self.say("سلام")
        with model_says(intent="hello"):
            handed = self.answer()
        if handed:
            agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=900)
        for message_id in (11, 12, 13):
            agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", message_id, "", True)
        agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 14, "", False, has_video=True)
        last = agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 15, "سلام خیلی ممنونم", False)
        OutreachTurn.objects.filter(pk=last.turn.pk).update(reply_due_at=timezone.now() - timedelta(seconds=1))
        with model_says(intent="thanks"), patch("outreach.agent.transcript"):
            reply = self.answer()
        self.assertIsNotNone(reply)
        self.assertTrue(reply["text"].startswith("ویدیوها رسید") or reply["text"].startswith("عکس‌ها رسید"))

    def test_go_ahead_is_never_silence(self) -> None:
        from outreach import agent

        from tests.test_outreach_agent import model_says

        self.say("سلام")
        with model_says(intent="hello"):
            handed = self.answer()
        if handed:
            agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=900)
        self.say("بفرمایید", 2)
        with model_says(intent="thanks"):
            reply = self.answer()
        self.assertIsNotNone(reply)
        # Never silence; their ad link already went with the hello answer, so it is not sent again (conv 23, rules 36/40).
        self.assertTrue(reply["text"].strip())
        self.assertNotIn("https://", reply["text"])


class MediaKnownTests(AgentTestCase):
    """Audit 2026-11-09, conv 22: two photos sent, then «تو آگهی‌تون عکس نیومده بود... چند تا عکس بفرستید»."""

    def test_no_photo_ask_after_photos_were_sent(self) -> None:
        from outreach import agent, completion
        from outreach.models import OutreachConversation

        self.say("سلام")
        agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 31, "", True)
        conversation = OutreachConversation.objects.get()
        self.assertTrue(completion._sent_video(conversation))


class BackfillTimeTests(AgentTestCase):
    def test_a_reply_read_back_late_keeps_its_time_and_gets_a_sorry(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from outreach import agent
        from outreach.models import OutreachTurn

        from tests.test_outreach_agent import model_says

        sent = (timezone.now() - timedelta(hours=2)).isoformat()
        found = agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 61, "سلام بله موجوده", False, sent_at=sent)
        found.turn.refresh_from_db()
        self.assertLess(found.turn.created_at, timezone.now() - timedelta(minutes=100))
        OutreachTurn.objects.filter(pk=found.turn.pk).update(reply_due_at=timezone.now() - timedelta(seconds=1))
        with model_says(intent="available", listing="L1"):
            first = agent.next_reply("niloofar")
        self.assertTrue(first["text"].startswith(agent.LATE_APOLOGY))


class WhereIsMyAdTests(AgentTestCase):
    """Mohammad, 2026-11-09 (conv 22): «الان شما منو اد کردید تو این گروه؟» means where is my ad: give the link."""

    def test_a_question_about_a_group_or_where_the_ad_is_gets_the_ad_link(self) -> None:
        from outreach import agent

        from tests.test_outreach_agent import model_says

        self.say("سلام")
        with model_says(intent="hello"):
            handed = self.answer()
        if handed:
            agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=900)
        self.say("الان شما منو اد کردید تو این گروه؟", 2)
        with model_says(intent="other"):
            reply = self.answer()
        self.assertIn(f"l{self.ad.pk}", reply["text"])
        self.assertEqual(reply["text"].count("https://"), 1)


class PeerFloodRecipientTests(AgentTestCase):
    def test_a_recipient_refused_with_peer_flood_is_never_queued_again(self) -> None:
        from outreach.models import OutreachMessage, OutreachStatus
        from outreach.services import already_contacted

        OutreachMessage.objects.create(kind=OutreachKind.P1, recipient_telegram_id=134077363, recipient_username="x_flood", token="p" * 16, text="x", status=OutreachStatus.SKIPPED, error_code="peer_flood")
        OutreachMessage.objects.create(kind=OutreachKind.P1, recipient_telegram_id=555, recipient_username="y_stale", token="q" * 16, text="x", status=OutreachStatus.SKIPPED, error_code="stale")
        self.assertTrue(already_contacted(telegram_user_id=134077363))
        self.assertTrue(already_contacted(username="x_flood"))
        self.assertFalse(already_contacted(telegram_user_id=555))


class Audit0215Tests(AgentTestCase):
    """Audit 2026-11-09 02:15 UTC: rules 39, 40, 41 and the open-plan field ask."""

    def test_a_shared_unit_is_a_room(self) -> None:
        from home.models import HomePost
        from outreach import housing

        HomePost.objects.filter(pk=self.ad.post_id).update(text="یک اتاق در واحد ۲ خوابه، با یک خانوم تقسیم می‌شود. هم‌اطاقی خانوم")
        self.ad.refresh_from_db()
        self.assertTrue(housing.ad_desc(self.ad).startswith("اتاق"))

    def test_an_open_plan_is_not_asked_its_bedrooms(self) -> None:
        from home.models import HomeListing, HomePost
        from outreach import completion

        HomeListing.objects.filter(pk=self.ad.pk).update(property_type="apartment", rooms=None)
        HomePost.objects.filter(pk=self.ad.post_id).update(text="واگذاری لیز، طراحی داخلی باز با فضای مخصوص خواب")
        self.ad.refresh_from_db()
        self.assertNotIn("rooms", completion.gaps(self.ad))

    def test_the_who_line_is_not_followed_by_the_dont_you_know_us_intro(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        self.say("چمدون چی هست؟")
        conversation = OutreachConversation.objects.get()
        for _ in range(10):
            _code, intro = agent.pick_intro(conversation, after_who=True)
            self.assertNotIn("شاید چمدون رو نشناسید", intro)
        self.assertNotIn("رایگان", agent.WHO_PLAIN)

    def test_a_person_skipped_as_unclear_is_never_queued_again_for_that_ad(self) -> None:
        from outreach import housing
        from outreach.models import OutreachMessage, OutreachStatus

        OutreachMessage.objects.create(kind=OutreachKind.S2, listing=self.ad, token="u" * 16, text="x", status=OutreachStatus.SKIPPED, error_code="unclear_post")
        person = housing.Person(telegram_id=999001, username="nobody_x", listings=[(self.ad, None)])
        self.assertEqual(housing.person_skip_reason(person), "unclear_before")


class TownFromAreaTests(AgentTestCase):
    def test_a_separate_municipality_is_named_by_the_town(self) -> None:
        from home.models import HomeListing
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(area="North Vancouver, Lonsdale Street")
        self.ad.refresh_from_db()
        self.assertEqual(housing.town_of(self.ad), "نورث ونکوور")
        HomeListing.objects.filter(pk=self.ad.pk).update(area="Richmond Hill")
        self.ad.refresh_from_db()
        self.assertEqual(housing.town_of(self.ad), "ریچموند هیل")
        HomeListing.objects.filter(pk=self.ad.pk).update(area="Neukölln")
        self.ad.refresh_from_db()
        self.assertEqual(housing.town_of(self.ad), "")


class GapAdWordTests(AgentTestCase):
    def test_the_photo_variant_names_a_room_as_a_room(self) -> None:
        from django.utils import timezone

        from home.models import HomeListing
        from outreach import housing
        from outreach.models import OutreachMessage

        HomeListing.objects.filter(pk=self.ad.pk).update(property_type="room")
        message = OutreachMessage.objects.create(kind=OutreachKind.P1, listing=self.ad, country="DE", city="Berlin", token="g" * 16, text="x", recipient_name="Hoda")
        with patch("outreach.housing.p1_variant", return_value=("photo", 0)):
            text = housing.render(message, timezone.now())
        self.assertIn("آگهی اتاق‌تون", text)
        self.assertNotIn("آگهی خونه‌تون", text)


class AreaCityTests(AgentTestCase):
    """Audit 2026-11-09 #308: a Toronto room («Bayview Village») from the Montreal group was stored and written as Montreal."""

    def setUp(self) -> None:
        super().setUp()
        from home.models import HomeArea, HomeChannel

        for city, username in (("Toronto", "tor_t"), ("Montreal", "mtl_t"), ("Vancouver", "van_t")):
            HomeChannel.objects.get_or_create(country="CA", city=city, defaults={"username": username})
        HomeArea.objects.get_or_create(country="CA", city="Toronto", canonical="bayview village", defaults={"fa": "بیویو ویلج"})
        HomeArea.objects.get_or_create(country="CA", city="Montreal", canonical="plateau", defaults={"fa": "پلاتو"})

    def ad_in(self, city: str, area: str):
        from home.models import HomeListing

        HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", region="QC", city=city, area=area)
        self.ad.refresh_from_db()
        return self.ad

    def test_the_area_wins_over_the_group_city(self) -> None:
        from home.areas import area_metro, link_area

        ad = self.ad_in("Montreal", "Bayview Village")
        target, verdict = area_metro(ad)
        self.assertEqual((target.city, verdict), ("Toronto", "moved"))
        link_area(ad)
        ad.refresh_from_db()
        self.assertEqual((ad.city, ad.region, ad.home_area.canonical), ("Toronto", "ON", "bayview village"))

    def test_an_area_of_its_own_city_or_unknown_stays(self) -> None:
        from home.areas import area_metro

        self.assertEqual(area_metro(self.ad_in("Montreal", "Plateau"))[1], "")
        self.assertEqual(area_metro(self.ad_in("Montreal", "Somewhere Unknown"))[1], "")

    def test_an_area_of_two_other_cities_is_unclear(self) -> None:
        from home.areas import area_metro
        from home.models import HomeArea

        HomeArea.objects.get_or_create(country="CA", city="Vancouver", canonical="bayview village", defaults={"fa": "بیویو"})
        self.assertEqual(area_metro(self.ad_in("Montreal", "Bayview Village"))[1], "unclear")


class RoomInUnitWordTests(AgentTestCase):
    def test_a_master_room_in_a_basement_is_a_room(self) -> None:
        # #307: «اجاره اتاق مستر در بیسمنت» was written «آگهی بیسمنت‌تون».
        from home.models import HomeListing, HomePost
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(property_type="basement", rooms=None)
        HomePost.objects.filter(pk=self.ad.post_id).update(text="اجاره اتاق مستر در بیسمنت فقط خانم، نزدیک یانگ")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "اتاق")
        HomeListing.objects.filter(pk=self.ad.pk).update(property_type="apartment", rooms=3)
        HomePost.objects.filter(pk=self.ad.post_id).update(text="آپارتمان ۳ اتاق خواب، اجاره ۳ اتاق خوابه نزدیک مترو")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "آپارتمان ۳ خوابه")  # the post counts bedrooms itself (audit 19:15 #336)
        HomePost.objects.filter(pk=self.ad.post_id).update(text="3-Zimmer-Wohnung nahe U-Bahn")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "آپارتمان سه اتاقه")  # Zimmer stay Zimmer (rule 46)


class Audit0120Tests(AgentTestCase):
    """Audit 2026-11-10 01:20: a misspelled town (#319) and a whole unit offered with its rooms (#320)."""

    def test_a_misspelled_town_is_still_named(self) -> None:
        from home.models import HomeChannel, HomeListing
        from outreach import housing

        HomeChannel.objects.get_or_create(country="CA", city="Toronto", defaults={"username": "toronto_t"})
        for typo in ("Kezwick", "Keswik"):
            HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", city="Toronto", area=typo)
            self.ad.refresh_from_db()
            self.assertEqual(housing.town_of(self.ad), "کزیک", typo)
        HomeListing.objects.filter(pk=self.ad.pk).update(area="Mississuaga Square One")
        self.ad.refresh_from_db()
        self.assertEqual(housing.town_of(self.ad), "میسیساگا")
        HomeListing.objects.filter(pk=self.ad.pk).update(area="Yonge & Sheppard")
        self.ad.refresh_from_db()
        self.assertEqual(housing.town_of(self.ad), "")

    def test_a_basement_keeps_its_bedrooms(self) -> None:
        from home.models import HomeListing, HomePost
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", city="Toronto", property_type="basement", rooms=1)
        HomePost.objects.filter(pk=self.ad.post_id).update(text="بیسمنت ۱ خواب در کزویک نوساز، ۱۷۰۰$ اینکلود یوتیلیتی")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "بیسمنت ۱ خوابه")

    def test_a_unit_offered_whole_or_by_room_is_the_unit(self) -> None:
        from home.models import HomeListing, HomePost
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", city="Toronto", property_type="apartment", rooms=2)
        HomePost.objects.filter(pk=self.ad.post_id).update(
            text="اجاره واحد دوخوابه یا اجاره اتاق بصورت مجزا، طبقه همکف و بیسمنت واک‌اوت، ۹۰ متر، آرورا"
        )
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "۲ خوابه")
        HomePost.objects.filter(pk=self.ad.post_id).update(text="آپارتمان دو خوابه، اتاق‌ها جدا هم اجاره داده می‌شن")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "۲ خوابه")
        # Only a room on offer stays a room.
        HomePost.objects.filter(pk=self.ad.post_id).update(text="اجاره اتاق در واحد دوخوابه، فقط خانم")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "اتاق")


class GermanRoomsAndTownTests(AgentTestCase):
    """Audit 2026-11-09 #310: «یک واحد دو اتاقه» in Offenbach Bieber, from the Cologne group, written «۲ خوابه‌تون»."""

    def test_rooms_in_germany_are_zimmer(self) -> None:
        from home.models import HomeListing, HomePost
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(property_type="apartment", rooms=2)
        HomePost.objects.filter(pk=self.ad.post_id).update(text="یک واحد دو اتاقه در Bieber")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "آپارتمان دو اتاقه")
        HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", city="Toronto")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "۲ خوابه")

    def test_a_city_outside_the_metro_is_named_by_its_town(self) -> None:
        from home.models import HomeChannel, HomeListing
        from outreach import housing

        HomeChannel.objects.get_or_create(country="DE", city="Cologne", defaults={"username": "koln_t"})
        HomeListing.objects.filter(pk=self.ad.pk).update(city="Cologne", area="Offenbach Bieber")
        self.ad.refresh_from_db()
        self.assertEqual(housing.town_of(self.ad), "اوفنباخ")


class SeekerMatchTests(AgentTestCase):
    def test_any_match_looks_past_the_newest_offer(self) -> None:
        # #309: S2 «nothing fits» while offers fit, because the check looked at the country's newest offer only.
        from home.alerts import search
        from home.models import HomeAlert, HomeChannel, HomeListing
        from outreach import housing

        for city in ("Berlin", "Hamburg"):
            HomeChannel.objects.get_or_create(country="DE", city=city, defaults={"username": f"{city.lower()}_t"})
        newest = self.listing(city="Hamburg", area="Altona")
        HomeListing.objects.exclude(pk=newest.pk).update(last_seen=newest.last_seen - timedelta(hours=1))
        alert = HomeAlert(country="DE", city="Berlin", answered=["budget", "type", "gender", "rooms", "registration", "term", "area"], complete=True)
        # Since audit 2026-11-13 07:20 the limit caps matches, not candidates: search finds it too.
        self.assertTrue(search(alert, limit=1))
        self.assertTrue(housing._any_match(alert))


class SeekerUnknownsTests(AgentTestCase):
    def test_what_the_post_never_said_does_not_make_nothing_fit(self) -> None:
        # #309: «دنبال خونه میگردم سمت هلرسدورف», no budget; Hellersdorf rooms for men and without a price were live.
        from home.models import HomeChannel, HomeListing
        from outreach import housing

        channel, _ = HomeChannel.objects.get_or_create(country="DE", city="Berlin", defaults={"username": "berlin_t"})
        offer = self.listing(area="Hellersdorf", price_amount=None, property_type="room", roommate_gender="male")
        wanted = self.listing(kind="wanted", area="Hellersdorf", property_type="other", price_amount=None)
        self.assertEqual(offer.area, "Hellersdorf")
        self.assertTrue(housing.matches_any(wanted, channel))
        HomeListing.objects.filter(pk=offer.pk).update(area="Spandau")
        self.assertFalse(housing.matches_any(wanted, channel))


class Audit0316Tests(AgentTestCase):
    """Audit 2026-11-10 03:16 (#324, rule 50): a title glued to the name, a living room read as a bedroom, and a
    Montreal south-shore town."""

    POST = (
        "یک خونه کوچک با سالن و اتاق خواب و آشپزخانه و سرویس بهداشتی در سنت هوبرت (جنوب مونترال) "
        "میزان اجاره ۱۳۵۰$ و بابت شوفاژ ۱۰۰ دلار"
    )

    def test_a_title_is_not_the_name(self) -> None:
        from outreach.templates import valid_name

        self.assertEqual(valid_name("دکترناهید فرخ یار"), "ناهید")
        self.assertEqual(valid_name("دکتر ناهید"), "ناهید")
        self.assertEqual(valid_name("مهندس‌علی رضایی"), "علی")
        self.assertEqual(valid_name("Dr. Sara"), "Sara")
        self.assertEqual(valid_name("Dr"), "")
        self.assertEqual(valid_name("مهدی"), "مهدی")

    def test_a_living_room_is_not_a_bedroom(self) -> None:
        from decimal import Decimal

        from home.review import bedrooms_written

        self.assertEqual(bedrooms_written(self.POST, Decimal("2")), Decimal("1"))
        self.assertEqual(bedrooms_written("پذیرایی و اتاق خواب، مبله", Decimal("2")), Decimal("1"))
        self.assertEqual(bedrooms_written("آپارتمان ۲ خوابه با سالن و اتاق خواب مستر", Decimal("2")), Decimal("2"))
        self.assertEqual(bedrooms_written("دوخوابه با سالن بزرگ", Decimal("2")), Decimal("2"))
        self.assertEqual(bedrooms_written("سالن و اتاق‌خواب‌ها نورگیر", Decimal("3")), Decimal("3"))

    def test_saint_hubert_is_named(self) -> None:
        from home.models import HomeListing
        from outreach import housing

        for area, town in (("Saint-Hubert", "سنت هوبرت"), ("St-Hubert", "سنت هوبرت"), ("Greenfield Park", "گرینفیلد پارک"),
                           ("Vieux-Longueuil", "لانگوی"), ("Saint-Lambert", "سنت لمبرت"), ("Boucherville", "بوشرویل"),
                           ("Terrebonne", "تربن"), ("Laval", "لاوال"), ("Brossard", "بروسارد")):  # fmt: skip
            HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", city="Montreal", area=area)
            self.ad.refresh_from_db()
            self.assertEqual(housing.town_of(self.ad), town, area)


class Audit0517Tests(AgentTestCase):
    """Audit 2026-11-10 05:17 (#327, rule 51): a neighbourhood named by its municipality, a date without a year is
    the next one, and a summary in another script is dropped."""

    def test_a_neighbourhood_is_its_municipality(self) -> None:
        from home.models import HomeListing
        from outreach import housing

        for area, town in (("Burquitlam", "کوکیتلام"), ("Lonsdale", "نورث ونکوور"), ("Metrotown", "برنابی"),
                           ("Steveston", "ریچموند"), ("Guildford", "ساری")):  # fmt: skip
            HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", city="Vancouver", area=area)
            self.ad.refresh_from_db()
            self.assertEqual(housing.town_of(self.ad), town, area)
        HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", city="Calgary", area="Brentwood")
        self.ad.refresh_from_db()
        self.assertEqual(housing.town_of(self.ad), "")

    def test_a_date_without_a_year_is_the_next_one(self) -> None:
        from datetime import date, datetime
        from datetime import timezone as tz
        from types import SimpleNamespace

        from home.review import _next_year

        posted = datetime(2026, 11, 10, 5, tzinfo=tz.utc)
        post = SimpleNamespace(posted_at=posted, text="Sublet from January 1st to January 31st, close to SkyTrain Burquitlam")
        notes: list[str] = []
        self.assertEqual(_next_year(post, date(2026, 1, 1), date(2026, 1, 31), notes), (date(2027, 1, 1), date(2027, 1, 31)))
        self.assertEqual(notes, [])
        post.text = "available since January 2026"
        self.assertEqual(_next_year(post, date(2026, 1, 1), None, notes), (date(2026, 1, 1), None))
        self.assertTrue(notes)
        self.assertEqual(_next_year(post, date(2026, 11, 1), None, []), (date(2026, 11, 1), None))

    def test_a_summary_in_another_script_is_dropped(self) -> None:
        from home.review import _summary

        self.assertEqual(_summary("آپارتمان مبله نزدیک اسکایت्रेन برکیتلام"), "")
        kept = "آپارتمان ۱ خوابه مبله، نزدیک اسکای‌ترین برکیتلام، Köln-Ehrenfeld, Montréal"
        self.assertTrue(_summary(kept))


class Audit0816Tests(AgentTestCase):
    """Audit 2026-11-10 08:16 (#328, rule 52): a city the post names wins over the shared group's default city."""

    def test_the_named_city_wins(self) -> None:
        from home.review import named_city

        post = "یک مورد استودیو مبله به متراژ ۲۰ متر در لوکیشن بسیار خوب دوسلدورف Friedrichstadt\n🏙 شهر: #کلن آلمان"
        notes: list[str] = []
        self.assertEqual(named_city(post, "Cologne", "Cologne", notes), "Dusseldorf")
        self.assertEqual(notes, [])
        self.assertEqual(named_city("اتاق در Düsseldorf-Bilk", "Cologne", "Cologne", []), "Dusseldorf")
        self.assertEqual(named_city("اتاق در کلن، ۲۰ دقیقه تا دوسلدورف", "Cologne", "Cologne", notes), "Cologne")
        self.assertTrue(notes)
        self.assertEqual(named_city("اتاق در Ehrenfeld #دوسلدورف", "Cologne", "Cologne", []), "Cologne")
        self.assertEqual(named_city("Wir essen zusammen, Zimmer in Nippes", "Cologne", "Cologne", []), "Cologne")
        self.assertEqual(named_city("دوسلدورف", "Berlin", "Berlin", []), "Berlin")


class Audit1017Tests(AgentTestCase):
    """Audit 2026-11-10 10:17 (conv 24, rule 31): «تیم چمدون ؟» is answered yes on line 1."""

    def test_is_it_chamedoon_is_answered_yes(self) -> None:
        from outreach.agent import YES_US, who_lead

        self.assertEqual(who_lead("hello", "سلام تیم چمدون ؟"), YES_US)
        self.assertEqual(who_lead("who", "شما چمدونید؟"), YES_US)
        self.assertEqual(who_lead("hello", "سلام"), "")


class Audit1116Tests(AgentTestCase):
    """Audit 2026-11-10 11:16 (#331, rules 19 and 53): a handle is not a name, and Eichwalde is not Berlin."""

    def test_one_latin_word_must_be_a_known_name(self) -> None:
        from outreach.templates import valid_name

        self.assertEqual(valid_name("Amix"), "")
        self.assertEqual(valid_name("Amir"), "Amir")
        self.assertEqual(valid_name("Omid"), "Omid")
        self.assertEqual(valid_name("Sali"), "Sali")
        self.assertEqual(valid_name("Mobina"), "Mobina")
        for raw, name in (("amir", "Amir"), ("AMIR", "Amir"), ("MahsA", "Mahsa"), ("Elham✨", "Elham"), ("naz", "Naz"), ("nasrin", "Nasrin")):
            self.assertEqual(valid_name(raw), name, raw)
        for raw in ("M", "HJ", "AM", "SZR", "P.hz", "S@m", "F-B", "Z@p", "H-p", "Breeze", "Mac"):
            self.assertEqual(valid_name(raw), "", raw)
        self.assertEqual(valid_name("Sali Ahmadi"), "Sali")

    def test_berlin_edge_towns_are_named(self) -> None:
        from home.models import HomeListing
        from outreach import housing

        for area, town in (("Eichwalde", "آیشوالده"), ("Schönefeld", "شونفلد"), ("Teltow", "تلتو"),
                           ("Königs Wusterhausen", "کونیگس ووسترهاوزن"), ("Teltower Damm", ""), ("Moabit", "")):  # fmt: skip
            HomeListing.objects.filter(pk=self.ad.pk).update(country="DE", city="Berlin", area=area)
            self.ad.refresh_from_db()
            self.assertEqual(housing.town_of(self.ad), town, area)

    def test_an_unknown_berlin_area_is_held(self) -> None:
        from home.models import HomeListing
        from outreach import housing
        from outreach.models import OutreachKind, OutreachMessage

        message = OutreachMessage(kind=OutreachKind.P1, listing=self.ad, text="x")
        HomeListing.objects.filter(pk=self.ad.pk).update(country="DE", city="Berlin", area="Adalbertstraße10179", home_area=None)
        self.ad.refresh_from_db()
        self.assertFalse(housing.area_unresolved(message))  # a Berlin postcode is Berlin proper (#277)
        HomeListing.objects.filter(pk=self.ad.pk).update(area="Adalbertstraße")
        self.ad.refresh_from_db()
        self.assertTrue(housing.area_unresolved(message))
        HomeListing.objects.filter(pk=self.ad.pk).update(area="Eichwalde")
        self.ad.refresh_from_db()
        self.assertFalse(housing.area_unresolved(message))
        HomeListing.objects.filter(pk=self.ad.pk).update(area="")
        self.ad.refresh_from_db()
        self.assertFalse(housing.area_unresolved(message))


class Audit1216Tests(AgentTestCase):
    """Audit 2026-11-10 12:16 (#332): a bed let for a night or two is a bed, and a short stay says so."""

    def test_a_bed_is_a_bed_and_furniture_is_not(self) -> None:
        from home.models import HomeListing, HomePost
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", city="Montreal", property_type="room", rental_mode="short_term")
        HomePost.objects.filter(pk=self.ad.post_id).update(text="#اجاره تخت جهت اقامت یک یا ۲ شب #مناسب برای خانم ها #هماهنگی دایرکت")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "تخت")
        HomePost.objects.filter(pk=self.ad.post_id).update(text="اجاره یک اتاق مبله با تخت دونفره و کمد، نزدیک مترو")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "اتاق")


class Audit1615Tests(AgentTestCase):
    """Audit 2026-11-10 16:15 (conv 25 nike071, conv 26 User030X)."""

    T142 = "اوکی خیلی هم عالی ،\nخونه تقریبا ۷۰ متره ،\nیه خواب بزرگ با حیاط شخصی داره ، مناسب برای حیوان خانگی یا گریل ، عکس میفرستم براتون"

    def eichwalde(self) -> None:
        from home.models import HomeListing

        HomeListing.objects.filter(pk=self.ad.pk).update(country="DE", city="Berlin", area="Eichwalde", size_m2=None, home_area=None)
        self.ad.refresh_from_db()

    def test_a_size_is_never_written_as_the_area(self) -> None:
        """T142/T143: «۷۰ متره» went into the area field and Eichwalde was lost."""
        from outreach import actions, completion
        from outreach.models import OutreachConversation

        self.eichwalde()
        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        self.assertFalse(actions.fill_field(self.ad, "area", "70 متر"))
        for value in ("۷۰ متر", "85 qm", "70m2", "12"):
            self.assertFalse(actions.is_place(self.ad, value), value)
        self.assertTrue(actions.is_place(self.ad, "Eichwalde"))
        applied = completion.apply_values(conversation, self.ad.pk, {"area": "70 متر"}, by="agent")
        self.assertEqual(applied.filled, ["size"])
        self.ad.refresh_from_db()
        self.assertEqual((self.ad.area, self.ad.size_m2), ("Eichwalde", 70))
        self.assertEqual(completion.saved_text(self.ad, ["size"]), "ممنون، متراژ ۷۰ متر رو تو آگهی ثبت کردم.")

    def test_no_photo_ask_when_they_say_they_will_send_them(self) -> None:
        """T143 asked for photos one minute after «عکس میفرستم براتون»."""
        from outreach.agent import PHOTOS_WHEN_SENT

        self.eichwalde()
        self.say("سلام")
        with model_says_(intent="hello"):
            self.answer()
        self.say(self.T142, 2)
        with model_says_(intent="field", field="area", value="70 متر"):
            reply = self.answer()
        self.assertIn("متراژ ۷۰ متر", reply["text"])
        self.assertIn(PHOTOS_WHEN_SENT, reply["text"])
        self.assertNotIn("عکس نیومده", reply["text"])
        self.assertNotIn("70", reply["text"])
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.area, "Eichwalde")

    def test_a_question_earlier_in_the_burst_is_answered_first(self) -> None:
        """T136/T140: «گروه تلگرامیه یا سایته؟» then «لطفا لینک و بفرستید»: only the second was read."""
        from outreach import agent
        from outreach.models import OutreachConversation, OutreachTurn

        self.eichwalde()
        self.say("سلام ، بله درسته ، چمدون منظورتون گروه تلگرامیه ؟ یا سایته ؟")
        last = self.say("لطفا لینک و برای من بفرستید ،", 2)
        conversation = OutreachConversation.objects.get()
        self.assertIn("سایته", agent.burst_text(conversation, OutreachTurn.objects.get(pk=last.turn.pk)))
        with model_says_(intent="hello"):
            intro = self.answer(with_intro=True)
        self.assertTrue(intro["text"].startswith(agent.MINI_APP_FIRST))
        agent.reply_sent(intro["turn_id"], ok=True, telegram_message_id=5001)
        link = agent.next_reply("niloofar")
        self.assertTrue(link["text"].startswith("اینم لینک آگهی‌تون:"))

    def test_the_reply_names_the_town(self) -> None:
        """T141 said «تو برلین» for an Eichwalde house; the first message said «تو آیشوالده»."""
        from outreach import agent
        from outreach.models import OutreachConversation

        self.eichwalde()
        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        self.assertIn("آگهی‌تون تو آیشوالده اینجاست", agent._own_line(conversation, [self.ad]))

    def test_a_bare_okay_after_a_full_answer_gets_nothing(self) -> None:
        """T144 «اوکی» (intent thanks) got «هر چیزی رو بخواید ...»."""
        self.say("سلام")
        with model_says_(intent="hello"):
            first = self.answer()
        from outreach import agent

        agent.reply_sent(first["turn_id"], ok=True, telegram_message_id=5002)
        self.say("اوکی", 2)
        with model_says_(intent="thanks"):
            self.assertIsNone(self.answer())

    def test_a_seeker_count_and_link_keep_their_type(self) -> None:
        """T139: «۲۹ تا آگهی زیر ۵۰۰» for someone who asked for a WG twice; the alert was never mentioned."""
        from home.models import HomeListing, ListingStatus
        from outreach.agent import ALERT_ON

        HomeListing.objects.filter(pk=self.ad.pk).update(status=ListingStatus.RENTED)
        wanted = self.listing(kind="wanted", price_amount=None, price_max=None, area="Berlin")
        HomeListing.objects.filter(pk=wanted.pk).update(property_type="room", flags={"wg": True})
        for user_id in (71, 72, 73, 75, 76):
            self.listing(poster=person_post_fields(user_id=user_id, username=f"r_{user_id}", name="R"), price_amount=450, property_type="room")
        self.listing(poster=person_post_fields(user_id=74, username="a_74", name="A"), price_amount=480, property_type="apartment")
        self.say("سلام دنبال WG تا 500 هستم")
        with model_says_(intent="budget", budget=500):
            reply = self.answer()
        from outreach.agent import feed_count
        from outreach.models import OutreachConversation
        from outreach.templates import fa_number

        count = feed_count(OutreachConversation.objects.get(), 500, kind="room")
        self.assertLess(count, feed_count(OutreachConversation.objects.get(), 500))  # the apartment is left out
        self.assertIn("اتاق و WG زیر ۵۰۰ یورو تو برلین", reply["text"])
        self.assertNotIn(f"{fa_number(count + 1)} تا", reply["text"])
        self.assertIn("t-room", reply["text"])
        self.assertIn(ALERT_ON, reply["text"])

    def test_an_armed_text_waits_for_the_next_inbound(self) -> None:
        """Conv 26: closed with «مرسی»; the size and yard line goes with his next message, re-checked then."""
        from home.models import HomeListing
        from outreach.models import OutreachMessage

        HomeListing.objects.filter(pk=self.ad.pk).update(size_m2=70)
        line = "متراژ ۷۰ متر و حیاط اختصاصی رو هم تو آگهی ثبت کردم."
        photo = "عکس‌ها رسید، ممنون. به آگهی‌تون اضافه‌شون می‌کنم. " + line
        armed = {"text": line, "photo_text": photo, "check": {"listing": self.ad.pk, "fields": {"size_m2": "70.0"}}}
        self.say("سلام", 9)
        with model_says_(intent="hello"):
            first = self.answer()
        from outreach import agent as _agent

        _agent.reply_sent(first["turn_id"], ok=True, telegram_message_id=5003)
        OutreachMessage.objects.filter(pk=self.message.pk).update(extra={"on_next_inbound": armed})
        self.say("مرسی")
        with model_says_(intent="thanks"):
            self.assertIsNone(self.answer())
        self.message.refresh_from_db()
        self.assertFalse(self.message.extra["on_next_inbound"].get("done"))
        from outreach import agent
        from outreach.models import OutreachTurn

        for message_id in (2, 3):
            found = agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", message_id, "", True)
        OutreachTurn.objects.filter(pk=found.turn.pk).update(reply_due_at=timezone.now() - timedelta(seconds=1))
        with patch("outreach.agent.transcript"):
            reply = self.answer()
        self.assertTrue(reply["text"].startswith("عکس‌ها رسید، ممنون."), reply["text"])
        self.assertIn(line, reply["text"])
        self.assertEqual(reply["text"].count("عکس‌ها رسید"), 1)
        self.message.refresh_from_db()
        self.assertEqual(self.message.extra["on_next_inbound"]["outcome"], "photo")


def model_says_(**data):
    from tests.test_outreach_agent import model_says

    return model_says(**data)


class Audit1715Tests(AgentTestCase):
    """Audit 2026-11-10 17:15 (conv 26): one house in three copies, «کدوم آگهی‌تون؟» in a chat about one ad."""

    def copies(self):
        from home.models import HomeListing

        HomeListing.objects.filter(pk=self.ad.pk).update(country="DE", city="Berlin", area="Eichwalde", price_amount=None)
        a = self.listing(price_amount=1100)
        b = self.listing(price_amount=1100)
        HomeListing.objects.filter(pk__in=(a.pk, b.pk)).update(country="DE", city="Berlin", area="Eichwalde", status="active", canonical=None)
        HomeListing.objects.filter(pk=b.pk).update(area="15732Eichwalde")
        self.ad.refresh_from_db()
        return HomeListing.objects.get(pk=a.pk), HomeListing.objects.get(pk=b.pk)

    def test_a_glued_postcode_is_not_part_of_the_place(self) -> None:
        from home.contact_text import unglue_postcode
        from outreach import actions

        self.assertEqual(unglue_postcode("15732Eichwalde"), "Eichwalde")
        self.assertEqual(unglue_postcode("Adalbertstraße10179"), "Adalbertstraße10179")
        self.assertTrue(actions.fill_field(self.ad, "area", "15732Eichwalde"))
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.area, "Eichwalde")

    def test_answers_go_to_the_chat_listing_without_a_which(self) -> None:
        """T154/T155: three copies; the fill went to 6923, not 7048 whose link he holds."""
        a, b = self.copies()
        self.say("سلام")
        with model_says_(intent="hello"):
            self.answer()
        self.say("قیمتش ۱۱۰۰ یوروئه", 2)
        with model_says_(intent="field", field="price", value="1100", listing="L2"):
            reply = self.answer()
        self.assertNotIn("کدوم آگهی", reply["text"])
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.price_amount, 1100)

    def test_copies_that_read_the_same_are_one_choice(self) -> None:
        """T154: «برلین، ۱۱۰۰ یورو، Eichwalde» twice and «برلین» for Eichwalde."""
        from outreach import completion

        a, b = self.copies()
        self.assertEqual(completion.label(a), "آیشوالده، ۱۱۰۰ یورو")
        self.assertEqual(completion.label(b), "آیشوالده، ۱۱۰۰ یورو")
        self.assertEqual(len(completion.distinct([a, b])), 1)
        text = completion.which_text([self.ad, a, b])
        self.assertEqual(text.count("آیشوالده"), 2)
        self.assertNotIn("برلین", text)

    def test_the_same_posters_copy_is_merged_into_the_one_we_wrote_about(self) -> None:
        from home import review
        from home.models import HomeListing, ListingStatus

        HomeListing.objects.filter(pk=self.ad.pk).update(area="Eichwalde", price_amount=None)
        copy = self.listing(price_amount=1100)
        HomeListing.objects.filter(pk=copy.pk).update(area="15732Eichwalde", status=ListingStatus.ACTIVE, canonical=None, size_m2=self.ad.size_m2)
        from home.models import HomePost

        HomePost.objects.filter(pk__in=(self.ad.post_id, copy.post_id)).update(author_id=5551001)
        copy.refresh_from_db()
        self.assertEqual(review.same_poster_copy(copy).pk, self.ad.pk)
        review.link_duplicate(copy)
        copy.refresh_from_db()
        self.ad.refresh_from_db()
        self.assertEqual((copy.status, copy.canonical_id), (ListingStatus.DUPLICATE, self.ad.pk))
        self.assertEqual(self.ad.status, ListingStatus.ACTIVE)
        self.assertEqual(self.ad.price_amount, 1100)  # the copy's price moves to the one we keep

    def test_an_agents_two_units_stay_apart(self) -> None:
        from home import review
        from home.models import HomeListing, HomePost, ListingStatus

        other = self.listing(price_amount=1400)
        HomeListing.objects.filter(pk=other.pk).update(area=self.ad.area, size_m2=self.ad.size_m2, status=ListingStatus.ACTIVE, canonical=None)
        HomePost.objects.filter(pk__in=(self.ad.post_id, other.post_id)).update(author_id=5551001)
        other.refresh_from_db()
        self.assertIsNone(review.same_poster_copy(other))  # a different price is a different unit

    def test_an_armed_text_is_dropped_when_we_wrote_after_it(self) -> None:
        from outreach.models import OutreachMessage, OutreachTurn

        self.say("سلام")
        with model_says_(intent="hello"):
            self.answer()
        last = OutreachTurn.objects.order_by("-pk").first().pk
        held = "قیمت ۱۱۰۰ یورو و متراژ ۷۰ متر رو هم تو آگهی‌تون ثبت کردم."
        armed = {"text": held, "media_text": held + " عکس‌ها هم اضافه شد.", "check": {"listing": self.ad.pk}, "after_turn": last}
        OutreachMessage.objects.filter(pk=self.message.pk).update(extra={"on_next_inbound": armed})
        self.say("یه سؤال داشتم", 2)
        with model_says_(intent="other"):
            reply = self.answer()
        self.assertTrue(reply["text"].endswith(held), reply["text"])  # the photos still wait: the held text
        self.message.refresh_from_db()
        self.assertEqual(self.message.extra["on_next_inbound"]["outcome"], "held")
        armed["after_turn"] = last - 100
        OutreachMessage.objects.filter(pk=self.message.pk).update(extra={"on_next_inbound": armed})
        self.say("یه سؤال دیگه", 3)
        with model_says_(intent="other"):
            reply = self.answer()
        # The mocked model says the same answer again: its lines were sent already, so nothing new may go (audit
        # 2026-11-11 12:15); either way the stale armed text is not in it.
        self.assertNotIn(held, (reply or {}).get("text", ""))
        self.message.refresh_from_db()
        self.assertEqual(self.message.extra["on_next_inbound"]["outcome"], "dropped:stale")


class Audit1915Tests(AgentTestCase):
    """Audit 2026-11-10 19:15: rooms in a shared basement (#335) and a 2-bedroom flat let monthly in Cologne (#336)."""

    P335 = (
        "اتاق‌های بیسمنت برای اجاره\nآشپزخانه کوچک مشترک\nنزدیک قطار هوایی\n۴ دقیقه پیاده‌روی تا ایستگاه اتوبوس که شما را در ۶ دقیقه "
        "به ایستگاه مرکزی بورکیتلم می‌رساند\n🎓 نزدیک به کالج‌ها و دانشگاه‌های بزرگ: • دانشگاه سایمون فریزر (SFU) – پردیس برنابی "
        "• کالج داگلاس – پردیس کوکیتلام • BCIT – پردیس برنابی\n• مبله. حمام مشترک تمیز\nاجاره هراتاق با تخت دابل برای یک نفر660"
    )
    P336 = (
        "🏡 اجاره آپارتمان مبله در کلن | Köln\n📍 Köln – Steinstraße\n✅ ۱۰۰ مترمربع، ۲ اتاق خواب\n✅ پذیرایی بزرگ ۴۵ متری\n"
        "✅ اجاره کوتاه‌مدت یا ماهانه با امکان تمدید\n💶 اجاره ماهانه: ۱,۲۵۰ یورو"
    )

    def post(self, text: str, **listing) -> None:
        from home.models import HomeListing, HomePost

        HomeListing.objects.filter(pk=self.ad.pk).update(**listing)
        HomePost.objects.filter(pk=self.ad.post_id).update(text=text)
        self.ad.refresh_from_db()

    def p1_photo(self, country: str, city: str) -> str:
        from django.utils import timezone

        from outreach import housing
        from outreach.models import OutreachMessage

        message = OutreachMessage.objects.create(kind=OutreachKind.P1, listing=self.ad, country=country, city=city, token="h" * 16, text="x", recipient_name="Leila")
        with patch("outreach.housing.p1_variant", return_value=("photo", 0)):
            return housing.render(message, timezone.now())

    def test_rooms_in_a_shared_basement_are_a_room(self) -> None:
        from outreach import housing

        self.post(self.P335, country="CA", city="Vancouver", property_type="basement", rooms=1, area="")
        self.assertEqual(housing._type_word(self.ad), "اتاق")
        self.post("اجاره هر اتاق بیسمنت ۷۰۰ دلار", property_type="basement", rooms=1)
        self.assertEqual(housing._type_word(self.ad), "اتاق")
        self.post("بیسمنت ۱ خواب با آشپزخانه و حمام جدا، ورودی مستقل", property_type="basement", rooms=1)
        self.assertEqual(housing._type_word(self.ad), "بیسمنت ۱ خوابه")

    def test_a_campus_name_is_not_the_town(self) -> None:
        from outreach import housing

        self.post(self.P335, country="CA", city="Vancouver", property_type="basement", rooms=1, area="Burnaby")
        self.assertTrue(housing.town_only_landmark(self.ad))
        text = self.p1_photo("CA", "Vancouver")
        self.assertNotIn("برنابی", text)
        self.assertIn("آگهی اتاق‌تون", text)
        self.assertNotIn("خوابه", text)
        self.post("اتاق مبله در برنابی، نزدیک مترو تاون، ۹۰۰ دلار", area="Burnaby")
        self.assertFalse(housing.town_only_landmark(self.ad))
        self.post("اتاق مبله، ۹۰۰ دلار", area="Burnaby")
        self.assertFalse(housing.town_only_landmark(self.ad))  # not named at all: nothing to judge

    def test_a_german_post_that_counts_bedrooms_says_bedrooms(self) -> None:
        self.post(self.P336, country="DE", city="Cologne", property_type="apartment", rooms=2, area="Steinstraße", rental_mode="short_term")
        text = self.p1_photo("DE", "Cologne")
        self.assertIn("آگهی آپارتمان ۲ خوابه‌تون تو کلن", text)
        self.assertNotIn("اتاقه", text)

    def test_a_short_stay_is_named_only_without_a_monthly_let(self) -> None:
        from outreach import housing

        self.post(self.P336, country="DE", city="Cologne", property_type="apartment", rooms=2, area="Steinstraße", rental_mode="short_term")
        self.assertFalse(housing.only_short_stays(self.ad))
        self.assertNotIn("اقامت کوتاه", self.p1_photo("DE", "Cologne"))
        self.post("#اجاره تخت جهت اقامت یک یا ۲ شب #مناسب برای خانم ها", country="CA", city="Montreal", property_type="room", rental_mode="short_term", rooms=None, area="")
        self.assertTrue(housing.only_short_stays(self.ad))


class Audit2015Tests(AgentTestCase):
    """Audit 2026-11-10 20:15: a town known only through the listing's home area (#338 post 111132, #339 post 111022)."""

    P338 = (
        "اجاره آپارتمان بیسمنت دوخوابه نوساز\nیک واحد بیسمنت دوخوابه، نوساز و تمیز، واقع در پشت مرکز خرید آپر کانادا مال "
        "(Upper Canada Mall) جهت اجاره موجود است.\n💰 اجاره ماهیانه: ۱۹۰۰ دلار + هزینه یوتیلیتی"
    )
    P339 = (
        "🏡 اجاره بیسمنت  واک اوت در کینگ سیتی، ریچموند هیل\nیک واحد بیسمنت دوخوابه، کاملاً مبله و آماده سکونت برای اجاره موجود است.\n"
        "💰 اجاره ماهانه: ۱٬۹۰۰ دلار\n📍 موقعیت: کینگ سیتی، ریچموند هیل"
    )

    def area(self, canonical: str, fa: str):
        from home.models import HomeArea

        return HomeArea.objects.get_or_create(country="CA", city="Toronto", canonical=canonical, defaults={"fa": fa, "group": "شمال"})[0]

    def p1_photo(self, text: str, **listing) -> str:
        from django.utils import timezone

        from home.models import HomeListing, HomePost
        from outreach import housing
        from outreach.models import OutreachMessage

        HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", property_type="basement", rooms=2, **listing)
        HomePost.objects.filter(pk=self.ad.post_id).update(text=text)
        self.ad.refresh_from_db()
        message = OutreachMessage.objects.create(kind=OutreachKind.P1, listing=self.ad, country="CA", city="Toronto", token=f"m{OutreachMessage.objects.count():015d}", text="x", recipient_name="Mahnaz")
        with patch("outreach.housing.p1_variant", return_value=("photo", 0)):
            return housing.render(message, timezone.now())

    def test_a_mall_resolved_to_its_town_names_the_town(self) -> None:
        text = self.p1_photo(self.P338, city="Toronto", area="Upper Canada Mall", home_area=self.area("newmarket", "نیومارکت"))
        self.assertIn("آگهی بیسمنت ۲ خوابه‌تون تو نیومارکت رو", text)
        self.assertNotIn("تورنتو", text)

    def test_king_city_is_named_and_never_the_group_city(self) -> None:
        text = self.p1_photo(self.P339, city="Richmond Hill", area="King City", home_area=self.area("king city", "کینگ سیتی"))
        self.assertIn("آگهی بیسمنت ۲ خوابه‌تون تو کینگ سیتی رو", text)
        self.assertNotIn("تورنتو", text)
        text = self.p1_photo(self.P339, city="Richmond Hill", area="", home_area=None)
        self.assertIn("تو ریچموند هیل", text)  # no town known: the listing's own city, not the group's
        self.assertNotIn("تورنتو", text)

    def test_a_district_of_the_city_is_not_a_town(self) -> None:
        text = self.p1_photo("بیسمنت دوخوابه نورث یورک", city="Toronto", area="Yonge and Finch", home_area=self.area("north york", "نورث یورک"))
        self.assertIn("تو تورنتو", text)


class Audit2215Tests(AgentTestCase):
    """Audit 2026-11-10 22:15 (#342, post 111381): a weekly price never reads as monthly, and a weekly-only let says so."""

    P342 = (
        "#اجاره كوتاه مدت\n🏠 #نورث_ونکوور\n🏡 منزل مسکونی ۲ خوابه کاملا بازسازی‌ شده(مبله)_ نورث ونکوور(بلوریج)\n"
        "●۲ اتاق خواب\n●حدود ۱۱۰۰ فوت مربع\nاجاره: 1175$ هفتگي + نصف اجاره سكيوريتی دپازيت"
    )

    def setUp(self) -> None:
        super().setUp()
        from home.models import HomeListing, HomePost

        HomeListing.objects.filter(pk=self.ad.pk).update(
            country="CA", city="Vancouver", area="North Vancouver", property_type="house", rooms=2, price_amount=1175, price_currency="CAD",
            price_period="week", rental_mode="short_term",
        )
        HomePost.objects.filter(pk=self.ad.post_id).update(text=self.P342)
        self.ad.refresh_from_db()

    def test_the_demand_text_names_the_week_and_the_short_stay(self) -> None:
        from django.utils import timezone

        from outreach import housing
        from outreach.models import OutreachMessage

        self.assertEqual(housing.ad_desc(self.ad), "خونه‌ی ۲ خوابه‌تون برای اقامت کوتاه (هفته‌ای ۱۱۷۵ دلار)")
        message = OutreachMessage.objects.create(kind=OutreachKind.P1, listing=self.ad, country="CA", city="Vancouver", token="w" * 16, text="x", recipient_name="F Saraei")
        with patch("outreach.housing.p1_variant", return_value=("demand", 6)):
            text = housing.render(message, timezone.now())
        self.assertIn("آگهی خونه‌ی ۲ خوابه‌تون برای اقامت کوتاه (هفته‌ای ۱۱۷۵ دلار) تو نورث ونکوور رو", text)

    def test_each_period_is_written_and_a_month_is_bare(self) -> None:
        from home.models import HomeListing
        from outreach import housing

        for period, want in (("night", "شبی ۱۱۷۵ دلار"), ("month", "۱۱۷۵ دلار"), ("", "")):
            HomeListing.objects.filter(pk=self.ad.pk).update(price_period=period)
            self.ad.refresh_from_db()
            self.assertEqual(housing.listing_price(self.ad), want, period)

    def test_a_let_that_also_runs_monthly_is_not_a_short_stay(self) -> None:
        from home.models import HomePost
        from outreach import housing

        self.assertTrue(housing.only_short_stays(self.ad))
        HomePost.objects.filter(pk=self.ad.post_id).update(text=self.P342 + "\nاجاره ماهانه هم ممکنه")
        self.ad.refresh_from_db()
        self.assertFalse(housing.only_short_stays(self.ad))


class Audit0136Tests(AgentTestCase):
    """Audit 2026-11-11 01:36 (conv 27, T160 to T162): the who line and the intro said the same thing, and the link
    came as a second message."""

    T160 = "سلام من آشنایی ندارم با این تیم میشه لطف کنید بیشتر توضیح بدید"

    def test_they_do_not_know_us_one_intro_with_the_link(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation, OutreachTurn

        for _ in range(6):
            OutreachTurn.objects.all().delete()
            OutreachConversation.objects.all().delete()
            self.say(self.T160)
            with model_says_(intent="who"):
                handed = agent.next_reply("niloofar")
            text = handed["text"]
            self.assertEqual(OutreachTurn.objects.filter(direction="out").count(), 1, text)
            self.assertEqual(text.count("جمع می"), 1, text)
            self.assertEqual(len(__import__("re").findall(r"https?://", text)), 1, text)
            self.assertNotIn("توضیح کوتاه", text)
            self.assertNotIn("معرفی کوتاه", text)
            self.assertNotIn("شاید چمدون رو نشناسید", text)
            self.assertFalse(text.startswith(agent.WHO_PLAIN), text)

    def test_the_intro_opener_is_dropped_only_when_they_asked(self) -> None:
        from outreach import agent

        self.assertTrue(agent.asked_about("hello", self.T160))
        self.assertFalse(agent.asked_about("hello", "سلام، بله آگهی هنوز هست"))
        for intro in agent.INTRO_TEXTS.values():
            body = agent.intro_body(intro)
            self.assertNotIn("توضیح کوتاه", body)
            self.assertNotIn("معرفی کوتاه", body)
            self.assertFalse(body.startswith("راستی"), body)
            self.assertTrue(body.startswith(("چمدون", "ما ")), body)
        self.assertEqual(agent.lead_for_intro(agent.WHO_PLAIN), "")
        self.assertEqual(agent.lead_for_intro(agent.WHO_FIRST), "چمدون گروه نیست و لازم نیست جایی عضو بشید.")
        self.assertEqual(agent.lead_for_intro(agent.YES_US), agent.YES_US)


class BotHandleTests(AgentTestCase):
    """Mohammad 2026-11-11: every chat gets the bot handle and the poster's own ad link; a question is never «thanks»
    (conv 29 T177)."""

    T177 = "سلام ممنون. برای اینکار چارج میکنید؟"

    def test_a_fee_question_is_answered_not_closed_as_thanks(self) -> None:
        from outreach import agent

        self.assertEqual(agent.settle_intent("thanks", self.T177), "fee")
        self.assertEqual(agent.settle_intent("thanks", "ممنون، کی می‌تونم ببینمش؟"), "other")
        self.assertEqual(agent.settle_intent("thanks", "ممنونم"), "thanks")
        self.assertEqual(agent.settle_intent("thanks", "مرسی 🙏"), "thanks")
        self.say(self.T177)
        with model_says_(intent="thanks"):
            intro = agent.next_reply("niloofar")
            agent.reply_sent(intro["turn_id"], ok=True, telegram_message_id=801)
            reply = agent.next_reply("niloofar")
        self.assertIsNotNone(reply)
        self.assertIn(agent.FEE_ANSWER, reply["text"])
        self.assertIn(f"_l{self.ad.pk}", reply["text"])
        self.assertIn(agent.bot_handle(), intro["text"])
        self.assertEqual((intro["text"] + reply["text"]).count(agent.bot_handle()), 1)

    def test_the_chat_does_not_go_quiet_without_the_bot_and_the_ad_link(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation, OutreachTurn

        self.say("سلام")
        with model_says_(intent="hello"):
            first = self.answer(with_intro=True)
        self.assertIn(agent.bot_handle(), first["text"])
        conversation = OutreachConversation.objects.get()
        # A chat that never had the bot (before this rule): a bare thanks gets the handle and the link, once.
        OutreachTurn.objects.filter(conversation=conversation, direction="out").update(text="سلام")
        agent.reply_sent(first["turn_id"], ok=True, telegram_message_id=901)
        while (left := agent.next_reply("niloofar")) is not None:
            agent.reply_sent(left["turn_id"], ok=True, telegram_message_id=902)
        OutreachTurn.objects.filter(conversation=conversation, direction="out").update(text="سلام")
        self.say("ممنونم", 3)
        with model_says_(intent="thanks"):
            handed = agent.next_reply("niloofar")
        self.assertIsNotNone(handed)
        self.assertIn(agent.bot_handle(), handed["text"])
        self.assertIn(f"_l{self.ad.pk}", handed["text"])
        agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=903)
        self.say("مرسی", 4)
        with model_says_(intent="thanks"):
            self.assertIsNone(agent.next_reply("niloofar"))

    def test_every_video_carries_the_bot_and_the_ad_link(self) -> None:
        from outreach import agent, videos

        self.message.listing = self.ad
        caption = videos.caption_for(self.message)
        self.assertTrue(caption.startswith(videos.CAPTION))
        self.assertIn(agent.bot_handle(), caption)
        self.assertIn(f"_l{self.ad.pk}", caption)


class Audit0317Tests(AgentTestCase):
    """Audit 2026-11-11 03:17: conv 27 T180 (link does not open), conv 29 T177 (fee), #216 (town from an alias)."""

    T180 = "لینک باز نمیشه برای من"
    T177 = "سلام ممنون. برای اینکار چارج میکنید؟"
    P216 = "رومیت خانم\nواحد در طبقه گرند لول یک هوس در منطقه   هایلند نورث ونکوور\nفرنیش\nسرویس مجزا\nماهانه ۱۰۵۰$"

    def test_a_link_that_does_not_open_gets_how_to_open_it(self) -> None:
        from outreach import agent

        for intent in ("other", "thanks", "hello"):
            self.assertEqual(agent.settle_intent(intent, self.T180), "link_broken")
        self.assertEqual(agent.settle_intent("other", "the link doesn't open"), "link_broken")
        self.assertEqual(agent.settle_intent("other", "آگهی رو دیدم، ممنون"), "other")
        self.say("سلام")
        with model_says_(intent="hello"):
            self.answer()
        self.say(self.T180, 2)
        with model_says_(intent="other"):
            reply = self.answer()
        self.assertIn("نسخه‌های قدیمی تلگرام", reply["text"])
        self.assertIn(agent.bot_handle(), reply["text"])
        self.assertNotIn("همین‌جا بنویسید", reply["text"])

    def test_a_fee_question_is_never_thanks(self) -> None:
        from outreach import agent

        self.assertEqual(agent.settle_intent("thanks", self.T177), "fee")
        self.assertEqual(agent.settle_intent("thanks", "ممنون، رایگانه دیگه"), "fee")
        self.assertEqual(agent.settle_intent("thanks", "ممنون، قیمتش ۱۰۵۰ هست"), "thanks")

    def test_the_town_the_post_names_beats_an_alias(self) -> None:
        from home.models import HomeArea, HomeListing, HomePost
        from outreach import housing

        burnaby = HomeArea.objects.get_or_create(country="CA", city="Vancouver", canonical="burnaby", defaults={"fa": "برنابی", "group": "x"})[0]
        HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", city="Vancouver", area="Highland North", home_area=burnaby)
        HomePost.objects.filter(pk=self.ad.post_id).update(text=self.P216)
        self.ad.refresh_from_db()
        self.assertEqual(housing.town_of(self.ad), "نورث ونکوور")
        # The post names the home area's own town too, or two towns: the stored area stands.
        HomePost.objects.filter(pk=self.ad.post_id).update(text=self.P216 + "\nنزدیک برنابی")
        self.ad.refresh_from_db()
        self.assertNotEqual(housing.post_town(self.ad), "نورث ونکوور")
        HomePost.objects.filter(pk=self.ad.post_id).update(text="اتاق تو برنابی، ماهانه ۹۰۰")
        self.ad.refresh_from_db()
        self.assertEqual(housing.post_town(self.ad), "")


class Audit0350Tests(AgentTestCase):
    """Audit 2026-11-11 03:50, conv 30: «سلام بله موجوده» then «من نورث هستم البته» after a first message that said
    «تو برنابی». The answer opens with the correction (rules 32 and 56)."""

    def setUp(self) -> None:
        super().setUp()
        from home.models import HomeListing
        from outreach.models import OutreachMessage

        HomeListing.objects.filter(pk=self.ad.pk).update(country="CA", city="Vancouver", area="North Vancouver (Highlands)", home_area=None)
        OutreachMessage.objects.filter(pk=self.message.pk).update(text="سلام Maryam جان، آگهی اتاق‌تون (۱۰۵۰ دلار) تو برنابی رو رایگان تو چمدون هم گذاشتیم.")
        self.ad.refresh_from_db()
        self.message.refresh_from_db()

    def test_a_town_named_in_the_burst_opens_the_reply_with_the_correction(self) -> None:
        from outreach import agent

        self.say("سلام بله موجوده")
        self.say("من نورث هستم البته", 2)
        with model_says_(intent="available", listing="L1"):
            first = agent.next_reply("niloofar")
        self.assertTrue(first["text"].startswith("ببخشید، تو پیام اول اشتباه برنابی نوشته بودم؛ درستش کردم و آگهی‌تون الان تو نورث ونکوور ثبت شده."), first["text"])

    def test_the_same_town_or_a_said_correction_adds_nothing(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        self.say("سلام بله موجوده، برنابی هست")
        conversation = OutreachConversation.objects.get()
        self.assertEqual(agent.town_fix_line(conversation, "سلام بله موجوده، برنابی هست", [self.ad]), "")
        self.assertEqual(agent.town_fix_line(conversation, "سلام بله موجوده", [self.ad]), "")
        self.assertEqual(agent._towns_in("من نورث هستم", "Toronto"), [])


class Audit0416Tests(AgentTestCase):
    """Audit 2026-11-11 04:16, #348 (listing 6921): a WG offered as an apartment, «Alt-Glienicke/Grunau» unresolved,
    «Sahll Karimzada» greeted by name."""

    P6921 = "سلام دوستان کسی خانه مشترک WG\nنیاز داره\nآپارتمان بسیار آرام است.\nکرایه ماهانه: ۵۵۰ یورو"

    def test_a_shared_flat_is_a_room(self) -> None:
        from home.models import HomeListing, HomePost
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(country="DE", city="Berlin", property_type="apartment", rooms=None)
        for text in (self.P6921, "Zimmer in Wohngemeinschaft frei", "یک اتاق در آپارتمان مشترک"):
            HomePost.objects.filter(pk=self.ad.post_id).update(text=text)
            self.ad.refresh_from_db()
            self.assertEqual(housing._type_word(self.ad), "اتاق", text)
        HomePost.objects.filter(pk=self.ad.post_id).update(text="آپارتمان دو اتاقه، Bewegung")
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "آپارتمان")

    def test_a_hyphenated_area_resolves(self) -> None:
        from home.areas import AreaIndex
        from home.models import HomeArea, HomeChannel

        row = HomeArea.objects.create(country="DE", city="Berlin", canonical="altglienicke", fa="آلت‌گلینیکه", group="x")
        index = AreaIndex(HomeChannel(country="DE", city="Berlin"), [row])
        self.assertEqual(index.match("Alt-Glienicke/Grunau", "Berlin"), row)

    def test_an_unknown_latin_first_name_is_not_used(self) -> None:
        from outreach import templates

        self.assertEqual(templates.valid_name("Sahll Karimzada"), "")
        self.assertEqual(templates.valid_name("Sahil Karimzada"), "Sahil" if "sahil" in __import__("outreach.first_names", fromlist=["KNOWN"]).KNOWN else "")
        self.assertEqual(templates.valid_name("Maryam Taheri"), "Maryam")
        self.assertEqual(templates.valid_name("مریم طاهری"), "مریم")


class Audit0820Tests(AgentTestCase):
    """Audit 2026-11-11 08:20, #353 (listing 7209, post 112924): review guessed rooms=1 from 35 m2 and the message said
    «آپارتمان یک اتاقه»; a room count is printed only when the post or the chat states it (rule 65)."""

    P112924 = "اجاره آپارتمان مبله در Alt-Moabit برلین / آپارتمان ۳۵ متری با بالکن"

    def test_an_inferred_room_count_is_not_printed(self) -> None:
        from home.models import HomeListing, HomePost
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(country="DE", city="Berlin", property_type="apartment", rooms=1, size_m2=35)
        HomePost.objects.filter(pk=self.ad.post_id).update(text=self.P112924)
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "آپارتمان")
        self.assertNotIn("اتاقه", housing.ad_desc(self.ad))
        self.assertNotIn("اتاقه", housing.teaser(self.ad))

    def test_a_stated_room_count_is_printed(self) -> None:
        from home.models import HomeListing, HomePost
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(country="DE", city="Berlin", property_type="apartment", rooms=2)
        for text in ("آپارتمان دو اتاقه ۵۰ متری", "2-Zimmer-Wohnung, 50 m2", "Wohnung 2 Zi. frei"):
            HomePost.objects.filter(pk=self.ad.post_id).update(text=text)
            self.ad.refresh_from_db()
            self.assertEqual(housing._type_word(self.ad), "آپارتمان دو اتاقه", text)

    def test_a_count_told_in_the_chat_is_printed(self) -> None:
        from home.models import HomeListing, HomePost
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(country="DE", city="Berlin", property_type="apartment", rooms=1, flags={"rooms_told": True})
        HomePost.objects.filter(pk=self.ad.post_id).update(text=self.P112924)
        self.ad.refresh_from_db()
        self.assertEqual(housing._type_word(self.ad), "آپارتمان یک اتاقه")


class Audit1019Tests(AgentTestCase):
    """Audit 2026-11-11 10:19, conv 31 T190-T193: a photo, «سلام ممنون», «فقط شما؟», «چمدون کجاست». T194 put the intro
    first and the photo thanks on line 3, and left «فقط شما؟» unread (rules 31, 35, 56)."""

    def burst(self, *texts):
        from outreach import agent

        agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 190, "", True)
        last = None
        for message_id, text in enumerate(texts, start=191):
            last = agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", message_id, text, False)
        OutreachTurn.objects.filter(conversation__isnull=False).update(reply_due_at=timezone.now() - timedelta(seconds=1))
        return last

    def test_media_ack_then_where_answer_then_intro(self) -> None:
        from outreach import agent

        self.burst("سلام ممنون", "چمدون کجاست")
        with model_says_(intent="who", listing="L1"), patch("outreach.agent.transcript"):
            handed = agent.next_reply("niloofar")
        lines = handed["text"].split("\n")
        self.assertTrue(lines[0].startswith("عکس رسید، ممنون"), handed["text"])
        self.assertEqual(lines[1], agent.WHERE_FIRST, handed["text"])
        self.assertIn("startapp=", lines[2] + lines[3] if len(lines) > 3 else lines[2], handed["text"])
        self.assertTrue(handed["text"].rstrip().endswith(agent.bot_line(False)), handed["text"])

    def test_an_unread_question_holds_the_reply(self) -> None:
        from outreach import agent

        self.burst("سلام ممنون", "فقط شما؟", "چمدون کجاست")
        with model_says_(intent="who", listing="L1"), patch("outreach.agent.transcript"), patch("outreach.agent.tell_mohammad") as told:
            handed = agent.next_reply("niloofar")
        self.assertIsNone(handed)
        self.assertTrue(told.called)
        held = OutreachTurn.objects.filter(action_detail__blocked="uncovered_question")
        self.assertEqual(held.count(), 1)
        self.assertFalse(OutreachTurn.objects.filter(direction=TurnDirection.OUT).exists())

    def test_where_is_chamedoon_is_a_mini_app(self) -> None:
        from outreach import agent

        self.assertEqual(agent.who_lead("other", "چمدون کجاست"), agent.WHERE_FIRST)
        self.assertEqual(agent.who_lead("hello", "سلام"), "")


class Audit1215Tests(AgentTestCase):
    """Audit 2026-11-11 12:15, conv 31: T196 «عکس رسید، ممنون. به آگهی‌تون اضافه‌ش می‌کنم.» was T194's third line word
    for word. No line of an earlier message is sent again; a later media ack says «این عکس هم رسید»."""

    def test_a_line_already_sent_is_dropped(self) -> None:
        earlier = ["سلام\nعکس رسید، ممنون. به آگهی‌تون اضافه‌ش می‌کنم.\nاینم لینک آگهی‌تون: https://t.me/x?startapp=a"]
        self.assertEqual(guard.check("عکس رسید،  ممنون. به آگهی‌تون اضافه‌ش می‌کنم.\nقیمت رو هم بگید؟", earlier=earlier), "قیمت رو هم بگید؟")
        with self.assertRaises(guard.Blocked):
            guard.check("عکس رسید، ممنون. به آگهی‌تون اضافه‌ش می‌کنم.\nسلام", earlier=earlier)
        # A link line may go again.
        self.assertIn("https://t.me/x?startapp=a", guard.check("بفرمایید لینک:\nاینم لینک آگهی‌تون: https://t.me/x?startapp=a", earlier=earlier))

    def test_the_second_media_ack_is_not_the_first(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 190, "", True)
        conversation = OutreachConversation.objects.get()
        turn = conversation.turns.get()
        first = agent.media_ack(conversation, turn)
        self.assertEqual(first, "عکس رسید، ممنون. به آگهی‌تون اضافه‌ش می‌کنم.")
        OutreachTurn.objects.create(conversation=conversation, direction=TurnDirection.OUT, text="سلام\n" + first + "\nلینک", status="done", telegram_message_id=94)
        second_in = agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 195, "", True)
        second = agent.media_ack(conversation, second_in.turn)
        self.assertEqual(second, "این عکس هم رسید، ممنون. به آگهی‌تون اضافه‌ش می‌کنم.")
        self.assertEqual(guard.check(second, earlier=[t.text for t in conversation.turns.filter(direction=TurnDirection.OUT)]), second)


class Audit1615Tests(AgentTestCase):
    """Audit 2026-11-11 16:15 UTC: #362 «repost» for one cross-post burst and one of two towns (rule 68); conv 32 T208
    video 73 s after the intro, repeating its link and bot line."""

    def test_one_cross_post_burst_is_not_a_repost(self) -> None:
        from datetime import datetime, timezone as tz

        from outreach.housing import reposted

        at = datetime(2026, 11, 11, 15, 35, 15, tzinfo=tz.utc)
        burst = [(pk, "7994df55c399", at + timedelta(seconds=s)) for pk, s in ((114274, 0), (114316, 1), (114334, 1), (114267, 2), (114309, 62))]
        self.assertFalse(reposted(burst))
        days = [(pk, "7994df55c399", at - timedelta(days=d)) for d, pk in enumerate((114309, 114267, 114334, 114316, 114274))]
        self.assertTrue(reposted(sorted(days, key=lambda row: row[2])))

    def test_two_towns_of_the_post_are_both_named(self) -> None:
        from home.models import HomePost

        from outreach.housing import both_towns

        listing = self.listing(area="Richmond Hill, Markham")
        HomePost.objects.filter(pk=listing.post_id).update(text="خونه تو یکی از مناطق خوب ریچموندهیل و مارکام، نزدیک ویکتوریا اسکوئر")
        listing.refresh_from_db()
        self.assertEqual(both_towns(listing), "ریچموند هیل و مارکام")
        HomePost.objects.filter(pk=listing.post_id).update(text="خونه تو ریچموند هیل")
        listing.refresh_from_db()
        self.assertEqual(both_towns(listing), "")

    def test_the_video_waits_for_their_next_message_and_repeats_nothing(self) -> None:
        from outreach import agent, completion, videos
        from outreach.models import OutreachConversation, OutreachMessage

        self.say("Salam azizam \nBefarmaEd")
        conversation = OutreachConversation.objects.get()
        OutreachMessage.objects.filter(pk=self.message.pk).update(recipient_telegram_id=conversation.telegram_user_id)
        self.message.refresh_from_db()
        bot = agent.bot_line(False)
        t207 = "\n".join(
            [
                "عکس‌ها رسید، ممنون. به آگهی‌تون اضافه‌شون می‌کنم.",
                f"آگهی‌تون تو برلین اینجاست: {completion.view_link(self.ad, self.message.token)}",
                agent.INTRO_TEXTS["HO3"],
                bot,
            ]
        )
        OutreachTurn.objects.create(conversation=conversation, direction=TurnDirection.OUT, text=t207, status="done", telegram_message_id=207, action="about")
        self.assertTrue(videos._waits_after_intro(conversation))
        self.assertEqual(videos.caption_for(self.message), videos.CAPTION)
        self.say("Mamnoon \nNegah mikonam", 209)
        self.assertFalse(videos._waits_after_intro(conversation))


class Audit1615HouseWordTests(AgentTestCase):
    """Audit 2026-11-11 16:15 #362: a 3-bedroom house in Canada is «خونه‌ی ۳ خوابه», not «۳ خوابه»."""

    def test_a_house_says_house(self) -> None:
        from home.models import HomePost, PropertyType

        from outreach.housing import _type_word

        listing = self.listing(area="Richmond Hill", property_type=PropertyType.HOUSE, rooms=3)
        HomePost.objects.filter(pk=listing.post_id).update(text="خونه ۳ اتاق خواب تو ریچموندهیل")
        listing.country = "CA"
        listing.refresh_from_db(fields=["post"])
        listing.country = "CA"
        self.assertEqual(_type_word(listing), "خونه‌ی ۳ خوابه")


class Audit1715Tests(AgentTestCase):
    """Audit 2026-11-11 17:15 UTC: conv 33 T214 link with no room filter, «هزینه داره؟» not in line 1; #364 a one-night
    room asked for got S1B (rule 69)."""

    T7279 = "درود دوستان \nمن برای فردا شب ( دوروبره ساعت ده شب میرسم ) تو برلین یه اتاق لازم دارم برای یشب فرداش ساعت هشت باید برم به کارم برسم و برگردم شهرم \nکسی اگر اتاق اجاره میده ممنون میشم پیام بده بهم ."
    T7261 = "درود دوستان\nدانشجو ارشد tuberlin هستم و در برلین دنبال اتاق هستم با قابلیت ملده شدن\nممنون میشم اگر گزینه ای دارید بهم بگید\nسپاس🙏🏻"
    BURST = "اگر کمکم کنید ممنون میشم\nهزینه داره؟\nیا اگر میشه لینکش رو برام بفرستید"

    def _seeker(self, text: str):
        from home.models import HomePost

        wanted = self.listing(kind="wanted", property_type="room", price_amount=None, price_max=None, area="Berlin")
        HomePost.objects.filter(pk=wanted.post_id).update(text=text)
        wanted.refresh_from_db()
        self.message.kind = OutreachKind.S1B
        self.message.listing = wanted
        self.message.save(update_fields=["kind", "listing"])
        return wanted

    def test_a_room_seeker_link_keeps_the_room_filter(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        self._seeker(self.T7261)
        self.say("اگر کمکم کنید ممنون میشم")
        conversation = OutreachConversation.objects.get()
        self.assertEqual(agent.wanted_kind(conversation), "room")
        line = agent._own_line(conversation, [])
        self.assertIn("_t-room", line)
        self.assertIn("_t-room", agent._feed_link(conversation))

    def test_a_cost_question_anywhere_in_the_burst_is_line_one(self) -> None:
        from outreach import agent

        self.assertTrue(agent.fee_asked(self.BURST))
        self.assertEqual(agent.who_lead("other", self.BURST), agent.FEE_FIRST)
        self.assertFalse(agent.fee_asked("سپاس"))

    def test_a_one_night_stay_gets_no_seeker_message(self) -> None:
        from outreach import housing

        wanted = self._seeker(self.T7279)
        self.assertTrue(housing.few_nights(wanted))
        self.assertEqual(housing.skip_reason(self.message, timezone.now()), "short_stay")
        self.assertFalse(housing.few_nights(self._seeker(self.T7261)))
        self.assertFalse(housing.ONE_NIGHT.search("اتاق برای شبکه‌ی دانشجویی"))


class Audit1820Tests(AgentTestCase):
    """Audit 2026-11-11 18:20 #365: a basement let whole or by the room is named by the whole unit (rule 49)."""

    P114606 = (
        "🏡 اجاره بیسمنت مستقل و اتاق در نیومارکت (NEWMARKET)\n\nبیسمنت روشن، دلباز و بزرگ با ورودی کاملاً مستقل و خصوصی.\n\nمشخصات ملک:\n\n"
        "- ۲ اتاق خواب\n- ۱ حمام کامل\n- سالن پذیرایی و آشپزخانه بزرگ\n- امکان اجاره هر اتاق به‌صورت جداگانه\n- امکان اجاره کل واحد دوخوابه\n"
        "- یک جای پارک اختصاصی\n\n💰 قیمت اجاره:\n\n- کل بیسمنت: ماهانه ۱۸۰۰ دلار + یک‌سوم هزینه یوتیلیتی\n"
        "- اجاره هر اتاق به‌صورت جداگانه: ماهانه ۹۰۰ دلار + ۵۰ دلار هزینه یوتیلیتی\n"
    )

    def test_the_whole_unit_names_the_ad(self) -> None:
        from home.models import HomeListing, HomePost, PropertyType

        from outreach import housing

        listing = self.listing(area="Newmarket", property_type=PropertyType.BASEMENT, rooms=2, price_amount=1800)
        HomePost.objects.filter(pk=listing.post_id).update(text=self.P114606)
        HomeListing.objects.filter(pk=listing.pk).update(country="CA", city="Toronto", price_currency="CAD")
        listing = HomeListing.objects.get(pk=listing.pk)
        self.assertEqual(housing._type_word(listing), "بیسمنت ۲ خوابه")
        self.assertTrue(housing.ad_desc(listing).startswith("بیسمنت ۲ خوابه‌تون (۱۸۰۰"))
        # Rooms only, no whole-unit option: still «اتاق».
        HomePost.objects.filter(pk=listing.post_id).update(text="اجاره هر اتاق بیسمنت به‌صورت جداگانه، ماهانه ۹۰۰ دلار")
        self.assertEqual(housing._type_word(HomeListing.objects.get(pk=listing.pk)), "اتاق")


class Audit2015Tests(AgentTestCase):
    """Audit 2026-11-11 20:15, conv 27 T230: «اگه کسی متقاضی بود من چجوری باید بفهمم؟» got a reply whose every line was
    already sent; the dedupe blocked it and nothing went. A question is said again in new words or held and told."""

    def chat(self):
        from outreach import agent
        from outreach.models import OutreachConversation

        agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sahar", 160, "سلام، میشه بیشتر توضیح بدید؟", False)
        conversation = OutreachConversation.objects.get()
        said = "هر چیزی رو بخواید توی آگهی عوض بشه، همین‌جا بنویسید تا خودم درستش کنم."
        OutreachTurn.objects.create(conversation=conversation, direction=TurnDirection.OUT, text=said, status="done", telegram_message_id=181)
        inbound = agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sahar", 230, "و اگه کسی متقاضی بود من چجوری باید بفهمم !؟", False)
        return conversation, inbound.turn, said

    def test_a_repeated_answer_to_a_question_is_reworded(self) -> None:
        from outreach import agent

        conversation, turn, said = self.chat()
        fresh = "اگه کسی آگهی‌تون رو ببینه و بخواد، از همون آگهی مستقیم تو تلگرام بهتون پیام می‌ده."
        with patch("outreach.writer.rewrite", return_value=fresh), patch("outreach.agent.tell_mohammad") as told:
            self.assertEqual(agent._said_again(conversation, turn, said, guard.Blocked("duplicate")), fresh)
        told.assert_not_called()

    def test_still_repeated_is_held_and_told_never_silent(self) -> None:
        from outreach import agent

        conversation, turn, said = self.chat()
        with patch("outreach.writer.rewrite", return_value=said), patch("outreach.agent.tell_mohammad") as told:
            self.assertIsNone(agent._said_again(conversation, turn, said, guard.Blocked("duplicate")))
        turn.refresh_from_db()
        self.assertEqual(turn.action_detail["blocked"], "uncovered_question")
        self.assertIn("چجوری", turn.action_detail["question"])
        self.assertIn("بی‌جوابه", told.call_args[0][0])

    def test_a_blocked_turn_with_nothing_after_it_is_a_health_incident(self) -> None:
        from django.utils import timezone

        from outreach import health

        conversation, turn, _ = self.chat()
        OutreachTurn.objects.filter(pk=turn.pk).update(status="done", action="help", action_detail={"intent": "other", "blocked": "duplicate"})
        now = timezone.now()
        conversation.turns.exclude(pk=turn.pk).update(created_at=now - timedelta(hours=1))
        OutreachTurn.objects.filter(pk=turn.pk).update(created_at=now - timedelta(minutes=3))
        self.assertEqual(health.blocked_silent(now), [])
        OutreachTurn.objects.filter(pk=turn.pk).update(created_at=now - timedelta(minutes=7))
        [incident] = health.blocked_silent(now)
        self.assertEqual(incident.key, f"blocked_{turn.pk}")
        self.assertIn("duplicate", incident.facts)
        OutreachTurn.objects.create(conversation=conversation, direction=TurnDirection.OUT, text="جواب", status="done", telegram_message_id=231)
        self.assertEqual(health.blocked_silent(now), [])


class OneAccountTests(AgentTestCase):
    """Mohammad, 2026-11-11: a person is written to only by the account that opened the chat, never by two."""

    def test_a_person_with_an_account_never_gets_another(self) -> None:
        from outreach.models import OutreachAccount, OutreachMessage, OutreachStatus
        from outreach.services import claim_next_message, person_account_id

        other = OutreachAccount.objects.create(name="monis")
        later = OutreachMessage.objects.create(
            kind=OutreachKind.S2, recipient_username="Sara_B", listing=self.ad, country="DE", city="Berlin", text="سلام", token="b" * 16, status=OutreachStatus.QUEUED
        )
        self.assertEqual(person_account_id(later), self.account.pk)
        with patch("outreach.services.sending_gate", return_value=""):
            message, _ = claim_next_message(berlin_noon(), account=other)
        self.assertNotEqual(getattr(message, "pk", None), later.pk)
        later.refresh_from_db()
        self.assertEqual(later.status, OutreachStatus.QUEUED)

    def test_a_pinned_message_waits_for_its_account(self) -> None:
        from outreach.models import OutreachAccount, OutreachMessage, OutreachStatus
        from outreach.services import claim_next_message

        other = OutreachAccount.objects.create(name="monis")
        pinned = OutreachMessage.objects.create(
            kind=OutreachKind.P1Q, recipient_username="new_person", listing=self.ad, country="DE", city="Berlin", text="سلام", token="c" * 16,
            status=OutreachStatus.QUEUED, account=self.account, extra={"account_pinned": True},
        )
        with patch("outreach.services.sending_gate", return_value=""):
            message, _ = claim_next_message(berlin_noon(), account=other, other_ready=lambda _a: False)
        self.assertNotEqual(getattr(message, "pk", None), pinned.pk)
        pinned.refresh_from_db()
        self.assertEqual(pinned.status, OutreachStatus.QUEUED)


class Audit2115Tests(Audit1715Tests):
    """Audit 2026-11-11 21:15, conv 33 T236 «متاسفانه کسی پاسخ نمیده»: a seeker who says nobody answers is always
    answered on line 1, with the fresh ads of their type counted at send time."""

    def test_nobody_answers_is_matched(self) -> None:
        from outreach import agent

        for text in ("متاسفانه کسی پاسخ نمیده", "کسی جواب نمی‌ده", "هیچکس جواب ندادن", "nobody answers"):
            self.assertTrue(agent.NO_ANSWER.search(text), text)
        self.assertFalse(agent.NO_ANSWER.search("ممنون از جوابتون"))

    def test_the_line_counts_fresh_rooms_and_registration(self) -> None:
        from home.models import HomeListing

        from outreach import agent
        from outreach.models import OutreachConversation

        self._seeker(self.T7261)
        for index in range(4):
            room = self.listing(property_type="room", days_ago=1)
            HomeListing.objects.filter(pk=room.pk).update(registration="yes" if index < 3 else "no", city="Berlin", country="DE")
        self.say("متاسفانه کسی پاسخ نمیده")
        conversation = OutreachConversation.objects.get()
        line = agent.no_answer_line(conversation)
        self.assertTrue(line.startswith(agent.NO_ANSWER_LEAD))
        self.assertIn("تا اتاق تازه تو برلین اومده", line)
        self.assertIn("امکان ملده", line)
        HomeListing.objects.filter(kind="offer").update(registration="no")
        self.assertNotIn("ملده", agent.no_answer_line(conversation))
        HomeListing.objects.filter(kind="offer").delete()
        self.assertEqual(agent.no_answer_line(conversation), agent.NO_ANSWER_LEAD + " به آگهی‌های تازه‌تر پیام بدید، شانس جواب گرفتن بیشتره.")


class Audit0440Tests(AgentTestCase):
    """Audit 2026-11-12 04:40, conv 34 T239 «سلام باه حتما» to a P1-photo: classified available, answered «همچنان فعاله»
    and asked for photos again. A bare yes to the photo ask is thanked with «هر وقت فرستادید»; to the price ask, the
    price is asked once more and nothing is stored."""

    def _gap(self, variant: str) -> None:
        from outreach.models import OutreachMessage

        OutreachMessage.objects.filter(pk=self.message.pk).update(kind=OutreachKind.P1, variant=variant)
        self.message.refresh_from_db()

    def test_bare_yes(self) -> None:
        from outreach import agent

        for text in ("سلام باه حتما", "باشه حتما", "چشم", "اوکی 👍", "ok", "باشه"):
            self.assertTrue(agent.bare_yes(text), text)
        for text in ("باشه ۱۵۰۰ دلار", "حتما، فقط کی؟", "سلام", "ممنون"):
            self.assertFalse(agent.bare_yes(text), text)

    def test_a_yes_to_the_photo_ask(self) -> None:
        from outreach import agent

        self._gap("photo")
        self.say("سلام باه حتما")
        with model_says(intent="available", listing="L1"):
            intro = self.answer(with_intro=True)
            self.assertTrue(intro["text"].startswith(agent.GAP_PHOTO_YES), intro["text"])
            agent.reply_sent(intro["turn_id"], ok=True, telegram_message_id=501)
            reply = agent.next_reply("niloofar")
        both = intro["text"] + "\n" + reply["text"]
        self.assertTrue(reply["text"].startswith("اینم لینک آگهی‌تون تو چمدون:"), reply["text"])
        self.assertNotIn("همچنان فعاله", both)
        self.assertNotIn("فعال می‌مونه", both)
        self.assertEqual(both.count("عکس"), agent.GAP_PHOTO_YES.count("عکس"))

    def test_a_yes_to_the_price_ask_stores_nothing(self) -> None:
        from outreach import agent

        self._gap("price")
        price = self.ad.price_amount
        self.say("باشه")
        with model_says(intent="available", listing="L1"):
            reply = self.answer()
        self.assertIn("قیمت ماهانه‌ش چقدره؟", reply["text"])
        self.assertNotIn("همچنان فعاله", reply["text"])
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.price_amount, price)


class Audit1520Tests(Audit1715Tests):
    """Audit 2026-11-12 15:20 UTC: conv 35 a Jobcenter cap re-asked as a budget; conv 36 «سلام چشم میفرستم حتما» read
    as available; conv 37 an intro opened the first message and «ممنون» twice; #381 S1B with a known budget; #383 a
    den counted as a bedroom."""

    def test_a_jobcenter_cap_is_the_budget(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        self._seeker(self.T7261)
        self.say("سلام")
        conversation = OutreachConversation.objects.get()
        self.assertEqual(agent.stated_budget(conversation, "سلام خسته نباشید من جابسنتری هستم جابسنتر سرد  برای چهار نفر ۱۰۶۰ میده"), 1060)
        self.assertEqual(agent.stated_budget(conversation, "Jobcenter zahlt 900"), 900)
        self.assertIsNone(agent.stated_budget(conversation, "۱۲۰۰ یورو"))
        conversation.awaiting = {"budget": True}
        self.assertEqual(agent.stated_budget(conversation, "تا ۱۲۰۰ یورو"), 1200)

    def test_a_budget_answer_is_saved_with_its_band_and_not_asked_again(self) -> None:
        self._seeker(self.T7261)
        self.say("من جابسنتری هستم جابسنتر سرد برای چهار نفر ۱۰۶۰ میده")
        with model_says(intent="field", field="price", value="1060"):
            reply = self.answer()
        self.assertIn("p0-1060", reply["text"])
        self.assertNotIn("بودجه‌تون حدوداً چقدره", reply["text"])

    def test_a_send_promise_after_the_photo_ask(self) -> None:
        from outreach import agent
        from outreach.models import OutreachMessage

        self.assertTrue(agent.send_promise("سلام چشم میفرستم حتما"))
        self.assertTrue(agent.send_promise("الان میفرستم"))
        self.assertFalse(agent.send_promise("میفرستم، قیمتش چنده؟"))
        OutreachMessage.objects.filter(pk=self.message.pk).update(kind=OutreachKind.P1, variant="photo")
        self.say("سلام چشم میفرستم حتما")
        with model_says(intent="available", listing="L1"):
            intro = self.answer(with_intro=True)
        self.assertTrue(intro["text"].startswith(agent.GAP_PHOTO_YES), intro["text"])
        self.assertNotIn("همچنان فعاله", intro["text"])

    def test_the_answer_opens_the_first_message_and_thanks_once(self) -> None:
        from outreach import agent
        from outreach.models import OutreachMessage

        OutreachMessage.objects.filter(pk=self.message.pk).update(kind=OutreachKind.P1, variant="price")
        self.say("Salam $2200")
        with model_says(intent="field", listing="L1", field="price", value="2200"):
            intro = self.answer(with_intro=True)
            self.assertTrue(intro["text"].startswith("ممنون، قیمت"), intro["text"])
            agent.reply_sent(intro["turn_id"], ok=True, telegram_message_id=601)
            reply = agent.next_reply("niloofar")
        both = intro["text"] + "\n" + (reply["text"] if reply else "")
        self.assertEqual(sum(1 for line in both.split("\n") if line.startswith("ممنون")), 1, both)
        self.assertFalse(intro["text"].startswith("راستی"))

    def test_a_den_is_not_a_bedroom(self) -> None:
        from decimal import Decimal

        from home.review import den_rooms

        text = "🛏 یک اتاق خواب اصلی + یک دن (Den) با قابلیت استفاده به‌عنوان اتاق خواب دوم"
        fields = {"rooms": Decimal("2"), "summary_fa": "کاندو ۲ خوابه در ریچموند هیل"}
        den_rooms(fields, text)
        self.assertEqual(fields["rooms"], Decimal("1"))
        self.assertIn("1 خوابه با دن", fields["summary_fa"])
        one_plus = {"rooms": Decimal("2")}
        den_rooms(one_plus, "condo 1+1 near Finch")
        self.assertEqual(one_plus["rooms"], Decimal("1"))
        plain = {"rooms": Decimal("2")}
        den_rooms(plain, "آپارتمان ۲ خوابه")
        self.assertEqual(plain["rooms"], Decimal("2"))

    def test_a_known_budget_is_never_asked(self) -> None:
        from home.models import HomeListing

        from outreach import housing

        wanted = self._seeker(self.T7261)
        HomeListing.objects.filter(pk=wanted.pk).update(price_max=1100)
        self.message.kind = OutreachKind.S1B
        self.message.listing = HomeListing.objects.get(pk=wanted.pk)
        housing.render(self.message, timezone.now())
        self.assertIn(self.message.kind, (OutreachKind.S1, OutreachKind.S2))


class Audit1604Tests(HousingOutreachTestCase):
    """Audit 2026-11-12 16:04: a row whose kind changed keeps an old stored text; the claim must send the re-rendered
    text. A listing moved to another city names that city in its summary."""

    def setUp(self) -> None:
        super().setUp()
        from outreach.models import OutreachAccount, OutreachSettings

        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": True})
        OutreachAccount.objects.create(name="niloofar")

    def test_the_claim_sends_the_re_rendered_text(self) -> None:
        from outreach.accounts import claim_for_account
        from outreach.models import OutreachMessage, OutreachStatus

        self.listing(poster=person_post_fields(user_id=8, username="maryam_s", name="مریم"), kind="wanted", price_amount=None, price_max=700, area="Berlin")
        [seeker] = [message for message in self.built() if message.recipient_username == "maryam_s"]
        stale = "سلام وقت بخیر، این هفته ۱۳۸ تا آگهی خونه‌ی برلین رو از گروه‌های ایرانی یه‌جا جمع کردیم.\nاز تیم چمدونم. بودجه‌تون حدوداً چقدره تا اونایی که می‌خوره رو براتون جدا کنم؟"
        OutreachMessage.objects.filter(pk=seeker.pk).update(kind=OutreachKind.S2, text=stale, status=OutreachStatus.QUEUED)
        message, _ = claim_for_account("niloofar", berlin_noon())
        self.assertEqual(message.pk, seeker.pk)
        self.assertNotIn("بودجه‌تون حدوداً", message.text)
        self.assertIn(message.kind, (OutreachKind.S1, OutreachKind.S2))

    def test_a_moved_listing_names_its_new_city(self) -> None:
        from home.areas import summary_city

        self.assertEqual(summary_city("آپارتمان ۳ خوابه در ویِرگرن‌تسن هامبورگ", "Hamburg", "Hanover"), "آپارتمان ۳ خوابه در ویِرگرن‌تسن هانوفر")
        self.assertEqual(summary_city("", "Hamburg", "Hanover"), "")


class Audit1618Tests(AgentTestCase):
    """Audit 2026-11-12 16:18: conv 38 an album of 3 photos had items 2-3 stored empty and a singular ack; #391 a
    Gelsenkirchen room pitched Cologne; #392 a living-room spot named «اتاق» and a 1-bed apartment."""

    P117444 = "سلام دوستان .من دنبال ی اتاق هستم اطراف Gelsenkirchen،یا Bottrop ترجیحه بدونه ملد چون خونه دارم. بخاطرته مسافت محل کارم می‌خوام . لطفاً پیام بزارید .ممنون"
    P117423 = "#مونترال\n#همخانه \n\nهم‌خانه سالن یک آپارتمان یک خوابه  🚨\n\n🏡 مشخصات:\n🔹 محل: لونگوی\n🔹 فرنیشد شامل مبل و تخت\n✅ سالن ورزشی با تجهیزات کامل"

    def test_every_album_item_asks_for_its_photo_and_the_ack_is_plural(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation

        wants = [agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 581 + index, "", True).want_photo for index in range(3)]
        self.assertEqual(wants, [True, True, True])
        agent.record_inbound("niloofar", self.message.recipient_telegram_id, "sara_b", 584, "سلام وقت شما هم بخیر خیلی ممنونم", False)
        conversation = OutreachConversation.objects.get()
        turn = conversation.turns.order_by("-telegram_message_id").first()
        self.assertTrue(agent.media_ack(conversation, turn).startswith("عکس‌ها رسید"))

    def test_our_own_reply_is_not_an_answer_by_person(self) -> None:
        from outreach import agent
        from outreach.models import OutreachConversation, TurnStatus

        found = self.say("سلام")
        conversation = OutreachConversation.objects.get()
        OutreachTurn.objects.create(conversation=conversation, direction=TurnDirection.OUT, text="جواب ما", status=TurnStatus.DONE, action="help", telegram_message_id=585)
        OutreachTurn.objects.filter(pk=found.turn.pk).update(created_at=timezone.now() - timedelta(minutes=5), reply_due_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(agent.next_reply("niloofar"))
        found.turn.refresh_from_db()
        self.assertEqual(found.turn.action, "answered")

    def test_a_ruhr_town_is_not_cologne(self) -> None:
        from home.models import HomeListing, HomePost

        from outreach import housing

        wanted = self.listing(kind="wanted", property_type="room", price_amount=None, area="Gelsenkirchen")
        HomePost.objects.filter(pk=wanted.post_id).update(text=self.P117444)
        HomeListing.objects.filter(pk=wanted.pk).update(country="DE", city="Cologne")
        wanted = HomeListing.objects.get(pk=wanted.pk)
        self.assertEqual(housing.outside_town(wanted), "Gelsenkirchen")
        HomePost.objects.filter(pk=wanted.post_id).update(text="اتاق در Köln-Mülheim نزدیک Wiener Platz")
        HomeListing.objects.filter(pk=wanted.pk).update(area="Mülheim")
        self.assertEqual(housing.outside_town(HomeListing.objects.get(pk=wanted.pk)), "")

    def test_a_living_room_spot_is_hamkhoone(self) -> None:
        from home.models import HomeListing, HomePost, PropertyType
        from home.review import living_room_spot, living_spot

        from outreach import housing

        self.assertTrue(living_spot(self.P117423))
        self.assertFalse(living_spot("آپارتمان یک خوابه با سالن بزرگ و آشپزخانه، ۱۲۰۰ دلار"))
        fields = {"property_type": "apartment", "rooms": 1, "flags": {}}
        living_room_spot(fields, self.P117423)
        self.assertEqual((fields["property_type"], fields["rooms"], fields["flags"]["living_room"]), ("room", None, True))
        listing = self.listing(property_type=PropertyType.APARTMENT, rooms=1)
        HomePost.objects.filter(pk=listing.post_id).update(text=self.P117423)
        self.assertEqual(housing._type_word(HomeListing.objects.get(pk=listing.pk)), "هم‌خونه")


class Audit1716Tests(AgentTestCase):
    """Audit 2026-11-12 17:16, conv 41 T261 «لطفا لینک کانالتون رو بفرمایید من اطلاعی از چمدون ندارم»: line 1 answers
    with the city's real channel, then the intro. Admin switch actions leave a trace."""

    def test_a_channel_question_gets_the_channel_on_line_one(self) -> None:
        from home.models import HomeChannel

        from outreach import agent

        HomeChannel.objects.update_or_create(country="DE", city="Berlin", defaults={"username": "chamedoon_berlin_khane", "enabled": True})
        self.assertTrue(agent.CHANNEL_ASK.search("لطفا لینک کانالتون رو بفرمایید من اطلاعی از چمدون ندارم"))
        self.assertFalse(agent.CHANNEL_ASK.search("آگهی رو تو کانال دیدم؟"))
        self.say("لطفا لینک کانالتون رو بفرمایید من اطلاعی از چمدون ندارم")
        with model_says(intent="who"):
            intro = self.answer(with_intro=True)
        self.assertTrue(intro["text"].startswith("سلام، کانال چمدون برلین: @chamedoon_berlin_khane"), intro["text"])

    def test_switching_an_account_is_logged(self) -> None:
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        from outreach.models import OutreachAccount

        request = RequestFactory().post("/")
        request.user = type("U", (), {"username": "mohammad"})()
        model_admin = site._registry[OutreachAccount]
        model_admin.message_user = lambda *args, **kwargs: None
        model_admin.disable_selected(request, OutreachAccount.objects.filter(pk=self.account.pk))
        event = self.account.events.first()
        self.assertEqual((event.code, event.detail), ("DISABLED", "admin action by mohammad"))


class Audit2016KindCheckTests(AgentTestCase):
    """Audit 2026-11-12 20:16: #398 «اتاق برای دو هفته از ۱۵ نوامبر کسی سراغ داره؟» stored as an offer and sent a P1;
    7459 summary «21205 دلار» for a post that says $2,125."""

    P118229 = "اتاق برای دو هفته از ۱۵ نوامبر کسی سراغ داره؟"

    def test_a_question_for_a_place_is_wanted(self) -> None:
        from home.review import seeks_housing

        self.assertTrue(seeks_housing(self.P118229))
        self.assertTrue(seeks_housing("کسی اتاق داره؟ از اول دسامبر"))
        self.assertTrue(seeks_housing("سوئیت موجود دارید؟"))
        self.assertFalse(seeks_housing("اتاق مبله موجود است، ۸۰۰ یورو گرم، کسی سراغ داره بگه"))
        self.assertFalse(seeks_housing("کسی سراغ داره که گواهینامه رو چطور عوض کنم؟"))

    def test_the_kind_check_stops_a_first_message_to_a_seeker(self) -> None:
        from home.models import HomePost

        from outreach import kindcheck

        HomePost.objects.filter(pk=self.ad.post_id).update(text=self.P118229)
        self.ad.refresh_from_db()
        self.message.listing = self.ad
        self.message.extra = {}
        with patch("ai.openrouter.complete", side_effect=AssertionError("model asked")):
            self.assertFalse(kindcheck.agrees(self.message))
        self.assertEqual(self.message.extra["kind_checked"], "wanted")

    def test_a_stay_of_weeks_is_short(self) -> None:
        from home.models import HomeListing, HomePost

        from outreach import housing

        wanted = self.listing(kind="wanted", property_type="room", price_amount=None, area="Köln")
        for text, short in ((self.P118229, True), ("برای چند روز یه جا لازم دارم", True), ("از دو هفته دیگه دنبال خونه‌ی دائمی هستم", False)):
            HomePost.objects.filter(pk=wanted.post_id).update(text=text)
            self.assertEqual(housing.few_nights(HomeListing.objects.get(pk=wanted.pk)), short, text)

    def test_a_summary_number_not_in_the_post(self) -> None:
        from decimal import Decimal

        from home.review import summary_numbers

        text = "Lease takeover, 105 Harrison Garden Blvd, Rent $2,125, furnished"
        fields = {"summary_fa": "آپارتمان مبله در 105 هریسون گاردن بلوار تورنتو با اجاره 21205 دلار کانادا", "price_amount": Decimal("2125")}
        notes: list[str] = []
        summary_numbers(fields, text, notes)
        self.assertIn("اجاره 2125 دلار", fields["summary_fa"])
        self.assertIn("105 هریسون", fields["summary_fa"])
        other = {"summary_fa": "۹۹۹ متر", "price_amount": None}
        summary_numbers(other, text, notes)
        self.assertEqual(other["summary_fa"], "")
        fine = {"summary_fa": "اجاره ۲۱۲۵ دلار", "price_amount": Decimal("2125")}
        summary_numbers(fine, text, notes)
        self.assertEqual(fine["summary_fa"], "اجاره ۲۱۲۵ دلار")


class Audit2319Tests(AgentTestCase):
    """Audit 2026-11-12 23:19 #401 post 118817: a whole 3-bed short-term house named «اتاق‌تون» because its backyard
    is «shared with the lower-level suite», and no «برای اقامت کوتاه» for «Short-term rentals only»."""

    P118817 = (
        "Beautiful Fully Furnished Home for Short-Term Rent - North Vancouver\n"
        "This main-level home offers 3 spacious bedrooms, 2 bathrooms and a bright living room.\n"
        "Spacious backyard (shared with the lower-level suite)\nShort-term rentals only\nPlease send me a private message for pricing"
    )

    def _house(self, text: str):
        from home.models import HomeListing, HomePost, PropertyType

        listing = self.listing(property_type=PropertyType.HOUSE, rooms=3, price_amount=None)
        HomePost.objects.filter(pk=listing.post_id).update(text=text)
        HomeListing.objects.filter(pk=listing.pk).update(rental_mode="short_term", country="CA", city="Vancouver")
        return HomeListing.objects.get(pk=listing.pk)

    def test_a_shared_backyard_is_not_a_shared_unit(self) -> None:
        from outreach import housing

        listing = self._house(self.P118817)
        self.assertNotEqual(housing._type_word(listing), "اتاق")
        self.assertTrue(housing.only_short_stays(listing))
        room = self._house("Private room in a shared house near UBC, 900/month")
        self.assertEqual(housing._type_word(room), "اتاق")

    def test_short_or_long_term_is_not_short_only(self) -> None:
        from outreach import housing

        self.assertFalse(housing.only_short_stays(self._house("Furnished house, short or long term, message me")))

    def test_the_price_answer_keeps_its_period(self) -> None:
        from outreach import actions

        listing = self._house(self.P118817)
        self.assertTrue(actions.fill_field(listing, "price", "350 per night"))
        listing.refresh_from_db()
        self.assertEqual((int(listing.price_amount), listing.price_period), (350, "night"))
        self.assertEqual(actions.price_period_of("۲۰۰۰ دلار ماهانه"), "month")
        self.assertEqual(actions.price_period_of("۹۰۰ هفته‌ای"), "week")
        self.assertEqual(actions.price_period_of("2400"), "")


class Audit0018Tests(AgentTestCase):
    """Audit 2026-11-13 00:18: #403 a shared studio named «استودیو‌تون»; #404 «نورت ونکوور ... Canyon Blvd» named
    «ونکوور»; human claims past HUMAN_LEASE never expired while every account was gated."""

    P118914 = "Studio Share for Rent\nshared studio space for a female roommate. This is a shared studio (not a private room). You will be sharing the studio space, kitchen, and bathroom. 650/month"
    P118911 = "دوخوابه‌ نوساز در نورت ونکوور، بالای هایلند، منطقه‌ی عالی Canyon Blvd، ۲۷۵۰ دلار"

    def test_a_shared_studio_is_hamkhoone(self) -> None:
        from home.models import HomeListing, HomePost, PropertyType
        from home.review import living_spot

        from outreach import housing

        self.assertTrue(living_spot(self.P118914))
        self.assertFalse(living_spot("Bright studio for rent near Yonge, private bathroom"))
        listing = self.listing(property_type=PropertyType.STUDIO, price_amount=650)
        HomePost.objects.filter(pk=listing.post_id).update(text=self.P118914)
        self.assertEqual(housing._type_word(HomeListing.objects.get(pk=listing.pk)), "هم‌خونه")

    def test_north_vancouver_spellings_and_streets(self) -> None:
        from home.models import HomeListing, HomePost, PropertyType

        from outreach import housing

        listing = self.listing(property_type=PropertyType.APARTMENT, rooms=2, area="Canyon Blvd")
        HomePost.objects.filter(pk=listing.post_id).update(text=self.P118911)
        HomeListing.objects.filter(pk=listing.pk).update(country="CA", city="Vancouver")
        self.assertEqual(housing.town_of(HomeListing.objects.get(pk=listing.pk)), "نورث ونکوور")
        text = "سلام"
        for variant, canonical in housing.TOWN_SPELLINGS:
            text += " " + variant
        self.assertNotIn("نورت ونکوور", re.sub(r"(?<![\w])نورت ونکوور(?![\w])", "نورث ونکوور", text))

    def test_an_expired_human_claim_fails_at_the_health_check(self) -> None:
        from outreach import health
        from outreach.models import OutreachMessage, OutreachStatus, SentBy
        from outreach.services import HUMAN_LEASE

        now = timezone.now()
        OutreachMessage.objects.filter(pk=self.message.pk).update(status=OutreachStatus.SENDING, sent_by=SentBy.HUMAN, claimed_at=now - HUMAN_LEASE - timedelta(minutes=1))
        health.check(now)
        self.message.refresh_from_db()
        self.assertEqual((self.message.status, self.message.error_code), (OutreachStatus.FAILED, "human_unreported"))


class Audit0215Tests(AgentTestCase):
    """Audit 2026-11-13 02:15 #407 post 119121: a one-month stay (5 Dec to 5 Jan, «اجاره موقت») had no «برای اقامت کوتاه»."""

    P119121 = "اجاره موقت آپارتمان یک‌خوابه در برنابی\nبرای اجاره یک‌ماهه از ۵ دسامبر تا ۵ ژانویه\n۱۵۵۰ دلار برای یک ماه"

    def test_a_dated_month_is_a_short_stay(self) -> None:
        from datetime import date

        from home.models import HomeListing, HomePost, PropertyType

        from outreach import housing

        listing = self.listing(property_type=PropertyType.APARTMENT, rooms=1, price_amount=1550, area="Burnaby")
        HomePost.objects.filter(pk=listing.post_id).update(text=self.P119121)
        HomeListing.objects.filter(pk=listing.pk).update(rental_mode="short_term", country="CA", city="Vancouver", available_from=date(2026, 12, 5), available_to=date(2027, 1, 5))
        listing = HomeListing.objects.get(pk=listing.pk)
        self.assertTrue(housing.only_short_stays(listing))
        self.assertIn("برای اقامت کوتاه", housing.ad_desc(listing))
        HomePost.objects.filter(pk=listing.post_id).update(text="آپارتمان یک خوابه، ماهانه ۱۵۵۰، بلندمدت")
        HomeListing.objects.filter(pk=listing.pk).update(rental_mode="long_term", available_from=None, available_to=None, flags={})
        self.assertFalse(housing.only_short_stays(HomeListing.objects.get(pk=listing.pk)))


class Audit0616Tests(AgentTestCase):
    """Audit 2026-11-13 06:16 #413, listing 7525 post 119515: «بیسمنت یکخوابه مستقل ... یانگ و خیابان ۱۶» stored as an
    apartment, and an area ending «richmond hill» named «ریچموند» (Richmond BC) instead of «ریچموند هیل»."""

    def test_a_routed_town_is_not_cut_to_another_city(self) -> None:
        from home.areas import other_town
        from home.models import HomeChannel

        toronto, _ = HomeChannel.objects.update_or_create(country="CA", city="Toronto", defaults={"username": "chamedoon_toronto_khane", "aliases": "richmond hill, markham"})
        self.assertEqual(other_town("yonge & 16th ave, richmond hill", toronto), "")

    def test_a_basement_word_makes_an_apartment_a_basement(self) -> None:
        from home.models import HomeListing
        from home.review import apply_review
        from tests.test_home import ACCEPT, make_post

        post = make_post(self.source, text="بیسمنت یکخوابه مستقل با ورودی مجزا، موقعیت: یانگ و خیابان ۱۶، ۱۵۰۰ دلار")
        apply_review(post, {**ACCEPT, "property_type": "apartment", "rooms": 1, "price_amount": 1500, "summary_fa": "بیسمنت یک خوابه مستقل، 1500 دلار"})
        self.assertEqual(HomeListing.objects.get(post=post).property_type, "basement")


class Audit0720Tests(AgentTestCase):
    """Audit 2026-11-13 07:20 #414: S2 «nothing fits» while a matching North Vancouver offer was live (the search cut
    the country's newest 300 before matching), and «تو ونکوور» for a post asking for North and West Vancouver."""

    P119586 = "👀#متقاضی \n#نورث_ونکوور\n#وست_ونکوور\n#ماریندرایو\nمتقاضی یک بیسمنت\nیا یک اپارتمان  یک خوابه\nبودجه:تا ۲۰۰۰ دلار"

    def test_the_limit_caps_matches_not_candidates(self) -> None:
        from home.alerts import alert_from_wanted, search
        from home.models import HomeAlert, HomeListing

        wanted = self.listing(kind="wanted", property_type="room", price_amount=None, price_max=500, area="Berlin")
        fit = HomeListing.objects.get(pk=self.ad.pk)
        HomeListing.objects.filter(pk=fit.pk).update(price_amount=400, property_type="room")
        for _ in range(3):
            newer = self.listing(price_amount=1500, property_type="room")
            HomeListing.objects.filter(pk=newer.pk).update(last_seen=timezone.now())
        HomeListing.objects.filter(pk=fit.pk).update(last_seen=timezone.now() - timedelta(days=2))
        alert = HomeAlert(**alert_from_wanted(HomeListing.objects.get(pk=wanted.pk), "Berlin", telegram_id=0))
        self.assertEqual([item.pk for item in search(alert, limit=1)], [fit.pk])

    def test_the_towns_the_post_asks_for(self) -> None:
        from home.models import HomeListing, HomePost

        from outreach import housing

        wanted = self.listing(kind="wanted", property_type="basement", price_amount=None, price_max=2000, area="North Vancouver")
        HomePost.objects.filter(pk=wanted.post_id).update(text=self.P119586)
        self.assertEqual(housing.seeker_towns(HomeListing.objects.get(pk=wanted.pk)), "نورث ونکوور و وست ونکوور")
        HomePost.objects.filter(pk=wanted.post_id).update(text="دنبال اتاق در ونکوور هستم")
        self.assertEqual(housing.seeker_towns(HomeListing.objects.get(pk=wanted.pk)), "")


class Audit0720SendTests(AgentTestCase):
    def test_an_s2_with_a_budget_becomes_s1_when_something_fits_at_send(self) -> None:
        from home.models import HomeListing

        from outreach import housing

        wanted = self.listing(kind="wanted", property_type="room", price_amount=None, price_max=500, area="Berlin")
        HomeListing.objects.filter(pk=self.ad.pk).update(price_amount=400, property_type="room")
        self.message.kind = OutreachKind.S2
        self.message.listing = HomeListing.objects.get(pk=wanted.pk)
        self.message.extra = {}
        text = housing.render(self.message, timezone.now())
        self.assertEqual(self.message.kind, OutreachKind.S1, text)
        self.assertNotIn("الان چیزی که بخوره نیست", text)


class SaleNotRentalTests(AgentTestCase):
    """Audit 2026-11-13 07:20: 6816 (post 104129) «قیمت: ۹۵۰,۰۰۰ دلار» by a realtor was stored as 950 a month."""

    P104129 = "آپارتمان مدرن ۲ خوابه ساخت ۲۰۲۴ در نورث ونکوور\nقیمت: ۹۵۰,۰۰۰ دلار\nشارژ ماهانه ۵۹۵\nبرای سرمایه‌گذاری هم مناسب است\nRealtor: 88 West Realty"

    def test_a_sale_is_never_a_rental(self) -> None:
        from home.models import HomeListing
        from home.review import apply_review, for_sale
        from tests.test_home import ACCEPT, make_post

        self.assertTrue(for_sale(self.P104129))
        self.assertFalse(for_sale("اجاره ماهانه ۱,۲۰۰ دلار، مشاور املاک نیستم"))
        self.assertFalse(for_sale("کاندو ۲ خوابه، اجاره ۲,۹۰۰ دلار ماهانه"))
        post = make_post(self.source, text=self.P104129)
        apply_review(post, {**ACCEPT, "property_type": "apartment", "rooms": 2, "price_amount": 950, "summary_fa": "آپارتمان ۲ خوابه، 950 دلار"})
        self.assertFalse(HomeListing.objects.filter(post=post).exists())

    def test_a_cheap_two_bed_in_canada_waits_for_a_person(self) -> None:
        from home.models import HomeListing
        from home.review import apply_review
        from tests.test_home import ACCEPT, make_post
        from home.models import HomeSource

        from tests.test_home_publish import ca_source

        source = HomeSource.objects.filter(username="RentToronto").first() or ca_source()
        post = make_post(source, message_id=77, text="آپارتمان دو خوابه، 850 دلار، تورنتو")
        apply_review(post, {**ACCEPT, "country": "CA", "city": "Toronto", "price_currency": "CAD", "property_type": "apartment", "rooms": 2, "price_amount": 850, "summary_fa": "آپارتمان دو خوابه، 850 دلار"})
        self.assertTrue(HomeListing.objects.get(post=post).needs_review)


class Audit1115KindCheckTests(AgentTestCase):
    def test_a_generic_home_seeker_is_housing(self) -> None:
        from home.models import HomeListing, HomePost

        from outreach import kindcheck

        wanted = self.listing(kind="wanted", property_type="other", price_amount=None, price_max=None, area="")
        HomePost.objects.filter(pk=wanted.post_id).update(text="سلام دنبال خونه هستم اگر کسی سراغ داره \nمرسی")
        HomeListing.objects.filter(pk=wanted.pk).update(rooms=None, area="")
        self.message.kind = OutreachKind.S1B
        self.message.listing = HomeListing.objects.get(pk=wanted.pk)
        self.message.extra = {"post_housing": True}
        self.assertFalse(kindcheck.not_housing(self.message))
class S1FewTeaserTests(SimpleTestCase):
    def test_one_fit_is_not_one_of_them(self) -> None:
        from outreach import templates

        text, _ = templates.s1("", "Vancouver", budget=2000, currency="CAD", count=1, teaser="۲ خوابه تو نورث ونکوور، ۱۹۰۰ دلار")
        self.assertNotIn("یکیش", text)
        many, _ = templates.s1("", "Vancouver", budget=2000, currency="CAD", count=4, teaser="۲ خوابه تو نورث ونکوور، ۱۹۰۰ دلار")
        self.assertIn("یکیش", many)


class Audit1620Tests(AgentTestCase):
    """Audit 2026-11-13 16:20 #419 post 120997: «نزدیک مترو مثل بروسارد، داون‌تاون، مک‌گیل یا کونکوردیا» are
    examples; the matcher kept only Brossard and the text named it alone."""

    P120997 = "سلام، من دنبال یه اتاق مبله خصوصی هستم. بودجه‌ام حداکثر ۷۰۰ دلار در ماهه. نزدیک مترو مثل بروسارد، داون‌تاون، مک‌گیل یا کونکوردیا. من دانشجو هستم."

    def test_example_places_are_not_a_limit(self) -> None:
        from home.models import HomeListing, HomePost

        from outreach import housing

        wanted = self.listing(kind="wanted", property_type="room", price_amount=None, price_max=700, area="Brossard")
        HomePost.objects.filter(pk=wanted.post_id).update(text=self.P120997)
        wanted = HomeListing.objects.get(pk=wanted.pk)
        self.assertTrue(housing.example_places(wanted))
        self.assertEqual(housing.seeker_towns(wanted), "")
        HomePost.objects.filter(pk=wanted.post_id).update(text="دنبال اتاق در بروسارد هستم، تا ۷۰۰")
        self.assertFalse(housing.example_places(HomeListing.objects.get(pk=wanted.pk)))


class Audit1620SpotTests(AgentTestCase):
    def test_a_room_seeker_is_not_offered_a_living_room_spot(self) -> None:
        from home.models import HomeChannel, HomeListing

        from outreach import housing

        HomeChannel.objects.get_or_create(country="DE", city="Berlin", defaults={"username": "berlin_t"})
        wanted = self.listing(kind="wanted", property_type="room", price_amount=None, price_max=700, area="")
        HomeListing.objects.filter(pk=self.ad.pk).update(price_amount=600, property_type="room", flags={"living_room": True})
        room = self.listing(price_amount=650, property_type="room")
        found = [item.pk for item in housing.matches_for(HomeListing.objects.get(pk=wanted.pk), HomeChannel.objects.get(country="DE", city="Berlin"))]
        self.assertIn(room.pk, found)
        self.assertNotIn(self.ad.pk, found)


class Audit1650Tests(SimpleTestCase):
    def test_living_room_rent_and_room_wording(self) -> None:
        from home.review import living_spot

        from outreach import templates

        self.assertTrue(living_spot("اجاره لیوینگ‌روم به یک خانم در آپارتمان یک‌خوابه فرنیشد"))
        self.assertFalse(living_spot("آپارتمان دو خوابه با لیوینگ روم بزرگ، ۱۸۰۰ دلار"))
        many, _ = templates.s1("", "Montreal", budget=700, currency="CAD", count=5, teaser="اتاق تو Ridgewood Ave، ۶۹۰ دلار", what="اتاق")
        self.assertIn("۵ تا اتاق تو مونترال", many)
        one, _ = templates.s1("", "Montreal", budget=700, currency="CAD", count=1, teaser="x", what="خونه")
        self.assertIn("خونه‌ای پیدا کردیم", one)


class Audit1820Tests2(AgentTestCase):
    """Audit 2026-11-13 18:20: #424 a ground-floor 1-bed with «یک انباری در زیرزمین» stored as a basement; #422
    «کسی همخونه خانم نیاز نداره؟» stored as an offer."""

    P7603 = "در جستجوی مستأجر جدید هستیم.\nمتراژ: حدود ۴۰ متر مربع\nیک اتاق خواب\nیک اتاق نشیمن\nیک تراس\nیک انباری در زیرزمین\nواقع در طبقه همکف"

    def test_a_basement_storage_room_is_not_a_basement_unit(self) -> None:
        from home.models import HomeListing
        from home.review import apply_review
        from tests.test_home import ACCEPT, make_post

        post = make_post(self.source, text=self.P7603)
        apply_review(post, {**ACCEPT, "property_type": "apartment", "rooms": 1, "price_amount": 930, "summary_fa": "آپارتمان یک خوابه، 930 یورو"})
        self.assertEqual(HomeListing.objects.get(post=post).property_type, "apartment")
        other = make_post(self.source, message_id=2, text="بیسمنت یکخوابه مستقل با ورودی مجزا، ۱۵۰۰")
        apply_review(other, {**ACCEPT, "property_type": "apartment", "rooms": 1, "price_amount": 1500, "summary_fa": "بیسمنت، 1500"})
        self.assertEqual(HomeListing.objects.get(post=other).property_type, "basement")

    def test_offering_herself_as_a_flatmate_is_wanted(self) -> None:
        from home.review import seeks_housing

        self.assertTrue(seeks_housing("سلام دوستان کسی همخونه خانم نیاز نداره ؟که مکان نزدیک دانشگاه کونکوردیا باشه و اتاق جداگانه باشه"))


class Audit1820WordTests(AgentTestCase):
    def test_a_living_room_line_is_not_a_room_let(self) -> None:
        from home.models import HomeListing, HomePost, PropertyType

        from outreach import housing

        listing = self.listing(property_type=PropertyType.APARTMENT, rooms=1)
        HomePost.objects.filter(pk=listing.post_id).update(text=Audit1820Tests2.P7603)
        self.assertNotEqual(housing._type_word(HomeListing.objects.get(pk=listing.pk)), "اتاق")
