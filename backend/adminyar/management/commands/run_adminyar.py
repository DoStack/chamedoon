"""Long-polls @Chamedoon_adminyar_bot. Separate process from the housing bot: its own token and loop."""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from adminyar import conf
from adminyar.bot import Bot
from home.bot import BotError

logger = logging.getLogger("adminyar")


class Command(BaseCommand):
    help = "Run @Chamedoon_adminyar_bot (long polling)."

    def add_arguments(self, parser):
        parser.add_argument("--env-file", help="KEY=VALUE file with the ADMINYAR_* settings. Values are never printed.")
        parser.add_argument("--once", action="store_true", help="One getUpdates round, then exit.")

    def handle(self, *args, **options):
        if options.get("env_file"):
            conf.load_env_file(options["env_file"])
        token = conf.token()
        if not token:
            raise CommandError("ADMINYAR_BOT_TOKEN is not set.")
        bot = Bot(token)
        bot.api("deleteWebhook", {"drop_pending_updates": False})
        me = bot.api("getMe", {})
        self.stdout.write(f"adminyar: polling as @{me.get('username')}, {len(conf.allowed_ids())} allowed ids", ending="\n")
        self.stdout.flush()
        offset = 0
        next_remind = 0.0
        while True:
            if time.monotonic() >= next_remind:
                next_remind = time.monotonic() + 300
                close_old_connections()
                try:
                    bot.remind()
                except Exception:  # noqa: BLE001
                    logger.exception("adminyar reminders failed")
            try:
                updates = bot.api("getUpdates", {"offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]})
            except BotError as exc:
                logger.warning("getUpdates failed: %s", exc.description)
                time.sleep(max(3, exc.retry_after))
                continue
            for update in updates or []:
                offset = max(offset, int(update["update_id"]) + 1)
                close_old_connections()
                try:
                    bot.handle(update)
                except Exception:  # noqa: BLE001 - one bad update must not stop the bot
                    logger.exception("adminyar update %s failed", update.get("update_id"))
            if options.get("once"):
                return
