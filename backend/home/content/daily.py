"""Daily digest of a city channel: card, stats image, caption and voice script from one `data.Daily` (spec §2.0, §2.1, §5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from home.content import text as t
from home.content.data import CURRENCY, Daily
from home.publish import photo_urls

ANIM_SECONDS = 7.0


@dataclass
class DailyView:
    card: dict
    stats: dict
    caption: str
    script: str
    expected: list[int]
    payload: dict


def _basis(listing) -> str:
    basis = t.basis_fa(listing)
    return basis if basis in {"گرم", "سرد"} else ""


def build(d: Daily, day: date, username: str) -> DailyView:
    ch = d.channel
    cur = CURRENCY[ch.country]
    sym, cur_fa, city = t.SYMBOL[cur], t.CURRENCY_FA[cur], t.city_fa(ch.city)
    show_wanted = d.wanted > 0 if ch.country == "DE" else d.wanted >= 3
    sub2 = f"{t.fa(d.offer)} آگهی اجاره و {t.fa(d.wanted)} نفر دنبال خانه" if show_wanted else ""
    chip = f"{city} · {t.jalali(day)}"

    if d.deal:
        row, kind, discount = d.deal
        pct = round(discount * 100)
        tag, note = "پیشنهاد امروز", f"{t.fa(pct)}٪ زیر میانه {t.TYPE_FA[kind]} در {city}"
    else:
        row, kind, pct = d.newest, (d.newest.type if d.newest else ""), 0
        tag, note = "تازه‌ترین", ""
    listing = row.listing if row else None
    img = ""
    if listing is not None and row.photos:
        urls = photo_urls(listing)
        img = urls[0] if urls else ""
    deal_card = (
        {"id": row.id, "tag": tag, "price": round(row.price), "basis": t.esc(_basis(listing)), "title": t.esc(t.deal_title(listing)), "note": note, "img": img}
        if row
        else None
    )
    card = {
        "chip": chip, "username": username, "sym": sym, "n": d.n, "sub2": sub2,
        "tiles": [{"fa": t.TYPE_FA[k], "v": round(v)} for k, v in d.tiles],
        "deal": deal_card or {"tag": "", "price": 0, "basis": "", "title": "", "note": "", "img": ""},
    }

    rows = [[t.TYPE_FA.get(k, "سایر"), n] for k, n in d.by_type]
    if ch.country == "DE" and d.wanted > 0:
        rows.append(["دنبال خانه", d.wanted])
    facts = []
    if d.busiest_area:
        area, n = d.busiest_area
        facts.append([t.place_fa(area) if area.isascii() else area, f"بیشترین آگهی امروز ({t.fa(n)})", 1])
    elif d.wanted > 0:
        facts.append([t.fa(d.wanted), "نفر دنبال خانه", 0])
    elif d.photos >= 3:
        facts.append([t.fa(d.photos), "آگهی با عکس", 0])
    if d.price_range:
        low, high = d.price_range
        facts.append([f"{sym}{t.money(low)} – {t.money(high)}", "بازه قیمت آگهی‌های امروز", 1])
    facts.append([f"{t.fa(d.long_term)} از {t.fa(d.n)}", "اجاره بلندمدت", 0])
    stats = {
        "chip": chip, "n2": d.n, "fa": city, "username": username, "sym": sym, "rows": rows,
        "meds": [[t.TYPE_FA[k], round(v)] for k, v in d.stats_meds], "facts": facts,
    }

    params = t.daily_params(ch.city, day, row.id if row else None)
    lines = [f"<b>{t.fa(d.n)} آگهی تازه در {city}، در ۲۴ ساعت گذشته</b>"]
    if sub2:
        lines.append(sub2)
    lines += [t.dates_line(day), ""]
    if row:
        basis = f" (اجاره {_basis(listing)})" if _basis(listing) else ""
        title = t.esc(t.deal_title(listing))
        if d.deal:
            lines.append(f"🏷 <b>پیشنهاد امروز:</b> {title}، ماهی {t.money(row.price)} {cur_fa}{basis}؛ {note}.")
        else:
            lines.append(f"🆕 <b>تازه‌ترین:</b> {title}، ماهی {t.money(row.price)} {cur_fa}{basis}.")
        lines.append("")
    if d.tiles:
        lines.append("📊 میانه اجاره ماهانه در ۳۰ روز اخیر:")
        lines.append(" · ".join(f"{t.TYPE_FA[k]} {t.money(v)}" for k, v in d.tiles) + f" {cur_fa}")
        lines.append("")
    links = [(f"🏠 آگهی‌های {city} در چمدون", params["all"])]
    if row:
        links.append(("🏷 پیشنهاد امروز" if d.deal else "🆕 تازه‌ترین", params["deal"]))
    links += [("🔔 خبرم کن", params["alert"]), ("➕ آگهی بده", params["post"])]
    lines.append(" · ".join(f'<a href="{t.link(p)}">{label}</a>' for label, p in links))
    lines += ["", f"@{username} · @{t.BOT}"]

    expected = [d.n]
    if show_wanted and ch.country == "DE":
        script = f"امروز {t.words(d.n)} آگهی جدید توی {city} منتشر شده؛ {t.words(d.offer)} تا اجاره، و {t.words(d.wanted)} نفر هم دنبالِ خونه‌ان."
        expected += [d.offer, d.wanted]
    else:
        script = f"امروز {t.words(d.n)} آگهی جدیدِ خونه توی {city} منتشر شده."
    if row and d.deal:
        script += f" پیشنهادِ امروز: یه {t.spoken_type(listing, kind)}{t.spoken_place(listing)}، ماهی {t.words(row.price)} {cur_fa}؛ {t.words(pct)} درصد ارزون‌تر از میانه‌ی شهر."
        expected += [round(row.price), pct]
    elif row:
        script += f" تازه‌ترینش: یه {t.spoken_type(listing, kind)}{t.spoken_place(listing)}، ماهی {t.words(row.price)} {cur_fa}."
        expected += [round(row.price)]
    script += " همه‌ی آگهی‌ها رو توی چمدونِ خانه ببین."

    payload = {
        "n": d.n, "offer": d.offer, "wanted": d.wanted, "ids": d.ids,
        "tiles": [[k, round(v), d.meds[k][0]] for k, v in d.tiles],
        "deal": {"id": row.id, "kind": kind, "price": round(row.price), "pct": pct, "is_deal": bool(d.deal)} if row else None,
        "params": params, "by_type": d.by_type,
    }
    return DailyView(card=card, stats=stats, caption="\n".join(lines), script=script, expected=expected, payload=payload)
