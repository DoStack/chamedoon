from django.core.management.base import BaseCommand

from market.ingest import clamp_lookback_days, ingest_all_market_channels
from market.migrate import migrate_market_posts


class Command(BaseCommand):
    help = "Ingest public market channels, then turn cleaned posts into live Explore requests."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-ingest",
            action="store_true",
            help="Only migrate already stored posts.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Only ingest posts from the last N days (1-30). Default: 30.",
        )

    def handle(self, *args, **options):
        days = None if options.get("days") is None else clamp_lookback_days(options.get("days"))
        if not options.get("skip_ingest"):
            ingest = ingest_all_market_channels(days=days)
            for item in ingest.get("channels") or []:
                channel = item.get("channel") or "?"
                if item.get("skipped") or item.get("deferred"):
                    self.stdout.write(self.style.WARNING(f"ingest {channel}: {item.get('error') or 'deferred'}"))
                elif item.get("ok"):
                    self.stdout.write(
                        f"ingest {channel}: created {item.get('created', 0)}, updated {item.get('updated', 0)}"
                    )
        result = migrate_market_posts()
        self.stdout.write(
            self.style.SUCCESS(
                "created {created}, updated {updated}, skipped {skipped}, expired {expired}".format(
                    **result
                )
            )
        )
