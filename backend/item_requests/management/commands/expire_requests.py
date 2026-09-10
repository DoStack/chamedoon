from django.core.management.base import BaseCommand

from item_requests.services import expire_due_requests


class Command(BaseCommand):
    help = "Mark active supply and demand requests as expired after their date has passed."

    def handle(self, *args, **options) -> None:
        expired = expire_due_requests()
        self.stdout.write(self.style.SUCCESS(f"Expired {expired} request(s)."))
