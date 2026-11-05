"""Messages after a chat: P3 (numbers) and P5 (still available?). Built in phase 2."""

from __future__ import annotations

from datetime import datetime

from outreach.models import OutreachMessage


def skip_reason(message: OutreachMessage, now: datetime) -> str:
    return ""
