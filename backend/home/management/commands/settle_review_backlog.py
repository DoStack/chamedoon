from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from home.review_backlog import DEFAULT_BATCH, requeue_rule_rejects, requeue_wrong_rejects, settle_backlog


class Command(BaseCommand):
    help = "Settle every waiting housing post that needs no model: duplicates and posts with no housing word. Calls no model. Safe to run again. Writes only with review_gate_on."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print the counts and write nothing.")
        parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH, help="Posts decided per batch, 500 at most.")
        parser.add_argument(
            "--requeue-noise",
            action="store_true",
            help="Instead: after a word list change, send back to the queue the posts the gate rejected that now pass it.",
        )
        parser.add_argument(
            "--requeue-wrong",
            action="store_true",
            help="Instead: send back recent short posts with a housing word and model rejections (ad, other, incomplete, "
            "not_housing) of posts with a housing word, one per wording. Needs --backup PATH unless --dry-run.",
        )
        parser.add_argument("--days", type=int, default=3, help="With --requeue-wrong: how many days back, by post time.")
        parser.add_argument("--backup", default="", help="With --requeue-wrong: JSON file the changed rows are written to first.")

    def handle(self, *args, **options):
        if options["requeue_wrong"]:
            report = requeue_wrong_rejects(days=max(1, min(30, options["days"])), dry_run=options["dry_run"], backup_path=options["backup"])
        elif options["requeue_noise"]:
            report = requeue_rule_rejects(dry_run=options["dry_run"], batch_size=options["batch_size"])
        else:
            report = settle_backlog(dry_run=options["dry_run"], batch_size=options["batch_size"], say=self._say)
        if "refused" in report:
            raise CommandError(report["refused"])
        self.stdout.write(json.dumps(report, indent=2))

    def _say(self, line: str) -> None:
        self.stderr.write(line)
