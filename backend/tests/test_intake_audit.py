"""Intake audit 2026-11-05: FloodWait pause, stored Telegram addresses, rich messages, short posts, health, requeue."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from home.health import intake_health
from home.ingest import ingest_sources
from home.models import HomePost, HomeSettings, HomeSource, PostStatus
from home.review import precheck, text_key, too_short
from home.review_backlog import requeue_wrong_rejects, wrong_reject_candidates
from home.telegram import LOOKUP_DEFERRED, MAX_LOOKUPS_PER_RUN, HomeHistoryReader, home_message_to_raw, rich_message_text
from market.telegram_history import flood_seconds


def make_source(**overrides) -> HomeSource:
    values = {"username": "berlin_khane", "country": "DE", "default_city": "Berlin", "default_currency": "EUR"}
    values.update(overrides)
    return HomeSource.objects.create(**values)


def raw(message_id: int, text: str = "") -> dict:
    return {
        "telegram_message_id": message_id,
        "posted_at": timezone.now() - timedelta(hours=1),
        "text": text or f"آگهی اجاره اتاق شماره {message_id} در برلین، ۵۰۰ یورو گرم",
        "author_username": "",
        "author_name": "",
        "source_url": f"https://t.me/x/{message_id}",
        "views": 1,
        "has_photo": False,
    }


class FloodWaitError(Exception):
    """Shaped like telethon.errors.FloodWaitError."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"A wait of {seconds} seconds is required (caused by ResolveUsernameRequest)")
        self.seconds = seconds
        self.request = type("ResolveUsernameRequest", (), {})()


class FloodingReader:
    """The first source reads fine; the second gets a FloodWait, as a run did all afternoon on 2026-11-05."""

    def __init__(self) -> None:
        self.flood_wait = 0
        self.flood_request = ""
        self.read: list[str] = []
        self.learned = {"first": ("channel", 111, 222, "first")}
        self.stale: set[str] = set()

    def page(self, username, before):
        self.read.append(username)
        if username == "second":
            self.flood_wait = 16520
            self.flood_request = "ResolveUsernameRequest"
            return None
        return [raw(10)] if before is None else []

    def error_for(self, username):
        return "A wait of 16520 seconds is required (caused by ResolveUsernameRequest)" if username == "second" else ""


class FloodWaitTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        base = timezone.now() - timedelta(days=1)
        for offset, name in enumerate(("first", "second", "third")):
            make_source(username=name, last_run_at=base + timedelta(minutes=offset))

    def test_flood_wait_stops_the_run_pauses_reading_and_is_not_ok(self) -> None:
        reader = FloodingReader()
        result = ingest_sources(days=30, reader=reader)
        self.assertFalse(result["ok"])
        self.assertIn("FloodWait of 16520 s", result["error"])
        self.assertEqual(reader.read[-1], "second")
        self.assertNotIn("third", reader.read)
        self.assertEqual(result["sources"]["third"], {"skipped": "Telegram asked to wait"})
        paused = HomeSettings.load().ingest_paused_until
        self.assertGreater(paused, timezone.now() + timedelta(seconds=16500))
        # The address found before the wait is kept.
        first = HomeSource.objects.get(username="first")
        self.assertEqual((first.tg_peer_kind, first.tg_peer_id, first.tg_access_hash), ("channel", 111, 222))

    def test_a_paused_run_never_opens_telegram(self) -> None:
        HomeSettings.load()
        HomeSettings.objects.filter(pk=1).update(ingest_paused_until=timezone.now() + timedelta(hours=1), ingest_pause_reason="FloodWait 3600 s")
        with patch("home.ingest.HomeHistoryReader") as reader_class:
            result = ingest_sources(days=30)
        reader_class.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("paused_until", result)

    def test_every_source_failing_is_not_ok(self) -> None:
        class Broken(FloodingReader):
            def page(self, username, before):
                return None

            def error_for(self, username):
                return "ChannelPrivateError"

        result = ingest_sources(days=30, reader=Broken())
        self.assertFalse(result["ok"])
        self.assertEqual(result["source_errors"], 3)

    def test_flood_seconds_reads_only_flood_errors(self) -> None:
        self.assertEqual(flood_seconds(FloodWaitError(278)), 278)
        self.assertEqual(flood_seconds(TimeoutError()), 0)


class PeerCacheTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()

    def _reader(self) -> HomeHistoryReader:
        reader = HomeHistoryReader()
        self.addCleanup(reader._loop.close)
        return reader

    def test_a_stored_address_is_used_without_a_lookup(self) -> None:
        make_source(username="cached", tg_peer_kind="channel", tg_peer_id=1234, tg_access_hash=-99, tg_public_username="cached")
        reader = self._reader()
        with patch("market.telegram_history.HistoryReader._resolve") as lookup:
            entity = reader._loop.run_until_complete(reader._resolve("cached"))
        lookup.assert_not_called()
        self.assertEqual((entity.channel_id, entity.access_hash), (1234, -99))
        self.assertEqual(reader.chat_id("cached"), 1234)
        self.assertFalse(reader.needs_lookup("cached"))

    def test_lookups_are_capped_per_run_and_learned(self) -> None:
        from telethon.tl.types import Channel

        names = [f"group{number}" for number in range(MAX_LOOKUPS_PER_RUN + 2)]
        for name in names:
            make_source(username=name)
        reader = self._reader()

        async def fake_lookup(this, username):
            this._labels[username] = (username, 500 + len(this._labels))
            entity = MagicMock(spec=Channel)
            entity.access_hash = 7
            return entity

        with patch("market.telegram_history.HistoryReader._resolve", fake_lookup):
            found = [reader._loop.run_until_complete(reader._resolve(name)) for name in names]
        self.assertEqual(sum(entity is not None for entity in found), MAX_LOOKUPS_PER_RUN)
        self.assertEqual(reader.error_for(names[-1]), LOOKUP_DEFERRED)
        self.assertEqual(reader.lookups_left(), 0)
        self.assertEqual(len(reader.learned), MAX_LOOKUPS_PER_RUN)

    def test_a_broken_address_is_forgotten(self) -> None:
        make_source(username="moved", tg_peer_kind="channel", tg_peer_id=5, tg_access_hash=6)
        reader = self._reader()
        ChannelInvalidError = type("ChannelInvalidError", (Exception,), {})
        reader._failed("moved", ChannelInvalidError())
        self.assertIn("moved", reader.stale)
        self.assertTrue(reader.needs_lookup("moved"))

    def test_ingest_skips_a_source_that_must_wait_for_a_lookup(self) -> None:
        make_source(username="later")

        class Full(FloodingReader):
            def needs_lookup(self, username):
                return True

            def lookups_left(self):
                return 0

        reader = Full()
        result = ingest_sources(days=30, reader=reader)
        self.assertEqual(result["sources"]["later"], {"skipped": "waits for a username lookup"})
        self.assertEqual(reader.read, [])
        self.assertIsNone(HomeSource.objects.get(username="later").last_run_at)


class TextPlain(SimpleNamespace):
    pass


class TextBold(SimpleNamespace):
    pass


class TextConcat(SimpleNamespace):
    pass


class PageBlockParagraph(SimpleNamespace):
    pass


class PageBlockList(SimpleNamespace):
    pass


class PageListItemText(SimpleNamespace):
    pass


class RichMessageTests(TestCase):
    def rich(self):
        heading = PageBlockParagraph(text=TextBold(text=TextPlain(text="اجاره اتاق در نورث یورک")))
        items = PageBlockList(items=[PageListItemText(text=TextPlain(text="۹۵۰ دلار")), PageListItemText(text=TextConcat(texts=[TextPlain(text="از اول "), TextPlain(text="نوامبر")]))])
        return SimpleNamespace(
            id=7,
            date=timezone.now(),
            message="",
            photo=None,
            action=None,
            sender=None,
            sender_id=1,
            views=1,
            grouped_id=None,
            rich_message=SimpleNamespace(blocks=[heading, items]),
        )

    def test_rich_message_text_reads_the_blocks(self) -> None:
        self.assertEqual(rich_message_text(self.rich()), "اجاره اتاق در نورث یورک\n\n۹۵۰ دلار\nاز اول نوامبر")

    def test_a_rich_message_is_kept_by_the_reader(self) -> None:
        with patch("home.telegram.read_poster", return_value={}):
            row = home_message_to_raw("RentToronto", self.rich(), public_username="RentToronto")
        self.assertIsNotNone(row)
        self.assertIn("نورث یورک", row["text"])

    def test_a_plain_message_has_no_rich_text(self) -> None:
        self.assertEqual(rich_message_text(SimpleNamespace(message="x")), "")


class ShortPostTests(TestCase):
    def test_short_post_with_a_housing_word_reaches_the_model(self) -> None:
        self.assertFalse(too_short("2 bed/ one bath house In Burquitlam"))
        self.assertFalse(too_short("سلام نیاز به خانه دارم دو اتاق"))
        self.assertTrue(too_short("سلام وقت بخیر، کسی میدونه کجا؟"))
        self.assertTrue(too_short("دورخانه"))

    def test_precheck_keeps_a_short_housing_post_for_the_model(self) -> None:
        source = make_source(username="vancouver")
        text = "2 bed & 1 bath in Burquitlam"
        post = HomePost.objects.create(source=source, telegram_message_id=1, posted_at=timezone.now(), text=text, text_key=text_key(text))
        self.assertIsNone(precheck(post, gate=True))
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.NEW)


class HealthTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source(last_run_at=timezone.now())

    def post(self, message_id: int, **values) -> HomePost:
        return HomePost.objects.create(source=self.source, telegram_message_id=message_id, posted_at=timezone.now(), text="x", **values)

    def test_fresh_intake_is_ok(self) -> None:
        self.post(1, status=PostStatus.REJECTED)
        report = intake_health()
        self.assertTrue(report["ok"], report)

    def test_silent_ingest_paused_reader_and_stuck_queue_are_problems(self) -> None:
        old = self.post(1)
        HomePost.objects.filter(pk=old.pk).update(ingested_at=timezone.now() - timedelta(hours=5))
        HomeSettings.load()
        HomeSettings.objects.filter(pk=1).update(ingest_paused_until=timezone.now() + timedelta(hours=2), ingest_pause_reason="FloodWait 7200 s")
        report = intake_health()
        joined = " | ".join(report["problems"])
        self.assertFalse(report["ok"])
        self.assertIn("ingest paused", joined)
        self.assertIn("no new post", joined)
        self.assertIn("waiting for review", joined)

    def test_review_errors_over_the_limit_are_a_problem(self) -> None:
        for number in range(25):
            self.post(number + 1, status=PostStatus.ERROR, reviewed_at=timezone.now())
        self.assertTrue(any("review errors" in line for line in intake_health()["problems"]))

    def test_command_exits_1_on_a_problem(self) -> None:
        with self.assertRaises(SystemExit) as stop:
            call_command("home_health")
        self.assertEqual(stop.exception.code, 1)


class RequeueTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()

    def post(self, message_id: int, text: str, **values) -> HomePost:
        return HomePost.objects.create(
            source=self.source, telegram_message_id=message_id, posted_at=timezone.now(), text=text, text_key=text_key(text), **values
        )

    def test_candidates_and_apply_with_backup(self) -> None:
        agent = self.post(1, "Condo for rent Sheppard and 404, 2 bedrooms, price 2700, call the broker", status=PostStatus.REJECTED, reject_reason="ad", model="nemotron")
        self.post(2, "Condo for rent Sheppard and 404, 2 bedrooms, price 2700, call the broker", status=PostStatus.REJECTED, reject_reason="repost", model="rule")
        short = self.post(3, "2 bed & 1 bath in Burquitlam", status=PostStatus.REJECTED, reject_reason="noise", model="rule")
        self.post(4, "Moving company, best prices in Toronto, call now for a quote today", status=PostStatus.REJECTED, reject_reason="ad", model="nemotron")
        self.post(5, "سلام", status=PostStatus.REJECTED, reject_reason="noise", model="rule")
        accepted = self.post(6, "Room for rent in Berlin Neukölln, 500 EUR warm", status=PostStatus.ACCEPTED, model="nemotron")
        self.assertEqual(sorted(row["pk"] for row in wrong_reject_candidates()), sorted([agent.pk, short.pk]))
        dry = requeue_wrong_rejects(dry_run=True)
        self.assertEqual(dry["candidates"], 2)
        self.assertEqual(HomePost.objects.filter(status=PostStatus.NEW).count(), 0)
        self.assertIn("refused", requeue_wrong_rejects(dry_run=False))
        with tempfile.TemporaryDirectory() as folder:
            backup = os.path.join(folder, "backup.json")
            done = requeue_wrong_rejects(dry_run=False, backup_path=backup)
            saved = json.load(open(backup, encoding="utf-8"))
        self.assertEqual(done["requeued"], 2)
        self.assertEqual(sorted(row["pk"] for row in saved), sorted([agent.pk, short.pk]))
        agent.refresh_from_db()
        self.assertEqual(agent.status, PostStatus.NEW)
        self.assertEqual(agent.review_json["was"], "ad")
        accepted.refresh_from_db()
        self.assertEqual(accepted.status, PostStatus.ACCEPTED)


class IngestBudgetTests(TestCase):
    """Every source on every tick: reading gets the review's share when the worker reviews, and no source eats the rest."""

    def _run(self, review_on: bool) -> float:
        import time as clock

        from home.job import run_home_job

        HomeSettings.objects.update_or_create(
            pk=1,
            defaults={"ingest_enabled": True, "review_enabled": review_on, "media_enabled": False, "vision_enabled": False,
                      "publish_enabled": False, "alerts_enabled": False, "freshness_enabled": False},
        )
        started = clock.monotonic()
        with patch("home.job.ingest_sources", return_value={"ok": True, "sources": {}, "created": 0}) as ingest, patch(
            "home.job.review_pending", return_value={"reviewed": 0}
        ), patch("home.job.sample_rule_rejects", return_value={}), patch("home.job.backfill_listing_areas", return_value={"ok": True}), patch(
            "home.job.write_snapshots", return_value={"ok": True}
        ):
            run_home_job(step="all", stop_at=started + 270)
        return ingest.call_args.kwargs["stop_at"] - started

    def test_reading_gets_most_of_the_run_when_the_worker_reviews(self) -> None:
        self.assertGreater(self._run(review_on=False), 130)
        self.assertLess(self._run(review_on=True), 70)

    def test_one_source_gets_twice_its_fair_share_at_most(self) -> None:
        import time as clock

        from home.ingest import SOURCE_MIN_SECONDS, _source_stop

        now = clock.monotonic()
        self.assertAlmostEqual(_source_stop(now + 100, left=50) - now, 4.0 if 4.0 > SOURCE_MIN_SECONDS else SOURCE_MIN_SECONDS, delta=0.5)
        self.assertAlmostEqual(_source_stop(now + 100, left=2) - now, 100, delta=0.5)
        self.assertIsNone(_source_stop(None, left=3))

    def test_each_source_is_read_with_its_own_stop(self) -> None:
        HomeSource.objects.all().delete()
        for name in ("a", "b", "c"):
            make_source(username=name)
        seen = []

        def fake(reader, source, *, days, stop_at):
            seen.append((source.username, stop_at))
            return {"created": 0, "pages": 1, "error": "", "backfill_complete": True}

        import time as clock

        overall = clock.monotonic() + 90
        with patch("home.ingest.ingest_source", side_effect=fake):
            ingest_sources(days=30, reader=FloodingReader.__new__(FloodingReader), stop_at=overall)
        self.assertEqual([name for name, _ in seen], ["a", "b", "c"])
        self.assertLess(seen[0][1], overall)


class BornExpiredTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()

    def _accept(self, days_ago: int, kind: str) -> str:
        from home.review import apply_review

        text = f"Room for rent in Berlin Neukölln, 500 EUR warm, from December, message me {days_ago}{kind}"
        post = HomePost.objects.create(
            source=self.source, telegram_message_id=days_ago * 10 + len(kind), posted_at=timezone.now() - timedelta(days=days_ago), text=text, text_key=text_key(text)
        )
        data = {"accept": True, "kind": kind, "property_type": "room", "city": "Berlin", "country": "DE", "price_amount": 500, "price_currency": "EUR", "summary_fa": "اتاق"}
        with patch("home.areas.link_area"):
            outcome = apply_review(post, data, model="m")
        return outcome.listing.status

    def test_an_offer_older_than_its_expiry_is_born_expired(self) -> None:
        self.assertEqual(self._accept(20, "offer"), "expired")
        self.assertEqual(self._accept(5, "offer"), "active")

    def test_wanted_keeps_its_longer_expiry(self) -> None:
        self.assertEqual(self._accept(17, "wanted"), "active")
        self.assertEqual(self._accept(25, "wanted"), "expired")


class RequeuePacingTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source()

    def post(self, message_id: int, reviewed_at) -> HomePost:
        text = f"Condo for rent at Yonge and Sheppard, one bedroom plus den, 2400 dollars, number {message_id}"
        return HomePost.objects.create(
            source=self.source, telegram_message_id=message_id, posted_at=timezone.now() - timedelta(days=5), text=text, text_key=text_key(text),
            status=PostStatus.REJECTED, reject_reason="ad", model="nemotron", reviewed_at=reviewed_at,
        )

    def test_posts_judged_on_the_new_prompt_are_skipped_and_batches_are_capped(self) -> None:
        deploy = timezone.now() - timedelta(hours=2)
        old = [self.post(number, deploy - timedelta(days=1)) for number in range(1, 5)]
        self.post(9, deploy + timedelta(minutes=10))
        self.assertEqual(len(wrong_reject_candidates(days=14)), 5)
        self.assertEqual(sorted(row["pk"] for row in wrong_reject_candidates(days=14, reviewed_before=deploy)), sorted(post.pk for post in old))
        batch = wrong_reject_candidates(days=14, reviewed_before=deploy, limit=2)
        self.assertEqual([row["pk"] for row in batch], [old[3].pk, old[2].pk])

    def test_command_flags(self) -> None:
        deploy = timezone.now() - timedelta(hours=2)
        self.post(1, deploy - timedelta(days=1))
        self.post(2, deploy + timedelta(minutes=5))
        from io import StringIO

        out = StringIO()
        call_command("settle_review_backlog", "--requeue-wrong", "--days", "14", "--dry-run", "--reviewed-before", deploy.isoformat(), "--limit", "5", stdout=out)
        self.assertEqual(json.loads(out.getvalue())["candidates"], 1)


class UnreadSourceHealthTests(TestCase):
    def test_sources_not_read_for_70_minutes_are_a_problem(self) -> None:
        HomeSource.objects.all().delete()
        for number in range(10):
            make_source(username=f"s{number}", last_run_at=timezone.now() - timedelta(minutes=5 if number < 8 else 120))
        HomePost.objects.create(source=HomeSource.objects.first(), telegram_message_id=1, posted_at=timezone.now(), text="x", status=PostStatus.REJECTED)
        report = intake_health()
        self.assertEqual(report["metrics"]["sources_unread_70m"], "2/10")
        self.assertTrue(any("not read for over 70 min" in line for line in report["problems"]))
