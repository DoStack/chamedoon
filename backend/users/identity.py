"""Accounts without Telegram (email sign-in) and the one rule that keeps the bots away from them.

`User.telegram_user_id` stays unique and NOT NULL, and 83 modules key on it. An email-only account gets a synthetic id
in [EMAIL_ID_MIN, EMAIL_ID_MAX): negative and above 1e17 in size, so it is never a Telegram user (positive, < 2^52), never
a Telegram chat (channels and groups are negative but under 1e16 in size) and never a market "shadow" (>= 9e12).
Every Bot API client refuses these ids before the network (`refuses`), so nothing is ever sent to one.
"""

from __future__ import annotations

import secrets

EMAIL_ID_MIN = -(2 * 10**17)
EMAIL_ID_MAX = -(10**17)


def is_email_id(value) -> bool:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return False
    return EMAIL_ID_MIN <= number < EMAIL_ID_MAX


def new_email_id() -> int:
    return EMAIL_ID_MAX - 1 - secrets.randbelow(EMAIL_ID_MAX - EMAIL_ID_MIN - 1)


def refuses(payload: dict | None) -> bool:
    """True when a Bot API call would reach an email-only account: such a call is never sent."""
    payload = payload or {}
    return is_email_id(payload.get("chat_id")) or is_email_id(payload.get("user_id"))


def has_telegram(user) -> bool:
    return user is not None and not is_email_id(user.telegram_user_id)
