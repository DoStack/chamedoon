from django.core.management.base import BaseCommand

from item_requests.models import ItemRequest, RequestStatus
from notifications.channel import sync_request_channel


class Command(BaseCommand):
    help = "Publish or update ACTIVE requests on the Telegram channel."

    def handle(self, *args, **options):
        rows = ItemRequest.objects.filter(status=RequestStatus.ACTIVE).order_by("id")
        published = 0
        failed = 0
        for item in rows:
            if sync_request_channel(item.pk):
                published += 1
            else:
                failed += 1
        self.stdout.write(f"Published or updated {published}. Failed or skipped {failed}.")
