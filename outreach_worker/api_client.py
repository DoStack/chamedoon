from __future__ import annotations

import json
import urllib.error
import urllib.request

from config import API_BASE_URL, OUTREACH_SERVICE_SECRET


class OutreachApiError(Exception):
    pass


def claim() -> dict:
    return _request("POST", "/api/outreach/claim/", {})


def result(message_id: int, outcome: str, **fields) -> dict:
    return _request("POST", f"/api/outreach/{message_id}/result/", {"outcome": outcome, **fields})


def opt_out(*, username: str, telegram_user_id: int) -> dict:
    return _request("POST", "/api/outreach/opt-out/", {"username": username, "telegram_user_id": telegram_user_id})


def reply(*, username: str, telegram_user_id: int) -> dict:
    return _request("POST", "/api/outreach/reply/", {"username": username, "telegram_user_id": telegram_user_id})


def hold(message_id: int, reason: str = "") -> dict:
    return _request("POST", f"/api/outreach/{message_id}/hold/", {"reason": reason})


def release(message_id: int) -> dict:
    return _request("POST", f"/api/outreach/{message_id}/release/", {})


def build() -> dict:
    return _request("POST", "/api/outreach/build/", {})


def heartbeat() -> dict:
    return _request("POST", "/api/outreach/heartbeat/", {})


def preview(limit: int = 5) -> dict:
    return _request("GET", f"/api/outreach/preview/?limit={int(limit)}")


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Outreach-Secret": OUTREACH_SERVICE_SECRET,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise OutreachApiError(detail or f"Backend {exc.code} for {path}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise OutreachApiError(f"Backend unavailable for {path}: {exc}") from exc
