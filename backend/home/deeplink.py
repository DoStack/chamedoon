"""Start parameters of the housing links: one grammar for the channel posts and the Mini App.

Telegram allows only A-Z, a-z, 0-9, _ and - in a start parameter, at most 64 characters.
Parts are joined by `_`, so no part may contain one; everything is lower case:

    home                                   the housing landing (old links, still work)
    home_<id>                              listing <id> (old links, still work)
    home_<cc>[_<city>][_l<id>][_t-<type>][_p<min>-<max>][_src-<tag>]

- `<cc>`    ISO country code, `ca` or `de`.
- `<city>`  ASCII slug of the metro, letters and digits only: toronto, montreal, vancouver,
            berlin, hamburg, cologne. Suburbs are posted under their metro.
- `l<id>`   open listing <id>.
- `t-<type>` property type with `_` written as `-`, e.g. `t-room`, `t-lease-transfer`.
- `p<min>-<max>` rent band in the listing's currency, whole numbers.
- `src-<tag>` the channel the click came from: `ch` plus the first three letters of the
            city slug for a city channel (`chtor`, `chmon`, `chvan`, `chber`, `chham`,
            `chcol`), or `ch` plus the country code for a country channel (`chca`, `chde`).

The bot's alert link uses `start=alert_home_<cc>[_<city>]` (same slugs).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlencode

START_LIMIT = 64
CITY_SLUG_LIMIT = 24
_COUNTRY = re.compile(r"^[a-z]{2}$")
_CITY = re.compile(r"^[a-z][a-z0-9]{1,23}$")
_LISTING = re.compile(r"^l(\d{1,12})$")
_TYPE = re.compile(r"^t-([a-z]+(?:-[a-z]+)*)$")
_BAND = re.compile(r"^p(\d{1,6})-(\d{1,6})$")
_SOURCE = re.compile(r"^src-([a-z0-9-]{1,24})$")


@dataclass
class HomeStart:
    country: str = ""
    city: str = ""
    listing: int | None = None
    type: str = ""
    min_price: int | None = None
    max_price: int | None = None
    source: str = ""


def slug(name: str) -> str:
    """ASCII letters and digits of a city name, lower case: "Köln" → "koln", "North York" → "northyork"."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", text.lower())[:CITY_SLUG_LIMIT]


def source_tag(country: str, city: str = "") -> str:
    """Tag of a channel: `ch` + three letters of its city, or `ch` + the country code."""
    return f"ch{slug(city)[:3]}" if slug(city) else f"ch{country.lower()}"


def build(
    country: str,
    city: str = "",
    *,
    listing: int | None = None,
    type: str = "",
    band: tuple[int, int] | None = None,
    source: str = "",
) -> str:
    """A start parameter in the grammar above. The source, then the band, are dropped if it would be too long."""
    parts = ["home", country.lower()]
    city_slug = slug(city)
    if _CITY.match(city_slug):
        parts.append(city_slug)
    if listing:
        parts.append(f"l{int(listing)}")
    if type:
        parts.append("t-" + type.replace("_", "-"))
    optional = []
    if band:
        optional.append(f"p{int(band[0])}-{int(band[1])}")
    if source:
        optional.append(f"src-{source}")
    while optional and len("_".join(parts + optional)) > START_LIMIT:
        optional.pop()
    return "_".join(parts + optional)[:START_LIMIT]


def parse(value: str | None) -> HomeStart | None:
    """The parts of a `home_<cc>…` start parameter, or None when it is not one (old forms included)."""
    tokens = (value or "").strip().lower().split("_")
    if len(tokens) < 2 or tokens[0] != "home" or not _COUNTRY.match(tokens[1]):
        return None
    start = HomeStart(country=tokens[1].upper())
    for index, token in enumerate(tokens[2:]):
        if match := _LISTING.match(token):
            start.listing = int(match.group(1))
        elif match := _TYPE.match(token):
            start.type = match.group(1).replace("-", "_")
        elif match := _BAND.match(token):
            low, high = int(match.group(1)), int(match.group(2))
            start.min_price, start.max_price = (low or None), (high if high >= low else None)
        elif match := _SOURCE.match(token):
            start.source = match.group(1)
        elif index == 0 and _CITY.match(token):
            start.city = token
        else:
            return None
    return start


def path(start: HomeStart) -> str:
    """Where the Mini App opens for a parsed start parameter: the listing, or the feed with its filters."""
    if start.listing:
        return f"/app/home/{start.listing}/"
    pairs = [("country", start.country)]
    if start.city:
        pairs.append(("city", start.city))
    if start.type:
        pairs.append(("type", start.type))
    if start.min_price:
        pairs.append(("min", start.min_price))
    if start.max_price:
        pairs.append(("max", start.max_price))
    return "/app/home/list/?" + urlencode(pairs)


def alert_start(country: str, city: str = "") -> str:
    """Bot start parameter that asks for alerts on a city: `alert_home_<cc>[_<city>]`."""
    city_slug = slug(city)
    return f"alert_home_{country.lower()}" + (f"_{city_slug}" if _CITY.match(city_slug) else "")
