"""Daily courier board for the channels, instead of one post per request.

Three scopes:
- ``ca`` and ``de``: the country channels. Only requests whose route starts or ends in that country.
  Card A (departures board): the next trips and packages by date, 14 days ahead.
- ``all``: the umbrella channel (TELEGRAM_CHANNEL_ID). Card B: every active request, grouped by
  country pair, busiest corridors first.

This module only builds the numbers, caption and buttons and sends a rendered image. The image
itself is drawn outside Django by ``digest_render/`` (HTML + Chrome), because the web function
cannot run a browser. Nothing here is sent unless ``send`` is called for a scope.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import jdatetime
from django.conf import settings
from django.utils import timezone

from courier.models import CourierDigestPost, CourierSettings
from item_requests.models import City, Country, CourierChannel, ItemRequest, RequestStatus, RequestType

WINDOW_DAYS = 14
MIN_ITEMS = 3
BOARD_ROWS = 5
TILES = 6
SEND_HOUR = 20

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
PHOTOS = ["jet-sunset.jpg", "window-wing.jpg", "plane-sky.jpg"]


@dataclass(frozen=True)
class Scope:
    key: str
    tz: str
    country: str = ""  # empty: every route (umbrella channel)


SCOPES = {
    "ca": Scope("ca", "America/Toronto", "CA"),
    "de": Scope("de", "Europe/Berlin", "DE"),
    "all": Scope("all", "Europe/Berlin"),
}


def fa(value) -> str:
    return str(value).translate(FA_DIGITS)


def jalali(day: date) -> str:
    j = jdatetime.date.fromgregorian(date=day)
    return f"{fa(j.day)} {JALALI_MONTHS[j.month - 1]}"


def gregorian(day: date) -> str:
    return f"{day.day} {day.strftime('%b')}"


def local_today(scope: Scope, now: datetime | None = None) -> date:
    return (now or timezone.now()).astimezone(ZoneInfo(scope.tz)).date()


def key_date(item: ItemRequest, today: date) -> date | None:
    """The date a reader cares about: the flight for a traveller, the wanted date for a package.

    A window that has started but not ended counts as today. None when it has passed."""
    first = item.flight_date or item.desired_date or item.date_from
    if first and first >= today:
        return first
    if item.date_to and item.date_to >= today:
        return today
    return None


class Names:
    def __init__(self) -> None:
        self.countries = {row.code: row.name_fa or row.name_en for row in Country.objects.all()}
        self.cities = {(row.country.code, row.slug): row.name_fa or row.name_en for row in City.objects.select_related("country")}

    def country(self, code: str) -> str:
        return self.countries.get(code, code)

    def city(self, country: str, slug: str) -> str:
        return self.cities.get((country, slug)) or slug.replace("-", " ").title()


def _active(now: datetime):
    return ItemRequest.objects.filter(status=RequestStatus.ACTIVE, expires_at__gt=now).exclude(unmatchable_reason__gt="")


def _touches(item: ItemRequest, country: str) -> bool:
    return item.origin_country == country or country in item.route_countries()


def _channel(scope: Scope) -> tuple[int | str | None, str]:
    """Chat id and @handle of the scope's channel."""
    if scope.country:
        row = CourierChannel.objects.filter(country=scope.country, enabled=True).first()
        if row is None:
            return None, ""
        return (row.chat_id or (f"@{row.username}" if row.username else None)), row.username
    from notifications.channel import channel_chat_id

    return channel_chat_id(), (getattr(settings, "TELEGRAM_CHANNEL_USERNAME", "") or "").lstrip("@")


def bot_username() -> str:
    return (getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").lstrip("@")


def buttons(count: int) -> list[list[dict]]:
    bot = bot_username()

    def link(start: str) -> str:
        return f"https://t.me/{bot}?startapp={start}"

    return [
        [{"text": f"همه {fa(count)} مورد", "url": link("courier")}],
        [{"text": "خبرم کن", "url": link("courier_alerts")}, {"text": "ثبت سفر یا بسته", "url": link("supply")}],
    ]


def _photo(day: date, scope_key: str) -> str:
    """Round robin by day, shifted per channel so the three boards of one evening differ."""
    shift = sorted(SCOPES).index(scope_key)
    return PHOTOS[(day.toordinal() + shift) % len(PHOTOS)]


def build(scope_key: str, *, now: datetime | None = None) -> dict | None:
    """The board's numbers for one scope today, or None when there is too little to show."""
    scope = SCOPES[scope_key]
    now = now or timezone.now()
    today = local_today(scope, now)
    names = Names()
    items = []
    seen: set[tuple] = set()
    for item in _active(now):
        when = key_date(item, today)
        if when is None:
            continue
        # The same trip posted twice (two groups, or a repost) shows once.
        same = (item.type, item.origin_country, item.origin_city, item.destination_country, item.destination_city, when)
        if same in seen:
            continue
        seen.add(same)
        if scope.country:
            if when > today + timedelta(days=WINDOW_DAYS) or not _touches(item, scope.country):
                continue
        items.append((when, item))
    if len(items) < MIN_ITEMS:
        return None
    items.sort(key=lambda pair: (pair[0], pair[1].pk))
    supply = sum(1 for _when, item in items if item.type == RequestType.SUPPLY)
    _chat, handle = _channel(scope)
    data = {
        "scope": scope.key,
        "kind": "board" if scope.country else "tiles",
        "country": scope.country.lower(),
        "title": f"باربری {names.country(scope.country)}" if scope.country else "چمدون باربری",
        "day": {"main": gregorian(today), "sub": jalali(today)},
        "total": len(items),
        "total_fa": fa(len(items)),
        "supply": fa(supply),
        "demand": fa(len(items) - supply),
        "handle": handle,
        "photo": _photo(today, scope.key),
    }
    if scope.country:
        rows = []
        for when, item in items[:BOARD_ROWS]:
            dest_country, dest_city = item.destination_country, item.destination_city
            rows.append(
                {
                    "day": str(when.day),
                    "month": when.strftime("%b"),
                    "sub": jalali(when),
                    "from_flag": item.origin_country.lower(),
                    "from": names.city(item.origin_country, item.origin_city),
                    "to_flag": dest_country.lower(),
                    "to": names.city(dest_country, dest_city),
                    "type": "supply" if item.type == RequestType.SUPPLY else "demand",
                }
            )
        data["rows"] = rows
        data["more"] = fa(max(0, len(items) - BOARD_ROWS))
        data["caption"] = f"<b>{fa(len(items))} سفر و بسته {names.country(scope.country)} در ۲ هفته آینده</b>"
    else:
        corridors: dict[tuple[str, str], dict] = {}
        for when, item in items:
            key = (item.origin_country, item.destination_country)
            row = corridors.setdefault(key, {"supply": 0, "demand": 0, "next": when})
            row["supply" if item.type == RequestType.SUPPLY else "demand"] += 1
            row["next"] = min(row["next"], when)
        ranked = sorted(corridors.items(), key=lambda pair: (-(pair[1]["supply"] + pair[1]["demand"]), pair[1]["next"]))
        data["tiles"] = [
            {
                "from_flag": origin.lower(),
                "to_flag": dest.lower(),
                "supply": fa(row["supply"]),
                "demand": fa(row["demand"]),
                "next_main": gregorian(row["next"]),
                "next_sub": jalali(row["next"]),
            }
            for (origin, dest), row in ranked[:TILES]
        ]
        data["routes"] = fa(len(corridors))
        data["caption"] = f"<b>{fa(len(items))} سفر و بسته فعال در {fa(len(corridors))} مسیر</b>"
    data["buttons"] = buttons(len(items))
    return data


def due(scope_key: str, *, now: datetime | None = None) -> bool:
    """True, with the board switched on, at the send hour in the scope's timezone when today's board was not sent or skipped yet (a failure retries)."""
    scope = SCOPES[scope_key]
    now = now or timezone.now()
    local = now.astimezone(ZoneInfo(scope.tz))
    if not digest_on() or local.hour != SEND_HOUR:
        return False
    return not CourierDigestPost.objects.filter(scope=scope.key, day=local.date(), status__in=["sent", "skipped"]).exists()


def record_skip(scope_key: str, *, now: datetime | None = None) -> None:
    scope = SCOPES[scope_key]
    CourierDigestPost.objects.get_or_create(scope=scope.key, day=local_today(scope, now), defaults={"status": "skipped"})


def _multipart(fields: dict, file_field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----courier" + secrets.token_hex(12)
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        "Content-Type: image/png\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def send(scope_key: str, image: bytes, data: dict, *, now: datetime | None = None) -> CourierDigestPost:
    """Send the rendered board once per scope and local day. A second call the same day returns the first row."""
    scope = SCOPES[scope_key]
    day = local_today(scope, now)
    existing = CourierDigestPost.objects.filter(scope=scope.key, day=day).first()
    if existing is not None and existing.status == "sent":
        return existing
    chat_id, _handle = _channel(scope)
    row = existing or CourierDigestPost(scope=scope.key, day=day)
    row.chat_id = chat_id if isinstance(chat_id, int) else None
    row.payload = {key: value for key, value in data.items() if key != "buttons"}
    token = (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not chat_id or not token:
        row.status, row.error = "failed", "channel or bot token missing"
        row.save()
        return row
    fields = {
        "chat_id": chat_id,
        "caption": data["caption"],
        "parse_mode": "HTML",
        "reply_markup": json.dumps({"inline_keyboard": data["buttons"]}, ensure_ascii=False),
    }
    body, content_type = _multipart(fields, "photo", f"courier-{scope.key}.png", image)
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto", data=body, headers={"Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
        row.message_id = int(result["result"]["message_id"])
        row.status, row.error = "sent", ""
    except urllib.error.HTTPError as exc:
        row.status = "failed"
        row.error = exc.read().decode("utf-8", "replace").replace(token, "<token>")[:300]
    except Exception as exc:  # noqa: BLE001 - the row keeps the reason; the cron retries next hour
        row.status, row.error = "failed", f"{type(exc).__name__}: {exc}".replace(token, "<token>")[:300]
    row.save()
    return row


def digest_on() -> bool:
    try:
        return CourierSettings.load().channel_digest_on
    except Exception:  # noqa: BLE001 - before migrations the old behaviour stays
        return False
