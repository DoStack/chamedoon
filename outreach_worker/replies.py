from __future__ import annotations

# The operator chats with recipients personally, so nothing is answered automatically.
# Only a message that is exactly one of these words is recorded as an opt-out; a sentence
# like «پروازم لغو شد» (my flight was cancelled) is an ordinary reply.
OPT_OUT_EXACT = {"لغو", "stop", "unsubscribe"}


def normalize(text: str | None) -> str:
    return (text or "").replace("ي", "ی").replace("ك", "ک").replace("‌", " ").strip().lower()


def is_opt_out(text: str | None) -> bool:
    return normalize(text).strip(" .!?؟") in OPT_OUT_EXACT
