"""Glued usernames, the backfill crash, reposted ads, hashtags, law lines, the lease transfer name, and finishing an intro."""

from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from telethon.errors.common import TypeNotFoundError

from courier import intros
from courier.models import CourierIntro, IntroStatus
from home import channel_format
from home.botv2 import TERM_LABEL, TERM_LABEL_DE, safety_screen
from home.contact_text import handle_in_text
from home.management.commands.poster_backfill import _not_readable, read_batch
from home.models import REVIVED_FLAG, HomeArea, HomeChannel, HomeListing, HomePost, HomeSettings, HomeSource, ListingStatus, PostStatus
from home.publish import area_tag, city_publishable, hashtags, publishable, warning_items
from home.review import apply_review, precheck, revive_from_repost, why_not_revived
from home.vision import vision_candidates
from matching.models import Match, MatchRating, MatchStatus
from tests.helpers import bearer_auth
from tests.test_courier_intro_everywhere import IntroCase
from tests.test_home import ACCEPT, make_post, make_source
from tests.test_home_publish import ca_listing, de_listing


class GluedHandleTests(SimpleTestCase):
    def test_a_persian_letter_right_before_the_at_sign(self) -> None:
        for text in ("تماس@ali_home", "برای اجاره تماس＠ali_home لطفا", "تلگرام:@ali_home", "آیدی@ali_home."):
            self.assertTrue(handle_in_text("ali_home", text), text)

    def test_an_email_address_still_does_not_count(self) -> None:
        for handle, text in (("gmail", "me@gmail.com"), ("gmail", "تماس me@gmail.com"), ("gmail", "تماس@gmail.com"), ("ali_home", "sara.b@ali_home"), ("ali_home", "müller@ali_home")):
            self.assertFalse(handle_in_text(handle, text), text)


class RepairContactsTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()
        self.post = make_post(self.source, 1, text="اتاق مبله در برلین نویکلن اجاره گرم ۵۲۰ یورو از اول نوامبر تماس@ali_home")
        apply_review(self.post, {**ACCEPT, "contact_telegram": "ali_home"})
        HomeListing.objects.filter(post=self.post).update(contact_telegram="")

    def run_it(self, *args) -> dict:
        out = StringIO()
        call_command("home_repair", "contacts", *args, stdout=out)
        return json.loads(out.getvalue())

    def test_a_dry_run_counts_and_writes_nothing(self) -> None:
        report = self.run_it()
        self.assertEqual((report["would_fill"], report["written"]), (1, 0))
        self.assertEqual(HomeListing.objects.get(post=self.post).contact_telegram, "")

    def test_apply_fills_the_contact_then_finds_nothing_more(self) -> None:
        self.assertEqual(self.run_it("--apply")["written"], 1)
        self.assertEqual(HomeListing.objects.get(post=self.post).contact_telegram, "ali_home")
        self.assertEqual(self.run_it("--apply")["written"], 0)

    def test_the_report_never_names_anyone(self) -> None:
        out = StringIO()
        call_command("home_repair", "contacts", "--apply", stdout=out)
        self.assertNotIn("ali_home", out.getvalue())


class BackfillCrashTests(SimpleTestCase):
    def undecodable(self) -> TypeNotFoundError:
        return TypeNotFoundError(0x1234, b"")

    def test_an_answer_telethon_cannot_decode_reopens_the_connection_and_retries(self) -> None:
        first, second = MagicMock(), MagicMock()
        first.messages.side_effect = self.undecodable()
        second.messages.return_value = ["row"]
        box = {"reader": first, "reopen": lambda: second}
        pauses: list[float] = []
        self.assertEqual(read_batch(box, "RentToronto", [1], sleep=pauses.append), ["row"])
        first.close.assert_called_once()
        self.assertIs(box["reader"], second)
        self.assertEqual(pauses, [10])

    def test_it_gives_up_as_not_readable_instead_of_ending_the_run(self) -> None:
        reader = MagicMock()
        reader.messages.side_effect = self.undecodable()
        with self.assertRaises(TypeNotFoundError) as raised:
            read_batch({"reader": reader}, "RentToronto", [1], sleep=lambda _seconds: None)
        self.assertTrue(_not_readable(raised.exception))


class RepostRevivalTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        settings = HomeSettings.load()
        settings.review_gate_on = True
        settings.save()
        self.source = make_source()
        self.now = timezone.now()
        self.old = make_post(self.source, 1, posted_at=self.now - timedelta(days=20))
        apply_review(self.old, dict(ACCEPT))
        self.listing = HomeListing.objects.get(post=self.old)
        self.set(status=ListingStatus.EXPIRED, last_seen=self.now - timedelta(days=20))

    def set(self, **values) -> None:
        HomeListing.objects.filter(pk=self.listing.pk).update(**values)
        self.listing.refresh_from_db()

    def repost(self, number: int = 2, days_ago: int = 0) -> HomePost:
        post = make_post(self.source, number, posted_at=self.now - timedelta(days=days_ago))
        with patch("home.review.ask_model") as model:
            verdict = precheck(post)
        model.assert_not_called()
        self.assertEqual(verdict.reject_reason, "repost")
        return post

    def test_a_repost_brings_it_back_with_no_new_row_and_no_model(self) -> None:
        count = HomeListing.objects.count()
        post = self.repost()
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.ACTIVE)
        self.assertEqual(self.listing.last_seen, post.posted_at)
        self.assertIn(REVIVED_FLAG, self.listing.flags)
        self.assertEqual(HomeListing.objects.count(), count)

    def test_it_stays_alive_until_it_goes_unseen_again(self) -> None:
        from home.freshness import expire_stale

        self.repost()
        expire_stale()
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.ACTIVE)

    def test_rented_reported_and_removed_stay_closed(self) -> None:
        for status in (ListingStatus.RENTED, ListingStatus.REPORTED, ListingStatus.REMOVED):
            self.set(status=status)
            self.assertEqual(why_not_revived(self.listing, self.now), status)
            self.assertFalse(revive_from_repost(self.listing, self.now))
            self.listing.refresh_from_db()
            self.assertEqual(self.listing.status, status)

    def test_an_app_listing_is_left_to_its_owner(self) -> None:
        self.source.username = "app_ca"
        self.source.save()
        self.listing.refresh_from_db()
        self.assertEqual(why_not_revived(self.listing, self.now), "app_listing")

    def test_an_old_repost_would_only_expire_again(self) -> None:
        self.assertEqual(why_not_revived(self.listing, self.now - timedelta(days=30)), "repost_too_old")

    def test_a_live_copy_means_no_second_live_listing(self) -> None:
        other = make_post(self.source, 5, text=self.old.text + " ", posted_at=self.now - timedelta(days=30))
        HomePost.objects.filter(pk=other.pk).update(text_key=self.old.text_key, status=PostStatus.ACCEPTED)
        HomeListing.objects.create(post=other, kind="offer", country=self.listing.country, first_seen=self.now, last_seen=self.now, status=ListingStatus.ACTIVE)
        self.assertEqual(why_not_revived(self.listing, self.now), "live_copy_exists")

    def test_it_revives_the_listing_a_duplicate_points_at(self) -> None:
        keeper_post = make_post(self.source, 6, text="یک متن دیگر برای آگهی اصلی که هیچ شباهتی ندارد و فقط برای آزمون است، با طول کافی.", posted_at=self.now - timedelta(days=30))
        keeper = HomeListing.objects.create(post=keeper_post, kind="offer", country=self.listing.country, first_seen=self.now - timedelta(days=30), last_seen=self.now - timedelta(days=30), status=ListingStatus.EXPIRED)
        self.set(status=ListingStatus.DUPLICATE, canonical=keeper)
        self.repost()
        keeper.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual((keeper.status, self.listing.status), (ListingStatus.ACTIVE, ListingStatus.DUPLICATE))

    def test_no_new_channel_post_and_no_photo_reading(self) -> None:
        HomeChannel.objects.all().delete()
        HomeChannel.objects.create(username="home_de", country="DE", enabled=True)
        HomeChannel.objects.create(username="home_berlin", country="DE", city="Berlin", enabled=True)
        self.repost()
        self.listing.refresh_from_db()
        self.assertIsNone(self.listing.published_at)
        self.assertNotIn(self.listing, list(publishable()))
        self.assertNotIn(self.listing.pk, [row.pk for row, _channel in city_publishable()])
        self.assertNotIn(self.listing, list(vision_candidates()))

    def test_the_repair_counts_then_applies(self) -> None:
        post = make_post(self.source, 7, posted_at=self.now - timedelta(days=1))
        HomePost.objects.filter(pk=post.pk).update(status=PostStatus.REJECTED, reject_reason="repost", model="rule", review_json={"repost_of": self.old.pk})

        def run(*args) -> dict:
            out = StringIO()
            call_command("home_repair", "reposts", *args, stdout=out)
            return json.loads(out.getvalue())

        dry = run()
        self.assertEqual((dry["reposts"], dry["listings_behind"], dry["would_revive"], dry["never_on_a_channel"], dry["revived"]), (1, 1, 1, 1, 0))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.EXPIRED)
        self.assertEqual(run("--apply")["revived"], 1)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.ACTIVE)
        self.assertEqual(run("--apply")["revived"], 0)


class HashtagTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()

    def test_free_text_never_becomes_a_hashtag(self) -> None:
        listing = ca_listing()
        listing.city = "Montreal"
        for area in ("near Yonge and Finch", "نزدیک مترو فینچ", "Bloor St W"):
            listing.area = area
            self.assertEqual(area_tag(listing, "Montreal"), "", area)
            for text in (hashtags(listing, "Montreal"), channel_format.hashtags(listing, "Montreal")):
                self.assertNotIn("near", text)
                self.assertNotIn("مترو", text)
                self.assertNotIn("Bloor", text)
                self.assertIn("#مونترال", text)

    def test_the_city_and_a_canonical_area_remain(self) -> None:
        listing = ca_listing()
        listing.area = "free words"
        listing.home_area = HomeArea.objects.create(country="CA", city="Toronto", canonical="north york test", fa="نورث یورک تست")
        for text in (hashtags(listing, "Toronto"), channel_format.hashtags(listing, "Toronto")):
            self.assertIn("#تورنتو", text)
            self.assertIn("#نورث_یورک_تست", text)
            self.assertNotIn("free", text)

    def test_an_intersection_uses_its_district(self) -> None:
        listing = ca_listing()
        listing.area = "Yonge & Finch"
        self.assertEqual(area_tag(listing, "Toronto"), "#نورث_یورک")


class LawLineTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()

    def test_no_province_rule_in_a_post(self) -> None:
        listing = ca_listing()
        listing.region = "ON"
        listing.flags = {**listing.flags, "deposit_warning": "x"}
        self.assertFalse([item for item in warning_items(listing) if "انتاریو" in item or "کبک" in item])

    def test_the_safety_screen_keeps_only_the_general_warning(self) -> None:
        for city, country in (("Toronto", "CA"), ("Montreal", "CA"), ("Berlin", "DE")):
            text, _keyboard = safety_screen(HomeChannel(username="x", city=city, country=country))
            for word in ("انتاریو", "کبک", "Kaution", "Anmeldung"):
                self.assertNotIn(word, text)
            self.assertIn("هیچ پولی", text)


class LeaseTransferNameTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()

    def test_the_name_in_canada_and_germany(self) -> None:
        canada = ca_listing(rental_mode="lease_transfer")
        self.assertIn("🔁 <b>لیست ترانسفر</b>", channel_format.mode_line(canada))
        self.assertIn("#لیست_ترانسفر", channel_format.hashtags(canada, "Toronto"))
        self.assertIn("#لیست_ترانسفر", hashtags(canada, "Toronto"))
        self.assertEqual(TERM_LABEL["lease_transfer"], "لیست ترانسفر")
        germany = de_listing(2, rental_mode="lease_transfer")
        self.assertIn("Nachmieter", channel_format.mode_line(germany))
        self.assertEqual(TERM_LABEL_DE["lease_transfer"], "Nachmieter")


class IntroFinishTests(IntroCase):
    def accepted(self, *, requester_request=None) -> CourierIntro:
        self.trip_row = self.trip()
        return CourierIntro.objects.create(requester=self.sara, target=self.omar, target_request=self.trip_row, requester_request=requester_request, status=IntroStatus.ACCEPTED, answered_at=timezone.now())

    def finish(self, intro: CourierIntro, **kwargs):
        return self.post(f"/me/intros/{intro.pk}/complete/", **kwargs)

    def test_an_accepted_intro_says_it_can_finish(self) -> None:
        intro = self.accepted()
        body = self.get(f"/me/intros/{intro.pk}/").json()
        self.assertEqual((body["match_id"], body["finish_state"]), (None, "open"))

    def test_a_pending_intro_cannot(self) -> None:
        intro = self.accepted()
        CourierIntro.objects.filter(pk=intro.pk).update(status=IntroStatus.PENDING)
        self.assertEqual(self.get(f"/me/intros/{intro.pk}/").json()["finish_state"], "closed")
        response = self.finish(intro)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "match_not_open"))
        self.assertFalse(Match.objects.exists())

    def test_finishing_makes_the_match_with_a_hidden_side_and_closes_both(self) -> None:
        intro = self.accepted()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.finish(intro)
        self.assertEqual(response.status_code, 200)
        match = Match.objects.get()
        self.assertEqual(match.status, MatchStatus.COMPLETED)
        self.assertEqual(response.json()["id"], match.pk)
        self.assertTrue(response.json()["rating"]["can_rate"])
        body = self.get(f"/me/intros/{intro.pk}/").json()
        self.assertEqual((body["match_id"], body["finish_state"]), (match.pk, "finished"))

    def test_finishing_twice_returns_the_same_match(self) -> None:
        intro = self.accepted()
        first = self.finish(intro).json()["id"]
        self.assertEqual(self.finish(intro).json()["id"], first)
        self.assertEqual(Match.objects.count(), 1)

    def test_it_uses_the_requesters_own_request_when_there_is_one(self) -> None:
        package = self.package()
        intro = self.accepted(requester_request=package)
        self.finish(intro)
        self.assertEqual(Match.objects.get().demand_request, package)

    def test_either_side_can_finish_then_both_rate_by_the_old_rules(self) -> None:
        intro = self.accepted()
        match_id = self.finish(intro).json()["id"]
        rated = self.post(f"/me/matches/{match_id}/rate/", {"score": 5, "comment": "عالی"})
        self.assertEqual(rated.status_code, 201)
        again = self.post(f"/me/matches/{match_id}/rate/", {"score": 4})
        self.assertEqual(again.json()["code"], "already_rated")
        self.assertEqual(MatchRating.objects.get().score, 5)
        invalid = self.post(f"/me/matches/{match_id}/rate/", {"score": 9}, auth=bearer_auth(self.omar))
        self.assertEqual(invalid.status_code, 400)

    def test_a_stranger_gets_not_found(self) -> None:
        intro = self.accepted()
        CourierIntro.objects.filter(pk=intro.pk).update(requester=self.omar)
        response = self.finish(CourierIntro.objects.get(pk=intro.pk))
        self.assertEqual(response.status_code, 404)

    def test_a_target_gone_with_no_match_is_not_open(self) -> None:
        intro = self.accepted()
        self.trip_row.status = "CANCELLED"
        self.trip_row.save()
        self.assertEqual(self.get(f"/me/intros/{intro.pk}/").json()["finish_state"], "closed")
        self.assertEqual(self.finish(intro).json()["code"], "match_not_open")
        self.assertFalse(Match.objects.exists())

    def test_the_pair_match_is_found_through_a_hidden_side(self) -> None:
        intro = self.accepted()
        match = intros.connect_accepted(intro)
        self.assertEqual(intros.match_for_intro(intro), match)
        self.assertEqual(self.get(f"/me/intros/{intro.pk}/").json()["match_id"], match.pk)
