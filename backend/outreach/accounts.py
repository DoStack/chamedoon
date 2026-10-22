"""Claims for a named sending account (sina, sara, ...): per-account switch, daily limit and gap.

A claim without an account goes straight to services.claim_next_message, exactly as before.
"""

from __future__ import annotations

import re
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from outreach import conf
from outreach.models import OutreachAccount, OutreachMessage
from outreach.persian import TEHRAN_TZ
from outreach.services import ATTEMPTED_STATUSES, claim_next_message

ACCOUNT_NAME = re.compile(r"^[a-z0-9_-]{1,32}$")


def clean_account_name(value) -> str:
    return str(value or "").strip().lstrip("@").lower()


def attempted_today_for(account: OutreachAccount, now: datetime) -> int:
    midnight = now.astimezone(TEHRAN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return OutreachMessage.objects.filter(
        account=account, status__in=ATTEMPTED_STATUSES, claimed_at__gte=midnight
    ).count()


def account_gate(account: OutreachAccount, now: datetime) -> str:
    """Empty string when this account may take a message now, else why not."""
    if not account.enabled:
        return "account_disabled"
    limit = conf.account_daily_limit(account)
    if limit and attempted_today_for(account, now) >= limit:
        return "account_daily_limit"
    gap = conf.account_gap(account)
    if gap and account.last_sent_at and account.last_sent_at > now - gap:
        return "account_gap"
    return ""


def claim_for_account(name, now: datetime | None = None) -> tuple[OutreachMessage | None, str]:
    """Claim the next message for `name`. An unknown name is registered (switched on) so it shows in the admin."""
    now = now or timezone.now()
    name = clean_account_name(name)
    if not ACCOUNT_NAME.fullmatch(name):
        return None, "bad_account"
    with transaction.atomic():
        OutreachAccount.objects.get_or_create(name=name)
        account = OutreachAccount.objects.select_for_update().get(name=name)
        gate = account_gate(account, now)
        if gate:
            return None, gate
        message, reason = claim_next_message(now)
        if message is None:
            return None, reason
        message.account = account
        message.save(update_fields=["account", "updated_at"])
        account.last_sent_at = now
        account.save(update_fields=["last_sent_at"])
        return message, ""
