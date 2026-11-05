from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from home.models import HomeAlert, HomeListing, HomeListingStat, HomePost, StatKind
from home.owner_posts import mine_row
from home.publish import publishable
from home.stats import owner_stats, record
from home.submit import user_listings
from item_requests.services import create_item_request
from market.models import MarketPost, MarketRole
from matching.contact import SYNTHETIC_TELEGRAM_USER_ID
from outreach.housing import confirm_alert, landing, owner_opened
from outreach.models import (
    OutreachAccount,
    OutreachConversation,
    OutreachKind,
    OutreachMessage,
    OutreachSettings,
    OutreachStatus,
)
from outreach.services import build_outreach_queue, open_outreach
from tests.helpers import make_user
from tests.test_outreach import OutreachTestCase
from tests.test_outreach_housing import POSTER_ID, HousingOutreachTestCase, person_post_fields
from tests.test_requests import SUPPLY_PAYLOAD


class StatsAndOwnershipTests(HousingOutreachTestCase):
    def test_views_count_once_per_person_and_never_the_owner(self) -> None:
        listing = self.listing()
        self.assertTrue(record(listing, 11, StatKind.VIEW))
        self.assertFalse(record(listing, 11, StatKind.VIEW))
        self.assertFalse(record(listing, POSTER_ID, StatKind.VIEW))
        record(listing, 12, StatKind.CONTACT)
        stats = owner_stats(listing)
        self.assertEqual((stats["views"], stats["contacts"], stats["views_7d"]), (1, 1, 1))

    def test_a_verified_crawled_ad_is_in_its_owners_list(self) -> None:
        listing = self.listing()
        self.listing(poster=person_post_fields(via="forwarder"))
        owner = make_user(telegram_user_id=POSTER_ID, first_name="Sara", telegram_username="sara_b")
        self.assertEqual(list(user_listings(owner)), [listing])
        row = mine_row(listing, [], None)
        self.assertTrue(row["crawled"])
        self.assertIn("views_7d", row["stats"])

    def test_a_claimed_ad_skips_the_publish_window_and_goes_first(self) -> None:
        listing = self.listing(days_ago=12)
        HomeListing.objects.filter(pk=listing.pk).update(last_seen=timezone.now() - timedelta(days=12))
        self.assertNotIn(listing, list(publishable()))
        owner = make_user(telegram_user_id=POSTER_ID, first_name="Sara", telegram_username="sara_b")
        owner_opened(listing, owner)
        self.assertEqual(list(publishable())[0], listing)
        listing.refresh_from_db()
        self.assertIsNotNone(listing.owner_claimed_at)


class LandingTests(HousingOutreachTestCase):
    def test_a_seeker_gets_a_ready_alert_that_one_tap_turns_on(self) -> None:
        self.listing(poster=person_post_fields(user_id=7, username="owner_x", name="Owner"))
        wanted = self.listing(kind="wanted", price_amount=None, price_max=700, area="Berlin")
        build_outreach_queue()
        message = OutreachMessage.objects.get(recipient_username="sara_b")
        seeker = make_user(telegram_user_id=POSTER_ID, first_name="Sara", telegram_username="sara_b")
        got = landing(f"o-{message.token}", seeker)
        self.assertTrue(got["is_recipient"])
        self.assertFalse(got["alert"]["active"])
        self.assertEqual(got["alert"]["match_count"], 1)
        self.assertTrue(got["ask_write_access"])
        alert = HomeAlert.objects.get(wanted_listing=wanted)
        confirm_alert(alert.pk, seeker)
        alert.refresh_from_db()
        self.assertTrue(alert.active)
        message.refresh_from_db()

    def test_a_forwarded_link_offers_nothing(self) -> None:
        self.listing()
        build_outreach_queue()
        message = OutreachMessage.objects.get()
        other = make_user(telegram_user_id=31, first_name="X", telegram_username="xx_other")
        self.assertFalse(landing(f"o-{message.token}", other)["is_recipient"])
        self.assertFalse(HomeAlert.objects.exists())


class FollowupTests(HousingOutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        OutreachSettings.objects.update_or_create(pk=1, defaults={"followups_enabled": True, "enabled_kinds": "S1"})
        self.account = OutreachAccount.objects.create(name="sara")
        self.ad = self.listing()
        first = OutreachMessage.objects.create(
            kind=OutreachKind.P1Q, recipient_username="sara_b", recipient_telegram_id=POSTER_ID, listing=self.ad, text="x",
            token="aa00aa00aa00aa00", status=OutreachStatus.SENT, sent_at=timezone.now(), account=self.account,
        )
        self.conversation = OutreachConversation.objects.create(telegram_user_id=POSTER_ID, username="sara_b", account=self.account, first_message=first)

    def test_day_twelve_asks_again_on_the_same_account_after_a_yes(self) -> None:
        HomeListing.objects.filter(pk=self.ad.pk).update(first_seen=timezone.now() - timedelta(days=12))
        build_outreach_queue()
        p5 = OutreachMessage.objects.get(kind=OutreachKind.P5)
        self.assertEqual((p5.conversation, p5.account, p5.status), (self.conversation, self.account, OutreachStatus.HELD))
        self.assertIn("هنوز موجوده؟", p5.text)
        build_outreach_queue()
        self.assertEqual(OutreachMessage.objects.filter(kind=OutreachKind.P5).count(), 1)

    def test_numbers_go_out_only_when_they_are_real(self) -> None:
        for person in range(4):
            record(self.ad, 100 + person, StatKind.VIEW)
        build_outreach_queue()
        self.assertFalse(OutreachMessage.objects.filter(kind=OutreachKind.P3).exists())
        record(self.ad, 200, StatKind.CONTACT)
        build_outreach_queue()
        p3 = OutreachMessage.objects.get(kind=OutreachKind.P3)
        self.assertIn("اطلاعات تماستون رو گرفتن", p3.text)
        self.assertNotIn("تماس گرفتن", p3.text.replace("اطلاعات تماستون رو گرفتن", ""))

    def test_off_means_none(self) -> None:
        OutreachSettings.objects.filter(pk=1).update(followups_enabled=False)
        HomeListing.objects.filter(pk=self.ad.pk).update(first_seen=timezone.now() - timedelta(days=12))
        build_outreach_queue()
        self.assertFalse(OutreachMessage.objects.filter(kind=OutreachKind.P5).exists())

    def test_a_contact_tells_a_bot_member_once_a_day(self) -> None:
        from home.models import HomeChatState

        HomeChatState.objects.create(telegram_user_id=POSTER_ID)
        with patch("home.bot.send_message", return_value=[1]) as sent:
            record(self.ad, 300, StatKind.CONTACT)
            record(self.ad, 301, StatKind.CONTACT)
        sent.assert_called_once()
        self.assertIn("اطلاعات تماس", sent.call_args[0][1])


@override_settings(OUTREACH_ENABLED_KINDS="C2")
class CourierWaveTests(OutreachTestCase):
    def test_an_imported_traveler_in_a_launch_city_gets_c2(self) -> None:
        traveler = make_user(telegram_user_id=SYNTHETIC_TELEGRAM_USER_ID + 77, first_name="رضا", telegram_username="reza_flies")
        trip = create_item_request(traveler, SUPPLY_PAYLOAD, imported=True, source_url="https://t.me/koolbarcanada/90", sync_channel=False)
        MarketPost.objects.create(channel_username="koolbarcanada", telegram_message_id=90, posted_at=timezone.now() - timedelta(days=1), text="سفر دارم", role=MarketRole.SUPPLY, author_username="reza_flies", item_request=trip)
        build_outreach_queue()
        message = OutreachMessage.objects.get(kind=OutreachKind.C2)
        self.assertEqual(message.courier_request, trip)
        self.assertIn("سلام رضا جان", message.text)
        self.assertIn("سفرتون از", message.text)
        real = make_user(telegram_user_id=6001, first_name="Reza", telegram_username="reza_flies")
        self.assertEqual(open_outreach(message.token, real), f"/app/requests/{trip.pk}/")
