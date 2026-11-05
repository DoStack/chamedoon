from __future__ import annotations

import json
import urllib.error
import urllib.request

from config import API_BASE_URL, OUTREACH_SERVICE_SECRET


class OutreachApiError(Exception):
    pass


def claim(account: str) -> dict:
    return _request("POST", "/api/outreach/claim/", {"account": account})


def result(message_id: int, outcome: str, **fields) -> dict:
    return _request("POST", f"/api/outreach/{message_id}/result/", {"outcome": outcome, **fields})


def inbound(*, account: str, telegram_user_id: int, username: str, message_id: int, text: str, has_photo: bool) -> dict:
    return _request(
        "POST",
        "/api/outreach/inbound/",
        {"account": account, "telegram_user_id": telegram_user_id, "username": username, "message_id": message_id, "text": text, "has_photo": has_photo},
    )


def inbound_photo(turn_id: int, data_base64: str) -> dict:
    return _request("POST", f"/api/outreach/turn/{turn_id}/photo/", {"data": data_base64})


def agent_next(account: str) -> dict:
    return _request("POST", "/api/outreach/agent/next/", {"account": account}, timeout=55)


def agent_result(turn_id: int, *, ok: bool, telegram_message_id: int | None = None, error: str = "", retry_after_seconds: int | None = None) -> dict:
    return _request(
        "POST",
        f"/api/outreach/turn/{turn_id}/sent/",
        {"ok": ok, "telegram_message_id": telegram_message_id, "error": error, "retry_after_seconds": retry_after_seconds},
    )


def hold(message_id: int, reason: str = "") -> dict:
    return _request("POST", f"/api/outreach/{message_id}/hold/", {"reason": reason})


def release(message_id: int) -> dict:
    return _request("POST", f"/api/outreach/{message_id}/release/", {})


def build() -> dict:
    return _request("POST", "/api/outreach/build/", {})


def heartbeat(account: str = "", *, session_invalid: bool = False) -> dict:
    return _request("POST", "/api/outreach/heartbeat/", {"account": account, "session_invalid": session_invalid})


def preview(limit: int = 5) -> dict:
    return _request("GET", f"/api/outreach/preview/?limit={int(limit)}")


def _request(method: str, path: str, payload: dict | None = None, *, timeout: int = 20) -> dict:
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise OutreachApiError(detail or f"Backend {exc.code} for {path}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise OutreachApiError(f"Backend unavailable for {path}: {exc}") from exc
