"""Print one line on housing intake health and exit 1 when there is a problem.

Run on the server by /opt/chamedoon-db/bin/check.sh (every 15 minutes):
    docker exec chamedoon-worker python manage.py home_health
"""

import sys

from django.core.management.base import BaseCommand

from home.health import health_line, intake_health


class Command(BaseCommand):
    help = "Housing intake health: FloodWait pause, silent ingest, stuck queue, review errors, failing sources."

    def handle(self, *args, **options):
        report = intake_health()
        self.stdout.write(health_line(report))
        if not report["ok"]:
            sys.exit(1)
