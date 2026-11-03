from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from home.review_backlog import DEFAULT_BATCH, requeue_rule_rejects, settle_backlog


class Command(BaseCommand):
    help = "Settle every waiting housing post that needs no model: duplicates and posts with no housing word. Calls no model. Safe to run again."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print the counts and write nothing.")
        parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH, help="Posts read and settled per batch.")
        parser.add_argument(
            "--requeue-noise",
            action="store_true",
            help="Instead: after a word list change, send back to the queue the posts the gate rejected that now pass it.",
        )

    def handle(self, *args, **options):
        if options["requeue_noise"]:
            report = requeue_rule_rejects(dry_run=options["dry_run"], batch_size=options["batch_size"])
        else:
            report = settle_backlog(dry_run=options["dry_run"], batch_size=options["batch_size"], say=self._say)
        self.stdout.write(json.dumps(report, indent=2))

    def _say(self, line: str) -> None:
        self.stderr.write(line)
