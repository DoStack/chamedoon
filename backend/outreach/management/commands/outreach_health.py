"""The 5-minute health check and the 20:30 Toronto daily report (outreach operating plan v1).

Run from the VPS crontab by /opt/chamedoon-outreach/health.sh, which passes the worker's recent backend errors and
restarts the worker when this prints RESTART.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from outreach import health


class Command(BaseCommand):
    help = "Check outreach health, alert Mohammad with DB facts, send the daily report when due."

    def add_arguments(self, parser):
        parser.add_argument("--worker-errors", type=int, default=0)
        parser.add_argument("--dry-run", action="store_true", help="Print only; tell nobody.")

    def handle(self, *args, **options):
        result = health.run(worker_errors=options["worker_errors"], alert=not options["dry_run"])
        for incident in result.incidents:
            self.stdout.write(f"INCIDENT {incident.key}: {incident.facts}")
        if result.report:
            self.stdout.write(result.report)
        if result.restart:
            self.stdout.write("RESTART")
