from __future__ import annotations

OPT_OUT_WORD = "لغو"
OPT_OUT_EXACT = {OPT_OUT_WORD, "stop", "unsubscribe", "cancel"}
OPT_OUT_CONFIRMATION = "باشه 🙏 دیگه از طرف چمدون پیامی براتون نمی‌فرستیم."


def normalize(text: str | None) -> str:
    return (text or "").replace("ي", "ی").replace("ك", "ک").replace("‌", " ").strip().lower()


def is_opt_out(text: str | None) -> bool:
    normalized = normalize(text)
    return OPT_OUT_WORD in normalized or normalized.strip(" .!?؟") in OPT_OUT_EXACT
