from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from home import assistant, bot
from home.models import HomeChatState

MENU_TEXT = "باز کردن چمدون"


class Command(BaseCommand):
    help = (
        "The housing bot as a door to the Mini App: the menu button opens the Mini App, the command list is the "
        "conversation's, and the old 6-button reply keyboard is taken away from people who still have it (a silent "
        "message that removes it, deleted at once). Shows what it would do unless --apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument("--url", default="", help="The Mini App's https address for the menu button, e.g. https://chamedoon.example/app/home/.")
        parser.add_argument("--keyboards", action="store_true", help="Also take the old reply keyboard away from everyone who talked to the bot.")
        parser.add_argument("--only", type=int, default=0, help="With --keyboards: one Telegram id only (try it on yourself first).")
        parser.add_argument("--apply", action="store_true", help="Really call Telegram.")

    def handle(self, *args, **options):
        url = (options["url"] or "").strip()
        if not url.startswith("https://"):
            raise CommandError("Give the Mini App's https address with --url.")
        menu = {"menu_button": {"type": "web_app", "text": MENU_TEXT, "web_app": {"url": url}}}
        commands = {"commands": assistant.COMMANDS}
        people = list(HomeChatState.objects.values_list("telegram_user_id", flat=True).order_by("telegram_user_id")) if options["keyboards"] else []
        if options["only"]:
            people = [person for person in people if person == options["only"]] or [options["only"]]
        if not options["apply"]:
            self.stdout.write("setChatMenuButton " + json.dumps(menu, ensure_ascii=False))
            self.stdout.write("setMyCommands " + json.dumps(commands, ensure_ascii=False))
            self.stdout.write(f"keyboard removal for {len(people)} people. Add --apply to do it.")
            return
        bot.call("setChatMenuButton", menu)
        bot.call("setMyCommands", commands)
        self.stdout.write("getChatMenuButton " + json.dumps(bot.call("getChatMenuButton"), ensure_ascii=False))
        removed = sum(1 for person in people if assistant.drop_keyboard(person))
        self.stdout.write(f"keyboard removed for {removed} of {len(people)} people")
