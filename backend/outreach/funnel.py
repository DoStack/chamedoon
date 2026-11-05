"""The funnel of each model, account, wording and country, from our own tables (spec section 9).

Sent, replied, opened, member, bot start, active, back after 7 and 30 days, asked to stop. One unanswered message is
final, so the reply rate is the main number. Below 30 sent, a row decides nothing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from django.db.models import Max
from django.utils import timezone

from home.models import HomeChatState
from outreach.models import FIRST_CONTACT_KINDS, OutreachMessage, OutreachStatus
from users.models import User

COLUMNS = ("sent", "replied", "opened", "member", "bot_start", "active", "back_7d", "back_30d", "stopped")
DECIDE_AFTER = 30


def _last_seen(telegram_ids: set[int]) -> dict[int, datetime]:
    seen: dict[int, datetime] = {}
    for telegram_id, at in User.objects.filter(telegram_user_id__in=telegram_ids).values_list("telegram_user_id", "updated_at"):
        seen[telegram_id] = at
    for telegram_id, at in HomeChatState.objects.filter(telegram_user_id__in=telegram_ids).values_list("telegram_user_id", "updated_at"):
        seen[telegram_id] = max(at, seen.get(telegram_id, at))
    return seen


def rows(*, by: tuple[str, ...] = ("kind", "account", "variant", "country"), since: datetime | None = None) -> list[dict]:
    sent = OutreachMessage.objects.filter(status=OutreachStatus.SENT, kind__in=FIRST_CONTACT_KINDS).select_related("account")
    if since is not None:
        sent = sent.filter(sent_at__gte=since)
    messages = list(sent)
    seen = _last_seen({message.recipient_telegram_id for message in messages if message.recipient_telegram_id})
    groups: dict[tuple, dict] = defaultdict(lambda: dict.fromkeys(COLUMNS, 0))
    for message in messages:
        key = tuple((message.account.name if message.account else "-") if name == "account" else (getattr(message, name) or "-") for name in by)
        row = groups[key]
        row["sent"] += 1
        row["replied"] += message.replied_at is not None
        row["opened"] += message.opened_at is not None
        row["member"] += message.member_at is not None
        row["bot_start"] += message.bot_started_at is not None
        row["active"] += message.active_at is not None
        row["stopped"] += message.stopped_at is not None
        start = message.member_at
        last = seen.get(message.recipient_telegram_id or 0)
        if start and last:
            row["back_7d"] += last >= start + timedelta(days=7)
            row["back_30d"] += last >= start + timedelta(days=30)
    out = []
    for key, row in sorted(groups.items()):
        rates = {f"{name}_rate": (row[name] * 100 // row["sent"]) if row["sent"] else 0 for name in ("replied", "member", "stopped")}
        out.append({**dict(zip(by, key)), **row, **rates, "verdict": verdict(row)})
    return out


def verdict(row: dict) -> str:
    """The rule after 100 messages or 4 weeks; under 30 sent it says nothing."""
    if row["sent"] < DECIDE_AFTER:
        return "too few"
    if row["stopped"] * 100 > row["sent"] * 5:
        return "stop and review"
    member = row["member"] * 100 / row["sent"]
    if member >= 10:
        return "send more"
    if member >= 5:
        return "change the text"
    return "keep the model"
