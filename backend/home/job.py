"""One housing job run: read sources, review waiting posts with the AI, then handle photos.

Vercel functions are short, so every stage works through a bounded batch and reports
how many are left. Call the cron again, or run the management command, to clear a
backlog. Order matters: photos are fetched only for listings the review accepted.

Alerts («گوش به زنگ») go out right after the review accepts a listing, in the same run,
and once more at the end. So a DM is as fresh as the run that read the post: with the
Vercel crons that is a few times a day; minute-level latency needs the reader on a
server, `python manage.py home_job --every 5`.
"""

from __future__ import annotations

import time
from datetime import timedelta

from django.db import models
from django.utils import timezone

from home import conf
from home.alerts import notify_pending
from home.ingest import DEFAULT_LOOKBACK_DAYS, ingest_sources
from home.media import process_pending_media
from home.publish import publish_pending
from home.models import HomePost, HomeSettings, PostStatus
from home.review import review_post
from home.storage import get_storage
from home.telegram import HomeHistoryReader
from home.vision import enrich_pending

JOB_SECONDS = 270
REVIEW_RESERVE_SECONDS = 4
ERROR_RETRY_HOURS = 3
STEPS = {"all", "ingest", "review", "media", "vision", "publish", "alerts"}


LOCK_SECONDS = 300


def acquire_lock(field: str = "job_lock_until") -> bool:
    """Only one housing run at a time: two runs would open the same Telegram session at once and review the same posts twice.

    Posting has its own lock (`publish_lock_until`): it only talks to the Bot API, so a long read never holds it up.
    """
    now = timezone.now()
    HomeSettings.load()
    taken = (
        HomeSettings.objects.filter(pk=1)
        .filter(models.Q(**{f"{field}__isnull": True}) | models.Q(**{f"{field}__lt": now}))
        .update(**{field: now + timedelta(seconds=LOCK_SECONDS)})
    )
    return bool(taken)


def release_lock(field: str = "job_lock_until") -> None:
    HomeSettings.objects.filter(pk=1).update(**{field: None})


def publish_with_lock(stop_at: float | None) -> dict:
    if not acquire_lock("publish_lock_until"):
        return {"ok": True, "skipped": "posts are already going out in another run", "published": 0}
    try:
        return publish_pending(stop_at=stop_at)
    finally:
        release_lock("publish_lock_until")


def alerts_with_lock(stop_at: float | None) -> dict:
    if not acquire_lock("alerts_lock_until"):
        return {"ok": True, "skipped": "alerts are already going out in another run", "sent": 0}
    try:
        return notify_pending(stop_at=stop_at)
    finally:
        release_lock("alerts_lock_until")


def home_enabled() -> bool:
    return conf.home_enabled()


def pending_posts():
    """New posts, plus ones that failed earlier and are old enough to retry."""
    retry_before = timezone.now() - timedelta(hours=ERROR_RETRY_HOURS)
    waiting = HomePost.objects.filter(status=PostStatus.NEW)
    failed = HomePost.objects.filter(status=PostStatus.ERROR, reviewed_at__lt=retry_before)
    return (waiting | failed).select_related("source").order_by("-posted_at", "-telegram_message_id")


QUICK_SCAN = 4000


def review_pending(*, limit: int, stop_at: float | None = None) -> dict:
    """Reposts and noise are settled by rule first, then up to `limit` posts go to the model,
    several at a time. Only the HTTP calls run in threads; every database write stays here."""
    from concurrent.futures import ThreadPoolExecutor

    from home import review as review_module

    accepted = rejected = reposts = errors = done = 0
    waiting: list[HomePost] = []
    for post in pending_posts()[:QUICK_SCAN]:
        if stop_at is not None and time.monotonic() + REVIEW_RESERVE_SECONDS >= stop_at:
            break
        verdict = review_module.precheck(post)
        if verdict is None:
            waiting.append(post)
            if len(waiting) >= limit:
                break
            continue
        done += 1
        if verdict.reject_reason == "repost":
            reposts += 1
        else:
            rejected += 1
    if waiting and review_module.llm_enabled():
        model = conf.review_model()
        workers = conf.review_workers()
        prompts = [review_module.build_prompt(post) for post in waiting]
        if workers <= 1:
            results = [review_module.ask_model(prompt, model=model) for prompt in prompts]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(lambda prompt: review_module.ask_model(prompt, model=model), prompts))
        for post, result in zip(waiting, results):
            outcome = review_module.finish_with_result(post, result)
            done += 1
            if outcome.accepted:
                accepted += 1
            elif outcome.error:
                errors += 1
            else:
                rejected += 1
    skipped = {}
    if waiting and not review_module.llm_enabled():
        # Nothing can be reviewed without a model: name the missing setting instead of a bare error count.
        from api.config_check import llm_missing, report_missing

        errors += len(waiting)
        skipped = {"skipped": report_missing("housing review", llm_missing())}
    return {
        **skipped,
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
    if step == "publish":
        # Posting uses the housing bot, not the Telegram reader, so it does not wait for a read in progress.
        if not conf.step_enabled("publish"):
            return {**out, "publish": {"ok": True, "skipped": "publish is switched off in Home settings"}}
        return {**out, "publish": publish_with_lock(stop_at)}
    if step == "alerts":
        # Alerts only talk to the Bot API too, and run right after posting in a whole run.
        if not conf.step_enabled("alerts"):
            return {**out, "alerts": {"ok": True, "skipped": "alerts are switched off in Home settings"}}
        return {**out, "alerts": alerts_with_lock(stop_at)}
    if not acquire_lock():
        return {**out, "ok": False, "skipped": "another housing run is still in progress; try again in a few minutes"}
    try:
        return _run_steps(out, step, whole, budget, started, stop_at, days, review_limit, media_limit, usernames, reader)
    finally:
        release_lock()


def _run_steps(out, step, whole, budget, started, stop_at, days, review_limit, media_limit, usernames, reader) -> dict:
    off = [name for name in ("ingest", "review", "media", "vision", "publish", "alerts") if step in {"all", name} and not conf.step_enabled(name)]
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
        if out["review"].get("accepted") and conf.step_enabled("alerts"):
            # Instant DM: listings accepted in this run reach matching alerts now, before photos and posting
            # take the rest of the time. The last step below catches anything this one had no time for.
            out["alerts_after_review"] = alerts_with_lock(min(stop_at, started + budget * 0.8) if whole else stop_at)
    if step in {"all", "media"} and "media" in off:
        pass
    elif step in {"all", "media"} and get_storage() is None:
        from home.media import storage_skip_message

        out["media"] = {"ok": True, "skipped": storage_skip_message(), "stored": 0, "failed": 0}
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
        out["publish"] = publish_with_lock(stop_at)
    if step in {"all", "alerts"} and "alerts" not in off:
        out["alerts"] = alerts_with_lock(stop_at)
    return out
