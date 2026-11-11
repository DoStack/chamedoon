from __future__ import annotations

import base64
import io
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from home.models import HomeListing, HomeMedia, ListingStatus, MediaStatus
from outreach import agent, completion
from outreach.accounts import claim_for_account
from outreach.actions import parse_date
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
from outreach.photos import attach_chat_photo
from outreach.services import build_outreach_queue
from tests.test_outreach_agent import AgentTestCase, model_says
from tests.test_outreach_housing import POSTER_ID, HousingOutreachTestCase, berlin_noon, person_post_fields


def jpeg() -> str:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), "red").save(out, "JPEG")
    return base64.b64encode(out.getvalue()).decode("ascii")


class FakeStorage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def put(self, key, data, content_type="image/jpeg", **_kwargs):
        self.files[key] = data

    def url(self, key):
        return f"https://storage.test/{key}"


def chat(account: OutreachAccount, first: OutreachMessage) -> OutreachConversation:
    return OutreachConversation.objects.create(telegram_user_id=POSTER_ID, username="sara_b", account=account, first_message=first)


class GapTests(HousingOutreachTestCase):
    def test_gaps_follow_the_impact_order_and_use_real_columns(self) -> None:
        ad = self.listing(price_amount=None, property_type="apartment")
        self.assertEqual(completion.gaps(ad)[:3], ["photo", "price", "rooms"])
        self.assertIn("bills", completion.gaps(ad))
        HomeListing.objects.filter(pk=ad.pk).update(price_amount=900, rooms=2)
        ad.refresh_from_db()
        self.assertEqual(completion.core_gaps(ad), ["photo"])

    def test_a_room_is_not_asked_for_its_rooms(self) -> None:
        ad = self.listing(property_type="room")
        self.assertNotIn("rooms", completion.gaps(ad))

    def test_one_thing_per_message_with_a_true_line_and_no_exit_line(self) -> None:
        text = completion.ask_text(["photo", "price", "rooms"])
        self.assertIn("عکس", text)
        self.assertNotIn("قیمت", text)
        self.assertNotIn("اتاق", text)
        self.assertNotIn("برابر", text)
        self.assertNotIn("لغو", text)

    def test_dates_people_write(self) -> None:
        today = date(2026, 11, 5)
        self.assertEqual(parse_date("اول دسامبر", today), date(2026, 12, 1))
        self.assertEqual(parse_date("۱۵ ژانویه", today), date(2027, 1, 15))
        self.assertEqual(parse_date("الان خالیه", today), today)
        self.assertEqual(parse_date("2026-12-20", today), date(2026, 12, 20))
        self.assertIsNone(parse_date("نمی‌دونم", today))


class OperatorTests(AgentTestCase):
    def setUp(self) -> None:
        super().setUp()
        OutreachSettings.objects.filter(pk=1).update(agent_enabled=False)
        HomeListing.objects.filter(pk=self.ad.pk).update(price_amount=None, rooms=None, property_type="apartment")
        self.ad.refresh_from_db()
        self.chat = chat(self.account, self.message)

    def test_a_price_is_saved_on_their_ad_and_the_next_gap_is_given(self) -> None:
        applied = completion.apply_values(self.chat, self.ad.pk, {"price": "۱۲۰۰ دلار"}, by="operator:test")
        self.assertTrue(applied.ok)
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.price_amount, 1200)
        self.assertIn("عکس", applied.next)
        self.message.refresh_from_db()
        self.assertEqual(self.message.extra["completion"]["filled"], ["price"])

    def test_someone_elses_ad_is_never_touched(self) -> None:
        other = self.listing(poster=person_post_fields(user_id=777, username="other_one", name="Ali"), price_amount=None)
        applied = completion.apply_values(self.chat, other.pk, {"price": "900"}, by="operator:test")
        self.assertEqual((applied.ok, applied.error), (False, "not_their_ad"))
        other.refresh_from_db()
        self.assertIsNone(other.price_amount)

    def test_several_ads_are_named_by_city_and_price_before_anything(self) -> None:
        second = self.listing(price_amount=1500)
        line = completion.start_line(self.chat)
        self.assertTrue(line.startswith("کدوم آگهی‌تون؟"))
        self.assertIn("برلین، بدون قیمت", line)
        self.assertIn("برلین، ۱۵۰۰ یورو", line)
        self.assertEqual(sorted(self.chat.awaiting["listings"]), sorted([self.ad.pk, second.pk]))

    def test_done_thanks_with_the_view_link_of_that_ad_and_its_city(self) -> None:
        applied = completion.finish(self.chat, self.ad.pk, by="operator:test")
        self.assertTrue(applied.next.startswith("ممنون، آگهی‌تون الان کامل شد"))
        self.assertIn(f"_l{self.ad.pk}", applied.next)
        self.assertIn("home_de_berlin", applied.next)
        self.assertIn(f"src-o-{self.message.token}", applied.next)
        self.message.refresh_from_db()
        self.assertTrue(self.message.extra["completion"]["done_at"])

    def test_the_last_core_gap_filled_gives_the_thanks(self) -> None:
        HomeListing.objects.filter(pk=self.ad.pk).update(rooms=2)
        with patch("outreach.completion.has_photo", return_value=True):
            applied = completion.apply_values(self.chat, self.ad.pk, {"price": "1200"}, by="operator:test")
        self.assertTrue(applied.next.startswith("ممنون، آگهی‌تون الان کامل شد"))

    def test_command_sets_and_shows(self) -> None:
        out = io.StringIO()
        call_command("outreach_complete", "set", f"#c{self.chat.pk}", "rooms", "۲", "--listing", str(self.ad.pk), stdout=out)
        self.assertIn("saved: rooms", out.getvalue())
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.rooms, 2)
        out = io.StringIO()
        call_command("outreach_complete", "show", "@sara_b", stdout=out)
        self.assertIn(f"ad {self.ad.pk}", out.getvalue())

    def test_admin_page_saves_a_field(self) -> None:
        admin = get_user_model().objects.create_superuser(username="op", email="op@example.com", password="x")
        self.client.force_login(admin)
        url = reverse("admin:outreach_outreachconversation_complete", args=[self.chat.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"op": "fill", "listing": self.ad.pk, "field": "price", "value": "950"})
        self.assertEqual(response.status_code, 200)
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.price_amount, 950)


class PhotoTests(AgentTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.storage = FakeStorage()
        self.patcher = patch("outreach.photos.get_storage", return_value=self.storage)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        storage_patch = patch("home.storage.get_storage", return_value=self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        self.chat = chat(self.account, self.message)

    def photo_turn(self, message_id: int = 50) -> OutreachTurn:
        return OutreachTurn.objects.create(conversation=self.chat, direction=TurnDirection.IN, telegram_message_id=message_id, has_photo=True)

    def test_with_the_agent_off_a_photo_waits_for_the_operator(self) -> None:
        OutreachSettings.objects.filter(pk=1).update(agent_enabled=False)
        turn = self.photo_turn()
        result = attach_chat_photo(turn.pk, jpeg())
        self.assertTrue(result["held"])
        self.assertFalse(HomeMedia.objects.filter(post=self.ad.post).exists())
        applied = completion.attach_photos(self.chat, self.ad.pk, [turn.pk], by="operator:test")
        self.assertTrue(applied.ok)
        media = HomeMedia.objects.get(post=self.ad.post)
        self.assertEqual(media.status, MediaStatus.STORED)
        self.assertTrue(completion.has_photo(self.ad))

    def test_the_agent_attaches_only_what_the_check_calls_housing(self) -> None:
        with patch("outreach.photos.check_photo", return_value="not_housing"):
            dropped = attach_chat_photo(self.photo_turn(51).pk, jpeg())
        self.assertTrue(dropped["dropped"])
        with patch("outreach.photos.check_photo", return_value="unsure"):
            held = attach_chat_photo(self.photo_turn(52).pk, jpeg())
        self.assertTrue(held["held"])
        with patch("outreach.photos.check_photo", return_value="housing"):
            attached = attach_chat_photo(self.photo_turn(53).pk, jpeg())
        self.assertEqual(attached["listing"], self.ad.pk)
        self.assertEqual(HomeMedia.objects.filter(post=self.ad.post).count(), 1)

    def test_in_a_chat_about_one_ad_photos_never_ask_which(self) -> None:
        """Audit 2026-11-10 17:15 (T154): the chat's own ad takes the photos, whatever else they have."""
        self.listing(price_amount=1500)
        from outreach.photos import _target

        turn = self.photo_turn(54)
        self.assertEqual(_target(turn).pk, self.ad.pk)
        self.assertTrue(attach_chat_photo(turn.pk, jpeg())["held"])  # the vision check is unsure here: a person looks
        OutreachTurn.objects.filter(pk=turn.pk).update(status="pending", reply_due_at=timezone.now() - timedelta(seconds=1))
        reply = self.answer(with_intro=True)  # the photo thanks and the intro go as one message
        self.assertTrue(reply["text"].startswith("عکس رسید"))
        self.assertNotIn("کدوم آگهی", reply["text"])

    def test_with_several_ads_the_agent_asks_which_then_attaches_there(self) -> None:
        # A chat with no ad of its own (audit 2026-11-10 17:15: only then is «which» asked).
        OutreachMessage.objects.filter(pk=self.message.pk).update(listing=None)
        self.chat.first_message.listing_id = None
        second = self.listing(price_amount=1500)
        turn = self.photo_turn(54)
        self.assertTrue(attach_chat_photo(turn.pk, jpeg())["held"])
        OutreachTurn.objects.filter(pk=turn.pk).update(status="pending", reply_due_at=timezone.now() - timedelta(seconds=1))
        first = self.answer(with_intro=True)
        # The photo is thanked first, on line 1 of the first message out, then the question (rules 22 and 35,
        # audit 2026-11-11 10:19).
        self.assertTrue(first["text"].startswith("عکس رسید"))
        if "کدوم آگهی‌تون؟" in first["text"]:
            reply = first
        else:
            agent.reply_sent(first["turn_id"], ok=True, telegram_message_id=first["turn_id"] + 10_000)
            reply = agent.next_reply("niloofar")
        self.assertIn("کدوم آگهی‌تون؟", reply["text"])
        self.assertFalse(HomeMedia.objects.exists())
        self.say("اونی که ۱۵۰۰ ـه", 55)
        listings = completion.live_ads(POSTER_ID)
        ref = f"L{[item.pk for item in agent_listings(self.chat)].index(second.pk) + 1}"
        with model_says(intent="which", listing=ref), patch("outreach.photos.check_photo", return_value="housing"):
            reply = self.answer()
        self.assertTrue(reply["text"].startswith("عکس‌ها اضافه شد"))
        self.assertEqual(HomeMedia.objects.get().post_id, second.post_id)
        self.assertEqual(len(listings), 2)


def agent_listings(conversation):
    from outreach.agent import _listings

    return _listings(conversation)


class AgentCompletionTests(AgentTestCase):
    def test_several_facts_in_one_answer_are_all_saved(self) -> None:
        HomeListing.objects.filter(pk=self.ad.pk).update(price_amount=None, rooms=None, property_type="apartment")
        self.say("۱۲۰۰ یورو، دو خوابه، از اول دسامبر")
        with model_says(intent="field", field="price", value="1200", values={"price": "1200", "rooms": "2", "available_from": "اول دسامبر"}):
            reply = self.answer()
        self.assertTrue(reply["text"].startswith("ممنون، همه رو تو آگهی ثبت کردم."))
        self.ad.refresh_from_db()
        self.assertEqual((self.ad.price_amount, self.ad.rooms, self.ad.available_from.day), (1200, 2, 1))

    def asked_rooms(self) -> OutreachConversation:
        """T124: their ad is on Chamedoon (link sent), and we asked the bedrooms."""
        HomeListing.objects.filter(pk=self.ad.pk).update(rooms=None, property_type="apartment")
        self.say("سلام بله")
        with model_says(intent="available", listing="L1"):
            first = self.answer()
        agent.reply_sent(first["turn_id"], ok=True, telegram_message_id=20_001)
        conversation = OutreachConversation.objects.get()
        conversation.awaiting = {"listing": self.ad.pk, "fields": ["rooms"], "field": "rooms"}
        conversation.save(update_fields=["awaiting"])
        return conversation

    def test_a_studio_answer_is_saved_and_named_before_anything_else(self) -> None:
        # Conv 23 (audit 2026-11-09): T129 «استدیو هستش» got T130, the ad link and «رایگان» again, and nothing saved.
        self.asked_rooms()
        self.say("استدیو هستش", 2)
        with model_says(intent="field", field="rooms", value="استدیو"):
            reply = self.answer()
        self.assertTrue(reply["text"].startswith("ممنون، استدیو بودنش رو تو آگهی ثبت کردم."))
        self.assertNotIn("https://", reply["text"])
        self.assertNotIn("رایگان", reply["text"])
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.property_type, "studio")
        self.assertNotIn("rooms", completion.gaps(self.ad))

    def test_an_answer_that_does_not_save_is_asked_once_more_without_the_link(self) -> None:
        self.asked_rooms()
        self.say("نمی‌دونم والا", 2)
        with model_says(intent="field", field="rooms", value="نمی‌دونم"):
            reply = self.answer()
        self.assertEqual(reply["text"], "ببخشید، دقیق متوجه نشدم؛ چند تا اتاق خواب داره؟")
        self.ad.refresh_from_db()
        self.assertIsNone(self.ad.rooms)
        agent.reply_sent(reply["turn_id"], ok=True, telegram_message_id=20_002)
        self.say("نمی‌دونم", 3)
        with model_says(intent="field", field="rooms", value="نمی‌دونم"):
            again = self.answer()
        # Asked once only: then the help line, and still not the link sent an hour ago.
        self.assertNotIn("https://", again["text"])
        self.assertNotIn("رایگان", again["text"])


@override_settings(OUTREACH_ENABLED_KINDS="S1,S1B,S2,P1,P1Q,P6,P1G")
class FollowupTests(HousingOutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": False})
        self.niloofar = OutreachAccount.objects.create(name="niloofar")
        self.sara = OutreachAccount.objects.create(name="sara")
        self.ad = self.listing(price_amount=None)

    def first(self, hours_ago: float) -> OutreachMessage:
        at = berlin_noon() - timedelta(hours=hours_ago)
        return OutreachMessage.objects.create(
            kind=OutreachKind.P1Q, recipient_username="sara_b", recipient_name="سارا", recipient_telegram_id=POSTER_ID, listing=self.ad,
            country="DE", city="Berlin", text="سلام", token=f"f{int(hours_ago):015d}", status=OutreachStatus.SENT, sent_at=at, claimed_at=at,
            video_sent_at=at, account=self.niloofar,
        )

    def build(self, at=None):
        return [message for message in build_outreach_queue(now=at or berlin_noon()).created if message.kind == OutreachKind.P1G]

    def test_one_held_follow_up_on_the_same_account_after_no_reply(self) -> None:
        first = self.first(hours_ago=26)
        built = self.build()
        self.assertEqual(len(built), 1)
        message = built[0]
        self.assertEqual((message.status, message.error_code, message.account), (OutreachStatus.HELD, "awaiting_yes", self.niloofar))
        self.assertEqual(message.extra["first"], first.pk)
        self.assertIn("سلام سارا جان", message.text)
        self.assertIn("عکس نداره", message.text)
        self.assertIn("«ویرایش آگهی»", message.text)
        self.assertIn("home_de_berlin", message.text)
        self.assertNotIn("لغو", message.text)
        self.assertEqual(self.build(), [])

    def test_no_follow_up_after_a_reply_a_stop_or_when_switched_off(self) -> None:
        first = self.first(hours_ago=26)
        OutreachMessage.objects.filter(pk=first.pk).update(replied_at=timezone.now())
        self.assertEqual(self.build(), [])
        OutreachMessage.objects.filter(pk=first.pk).update(replied_at=None)
        OutreachOptOut.objects.create(telegram_user_id=POSTER_ID)
        self.assertEqual(self.build(), [])
        OutreachOptOut.objects.all().delete()
        with override_settings(OUTREACH_ENABLED_KINDS="S1,S1B,S2,P1,P1Q,P6"):
            self.assertEqual(self.build(), [])

    def test_not_after_48_hours_and_not_for_a_complete_ad(self) -> None:
        self.first(hours_ago=50)
        self.assertEqual(self.build(), [])
        OutreachMessage.objects.all().delete()
        HomeListing.objects.filter(pk=self.ad.pk).update(price_amount=900, rooms=1, property_type="room")
        self.first(hours_ago=26)
        with patch("outreach.completion.has_photo", return_value=True):
            self.assertEqual(self.build(), [])

    def test_sent_only_by_its_own_account_and_never_before_24_hours(self) -> None:
        self.first(hours_ago=14)
        message = self.build()[0]
        OutreachMessage.objects.filter(pk=message.pk).update(status=OutreachStatus.QUEUED, error_code="")
        self.assertEqual(claim_for_account("niloofar", berlin_noon())[0], None)
        first = OutreachMessage.objects.get(pk=message.extra["first"])
        OutreachMessage.objects.filter(pk=first.pk).update(sent_at=berlin_noon() - timedelta(hours=25))
        self.assertEqual(claim_for_account("sara", berlin_noon())[0], None)
        claimed, reason = claim_for_account("niloofar", berlin_noon())
        self.assertEqual((claimed.pk if claimed else None, reason), (message.pk, ""))

    def test_a_reply_after_the_follow_up_belongs_to_the_first_message(self) -> None:
        from outreach import agent

        first = self.first(hours_ago=30)
        message = self.build()[0]
        OutreachMessage.objects.filter(pk=message.pk).update(status=OutreachStatus.SENT, sent_at=berlin_noon())
        found = agent.record_inbound("niloofar", POSTER_ID, "sara_b", 7, "عکس می‌فرستم", False)
        self.assertEqual(found.turn.conversation.first_message_id, first.pk)
        message.refresh_from_db()
        self.assertIsNotNone(message.replied_at)

    def test_a_closed_ad_skips_the_follow_up(self) -> None:
        self.first(hours_ago=30)
        message = self.build()[0]
        OutreachMessage.objects.filter(pk=message.pk).update(status=OutreachStatus.QUEUED, error_code="")
        HomeListing.objects.filter(pk=self.ad.pk).update(status=ListingStatus.RENTED)
        claim_for_account("niloofar", berlin_noon())
        message.refresh_from_db()
        self.assertEqual((message.status, message.error_code), (OutreachStatus.SKIPPED, "listing_closed"))
