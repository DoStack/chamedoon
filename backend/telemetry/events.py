"""What the bots and the jobs need around `capture`: reading an incoming update into one event, and a few guards.

Nothing here raises, and nothing here reads more than the update it is given. An update's text never leaves: a message
becomes its type and length, a button becomes its code, a /start payload becomes its shape.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import telemetry

logger = logging.getLogger(__name__)

HOUSING = {"source": "bot_housing", "side": "housing"}
COURIER = {"source": "bot_courier", "side": "courier"}

# Every command either bot answers. Anything else a person types after a slash could be anything.
_COMMANDS = {"start", "alerts", "myalerts", "search", "saved", "lang", "help", "stop", "forget", "cancel", "send", "carry", "mine", "requests"}
_COMMAND = re.compile(r"^/([A-Za-z_]+)(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$", re.DOTALL)
_SEGMENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}$")
_PAYLOAD_KINDS = {"courier", "request", "match", "listing", "ref", "app"}
_MESSAGE_TYPES = ("photo", "document", "location", "contact", "voice", "video", "sticker")
_SYNTHETIC_FROM = 9_000_000_000_000  # matching.contact.SYNTHETIC_TELEGRAM_USER_ID: imported people, no Telegram account behind them.


def real_telegram_id(user):
    """The Telegram id of a person who exists on Telegram. Imported courier people get None, or they would become profiles."""
    value = getattr(user, "telegram_user_id", None)
    return value if isinstance(value, int) and 0 < value < _SYNTHETIC_FROM else None


def is_new_user(user, seconds: int = 30) -> bool:
    """An upsert hides whether it created the row, and asking first would be one more query on every sign-in."""
    created_at = getattr(user, "created_at", None)
    return created_at is not None and (datetime.now(timezone.utc) - created_at).total_seconds() < seconds


def _private_chat(chat_id):
    return chat_id if isinstance(chat_id, int) and 0 < chat_id < _SYNTHETIC_FROM else None


def callback_prefix(data) -> str:
    """The first two segments of callback data while they are plain words: `hx:listing:123` gives `hx:listing`."""
    words: list[str] = []
    for segment in str(data or "").split(":")[:2]:
        if not _SEGMENT.match(segment):
            break
        words.append(segment)
    return ":".join(words) or "other"


def payload_kind(payload: str) -> str:
    """The shape of a /start payload, from a closed list. The payload itself can hold a token or an id."""
    value = (payload or "").strip().lower()
    if not value:
        return "none"
    if value.startswith("alert_home"):
        return "alert_home"
    if value == "home" or value.startswith("home_"):
        return "home_listing" if re.search(r"_l?\d+(?:_|$)", value) else "home"
    head = re.split(r"[_-]", value, maxsplit=1)[0]
    return head if head in _PAYLOAD_KINDS else "other"


def _housing_buttons() -> tuple[dict[str, str], tuple[str, ...]]:
    from home.botchat import KB_ALERTS, KB_CITY_WORD, KB_COURIER, KB_HELP, KB_PICK_CITY, KB_POST, KB_SEARCH
    from home.botv2 import KB

    codes = {KB_SEARCH: "search", KB_ALERTS: "alerts", KB_POST: "post", KB_COURIER: "courier", KB_HELP: "help", KB_PICK_CITY: "pick"}
    for words in KB.values():
        codes.update({words[name]: name for name in ("search", "alerts", "post", "saved", "pick", "help")})
    # The city button carries the city's name, so it is told by the words around the name, as the bot does.
    return codes, (KB_CITY_WORD, *(words["city"] for words in KB.values()))


def _courier_buttons() -> tuple[dict[str, str], tuple[str, ...]]:
    from courier.texts import KB_ALERTS, KB_DEMAND, KB_HELP, KB_MINE, KB_SUPPLY

    return {KB_DEMAND: "demand", KB_SUPPLY: "supply", KB_ALERTS: "alerts", KB_MINE: "mine", KB_HELP: "help"}, ()


def keyboard_code(text: str, source: str) -> str | None:
    """The stable code of a reply-keyboard tap, which arrives as plain text. None when the text is not a button."""
    codes, city_words = _housing_buttons() if source == HOUSING["source"] else _courier_buttons()
    if text in codes:
        return codes[text]
    return "city" if any(word in text for word in city_words) else None


def message_type(message: dict) -> str:
    if message.get("forward_origin") or message.get("forward_from") or message.get("forward_from_chat") or message.get("forward_date"):
        return "forward"
    if message.get("text"):
        return "text"
    return next((name for name in _MESSAGE_TYPES if message.get(name)), "other")


def _message_event(message: dict, source: str) -> tuple[str, dict]:
    text = (message.get("text") or "").strip()
    command = _COMMAND.match(text)
    if command:
        name = command.group(1).lower()
        argument = (command.group(2) or "").strip()
        props = {"command": name if name in _COMMANDS else "other", "has_payload": bool(argument)}
        if name == "start":
            props["payload_kind"] = payload_kind(argument)
        return "bot_command", props
    code = keyboard_code(text, source) if text else None
    if code:
        return "bot_button", {"kind": "keyboard", "code": code}
    return "bot_message", {"message_type": message_type(message), "text_length": len(text or message.get("caption") or "")}


def _member_event(member: dict) -> tuple[str, dict]:
    new = ((member.get("new_chat_member") or {}).get("status")) or ""
    old = ((member.get("old_chat_member") or {}).get("status")) or ""
    action = "blocked" if new == "kicked" else "unblocked" if old == "kicked" else "other"
    return "bot_member", {"action": action, "new_status": new[:16], "old_status": old[:16]}


def _describe(update: dict, source: str) -> tuple[str, object, dict]:
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        return "bot_button", (callback.get("from") or {}).get("id"), {"kind": "inline", "prefix": callback_prefix(callback.get("data"))}
    message = update.get("message")
    if isinstance(message, dict):
        event, props = _message_event(message, source)
        chat_type = str((message.get("chat") or {}).get("type") or "")[:16]
        return event, (message.get("from") or {}).get("id"), {**props, "chat_type": chat_type}
    member = update.get("my_chat_member")
    if isinstance(member, dict):
        event, props = _member_event(member)
        return event, (member.get("from") or {}).get("id"), props
    kinds = [key for key in update if key != "update_id"]
    return "bot_update_other", None, {"update_type": str(kinds[0])[:32] if kinds else "none"}


def capture_update(update, bot: dict) -> None:
    """One event per incoming update. A failure here is swallowed, so it can never change a webhook's answer."""
    try:
        if not telemetry.enabled() or not isinstance(update, dict):
            return
        event, telegram_id, properties = _describe(update, bot["source"])
        telemetry.capture(event, telegram_id=telegram_id, properties=properties, **bot)
    except Exception:  # noqa: BLE001 - telemetry never breaks a webhook
        logger.info("posthog_update_failed", exc_info=True)


def capture_send_failure(method: str, chat_id, code, bot: dict) -> None:
    """A message that did not go out. Only the code: Telegram's description can quote the text. An edit falls back to a send, so it is not counted."""
    try:
        if not str(method).startswith("send") or method == "sendChatAction":
            return
        telemetry.capture("bot_send_failed", telegram_id=_private_chat(chat_id), properties={"method": method, "error_code": code if isinstance(code, int) else 0}, **bot)
    except Exception:  # noqa: BLE001
        logger.info("posthog_send_failure_failed", exc_info=True)


def person_of_listing(listing):
    """The poster of a listing posted in the app. A crawled post's author never came to us: None, and no per-post event."""
    try:
        if not telemetry.enabled():
            return None
        post = listing.post
        return post.author_id if post.source.username.startswith("app_") else None
    except Exception:  # noqa: BLE001
        return None


_said: set = set()
_said_day = None


def capture_once_a_day(event: str, telegram_id, *, properties: dict, bot: dict) -> None:
    """For a state a run sees again and again, such as a match held by quiet hours: every 10 minutes all night would burn
    the free tier. One event per person, event and UTC day in this process. A cold start may repeat one, and that is fine."""
    global _said, _said_day
    try:
        if not telemetry.enabled():
            return
        today = datetime.now(timezone.utc).date()
        if today != _said_day:
            _said, _said_day = set(), today
        key = (event, telegram_id)
        if key in _said:
            return
        _said.add(key)
        telemetry.capture(event, telegram_id=telegram_id, properties=properties, **bot)
    except Exception:  # noqa: BLE001
        logger.info("posthog_once_failed", exc_info=True)
