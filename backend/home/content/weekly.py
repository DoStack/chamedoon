"""Weekly city report (spec §2.0 weekly album, §2.3): weekly card video + per-day chart, from the week's listings.

Window: local Monday 00:00 to the slot (Sunday 18:00), on `post.posted_at`. Week-over-week badges stay hidden (§2.0).
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import jdatetime

from home.content import data
from home.content import text as t
from home.content.data import CURRENCY, TYPES
from home.models import HomeChannel, ListingKind

ANIM_SECONDS = 7.0
MAX_TYPES = 5
MIN_AREA = 3


@dataclass
class WeeklyView:
    card: dict
    days: dict
    caption: str
    script: str
    expected: list[int]
    payload: dict


def week_start(local: datetime) -> datetime:
    monday = local.date() - timedelta(days=local.weekday())
    return datetime.combine(monday, time(0), tzinfo=local.tzinfo)


def period_key(monday: date) -> str:
    year, week, _ = monday.isocalendar()
    return f"{year}-W{week:02d}"


def range_fa(first: date, last: date) -> str:
    """«۱۸ تا ۲۴ آبان ۱۴۰۵», or «۲۸ آبان تا ۴ آذر ۱۴۰۵» across months."""
    a, b = jdatetime.date.fromgregorian(date=first), jdatetime.date.fromgregorian(date=last)
    if a.month == b.month:
        return f"{t.fa(a.day)} تا {t.fa(b.day)} {t.JALALI_MONTHS[b.month - 1]} {t.fa(b.year)}"
    return f"{t.jalali(first)} تا {t.jalali(last)} {t.fa(b.year)}"


def range_en(first: date, last: date) -> str:
    """«9–15 Nov 2026»."""
    if first.month == last.month:
        return f"{first.day}–{last.day} {t.EN_MONTHS[last.month - 1]} {last.year}"
    return f"{first.day} {t.EN_MONTHS[first.month - 1]} – {last.day} {t.EN_MONTHS[last.month - 1]} {last.year}"


def build(channel: HomeChannel, slot: datetime, channels=None) -> WeeklyView:
    start = week_start(slot)
    rows = data.dedupe(data.rows_for(channel, start, slot, channels))
    cur = CURRENCY[channel.country]
    sym, cur_fa, city = t.SYMBOL[cur], t.CURRENCY_FA[cur], t.city_fa(channel.city)
    first, last = start.date(), start.date() + timedelta(days=6)
    offer = sum(1 for r in rows if r.kind == ListingKind.OFFER)
    wanted = sum(1 for r in rows if r.kind == ListingKind.WANTED)
    meds = data.medians(rows, cur)
    med_rows = sorted(((k, v) for k, v in meds.items() if k in TYPES), key=lambda item: -item[1][0])[:MAX_TYPES]
    med_rows.sort(key=lambda item: item[1][1])

    names: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    counts = collections.Counter()
    for r in rows:
        if r.area and r.area.casefold() != channel.city.casefold() and data._CLEAN_AREA.match(r.area_name or r.area):
            counts[r.area.casefold()] += 1
            names[r.area.casefold()][r.area_name or r.area] += 1
    areas = [[names[key].most_common(1)[0][0], n] for key, n in counts.most_common(3) if n >= MIN_AREA]
    if len(areas) < 2:
        areas = []

    per_day = collections.Counter(r.posted_at.astimezone(slot.tzinfo).date() for r in rows)
    days = []
    for i in range(7):
        day = first + timedelta(days=i)
        days.append([t.WEEKDAYS[i], t.jalali(day), f"{t.EN_MONTHS[day.month - 1]} {day.day}", per_day.get(day, 0)])
    top = max(d[3] for d in days)
    busiest = [d for d in days if d[3] == top and top > 0]

    chip, chip_en = range_fa(first, last), range_en(first, last)
    card = {
        "chip": chip, "chip_en": chip_en, "fa": city, "sym": sym, "username": channel.username, "total": len(rows), "offer": offer, "wanted": wanted,
        "meds": [[t.TYPE_FA[k], round(v[1]), v[0]] for k, v in med_rows], "areas": areas,
    }
    days_card = {
        "chip": chip, "chip_en": chip_en, "fa": city, "username": channel.username, "total": len(rows), "days": days,
        "busiest": " و ".join(d[0] for d in busiest) or "—",
    }

    week = f"{first.isocalendar()[0]}{first.isocalendar()[1]:02d}"
    slug = t.city_slug(channel.city)
    params = {"all": f"wk_{slug}_{week}", "alert": f"wk_{slug}_{week}_alert", "post": "home_post"}
    lines = [f"<b>بازار اجاره {city}، هفته گذشته</b>", f"📅 {chip} · {chip_en}", f"{t.fa(len(rows))} آگهی تازه: {t.fa(offer)} اجاره، {t.fa(wanted)} دنبال خانه", ""]
    if med_rows:
        lines.append("📊 میانه اجاره ماهانه آگهی‌های این هفته:")
        lines.append(" · ".join(f"{t.TYPE_FA[k]} {t.money(v[1])}" for k, v in med_rows) + f" {cur_fa}")
    if areas:
        lines.append("📍 پرآگهی‌ترین منطقه‌ها: " + "، ".join(f"{t.esc(a)} ({t.fa(n)})" for a, n in areas))
    if busiest:
        lines.append("📈 پرآگهی‌ترین روز: " + " و ".join(f"{d[0]} {d[1]} ({d[2]})" for d in busiest) + f"، {t.fa(top)} آگهی")
    lines.append("")
    links = [(f"🏠 آگهی‌های {city} در چمدون", params["all"]), ("🔔 خبرم کن", params["alert"]), ("➕ آگهی بده", params["post"])]
    lines.append(" · ".join(f'<a href="{t.link(p)}">{label}</a>' for label, p in links))
    lines += ["", f"@{channel.username} · @{t.BOT}"]

    a, b = jdatetime.date.fromgregorian(date=first), jdatetime.date.fromgregorian(date=last)
    span = f"از {t.words(a.day)} تا {t.words(b.day)} {t.JALALI_MONTHS[b.month - 1]}" if a.month == b.month else f"از {t.words(a.day)} {t.JALALI_MONTHS[a.month - 1]} تا {t.words(b.day)} {t.JALALI_MONTHS[b.month - 1]}"
    expected = [a.day, b.day, len(rows)]
    script = f"هفته‌ی گذشته، {span}، {t.words(len(rows))} آگهی جدیدِ خونه توی {city} منتشر شد"
    if channel.country == "DE" and wanted:
        script += f"؛ {t.words(offer)} تا اجاره، و {t.words(wanted)} نفر هم دنبالِ خونه بودن"
        expected += [offer, wanted]
    script += "."
    spoken = med_rows[:3]
    if spoken:
        parts = [f"{t.SPOKEN_TYPE[k]} {t.words(v[1])}" for k, v in spoken]
        joined = parts[0] if len(parts) == 1 else "، ".join(parts[:-1]) + "، و " + parts[-1]
        script += f" میانه‌ی اجاره‌ی {joined} {cur_fa} بود."
        expected += [round(v[1]) for _, v in spoken]
    if areas:
        area_fa = t.place_fa(areas[0][0])
        if area_fa and not area_fa.isascii():
            script += f" پرآگهی‌ترین منطقه هم {area_fa} بود."
    script += " همه‌ی آگهی‌ها رو توی چمدونِ خانه ببین."

    payload = {"total": len(rows), "offer": offer, "wanted": wanted, "meds": [[k, round(v[1]), v[0]] for k, v in med_rows], "areas": areas,
               "days": [d[3] for d in days], "window": [start.isoformat(), slot.isoformat()], "params": params}
    return WeeklyView(card=card, days=days_card, caption="\n".join(lines), script=script, expected=expected, payload=payload)
