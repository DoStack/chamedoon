from django.core.management.base import BaseCommand, CommandError

from market.extract import extract_named_post, parse_telegram_post_ref


class Command(BaseCommand):
    help = "Fetch one Telegram channel post by URL and open it for market extract/review."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            required=True,
            help="Telegram post URL, e.g. https://t.me/koolbar_international/7265",
        )
        parser.add_argument(
            "--no-force-review",
            action="store_true",
            help="Reuse an existing LLM review when present.",
        )

    def handle(self, *args, **options):
        url = (options.get("url") or "").strip()
        if parse_telegram_post_ref(url) is None:
            raise CommandError(
                "Need a public Telegram post URL such as https://t.me/koolbar_international/7265."
            )
        result = extract_named_post(url=url, force_review=not options.get("no_force_review"))
        for line in result.get("logs") or []:
            message = line.get("message") or ""
            level = line.get("level") or "info"
            writer = self.stderr.write if level in {"warn", "error"} else self.stdout.write
            style = {
                "error": self.style.ERROR,
                "warn": self.style.WARNING,
            }.get(level, self.style.NOTICE)
            writer(style(message) if message else message)
        if not result.get("ok"):
            raise SystemExit(1)
        outcome = result.get("result") or "ok"
        post_id = result.get("post_id")
        self.stdout.write(self.style.SUCCESS(f"{outcome} post_id={post_id or '—'}"))
        if outcome != "draft":
            raise SystemExit(0 if result.get("ok") else 1)
