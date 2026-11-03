from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from home import bot, channel_format, conf
from home.guard import is_courier_channel
from home.models import HomeChannel


class Command(BaseCommand):
    help = (
        "Show (default) or send a channel post that is not a listing: the pinned post or the weekly report. "
        "Nothing is sent to Telegram unless --apply is given, and each send needs Mohammad's yes."
    )

    def add_arguments(self, parser):
        parser.add_argument("kind", choices=["pinned", "report"])
        parser.add_argument("--country", required=True, help="CA or DE.")
        parser.add_argument("--city", default="", help="The metro, e.g. Toronto. Blank: the country channel.")
        parser.add_argument("--apply", action="store_true", help="Really send it to the channel.")

    def handle(self, *args, **options):
        channel = HomeChannel.objects.filter(country=options["country"].upper(), city__iexact=options["city"]).first()
        if channel is None:
            raise CommandError("No such housing channel.")
        if options["kind"] == "pinned":
            text, buttons = channel_format.pinned_text(channel), channel_format.pinned_buttons(channel)
        else:
            text, buttons = channel_format.weekly_text(channel), channel_format.weekly_buttons(channel)
        self.stdout.write(text)
        if not options["apply"]:
            self.stdout.write(f"\nDry run: would post to @{channel.username}. Add --apply to do it.")
            return
        if not conf.channel_v2_on():
            raise CommandError("channel_v2 is off in Home settings.")
        if is_courier_channel(channel.username, channel.chat_id):
            raise CommandError("This is the courier channel. Housing never posts there.")
        ids = bot.send_message(channel.target(), text, buttons=buttons)
        self.stdout.write(f"\nPosted to @{channel.username}: message {ids[0]}.")
