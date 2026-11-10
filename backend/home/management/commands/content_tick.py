"""Scheduled channel content (spec CONTENT-SPEC.md §3). Cron on bigde-vps, every 15 minutes:

    python manage.py content_tick

Preview one city without touching the database or the channel (numbers, caption, script, rendered files):

    python manage.py content_tick --preview toronto [--now 2026-11-11T20:05-05:00] [--out /tmp/x] [--send-to <chat_id>]
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from home.content import daily as daily_view
from home.content import data, render, schedule, send, voice
from home.content.text import city_slug


class Command(BaseCommand):
    help = "Send the channel digests that are due (daily 20:00 local). Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("--preview", metavar="CITY", help="Build one city's daily without sending to its channel or writing rows.")
        parser.add_argument("--now", help="ISO time to act as now (default: now).")
        parser.add_argument("--out", help="Preview folder (default: CONTENT_DIR/preview).")
        parser.add_argument("--send-to", type=int, help="Preview only: send the album to this chat id (e.g. the owner's bot chat).")
        parser.add_argument("--no-video", action="store_true", help="Preview only: images and text, no video or voice.")

    def handle(self, *args, **options):
        now = datetime.fromisoformat(options["now"]) if options["now"] else timezone.now()
        if options["preview"]:
            return self.preview(options["preview"], now, options)
        self.stdout.write(json.dumps(schedule.tick(now), ensure_ascii=False, default=str))

    def preview(self, city: str, now: datetime, options) -> None:
        # Read only, at the transaction level (pgbouncer transaction pooling: never a session SET).
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
            channels = schedule.city_channels()
            channel = next((c for c in channels if city_slug(c.city) == city.lower()), None)
            if channel is None:
                raise CommandError(f"no city channel {city!r}")
            day = now.astimezone(schedule.tz_of(channel)).date()
            numbers = data.daily(channel, now, channels)
            view = daily_view.build(numbers, day, channel.username)
        out = Path(options["out"] or (schedule.work_dir() / "preview" / city.lower()))
        out.mkdir(parents=True, exist_ok=True)
        (out / "view.json").write_text(
            json.dumps({"payload": view.payload, "card": view.card, "stats": view.stats, "caption": view.caption, "script": view.script, "expected": view.expected},
                       ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
        self.stdout.write(view.caption + "\n\n" + view.script + f"\n\nskip={numbers.skip} expected={view.expected}")
        stats_png = render.still("stats.html", view.stats, out, "stats")
        if options["no_video"]:
            render.still("daily.html", view.card, out, "card")
            return
        silent, _png, cover, thumb = render.video("daily.html", view.card, out, "card", daily_view.ANIM_SECONDS)
        clip, check = voice.checked_voice(view.script, view.expected, out / "voice.mp3")
        self.stdout.write(json.dumps(check, ensure_ascii=False))
        final = out / "daily.mp4"
        seconds = voice.mix(silent, clip, schedule.music(), final, daily_view.ANIM_SECONDS)
        self.stdout.write(f"video {final} {seconds}s")
        if options["send_to"]:
            ids = send.album(options["send_to"], final, cover, thumb, seconds, stats_png, view.caption)
            self.stdout.write(f"sent {ids}")
