"""Alert when a sending account's worker has gone quiet.

Run every 5 minutes from the VPS crontab. Prints the stale account names (one per line) so the host script can restart
the worker; tells Mohammad once per stale spell (the alert is not repeated until the heartbeat comes back).
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from outreach.models import OutreachAccount
from outreach.notify import tell_mohammad

STALE_AFTER = timedelta(minutes=15)


def stale_accounts(now=None) -> list[OutreachAccount]:
    """Enabled accounts with a working session whose worker has not checked in for 15 minutes."""
    since = (now or timezone.now()) - STALE_AFTER
    return [
        account
        for account in OutreachAccount.objects.filter(enabled=True, session_ok=True).order_by("name")
        if account.last_heartbeat_at is None or account.last_heartbeat_at < since
    ]


class Command(BaseCommand):
    help = "Alert Mohammad when an outreach account's heartbeat is older than 15 minutes."

    def add_arguments(self, parser):
        parser.add_argument("--alert", action="store_true", help="Send the Telegram alert (the host script dedupes).")

    def handle(self, *args, **options):
        stale = stale_accounts()
        for account in stale:
            self.stdout.write(account.name)
        if stale and options["alert"]:
            names = ", ".join(f"{a.name} ({timezone.localtime(a.last_heartbeat_at).strftime('%H:%M') if a.last_heartbeat_at else '-'})" for a in stale)
            tell_mohammad(f"FYI outreach: worker ساکته، heartbeat قدیمی‌تر از ۱۵ دقیقه: {names}. ری‌استارت خودکار زده شد.")
