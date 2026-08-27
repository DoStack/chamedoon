from django.core.management.base import BaseCommand

from item_requests.seed import seed_catalog


class Command(BaseCommand):
    help = "Seed item categories and origin/destination cities."

    def handle(self, *args, **options) -> None:
        seed_catalog()
        self.stdout.write(self.style.SUCCESS("Catalog seeded."))
