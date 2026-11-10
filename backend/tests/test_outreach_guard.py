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
            self.assertIsNone(claim_for_account("sina", berlin_noon())[0])
        self.message.refresh_from_db()
        self.assertEqual(self.message.error_code, "unclear_post")
        self.ad.refresh_from_db()
        self.assertFalse(self.ad.needs_review)

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
        self.assertIsNotNone(agent.next_reply("sina", night))


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
            self.assertIsNone(agent.next_reply("sina"))
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
            self.assertEqual(claim_for_account("sina", berlin_noon()), (None, "model_down"))
        self.assertEqual(model.call_count, 1)
        answer = '{"kind": "offer", "places": 1, "sure": true, "housing": true}'
        with patch("ai.openrouter.complete", return_value=ChatResult(ok=True, text=answer, model="m")) as model:
            message, reason = claim_for_account("sina", berlin_noon() + timedelta(minutes=30))
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
            handed = agent.next_reply("sina")
        turn = OutreachTurn.objects.get(pk=handed["turn_id"])
        self.assertEqual(turn.action, "about")
        self.assertTrue(turn.text.startswith(agent.WHO_FIRST))
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
        turn = agent.record_outbound("sina", conversation.telegram_user_id, 421, "این ویدیو کوتاه", sent_at=sent)
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

        found = agent.record_inbound("sina", self.message.recipient_telegram_id, "sara_b", 7, "", True)
        OutreachTurn.objects.filter(pk=found.turn.pk).update(reply_due_at=timezone.now() - timedelta(seconds=1), action_detail={"photo_held": "unsure"})
        with patch("outreach.agent.transcript"):
            handed = agent.next_reply("sina")
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
            intro = agent.next_reply("sina")
            agent.reply_sent(intro["turn_id"], ok=True, telegram_message_id=901)
            reply = agent.next_reply("sina")
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
            handed = agent.next_reply("sina")
            agent.reply_sent(handed["turn_id"], ok=True, telegram_message_id=990)
            reply = agent.next_reply("sina")
        # The sorry goes in the first message out, here the intro.
        self.assertTrue(handed["text"].startswith(agent.LATE_APOLOGY))
        self.assertNotIn(agent.LATE_APOLOGY, reply["text"])

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
            agent.record_inbound("sina", self.message.recipient_telegram_id, "sara_b", message_id, "", True)
        agent.record_inbound("sina", self.message.recipient_telegram_id, "sara_b", 14, "", False, has_video=True)
        last = agent.record_inbound("sina", self.message.recipient_telegram_id, "sara_b", 15, "سلام خیلی ممنونم", False)
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
        agent.record_inbound("sina", self.message.recipient_telegram_id, "sara_b", 31, "", True)
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
        found = agent.record_inbound("sina", self.message.recipient_telegram_id, "sara_b", 61, "سلام بله موجوده", False, sent_at=sent)
        found.turn.refresh_from_db()
        self.assertLess(found.turn.created_at, timezone.now() - timedelta(minutes=100))
        OutreachTurn.objects.filter(pk=found.turn.pk).update(reply_due_at=timezone.now() - timedelta(seconds=1))
        with model_says(intent="available", listing="L1"):
            first = agent.next_reply("sina")
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
        self.assertEqual(housing._type_word(self.ad), "آپارتمان سه اتاقه")  # a German ad: Zimmer (rule 46)


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
        from home.models import HomeListing
        from outreach import housing

        HomeListing.objects.filter(pk=self.ad.pk).update(property_type="apartment", rooms=2)
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
        self.assertFalse(search(alert, limit=1))
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
        self.assertTrue(housing.area_unresolved(message))
        HomeListing.objects.filter(pk=self.ad.pk).update(area="Eichwalde")
        self.ad.refresh_from_db()
        self.assertFalse(housing.area_unresolved(message))
        HomeListing.objects.filter(pk=self.ad.pk).update(area="")
        self.ad.refresh_from_db()
        self.assertFalse(housing.area_unresolved(message))
