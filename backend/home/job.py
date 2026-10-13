"""One housing job run: read sources, then review waiting posts with the AI.

Vercel functions are short, so the review step works through a bounded batch,
newest posts first, and reports how many are left. Call the cron again, or run
the management command, to clear a backlog.
"""

from __future__ import annotations

import time
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from home.ingest import DEFAULT_LOOKBACK_DAYS, ingest_sources
from home.models import HomePost, PostStatus
from home.review import review_post

JOB_SECONDS = 50
REVIEW_RESERVE_SECONDS = 4
ERROR_RETRY_HOURS = 3


def home_enabled() -> bool:
    return bool(getattr(settings, "HOME_ENABLED", True))


def pending_posts():
    """New posts, plus ones that failed earlier and are old enough to retry."""
    retry_before = timezone.now() - timedelta(hours=ERROR_RETRY_HOURS)
    waiting = HomePost.objects.filter(status=PostStatus.NEW)
    failed = HomePost.objects.filter(status=PostStatus.ERROR, reviewed_at__lt=retry_before)
    return (waiting | failed).select_related("source").order_by("-posted_at", "-telegram_message_id")


def review_pending(*, limit: int, stop_at: float | None = None) -> dict:
    accepted = rejected = errors = 0
    done = 0
    for post in pending_posts()[:limit]:
        if stop_at is not None and time.monotonic() + REVIEW_RESERVE_SECONDS >= stop_at:
            break
        outcome = review_post(post)
        done += 1
        if outcome.accepted:
            accepted += 1
        elif outcome.error:
            errors += 1
        else:
            rejected += 1
    return {
        "reviewed": done,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "remaining": pending_posts().count(),
    }


def run_home_job(
    *,
    step: str = "all",
    days: int = DEFAULT_LOOKBACK_DAYS,
    review_limit: int = 15,
    stop_at: float | None = None,
    usernames: list[str] | None = None,
    reader=None,
) -> dict:
    started = time.monotonic()
    if stop_at is None:
        stop_at = started + JOB_SECONDS
    out: dict = {"ok": True, "step": step}
    if step in {"all", "ingest"}:
        ingest = ingest_sources(days=days, stop_at=stop_at - 20 if step == "all" else stop_at, usernames=usernames, reader=reader)
        out["ingest"] = ingest
        out["ok"] = bool(ingest.get("ok"))
    if step in {"all", "review"}:
        out["review"] = review_pending(limit=review_limit, stop_at=stop_at)
    return out
