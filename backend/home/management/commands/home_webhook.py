from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from home import bot, conf
from home.botchat import COMMANDS
from home.webhook import PATH


class Command(BaseCommand):
    help = (
        "Show (default) or set the housing bot's webhook. Nothing is sent to Telegram unless --apply is given. "
        "The secret comes from Home settings or HOME_WEBHOOK_SECRET and is never printed."
    )

    def add_arguments(self, parser):
        parser.add_argument("--url", default="", help="Public base address of the site, e.g. https://chamedoon.example.")
        parser.add_argument("--apply", action="store_true", help="Really call setWebhook and setMyCommands.")
        parser.add_argument("--delete", action="store_true", help="With --apply: remove the webhook instead.")
        parser.add_argument("--info", action="store_true", help="Call getWebhookInfo (read only).")

    def handle(self, *args, **options):
        if options["info"]:
            self._print(bot.call("getWebhookInfo"))
            return
        if options["delete"]:
            if not options["apply"]:
                self.stdout.write("Dry run: would call deleteWebhook. Add --apply to do it.")
                return
            self._print(bot.call("deleteWebhook", {"drop_pending_updates": False}))
            return
        base = (options["url"] or "").strip().rstrip("/")
        if not base.startswith("https://"):
            raise CommandError("Give the public https address of the site with --url.")
        secret = conf.webhook_secret()
        payload = {
            "url": f"{base}{PATH}",
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        }
        shown = {**payload, "secret_token": "(set)" if secret else "(MISSING)"}
        self.stdout.write("setWebhook " + json.dumps(shown, ensure_ascii=False))
        self.stdout.write("setMyCommands " + json.dumps({"commands": COMMANDS}, ensure_ascii=False))
        if not secret:
            raise CommandError("Set the webhook secret in Home settings or HOME_WEBHOOK_SECRET first.")
        if not conf.bot_token():
            raise CommandError("Set the housing bot token in Home settings first.")
        if not options["apply"]:
            self.stdout.write("Dry run: nothing was sent. Add --apply to set the webhook.")
            return
        self._print(bot.call("setWebhook", payload))
        self._print(bot.call("setMyCommands", {"commands": COMMANDS}))

    def _print(self, value) -> None:
        self.stdout.write(json.dumps(value, ensure_ascii=False, indent=2, default=str))
