"""Before any first message: does the post really offer a place, or look for one? (Mohammad, 2026-11-06.)

A room offered as «جویای هم‌خانه» was stored as a request and its poster got seeker messages. One model call reads the
post text again before the first message goes; when it disagrees with the stored kind, nothing is sent and the
listing goes back to review.
"""

from __future__ import annotations

import json

from django.utils import timezone


from outreach.models import OutreachKind, OutreachMessage

SEEKER = {OutreachKind.S1, OutreachKind.S1B, OutreachKind.S2}
OWNER = {OutreachKind.P1, OutreachKind.P1Q, OutreachKind.P6}
PROMPT = """You read one housing post from a Telegram group. Decide who has the place.
"offer": the poster has a room or home and wants a tenant or flatmate. «جویای هم‌خانه», «هم‌خونه می‌خوام», «اتاق آماده اجاره است»,
«Nachmieter gesucht», «Mitbewohner gesucht» with a room described are offers.
"wanted": the poster has no place and is looking for one.
Answer with JSON only: {"kind": "offer"} or {"kind": "wanted"}."""


def agrees(message: OutreachMessage) -> bool | None:
    """True when the post matches the message's audience, False when it does not, None when the model is down."""
    if message.kind not in SEEKER | OWNER or message.listing is None:
        return True
    checked = (message.extra or {}).get("kind_checked")
    if checked in {"offer", "wanted"}:
        return _fits(message, checked)
    from ai.openrouter import complete

    text = (message.listing.post.text or "")[:2500]
    result = complete(text, system=PROMPT, temperature=0, max_tokens=20)
    if not result.ok:
        return None
    try:
        kind = json.loads((result.text or "").strip().strip("`").removeprefix("json").strip()).get("kind")
    except (ValueError, AttributeError):
        return None
    if kind not in {"offer", "wanted"}:
        return None
    message.extra = {**(message.extra or {}), "kind_checked": kind}
    return _fits(message, kind)


def _fits(message: OutreachMessage, kind: str) -> bool:
    return (kind == "wanted") if message.kind in SEEKER else (kind == "offer")


def flag(message: OutreachMessage) -> None:
    """The stored kind is doubtful: the listing goes back to review and leaves the site until a person looks."""
    listing = message.listing
    listing.needs_review = True
    listing.save(update_fields=["needs_review", "updated_at"])
    message.extra = {**(message.extra or {}), "kind_flagged_at": timezone.now().isoformat(), "stored_kind": listing.kind}
