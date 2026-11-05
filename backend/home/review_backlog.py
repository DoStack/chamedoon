"""The review gate's bulk work: settling the `new` backlog, re-queuing rule rejects, and the daily sample."""

from __future__ import annotations

import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.utils import timezone

from home import conf
from home import review as review_module
from home.models import HomeGateSample, HomePost, PostStatus
from home.review import GATE_RULE_NO_WORD, apply_gate_decision, gate_decision, housing_word, too_short

logger = logging.getLogger(__name__)

DEFAULT_BATCH = 200
MAX_BATCH = 500
LOCK_WAIT_SECONDS = 600
LOCK_RETRY_SECONDS = 5
GATE_SAMPLE_PER_DAY = 30
GATE_SAMPLE_DAYS = 14
GATE_MISS_LIMIT = 0.01
SAMPLE_RESERVE_SECONDS = 30
GATE_OFF = "review_gate_on is off. Switch it on in Home settings first, or use --dry-run."


def _batch_size(size: int) -> int:
    return max(1, min(MAX_BATCH, size))


def _batches(queryset, size: int, *fields: str):
    """Rows by primary key, one bounded page at a time, so a big table never sits in memory."""
    after = 0
    while True:
        page = list(queryset.filter(pk__gt=after).order_by("pk").values(*fields)[:size])
        if not page:
            return
        yield page
        after = page[-1]["pk"]


def _wait_for_lock() -> bool:
    from home.job import acquire_lock

    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while not acquire_lock():
        if time.monotonic() >= deadline:
            return False
        time.sleep(LOCK_RETRY_SECONDS)
    return True


def settle_backlog(*, dry_run: bool = False, batch_size: int = DEFAULT_BATCH, say=lambda _line: None) -> dict:
    """Run the gate's rules over every `new` post, highest id first. Calls no model.

    Each post is decided as the review would decide it. Going down the ids means a post sees only
    waiting copies that came before it, none of them settled yet, so a dry run counts what a real
    run does. Idempotent: what is settled is no longer `new`. Each batch takes the housing job's
    lock afresh and lets go after, so a running job waits for one small batch at most, and checks
    the flag first, so switching it off stops a run.
    """
    if not dry_run and not conf.review_gate_on():
        return {"refused": GATE_OFF}
    from home.job import release_lock

    size = _batch_size(batch_size)
    counts: Counter = Counter()
    before = None
    while True:
        if not dry_run and not conf.review_gate_on():
            return {**_report(counts, dry_run), "stopped": GATE_OFF}
        if not dry_run and not _wait_for_lock():
            return {**_report(counts, dry_run), "stopped": "another housing run held the lock too long; run this again"}
        try:
            page = HomePost.objects.filter(status=PostStatus.NEW).select_related("source").order_by("-pk")
            if before is not None:
                page = page.filter(pk__lt=before)
            posts = list(page[:size])
            for post in posts:
                decision = gate_decision(post)
                counts[decision.kind] += 1
                if not dry_run:
                    apply_gate_decision(post, decision)
        finally:
            if not dry_run:
                release_lock()
        if not posts:
            return _report(counts, dry_run)
        before = posts[-1].pk
        say(f"decided {sum(counts.values())} posts, down to id {before}")


def _report(counts: Counter, dry_run: bool) -> dict:
    return {
        "dry_run": dry_run,
        "waiting": sum(counts.values()),
        "too_short": counts["too_short"],
        "duplicate": counts["duplicate"],
        "no_housing_word": counts["no_housing_word"],
        "settled": counts["too_short"] + counts["duplicate"] + counts["no_housing_word"],
        "reach_model": counts["reach_model"],
        "sent_back_by_hand": counts["sent_back"],
    }


def _gate_rejects():
    return HomePost.objects.filter(status=PostStatus.REJECTED, model="rule", reject_reason="noise", review_json__rule=GATE_RULE_NO_WORD)


def requeue_rule_rejects(*, dry_run: bool = False, batch_size: int = DEFAULT_BATCH) -> dict:
    """After the word list grows: posts the gate rejected that it would now let through go back in the queue."""
    if not dry_run and not conf.review_gate_on():
        return {"refused": GATE_OFF}
    checked = requeued = 0
    for page in _batches(_gate_rejects(), _batch_size(batch_size), "pk", "text"):
        if not dry_run and not conf.review_gate_on():
            return {"dry_run": dry_run, "checked": checked, "requeued": requeued, "stopped": GATE_OFF}
        checked += len(page)
        passing = [row["pk"] for row in page if housing_word(row["text"])]
        requeued += len(passing)
        if passing and not dry_run:
            HomePost.objects.filter(pk__in=passing).update(status=PostStatus.NEW, reject_reason="", model="", review_json=None, reviewed_at=None)
    return {"dry_run": dry_run, "checked": checked, "requeued": requeued}


# Model verdicts the 2026-11-05 intake audit found wrong for about a third of the posts with a housing word:
# a realtor's specific unit called «ad», a wanted post called «other», a post with a city called «incomplete».
REQUEUE_MODEL_REASONS = ("ad", "other", "incomplete", "not_housing")
REQUEUE_TAG = "requeue"


def wrong_reject_candidates(*, days: int = 3):
    """Rejected posts from the last `days` days that the fixed rules or prompt may decide differently. Reads only.

    Short posts with a housing word that the old length rule settled, and model rejections of a post with a housing word
    for the reasons above. One post per wording (the newest): a copy follows its original's verdict anyway.
    """
    since = timezone.now() - timedelta(days=days)
    rejected = HomePost.objects.filter(status=PostStatus.REJECTED, posted_at__gte=since)
    short = rejected.filter(model="rule", reject_reason="noise", review_json__isnull=True)
    by_model = rejected.exclude(model="rule").exclude(model="").filter(reject_reason__in=REQUEUE_MODEL_REASONS)
    picked: dict[str, dict] = {}
    for group, queryset in (("short", short), ("model", by_model)):
        for row in queryset.order_by("-posted_at", "-pk").values("pk", "text", "text_key", "reject_reason", "model", "review_json", "reviewed_at"):
            text = row["text"] or ""
            if group == "short" and too_short(text):
                continue
            if group == "model" and not housing_word(text):
                continue
            key = row["text_key"] or f"pk:{row['pk']}"
            if key not in picked:
                picked[key] = {**row, "group": group}
    return list(picked.values())


def requeue_wrong_rejects(*, days: int = 3, dry_run: bool = True, backup_path: str = "") -> dict:
    """Send the candidates back to the queue. The model sees each again, with no rule in the way.

    The mark in `review_json` keeps the gate from settling it by rule again (a post sent back is the model's to decide),
    and says where it came from. Writes a backup of every row it changes first, and only rows still rejected.
    """
    rows = wrong_reject_candidates(days=days)
    counts = Counter(f"{row['group']}:{row['reject_reason']}" for row in rows)
    report = {"dry_run": dry_run, "days": days, "candidates": len(rows), "by_reason": dict(sorted(counts.items()))}
    if dry_run:
        return report
    if not backup_path:
        return {**report, "refused": "Give --backup PATH: the rows are written there before anything changes."}
    import json

    with open(backup_path, "w", encoding="utf-8") as handle:
        json.dump(
            [{key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items() if key != "text"} for row in rows],
            handle,
        )
    requeued = 0
    for row in rows:
        mark = {REQUEUE_TAG: "intake-audit-2026-11-05", "was": row["reject_reason"], "was_model": row["model"][:64]}
        requeued += HomePost.objects.filter(pk=row["pk"], status=PostStatus.REJECTED).update(
            status=PostStatus.NEW, reject_reason="", model="", error="", review_json=mark, reviewed_at=None
        )
    logger.info("review_requeue_wrong_rejects requeued=%s backup=%s", requeued, backup_path)
    return {**report, "requeued": requeued, "backup": backup_path}


def gate_sample_report() -> dict:
    samples = HomeGateSample.objects.all()
    answered = samples.filter(answered=True).count()
    missed = samples.filter(missed=True).count()
    return {"attempts": samples.count(), "sampled": answered, "missed": missed, "miss_rate": round(missed / answered, 4) if answered else 0.0}


def _ask(prompt: str, model: str):
    try:
        return review_module.ask_model(prompt, model=model)
    except Exception:  # noqa: BLE001 - a sample must not end the run; the attempt is already on record
        logger.exception("review_gate_sample_call_failed")
        return None


def sample_rule_rejects(*, stop_at: float | None = None) -> dict:
    """For the first two weeks, show a few of each day's rule rejects to the model and log how many it would have kept.

    Only the model's verdict is recorded: the post keeps its rule verdict, and nothing becomes a listing
    or reaches a channel. Every attempt is written before the call, so failures count against the daily cap
    and the two weeks, and a post is tried once.
    """
    now = timezone.now()
    first = HomeGateSample.objects.order_by("created_at").values_list("created_at", flat=True).first()
    if first is not None and now - first > timedelta(days=GATE_SAMPLE_DAYS):
        return {"skipped": "the gate sample has run its two weeks", **gate_sample_report()}
    left = GATE_SAMPLE_PER_DAY - HomeGateSample.objects.filter(created_at__gte=now - timedelta(days=1)).count()
    if left <= 0:
        return {"skipped": "today's gate sample is done", **gate_sample_report()}
    if not review_module.llm_enabled():
        return {"skipped": "no model is configured", **gate_sample_report()}
    if stop_at is not None and time.monotonic() + SAMPLE_RESERVE_SECONDS >= stop_at:
        return {"skipped": "no time left in this run", **gate_sample_report()}
    pool = _gate_rejects().filter(gate_sample__isnull=True, reviewed_at__gte=now - timedelta(days=1)).select_related("source")
    posts = list(pool.order_by("?")[:left])
    if not posts:
        return {"skipped": "no rule rejects to sample today", **gate_sample_report()}
    samples = [HomeGateSample.objects.create(post=post) for post in posts]
    model = conf.review_model()
    prompts = [review_module.build_prompt(post) for post in posts]
    with ThreadPoolExecutor(max_workers=max(1, conf.review_workers())) as pool_runner:
        results = list(pool_runner.map(lambda prompt: _ask(prompt, model), prompts))
    errors = 0
    for sample, result in zip(samples, results):
        data = review_module.parse_json(result.text) if result is not None and result.ok else None
        if result is not None:
            sample.model = (result.model or "")[:96]
        if data is None:
            errors += 1
            sample.error = ((result.error if result is not None else "") or "no usable answer")[:255]
            sample.save(update_fields=["model", "error"])
            continue
        sample.answered = True
        sample.missed = bool(data.get("accept"))
        sample.model_reason = str(data.get("reject_reason") or "")[:64]
        sample.save(update_fields=["model", "answered", "missed", "model_reason"])
    report = gate_sample_report()
    log = logger.warning if report["miss_rate"] > GATE_MISS_LIMIT else logger.info
    log(
        "review_gate_sample attempts=%s sampled=%s missed=%s miss_rate=%s errors=%s",
        report["attempts"],
        report["sampled"],
        report["missed"],
        report["miss_rate"],
        errors,
    )
    return {**report, "errors": errors}
