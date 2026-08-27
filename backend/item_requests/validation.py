from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from item_requests.models import Category, City, ItemRequest, RequestStatus, RequestType

MAX_KG = Decimal("50.00")
MIN_KG = Decimal("0.01")


def validate_request_payload(payload: dict, *, partial: bool = False, instance: ItemRequest | None = None) -> dict:
    request_type = _require_type(payload, partial=partial, instance=instance)
    origin_country, origin_city = _require_location(
        payload,
        country_key="origin_country",
        city_key="origin_city",
        partial=partial,
        instance=instance,
        instance_country=instance.origin_country if instance else None,
        instance_city=instance.origin_city if instance else None,
    )
    destination_country, destination_city = _require_location(
        payload,
        country_key="destination_country",
        city_key="destination_city",
        partial=partial,
        instance=instance,
        instance_country=instance.destination_country if instance else None,
        instance_city=instance.destination_city if instance else None,
    )
    if origin_country == destination_country and origin_city == destination_city:
        raise ValidationError({"destination_city": "Destination must be different from origin."})

    date_from, date_to = _require_dates(payload, partial=partial, instance=instance)
    description = _optional_text(payload, "description", instance=instance, partial=partial)
    excluded_other_text = _optional_text(payload, "excluded_other_text", instance=instance, partial=partial)

    item_categories = _resolve_categories(
        payload.get("item_category_codes"),
        field="item_category_codes",
        partial=partial,
        instance=instance,
        instance_accessor="item_categories",
    )
    excluded_categories = _resolve_categories(
        payload.get("excluded_category_codes"),
        field="excluded_category_codes",
        partial=partial,
        instance=instance,
        instance_accessor="excluded_categories",
        allow_empty=True,
    )

    cleaned = {
        "type": request_type,
        "origin_country": origin_country,
        "origin_city": origin_city,
        "destination_country": destination_country,
        "destination_city": destination_city,
        "date_from": date_from,
        "date_to": date_to,
        "description": description,
        "excluded_other_text": excluded_other_text,
        "item_categories": item_categories,
        "excluded_categories": excluded_categories,
        "expires_at": _expires_at(date_to),
    }

    if request_type == RequestType.DEMAND:
        if "capacity_kg" in payload and payload["capacity_kg"] not in (None, ""):
            raise ValidationError({"capacity_kg": "Demand requests cannot include capacity_kg."})
        if "excluded_category_codes" in payload and payload["excluded_category_codes"]:
            raise ValidationError({"excluded_category_codes": "Demand requests cannot include exclusions."})
        if payload.get("excluded_other_text"):
            raise ValidationError({"excluded_other_text": "Demand requests cannot include exclusions."})
        cleaned["weight_kg"] = _require_kg(payload, "weight_kg", partial=partial, instance=instance)
        cleaned["capacity_kg"] = None
        cleaned["excluded_categories"] = []
        cleaned["excluded_other_text"] = ""
        if not item_categories:
            raise ValidationError({"item_category_codes": "Demand requests need at least one category."})
    else:
        if "weight_kg" in payload and payload["weight_kg"] not in (None, ""):
            raise ValidationError({"weight_kg": "Supply requests cannot include weight_kg."})
        cleaned["capacity_kg"] = _require_kg(payload, "capacity_kg", partial=partial, instance=instance)
        cleaned["weight_kg"] = None
        overlap = {category.code for category in item_categories} & {category.code for category in excluded_categories}
        if overlap:
            raise ValidationError(
                {"excluded_category_codes": "A category cannot be both carried and excluded."}
            )
        if not item_categories:
            raise ValidationError({"item_category_codes": "Select at least one category you can carry."})

    return cleaned


def _require_type(payload: dict, *, partial: bool, instance: ItemRequest | None) -> str:
    if "type" in payload:
        value = str(payload.get("type") or "").upper()
        if value not in RequestType.values:
            raise ValidationError({"type": "type must be DEMAND or SUPPLY."})
        if instance and instance.type != value:
            raise ValidationError({"type": "Request type cannot be changed."})
        return value
    if partial and instance:
        return instance.type
    raise ValidationError({"type": "This field is required."})


def _require_location(
    payload: dict,
    *,
    country_key: str,
    city_key: str,
    partial: bool,
    instance: ItemRequest | None,
    instance_country: str | None,
    instance_city: str | None,
) -> tuple[str, str]:
    if country_key not in payload and city_key not in payload and partial and instance:
        return instance_country or "", instance_city or ""
    country_code = str(payload.get(country_key) or instance_country or "").upper().strip()
    city_slug = str(payload.get(city_key) or instance_city or "").lower().strip()
    if not country_code or not city_slug:
        raise ValidationError({city_key: "Country and city are required."})
    city = (
        City.objects.select_related("country")
        .filter(slug=city_slug, country__code=country_code, is_active=True, country__is_active=True)
        .first()
    )
    if city is None:
        raise ValidationError({city_key: "Unknown city for this country."})
    return city.country.code, city.slug


def _require_dates(payload: dict, *, partial: bool, instance: ItemRequest | None) -> tuple[date, date]:
    date_from = _parse_date(payload.get("date_from"), "date_from") if "date_from" in payload else None
    date_to = _parse_date(payload.get("date_to"), "date_to") if "date_to" in payload else None
    if date_from is None:
        if partial and instance:
            date_from = instance.date_from
        else:
            raise ValidationError({"date_from": "This field is required."})
    if date_to is None:
        if partial and instance:
            date_to = instance.date_to
        else:
            raise ValidationError({"date_to": "This field is required."})
    if date_from > date_to:
        raise ValidationError({"date_to": "date_to must be on or after date_from."})
    return date_from, date_to


def _parse_date(value: object, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        raise ValidationError({field: "This field is required."})
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValidationError({field: "Enter a valid date (YYYY-MM-DD)."}) from exc


def _require_kg(
    payload: dict,
    field: str,
    *,
    partial: bool,
    instance: ItemRequest | None,
) -> Decimal:
    if field not in payload:
        if partial and instance:
            current = getattr(instance, field)
            if current is None:
                raise ValidationError({field: "This field is required."})
            return current
        raise ValidationError({field: "This field is required."})
    try:
        amount = Decimal(str(payload[field]))
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError({field: "Enter a valid weight in kg."}) from exc
    if amount < MIN_KG or amount > MAX_KG:
        raise ValidationError({field: f"Must be between {MIN_KG} and {MAX_KG} kg."})
    return amount.quantize(Decimal("0.01"))


def _resolve_categories(
    codes: object,
    *,
    field: str,
    partial: bool,
    instance: ItemRequest | None,
    instance_accessor: str,
    allow_empty: bool = False,
) -> list[Category]:
    if codes is None:
        if partial and instance:
            return list(getattr(instance, instance_accessor).all())
        return []
    if not isinstance(codes, list):
        raise ValidationError({field: "Provide a list of category codes."})
    normalized = [str(code).upper().strip() for code in codes if str(code).strip()]
    if not normalized:
        return []
    categories = list(Category.objects.filter(code__in=normalized, is_active=True))
    found = {category.code for category in categories}
    missing = [code for code in normalized if code not in found]
    if missing:
        raise ValidationError({field: f"Unknown categories: {', '.join(missing)}."})
    if not categories and not allow_empty:
        raise ValidationError({field: "Select at least one category."})
    return categories


def _optional_text(payload: dict, field: str, *, instance: ItemRequest | None, partial: bool) -> str:
    if field not in payload:
        if partial and instance:
            return getattr(instance, field) or ""
        return ""
    return str(payload.get(field) or "").strip()


def _expires_at(date_to: date) -> datetime:
    return datetime.combine(date_to, time(23, 59, 59), tzinfo=timezone.utc)


def assert_request_editable(item_request: ItemRequest) -> None:
    if item_request.status != RequestStatus.ACTIVE:
        raise ValidationError({"status": "Only active requests can be changed."})
    if item_request.is_expired():
        raise ValidationError({"status": "This request has expired."})
