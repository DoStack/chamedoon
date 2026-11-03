from django.core.management.base import BaseCommand

from home.snapshots import purge_old_versions


class Command(BaseCommand):
    help = "Delete city snapshot files the manifest no longer names. Run the writer first when taking a contact down."

    def handle(self, *args, **options):
        result = purge_old_versions()
        self.stdout.write(str(result))
