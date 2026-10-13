from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from home.job import run_home_job


class Command(BaseCommand):
    help = "Read the housing sources and review waiting posts with the AI."

    def add_arguments(self, parser):
        parser.add_argument("--step", choices=["all", "ingest", "review"], default="all")
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--limit", type=int, default=200, help="Posts to review in this run.")
        parser.add_argument("--seconds", type=int, default=900, help="Time budget for this run.")
        parser.add_argument("--source", action="append", default=[], help="Limit to this @username (repeatable).")

    def handle(self, *args, **options):
        import time

        result = run_home_job(
            step=options["step"],
            days=options["days"],
            review_limit=options["limit"],
            stop_at=time.monotonic() + options["seconds"],
            usernames=options["source"] or None,
        )
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
