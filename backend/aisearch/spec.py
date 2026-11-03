"""SearchSpec: what a text search means, in the fields of the list screen's filters.

The names are the URL keys of `frontend/src/lib/listing-view.ts`, so the client applies a spec with its own `matches`.
`to_alert` gives the same filters as `HomeAlert.search` for the alert matcher (ADR-8). Size has no list filter yet, so
the search screen applies it on its own and an alert leaves it out.

`from_model` is the only door for model output: unknown keys, wrong types and values outside the choices are dropped,
never trusted. `q` and labels never come from the model, so a reply cannot put text on the screen.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from home.search import COUNTRY_TOGGLES, GENDERS, TERMS, TOGGLES

TYPES = ("room", "studio", "basement", "apartment", "house", "bed1", "bed2", "bed3")
MAX_PRICE = 100_000
MAX_SIZE = 2_000
MAX_AHEAD_DAYS = 400
MAX_NAMES = 6


@dataclass
class SearchSpec:
    kind: str = "offer"
    types: list[str] = field(default_factory=list)
    min: int | None = None
    max: int | None = None
    size_min: int | None = None
    size_max: int | None = None
    area_ids: list[int] = field(default_factory=list)
    term: str = ""
    furnished: bool = False
    bills: bool = False
    owner: bool = False
    warm: bool = False
    reg: bool = False
    nowbs: bool = False
    gender: str = ""
    move: str = ""
    by: str = ""
    q: str = ""

    def is_empty(self) -> bool:
        return self == SearchSpec()

    def to_dict(self) -> dict:
        return asdict(self)

    def to_alert(self, area_names: list[str] | None = None) -> dict:
        """The `HomeAlert.search` of these filters, read by `home.search.fits`. Only what is set, as `to_search` writes it."""
        out: dict = {}
        for key in ("types", "min", "max", "term", "q", "gender", "move", "by", *TOGGLES):
            value = getattr(self, key)
            if value:
                out[key] = value
        if area_names:
            out["areas"] = area_names
        return out


def clean_int(value: object, top: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if 0 < number <= top else None


def clean_date(value: object, today: date) -> str:
    """A date from today to a little over a year ahead. Anything else means nothing."""
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return ""
    return parsed.isoformat() if today <= parsed <= today + timedelta(days=MAX_AHEAD_DAYS) else ""


def toggle_allowed(name: str, country: str) -> bool:
    return country in COUNTRY_TOGGLES.get(name, {country})


@dataclass
class ModelReply:
    spec: SearchSpec
    areas: list[str]
    ignored: list[str]


def from_model(data: object, *, country: str, today: date, text: str) -> ModelReply | None:
    """The model's JSON as a spec. None when it is not an object. `ignored` keeps only pieces of what the person typed."""
    if not isinstance(data, dict):
        return None
    spec = SearchSpec()
    if data.get("kind") == "wanted":
        spec.kind = "wanted"
    raw_types = data.get("types")
    if isinstance(raw_types, list):
        spec.types = [value for value in dict.fromkeys(raw_types) if value in TYPES]
    spec.min = clean_int(data.get("min"), MAX_PRICE)
    spec.max = clean_int(data.get("max"), MAX_PRICE)
    if spec.min and spec.max and spec.min > spec.max:
        spec.min, spec.max = spec.max, spec.min
    spec.size_min = clean_int(data.get("size_min"), MAX_SIZE)
    spec.size_max = clean_int(data.get("size_max"), MAX_SIZE)
    if spec.size_min and spec.size_max and spec.size_min > spec.size_max:
        spec.size_min, spec.size_max = spec.size_max, spec.size_min
    spec.term = data.get("term") if data.get("term") in TERMS else ""
    for name in TOGGLES:
        setattr(spec, name, data.get(name) is True and toggle_allowed(name, country))
    spec.gender = data.get("gender") if data.get("gender") in GENDERS else ""
    spec.move = clean_date(data.get("move"), today)
    spec.by = clean_date(data.get("by"), today)
    raw_areas = data.get("areas")
    areas = [value.strip() for value in raw_areas if isinstance(value, str) and value.strip()][:MAX_NAMES] if isinstance(raw_areas, list) else []
    raw_ignored = data.get("ignored")
    ignored = [value.strip() for value in raw_ignored if isinstance(value, str) and value.strip() and value.strip() in text][:MAX_NAMES] if isinstance(raw_ignored, list) else []
    return ModelReply(spec, areas, ignored)
