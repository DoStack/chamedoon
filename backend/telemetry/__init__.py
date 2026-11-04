"""PostHog capture for the server, the bots and the worker. Never slows or breaks the caller.

`capture()` only scrubs and puts the event on a queue; one daemon thread posts batches. On Vercel a function can freeze
once its response is out, so `request_finished` waits for that thread, for FLUSH_SECONDS at most and only when something
is pending. An event still in flight when a function freezes goes out when it thaws, with its original time.

Off unless POSTHOG_KEY and POSTHOG_HOST are set, and always off under pytest. Taxonomy: exec/posthog-2026-11-04/EVENTS.md.
"""

from __future__ import annotations

import atexit
import http.client
import json
import logging
import os
import queue
import re
import threading
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

FLUSH_SECONDS = 0.25
EXIT_FLUSH_SECONDS = 3.0
QUEUE_MAX = 2000
BATCH_MAX = 100
SEND_TIMEOUT = 4.0

# A property with one of these names never leaves, whatever its value.
BANNED_KEY = re.compile(r"phone|username|first_name|last_name|full_name|contact_value|telegram_handle|init_?data|token|secret|password|authorization|cookie|jwt|^text$|^body$|^raw$|^message$", re.I)

_DIGIT = r"[\d۰-۹٠-٩]"
_SCRUBS = (
    (re.compile(r"eyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]*"), "[token]"),
    (re.compile(r"Bearer\s+[\w.-]+", re.I), "[token]"),
    (re.compile(r"\b(tgWebAppData|init_?data|query_id|auth_date|signature|hash|access_token|token)=[^&#\s\"']*", re.I), r"\1=[masked]"),
    (re.compile(r"(?:https?://)?(?:t|telegram)\.me/[^\s\"'<>)]+|tg://[^\s\"'<>)]+", re.I), "[telegram-link]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"(?<![\w/.@])@[A-Za-z][A-Za-z0-9_]{3,31}"), "[username]"),
    (re.compile(rf"(?<![\w.])(?!\d{{4}}-\d{{2}}-\d{{2}}\b)\+?\(?{_DIGIT}(?:[\s().\-‐-―]{{0,2}}{_DIGIT}){{6,}}(?!\w)"), "[phone]"),
)


def scrub_text(text: str) -> str:
    for pattern, replacement in _SCRUBS:
        text = pattern.sub(replacement, text)
    return text


def scrub(value, depth: int = 0):
    if isinstance(value, str):
        return scrub_text(value[:500])
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if depth > 4:
        return None
    if isinstance(value, dict):
        return {str(key): scrub(item, depth + 1) for key, item in value.items() if not BANNED_KEY.search(str(key))}
    if isinstance(value, (list, tuple, set)):
        return [scrub(item, depth + 1) for item in list(value)[:50]]
    return scrub_text(str(value)[:200])


def distinct_for(telegram_id) -> str:
    """The same id the Mini App identifies with, so one person has one timeline across app and bot."""
    return f"tg_{telegram_id}"


def _key() -> str:
    return os.environ.get("POSTHOG_KEY", "")


def _host() -> str:
    return os.environ.get("POSTHOG_HOST", "").removeprefix("https://").rstrip("/")


def enabled() -> bool:
    return bool(_key() and _host()) and "PYTEST_CURRENT_TEST" not in os.environ


class _Sender:
    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue(QUEUE_MAX)
        self.idle = threading.Condition()
        self.pending = 0
        self.thread: threading.Thread | None = None
        self.pid = 0
        self.connection: http.client.HTTPSConnection | None = None

    def put(self, event: dict) -> None:
        self._ensure_thread()
        try:
            self.events.put_nowait(event)
        except queue.Full:
            return
        with self.idle:
            self.pending += 1

    def _ensure_thread(self) -> None:
        # A forked worker inherits no running thread.
        if self.thread is None or self.pid != os.getpid() or not self.thread.is_alive():
            self.pid = os.getpid()
            self.connection = None
            self.thread = threading.Thread(target=self._run, name="posthog-sender", daemon=True)
            self.thread.start()

    def _run(self) -> None:
        while True:
            batch = [self.events.get()]
            while len(batch) < BATCH_MAX:
                try:
                    batch.append(self.events.get_nowait())
                except queue.Empty:
                    break
            try:
                self._post(batch)
            except Exception:  # noqa: BLE001 - an event is worth less than the thread
                logger.info("posthog_send_failed", exc_info=True)
            with self.idle:
                self.pending = max(0, self.pending - len(batch))
                if self.pending == 0:
                    self.idle.notify_all()

    def _post(self, batch: list[dict]) -> None:
        body = json.dumps({"api_key": _key(), "batch": batch}, ensure_ascii=False, default=str).encode()
        headers = {"Content-Type": "application/json"}
        # Twice: a kept connection is often dead after a freeze.
        for attempt in (1, 2):
            try:
                if self.connection is None:
                    self.connection = http.client.HTTPSConnection(_host(), timeout=SEND_TIMEOUT)
                self.connection.request("POST", "/batch/", body=body, headers=headers)
                response = self.connection.getresponse()
                response.read()
                if response.status >= 400:
                    logger.info("posthog_send_status %s", response.status)
                return
            except (OSError, http.client.HTTPException):
                try:
                    if self.connection is not None:
                        self.connection.close()
                finally:
                    self.connection = None
                if attempt == 2:
                    raise

    def flush(self, seconds: float) -> None:
        with self.idle:
            if self.pending:
                self.idle.wait_for(lambda: self.pending == 0, timeout=seconds)


_sender = _Sender()


def capture(event: str, *, telegram_id=None, source: str = "api", side: str | None = None, properties: dict | None = None, person: dict | None = None, person_once: dict | None = None) -> None:
    """Never raises. With no telegram_id the event is about the system or an unverified caller and makes no person."""
    try:
        if not enabled():
            return
        props = scrub(properties or {})
        props.update({"source": source, "$lib": "chamedoon-server", "$geoip_disable": True, "build_id": os.environ.get("VERCEL_GIT_COMMIT_SHA", os.environ.get("BUILD_ID", "dev"))[:12]})
        if side:
            props["side"] = side
        if telegram_id:
            distinct = distinct_for(telegram_id)
        else:
            distinct = f"system_{source}"
            props["$process_person_profile"] = False
        if person:
            props["$set"] = scrub(person)
        if person_once:
            props["$set_once"] = scrub(person_once)
        _sender.put({"event": event, "distinct_id": distinct, "properties": props, "timestamp": datetime.now(timezone.utc).isoformat(), "uuid": str(uuid.uuid4())})
    except Exception:  # noqa: BLE001 - telemetry never breaks a request
        logger.info("posthog_capture_failed", exc_info=True)


def capture_user(event: str, user, **kwargs) -> None:
    """For a users.User row, or anything with telegram_user_id."""
    try:
        telegram_id = getattr(user, "telegram_user_id", None)
    except Exception:  # noqa: BLE001
        telegram_id = None
    capture(event, telegram_id=telegram_id, **kwargs)


def flush(seconds: float = FLUSH_SECONDS) -> None:
    try:
        if enabled():
            _sender.flush(seconds)
    except Exception:  # noqa: BLE001
        logger.info("posthog_flush_failed", exc_info=True)


atexit.register(lambda: flush(EXIT_FLUSH_SECONDS))
