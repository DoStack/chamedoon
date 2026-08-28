from django.core.management.base import BaseCommand

from market.ingest import ingest_all_market_channels, ingest_market_channel, market_channel_username


class Command(BaseCommand):
    help = "Fetch public Telegram channel posts into MarketPost (upsert by message id)."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="", help="Channel username without @. Default: all configured channels.")
        parser.add_argument("--head-pages", type=int, default=None)
        parser.add_argument("--backfill-pages", type=int, default=None)

    def handle(self, *args, **options):
        username = (options.get("username") or "").lstrip("@")
        kwargs = {
            "head_pages": options.get("head_pages"),
            "backfill_pages": options.get("backfill_pages"),
        }
        if username:
            result = ingest_market_channel(username=username, **kwargs)
            self._write_one(result)
            if not result.get("ok"):
                raise SystemExit(1)
            return
        result = ingest_all_market_channels(**kwargs)
        for item in result.get("channels") or []:
            self._write_one(item)
        if not result.get("ok"):
            raise SystemExit(1)

    def _write_one(self, result: dict) -> None:
        channel = result.get("channel") or market_channel_username()
        if result.get("deferred"):
            self.stdout.write(self.style.WARNING(f"{channel}: deferred (time budget)"))
            return
        if result.get("skipped"):
            self.stdout.write(self.style.WARNING(f"{channel}: skipped ({result.get('error') or 'unavailable'})"))
            return
        if result.get("ok"):
            self.stdout.write(
                self.style.SUCCESS(
                    f"{channel}: created {result['created']}, "
                    f"updated {result['updated']}, pages {result['pages']}, "
                    f"total {result.get('post_count', 0)}"
                )
            )
            return
        self.stderr.write(self.style.ERROR(f"{channel}: {result.get('error') or 'ingest failed'}"))
