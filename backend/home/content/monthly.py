"""Monthly city report (spec §2.4) and country weekly (§2.5): still image + caption, templates approved 2026-11-10."""

from __future__ import annotations

import collections
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import jdatetime

from home import deeplink
from home.content import data
from home.content import text as t
from home.content import weekly as weekly_view
from home.content.data import CURRENCY, TYPES
from home.models import ContentKind, HomeChannel, HomeContentPost, ListingKind

PERSIAN_MONTHS_G = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"]


@dataclass
class StillView:
    card: dict
    caption: str
    payload: dict


def month_window(slot: datetime) -> tuple[datetime, datetime]:
    """The Gregorian month just ended, local midnight to local midnight."""
    first = slot.date().replace(day=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    return datetime.combine(prev, time(0), tzinfo=slot.tzinfo), datetime.combine(first, time(0), tzinfo=slot.tzinfo)


def _span(first: date, last: date) -> tuple[str, str]:
    a, b = jdatetime.date.fromgregorian(date=first), jdatetime.date.fromgregorian(date=last)
    fa_ = f"{t.jalali(first)} تا {t.jalali(last)} {t.fa(b.year)}" if (a.month != b.month) else f"{t.fa(a.day)} تا {t.fa(b.day)} {t.JALALI_MONTHS[b.month - 1]} {t.fa(b.year)}"
    en = f"{t.EN_MONTHS[first.month - 1]} {first.day} – {t.EN_MONTHS[last.month - 1]} {last.day}, {last.year}"
    return fa_, en


def _areas(rows, city: str, limit: int = 3) -> list[list]:
    names: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    counts = collections.Counter()
    for r in rows:
        if r.area and r.area.casefold() != city.casefold() and data._CLEAN_AREA.match(r.area_name or r.area):
            counts[r.area.casefold()] += 1
            names[r.area.casefold()][r.area_name or r.area] += 1
    return [[names[key].most_common(1)[0][0], n] for key, n in counts.most_common(limit) if n >= 3]


def build_monthly(channel: HomeChannel, start: datetime, end: datetime, *, title2: str | None = None, period: str | None = None, channels=None) -> StillView:
    """KPIs of [start, end), weekly medians of the Mon–Sun weeks whose Monday falls inside, busiest areas."""
    cur = CURRENCY[channel.country]
    sym, cur_fa, city = t.SYMBOL[cur], t.CURRENCY_FA[cur], t.city_fa(channel.city)
    rows = data.dedupe(data.rows_for(channel, start, end, channels))
    offers = [r for r in rows if r.kind == ListingKind.OFFER]
    top = [k for k, _ in collections.Counter(r.type for r in offers if r.type in TYPES).most_common(3)]
    monday = start.date() + timedelta(days=(7 - start.weekday()) % 7)
    weeks, per_week = [], []
    while monday < end.date():
        ws = datetime.combine(monday, time(0), tzinfo=start.tzinfo)
        week_rows = data.dedupe(data.rows_for(channel, ws, ws + timedelta(days=7), channels))
        per_week.append(data.medians(week_rows, cur))
        weeks.append([t.jalali(monday), f"{t.EN_MONTHS[monday.month - 1]} {monday.day}"])
        monday += timedelta(days=7)
    series = [{"fa": t.TYPE_FA[k], "v": [round(m[k][1]) if k in m else None for m in per_week]} for k in top]
    series = [s for s in series if any(s["v"])]
    # Dearest line first, as drawn (apartment, basement, room): the template colours lines by position.
    series.sort(key=lambda s: -max(v for v in s["v"] if v))
    last_day = end.date() - timedelta(days=1)
    chip_fa, chip_en = _span(start.date(), last_day)
    title2 = title2 or f"{PERSIAN_MONTHS_G[start.month - 1]} {t.fa(start.year)}"
    areas = _areas(rows, channel.city)
    card = {"fa": city, "title2": title2, "chip_fa": chip_fa, "chip_en": chip_en, "sym": sym, "username": channel.username,
            "total": len(rows), "offer": len(offers), "wanted": sum(1 for r in rows if r.kind == ListingKind.WANTED),
            "weeks": weeks, "series": series, "areas": areas}
    slug = t.city_slug(channel.city)
    period = period or f"{start:%Y%m}"
    params = {"all": f"mo_{slug}_{period}", "alert": f"mo_{slug}_{period}_alert", "post": "home_post"}
    lines = [f"<b>بازار اجاره {city}، {title2}</b>", f"📅 {chip_fa} · {chip_en}",
             f"{t.fa(card['total'])} آگهی تازه: {t.fa(card['offer'])} اجاره، {t.fa(card['wanted'])} دنبال خانه", ""]
    latest = per_week[-1] if per_week else {}
    shown = [(k, latest[k][1]) for k in top if k in latest]
    if shown:
        lines.append(f"📊 میانه اجاره ماهانه، هفته {weeks[-1][0]}:")
        lines.append(" · ".join(f"{t.TYPE_FA[k]} {t.money(v)}" for k, v in shown) + f" {cur_fa}")
    if areas:
        lines.append("📍 پرآگهی‌ترین منطقه‌ها: " + "، ".join(f"{t.esc(a)} ({t.fa(n)})" for a, n in areas))
    lines.append("")
    links = [(f"🏠 آگهی‌های {city} در چمدون", params["all"]), ("🔔 خبرم کن", params["alert"]), ("➕ آگهی بده", params["post"])]
    lines.append(" · ".join(f'<a href="{t.link(p)}">{label}</a>' for label, p in links))
    lines += ["", f"@{channel.username} · @{t.BOT}"]
    payload = {"total": card["total"], "offer": card["offer"], "wanted": card["wanted"], "series": series, "weeks": weeks, "areas": areas,
               "window": [start.isoformat(), end.isoformat()], "params": params}
    return StillView(card=card, caption="\n".join(lines), payload=payload)


COUNTRY_FA = {"CA": "کانادا", "DE": "آلمان"}
COUNTRY_ORDER = {"CA": ["toronto", "montreal", "vancouver"], "DE": ["berlin", "hamburg", "cologne"]}


def city_week_numbers(channel: HomeChannel, slot: datetime, channels=None, *, full_week: bool = False) -> dict:
    """That city's weekly numbers for the week of `slot`: from its sent weekly post when there is one, so both posts agree;
    otherwise computed the same way (weekly.build) up to the city's own slot, or now if that slot is later (Vancouver)."""
    from home.content import schedule

    tz = schedule.tz_of(channel)
    local = slot.astimezone(tz)
    city_slot = local.replace(hour=schedule.WEEKLY_HOUR, minute=0, second=0, microsecond=0)
    period = weekly_view.period_key(weekly_view.week_start(city_slot).date())
    sent = HomeContentPost.objects.filter(kind=ContentKind.WEEKLY, chat_id=channel.chat_id, period_key=period, sent_at__isnull=False).first()
    payload = (sent.payload or {}) if sent else {}
    if full_week:  # dry runs against a hand-made full Mon–Sun week
        payload = weekly_view.build(channel, weekly_view.week_start(city_slot) + timedelta(days=7, microseconds=-1), channels).payload
    elif "total" not in payload:
        payload = weekly_view.build(channel, min(city_slot, local), channels).payload
    meds = {k: v for k, v, _n in payload.get("meds", [])}
    return {"n": payload["total"], "wanted": payload["wanted"], "room": meds.get("room"), "apt": meds.get("apartment"), "source": "post" if sent else "computed"}


def build_country(country_channel: HomeChannel, slot: datetime, city_channels: list[HomeChannel], *, full_week: bool = False) -> StillView:
    cc = country_channel.country
    cur = CURRENCY[cc]
    by_slug = {t.city_slug(c.city): c for c in city_channels if c.country == cc}
    rows, numbers = [], {}
    for slug in COUNTRY_ORDER.get(cc, sorted(by_slug)):
        if slug not in by_slug:
            continue
        nums = city_week_numbers(by_slug[slug], slot, city_channels, full_week=full_week)
        numbers[slug] = nums
        rows.append({"fa": t.city_fa(by_slug[slug].city), "n": nums["n"], "wanted": nums["wanted"], "room": nums["room"], "apt": nums["apt"], "slug": slug})
    first = weekly_view.week_start(slot).date()
    chip_fa, chip_en = weekly_view.range_fa(first, first + timedelta(days=6)), weekly_view.range_en(first, first + timedelta(days=6))
    total = sum(r["n"] for r in rows)
    card = {"country_fa": COUNTRY_FA.get(cc, cc), "sym": t.SYMBOL[cur], "username": country_channel.username, "rows": rows, "total": total,
            "chip_fa": chip_fa, "chip_en": chip_en}
    rooms = [r["room"] for r in rows if r["room"]]
    best = [r["fa"] for r in rows if rooms and r["room"] == min(rooms)]
    lines = [f"<b>اجاره در شهرهای {card['country_fa']}، هفته گذشته</b>", f"📅 {chip_fa} · {chip_en}", f"{t.fa(total)} آگهی تازه در {t.fa(len(rows))} شهر", ""]
    for r in rows:
        meds = []
        if r["room"]:
            meds.append(f"اتاق {t.money(r['room'])}")
        if r["apt"]:
            meds.append(f"آپارتمان {t.money(r['apt'])}")
        lines.append(f"{r['fa']}: {t.fa(r['n'])} آگهی، {t.fa(r['wanted'])} نفر دنبال خانه" + (f"؛ میانه {'، '.join(meds)} {t.CURRENCY_FA[cur]}" if meds else ""))
    if best:
        lines.append(f"💚 ارزان‌ترین اتاق این هفته: {' و '.join(best)}")
    lines.append("")
    src = f"cc{cc.lower()}"
    lines.append(" · ".join(f'<a href="{t.link(deeplink.build(cc, by_slug[r["slug"]].city, source=src))}">{r["fa"]}</a>' for r in rows))
    lines += ["", f"@{country_channel.username} · @{t.BOT}"]
    payload = {"total": total, "rows": rows, "numbers": numbers, "best": best, "window_start": first.isoformat()}
    return StillView(card=card, caption="\n".join(lines), payload=payload)
