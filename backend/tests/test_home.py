from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from ai.openrouter import ChatResult
from home.ingest import ingest_source, ingest_sources
from home.job import pending_posts, review_pending, run_home_job
from home.models import (
    HomeListing,
    HomePost,
    HomeSource,
    ListingStatus,
    PostStatus,
    PropertyType,
)
from home.review import apply_review, parse_json, review_post, text_key

CRON_SECRET = "cron-test-secret"

ROOM_TEXT = (
    "🏠 اتاق ۱۶ متری مبله در برلین نویکلن\n"
    "💶 اجاره گرم ۵۲۰ یورو، کاتسیون یک ماه\n"
    "Anmeldung ✅ از اول نوامبر. فقط خانم. @sara_b"
)

ACCEPT = {
    "accept": True,
    "kind": "offer",
    "property_type": "room",
    "country": "DE",
    "city": "Berlin",
    "area": "Neukölln",
    "price_amount": 520,
    "price_currency": "EUR",
    "price_period": "month",
    "price_basis": "warm",
    "deposit_months": 1,
    "size_m2": 16,
    "furnished": "yes",
    "registration": "yes",
    "available_from": "2026-11-01",
    "roommate_gender": "female",
    "lister_type": "owner",
    "contact_telegram": "sara_b",
    "summary_fa": "اتاق ۱۶ متری مبله در نویکلن برلین، ۵۲۰ یورو گرم. @sara_b",
    "flags": {"wg": True, "bogus": True, "kaution_text": "1 Monatsmiete"},
    "confidence": {"kind": 0.97, "price": 0.9, "overall": 0.92},
}


def make_source(**overrides) -> HomeSource:
    values = {"username": "berlin_khane", "country": "DE", "default_city": "Berlin", "default_currency": "EUR"}
    values.update(overrides)
    return HomeSource.objects.create(**values)


def make_post(source: HomeSource, message_id: int = 1, text: str = ROOM_TEXT, **overrides) -> HomePost:
    values = {
        "source": source,
        "telegram_message_id": message_id,
        "posted_at": datetime(2026, 10, 10, 8, 0, tzinfo=dt_timezone.utc),
        "text": text,
        "text_key": text_key(text),
    }
    values.update(overrides)
    return HomePost.objects.create(**values)


class ReviewTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()

    def test_accepted_post_becomes_a_clean_listing(self) -> None:
        post = make_post(self.source)
        outcome = apply_review(post, dict(ACCEPT), model="m")
        self.assertTrue(outcome.accepted)
        listing = HomeListing.objects.get(post=post)
        self.assertEqual(listing.kind, "offer")
        self.assertEqual(listing.property_type, PropertyType.ROOM)
        self.assertEqual(listing.price_amount, Decimal("520.00"))
        self.assertEqual(listing.price_currency, "EUR")
        self.assertEqual(listing.price_basis, "warm")
        self.assertEqual(listing.deposit_months, Decimal("1.0"))
        self.assertEqual(listing.roommate_gender, "female")
        self.assertEqual(listing.contact_telegram, "sara_b")
        self.assertEqual(listing.flags, {"wg": True, "kaution_text": "1 Monatsmiete"})
        self.assertNotIn("@", listing.summary_fa)
        self.assertFalse(listing.needs_review)
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.ACCEPTED)

    def test_rejected_post_keeps_the_reason_and_makes_no_listing(self) -> None:
        post = make_post(self.source, text="مشاور املاک رسمی، وام مسکن با ضمانت. تماس بگیرید. " * 2)
        apply_review(post, {"accept": False, "reject_reason": "ad"})
        post.refresh_from_db()
        self.assertEqual((post.status, post.reject_reason), (PostStatus.REJECTED, "ad"))
        self.assertFalse(HomeListing.objects.exists())

    def test_unknown_reject_reason_becomes_other(self) -> None:
        post = make_post(self.source)
        apply_review(post, {"accept": False, "reject_reason": "weird"})
        post.refresh_from_db()
        self.assertEqual(post.reject_reason, "other")

    def test_implausible_price_goes_to_the_review_queue(self) -> None:
        post = make_post(self.source)
        apply_review(post, {**ACCEPT, "price_amount": 52000})
        listing = HomeListing.objects.get(post=post)
        self.assertTrue(listing.needs_review)
        self.assertIn("outside", listing.review_note)

    def test_low_confidence_needs_review(self) -> None:
        post = make_post(self.source)
        apply_review(post, {**ACCEPT, "confidence": {"overall": 0.4}})
        listing = HomeListing.objects.get(post=post)
        self.assertTrue(listing.needs_review)
        self.assertIn("low confidence", listing.review_note)

    def test_an_offer_without_a_price_is_flagged_not_queued(self) -> None:
        post = make_post(self.source)
        apply_review(post, {**ACCEPT, "price_amount": None})
        listing = HomeListing.objects.get(post=post)
        self.assertTrue(listing.flags["price_missing"])
        self.assertFalse(listing.needs_review)

    def test_wanted_post_keeps_the_budget_ceiling(self) -> None:
        post = make_post(self.source, text="سلام، دنبال خونه یه خوابه در شارلوتنبورگ هستم، تا هزار یورو. @someone")
        apply_review(post, {**ACCEPT, "kind": "wanted", "price_amount": None, "price_max": 1000, "property_type": "apartment"})
        listing = HomeListing.objects.get(post=post)
        self.assertEqual((listing.kind, listing.price_max, listing.needs_review), ("wanted", Decimal("1000.00"), False))
        self.assertEqual(listing.lister_type, "unknown")

    def test_bare_amount_uses_the_source_currency(self) -> None:
        post = make_post(self.source)
        apply_review(post, {**ACCEPT, "price_currency": ""})
        self.assertEqual(HomeListing.objects.get(post=post).price_currency, "EUR")

    def test_deposit_amount_and_months_are_not_both_kept(self) -> None:
        post = make_post(self.source)
        apply_review(post, {**ACCEPT, "deposit_amount": 1040, "deposit_months": 2})
        listing = HomeListing.objects.get(post=post)
        self.assertEqual(listing.deposit_amount, Decimal("1040.00"))
        self.assertIsNone(listing.deposit_months)
        self.assertTrue(listing.needs_review)

    def test_end_date_before_start_is_dropped(self) -> None:
        post = make_post(self.source)
        apply_review(post, {**ACCEPT, "available_to": "2026-10-01"})
        listing = HomeListing.objects.get(post=post)
        self.assertIsNone(listing.available_to)
        self.assertTrue(listing.needs_review)

    def test_agency_source_labels_listings_as_agent(self) -> None:
        agency = make_source(username="ejare_agency", is_agency=True)
        post = make_post(agency)
        apply_review(post, {**ACCEPT, "lister_type": "owner"})
        self.assertEqual(HomeListing.objects.get(post=post).lister_type, "agent")

    def test_contact_falls_back_to_a_handle_in_the_text(self) -> None:
        post = make_post(self.source, text=ROOM_TEXT + " ")
        apply_review(post, {**ACCEPT, "contact_telegram": "berlin_khane"})
        self.assertEqual(HomeListing.objects.get(post=post).contact_telegram, "sara_b")

    def test_missing_city_and_area_on_a_general_board_is_incomplete(self) -> None:
        board = make_source(username="helfenads", default_city="")
        post = make_post(board)
        outcome = apply_review(post, {**ACCEPT, "city": "", "area": ""})
        self.assertFalse(outcome.accepted)
        post.refresh_from_db()
        self.assertEqual(post.reject_reason, "incomplete")

    def test_invalid_kind_is_incomplete(self) -> None:
        post = make_post(self.source)
        apply_review(post, {**ACCEPT, "kind": "banana"})
        post.refresh_from_db()
        self.assertEqual((post.status, post.reject_reason), (PostStatus.REJECTED, "incomplete"))


class DuplicateTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()

    def test_same_wording_is_marked_as_a_repost(self) -> None:
        first = make_post(self.source, 1)
        second = make_post(self.source, 2, posted_at=first.posted_at + timedelta(days=2))
        apply_review(first, dict(ACCEPT))
        apply_review(second, dict(ACCEPT))
        original = HomeListing.objects.get(post=first)
        repost = HomeListing.objects.get(post=second)
        self.assertEqual(repost.status, ListingStatus.DUPLICATE)
        self.assertEqual(repost.canonical, original)
        original.refresh_from_db()
        self.assertEqual(original.last_seen, second.posted_at)

    def test_same_contact_and_price_with_new_wording_is_a_repost(self) -> None:
        first = make_post(self.source, 1)
        second = make_post(self.source, 2, text="Zimmer in Neukölln, 520 warm, Kontakt sara_b, ab November. " * 2)
        apply_review(first, dict(ACCEPT))
        apply_review(second, {**ACCEPT, "price_amount": 530})
        self.assertEqual(HomeListing.objects.get(post=second).status, ListingStatus.DUPLICATE)

    def test_different_city_is_not_a_repost(self) -> None:
        first = make_post(self.source, 1)
        second = make_post(self.source, 2, text="Zimmer in Frankfurt, 520 warm, Kontakt sara_b, ab November. " * 2)
        apply_review(first, dict(ACCEPT))
        apply_review(second, {**ACCEPT, "city": "Frankfurt"})
        self.assertEqual(HomeListing.objects.get(post=second).status, ListingStatus.ACTIVE)


class LlmCallTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()

    def test_short_text_is_rejected_without_calling_the_model(self) -> None:
        post = make_post(self.source, text="سلام")
        with patch("home.review.ask_model") as call:
            outcome = review_post(post)
        call.assert_not_called()
        self.assertFalse(outcome.accepted)
        post.refresh_from_db()
        self.assertEqual((post.status, post.reject_reason), (PostStatus.REJECTED, "noise"))

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_model_json_with_fences_is_accepted(self, call, _enabled) -> None:
        call.return_value = ChatResult(ok=True, text="```json\n" + json.dumps(ACCEPT) + "\n```", model="gpt-x")
        post = make_post(self.source)
        outcome = review_post(post)
        self.assertTrue(outcome.accepted)
        post.refresh_from_db()
        self.assertEqual(post.model, "gpt-x")
        prompt = call.call_args.args[0]
        self.assertIn("Source default currency: EUR", prompt)
        self.assertIn("Posted at: 2026-10-10", prompt)

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_model_failure_marks_an_error_and_keeps_the_post(self, call, _enabled) -> None:
        call.return_value = ChatResult(ok=False, error="quota", model="m")
        post = make_post(self.source)
        outcome = review_post(post)
        self.assertEqual(outcome.error, "quota")
        post.refresh_from_db()
        self.assertEqual((post.status, post.error), (PostStatus.ERROR, "quota"))
        self.assertFalse(HomeListing.objects.exists())

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_unparseable_reply_is_an_error(self, call, _enabled) -> None:
        call.return_value = ChatResult(ok=True, text="sorry I cannot", model="m")
        post = make_post(self.source)
        review_post(post)
        post.refresh_from_db()
        self.assertEqual((post.status, post.error), (PostStatus.ERROR, "Invalid model JSON."))

    @patch("home.review.llm_enabled", return_value=False)
    def test_no_llm_leaves_the_post_waiting(self, _enabled) -> None:
        post = make_post(self.source)
        outcome = review_post(post)
        self.assertIn("No LLM", outcome.error)
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.NEW)

    def test_parse_json_handles_noise_around_the_object(self) -> None:
        self.assertEqual(parse_json('here: {"accept": false} thanks'), {"accept": False})
        self.assertIsNone(parse_json("nothing"))


class JobTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_review_pending_works_newest_first_and_reports_what_is_left(self, call, _enabled) -> None:
        call.return_value = ChatResult(ok=True, text=json.dumps({"accept": False, "reject_reason": "noise"}), model="m")
        for index in range(3):
            make_post(
                self.source,
                index + 1,
                text=f"پست شماره {index} که به اندازه کافی طولانی است تا از قاعده متن کوتاه رد شود.",
                posted_at=datetime(2026, 10, 1 + index, tzinfo=dt_timezone.utc),
            )
        result = review_pending(limit=2)
        self.assertEqual((result["reviewed"], result["rejected"], result["remaining"]), (2, 2, 1))
        self.assertEqual(HomePost.objects.get(telegram_message_id=3).status, PostStatus.REJECTED)
        self.assertEqual(HomePost.objects.get(telegram_message_id=1).status, PostStatus.NEW)

    def test_failed_posts_are_retried_only_after_a_pause(self) -> None:
        recent = make_post(self.source, 1, status=PostStatus.ERROR, reviewed_at=timezone.now())
        old = make_post(self.source, 2, status=PostStatus.ERROR, reviewed_at=timezone.now() - timedelta(hours=5))
        ids = {post.pk for post in pending_posts()}
        self.assertIn(old.pk, ids)
        self.assertNotIn(recent.pk, ids)


class FakeReader:
    """Newest-first pages like Telethon's get_messages with offset_id."""

    def __init__(self, messages: list[dict], page_size: int = 2) -> None:
        self.messages = sorted(messages, key=lambda m: -m["telegram_message_id"])
        self.page_size = page_size
        self.calls: list[int | None] = []

    def page(self, username: str, before: int | None):
        self.calls.append(before)
        rows = [m for m in self.messages if before is None or m["telegram_message_id"] < before]
        return rows[: self.page_size]

    def error_for(self, username: str) -> str:
        return ""


def raw(message_id: int, days_ago: float, text: str = "") -> dict:
    return {
        "telegram_message_id": message_id,
        "posted_at": timezone.now() - timedelta(days=days_ago),
        "text": text or f"آگهی اجاره شماره {message_id} در برلین",
        "author_username": "",
        "author_name": "",
        "source_url": f"https://t.me/berlin_khane/{message_id}",
        "views": 5,
        "has_photo": False,
    }


class IngestTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()

    def test_first_run_reads_down_to_the_cutoff_and_stores_each_message_once(self) -> None:
        reader = FakeReader([raw(5, 1), raw(4, 2), raw(3, 3), raw(2, 40), raw(1, 41)])
        result = ingest_source(reader, self.source, days=30, stop_at=None)
        self.assertEqual(result["created"], 3)
        self.assertEqual(sorted(HomePost.objects.values_list("telegram_message_id", flat=True)), [3, 4, 5])
        self.source.refresh_from_db()
        self.assertEqual(self.source.newest_message_id, 5)
        self.assertTrue(self.source.backfill_complete)
        again = ingest_source(FakeReader([raw(5, 1), raw(4, 2), raw(3, 3)]), self.source, days=30, stop_at=None)
        self.assertEqual(again["created"], 0)

    def test_next_run_only_reads_what_is_new(self) -> None:
        ingest_source(FakeReader([raw(5, 1), raw(4, 2), raw(3, 3)]), self.source, days=30, stop_at=None)
        reader = FakeReader([raw(7, 0.1), raw(6, 0.5), raw(5, 1), raw(4, 2), raw(3, 3)])
        result = ingest_source(reader, self.source, days=30, stop_at=None)
        self.assertEqual(result["created"], 2)
        self.assertEqual(reader.calls, [None, 6])
        self.source.refresh_from_db()
        self.assertEqual(self.source.newest_message_id, 7)

    def test_messages_without_text_are_skipped(self) -> None:
        reader = FakeReader([{**raw(2, 1), "text": "  "}, raw(1, 2)])
        ingest_source(reader, self.source, days=30, stop_at=None)
        self.assertEqual(list(HomePost.objects.values_list("telegram_message_id", flat=True)), [1])

    def test_a_failed_read_is_recorded_on_the_source(self) -> None:
        class Broken(FakeReader):
            def page(self, username, before):
                return None

            def error_for(self, username):
                return "ChannelPrivateError"

        result = ingest_source(Broken([]), self.source, days=30, stop_at=None)
        self.assertEqual(result["error"], "ChannelPrivateError")
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_error, "ChannelPrivateError")
        self.assertIsNone(self.source.newest_message_id)

    def test_only_enabled_sources_are_read(self) -> None:
        make_source(username="off_channel", enabled=False)
        reader = FakeReader([raw(1, 1)])
        result = ingest_sources(days=30, reader=reader)
        self.assertEqual(list(result["sources"]), ["berlin_khane"])

    def test_missing_telegram_session_is_reported(self) -> None:
        with patch("home.ingest.history_configured", return_value=False):
            result = ingest_sources(days=30)
        self.assertFalse(result["ok"])


@override_settings(CRON_SECRET=CRON_SECRET)
class CronTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        make_source()

    def test_requires_the_cron_secret(self) -> None:
        self.assertEqual(self.client.get("/api/cron/home/").status_code, 403)

    @patch("api.cron_views.run_home_job", return_value={"ok": True, "step": "review", "review": {"reviewed": 0}})
    def test_review_step_runs_with_a_clamped_limit(self, job) -> None:
        response = self.client.get(
            "/api/cron/home/?step=review&limit=999", HTTP_AUTHORIZATION=f"Bearer {CRON_SECRET}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(job.call_args.kwargs["step"], "review")
        self.assertEqual(job.call_args.kwargs["review_limit"], 100)

    @override_settings(HOME_ENABLED=False)
    def test_can_be_switched_off(self) -> None:
        response = self.client.get("/api/cron/home/", HTTP_AUTHORIZATION=f"Bearer {CRON_SECRET}")
        self.assertEqual(response.status_code, 503)


class SeedTests(TestCase):
    def test_germany_sources_are_seeded_and_the_city_ones_are_on(self) -> None:
        sources = {s.username: s for s in HomeSource.objects.all()}
        for name in ("berlin_khane", "BerlinerMaskan", "frankfurt_khane", "munich_online", "helfenads"):
            self.assertTrue(sources[name].enabled)
            self.assertEqual((sources[name].country, sources[name].default_currency), ("DE", "EUR"))
        for name in ("Khanehamburg", "hamburg_khane", "koln_khane"):
            self.assertTrue(sources[name].enabled)
        self.assertEqual(sources["helfenads"].default_city, "")

    def test_every_checked_source_of_the_six_cities_is_on_and_new_finds_wait(self) -> None:
        from importlib import import_module

        seed = import_module("home.migrations.0014_seed_city_channels")
        cities = ["Toronto", "Montreal", "Vancouver", "Berlin", "Hamburg", "Cologne"]
        found = [row[0] for row in seed.CANDIDATES]
        off = HomeSource.objects.filter(enabled=False, default_city__in=cities).exclude(username__in=found)
        self.assertEqual(list(off.values_list("username", flat=True)), [])
        self.assertEqual(HomeSource.objects.filter(username__in=found, enabled=False).count(), len(found))
        self.assertEqual(HomeSource.objects.get(username="iranian_koln").default_currency, "EUR")

    def test_a_first_read_goes_back_ten_days(self) -> None:
        from home import conf

        self.assertEqual(conf.lookback_days(), 10)
        self.assertEqual(conf.publish_window_days(), 10)


class ResumeTests(TestCase):
    """Big groups take several runs; each run must continue where the last one stopped."""

    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()

    def test_a_first_fill_cut_short_continues_below_next_time(self) -> None:
        from home import ingest

        messages = [raw(n, 1 + (200 - n) * 0.1) for n in range(200, 0, -1)]
        with patch.object(ingest, "MAX_PAGES_PER_SOURCE", 3):
            first = ingest_source(FakeReader(messages, page_size=10), self.source, days=30, stop_at=None)
            self.source.refresh_from_db()
            self.assertEqual(first["created"], 30)
            self.assertEqual((self.source.newest_message_id, self.source.backfill_complete), (200, False))
            second = ingest_source(FakeReader(messages, page_size=10), self.source, days=30, stop_at=None)
        self.assertEqual(second["created"], 20)
        self.assertEqual(min(HomePost.objects.values_list("telegram_message_id", flat=True)), 151)

    def test_a_failed_read_says_why_in_plain_words(self) -> None:
        class Silent(FakeReader):
            def page(self, username, before):
                return None

            def error_for(self, username):
                return ""

        result = ingest_source(Silent([]), self.source, days=30, stop_at=None)
        self.assertIn("did not answer in time", result["error"])

    def test_a_later_run_that_stops_early_does_not_skip_messages(self) -> None:
        from home import ingest

        ingest_source(FakeReader([raw(n, 1) for n in range(10, 0, -1)]), self.source, days=30, stop_at=None)
        self.source.refresh_from_db()
        self.assertEqual(self.source.newest_message_id, 10)
        newer = [raw(n, 0.5) for n in range(40, 10, -1)] + [raw(n, 1) for n in range(10, 0, -1)]
        with patch.object(ingest, "MAX_PAGES_PER_SOURCE", 1):
            ingest_source(FakeReader(newer, page_size=10), self.source, days=30, stop_at=None)
        self.source.refresh_from_db()
        self.assertEqual(self.source.newest_message_id, 10)
        ingest_source(FakeReader(newer, page_size=10), self.source, days=30, stop_at=None)
        self.assertEqual(HomePost.objects.count(), 40)


class FastReviewTests(TestCase):
    """Reposts are settled by rule before any model call, and model calls run several at a time."""

    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_reposts_never_reach_the_model_and_the_rest_run_in_parallel(self, ask, _enabled) -> None:
        from home.models import HomeSettings

        HomeSettings.objects.update_or_create(pk=1, defaults={"review_workers": 4, "review_model": "gpt-test"})
        ask.return_value = ChatResult(ok=True, text=json.dumps(ACCEPT), model="gpt-test")
        base = timezone.now() - timedelta(days=2)
        for number in range(1, 4):
            make_post(self.source, number, text=f"آگهی متفاوت شماره {number} برای اجاره اتاق در برلین " * 2, posted_at=base + timedelta(minutes=number))
        for number in range(10, 16):
            make_post(self.source, number, text="یک متن تکراری که بارها پست شده برای اجاره اتاق در برلین " * 2, posted_at=base + timedelta(hours=number))
        result = review_pending(limit=50)
        self.assertEqual(result["reposts"], 5)
        self.assertEqual(ask.call_count, 4)
        self.assertEqual({call.kwargs["model"] for call in ask.call_args_list}, {"gpt-test"})
        self.assertEqual(result["remaining"], 0)

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_the_limit_caps_model_calls_not_rule_verdicts(self, ask, _enabled) -> None:
        ask.return_value = ChatResult(ok=True, text=json.dumps({"accept": False, "reject_reason": "ad"}), model="m")
        base = timezone.now() - timedelta(days=2)
        for number in range(1, 6):
            make_post(self.source, number, text=f"پست متفاوت شماره {number} درباره اجاره خانه در برلین " * 2, posted_at=base + timedelta(minutes=number))
        result = review_pending(limit=2)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(result["remaining"], 3)

    def test_the_housing_model_call_uses_json_mode_and_little_reasoning(self) -> None:
        from home import llm

        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({"model": "gpt-5-mini", "choices": [{"message": {"content": '{"accept": false}'}}]}).encode()

        def fake(request, timeout=0):
            captured["body"] = json.loads(request.data)
            return Response()

        with override_settings(OPENAI_API_KEY="sk-test"), patch("home.llm.urllib.request.urlopen", fake):
            result = llm.review_completion("post", system="sys", model="gpt-5-mini")
        self.assertTrue(result.ok)
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(captured["body"]["reasoning_effort"], "minimal")
        self.assertIn("max_completion_tokens", captured["body"])

    def test_without_an_openai_key_the_shared_chain_is_used(self) -> None:
        from home import llm

        with override_settings(OPENAI_API_KEY=""), patch("home.llm.shared_complete", return_value=ChatResult(ok=True, text="{}", model="free")) as shared:
            self.assertEqual(llm.review_completion("p", system="s", model="gpt-5-mini").model, "free")
        shared.assert_called_once()

    def test_an_api_error_is_reported_not_raised(self) -> None:
        import io
        import urllib.error

        from home import llm

        error = urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b'{"error": {"message": "model not found"}}'))
        with override_settings(OPENAI_API_KEY="sk-test"), patch("home.llm.urllib.request.urlopen", side_effect=error):
            result = llm.review_completion("p", system="s", model="nope")
        self.assertFalse(result.ok)
        self.assertIn("model not found", result.error)
