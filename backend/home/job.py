"""One housing job run: read sources, review waiting posts with the AI, then handle photos.

Vercel functions are short, so every stage works through a bounded batch and reports
how many are left. Call the cron again, or run the management command, to clear a
backlog. Order matters: photos are fetched only for listings the review accepted.
"""

from __future__ import annotations

import time
from datetime import timedelta

from django.utils import timezone

from home import conf
from home.ingest import DEFAULT_LOOKBACK_DAYS, ingest_sources
from home.media import process_pending_media
from home.publish import publish_pending
from home.models import HomePost, PostStatus
from home.review import review_post
from home.storage import get_storage
from home.telegram import HomeHistoryReader
from home.vision import enrich_pending

JOB_SECONDS = 270
REVIEW_RESERVE_SECONDS = 4
ERROR_RETRY_HOURS = 3
STEPS = {"all", "ingest", "review", "media", "vision", "publish"}


def home_enabled() -> bool:
    return conf.home_enabled()


def pending_posts():
    """New posts, plus ones that failed earlier and are old enough to retry."""
    retry_before = timezone.now() - timedelta(hours=ERROR_RETRY_HOURS)
    waiting = HomePost.objects.filter(status=PostStatus.NEW)
    failed = HomePost.objects.filter(status=PostStatus.ERROR, reviewed_at__lt=retry_before)
    return (waiting | failed).select_related("source").order_by("-posted_at", "-telegram_message_id")


def review_pending(*, limit: int, stop_at: float | None = None) -> dict:
    accepted = rejected = reposts = errors = 0
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
        elif outcome.reject_reason == "repost":
            reposts += 1
        else:
            rejected += 1
    return {
        "reviewed": done,
        "accepted": accepted,
        "rejected": rejected,
        "reposts": reposts,
        "errors": errors,
        "remaining": pending_posts().count(),
    }


def run_home_job(
    *,
    step: str = "all",
    days: int | None = None,
    review_limit: int | None = None,
    media_limit: int | None = None,
    stop_at: float | None = None,
    usernames: list[str] | None = None,
    reader=None,
) -> dict:
    started = time.monotonic()
    if stop_at is None:
        stop_at = started + JOB_SECONDS
    if step not in STEPS:
        step = "all"
    whole = step == "all"
    budget = max(1.0, stop_at - started)
    out: dict = {"ok": True, "step": step}
    review_limit = review_limit or conf.review_batch()
    media_limit = media_limit or conf.media_batch()
    if days is None:
        days = conf.lookback_days()
    if not conf.home_enabled():
        return {**out, "ok": False, "skipped": "Chamedoon Home is switched off in Home settings"}
    off = [name for name in ("ingest", "review", "media", "vision", "publish") if step in {"all", name} and not conf.step_enabled(name)]
    for name in off:
        out[name] = {"ok": True, "skipped": f"{name} is switched off in Home settings"}
    if step in {"all", "ingest"} and "ingest" not in off:
        ingest = ingest_sources(
            days=days,
            stop_at=min(stop_at, started + budget * 0.3) if whole else stop_at,
            usernames=usernames,
            reader=reader,
        )
        out["ingest"] = ingest
        out["ok"] = bool(ingest.get("ok"))
    if step in {"all", "review"} and "review" not in off:
        out["review"] = review_pending(limit=review_limit, stop_at=min(stop_at, started + budget * 0.75) if whole else stop_at)
    if step in {"all", "media"} and "media" in off:
        pass
    elif step in {"all", "media"} and get_storage() is None:
        out["media"] = {"ok": True, "skipped": "photo storage is not configured", "stored": 0, "failed": 0}
    elif step in {"all", "media"}:
        own_reader = reader is None
        photo_reader = reader or HomeHistoryReader()
        try:
            if own_reader:
                photo_reader.open()
            out["media"] = process_pending_media(
                photo_reader, limit=media_limit, stop_at=min(stop_at, started + budget * 0.92) if whole else stop_at
            )
        except Exception as exc:  # noqa: BLE001 - photos must not fail the whole job
            out["media"] = {"ok": False, "error": str(exc)[:200]}
        finally:
            if own_reader:
                photo_reader.close()
    if step in {"all", "vision"} and "vision" not in off:
        out["vision"] = enrich_pending(limit=5, stop_at=stop_at)
    if step in {"all", "publish"} and "publish" not in off:
        out["publish"] = publish_pending(stop_at=stop_at)
    return out
