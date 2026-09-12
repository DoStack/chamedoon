from __future__ import annotations

from typing import Callable

from item_requests.services import expire_due_requests
from market.ingest import ingest_all_market_channels
from market.migrate import migrate_market_posts


def run_market_job(
    *,
    fetch_page: Callable[[str, int | None], str] | None = None,
    budget_seconds: float | None = None,
    stop_at: float | None = None,
) -> dict:
    expired = expire_due_requests()
    ingest = ingest_all_market_channels(fetch_page=fetch_page, budget_seconds=budget_seconds)
    migrated = migrate_market_posts(stop_at=stop_at, wait_for_llm=False)
    return {
        "ok": bool(ingest.get("ok") and migrated.get("ok")),
        "expired": expired,
        "ingest": ingest,
        "migrate": migrated,
    }
