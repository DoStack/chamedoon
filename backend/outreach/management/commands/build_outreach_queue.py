from django.core.management.base import BaseCommand

from outreach.services import build_outreach_queue


class Command(BaseCommand):
    help = "Queue Persian Telegram DMs for imported demanders with fresh strong matches."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Print the messages without saving them.")

    def handle(self, *args, dry_run: bool = False, **options) -> None:
        result = build_outreach_queue(dry_run=dry_run)
        for message in result.created:
            self.stdout.write(f"--- @{message.recipient_username} (request {message.demand_request_id})")
            self.stdout.write(message.text)
        verb = "Would queue" if dry_run else "Queued"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(result.created)} message(s). Skipped: {result.skipped}"))
