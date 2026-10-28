from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from api import webhooks


class Command(BaseCommand):
    help = (
        "Show (default) or set the courier bot's webhook. Nothing is sent to Telegram unless --apply is given. "
        "The secret comes from Courier settings or TELEGRAM_WEBHOOK_SECRET and is never printed. "
        "Prefer Admin → Telegram webhooks; this is the fallback."
    )

    def add_arguments(self, parser):
        parser.add_argument("--url", default="", help="Public base address of the site, e.g. https://chamedoon.example.")
        parser.add_argument("--apply", action="store_true", help="Really call setWebhook and setMyCommands.")
        parser.add_argument("--delete", action="store_true", help="With --apply: remove the webhook instead.")
        parser.add_argument("--info", action="store_true", help="Call getWebhookInfo (read only).")

    def handle(self, *args, **options):
        bot = webhooks.get_bot(webhooks.COURIER)
        try:
            if options["info"]:
                self._print(webhooks.webhook_info(webhooks.COURIER))
                return
            if options["delete"]:
                if not options["apply"]:
                    self.stdout.write("Dry run: would call deleteWebhook. Add --apply to do it.")
                    return
                self._outcome(webhooks.delete_webhook(webhooks.COURIER))
                return
            url = (options["url"] or "").strip()
            if not url.startswith("https://"):
                raise CommandError("Give the public https address of the site with --url.")
            base = webhooks.site_base(url=url)
        except webhooks.WebhookError as exc:
            raise CommandError(str(exc)) from None
        secret = bot.secret()
        payload = webhooks.set_payload(bot, base, secret)
        self.stdout.write("setWebhook " + json.dumps(webhooks.masked(payload), ensure_ascii=False))
        self.stdout.write("setMyCommands " + json.dumps({"commands": bot.commands()}, ensure_ascii=False))
        if not secret:
            raise CommandError("Set the webhook secret in Courier settings or TELEGRAM_WEBHOOK_SECRET first.")
        if not bot.token():
            raise CommandError("Set TELEGRAM_BOT_TOKEN first.")
        if not options["apply"]:
            self.stdout.write("Dry run: nothing was sent. Add --apply to set the webhook.")
            return
        self._outcome(webhooks.set_webhook(webhooks.COURIER, base))

    def _outcome(self, outcome) -> None:
        self._print({"ok": outcome.ok, "steps": [step.as_dict() for step in outcome.steps], "error": outcome.error})
        if not outcome.ok:
            raise CommandError(outcome.summary)

    def _print(self, value) -> None:
        self.stdout.write(json.dumps(value, ensure_ascii=False, indent=2, default=str))
