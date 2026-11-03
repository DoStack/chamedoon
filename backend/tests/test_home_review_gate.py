"""The no-model gate in front of housing review (`HomeSettings.review_gate_on`)."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ai.openrouter import ChatResult
from home.ingest import _store, ingest_source, link_media
from home.job import pending_posts, review_pending, run_home_job
from home.models import HomeGateSample, HomeListing, HomeMedia, HomePost, HomeSettings, HomeSource, ListingStatus, PostStatus
from home.review import (
    EMPTY_KEY,
    GATE_RULE_NO_WORD,
    apply_gate_decision,
    apply_review,
    copy_facts,
    find_original,
    finish_with_result,
    gate_decision,
    housing_word,
    precheck,
    review_post,
    settle_duplicate,
    text_key,
)
from home.review_backlog import GATE_OFF, GATE_SAMPLE_PER_DAY, MAX_BATCH, _batch_size, requeue_rule_rejects, sample_rule_rejects, settle_backlog
from tests.test_home import ACCEPT, ROOM_TEXT, FakeReader, make_post, make_source

LABELLED = json.loads((Path(__file__).parent / "fixtures" / "home_gate_labelled.json").read_text(encoding="utf-8"))
NEAR_COPY = ROOM_TEXT.replace("۵۲۰", "۵۵۰").replace("@sara_b", "@another_person")
NO_HOUSING_WORD = "سلام دوستان کسی می‌تواند از برلین به هامبورگ یک بسته کوچک برای من بیاورد، هر کسی که می‌تواند پیام بدهد، ممنون"
LONG_TAIL = " امکانات خوب و نزدیک ایستگاه است و همسایه‌ها آرام هستند" * 10


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

    def test_the_words_added_after_review_are_found(self) -> None:
        for text in (
            "nice apt near the station",
            "Suite available from May",
            "2 bedroom flat in Mitte",
            "location meublee a Laval",
            "a louer des maintenant",
            "studio meublé",
            "möbliert ab sofort",
            "3 bdrm upstairs",
            "متراژ خوب نزدیک مترو",
            "مستاجر خوش حساب می‌خواهیم",
            "دنبال یه جا هستم در مونترال",
        ):
            self.assertTrue(housing_word(text), text)

    def test_a_short_word_inside_a_longer_one_does_not_count(self) -> None:
        for text in ("we adapt quickly", "inflation is high", "the captain said hello", "zigzag and halt", "warmup at the bdsm", "m2x"):
            self.assertFalse(housing_word(text), text)

    def test_german_shorthand_is_found(self) -> None:
        self.assertTrue(housing_word("2-Zi-Whg, 48 qm, 690 € warm, ab 01.12."))
        for text in ("2 Zi. in Köln", "Whg. frei ab Mai", "3 ZKB im Zentrum", "48qm ab sofort", "48 m² in Köln", "48 m2 in Köln", "690 warm", "500 kalt"):
            self.assertTrue(housing_word(text), text)

    def test_canadian_shorthand_is_found(self) -> None:
        for text in (
            "650 sqft near TTC",
            "650 sq ft near TTC",
            "650 sq.ft near TTC",
            "2bd in Scarborough",
            "1 bdr main floor",
            "bsmt near Finch",
            "bach in Montreal",
            "new townhome near Yonge",
            "fully furnished, Bayview",
            "utilities included",
            "own washroom near TTC",
            "ensuite laundry",
            "فول فرنیش نزدیک مترو",
            "مبله نزدیک یانگ",
            "شامل یوتیلیتی",
            "واشروم جدا و ورودی جدا",
            "لاندری داخل",
            "دیتچ نورت یورک",
            "تاون هاوس در مارکهام",
            "۶۰۰ فوت مربع",
            "۴۰ متر مربع",
        ):
            self.assertTrue(housing_word(text), text)

    def test_arabic_letters_and_digits_read_like_persian(self) -> None:
        self.assertTrue(housing_word("اتاق در كرايه"))
        self.assertTrue(housing_word("٣ ½ in Montreal"))
        self.assertFalse(housing_word(NO_HOUSING_WORD))


class CopyFactsTests(SimpleTestCase):
    def test_the_same_post_says_the_same(self) -> None:
        self.assertEqual(copy_facts(ROOM_TEXT), copy_facts(ROOM_TEXT))

    def test_any_handle_form_counts(self) -> None:
        for other in ("@another_person", "https://t.me/another_person", "t.me/another_person", "تلگرام: another_person"):
            self.assertNotEqual(copy_facts("اتاق @sara_b"), copy_facts(f"اتاق {other}"), other)

    def test_handle_case_does_not_count(self) -> None:
        self.assertEqual(copy_facts("اتاق @Sara_B"), copy_facts("اتاق @sara_b"))

    def test_the_sources_own_handle_is_a_signature(self) -> None:
        self.assertEqual(copy_facts("اتاق @group_a", "group_a"), copy_facts("اتاق @group_b", "Group_B"))
        self.assertNotEqual(copy_facts("اتاق @group_a", "group_b"), copy_facts("اتاق @group_b", "group_b"))

    def test_phones_compare_by_digits(self) -> None:
        self.assertEqual(copy_facts("تماس (416) 555-1234"), copy_facts("تماس ۴۱۶ ۵۵۵ ۱۲۳۴"))
        self.assertEqual(copy_facts("تماس 1-416-555-1234"), copy_facts("تماس 416-555-1234"))
        self.assertNotEqual(copy_facts("تماس 416-555-1234"), copy_facts("تماس 416-555-9999"))
        self.assertNotEqual(copy_facts("Tel 0176 1234567"), copy_facts("Tel 0176 7654321"))

    def test_prices_compare_by_digits(self) -> None:
        self.assertEqual(copy_facts("اجاره 2,100 دلار"), copy_facts("اجاره ۲۱۰۰ دلار"))
        self.assertNotEqual(copy_facts("اجاره 2100 دلار"), copy_facts("اجاره 1950 دلار"))

    def test_a_short_number_inside_a_link_is_not_a_price(self) -> None:
        self.assertEqual(copy_facts("اتاق https://t.me/group/123"), copy_facts("اتاق https://t.me/group/456"))
        self.assertEqual(copy_facts("اتاق https://example.com/123"), copy_facts("اتاق https://example.com/456"))

    def test_a_phone_inside_a_link_counts(self) -> None:
        for first, second in (
            ("https://wa.me/4917612345678", "https://wa.me/4917687654321"),
            ("wa.me/+4917612345678", "wa.me/+4917687654321"),
            ("https://api.whatsapp.com/send?phone=14165551234&text=hi", "https://api.whatsapp.com/send?phone=14165559999&text=hi"),
        ):
            self.assertNotEqual(copy_facts(f"اتاق {first}"), copy_facts(f"اتاق {second}"), first)

    def test_a_phone_reads_the_same_in_a_link_and_in_the_text(self) -> None:
        self.assertEqual(copy_facts("اتاق https://wa.me/4917612345678"), copy_facts("اتاق +49 176 12345678"))

    def test_a_handle_inside_a_link_counts(self) -> None:
        for first, second in (
            ("https://www.instagram.com/sara_home", "https://www.instagram.com/lena_home"),
            ("tg://resolve?domain=sara_home", "tg://resolve?domain=lena_home"),
        ):
            self.assertNotEqual(copy_facts(f"اتاق {first}"), copy_facts(f"اتاق {second}"), first)


class FindOriginalTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()
        self.now = timezone.now()

    def at(self, number: int, days_ago: int, **extra) -> HomePost:
        return make_post(self.source, number, posted_at=self.now - timedelta(days=days_ago), **extra)

    def test_no_copies(self) -> None:
        self.assertIsNone(find_original(self.at(1, 0)))

    def test_the_earliest_copy_inside_the_window(self) -> None:
        self.at(1, 90)
        second = self.at(2, 10)
        self.at(3, 2)
        latest = self.at(4, 0)
        self.assertEqual(find_original(latest), second.pk)

    def test_a_later_post_that_came_first_counts_because_a_read_walks_backwards(self) -> None:
        later = self.at(2, 0)
        self.assertEqual(find_original(self.at(1, 5)), later.pk)

    def test_a_listing_still_being_seen_wins_however_old_its_post(self) -> None:
        old = self.at(1, 90)
        apply_review(old, dict(ACCEPT))
        HomeListing.objects.filter(post=old).update(last_seen=self.now - timedelta(days=10))
        self.at(2, 5)
        self.assertEqual(find_original(self.at(3, 0)), old.pk)

    def test_a_listing_not_seen_for_longer_than_the_window_is_no_original(self) -> None:
        old = self.at(1, 90)
        apply_review(old, dict(ACCEPT))
        HomeListing.objects.filter(post=old).update(last_seen=self.now - timedelta(days=60))
        self.assertIsNone(find_original(self.at(2, 0)))

    def test_a_copy_under_80_characters_never_settles_as_a_repost(self) -> None:
        text = "اتاق برای اجاره در برلین نزدیک ایستگاه قطار"
        self.assertLess(len(text), 80)
        self.at(1, 2, text=text)
        self.assertIsNone(find_original(self.at(2, 0, text=text)))
        self.assertEqual(gate_decision(HomePost.objects.get(telegram_message_id=2)).kind, "reach_model")

    def test_a_repost_is_never_an_original(self) -> None:
        self.at(1, 5, status=PostStatus.REJECTED, reject_reason="repost", model="rule")
        self.assertIsNone(find_original(self.at(2, 0)))

    def test_a_copy_past_the_comparison_limit_goes_to_the_model(self) -> None:
        for number in range(1, 5):
            self.at(number, 2, text=f"{ROOM_TEXT}{LONG_TAIL} {number}")
        latest = self.at(9, 0, text=f"{ROOM_TEXT}{LONG_TAIL} 4")
        self.assertEqual(find_original(latest), HomePost.objects.get(telegram_message_id=4).pk)
        with patch("home.review.GATE_COPIES_COMPARED", 2), patch("home.review._COPIES_PAGE", 1):
            self.assertIsNone(find_original(latest))


class TwoWaitingCopiesTests(TestCase):
    """Ingest and the worker can look at two copies at once. Neither order may leave both settled."""

    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        gate_on()
        self.source = make_source()
        self.now = timezone.now()

    def test_only_the_higher_id_can_settle_whichever_is_older_or_looks_first(self) -> None:
        low = make_post(self.source, 1, posted_at=self.now)
        high = make_post(self.source, 2, posted_at=self.now - timedelta(days=1))
        low_decision, high_decision = gate_decision(low), gate_decision(high)
        self.assertEqual(low_decision.kind, "reach_model")
        self.assertEqual((high_decision.kind, high_decision.original_pk), ("duplicate", low.pk))
        apply_gate_decision(high, high_decision)
        self.assertIsNone(precheck(HomePost.objects.get(pk=low.pk)))
        self.assertEqual(HomePost.objects.get(pk=low.pk).status, PostStatus.NEW)

    def test_a_failed_post_follows_the_same_rule(self) -> None:
        low = make_post(self.source, 1, status=PostStatus.NEW, posted_at=self.now)
        high = make_post(self.source, 2, status=PostStatus.ERROR, posted_at=self.now)
        self.assertEqual(gate_decision(low).kind, "reach_model")
        self.assertEqual(gate_decision(high).original_pk, low.pk)

    def test_a_verdict_lands_only_on_a_post_still_waiting(self) -> None:
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=1))
        post = make_post(self.source, 2, posted_at=self.now)
        stale = HomePost.objects.get(pk=post.pk)
        HomeMedia.objects.create(source=self.source, post=post, telegram_message_id=2)
        HomePost.objects.filter(pk=post.pk).update(status=PostStatus.ACCEPTED, model="m")
        outcome = apply_gate_decision(stale, gate_decision(stale))
        self.assertTrue(outcome.accepted)
        post.refresh_from_db()
        self.assertEqual((post.status, post.model, post.review_json), (PostStatus.ACCEPTED, "m", None))
        self.assertEqual(post.media.count(), 1)
        self.assertIsNone(first.review_json)

    def test_a_failed_post_can_still_take_a_verdict(self) -> None:
        post = make_post(self.source, 1, text=NO_HOUSING_WORD, status=PostStatus.ERROR, posted_at=self.now)
        self.assertEqual(precheck(post).reject_reason, "noise")
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.REJECTED)


class SettledDuringTheCallTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()
        self.post = make_post(self.source, 1, posted_at=timezone.now())
        self.stale = HomePost.objects.get(pk=self.post.pk)
        HomePost.objects.filter(pk=self.post.pk).update(status=PostStatus.REJECTED, reject_reason="repost", model="rule", review_json={"repost_of": 7})

    def assert_settle_stands(self) -> None:
        self.post.refresh_from_db()
        self.assertEqual((self.post.status, self.post.reject_reason, self.post.model), (PostStatus.REJECTED, "repost", "rule"))
        self.assertEqual(self.post.review_json, {"repost_of": 7})
        self.assertFalse(HomeListing.objects.exists())

    def test_an_accept_makes_no_listing(self) -> None:
        outcome = finish_with_result(self.stale, model_says(ACCEPT))
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reject_reason, "repost")
        self.assert_settle_stands()

    def test_a_reject_changes_nothing(self) -> None:
        finish_with_result(self.stale, model_says({"accept": False, "reject_reason": "ad"}))
        self.assert_settle_stands()

    def test_a_failed_call_changes_nothing(self) -> None:
        finish_with_result(self.stale, ChatResult(ok=False, text="", model="m", error="busy"))
        self.assert_settle_stands()
        finish_with_result(self.stale, ChatResult(ok=True, text="not json", model="m"))
        self.assert_settle_stands()


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
        with patch("home.review.gate_decision") as gate, patch("home.job.sample_rule_rejects") as sample:
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

    def test_a_copy_with_another_contact_waits_for_the_model(self) -> None:
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=2))
        apply_review(first, dict(ACCEPT))
        other_contact = ROOM_TEXT.replace("@sara_b", "@another_person")
        self.assertEqual(text_key(other_contact), text_key(ROOM_TEXT))
        self.read(message(2, other_contact, self.now - timedelta(hours=1)))
        self.assertEqual(HomePost.objects.get(telegram_message_id=2).status, PostStatus.NEW)
        self.assertEqual(HomeListing.objects.get(post=first).last_seen, first.posted_at)

    def test_a_copy_with_a_later_price_past_400_characters_waits_for_the_model(self) -> None:
        long_text = ROOM_TEXT + LONG_TAIL
        self.assertGreater(len(long_text), 400)
        make_post(self.source, 1, text=long_text + " قیمت ۵۲۰", posted_at=self.now - timedelta(days=2))
        changed = long_text + " قیمت ۶۰۰"
        self.assertEqual(text_key(changed), text_key(long_text + " قیمت ۵۲۰"))
        self.read(message(2, changed, self.now - timedelta(hours=1)))
        self.assertEqual(HomePost.objects.get(telegram_message_id=2).status, PostStatus.NEW)

    def test_a_copy_with_another_phone_inside_a_link_waits_for_the_model(self) -> None:
        make_post(self.source, 1, text=ROOM_TEXT + " https://wa.me/4917612345678", posted_at=self.now - timedelta(days=2))
        changed = ROOM_TEXT + " https://wa.me/4917687654321"
        self.assertEqual(text_key(changed), text_key(ROOM_TEXT + " https://wa.me/4917612345678"))
        self.read(message(2, changed, self.now - timedelta(hours=1)))
        self.assertEqual(HomePost.objects.get(telegram_message_id=2).status, PostStatus.NEW)

    def test_a_copy_with_only_another_plain_link_or_signature_settles(self) -> None:
        make_post(self.source, 1, text=ROOM_TEXT + " https://example.com/a", posted_at=self.now - timedelta(days=2))
        self.read(message(2, ROOM_TEXT + " https://example.com/b @berlin_khane", self.now - timedelta(hours=1)))
        self.assertEqual(HomePost.objects.get(telegram_message_id=2).reject_reason, "repost")

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_a_copy_with_another_contact_becomes_its_own_listing(self, call, _enabled) -> None:
        """Otherwise the call is wasted: the same wording would merge it back into the old contact."""
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=2))
        apply_review(first, dict(ACCEPT))
        other_contact = ROOM_TEXT.replace("@sara_b", "@another_person")
        call.return_value = model_says({**ACCEPT, "contact_telegram": "another_person"})
        self.read(message(2, other_contact, self.now - timedelta(hours=1)))
        review_pending(limit=5)
        listing = HomeListing.objects.get(post__telegram_message_id=2)
        self.assertEqual(listing.contact_telegram, "another_person")
        self.assertEqual(listing.status, ListingStatus.ACTIVE)

    def test_with_the_gate_off_the_same_wording_still_merges(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"review_gate_on": False})
        first = make_post(self.source, 1, posted_at=self.now - timedelta(days=2))
        apply_review(first, dict(ACCEPT))
        second = make_post(self.source, 2, text=ROOM_TEXT.replace("@sara_b", "@another_person"), posted_at=self.now)
        apply_review(second, {**ACCEPT, "contact_telegram": "another_person"})
        self.assertEqual(HomeListing.objects.get(post=second).status, ListingStatus.DUPLICATE)

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
        apply_review(first, dict(ACCEPT))
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
        gate_on()
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

    def test_a_dry_run_counts_a_long_run_of_copies_like_a_real_one(self) -> None:
        for number in range(10, 20):
            make_post(self.source, number, posted_at=self.now - timedelta(days=number))
        planned = settle_backlog(dry_run=True, batch_size=3)
        real = settle_backlog(batch_size=3)
        self.assertEqual((planned["duplicate"], planned["reach_model"]), (real["duplicate"], real["reach_model"]))
        self.assertEqual(real["duplicate"], 11)

    def test_running_it_again_settles_nothing_more(self) -> None:
        settle_backlog(batch_size=2)
        after = self.snapshot()
        again = settle_backlog(batch_size=2)
        self.assertEqual(self.snapshot(), after)
        self.assertEqual((again["settled"], again["reach_model"]), (0, 3))

    def test_it_never_calls_a_model(self) -> None:
        with patch("home.review.ask_model") as call:
            settle_backlog()
        call.assert_not_called()

    def test_it_agrees_with_the_review_on_the_same_posts(self) -> None:
        planned = settle_backlog(dry_run=True)
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

    def test_a_post_the_job_took_meanwhile_is_not_overwritten(self) -> None:
        def decide_then_job_takes_it(post):
            decision = gate_decision(post)
            if post.telegram_message_id == 4:
                HomePost.objects.filter(pk=post.pk).update(status=PostStatus.ACCEPTED)
            return decision

        with patch("home.review_backlog.gate_decision", side_effect=decide_then_job_takes_it):
            settle_backlog()
        self.assertEqual(HomePost.objects.get(telegram_message_id=4).status, PostStatus.ACCEPTED)

    def test_each_batch_takes_the_job_lock_afresh_and_lets_it_go(self) -> None:
        from home import job

        with patch("home.job.acquire_lock", wraps=job.acquire_lock) as acquire, patch("home.job.release_lock", wraps=job.release_lock) as release:
            settle_backlog(batch_size=2)
        self.assertEqual(acquire.call_count, 4)  # Three pages, then the empty one that ends it.
        self.assertEqual(release.call_count, 4)
        self.assertIsNone(HomeSettings.load().job_lock_until)

    def test_a_huge_batch_size_is_cut_down(self) -> None:
        self.assertEqual(_batch_size(100000), MAX_BATCH)
        self.assertEqual(_batch_size(0), 1)

    def test_no_whole_table_is_read_into_memory(self) -> None:
        with CaptureQueriesContext(connection) as queries:
            settle_backlog(dry_run=True, batch_size=2)
        full_rows = [query["sql"] for query in queries if '"review_json"' in query["sql"] and "LIMIT" not in query["sql"]]
        self.assertEqual(full_rows, [])

    def test_a_held_lock_stops_it_with_a_message_and_nothing_written(self) -> None:
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


class CopiesOfAChangeableVerdictTests(TestCase):
    """A copy settles against a waiting original. If the model then changes its mind about the original, the copy comes back."""

    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        gate_on()
        self.source = make_source()
        self.now = timezone.now()
        self.original = make_post(self.source, 1, posted_at=self.now - timedelta(days=3))

    def copy(self, number: int = 2, days_ago: int = 1) -> HomePost:
        post = make_post(self.source, number, posted_at=self.now - timedelta(days=days_ago))
        self.assertEqual(precheck(post).reject_reason, "repost")
        post.refresh_from_db()
        self.assertEqual(post.review_json, {"repost_of": self.original.pk})
        return post

    def test_a_changeable_verdict_sends_the_copy_back_and_the_model_sees_it(self) -> None:
        for reason in ("noise", "incomplete", "other"):
            HomePost.objects.exclude(pk=self.original.pk).delete()
            HomePost.objects.filter(pk=self.original.pk).update(status=PostStatus.NEW, reject_reason="", review_json=None, model="")
            self.original.refresh_from_db()
            copy = self.copy()
            finish_with_result(self.original, model_says({"accept": False, "reject_reason": reason}))
            copy.refresh_from_db()
            self.assertEqual((copy.status, copy.reject_reason, copy.model, copy.review_json, copy.reviewed_at), (PostStatus.NEW, "", "", None, None), reason)
            self.assertIsNone(precheck(copy), reason)

    def test_a_listing_with_nothing_usable_counts_as_incomplete(self) -> None:
        copy = self.copy()
        finish_with_result(self.original, model_says({"accept": True, "kind": "bogus"}))
        copy.refresh_from_db()
        self.assertEqual(copy.status, PostStatus.NEW)

    def test_a_firm_verdict_stands_for_the_copy_and_it_drops_its_photos(self) -> None:
        copy = self.copy()
        HomeMedia.objects.create(source=self.source, post=copy, telegram_message_id=2)
        finish_with_result(self.original, model_says({"accept": False, "reject_reason": "ad"}))
        copy.refresh_from_db()
        self.assertEqual(copy.reject_reason, "repost")
        self.assertEqual(copy.media.count(), 0)

    def test_an_accept_stands_for_the_copy_it_drops_its_photos_and_the_listing_counts_it(self) -> None:
        copy = self.copy()
        HomeMedia.objects.create(source=self.source, post=copy, telegram_message_id=2)
        finish_with_result(self.original, model_says(ACCEPT))
        copy.refresh_from_db()
        self.assertEqual(copy.reject_reason, "repost")
        self.assertEqual(copy.media.count(), 0)
        self.assertEqual(HomeListing.objects.get(post=self.original).last_seen, copy.posted_at)

    def test_the_copy_keeps_its_photos_while_it_can_come_back(self) -> None:
        copy = self.copy()
        HomeMedia.objects.create(source=self.source, post=copy, telegram_message_id=2)
        settled_again = make_post(self.source, 3, posted_at=self.now)
        HomeMedia.objects.create(source=self.source, telegram_message_id=3)
        precheck(settled_again)
        self.assertEqual(link_media(self.source, gate=True), 1)
        self.assertEqual((copy.media.count(), settled_again.media.count()), (1, 1))
        finish_with_result(self.original, model_says({"accept": False, "reject_reason": "noise"}))
        copy.refresh_from_db()
        self.assertEqual((copy.status, copy.media.count()), (PostStatus.NEW, 1))

    def test_a_late_photo_of_a_copy_of_a_decided_post_is_dropped(self) -> None:
        apply_review(self.original, dict(ACCEPT))
        copy = make_post(self.source, 2, posted_at=self.now)
        precheck(copy)
        HomeMedia.objects.create(source=self.source, telegram_message_id=2)
        self.assertEqual(link_media(self.source, gate=True), 0)
        self.assertEqual(HomeMedia.objects.count(), 0)

    def test_the_update_is_bounded_and_takes_the_newest(self) -> None:
        oldest = self.copy(2, days_ago=3)
        middle = self.copy(3, days_ago=2)
        newest = self.copy(4, days_ago=1)
        with patch("home.review.GATE_REQUEUE_LIMIT", 2):
            finish_with_result(self.original, model_says({"accept": False, "reject_reason": "noise"}))
        for post in (oldest, middle, newest):
            post.refresh_from_db()
        self.assertEqual((oldest.status, middle.status, newest.status), (PostStatus.REJECTED, PostStatus.NEW, PostStatus.NEW))

    def test_copies_take_their_turn_one_call_at_a_time(self) -> None:
        low, high = self.copy(2, days_ago=2), self.copy(3, days_ago=1)
        finish_with_result(self.original, model_says({"accept": False, "reject_reason": "noise"}))
        low.refresh_from_db()
        high.refresh_from_db()
        self.assertEqual(precheck(high).reject_reason, "repost")
        high.refresh_from_db()
        self.assertEqual(high.review_json, {"repost_of": low.pk})
        self.assertIsNone(precheck(low))
        finish_with_result(low, model_says({"accept": False, "reject_reason": "noise"}))
        high.refresh_from_db()
        self.assertEqual(high.status, PostStatus.NEW)
        self.assertIsNone(precheck(high))

    def test_only_a_copy_still_settled_by_rule_comes_back(self) -> None:
        copy = self.copy()
        other = self.copy(3)
        HomePost.objects.filter(pk=other.pk).update(status=PostStatus.ACCEPTED, reject_reason="", model="m")
        finish_with_result(self.original, model_says({"accept": False, "reject_reason": "noise"}))
        copy.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((copy.status, other.status, other.model), (PostStatus.NEW, PostStatus.ACCEPTED, "m"))

    def test_a_verdict_landing_as_the_copy_settles_is_not_missed(self) -> None:
        stale_original = HomePost.objects.get(pk=self.original.pk)
        finish_with_result(self.original, model_says({"accept": False, "reject_reason": "noise"}))
        self.assertEqual(stale_original.status, PostStatus.NEW)
        copy = make_post(self.source, 2, posted_at=self.now)
        settle_duplicate(copy, stale_original)
        copy.refresh_from_db()
        self.assertEqual((copy.status, copy.review_json), (PostStatus.NEW, None))

    def test_a_verdict_that_lost_a_race_sends_nothing_back(self) -> None:
        copy = self.copy()
        stale = HomePost.objects.get(pk=self.original.pk)
        HomePost.objects.filter(pk=self.original.pk).update(status=PostStatus.ACCEPTED)
        finish_with_result(stale, model_says({"accept": False, "reject_reason": "noise"}))
        copy.refresh_from_db()
        self.assertEqual(copy.reject_reason, "repost")

    def test_with_the_gate_off_nothing_comes_back(self) -> None:
        copy = self.copy()
        HomeSettings.objects.filter(pk=1).update(review_gate_on=False)
        finish_with_result(self.original, model_says({"accept": False, "reject_reason": "noise"}))
        copy.refresh_from_db()
        self.assertEqual(copy.reject_reason, "repost")


class GateOffRefusesWritesTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        HomeSettings.load()
        self.source = make_source()
        make_post(self.source, 1, text=NO_HOUSING_WORD, posted_at=timezone.now())

    def test_settling_refuses_and_writes_nothing(self) -> None:
        self.assertEqual(settle_backlog(), {"refused": GATE_OFF})
        self.assertEqual(HomePost.objects.get().status, PostStatus.NEW)

    def test_a_dry_run_still_counts(self) -> None:
        self.assertEqual(settle_backlog(dry_run=True)["no_housing_word"], 1)
        self.assertEqual(HomePost.objects.get().status, PostStatus.NEW)

    def test_requeue_refuses_and_writes_nothing(self) -> None:
        HomePost.objects.update(status=PostStatus.REJECTED, reject_reason="noise", model="rule", review_json={"rule": GATE_RULE_NO_WORD})
        with patch("home.review_backlog.housing_word", return_value=True):
            self.assertEqual(requeue_rule_rejects(), {"refused": GATE_OFF})
            self.assertEqual(HomePost.objects.get().status, PostStatus.REJECTED)
            self.assertEqual(requeue_rule_rejects(dry_run=True)["requeued"], 1)
        self.assertEqual(HomePost.objects.get().status, PostStatus.REJECTED)

    def test_switching_it_off_stops_a_run_at_the_next_batch(self) -> None:
        gate_on()
        for number in range(2, 8):
            make_post(self.source, number, text=f"{NO_HOUSING_WORD} {'الف' * number}", posted_at=timezone.now())

        def switch_off(_line: str) -> None:
            HomeSettings.objects.filter(pk=1).update(review_gate_on=False)

        report = settle_backlog(batch_size=2, say=switch_off)
        self.assertEqual(report["stopped"], GATE_OFF)
        self.assertEqual(report["no_housing_word"], 2)
        self.assertEqual(HomePost.objects.filter(status=PostStatus.NEW).count(), 5)

    def test_switching_it_off_stops_a_requeue_at_the_next_batch(self) -> None:
        gate_on()
        for number in range(2, 5):
            make_post(self.source, number, text=f"{NO_HOUSING_WORD} {'الف' * number}", status=PostStatus.REJECTED, reject_reason="noise", model="rule", review_json={"rule": GATE_RULE_NO_WORD})
        calls = []

        def passes_then_off(_text: str) -> bool:
            calls.append(1)
            HomeSettings.objects.filter(pk=1).update(review_gate_on=False)
            return True

        with patch("home.review_backlog.housing_word", side_effect=passes_then_off):
            report = requeue_rule_rejects(batch_size=1)
        self.assertEqual((report["requeued"], report["stopped"]), (1, GATE_OFF))

    def test_the_command_fails_loudly(self) -> None:
        for args in ((), ("--requeue-noise",)):
            with self.assertRaises(CommandError):
                call_command("settle_review_backlog", *args, stdout=StringIO())
        call_command("settle_review_backlog", "--dry-run", stdout=StringIO(), stderr=StringIO())


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

    def assert_only_rule_verdict(self, post: HomePost) -> None:
        post.refresh_from_db()
        self.assertEqual((post.status, post.reject_reason, post.model, post.review_json), (PostStatus.REJECTED, "noise", "rule", {"rule": GATE_RULE_NO_WORD}))
        self.assertFalse(HomeListing.objects.filter(post=post).exists())

    def test_a_rejected_sample_keeps_the_rule_verdict(self, call, _enabled) -> None:
        call.return_value = model_says({"accept": False, "reject_reason": "ad"})
        post = self.rule_rejects(1)[0]
        report = sample_rule_rejects()
        self.assertEqual((report["sampled"], report["missed"], report["miss_rate"]), (1, 0, 0.0))
        self.assert_only_rule_verdict(post)
        self.assertEqual(HomeGateSample.objects.get().model_reason, "ad")

    def test_a_miss_is_only_recorded_and_logged(self, call, _enabled) -> None:
        call.return_value = model_says(ACCEPT)
        post = self.rule_rejects(1)[0]
        with patch("home.review.apply_review") as apply, self.assertLogs("home.review_backlog", level="WARNING") as logs:
            report = sample_rule_rejects()
        apply.assert_not_called()
        self.assertEqual(report["miss_rate"], 1.0)
        self.assertIn("miss_rate=1.0", logs.output[0])
        self.assert_only_rule_verdict(post)
        self.assertFalse(HomeListing.objects.exists())
        sample = HomeGateSample.objects.get(post=post)
        self.assertEqual((sample.answered, sample.missed), (True, True))

    def test_a_failed_call_is_recorded_and_changes_nothing(self, call, _enabled) -> None:
        call.return_value = ChatResult(ok=False, text="", model="m", error="busy")
        post = self.rule_rejects(1)[0]
        report = sample_rule_rejects()
        self.assertEqual((report["errors"], report["attempts"], report["sampled"]), (1, 1, 0))
        self.assert_only_rule_verdict(post)
        sample = HomeGateSample.objects.get()
        self.assertEqual((sample.answered, sample.missed, sample.error), (False, False, "busy"))

    def test_a_failed_post_is_not_called_again(self, call, _enabled) -> None:
        call.return_value = ChatResult(ok=True, text="not json", model="m")
        self.rule_rejects(1)
        sample_rule_rejects()
        self.assertEqual(call.call_count, 1)
        self.assertIn("skipped", sample_rule_rejects())
        self.assertEqual(call.call_count, 1)

    def test_a_call_that_raises_is_recorded_too(self, call, _enabled) -> None:
        call.side_effect = RuntimeError("boom")
        self.rule_rejects(1)
        self.assertEqual(sample_rule_rejects()["errors"], 1)
        self.assertEqual(HomeGateSample.objects.get().error, "no usable answer")

    def test_failures_use_up_the_daily_cap(self, call, _enabled) -> None:
        call.return_value = ChatResult(ok=False, text="", model="m", error="busy")
        self.rule_rejects(GATE_SAMPLE_PER_DAY + 3)
        sample_rule_rejects()
        self.assertEqual(call.call_count, GATE_SAMPLE_PER_DAY)
        self.assertIn("skipped", sample_rule_rejects())
        self.assertEqual(call.call_count, GATE_SAMPLE_PER_DAY)

    def test_the_attempt_is_on_record_before_the_call(self, call, _enabled) -> None:
        seen = []

        def look(prompt, model=""):
            seen.append(HomeGateSample.objects.count())
            return model_says({"accept": False, "reject_reason": "ad"})

        call.side_effect = look
        self.rule_rejects(1)
        with patch("home.review_backlog.ThreadPoolExecutor") as pool:
            pool.return_value.__enter__.return_value.map = lambda function, items: [function(item) for item in items]
            sample_rule_rejects()
        self.assertEqual(seen, [1])

    def test_only_failures_still_end_after_two_weeks(self, call, _enabled) -> None:
        first, second = self.rule_rejects(2)
        old = HomeGateSample.objects.create(post=first, error="busy")
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
