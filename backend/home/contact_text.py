"""Finds a way to reach someone inside free text, so public text can drop it.

Used when a summary is stored and again when public text goes out. A phone or link is plain to spot. A bare
Telegram handle looks like any Latin word, so it only goes when one of these holds:

- it is a handle we already know for this listing;
- it has the shape only a username has: an underscore, or letters ending in two or more digits;
- it follows a word that announces a handle («آیدی», «تلگرام», id, whatsapp, ig, snap).

Plain Latin words stay: Yonge, North York, Neukölln, Apt 302, 2BR, M5V 2T6.
"""

from __future__ import annotations

import re

from market.poster import USERNAME

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
HANDLE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")
NANP = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?([2-9]\d{2})[\s.\-]?(\d{4})(?!\d)")
INTL = re.compile(r"(?<!\d)\+\d[\d\s().\-]{8,16}\d")
# German numbers written the local way: 0176 1234567, 030/1234567. The North American pattern misses them.
LOCAL_PHONE = re.compile(r"(?<![\d+])0\d{2,4}[\s/.\-]?\d[\d\s/.\-]{4,12}\d")
LINK = re.compile(
    r"\b(?:https?|tg)://\S+"
    r"|(?<![\w.])(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog|wa\.me|whatsapp\.com|chat\.whatsapp\.com|api\.whatsapp\.com|instagram\.com|signal\.me)(?:/\S*)?",
    re.IGNORECASE,
)
# An underscore between letters or digits: ca_user1, sara_home. Only a username looks like this.
_UNDERSCORED = re.compile(r"(?<![\w@])[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![\w])")
# Letters then two or more digits: sara123. The prefix check below keeps Apt302 and Unit12.
_LETTERS_DIGITS = re.compile(r"(?<![\w@])([A-Za-z]{3,})\d{2,}(?![\w])")
_NOT_A_HANDLE = {
    "apt", "apartment", "unit", "suite", "room", "floor", "bldg", "block", "haus", "zimmer", "wohnung", "tel", "studio",
    "cad", "usd", "eur", "gbp", "euro", "dollar",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}  # fmt: skip
# The word that announces a handle, then the handle. Only ASCII handle-like tokens of 5 or more characters.
_ANNOUNCED = re.compile(
    r"(?:آیدی|ایدی|تلگرام|واتساپ|واتس‌اپ|پی‌وی|پیوی|\b(?:id|telegram|tg|whatsapp|pv|contact|ig|insta|instagram|snap|snapchat)\b)"
    r"\s*[:：\-]?\s*([A-Za-z][A-Za-z0-9_.]{4,31})(?![\w])",
    re.IGNORECASE,
)


def _spans(ascii_text: str, known: tuple[str, ...]) -> list[tuple[int, int]]:
    spans = [
        match.span()
        for pattern in (LINK, HANDLE, NANP, INTL, LOCAL_PHONE, _UNDERSCORED)
        for match in pattern.finditer(ascii_text)
    ]
    spans += [match.span() for match in _LETTERS_DIGITS.finditer(ascii_text) if match.group(1).casefold() not in _NOT_A_HANDLE]
    spans += [match.span(1) for match in _ANNOUNCED.finditer(ascii_text)]
    for handle in known:
        handle = (handle or "").strip().lstrip("@")
        if len(handle) >= 3:
            spans += [match.span() for match in re.finditer(rf"(?<![\w@]){re.escape(handle)}(?!\w)", ascii_text, re.IGNORECASE)]
    return spans


def redact_contact(text: str, known: tuple[str, ...] = ()) -> str:
    """The text without links, handles or phone numbers. Other digits keep their script."""
    text = text or ""
    spans = _spans(text.translate(FA_DIGITS), known)
    if not spans:
        return text.strip()
    keep = []
    cursor = 0
    for start, end in sorted(spans):
        if start > cursor:
            keep.append(text[cursor:start])
        cursor = max(cursor, end)
    keep.append(text[cursor:])
    return re.sub(r"\s+", " ", "".join(keep)).strip()


# Words a model writes for "no contact". A real username is never one of them.
_NO_HANDLE = {"null", "none", "unknown", "empty", "undefined", "telegram", "username"}


def handle_in_text(handle: str, text: str) -> bool:
    """True when this username is written in the post, as @name, a t.me link or a bare word. Part of an email address is not."""
    name = (handle or "").strip().lstrip("@")
    if not USERNAME.fullmatch(name) or name.casefold() in _NO_HANDLE:
        return False
    escaped = re.escape(name)
    # Before @ a Persian letter is a word glued on («تماس@ali»), a Latin one is an address. After it, a domain ending is an address too.
    return bool(re.search(rf"(?<![\w@＠.\-]){escaped}(?!\w)|(?<![^\W\u0600-\u06FF])(?<![.\-])[@＠]{escaped}(?!\w|\.[A-Za-z]{{2,}})", text or "", re.IGNORECASE))


def has_contact(text: str, known: tuple[str, ...] = ()) -> bool:
    return bool(_spans((text or "").translate(FA_DIGITS), known))


def known_handles(listing) -> tuple[str, ...]:
    return (listing.contact_telegram, listing.post.author_username, listing.post.poster_username)


def clean_place(text: str, known: tuple[str, ...] = ()) -> str:
    """A place name goes out whole or not at all: what is left after cutting a contact out of it is not a place."""
    return "" if has_contact(text, known) else (text or "")


def safe_area(listing) -> str:
    """Read this, never `listing.area`, for anything a person will see. Old rows were stored before the cleaning."""
    return clean_place(listing.area, known_handles(listing))


def safe_city(listing) -> str:
    return clean_place(listing.city, known_handles(listing))


def safe_summary(listing, text: str) -> str:
    return redact_contact(text, known_handles(listing))
