"""The no-model gate in front of housing review (`HomeSettings.review_gate_on`)."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ai.openrouter import ChatResult
from home import review_backlog
from home.ingest import _store, ingest_source, link_media
from home.job import pending_posts, review_pending, run_home_job
from home.models import HomeGateSample, HomeListing, HomeMedia, HomePost, HomeSettings, HomeSource, ListingStatus, PostStatus
from home.review import (
    EMPTY_KEY,
    GATE_RULE_NO_WORD,
    PostCopy,
    apply_review,
    housing_word,
    is_live_copy,
    pick_original,
    precheck,
    review_post,
    text_key,
)
from home.review_backlog import GATE_SAMPLE_PER_DAY, requeue_rule_rejects, sample_rule_rejects, settle_backlog
from tests.test_home import ACCEPT, ROOM_TEXT, FakeReader, make_post, make_source

LABELLED = json.loads((Path(__file__).parent / "fixtures" / "home_gate_labelled.json").read_text(encoding="utf-8"))
NEAR_COPY = ROOM_TEXT.replace("۵۲۰", "۵۵۰").replace("@sara_b", "@another_person")
NO_HOUSING_WORD = "سلام دوستان کسی می‌تواند از برلین به هامبورگ یک بسته کوچک برای من بیاورد، ممنون"


def gate_on() -> None:
    HomeSettings.objects.update_or_create(pk=1, defaults={"review_gate_on": True})


def message(message_id: int, text: str, posted_at, **extra) -> dict:
    return {"telegram_message_id": message_id, "posted_at": posted_at, "text": text, **extra}


def model_says(data: dict) -> ChatResult:
    return ChatResult(ok=True, text=json.dumps(data), model="m")


class LabelledSetTests(SimpleTestCase):
    """Real posts with every rare word removed. A word list edit that loses one of these fails here."""

    def test_the_set_holds_no_contact_details(self) -> None:
        text = json.dumps(LABELLED, ensure_ascii=False)
        self.assertFalse(re.search(r"@\w|https?:|t\.me/|\d{5,}", text))

    def test_no_listing_is_lost(self) -> None:
        self.assertGreaterEqual(len(LABELLED["listings"]), 300)
        lost = [text for text in LABELLED["listings"] if not housing_word(text)]
        self.assertEqual(lost, [])

    def test_the_gate_still_drops_what_is_not_housing(self) -> None:
        kept = [text for text in LABELLED["not_listings"] if housing_word(text)]
        self.assertLessEqual(len(kept), len(LABELLED["not_listings"]) * 0.05)

    def test_the_misses_found_in_replay_stay_found(self) -> None:
        for text in (
            "درود یک خواب لاندری مستقل نزدیک ایستگاه اتوبوس",
            "درود یک‌خواب لاندری مستقل",
            "۱ خواب مستقل در نورت یورک",
            "یونیت کوچک کاملا مستقل به صورت مبله",
            "اماده اجار میباشد برای یک نفر",
            "با سلام من دنبال یک WGهستم ممنون میشم",
            "من دانشجو هستم و دنبال ی وگ تا سقف ۵۰۰ یورو میگردم",
            "بچه ها دنبال وگه در اسن هستم",
            "دنبال جایی مطمئن با مبلغ کم هستم",
            "2br near finch station",
        ):
            self.assertTrue(housing_word(text), text)

    def test_arabic_letters_and_digits_read_like_persian(self) -> None:
        self.assertTrue(housing_word("اتاق در كرايه"))
        self.assertTrue(housing_word("٣ ½ in Montreal"))
        self.assertFalse(housing_word(NO_HOUSING_WORD))


class PickOriginalTests(SimpleTestCase):
    def setUp(self) -> None:
        self.now = timezone.now()

    def copy(self, pk: int, days_ago: int, *, accepted: bool = False, seen_days_ago: int | None = None) -> PostCopy:
        seen = None if seen_days_ago is None else self.now - timedelta(days=seen_days_ago)
        return PostCopy(pk, self.now - timedelta(days=days_ago), accepted, seen)

    def test_no_copies(self) -> None:
        self.assertIsNone(pick_original(self.now, []))

    def test_the_earliest_copy_inside_the_window(self) -> None:
        self.assertEqual(pick_original(self.now, [self.copy(3, 2), self.copy(2, 10), self.copy(1, 90)]), 2)

    def test_a_later_copy_counts_because_a_read_walks_backwards(self) -> None:
        self.assertEqual(pick_original(self.now - timedelta(days=5), [self.copy(1, 1)]), 1)

    def test_a_listing_still_being_seen_wins_however_old_its_post(self) -> None:
        self.assertEqual(pick_original(self.now, [self.copy(2, 5), self.copy(1, 90, accepted=True, seen_days_ago=10)]), 1)

    def test_a_listing_not_seen_for_longer_than_the_window_is_no_original(self) -> None:
        self.assertIsNone(pick_original(self.now, [self.copy(1, 90, accepted=True, seen_days_ago=60)]))

    def test_live_copies(self) -> None:
        self.assertFalse(is_live_copy(PostStatus.REJECTED, "repost", "rule"))
        self.assertFalse(is_live_copy(PostStatus.REJECTED, "incomplete", "m"))
        self.assertFalse(is_live_copy(PostStatus.REJECTED, "noise", "m"))
        self.assertTrue(is_live_copy(PostStatus.REJECTED, "noise", "rule"))
        self.assertTrue(is_live_copy(PostStatus.REJECTED, "ad", "m"))
        self.assertTrue(is_live_copy(PostStatus.NEW, "", ""))
        self.assertTrue(is_live_copy(PostStatus.ACCEPTED, "", "m"))


class FlagOffTests(TestCase):
    """Off is the default, so the rest of the housing suite proves it too. This covers what the gate would change."""

    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()
        self.now = timezone.now()

    def test_the_flag_is_off_by_default(self) -> None:
        self.assertFalse(HomeSettings.load().review_gate_on)

    def test_reading_stores_everything_as_new_and_settles_nothing(self) -> None:
        messages = [
            message(3, ROOM_TEXT, self.now - timedelta(hours=1)),
            message(2, NEAR_COPY, self.now - timedelta(hours=2)),
            message(1, NO_HOUSING_WORD, self.now - timedelta(hours=3)),
        ]
        with patch("home.ingest.precheck") as settle:
            ingest_source(FakeReader(messages), self.source, days=30, stop_at=None)
        settle.assert_not_called()
        rows = HomePost.objects.order_by("telegram_message_id")
        self.assertEqual([row.status for row in rows], [PostStatus.NEW] * 3)
        self.assertEqual([row.review_json for row in rows], [None, None, None])

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_the_review_runs_as_before(self, call, _enabled) -> None:
        call.return_value = model_says({"accept": False, "reject_reason": "noise"})
        old = make_post(self.source, 1, posted_at=self.now - timedelta(days=3))
        apply_review(old, dict(ACCEPT))
        exact = make_post(self.source, 2, posted_at=self.now - timedelta(days=2))
        near = make_post(self.source, 3, text=NEAR_COPY, posted_at=self.now - timedelta(days=1))
        no_word = make_post(self.source, 4, text=NO_HOUSING_WORD, posted_at=self.now - timedelta(hours=1))
        short = make_post(self.source, 5, text="سلام", posted_at=self.now)
        with patch("home.review._gate_verdict") as gate, patch("home.job.sample_rule_rejects") as sample:
            result = run_home_job(step="review", review_limit=10, stop_at=None)["review"]
        gate.assert_not_called()
        sample.assert_not_called()
        self.assertEqual((result["reposts"], result["rejected"]), (1, 3))
        self.assertEqual(call.call_count, 2)  # The near copy and the post with no housing word both reach the model.
        for post in (exact, near, no_word, short):
            post.refresh_from_db()
        self.assertEqual((exact.reject_reason, exact.review_json), ("repost", {"repost_of": old.pk}))
        self.assertEqual((near.model, no_word.model, short.model), ("m", "m", "rule"))
        self.assertEqual(HomeListing.objects.get(post=old).last_seen, exact.posted_at)
        self.assertFalse(HomeGateSample.objects.exists())

    def test_a_short_repost_still_waits_for_the_model(self) -> None:
        text = "اتاق برای اجاره در برلین نزدیک ایستگاه قطار"
        make_post(self.source, 1, text=text, posted_at=self.now - timedelta(days=2))
        later = make_post(self.source, 2, text=text, posted_at=self.now - timedelta(days=1))
        self.assertIsNone(precheck(later))


class IngestGateTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        gate_on()
        self.source = make_source()
        self.now = timezone.now()

    def read(self, *messages) -> dict:
        ordered = sorted(messages, key=lambda row: -row["telegram_message_id"])
        self.source.refresh_from_db()
        return ingest_source(FakeReader(ordered, page_size=10), self.source, days=30, stop_at=None)

    def test_a_copy_settles_on_arrival_and_never_waits(self) -> None:
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=2))
        result = self.read(message(2, ROOM_TEXT, self.now - timedelta(hours=1)))
        self.assertEqual(result["created"], 1)
        copy = HomePost.objects.get(telegram_message_id=2)
        self.assertEqual((copy.status, copy.reject_reason, copy.model), (PostStatus.REJECTED, "repost", "rule"))
        self.assertEqual(copy.review_json, {"repost_of": first.pk})
        self.assertNotIn(copy.pk, [post.pk for post in pending_posts()])

    def test_a_copy_differing_only_in_a_handle_or_link_has_the_same_key(self) -> None:
        make_post(self.source, 1, posted_at=self.now - timedelta(days=2))
        self.read(message(2, ROOM_TEXT.replace("@sara_b", "@another_person https://t.me/x"), self.now - timedelta(hours=1)))
        self.assertEqual(HomePost.objects.get(telegram_message_id=2).reject_reason, "repost")

    def test_a_near_copy_with_new_numbers_waits_for_the_model_and_leaves_the_listing_alone(self) -> None:
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=2))
        apply_review(first, dict(ACCEPT))
        self.read(message(2, NEAR_COPY, self.now - timedelta(hours=1)))
        self.assertEqual(HomePost.objects.get(telegram_message_id=2).status, PostStatus.NEW)
        self.assertEqual(HomeListing.objects.get(post=first).last_seen, first.posted_at)

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_a_backward_read_leaves_exactly_one_copy_for_the_model(self, call, _enabled) -> None:
        """Newest arrives first here. Two copies must never each be settled against the other."""
        call.return_value = model_says(ACCEPT)
        self.read(
            message(3, ROOM_TEXT, self.now - timedelta(hours=1)),
            message(2, ROOM_TEXT, self.now - timedelta(hours=2)),
            message(1, ROOM_TEXT, self.now - timedelta(hours=3)),
        )
        self.assertEqual(pending_posts().count(), 1)
        result = review_pending(limit=5)
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(call.call_count, 1)
        listing = HomeListing.objects.get()
        self.assertEqual(listing.first_seen, self.now - timedelta(hours=3))
        self.assertEqual(listing.last_seen, self.now - timedelta(hours=1))

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_a_repost_of_an_accepted_listing_keeps_it_alive(self, call, _enabled) -> None:
        call.return_value = model_says(ACCEPT)
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=4))
        review_post(first)
        self.read(message(2, ROOM_TEXT, self.now - timedelta(days=2)))
        self.read(message(3, ROOM_TEXT, self.now - timedelta(days=1)))
        self.assertEqual(call.call_count, 1)
        self.assertEqual(HomeListing.objects.get(post=first).last_seen, self.now - timedelta(days=1))

    def test_a_repost_never_moves_last_seen_backwards(self) -> None:
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=1))
        apply_review(first, dict(ACCEPT))
        self.read(message(2, ROOM_TEXT, self.now - timedelta(days=3)))
        self.assertEqual(HomeListing.objects.get(post=first).last_seen, first.posted_at)

    def test_a_listing_reposted_for_months_is_not_reviewed_again_and_does_not_expire(self) -> None:
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=90))
        apply_review(first, dict(ACCEPT))
        HomeListing.objects.filter(post=first).update(last_seen=self.now - timedelta(days=30))
        self.read(message(2, ROOM_TEXT, self.now))
        self.assertEqual(HomePost.objects.get(telegram_message_id=2).review_json, {"repost_of": first.pk})
        self.assertEqual(HomeListing.objects.get(post=first).last_seen, self.now)

    def test_the_listing_a_copy_was_merged_into_is_seen_again_too(self) -> None:
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=4))
        apply_review(first, dict(ACCEPT))
        second = make_post(self.source, 2, text=ROOM_TEXT + " طبقه دوم", posted_at=self.now - timedelta(days=3))
        apply_review(second, dict(ACCEPT))
        duplicate = HomeListing.objects.get(post=second)
        canonical = HomeListing.objects.get(post=first)
        self.assertEqual((duplicate.status, duplicate.canonical_id), (ListingStatus.DUPLICATE, canonical.pk))
        self.read(message(3, ROOM_TEXT + " طبقه دوم", self.now))
        canonical.refresh_from_db()
        self.assertEqual(canonical.last_seen, self.now)

    def test_a_repost_keeps_no_photos_even_when_they_arrive_after_it(self) -> None:
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=2))
        self.source.fetch_photos = True
        self.source.save(update_fields=["fetch_photos"])
        self.read(
            message(3, "", self.now, has_photo=True, grouped_id=77),
            message(2, ROOM_TEXT, self.now - timedelta(minutes=1), has_photo=True, grouped_id=77),
        )
        copy = HomePost.objects.get(telegram_message_id=2)
        self.assertEqual(copy.reject_reason, "repost")
        self.assertEqual(HomeMedia.objects.filter(source=self.source).count(), 0)
        self.assertEqual(first.media.count(), 0)

    def test_link_media_keeps_photos_with_the_gate_off(self) -> None:
        copy = make_post(self.source, 2, status=PostStatus.REJECTED, reject_reason="repost")
        HomeMedia.objects.create(source=self.source, telegram_message_id=2)
        self.assertEqual(link_media(self.source), 1)
        self.assertEqual(copy.media.count(), 1)

    def test_other_countries_do_not_share_copies(self) -> None:
        make_post(make_source(username="toronto_rent", country="CA", default_currency="CAD"), 1, posted_at=self.now - timedelta(days=1))
        self.read(message(2, ROOM_TEXT, self.now))
        self.assertEqual(HomePost.objects.get(source=self.source).status, PostStatus.NEW)

    def test_posts_that_are_only_links_are_not_copies_of_each_other(self) -> None:
        text = "https://t.me/rental_group/1 🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠"
        self.assertEqual(text_key(text), EMPTY_KEY)
        make_post(self.source, 1, text=text, posted_at=self.now - timedelta(days=1))
        self.read(message(2, "https://t.me/rental_group/2 🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠🏠", self.now))
        self.assertNotEqual(HomePost.objects.get(telegram_message_id=2).reject_reason, "repost")

    def test_no_housing_word_is_rejected_by_rule_and_stays_auditable(self) -> None:
        self.read(message(1, NO_HOUSING_WORD, self.now))
        post = HomePost.objects.get()
        self.assertEqual((post.status, post.reject_reason, post.model), (PostStatus.REJECTED, "noise", "rule"))
        self.assertEqual(post.review_json, {"rule": GATE_RULE_NO_WORD})
        self.assertEqual(post.text, NO_HOUSING_WORD)
        self.assertIsNotNone(post.reviewed_at)

    def test_a_post_with_a_housing_word_waits_for_the_model(self) -> None:
        self.read(message(1, ROOM_TEXT, self.now))
        post = HomePost.objects.get()
        self.assertEqual(post.status, PostStatus.NEW)

    def test_a_failing_gate_never_stops_the_read(self) -> None:
        with patch("home.ingest.precheck", side_effect=RuntimeError("boom")):
            result = self.read(message(1, ROOM_TEXT, self.now))
        self.assertEqual(result["created"], 1)
        self.assertEqual(HomePost.objects.get().status, PostStatus.NEW)

    def test_reading_a_stored_message_again_changes_nothing(self) -> None:
        post = make_post(self.source, 1, status=PostStatus.ERROR, posted_at=self.now)
        self.assertFalse(_store(self.source, message(1, ROOM_TEXT, self.now), gate=True))
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.ERROR)


class ReviewGateTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        gate_on()
        self.source = make_source()
        self.now = timezone.now()

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_nothing_the_gate_settles_reaches_the_model(self, call, _enabled) -> None:
        call.return_value = model_says({"accept": False, "reject_reason": "ad"})
        make_post(self.source, 1, posted_at=self.now - timedelta(days=2))
        make_post(self.source, 2, text=NO_HOUSING_WORD, posted_at=self.now - timedelta(days=1))
        make_post(self.source, 3, posted_at=self.now)
        result = review_pending(limit=10)
        self.assertEqual(call.call_count, 1)
        self.assertEqual((result["reposts"], result["rejected"]), (1, 2))

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_a_failed_post_retried_goes_through_the_gate(self, call, _enabled) -> None:
        make_post(
            self.source, 1, text=NO_HOUSING_WORD, status=PostStatus.ERROR, reviewed_at=self.now - timedelta(hours=9), posted_at=self.now - timedelta(days=1)
        )
        review_pending(limit=10)
        call.assert_not_called()
        self.assertEqual(HomePost.objects.get().reject_reason, "noise")

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_a_copy_of_an_uncertain_model_verdict_gets_another_look(self, call, _enabled) -> None:
        call.return_value = model_says(ACCEPT)
        for reason, number in (("incomplete", 1), ("noise", 3), ("other", 5)):
            text = f"{ROOM_TEXT} {reason} {'الف' * number}"
            first = make_post(self.source, number, text=text, posted_at=self.now - timedelta(days=2))
            apply_review(first, {"accept": False, "reject_reason": reason}, model="m")
            copy = make_post(self.source, number + 1, text=text, posted_at=self.now - timedelta(days=1))
            self.assertIsNone(precheck(copy), reason)

    def test_a_copy_of_a_firm_model_verdict_settles(self) -> None:
        for reason, number in (("ad", 1), ("not_housing", 3), ("expired", 5)):
            text = f"{ROOM_TEXT} {reason} {'الف' * number}"
            first = make_post(self.source, number, text=text, posted_at=self.now - timedelta(days=2))
            apply_review(first, {"accept": False, "reject_reason": reason}, model="m")
            copy = make_post(self.source, number + 1, text=text, posted_at=self.now - timedelta(days=1))
            self.assertEqual(precheck(copy).reject_reason, "repost", reason)

    def test_a_copy_of_a_rule_reject_settles(self) -> None:
        make_post(self.source, 1, text=NO_HOUSING_WORD, posted_at=self.now - timedelta(days=2), status=PostStatus.REJECTED, reject_reason="noise", model="rule")
        copy = make_post(self.source, 2, text=NO_HOUSING_WORD, posted_at=self.now)
        self.assertEqual(precheck(copy).reject_reason, "repost")

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_a_post_sent_back_by_hand_reaches_the_model(self, call, _enabled) -> None:
        call.return_value = model_says(ACCEPT)
        post = make_post(self.source, 1, text=NO_HOUSING_WORD + " برلین", posted_at=self.now)
        precheck(post)
        post.refresh_from_db()
        self.assertEqual(post.reject_reason, "noise")
        # What the admin action "Send back to the AI review" does.
        HomePost.objects.filter(pk=post.pk).update(status=PostStatus.NEW, reject_reason="", error="", reviewed_at=None)
        post.refresh_from_db()
        review_post(post)
        self.assertEqual(call.call_count, 1)
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.ACCEPTED)


class BacklogTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()
        self.now = timezone.now()
        self.head = make_post(self.source, 1, posted_at=self.now - timedelta(days=5))
        make_post(self.source, 2, posted_at=self.now - timedelta(days=4))
        make_post(self.source, 3, text=NEAR_COPY, posted_at=self.now - timedelta(days=3))
        make_post(self.source, 4, text=NO_HOUSING_WORD, posted_at=self.now - timedelta(days=2))
        make_post(self.source, 5, text="سلام", posted_at=self.now - timedelta(days=1))
        make_post(self.source, 6, text=ROOM_TEXT + " در منطقه‌ای دیگر با شرایطی دیگر", posted_at=self.now)

    def snapshot(self) -> list[tuple]:
        return list(HomePost.objects.order_by("pk").values_list("pk", "status", "reject_reason", "model"))

    def test_a_dry_run_writes_nothing_and_counts_what_a_real_run_does(self) -> None:
        before = self.snapshot()
        with patch("home.job.acquire_lock") as lock:
            planned = settle_backlog(dry_run=True, batch_size=2)
        lock.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(
            {key: planned[key] for key in ("waiting", "too_short", "duplicate", "no_housing_word", "settled", "reach_model")},
            {"waiting": 6, "too_short": 1, "duplicate": 1, "no_housing_word": 1, "settled": 3, "reach_model": 3},
        )
        real = settle_backlog(batch_size=2)
        self.assertEqual({key: value for key, value in real.items() if key != "dry_run"}, {key: value for key, value in planned.items() if key != "dry_run"})
        self.assertEqual(HomePost.objects.filter(status=PostStatus.NEW).count(), 3)
        self.assertEqual(HomePost.objects.get(pk=self.head.pk).status, PostStatus.NEW)

    def test_running_it_again_settles_nothing_more(self) -> None:
        settle_backlog(batch_size=2)
        after = self.snapshot()
        again = settle_backlog(batch_size=2)
        self.assertEqual(self.snapshot(), after)
        self.assertEqual((again["settled"], again["reach_model"]), (0, 3))

    def test_it_never_calls_a_model(self) -> None:
        with patch("home.review.ask_model") as call, patch("home.review_backlog.review_module.ask_model") as other:
            settle_backlog()
        call.assert_not_called()
        other.assert_not_called()

    def test_it_agrees_with_the_review_on_the_same_posts(self) -> None:
        planned = settle_backlog(dry_run=True)
        gate_on()
        with patch("home.review.llm_enabled", return_value=False):
            review_pending(limit=100)
        self.assertEqual(HomePost.objects.filter(status=PostStatus.NEW).count(), planned["reach_model"])

    def test_a_copy_of_an_accepted_listing_refreshes_it(self) -> None:
        apply_review(self.head, dict(ACCEPT))
        settle_backlog()
        self.assertEqual(HomeListing.objects.get(post=self.head).last_seen, self.now - timedelta(days=4))

    def test_a_post_sent_back_by_hand_is_left_alone(self) -> None:
        HomePost.objects.filter(telegram_message_id=4).update(review_json={"rule": GATE_RULE_NO_WORD})
        report = settle_backlog()
        self.assertEqual(report["sent_back_by_hand"], 1)
        self.assertEqual(HomePost.objects.get(telegram_message_id=4).status, PostStatus.NEW)

    def test_a_post_the_job_took_meanwhile_is_skipped(self) -> None:
        real_plan = review_backlog._build_plan

        def plan_then_job_takes_it(*args):
            plan = real_plan(*args)
            HomePost.objects.filter(telegram_message_id=4).update(status=PostStatus.ACCEPTED)
            return plan

        with patch("home.review_backlog._build_plan", side_effect=plan_then_job_takes_it):
            report = settle_backlog()
        self.assertEqual(report["no_housing_word"], 0)
        self.assertEqual(HomePost.objects.get(telegram_message_id=4).status, PostStatus.ACCEPTED)

    def test_it_holds_the_job_lock_only_batch_by_batch(self) -> None:
        settle_backlog(batch_size=2)
        self.assertIsNone(HomeSettings.load().job_lock_until)

    def test_a_held_lock_stops_it_with_a_message_and_nothing_written(self) -> None:
        HomeSettings.load()
        HomeSettings.objects.filter(pk=1).update(job_lock_until=self.now + timedelta(minutes=5))
        with patch("home.review_backlog.LOCK_WAIT_SECONDS", 0):
            report = settle_backlog()
        self.assertIn("stopped", report)
        self.assertEqual(HomePost.objects.filter(status=PostStatus.NEW).count(), 6)

    def test_the_command_prints_counts(self) -> None:
        out, err = StringIO(), StringIO()
        call_command("settle_review_backlog", "--dry-run", "--batch-size", "2", stdout=out, stderr=err)
        report = json.loads(out.getvalue())
        self.assertEqual((report["dry_run"], report["reach_model"]), (True, 3))
        self.assertEqual(HomePost.objects.filter(status=PostStatus.NEW).count(), 6)


class RequeueTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        gate_on()
        self.source = make_source()
        self.post = make_post(self.source, 1, text=NO_HOUSING_WORD, posted_at=timezone.now())
        precheck(self.post)

    def test_nothing_moves_while_the_word_list_is_unchanged(self) -> None:
        self.assertEqual(requeue_rule_rejects(), {"dry_run": False, "checked": 1, "requeued": 0})
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, PostStatus.REJECTED)

    def test_a_longer_word_list_sends_the_post_back_and_the_model_sees_it(self) -> None:
        with patch("home.review_backlog.housing_word", return_value=True):
            self.assertEqual(requeue_rule_rejects(dry_run=True)["requeued"], 1)
            self.post.refresh_from_db()
            self.assertEqual(self.post.status, PostStatus.REJECTED)
            self.assertEqual(requeue_rule_rejects()["requeued"], 1)
        self.post.refresh_from_db()
        self.assertEqual((self.post.status, self.post.reject_reason, self.post.model, self.post.review_json), (PostStatus.NEW, "", "", None))

    def test_the_command_can_requeue(self) -> None:
        out = StringIO()
        call_command("settle_review_backlog", "--requeue-noise", stdout=out)
        self.assertEqual(json.loads(out.getvalue())["checked"], 1)

    def test_other_rule_rejects_are_not_touched(self) -> None:
        make_post(self.source, 2, text="سلام", status=PostStatus.REJECTED, reject_reason="noise", model="rule")
        self.assertEqual(requeue_rule_rejects()["checked"], 1)


@patch("home.review.llm_enabled", return_value=True)
@patch("home.review.ask_model")
class SampleTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        gate_on()
        self.source = make_source()
        self.now = timezone.now()

    def rule_rejects(self, count: int) -> list[HomePost]:
        posts = []
        for number in range(1, count + 1):
            post = make_post(self.source, number, text=f"{NO_HOUSING_WORD} {'الف' * number}", posted_at=self.now)
            precheck(post)
            posts.append(post)
        return posts

    def test_a_rejected_sample_keeps_the_rule_verdict(self, call, _enabled) -> None:
        call.return_value = model_says({"accept": False, "reject_reason": "ad"})
        post = self.rule_rejects(1)[0]
        report = sample_rule_rejects()
        self.assertEqual((report["sampled"], report["missed"], report["miss_rate"]), (1, 0, 0.0))
        post.refresh_from_db()
        self.assertEqual((post.status, post.model, post.review_json), (PostStatus.REJECTED, "rule", {"rule": GATE_RULE_NO_WORD}))
        self.assertEqual(HomeGateSample.objects.get().model_reason, "ad")

    def test_a_miss_is_counted_logged_and_the_post_becomes_a_listing(self, call, _enabled) -> None:
        call.return_value = model_says(ACCEPT)
        post = self.rule_rejects(1)[0]
        with self.assertLogs("home.review_backlog", level="WARNING") as logs:
            report = sample_rule_rejects()
        self.assertEqual(report["miss_rate"], 1.0)
        self.assertIn("miss_rate=1.0", logs.output[0])
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.ACCEPTED)
        self.assertTrue(HomeListing.objects.filter(post=post).exists())
        self.assertTrue(HomeGateSample.objects.get(post=post).missed)

    def test_an_accept_with_nothing_usable_is_no_miss(self, call, _enabled) -> None:
        call.return_value = model_says({"accept": True, "kind": "bogus"})
        self.rule_rejects(1)
        self.assertEqual(sample_rule_rejects()["missed"], 0)
        self.assertEqual(HomeGateSample.objects.get().model_reason, "incomplete")

    def test_a_failed_call_changes_nothing_and_can_be_tried_again(self, call, _enabled) -> None:
        call.return_value = ChatResult(ok=False, text="", model="m", error="busy")
        post = self.rule_rejects(1)[0]
        self.assertEqual(sample_rule_rejects()["errors"], 1)
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.REJECTED)
        self.assertFalse(HomeGateSample.objects.exists())
        call.return_value = ChatResult(ok=True, text="not json", model="m")
        self.assertEqual(sample_rule_rejects()["errors"], 1)

    def test_a_day_takes_its_quota_and_no_post_twice(self, call, _enabled) -> None:
        call.return_value = model_says({"accept": False, "reject_reason": "noise"})
        self.rule_rejects(GATE_SAMPLE_PER_DAY + 3)
        self.assertEqual(sample_rule_rejects()["sampled"], GATE_SAMPLE_PER_DAY)
        self.assertEqual(call.call_count, GATE_SAMPLE_PER_DAY)
        self.assertIn("skipped", sample_rule_rejects())
        self.assertEqual(call.call_count, GATE_SAMPLE_PER_DAY)

    def test_it_stops_after_two_weeks(self, call, _enabled) -> None:
        first, second = self.rule_rejects(2)
        old = HomeGateSample.objects.create(post=first)
        HomeGateSample.objects.filter(pk=old.pk).update(created_at=self.now - timedelta(days=15))
        self.assertIn("skipped", sample_rule_rejects())
        call.assert_not_called()

    def test_only_todays_gate_rejects_are_sampled(self, call, _enabled) -> None:
        old = self.rule_rejects(1)[0]
        HomePost.objects.filter(pk=old.pk).update(reviewed_at=self.now - timedelta(days=3))
        make_post(self.source, 9, text="سلام", status=PostStatus.REJECTED, reject_reason="noise", model="rule", reviewed_at=self.now)
        self.assertIn("skipped", sample_rule_rejects())
        call.assert_not_called()

    def test_no_model_or_no_time_means_no_sample(self, call, enabled) -> None:
        self.rule_rejects(1)
        self.assertIn("skipped", sample_rule_rejects(stop_at=0.0))
        enabled.return_value = False
        self.assertIn("skipped", sample_rule_rejects())
        call.assert_not_called()

    def test_the_review_step_runs_it(self, call, _enabled) -> None:
        call.return_value = model_says({"accept": False, "reject_reason": "noise"})
        self.rule_rejects(1)
        result = run_home_job(step="review", review_limit=5)
        self.assertEqual(result["gate_sample"]["sampled"], 1)
