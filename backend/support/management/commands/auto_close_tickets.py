from django.core.management.base import BaseCommand

from support.services import AUTO_CLOSE_HOURS, auto_close_stale_tickets


class Command(BaseCommand):
    help = "Close support tickets waiting for a user reply for 72 hours."

    def handle(self, *args, **options) -> None:
        closed = auto_close_stale_tickets(hours=AUTO_CLOSE_HOURS)
        self.stdout.write(self.style.SUCCESS(f"Closed {closed} ticket(s)."))
