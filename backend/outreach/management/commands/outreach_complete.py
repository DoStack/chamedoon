"""Ad completion by hand, one command per step. Same rules as Admin → Outreach → Chats → «Ad completion».

    manage.py outreach_complete show 12                      their live ads, what is missing, held photos, next line
    manage.py outreach_complete set 12 --listing 345 price 1200
    manage.py outreach_complete photos 12 --listing 345      every held photo of chat 12 onto ad 345
    manage.py outreach_complete edit 12 --listing 345        the edit link line
    manage.py outreach_complete done 12 --listing 345        thanks + view link, recorded as done

The chat can be given as its id (12, #c12) or @username. Only ads its Telegram id owns are touched. Sends nothing.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from outreach import completion
from outreach.models import OutreachConversation
from outreach.photos import held_photos


class Command(BaseCommand):
    help = "Ad completion for one outreach chat: show, set a field, add held photos, edit link, done. Sends nothing."

    def add_arguments(self, parser) -> None:
        parser.add_argument("op", choices=("show", "set", "photos", "edit", "done"))
        parser.add_argument("chat", help="Chat id (12 or #c12) or @username")
        parser.add_argument("field", nargs="?", choices=completion.FIELDS)
        parser.add_argument("value", nargs="?")
        parser.add_argument("--listing", type=int)

    def _chat(self, value: str) -> OutreachConversation:
        value = value.strip()
        found = None
        if value.startswith("@"):
            found = OutreachConversation.objects.filter(username__iexact=value[1:]).order_by("-pk").first()
        elif value.lower().lstrip("#c").isdigit():
            found = OutreachConversation.objects.filter(pk=int(value.lower().lstrip("#c"))).first()
        if found is None:
            raise CommandError(f"No outreach chat {value}")
        return found

    def handle(self, *args, **options) -> None:
        conversation = self._chat(options["chat"])
        op = options["op"]
        if op == "show":
            self._show(conversation)
            return
        if not options["listing"]:
            raise CommandError("--listing is required")
        if op == "set":
            if not options["field"] or not options["value"]:
                raise CommandError("set needs a field and a value")
            result = completion.apply_values(conversation, options["listing"], {options["field"]: options["value"]}, by="command")
        elif op == "photos":
            result = completion.attach_photos(conversation, options["listing"], by="command")
        elif op == "edit":
            listing = completion.own_ad(conversation.telegram_user_id, options["listing"])
            if listing is None:
                raise CommandError("not_their_ad")
            first = conversation.first_message
            result = completion.Applied(True, "", listing, [], completion.edit_text(listing, first.token if first else ""))
        else:
            result = completion.finish(conversation, options["listing"], by="command")
        conversation.save(update_fields=["awaiting", "hooks_done", "updated_at"])
        if not result.ok:
            raise CommandError(result.error)
        self.stdout.write(f"saved: {', '.join(result.filled or []) or '-'}")
        if result.next:
            self.stdout.write("send next:\n" + result.next)

    def _show(self, conversation: OutreachConversation) -> None:
        ads = completion.live_ads(conversation.telegram_user_id)
        first = conversation.first_message
        token = first.token if first else ""
        self.stdout.write(f"#c{conversation.pk} @{conversation.username} on {conversation.account.name}, state {conversation.state}")
        if not ads:
            self.stdout.write("no live ad owned by this Telegram id")
        for listing in ads:
            self.stdout.write(f"  ad {listing.pk}: {completion.label(listing, ads)} | missing: {', '.join(completion.gaps(listing)) or 'nothing'} | {completion.view_link(listing, token)}")
        photos = held_photos(conversation)
        self.stdout.write(f"photos not on an ad: {len(photos)} (turns {', '.join(str(turn.pk) for turn in photos) or '-'})")
        if ads:
            line = completion.which_text(ads) if len(ads) > 1 else completion.next_line(conversation, ads[0], mark=False)
            if line:
                self.stdout.write("suggested line:\n" + line)
