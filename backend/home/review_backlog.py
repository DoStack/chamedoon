"""The review gate's bulk work: settling the `new` backlog, re-queuing rule rejects, and the daily sample.

The backlog pass plans from memory rather than asking the database per post, so `--dry-run`
gets the same answer as a real run without writing: a real run settles copies as it goes,
and a dry run has to see those copies gone too. It applies the rules of `review.precheck`
through the same `pick_original`.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from home import conf
from home import review as review_module
from home.models import HomeGateSample, HomePost, PostStatus
from home.review import EMPTY_KEY, GATE_RULE_NO_WORD, MIN_TEXT_CHARS, PostCopy, housing_word, is_live_copy, pick_original

logger = logging.getLogger(__name__)

DEFAULT_BATCH = 500
LOCK_WAIT_SECONDS = 600
LOCK_RETRY_SECONDS = 5
GATE_SAMPLE_PER_DAY = 30
GATE_SAMPLE_DAYS = 14
GATE_MISS_LIMIT = 0.01
SAMPLE_RESERVE_SECONDS = 30


def _batches(queryset, size: int, *fields: str):
    """Rows by primary key, one bounded page at a time, so a big table never sits in memory."""
    after = 0
    while True:
        page = list(queryset.filter(pk__gt=after).order_by("pk").values(*fields)[:size])
        if not page:
            return
        yield page
        after = page[-1]["pk"]


@dataclass
class _Plan:
    """Who is still a live copy of what, and the waiting posts to decide."""

    copies: dict = field(default_factory=lambda: defaultdict(dict))
    waiting: list = field(default_factory=list)
    sent_back: int = 0

    def add(self, row: dict) -> None:
        key = (row["source__country"], row["text_key"])
        has_key = row["text_key"] and row["text_key"] != EMPTY_KEY
        if has_key:
            copy = PostCopy(row["pk"], row["posted_at"], row["status"] == PostStatus.ACCEPTED, row["listing__last_seen"])
            self.copies[key][copy.pk] = copy
        if row["status"] == PostStatus.NEW:
            if row["review_json"]:
                self.sent_back += 1
            else:
                self.waiting.append((row["posted_at"], row["pk"], key))

    def others(self, pk: int, key: tuple) -> list[PostCopy]:
        return [copy for other, copy in self.copies.get(key, {}).items() if other != pk]

    def remove(self, pk: int, key: tuple) -> None:
        self.copies.get(key, {}).pop(pk, None)


def _build_plan(batch_size: int, say) -> _Plan:
    plan = _Plan()
    fields = ("pk", "posted_at", "status", "reject_reason", "model", "text_key", "review_json", "source__country", "listing__last_seen")
    for page in _batches(HomePost.objects.all(), batch_size, *fields):
        for row in page:
            if is_live_copy(row["status"], row["reject_reason"], row["model"]):
                plan.add(row)
        say(f"read up to post {page[-1]['pk']}: {len(plan.waiting)} waiting so far")
    return plan


def _wait_for_lock() -> bool:
    from home.job import acquire_lock

    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while not acquire_lock():
        if time.monotonic() >= deadline:
            return False
        time.sleep(LOCK_RETRY_SECONDS)
    return True


def settle_backlog(*, dry_run: bool = False, batch_size: int = DEFAULT_BATCH, say=lambda _line: None) -> dict:
    """Run the gate's rules over every `new` post, newest first as the review would. Calls no model.

    Idempotent: a post settled once is no longer `new`, and what survives stays a live copy,
    so a second run settles only what arrived since. Each batch holds the housing job's lock
    and lets go between batches, so a running job only waits for one batch.
    """
    from home.job import release_lock

    batch_size = max(1, batch_size)
    plan = _build_plan(batch_size, say)
    counts: Counter = Counter()
    plan.waiting.sort(key=lambda item: (item[0], item[1]), reverse=True)
    for start in range(0, len(plan.waiting), batch_size):
        chunk = plan.waiting[start : start + batch_size]
        if not dry_run and not _wait_for_lock():
            return {**_report(plan, counts, dry_run), "stopped": "another housing run held the lock too long; run this again"}
        try:
            _settle_chunk(chunk, plan, counts, dry_run)
        finally:
            if not dry_run:
                release_lock()
        say(f"decided {min(start + batch_size, len(plan.waiting))} of {len(plan.waiting)}")
    return _report(plan, counts, dry_run)


def _settle_chunk(chunk: list, plan: _Plan, counts: Counter, dry_run: bool) -> None:
    pks = [item[1] for item in chunk]
    posts = {post.pk: post for post in HomePost.objects.filter(pk__in=pks, status=PostStatus.NEW).select_related("source")}
    for posted_at, pk, key in chunk:
        post = posts.get(pk)
        if post is None:
            continue  # The job got to it after the plan was made.
        text = (post.text or "").strip()
        if len(text) < MIN_TEXT_CHARS:
            counts["too_short"] += 1
            if not dry_run:
                review_module.precheck(post, gate=True)
            continue
        original_pk = pick_original(posted_at, plan.others(pk, key))
        if original_pk is not None:
            plan.remove(pk, key)
            counts["duplicate"] += 1
            if not dry_run:
                review_module.settle_duplicate(post, HomePost.objects.get(pk=original_pk))
            continue
        if not housing_word(text):
            counts["no_housing_word"] += 1
            if not dry_run:
                review_module.settle_no_housing_word(post)
            continue
        counts["reach_model"] += 1


def _report(plan: _Plan, counts: Counter, dry_run: bool) -> dict:
    return {
        "dry_run": dry_run,
        "waiting": len(plan.waiting),
        "too_short": counts["too_short"],
        "duplicate": counts["duplicate"],
        "no_housing_word": counts["no_housing_word"],
        "settled": counts["too_short"] + counts["duplicate"] + counts["no_housing_word"],
        "reach_model": counts["reach_model"],
        "sent_back_by_hand": plan.sent_back,
    }


def _gate_rejects():
    return HomePost.objects.filter(status=PostStatus.REJECTED, model="rule", reject_reason="noise", review_json__rule=GATE_RULE_NO_WORD)


def requeue_rule_rejects(*, dry_run: bool = False, batch_size: int = DEFAULT_BATCH) -> dict:
    """After the word list grows: posts the gate rejected that it would now let through go back in the queue."""
    checked = requeued = 0
    for page in _batches(_gate_rejects(), max(1, batch_size), "pk", "text"):
        checked += len(page)
        passing = [row["pk"] for row in page if housing_word(row["text"])]
        requeued += len(passing)
        if passing and not dry_run:
            HomePost.objects.filter(pk__in=passing).update(status=PostStatus.NEW, reject_reason="", model="", review_json=None, reviewed_at=None)
    return {"dry_run": dry_run, "checked": checked, "requeued": requeued}


def gate_sample_report() -> dict:
    total = HomeGateSample.objects.count()
    missed = HomeGateSample.objects.filter(missed=True).count()
    return {"sampled": total, "missed": missed, "miss_rate": round(missed / total, 4) if total else 0.0}


def sample_rule_rejects(*, stop_at: float | None = None) -> dict:
    """For the first two weeks, show a few of each day's rule rejects to the model and log how many it would have kept.

    A post the model accepts becomes a listing like any other: it was a real one, and rejecting it again helps nobody.
    A post it also rejects keeps its rule verdict. A failed call changes nothing and the post can be picked again.
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
    model = conf.review_model()
    workers = conf.review_workers()
    prompts = [review_module.build_prompt(post) for post in posts]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool_runner:
        results = list(pool_runner.map(lambda prompt: review_module.ask_model(prompt, model=model), prompts))
    errors = 0
    for post, result in zip(posts, results):
        data = review_module.parse_json(result.text) if result.ok else None
        if data is None:
            errors += 1
            continue
        missed = False
        reason = str(data.get("reject_reason") or "")
        if data.get("accept"):
            outcome = review_module.apply_review(post, data, model=result.model)
            missed = outcome.accepted
            reason = outcome.reject_reason
        HomeGateSample.objects.create(post=post, missed=missed, model_reason=reason[:64], model=(result.model or "")[:96])
    report = gate_sample_report()
    log = logger.warning if report["miss_rate"] > GATE_MISS_LIMIT else logger.info
    log("review_gate_sample sampled=%s missed=%s miss_rate=%s errors=%s", report["sampled"], report["missed"], report["miss_rate"], errors)
    return {**report, "errors": errors}
