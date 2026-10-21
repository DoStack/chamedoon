from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from home.job import run_home_job


class Command(BaseCommand):
    help = "Read the housing sources and review waiting posts with the AI."

    def add_arguments(self, parser):
        parser.add_argument("--step", choices=["all", "ingest", "review", "media", "vision"], default="all")
        parser.add_argument("--days", type=int, default=None, help="Lookback of a first read. Default: Home settings.")
        parser.add_argument("--limit", type=int, default=200, help="Posts to review in this run.")
        parser.add_argument("--media-limit", type=int, default=100, help="Photos to download in this run.")
        parser.add_argument("--seconds", type=int, default=900, help="Time budget for this run.")
        parser.add_argument("--source", action="append", default=[], help="Limit to this @username (repeatable).")
        parser.add_argument(
            "--every",
            type=int,
            default=0,
            help="Run again every N minutes until stopped, for live reading on a server. 0 runs once.",
        )

    def handle(self, *args, **options):
        import time

        every = max(0, options["every"]) * 60
        while True:
            started = time.monotonic()
            result = run_home_job(
                step=options["step"],
                days=options["days"],
                review_limit=options["limit"],
                media_limit=options["media_limit"],
                stop_at=started + options["seconds"],
                usernames=options["source"] or None,
            )
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            if not every:
                return
            self.stdout.flush()
            time.sleep(max(0, every - (time.monotonic() - started)))
