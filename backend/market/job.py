from __future__ import annotations

import time
from typing import Callable

from item_requests.services import expire_due_requests
from market.ingest import ingest_all_market_channels
from market.migrate import migrate_market_posts

JOB_SECONDS = 48
MIGRATE_RESERVE_SECONDS = 14


def run_market_job(
    *,
    fetch_page: Callable[[str, int | None], str] | None = None,
    budget_seconds: float | None = None,
    stop_at: float | None = None,
    days: int | None = None,
) -> dict:
    started = time.monotonic()
    if stop_at is None and fetch_page is None:
        stop_at = started + JOB_SECONDS
    expired = expire_due_requests(sync_channel=False)
    if budget_seconds is None and stop_at is not None:
        budget_seconds = max(1.0, stop_at - time.monotonic() - MIGRATE_RESERVE_SECONDS)
    ingest = ingest_all_market_channels(
        fetch_page=fetch_page,
        budget_seconds=budget_seconds,
        days=days,
        stop_at=stop_at,
    )
    migrated = migrate_market_posts(stop_at=stop_at, wait_for_llm=False)
    return {
        "ok": bool(ingest.get("ok") and migrated.get("ok")),
        "expired": expired,
        "ingest": ingest,
        "migrate": migrated,
        "days": days,
    }
