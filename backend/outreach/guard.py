"""The last check before any text goes to a person (Mohammad, 2026-11-06, after the audit of real chats).

Every outbound text passes `check`: Persian only, no emoji, every link written once as a plain URL, never the same text
twice in one chat, and never a promise to «tell the team». A text that fails is not sent.
"""

from __future__ import annotations

import re

from outreach.templates import no_emoji

PERSIAN = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")
URL = re.compile(r"https?://[^\s)\]]+")
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
# A promise to pass something on is never sent: the agent does it itself (Mohammad, 2026-11-06).
DEFERRAL = re.compile(r"به\s*تیم")


class Blocked(Exception):
    """The text must not go out. `reason` says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def plain_links(text: str) -> str:
    """`[label](url)` becomes the URL alone, so a link shows once and nothing is hidden behind a label."""
    return MARKDOWN_LINK.sub(lambda match: match.group(2), text)


def _same(a: str, b: str) -> bool:
    return re.sub(r"\s+", "", a or "") == re.sub(r"\s+", "", b or "")


# A count under 3 is never written (Mohammad's rule): «۲ نفر», «1 بازدید», «۰ تا آگهی». Bedrooms («۱ خوابه») are facts, not counts.
SMALL_COUNT = re.compile(r"(?<![\d۰-۹٠-٩.,/])[0-2۰-۲٠-٢]\s*(?:نفر|بازدید|بار(?=\s|$)|تا(?=\s|$)|درخواست|پیام|آگهی)")


def check(text: str, *, earlier: list[str] | None = None) -> str:
    """The text as it may go out, or `Blocked`. `earlier` are the texts already sent in this chat."""
    cleaned = no_emoji(plain_links(text or ""))
    if not cleaned.strip():
        raise Blocked("empty")
    words = URL.sub("", cleaned)
    if not PERSIAN.search(words) or len(LATIN.findall(words)) > len(PERSIAN.findall(words)):
        raise Blocked("not_persian")
    urls = URL.findall(cleaned)
    if len(urls) != len(set(urls)):
        raise Blocked("url_twice")
    if DEFERRAL.search(words):
        raise Blocked("deferral")
    if SMALL_COUNT.search(words):
        raise Blocked("small_number")
    if any(_same(cleaned, before) for before in (earlier or [])):
        raise Blocked("duplicate")
    return _new_lines(cleaned, earlier or [])


def _key(line: str) -> str:
    return re.sub(r"\s+", "", line or "")


def _new_lines(text: str, earlier: list[str]) -> str:
    """A line already sent in this chat is not sent again, whitespace ignored (audit 2026-11-11 12:15, conv 31 T196 was
    T194's third line word for word). A line with a link may repeat: they may ask for it again. Nothing new left is
    `Blocked("duplicate")`."""
    said = {_key(line) for before in earlier for line in (before or "").split("\n") if _key(line)}
    kept = [line for line in text.split("\n") if not _key(line) or URL.search(line) or _key(line) not in said]
    if not any(_key(line) and not URL.search(line) for line in kept) and any(_key(line) in said for line in text.split("\n")):
        raise Blocked("duplicate")
    return "\n".join(kept).strip()
