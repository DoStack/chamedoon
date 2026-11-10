"""Pick a rollout batch and hold it for a yes: by default day one, 2 seekers (S1 or S1b), 2 P1, 1 P1q.

Nothing is sent. The rows wait as Held (`awaiting_yes`) under the batch name; Admin → Outreach messages → filter by
batch → Approve queues them. Accounts are planned round robin; a limited account hands over at send time.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from outreach.housing import new_message, people, person_skip_reason, plans_for
from outreach.models import OutreachAccount, OutreachKind, OutreachStatus
from outreach.services import AWAITING_YES, BuildResult


class Command(BaseCommand):
    help = __doc__.splitlines()[0]

    def add_arguments(self, parser) -> None:
        parser.add_argument("--batch", default="day1")
        parser.add_argument("--seekers", type=int, default=2)
        parser.add_argument("--p1", type=int, default=2)
        parser.add_argument("--p1q", type=int, default=1)
        parser.add_argument("--accounts", default="niloofar,sara", help="Planned accounts, round robin.")
        parser.add_argument("--save", action="store_true", help="Write the Held rows. Without it, only print.")

    def handle(self, *args, **options) -> None:
        now = timezone.now()
        wanted = {"seeker": options["seekers"], OutreachKind.P1: options["p1"], OutreachKind.P1Q: options["p1q"]}
        accounts = [OutreachAccount.objects.get_or_create(name=name.strip())[0] for name in options["accounts"].split(",") if name.strip()]
        chosen = []
        result = BuildResult()
        for person in people(now, result):
            if all(count <= 0 for count in wanted.values()):
                break
            if person_skip_reason(person):
                continue
            plans = plans_for(person, now)
            seeker = plans.get(OutreachKind.S1) or plans.get(OutreachKind.S1B)
            if seeker is not None and wanted["seeker"] > 0:
                chosen.append((person, seeker, seeker.kind))
                wanted["seeker"] -= 1
                continue
            owner = plans.get(OutreachKind.P1)
            if owner is not None and not seeker:
                for kind in (OutreachKind.P1, OutreachKind.P1Q):
                    if wanted[kind] > 0:
                        chosen.append((person, owner, kind))
                        wanted[kind] -= 1
                        break
        rows = []
        for index, (person, plan, kind) in enumerate(chosen):
            message = new_message(person, plan, kind=kind, account=accounts[index % len(accounts)] if accounts else None, batch=options["batch"], now=now)
            message.status = OutreachStatus.HELD
            message.error_code = AWAITING_YES
            rows.append(message)
        if options["save"]:
            with transaction.atomic():
                for message in rows:
                    message.save()
        for message in rows:
            self.stdout.write(f"\n#{message.pk or '-'} {message.kind} {message.city} @{message.recipient_username} via {message.account.name if message.account else '-'} listing {message.listing_id}\n{message.text}")
        missing = {key: count for key, count in wanted.items() if count > 0}
        self.stdout.write(f"\n{len(rows)} picked{' and held' if options['save'] else ' (not saved)'}. Short: {missing or 'none'}.")
