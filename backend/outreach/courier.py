"""Courier models beyond C1: built in phase 3."""

from __future__ import annotations

from datetime import datetime

from outreach.models import OutreachMessage


def build_courier_waves(result, seen: set[str], *, now: datetime, dry_run: bool) -> None:
    return None


def skip_reason(message: OutreachMessage, now: datetime) -> str:
    return "kind_off"


def render(message: OutreachMessage) -> str:
    return message.text
