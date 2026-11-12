"""Read the view counts of the ready posts (ReadyPost and the newest home_contentpost per channel) with the market reader account.

Read only: one get_messages per channel, no join, no send. Run from cron on the worker, e.g. every 3 hours.
"""

from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils import timezone

from adminyar import posts
from adminyar.models import ReadyPost
from home.models import HomeContentPost
from market.telegram_history import HistoryReader


class Command(BaseCommand):
    help = "Refresh view counts of the posts the adminyar bot offers."

    def handle(self, *args, **options):
        wanted: dict[str, list[posts.Item]] = defaultdict(list)
        for code, _, _ in posts.CATEGORIES:
            for item in posts.items(code):
                wanted[item.channel.username].append(item)
        reader = HistoryReader()
        reader.open()
        updated = 0
        try:
            for username, found in wanted.items():
                ids = sorted({item.message_ids[0] for item in found})
                try:
                    messages = reader.messages(username, ids)
                except Exception as exc:  # noqa: BLE001 - one channel failing must not stop the rest
                    self.stderr.write(f"{username}: {type(exc).__name__}")
                    if reader.flood_wait:
                        break
                    continue
                views = {m.id: m.views for m in messages if m is not None and getattr(m, "views", None) is not None}
                now = timezone.now()
                for item in found:
                    value = views.get(item.message_ids[0])
                    if value is None:
                        continue
                    model = ReadyPost if item.ref.startswith("r") else HomeContentPost
                    updated += model.objects.filter(pk=int(item.ref[1:])).update(views=value, views_at=now)
        finally:
            reader.close()
        self.stdout.write(f"adminyar_views: {updated} posts updated")
