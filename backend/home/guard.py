"""The one place that knows where the courier (چمدون باربری) channel lives.

Housing must never post there. Every housing channel is checked against these names
before it is saved and again before anything is sent.
"""

from __future__ import annotations

from django.conf import settings


def courier_channel_names() -> set[str]:
    """Lower-case usernames and ids of the courier channel and group, without @."""
    found: set[str] = set()
    for name in ("TELEGRAM_CHANNEL_USERNAME", "TELEGRAM_CHANNEL_ID", "TELEGRAM_GROUP_USERNAME"):
        value = str(getattr(settings, name, "") or "").strip().lstrip("@").lower()
        if value:
            found.add(value)
            if value.lstrip("-").isdigit():
                found.add(value.lstrip("-").removeprefix("100"))
    return found


def is_courier_channel(username: str = "", chat_id: int | str | None = None) -> bool:
    names = courier_channel_names()
    if username and username.strip().lstrip("@").lower() in names:
        return True
    if chat_id:
        digits = str(chat_id).strip().lstrip("-")
        return digits in names or digits.removeprefix("100") in names
    return False
