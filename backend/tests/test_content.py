"""Channel content automation (CONTENT-SPEC.md): numbers, guards, schedule, links. No Chrome, ffmpeg or network here."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from home.content import data, links, schedule, voice
from home.content import daily as daily_view
from home.content import text as t
from home.models import HomeChannel, HomeContentPost, HomeEntry, HomeListing, HomePost, HomeSettings, HomeSource, PostStatus
from tests.test_home import make_source

NOW = datetime(2026, 11, 11, 1, 30, tzinfo=dt_timezone.utc)  # 20:30 in Toronto, 02:30 in Berlin
_ids = iter(range(1, 10_000))


def listing(source, text: str, *, kind="offer", type="room", price=900, hours_ago=2.0, city="Toronto", area="", status="active", **extra) -> HomeListing:
    n = next(_ids)
    post = HomePost.objects.create(
        source=source, telegram_message_id=n, posted_at=NOW - timedelta(hours=hours_ago), text=text, status=PostStatus.ACCEPTED
    )
    return HomeListing.objects.create(
        post=post, kind=kind, property_type=type, country=source.country, city=city, area=area,
        price_amount=Decimal(price) if price else None, price_currency="CAD" if source.country == "CA" else "EUR",
        price_period="month", status=status, first_seen=post.posted_at, last_seen=post.posted_at, **extra,
    )


class Base(TestCase):
    def setUp(self) -> None:
        self.source = make_source(username="toronto_src", country="CA", default_city="Toronto", default_currency="CAD")
        HomeChannel.objects.update(chat_id=None)
        self.channel, _ = HomeChannel.objects.update_or_create(
            country="CA", city="Toronto", defaults={"aliases": "North York", "username": "chamedoon_toronto_khane", "chat_id": -1001, "enabled": True}
        )

    def market(self, n_rooms=10, room_price=1000, days_ago=10):
        for i in range(n_rooms):
            listing(self.source, f"اتاق مبله شماره {i} نزدیک مترو", price=room_price + i * 10, hours_ago=24 * days_ago + i)


class DataTests(Base):
    def test_reposts_are_counted_once(self) -> None:
        for _ in range(2):
            listing(self.source, "اجاره بیسمنت یک خواب مبله نزدیک فینچ", type="basement", price=1500)
        listing(self.source, "اتاق تمیز در نورث یورک", price=800, city="North York")
        d = data.daily(self.channel, NOW)
        self.assertEqual(d.n, 2)

    def test_window_reads_post_time_not_listing_time(self) -> None:
        old = listing(self.source, "اتاق قدیمی", hours_ago=30)
        HomeListing.objects.filter(pk=old.pk).update(created_at=NOW - timedelta(hours=1))
        listing(self.source, "اتاق تازه یک", hours_ago=1)
        self.assertEqual(data.daily(self.channel, NOW).n, 1)

    def test_median_needs_eight_and_trims_from_twenty(self) -> None:
        rows = [data.Row(i, "offer", "room", p, "CAD", "month", "long_term", "", "", "", "", str(i), NOW, 0, "active", None) for i, p in enumerate([100] + [1000] * 18 + [9000])]
        self.assertEqual(data.medians(rows, "CAD")["room"], (20, 1000.0))
        self.assertNotIn("room", data.medians(rows[:7], "CAD"))

    def test_type_guard_reads_the_post(self) -> None:
        meds = {"room": (20, 1000.0), "basement": (12, 1600.0), "apartment": (30, 2300.0)}
        row = lambda text, kind, price: data.Row(1, "offer", kind, price, "CAD", "month", "", "", "", "", "", text, NOW, 0, "active", None)
        self.assertEqual(data.guarded_type(row("اجاره اتاق در بیسمنت، آشپزخانه مشترک", "basement", 850), meds), "room")
        self.assertEqual(data.guarded_type(row("اجاره بيسمنت اپارتمان يك خواب و يك دن مبله", "apartment", 1500), meds), "basement")
        self.assertEqual(data.guarded_type(row("آپارتمان دو اتاق خواب در داون تاون", "apartment", 2000), meds), "apartment")
        self.assertIsNone(data.guarded_type(row("یک اتاق در آپارتمان سه خوابه", "apartment", 2000), meds))

    def test_deal_is_fifteen_to_sixty_percent_under_with_photo_first(self) -> None:
        self.market()
        listing(self.source, "اتاق ارزان مبله", price=700)  # ~31 % under 1045
        listing(self.source, "اتاق خیلی ارزان", price=300)  # > 60 %: likely wrong data
        d = data.daily(self.channel, NOW)
        self.assertIsNotNone(d.deal, (d.n, d.meds, [(r.price, r.type, r.status, r.mode, r.currency) for r in data.rows_for(self.channel, NOW - timedelta(days=1), NOW)]))
        row, kind, discount = d.deal
        self.assertEqual((row.price, kind), (700.0, "room"))
        self.assertTrue(0.15 <= discount <= 0.60)

    def test_fewer_than_three_new_skips_the_day(self) -> None:
        listing(self.source, "اتاق یک", price=900)
        self.assertTrue(data.daily(self.channel, NOW).skip)


class ViewTests(Base):
    def test_caption_links_and_mandatory_last_line(self) -> None:
        self.market()
        for i in range(3):
            listing(self.source, f"اتاق مبله تازه {i}", price=700 + i)
        view = daily_view.build(data.daily(self.channel, NOW), NOW.date(), "chamedoon_toronto_khane")
        self.assertEqual(view.caption.splitlines()[-1], "@chamedoon_toronto_khane · @chamedoon_home_bot")
        self.assertIn("startapp=dg_toronto_20261111", view.caption)
        self.assertIn("startapp=dg_toronto_20261111_alert", view.caption)
        self.assertIn("startapp=dg_toronto_20261111_post", view.caption)
        self.assertIn("startapp=deal_toronto_", view.caption)
        self.assertIn("📅 ۲۰ آبان ۱۴۰۵ · Nov 11, 2026", view.caption)
        self.assertEqual((view.card["chip"], view.card["chip_en"]), ("۲۰ آبان ۱۴۰۵", "Nov 11, 2026"))
        self.assertEqual(view.expected[0], 3)
        self.assertIn("سه آگهی", view.script)

    def test_numbers_as_words(self) -> None:
        self.assertEqual(t.words(22), "بیست و دو")
        self.assertEqual(t.words(1900), "هزار و نهصد")
        self.assertEqual(t.words(2275), "دو هزار و دویست و هفتاد و پنج")
        self.assertEqual(t.money(1600), "۱٬۶۰۰")


class VoiceGateTests(TestCase):
    def test_every_number_must_match(self) -> None:
        self.assertTrue(voice.numbers_match([22, 850, 15], "امروز ۲۲ آگهی ... یه اتاق، ماهی 850 دلار؛ 15 درصد"))
        self.assertTrue(voice.numbers_match([11, 1900, 24], "11 آگهی ... ماهی 1,900 دلار؛ 24 درصد ... یه 1 خوابه"))
        self.assertFalse(voice.numbers_match([22, 850, 15], "22 آگهی ... ماهی 805 دلار؛ 15 درصد"))
        self.assertFalse(voice.numbers_match([22, 850, 15], "22 آگهی ... ماهی 850 دلار"))

    def test_wrong_number_twice_gives_no_voice(self) -> None:
        from pathlib import Path

        with patch.object(voice, "speak", side_effect=lambda text, out, choice=None: (out, "edge")), patch.object(voice, "transcribe", return_value="21 آگهی"):
            clip, log = voice.checked_voice("x", [22], Path("v.mp3"))
        self.assertIsNone(clip)
        self.assertEqual(len(log["tries"]), 2)
        self.assertFalse(log["ok"])


class ScheduleTests(Base):
    def test_due_only_from_twenty_local_for_three_hours(self) -> None:
        self.assertEqual(schedule.daily_due(self.channel, NOW), "20261110")
        self.assertIsNone(schedule.daily_due(self.channel, NOW - timedelta(hours=1)))
        self.assertIsNone(schedule.daily_due(self.channel, NOW + timedelta(hours=3)))
        berlin = HomeChannel(country="DE", city="Berlin", username="b", chat_id=-2)
        self.assertEqual(schedule.daily_due(berlin, datetime(2026, 11, 11, 19, 15, tzinfo=dt_timezone.utc)), "20261111")

    def test_claim_is_once_per_period(self) -> None:
        first = schedule.claim("daily", self.channel, "20261110", NOW)
        self.assertIsNotNone(first)
        self.assertIsNone(schedule.claim("daily", self.channel, "20261110", NOW + timedelta(minutes=15)))
        first.sent_at = NOW
        first.save()
        self.assertIsNone(schedule.claim("daily", self.channel, "20261110", NOW + timedelta(hours=1)))

    def test_failed_claim_is_retried_at_most_three_times(self) -> None:
        row = schedule.claim("daily", self.channel, "20261110", NOW)
        for i in range(2):
            row.error = "boom"
            row.save()
            row = schedule.claim("daily", self.channel, "20261110", NOW + timedelta(minutes=15 * (i + 1)))
            self.assertIsNotNone(row)
        row.error = "boom"
        row.save()
        self.assertIsNone(schedule.claim("daily", self.channel, "20261110", NOW + timedelta(hours=1)))

    def test_first_sent_daily_switches_single_posts_off_and_quiet_day_is_skipped(self) -> None:
        settings_row = HomeSettings.load()
        settings_row.publish_enabled = True
        settings_row.save()
        self.market()
        for i in range(3):
            listing(self.source, f"اتاق مبله تازه {i}", price=700 + i)

        def fake_run(channel, row, now, **kwargs):
            row.sent_at, row.message_id = now, 99
            row.save()
            return row

        with patch.object(schedule, "run_daily", side_effect=fake_run), patch.object(schedule, "count_members", return_value=0):
            report = schedule.tick(NOW)
        self.assertEqual(report["sent"], [["daily", "chamedoon_toronto_khane", "https://t.me/chamedoon_toronto_khane/99"]])
        self.assertFalse(HomeSettings.load().publish_enabled)
        with patch.object(schedule, "count_members", return_value=0):
            self.assertEqual(schedule.tick(NOW + timedelta(minutes=15))["sent"], [])
        self.assertEqual(HomeContentPost.objects.filter(chat_id=-1001).count(), 1)

    def test_skip_day_writes_a_skipped_row(self) -> None:
        listing(self.source, "اتاق یک", price=900)
        row = schedule.claim("daily", self.channel, "20261110", NOW)
        row = schedule.run_daily(self.channel, row, NOW)
        self.assertTrue(row.payload["skipped"])
        self.assertIsNone(row.sent_at)
        self.assertIsNone(schedule.claim("daily", self.channel, "20261110", NOW + timedelta(hours=1)))


class LinkTests(Base):
    def test_parse(self) -> None:
        self.assertEqual(links.parse("dg_toronto_20261111_alert").action, "alert")
        self.assertEqual(links.parse("deal_berlin_7043").listing, 7043)
        self.assertEqual(links.parse("cc_de_202646_hamburg").country, "DE")
        self.assertIsNone(links.parse("wk_toronto_202646_post"))
        self.assertIsNone(links.parse("home_ca_toronto"))

    def test_deeplink_routes(self) -> None:
        resolve = lambda v: self.client.get("/api/v1/public/deeplink/", {"startapp": v}).json()
        city = resolve("dg_toronto_20261111")
        self.assertEqual((city["kind"], city["city_slug"]), ("city", "toronto"))
        self.assertIn("sort=new", city["path"])
        self.assertEqual(resolve("dg_toronto_20261111_alert")["path"], "/saved?tab=alerts")
        self.assertEqual(resolve("dg_toronto_20261111_post")["path"], "/post")
        self.assertEqual(resolve("deal_toronto_123")["kind"], "listing")

    def test_entry_once_per_open(self) -> None:
        self.assertTrue(links.record(5, "dg_toronto_20261111", first_open=True))
        self.assertFalse(links.record(5, "dg_toronto_20261111"))
        self.assertFalse(links.record(5, "home_ca_toronto"))
        entry = HomeEntry.objects.get()
        self.assertEqual((entry.source, entry.city, entry.action, entry.first_open), ("dg", "toronto", "all", True))


class PipelineTests(Base):
    """run_daily with Chrome, ffmpeg, TTS and Telegram stubbed: album first, still card when the video breaks."""

    def setUp(self) -> None:
        super().setUp()
        self.market()
        for i in range(3):
            listing(self.source, f"اتاق مبله تازه {i}", price=700 + i)

    def _stubs(self, tmp, *, video_ok=True):
        from pathlib import Path

        def fake_still(template, data, out, name):
            path = Path(out) / f"{name}.png"
            path.write_bytes(b"png")
            return path

        def fake_video(template, data, out, name, seconds):
            if not video_ok:
                raise RuntimeError("chrome crashed")
            paths = [Path(out) / f"{name}{suffix}" for suffix in ("_silent.mp4", ".png", "_cover.jpg", "_thumb.jpg")]
            for path in paths:
                path.write_bytes(b"x")
            return tuple(paths)

        return [
            patch.object(schedule, "work_dir", return_value=Path(tmp)),
            patch.object(schedule.render, "still", side_effect=fake_still),
            patch.object(schedule.render, "video", side_effect=fake_video),
            patch.object(schedule.voice, "checked_voice", return_value=(Path(tmp) / "v.mp3", {"ok": True})),
            patch.object(schedule.voice, "mix", return_value=12.5),
        ]

    def _run(self, *, video_ok=True):
        import contextlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            for stub in self._stubs(tmp, video_ok=video_ok):
                stack.enter_context(stub)
            album = stack.enter_context(patch.object(schedule.send, "album", return_value=[11, 12]))
            photos = stack.enter_context(patch.object(schedule.send, "photos", return_value=[21, 22]))
            row = schedule.claim("daily", self.channel, "20261110", NOW)
            row = schedule.run_daily(self.channel, row, NOW)
        return row, album, photos

    def test_album_with_voice(self) -> None:
        row, album, photos = self._run()
        self.assertEqual((row.message_id, row.message_ids), (11, [11, 12]))
        self.assertEqual(row.payload["format"], "album")
        album.assert_called_once()
        photos.assert_not_called()
        caption = album.call_args.args[-1]
        self.assertTrue(caption.endswith("@chamedoon_toronto_khane · @chamedoon_home_bot"))

    def test_broken_video_sends_the_still_card(self) -> None:
        row, album, photos = self._run(video_ok=False)
        album.assert_not_called()
        self.assertEqual((row.message_id, row.payload["format"]), (21, "photos"))
        self.assertIn("chrome crashed", row.payload["video_error"])


    def test_weekly_album_then_still_fallback(self) -> None:
        import contextlib
        import tempfile

        slot = datetime(2026, 11, 15, 18, 0, tzinfo=schedule.tz_of(self.channel))
        for i in range(3):
            listing(self.source, f"اتاق این هفته {i}", price=900 + i, hours_ago=0)
        for video_ok, fmt in ((True, "album"), (False, "photos")):
            with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
                for stub in self._stubs(tmp, video_ok=video_ok):
                    stack.enter_context(stub)
                stack.enter_context(patch.object(schedule.send, "album", return_value=[31, 32]))
                stack.enter_context(patch.object(schedule.send, "photos", return_value=[41, 42]))
                row = schedule.claim("weekly", self.channel, f"2026-W46-{fmt}", NOW)
                row = schedule.run_weekly(self.channel, row, slot)
            self.assertEqual(row.payload["format"], fmt)


class SendTests(TestCase):
    def test_album_is_one_multipart_request(self) -> None:
        import io
        import json as jsonlib
        import tempfile
        from pathlib import Path

        from home.content import send

        seen = {}

        def fake_urlopen(request, timeout=0):
            seen["body"] = request.data
            seen["url"] = request.full_url
            result = {"message_id": 5} if "sendPhoto" in request.full_url else [{"message_id": 5}, {"message_id": 6}]
            return io.BytesIO(jsonlib.dumps({"ok": True, "result": result}).encode())

        with tempfile.TemporaryDirectory() as tmp, patch.object(send, "bot_token", return_value="T"), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            files = []
            for name in ("v.mp4", "c.jpg", "t.jpg", "s.png"):
                (Path(tmp) / name).write_bytes(b"data")
                files.append(Path(tmp) / name)
            ids = send.album(-100, files[0], files[1], files[2], 12.4, files[3], "<b>cap</b>")
            single = send.photos(-100, [files[3]], "cap")
        self.assertEqual(ids, [5, 6])
        self.assertIn("sendPhoto", seen["url"])  # the last call: one still image
        self.assertEqual(single, [5])
        self.assertIn(b'"cover": "attach://c"', send._multipart({"media": jsonlib.dumps({"cover": "attach://c"})}, {})[0])

    def test_telegram_refusal_raises(self) -> None:
        import io
        import json as jsonlib

        from home.bot import BotError
        from home.content import send

        with patch.object(send, "bot_token", return_value="T"), patch(
            "urllib.request.urlopen", return_value=io.BytesIO(jsonlib.dumps({"ok": False, "description": "Bad Request", "error_code": 400}).encode())
        ):
            with self.assertRaises(BotError):
                send.upload("sendPhoto", {"chat_id": 1}, {})


class CommandTests(Base):
    def test_tick_command_prints_report(self) -> None:
        import io

        from django.core.management import call_command

        out = io.StringIO()
        with patch.object(schedule, "count_members", return_value=0):
            call_command("content_tick", "--now", "2026-11-10T12:00:00+00:00", stdout=out)
        self.assertIn('"sent": []', out.getvalue())

    def test_member_counts_once_a_day(self) -> None:
        from home.models import HomeChannelCount

        with patch.object(schedule.bot, "call", return_value=321):
            self.assertEqual(schedule.count_members(NOW), 1)
            self.assertEqual(schedule.count_members(NOW), 0)
        self.assertEqual(HomeChannelCount.objects.get(chat_id=-1001).members, 321)


class WeeklyTests(Base):
    SLOT = datetime(2026, 11, 15, 23, 0, tzinfo=dt_timezone.utc)  # Sunday 18:00 in Toronto

    def test_due_sunday_eighteen_local_with_iso_week(self) -> None:
        period, slot = schedule.weekly_due(self.channel, self.SLOT + timedelta(minutes=10))
        self.assertEqual(period, "2026-W46")
        self.assertIsNone(schedule.weekly_due(self.channel, self.SLOT - timedelta(minutes=10)))
        self.assertIsNone(schedule.weekly_due(self.channel, self.SLOT - timedelta(days=1)))

    def test_view_counts_week_from_monday_and_both_calendars(self) -> None:
        from home.content import weekly

        local = self.SLOT.astimezone(schedule.tz_of(self.channel))
        for i in range(9):
            listing(self.source, f"اتاق مبله شماره {i}", price=1000 + i, hours_ago=24 + i, area="North York")
        listing(self.source, "اتاق قدیمی هفته قبل", hours_ago=24 * 8)
        view = weekly.build(self.channel, local)
        self.assertEqual(view.payload["total"], 9)
        self.assertEqual(sum(view.payload["days"]), 9)
        self.assertEqual(view.payload["partial_days"], ["یکشنبه"])
        self.assertIn("📅 ۱۸ تا ۲۴ آبان ۱۴۰۵ · 9–15 Nov 2026", view.caption)
        self.assertIn("startapp=wk_toronto_202646", view.caption)
        self.assertEqual(view.caption.splitlines()[-1], "@chamedoon_toronto_khane · @chamedoon_home_bot")
        self.assertEqual(view.expected[:3], [18, 24, 9])
        self.assertEqual(view.payload["meds"][0][0], "room")

    def test_hand_sent_week_is_not_posted_again(self) -> None:
        HomeContentPost.objects.create(kind="weekly", chat_id=-1001, period_key="2026-W46", sent_at=NOW, message_id=1)
        with patch.object(schedule, "run_weekly") as run, patch.object(schedule, "count_members", return_value=0):
            schedule.tick(self.SLOT + timedelta(minutes=5))
        run.assert_not_called()
