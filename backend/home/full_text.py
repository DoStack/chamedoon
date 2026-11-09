"""The post's own words for the public: contact cut out and replaced, then tidied for reading and for search.

The raw `HomePost.text` never leaves the server. Anything that could reach the advertiser (phone in any digit set or
spacing, usernames, links, emails) becomes MASK, so the join gate in `gate.py` stays the only way to a contact.
"""

from __future__ import annotations

import re

from home.contact_text import FA_DIGITS, _spans

MASK = "(در بخش تماس)"
FULL_TEXT_LIMIT = 4000
FIND_LIMIT = 160

# One char for one: fullwidth @ and Arabic-Indic or Persian digits fold to ASCII without moving any offset.
_FOLD = {**FA_DIGITS, ord("＠"): "@", 0x200F: " ", 0x200E: " ", 0x200C: " "}
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_ANY_AT = re.compile(r"@[\w.]{2,}")
# Nine or more digits with single separators between: a phone however it is spaced. Seven and eight when run together.
_DIGITS = re.compile(r"(?<![\d])\+?\d(?:[\s().\-/]?\d){8,}(?!\d)|(?<!\d)\d{7,8}(?!\d)")

_EMOJI = r"[\U0001F000-\U0001FAFF☀-➿⭐⭕]"
_EMOJI_RUN = re.compile(rf"({_EMOJI}[️‍]*)(?:\s?\1)+")
_HASHTAGS_ONLY = re.compile(r"^[\s\W_]*(?:#[\w؀-ۿ]+[\s\W_]*)+$")
_SYMBOLS_ONLY = re.compile(r"^[\W_]{3,}$")


def _fold(text: str) -> str:
    return text.translate(_FOLD)


def mask_contact(text: str, known: tuple[str, ...] = ()) -> str:
    """The text with every way to reach someone replaced by MASK."""
    text = text or ""
    folded = _fold(text)
    spans = list(_spans(folded, known))
    for pattern in (_EMAIL, _ANY_AT, _DIGITS):
        spans += [match.span() for match in pattern.finditer(folded)]
    if not spans:
        return text
    out: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        if end <= cursor:
            continue
        if start > cursor:
            out.append(text[cursor:start])
        if not out or not out[-1].endswith(MASK):
            out.append(MASK)
        cursor = end
    out.append(text[cursor:])
    # A mask next to a mask (number, then username) is one mask.
    return re.sub(rf"(?:{re.escape(MASK)}[\s,،:/\-]*)+(?={re.escape(MASK)})", "", "".join(out))


def tidy(text: str) -> str:
    """Repeated emojis once, no hashtag-only or symbol-only lines, blank lines collapsed, ends trimmed."""
    lines = []
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _EMOJI_RUN.sub(r"\1", line)
        line = re.sub(r"[ \t ]+", " ", line).strip()
        if line and (_HASHTAGS_ONLY.match(line) or _SYMBOLS_ONLY.match(line)):
            line = ""
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


def full_text(text: str, known: tuple[str, ...] = ()) -> str:
    """What the detail screen shows under “متن کامل”."""
    out = tidy(mask_contact(text, known))
    return out[:FULL_TEXT_LIMIT].rstrip()


def find_text(text: str, known: tuple[str, ...] = ()) -> str:
    """The short single line a card carries so the snapshot search can see past the summary."""
    out = re.sub(r"\s+", " ", full_text(text, known).replace(MASK, " ")).strip()
    return out[:FIND_LIMIT]
