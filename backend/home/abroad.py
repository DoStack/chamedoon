"""Offers for a place outside the source's country (rule 47).

Persian groups in Vancouver or Berlin also carry ads for flats in Dubai, Istanbul or Yerevan. The review used to
put the group's default city on them (#314: a Dubai Creek Harbour flat stored as Vancouver). Such an offer is
rejected before it becomes a listing, and a listing that is already one is never sent in outreach.

An offer is abroad when one of these holds:
- the review gave it a country other than the source's, and not one we run channels for;
- the post names a place abroad (Dubai, Emaar, Istanbul, Yerevan, Tehran ...).

A post whose only phone has another country's code (+971 in a Canadian group, +90 in a German one) is held for an
admin instead (needs_review): a real Hamburg room was posted with an Afghan +93 number.

Wanted posts are not checked: a newcomer still in Tehran with a +98 number looks for a room in Toronto.
"""

from __future__ import annotations

import re

from home.contact_text import FA_DIGITS, LOCAL_PHONE, NANP

HOME_CODES = {"CA": "1", "US": "1", "DE": "49"}

# Written with + or 00, then the country code and the number.
_PREFIXED = re.compile(r"(?<![\w+])(?:\+|00)\s?(\d[\d\s().\-]{8,18}\d)")
_FA = "؀-ۿ"
_PLACE_WORDS = (
    "dubai", "دبی", "دوبی", "emaar", "اعمار", "creek harbour", "sharjah", "شارجه", "abu dhabi", "ابوظبی",
    "istanbul", "استانبول", "اسلامبول", "esenyurt", "اسنیورت", "antalya", "آنتالیا", "alanya", "آلانیا",
    "yerevan", "ایروان", "tbilisi", "تفلیس", "batumi", "باتومی", "erbil", "اربیل", "tehran", "تهران",
)  # fmt: skip
_PLACES = re.compile(rf"(?<![\w{_FA}])({'|'.join(re.escape(word) for word in _PLACE_WORDS)})(?![\w{_FA}])", re.IGNORECASE)


def foreign_places(text: str) -> list[str]:
    return sorted({match.group(1).lower() for match in _PLACES.finditer(text or "")})


def _phones(text: str, country: str) -> tuple[list[str], bool]:
    """Foreign country codes in the text, and whether a phone of the source's country is there too."""
    home = HOME_CODES.get(country, "")
    cleaned = (text or "").translate(FA_DIGITS)
    foreign: list[str] = []
    domestic = False
    for match in _PREFIXED.finditer(cleaned):
        digits = re.sub(r"\D", "", match.group(1))
        if home and digits.startswith(home):
            domestic = True
        elif home:
            foreign.append(digits[:3])
    rest = _PREFIXED.sub(" ", cleaned)
    if country in {"CA", "US"}:
        domestic = domestic or bool(NANP.search(rest))
    elif country == "DE":
        domestic = domestic or bool(LOCAL_PHONE.search(rest))
    return foreign, domestic


def abroad_reason(text: str, source_country: str, listing_country: str = "") -> str:
    """Why an offer from this source is for a place abroad, or "" when nothing says so. Rejects."""
    # A country we run channels for is never abroad: a German flat from a Canadian board still has a home.
    if listing_country and source_country and listing_country != source_country and listing_country not in HOME_CODES:
        return f"country {listing_country}"
    places = foreign_places(text)
    if places:
        return f"place {places[0]}"
    return ""


def foreign_phone(text: str, source_country: str, listing_country: str = "") -> str:
    """The country code when the post's only phone is foreign, else "". Holds for an admin."""
    foreign, domestic = _phones(text, listing_country if listing_country in HOME_CODES else source_country)
    return f"+{foreign[0]}" if foreign and not domestic else ""


def listing_abroad(listing) -> str:
    from home.models import ListingKind

    if listing is None or listing.kind != ListingKind.OFFER or listing.post_id is None:
        return ""
    post = listing.post
    return abroad_reason(post.text, post.source.country, listing.country)
