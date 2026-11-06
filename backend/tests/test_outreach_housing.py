from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from home.models import HomeChannel, HomeChatState, HomeListing, HomePost, HomeUserPrefs, ListingStatus, PostStatus
from home.ownership import owned_listings, owns
from home.review import precheck
from outreach import templates
from outreach.accounts import claim_for_account
from outreach.housing import note_open
from outreach.models import OutreachKind, OutreachMessage, OutreachOptOut, OutreachSettings, OutreachStatus
from outreach.services import build_outreach_queue
from tests.helpers import make_user
from tests.test_home import ACCEPT, make_post, make_source
from tests.test_home_publish import listing_for

BERLIN = ZoneInfo("Europe/Berlin")
POSTER_ID = 5551001


def berlin_noon():
    return timezone.now().astimezone(BERLIN).replace(hour=12, minute=0, second=0, microsecond=0)


def person_post_fields(user_id: int = POSTER_ID, username: str = "sara_b", name: str = "سارا", via: str = "sender") -> dict:
    return {
        "poster_user_id": user_id,
        "poster_username": username,
        "poster_name": name,
        "poster_kind": "person",
        "poster_via": via,
        "poster_read_at": timezone.now(),
    }


@override_settings(
    OUTREACH_ENABLED=True,
    OUTREACH_SENDING_ENABLED=True,
    OUTREACH_ENABLED_KINDS="S1,S1B,S2,P1,P1Q,P6",
    OUTREACH_SEND_START_HOUR=10,
    OUTREACH_SEND_END_HOUR=20,
    OUTREACH_DAILY_LIMIT=10,
    OUTREACH_MAX_POST_AGE_DAYS=14,
)
class HousingOutreachTestCase(TestCase):
    def setUp(self) -> None:
        self.source = HomeSource_get()
        self.message_id = 100

    def listing(self, *, poster: dict | None = None, days_ago: float = 1, **data) -> HomeListing:
        self.message_id += 1
        # Different rooms, or review merges them as one ad.
        data.setdefault("size_m2", 10 + self.message_id % 50)
        if data.get("kind", "offer") == "offer":
            data.setdefault("price_amount", 300 + 40 * (self.message_id % 20))
        data.setdefault("area", ["Neukölln", "Kreuzberg", "Mitte", "Pankow", "Wedding", "Moabit"][self.message_id % 6])
        text = f"اتاق {data['size_m2']} متری مبله در برلین {data['area']}، اجاره گرم ۵۲۰ یورو، از اول نوامبر شماره {self.message_id}. @sara_b"
        listing = listing_for(self.source, self.message_id, text, {**ACCEPT, **data}, days_ago=days_ago)
        HomePost.objects.filter(pk=listing.post_id).update(**(poster if poster is not None else person_post_fields()))
        listing.refresh_from_db()
        return listing

    def built(self) -> list[OutreachMessage]:
        build_outreach_queue()
        return list(OutreachMessage.objects.order_by("pk"))


def HomeSource_get():
    from home.models import HomeSource

    return HomeSource.objects.filter(username="berlin_khane").first() or make_source()


class HousingBuildTests(HousingOutreachTestCase):
    def test_owner_ad_in_a_launch_city_waits_for_a_yes(self) -> None:
        listing = self.listing()
        [message] = self.built()
        self.assertIn(message.kind, {OutreachKind.P1, OutreachKind.P1Q})
        self.assertEqual(message.status, OutreachStatus.HELD)
        self.assertEqual(message.error_code, "awaiting_yes")
        self.assertEqual((message.listing, message.city, message.country), (listing, "Berlin", "DE"))
        self.assertEqual(message.recipient_telegram_id, POSTER_ID)
        self.assertIsNone(message.recipient)
        self.assertTrue(message.text.startswith("سلام سارا جان،"))
        self.assertIn("\nاز تیم چمدونم.", message.text)
        self.assertIn("برلین", message.text)
        self.assertNotIn("لغو", message.text)
        self.assertNotIn("http", message.text)
        self.assertNotIn("@", message.text)

    def test_building_twice_does_not_message_twice(self) -> None:
        self.listing()
        self.built()
        self.assertEqual(len(self.built()), 1)

    def test_forwarders_channels_and_unread_posters_get_nothing(self) -> None:
        self.listing(poster=person_post_fields(via="forwarder"))
        self.listing(poster={**person_post_fields(user_id=2, username="chan_x"), "poster_kind": "channel"})
        self.listing(poster={**person_post_fields(user_id=3, username="late_x"), "poster_read_at": None})
        self.assertEqual(self.built(), [])

    def test_no_username_means_no_message(self) -> None:
        self.listing(poster=person_post_fields(username=""))
        result = build_outreach_queue()
        self.assertEqual(result.skipped, {"no_username": 1})

    def test_a_city_outside_the_six_is_never_messaged(self) -> None:
        self.listing(city="München", area="Schwabing")
        result = build_outreach_queue()
        self.assertEqual((result.created, result.skipped), ([], {"outside_cities": 1}))

    def test_members_and_opted_out_people_are_left_alone(self) -> None:
        self.listing()
        HomeChatState.objects.create(telegram_user_id=POSTER_ID)
        self.assertEqual(build_outreach_queue().skipped, {"member": 1})
        HomeChatState.objects.all().delete()
        OutreachOptOut.objects.create(telegram_user_id=POSTER_ID)
        self.assertEqual(build_outreach_queue().skipped, {"opted_out": 1})

    def test_old_ads_are_not_used(self) -> None:
        self.listing(days_ago=20)
        self.assertEqual(self.built(), [])

    def owners(self, count: int = 3) -> None:
        for number in range(count):
            self.listing(poster=person_post_fields(user_id=70 + number, username=f"owner_{number}", name="Owner"))

    def test_seeker_with_budget_and_a_match_gets_s1(self) -> None:
        self.owners()
        self.listing(poster=person_post_fields(user_id=8, username="maryam_s", name="مریم"), kind="wanted", price_amount=None, price_max=700, area="Berlin")
        messages = {message.recipient_username: message for message in self.built()}
        seeker = messages["maryam_s"]
        self.assertEqual(seeker.kind, OutreachKind.S1)
        self.assertEqual(seeker.extra["count"], 3)
        first, second = seeker.text.split("\n")
        self.assertTrue(first.startswith("سلام مریم جان، ۳ تا خونه تو برلین پیدا کردیم که به بودجه‌ی ۷۰۰ یوروی‌تون می‌خوره؛ یکیش "))
        self.assertTrue(second.startswith("از تیم چمدونم. نیازی نیست همه‌ی گروه‌های خونه رو دونه دونه بگردید"))
        self.assertNotIn("http", seeker.text)

    def test_fewer_than_three_matches_never_says_a_number(self) -> None:
        self.owners(1)
        self.listing(poster=person_post_fields(user_id=8, username="maryam_s", name="مریم"), kind="wanted", price_amount=None, price_max=700, area="Berlin")
        seeker = {message.recipient_username: message for message in self.built()}["maryam_s"]
        self.assertEqual(seeker.kind, OutreachKind.S1B)
        self.assertTrue(seeker.text.startswith("سلام وقت بخیر، دیدم تو برلین دنبال خونه‌اید."))

    def test_seeker_without_budget_gets_the_budget_question(self) -> None:
        self.listing(poster=person_post_fields(user_id=7, username="owner_x", name="Owner"))
        self.listing(poster=person_post_fields(user_id=8, username="nima_s", name="Nima"), kind="wanted", price_amount=None, price_max=None, area="Berlin")
        seeker = OutreachMessage.objects.none()
        self.built()
        seeker = OutreachMessage.objects.get(recipient_username="nima_s")
        self.assertEqual(seeker.kind, OutreachKind.S1B)
        self.assertIn("بودجه", seeker.text)

    def test_seeker_beats_poster_for_the_same_person(self) -> None:
        self.owners()
        self.listing()
        self.listing(kind="wanted", price_amount=None, price_max=900, area="Berlin")
        messages = {message.recipient_username: message for message in self.built()}
        self.assertEqual(messages["sara_b"].kind, OutreachKind.S1)

    def test_agent_listings_get_p6_with_their_count(self) -> None:
        self.listing(lister_type="agent")
        self.listing(lister_type="agent")
        [message] = self.built()
        self.assertEqual(message.kind, OutreachKind.P6)
        self.assertEqual(message.extra["count"], 2)

    def test_kinds_switched_off_are_not_built(self) -> None:
        self.listing()
        OutreachSettings.objects.update_or_create(pk=1, defaults={"enabled_kinds": "S1,S1B,S2"})
        self.assertEqual(build_outreach_queue().skipped, {"kind_off": 1})


class HousingSendTests(HousingOutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        OutreachSettings.objects.update_or_create(pk=1, defaults={"auto_queue": True})

    def test_the_window_is_the_recipient_city_clock(self) -> None:
        self.listing()
        self.built()
        noon = berlin_noon()
        self.assertEqual(claim_for_account("sina", noon.replace(hour=7)), (None, "outside_window"))
        message, reason = claim_for_account("sina", noon)
        self.assertEqual(reason, "")
        self.assertEqual(message.status, OutreachStatus.SENDING)

    def test_a_closed_ad_is_skipped_at_send_time(self) -> None:
        listing = self.listing()
        self.built()
        HomeListing.objects.filter(pk=listing.pk).update(status=ListingStatus.RENTED)
        self.assertEqual(claim_for_account("sina", berlin_noon()), (None, "empty"))
        self.assertEqual(OutreachMessage.objects.get().error_code, "listing_closed")

    def test_s1_becomes_s1b_when_the_match_went_away(self) -> None:
        offer = self.listing(poster=person_post_fields(user_id=7, username="owner_x", name="Owner"))
        self.listing(poster=person_post_fields(user_id=8, username="maryam_s", name="مریم"), kind="wanted", price_amount=None, price_max=700, area="Berlin")
        self.built()
        OutreachMessage.objects.filter(recipient_username="owner_x").update(status=OutreachStatus.SKIPPED)
        HomeListing.objects.filter(pk=offer.pk).update(status=ListingStatus.RENTED)
        message, _reason = claim_for_account("sina", berlin_noon())
        self.assertEqual(message.kind, OutreachKind.S1B)
        message.refresh_from_db()
        self.assertEqual(message.kind, OutreachKind.S1B)
        self.assertIn("بودجه", message.text)

    def test_opening_the_link_counts_once_and_sets_the_city(self) -> None:
        self.listing()
        [message] = self.built()
        stranger = make_user(telegram_user_id=999, first_name="X", telegram_username="someone_else")
        self.assertIsNone(note_open(f"o-{message.token}", stranger))
        owner = make_user(telegram_user_id=POSTER_ID, first_name="Sara", telegram_username="sara_b")
        self.assertEqual(note_open(f"o-{message.token}", owner), message)
        message.refresh_from_db()
        self.assertIsNotNone(message.opened_at)
        self.assertIsNotNone(message.member_at)
        prefs = HomeUserPrefs.objects.get(user=owner)
        self.assertEqual((prefs.country, prefs.city), ("DE", "Berlin"))


class OwnershipTests(HousingOutreachTestCase):
    def test_only_a_verified_author_owns_a_crawled_listing(self) -> None:
        mine = self.listing()
        forwarded = self.listing(poster=person_post_fields(via="forwarder"))
        unread = self.listing(poster={**person_post_fields(), "poster_read_at": None})
        self.assertTrue(owns(mine, POSTER_ID))
        self.assertFalse(owns(forwarded, POSTER_ID))
        self.assertFalse(owns(unread, POSTER_ID))
        self.assertEqual(list(owned_listings(POSTER_ID)), [mine])


class RemovalTests(HousingOutreachTestCase):
    def test_a_person_who_asked_for_removal_is_not_imported_again(self) -> None:
        OutreachOptOut.objects.create(telegram_user_id=POSTER_ID, block_listings=True)
        post = make_post(self.source, 900, text="اتاق مبله در برلین، اجاره گرم ۵۰۰ یورو، از اول نوامبر. لطفاً پیام بدید", **person_post_fields())
        outcome = precheck(post, gate=False)
        self.assertEqual(outcome.reject_reason, "owner_removed")
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.REJECTED)


class GreetingTests(TestCase):
    def test_names(self) -> None:
        self.assertEqual(templates.valid_name("مریم احمدی"), "مریم")
        self.assertEqual(templates.valid_name("Ali"), "Ali")
        self.assertEqual(templates.valid_name("املاک تورنتو"), "")
        self.assertEqual(templates.valid_name("🏠 Rent"), "")
        self.assertEqual(templates.valid_name("sara_b", "sara_b"), "")
        self.assertEqual(templates.valid_name("Ali123"), "")
