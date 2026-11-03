"""The housing bot's log for the back office. A write that fails never reaches the person using the bot."""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.db import DatabaseError
from django.utils import timezone

from home import conf
from home.models import HomeAiUsage, HomeBotMessage, HomeSearchQuery

logger = logging.getLogger(__name__)

TEXT_LIMIT = 500
# Enough to read today's caps across a time zone. The counter holds no text, but no reason to keep ids either.
USAGE_KEEP_DAYS = 2
_TAG = re.compile(r"<[^>]+>")


def record(telegram_id: int, direction: str, kind: str, text: str = "", query: HomeSearchQuery | None = None) -> None:
    try:
        HomeBotMessage.objects.create(
            telegram_user_id=telegram_id, direction=direction, kind=kind, text=_TAG.sub("", text or "")[:TEXT_LIMIT], query=query
        )
    except DatabaseError:
        logger.warning("Bot message log write failed", exc_info=True)


def purge() -> dict:
    """Delete what is older than the retention days. The freshness step calls it on every run."""
    before = timezone.now() - timedelta(days=conf.search_log_retention_days())
    messages, _ = HomeBotMessage.objects.filter(created_at__lt=before).delete()
    queries, _ = HomeSearchQuery.objects.filter(created_at__lt=before).delete()
    usage, _ = HomeAiUsage.objects.filter(day__lt=timezone.localdate() - timedelta(days=USAGE_KEEP_DAYS)).delete()
    return {"messages": messages, "queries": queries, "usage": usage}


def forget(telegram_id: int) -> None:
    """/forget: the person's messages and searches go. Their alerts, saved listings and HomeAiUsage stay: that row is a budget."""
    HomeBotMessage.objects.filter(telegram_user_id=telegram_id).delete()
    HomeSearchQuery.objects.filter(telegram_user_id=telegram_id).delete()
