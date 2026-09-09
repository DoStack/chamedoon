from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone

from ai.openrouter import complete, openrouter_enabled
from item_requests.seed import CATEGORIES
from market.catalog import catalog_location
from market.dates import travel_date_for_post
from market.models import MarketPost, MarketRole
from market.rules import cleaned_kg

logger = logging.getLogger(__name__)

SKIP_AD = "ad"
SKIP_NOISE = "noise"
SKIP_LLM = "llm_error"
SKIP_DEFERRED = "deferred"
DESCRIPTION_MAX = 500
CATEGORY_CODES = {item["code"] for item in CATEGORIES}
_COUNTRY_NAMES = {
    "iran": "IR",
    "ایران": "IR",
    "canada": "CA",
    "کانادا": "CA",
    "united states": "US",
    "usa": "US",
    "america": "US",
    "آمریکا": "US",
    "امریکا": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "انگلیس": "GB",
    "germany": "DE",
    "آلمان": "DE",
    "turkey": "TR",
    "ترکیه": "TR",
    "uae": "AE",
    "dubai": "AE",
    "امارات": "AE",
    "italy": "IT",
    "ایتالیا": "IT",
    "france": "FR",
    "فرانسه": "FR",
}
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_LINKS = re.compile(r"https?://\S+|t\.me/\S+", re.I)

SYSTEM_PROMPT = """You review Telegram courier posts for Koolbar, a C2C send/carry marketplace.
Accept only a real request: DEMAND (someone needs a traveler to carry a package) or SUPPLY (a traveler can carry).
Reject ads, channel/group promo, join links, rules, customs info, pinned posts, taxis, shopping, and anything that is not a real send/carry request.
Do not copy the post. Write a short useful description in the post language. No URLs, no join links, no channel slogans.
Reply with JSON only, no markdown:
{
  "accept": true,
  "reject_reason": "",
  "role": "demand",
  "origin_city": "Toronto",
  "origin_country": "CA",
  "destination_city": "Tehran",
  "destination_country": "IR",
  "date_from": "2026-09-20",
  "date_to": "2026-09-25",
  "desired_date": "2026-09-23",
  "flight_date": "2026-09-20",
  "weight_kg": 10,
  "item_category_codes": ["DOCUMENTS"],
  "excluded_category_codes": [],
  "excluded_other_text": "",
  "description": "Short summary",
  "author_username": ""
}
role must be demand or supply. Dates YYYY-MM-DD. Use empty strings when unknown. reject_reason: ad, noise, incomplete, or expired."""


@dataclass
class ReviewResult:
    accept: bool
    reject_reason: str = ""
    role: str = ""
    origin_city: str = ""
    origin_country: str = ""
    destination_city: str = ""
    destination_country: str = ""
    date_from: str = ""
    date_to: str = ""
    desired_date: str = ""
    flight_date: str = ""
    weight_kg: str = ""
    item_category_codes: list[str] | None = None
    excluded_category_codes: list[str] | None = None
    excluded_other_text: str = ""
    description: str = ""
    author_username: str = ""
    error: str = ""

    def as_json(self) -> dict:
        return {
            "accept": self.accept,
            "reject_reason": self.reject_reason,
            "role": self.role,
            "origin_city": self.origin_city,
            "origin_country": self.origin_country,
            "destination_city": self.destination_city,
            "destination_country": self.destination_country,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "desired_date": self.desired_date,
            "flight_date": self.flight_date,
            "weight_kg": self.weight_kg,
            "item_category_codes": self.item_category_codes or [],
            "excluded_category_codes": self.excluded_category_codes or [],
            "excluded_other_text": self.excluded_other_text,
            "description": self.description,
            "author_username": self.author_username,
            "error": self.error,
        }


def post_text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:24]


def cached_review(post: MarketPost) -> ReviewResult | None:
    raw = post.review_json
    if not isinstance(raw, dict):
        return None
    if raw.get("_text_hash") != post_text_hash(post.text):
        return None
    return _from_stored(raw)


def review_market_post(post: MarketPost, *, force: bool = False) -> ReviewResult:
    if not force:
        cached = cached_review(post)
        if cached is not None:
            return cached
    result = _call_openrouter(post)
    stored = result.as_json()
    stored["_text_hash"] = post_text_hash(post.text)
    post.review_json = stored
    post.reviewed_at = timezone.now()
    if result.accept:
        if result.role == MarketRole.SUPPLY:
            post.role = MarketRole.SUPPLY
        elif result.role == MarketRole.DEMAND:
            post.role = MarketRole.DEMAND
    elif result.reject_reason in {SKIP_AD, SKIP_NOISE}:
        post.role = MarketRole.NOISE
    post.save(update_fields=["review_json", "reviewed_at", "role", "updated_at"])
    return result


def payload_from_review(post: MarketPost, review: ReviewResult) -> tuple[dict | None, str | None]:
    if review.error:
        return None, SKIP_LLM
    if not review.accept:
        return None, (review.reject_reason or SKIP_AD)[:64]
    is_supply = review.role == MarketRole.SUPPLY
    if review.role not in {MarketRole.SUPPLY, MarketRole.DEMAND}:
        return None, "unknown_role"
    origin = _resolve_place(review.origin_city, review.origin_country, post.text, side="origin")
    dest = _resolve_place(review.destination_city, review.destination_country, post.text, side="dest")
    if not origin or not dest:
        return None, "no_catalog_city"
    if origin == dest:
        return None, "same_city"
    today = timezone.now().date()
    dates = _dates_for_review(post, review, today=today, is_supply=is_supply)
    if dates is None:
        return None, "expired"
    kg = _kg(review.weight_kg, post.text)
    carried = _categories(review.item_category_codes) or ["DOCUMENTS"]
    excluded = _categories(review.excluded_category_codes)
    excluded = [code for code in excluded if code not in carried]
    other = (review.excluded_other_text or "").strip()[:255]
    description = _clean_description(review.description, is_supply=is_supply, origin=origin[1], dest=dest[1])
    payload: dict = {
        "type": "SUPPLY" if is_supply else "DEMAND",
        "origin_country": origin[0],
        "origin_city": origin[1],
        "destination_country": dest[0],
        "destination_city": dest[1],
        "item_category_codes": carried,
        "description": description,
    }
    if is_supply:
        payload["capacity_kg"] = kg
        payload["flight_date"] = dates["flight_date"]
        payload["date_from"] = dates["date_from"]
        payload["date_to"] = dates["date_to"]
        payload["excluded_category_codes"] = excluded
        payload["excluded_other_text"] = other
    else:
        payload["weight_kg"] = kg
        payload["desired_date"] = dates["desired_date"]
    return payload, None


def llm_review_limit() -> int:
    try:
        return max(1, int(getattr(settings, "MARKET_LLM_REVIEW_LIMIT", 12) or 12))
    except (TypeError, ValueError):
        return 12


def _call_openrouter(post: MarketPost) -> ReviewResult:
    if not openrouter_enabled():
        return ReviewResult(accept=False, reject_reason=SKIP_LLM, error="OpenRouter is not configured.")
    posted = post.posted_at.date().isoformat() if post.posted_at else ""
    prompt = (
        f"Channel: @{post.channel_username}\n"
        f"Posted at: {posted}\n"
        f"Author: {post.author_username or post.author_name or 'unknown'}\n"
        f"Allowed categories: {', '.join(sorted(CATEGORY_CODES))}\n\n"
        f"Post:\n{post.text[:4000]}"
    )
    result = complete(prompt, system=SYSTEM_PROMPT, temperature=0, max_tokens=700)
    if not result.ok:
        logger.warning("OpenRouter review failed for %s/%s: %s", post.channel_username, post.telegram_message_id, result.error)
        return ReviewResult(accept=False, reject_reason=SKIP_LLM, error=result.error or "llm_error")
    parsed = _parse_json(result.text)
    if parsed is None:
        return ReviewResult(accept=False, reject_reason=SKIP_LLM, error="Invalid model JSON.")
    return _from_stored(parsed)


def _from_stored(raw: dict) -> ReviewResult:
    role = str(raw.get("role") or "").strip().lower()
    if role in {"demand", "supply"}:
        mapped_role = MarketRole.DEMAND if role == "demand" else MarketRole.SUPPLY
    else:
        mapped_role = ""
    accept = bool(raw.get("accept"))
    reject = str(raw.get("reject_reason") or "").strip().lower()[:64]
    if not accept and not reject:
        reject = SKIP_AD
    return ReviewResult(
        accept=accept,
        reject_reason="" if accept else reject,
        role=mapped_role,
        origin_city=str(raw.get("origin_city") or "").strip(),
        origin_country=_country_code(raw.get("origin_country")),
        destination_city=str(raw.get("destination_city") or "").strip(),
        destination_country=_country_code(raw.get("destination_country")),
        date_from=_iso_date(raw.get("date_from")),
        date_to=_iso_date(raw.get("date_to")),
        desired_date=_iso_date(raw.get("desired_date")),
        flight_date=_iso_date(raw.get("flight_date")),
        weight_kg=str(raw.get("weight_kg") or "").strip(),
        item_category_codes=_categories(raw.get("item_category_codes")),
        excluded_category_codes=_categories(raw.get("excluded_category_codes")),
        excluded_other_text=str(raw.get("excluded_other_text") or "").strip()[:255],
        description=str(raw.get("description") or "").strip(),
        author_username=str(raw.get("author_username") or "").strip().lstrip("@")[:32],
        error=str(raw.get("error") or "").strip(),
    )


def _parse_json(text: str) -> dict | None:
    cleaned = _JSON_FENCE.sub("", (text or "").strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        body = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _country_code(value: object) -> str:
    raw = str(value or "").strip()
    if len(raw) == 2 and raw.isalpha():
        return raw.upper()
    return _COUNTRY_NAMES.get(raw.lower(), "")


def _iso_date(value: object) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    raw = str(value or "").strip()[:10]
    if len(raw) != 10:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return ""


def _categories(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    found: list[str] = []
    for item in value:
        code = str(item or "").strip().upper()
        if code in CATEGORY_CODES and code not in found:
            found.append(code)
    return found


def _kg(raw: str, text: str) -> str:
    if raw:
        try:
            amount = Decimal(str(raw))
            if Decimal("0.01") <= amount <= Decimal("50"):
                return str(amount.quantize(Decimal("0.01")))
        except (InvalidOperation, ValueError):
            pass
    return str(cleaned_kg(text))


def _resolve_place(city: str, country: str, text: str, *, side: str) -> tuple[str, str] | None:
    if country and city:
        mapped = catalog_location({"city": city.title() if city.islower() else city, "country": country})
        if mapped:
            return mapped
        if len(country) == 2:
            return country, city
    origin, dest = _route_from_text(text)
    fallback = origin if side == "origin" else dest
    return catalog_location(fallback)


def _route_from_text(text: str) -> tuple[dict | None, dict | None]:
    from market.classify import extract_route

    return extract_route(text)


def _dates_for_review(post: MarketPost, review: ReviewResult, *, today: date, is_supply: bool) -> dict[str, str] | None:
    fallback = travel_date_for_post(post.text, posted_at=post.posted_at, today=today)
    if fallback is None:
        parsed = [value for value in (review.desired_date, review.flight_date, review.date_from, review.date_to) if value]
        if not parsed or date.fromisoformat(min(parsed)) < today:
            return None
        fallback = date.fromisoformat(min(parsed))
    desired = _future_date(review.desired_date, today) or fallback.isoformat()
    flight = _future_date(review.flight_date, today) or desired
    date_from = _future_date(review.date_from, today) or flight
    date_to = _future_date(review.date_to, today) or date_from
    if date.fromisoformat(date_from) > date.fromisoformat(date_to):
        date_to = date_from
    if is_supply:
        return {"date_from": date_from, "date_to": date_to, "flight_date": flight}
    return {"desired_date": desired}


def _future_date(raw: str, today: date) -> str:
    if not raw:
        return ""
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        return ""
    if value < today:
        return ""
    return value.isoformat()


def _clean_description(text: str, *, is_supply: bool, origin: str, dest: str) -> str:
    cleaned = _LINKS.sub("", text or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned:
        return cleaned[:DESCRIPTION_MAX]
    if is_supply:
        return f"Traveler can carry from {origin} to {dest}."[:DESCRIPTION_MAX]
    return f"Needs a traveler from {origin} to {dest}."[:DESCRIPTION_MAX]
