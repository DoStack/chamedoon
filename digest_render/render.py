"""Draw the daily courier board (JSON from `manage.py courier_digest build`) as a 1080x1350 PNG.

Usage: python render.py board.json board.png
The look is the owner-approved mockup (projects/chamedoon/courier-board-mockup-v2.html, 2026-11-10):
card A for a country channel (photo header, two counts, next rows by date), card B for the umbrella
channel (photo hero, total, corridor tiles). Gregorian date first, Jalali second; flags from flag-icons;
SVG icons; no text arrows. Assets are local, so rendering needs no network.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
W, H, SCALE = 420, 525, 1080 / 420

ICONS = """
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
<symbol id="plane" viewBox="0 0 24 24"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/></symbol>
<symbol id="box" viewBox="0 0 24 24"><path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5"/><path d="M12 13v8"/></symbol>
<symbol id="cal" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></symbol>
<symbol id="go" viewBox="0 0 24 24"><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></symbol>
</svg>"""

CSS = """
@font-face{font-family:Vazirmatn;font-weight:400;src:url(assets/fonts/Vazirmatn-Regular.ttf)}
@font-face{font-family:Vazirmatn;font-weight:600;src:url(assets/fonts/Vazirmatn-SemiBold.ttf)}
@font-face{font-family:Vazirmatn;font-weight:700;src:url(assets/fonts/Vazirmatn-Bold.ttf)}
@font-face{font-family:Vazirmatn;font-weight:800;src:url(assets/fonts/Vazirmatn-ExtraBold.ttf)}
@font-face{font-family:Vazirmatn;font-weight:900;src:url(assets/fonts/Vazirmatn-Black.ttf)}
:root{--ink:#0f172a;--muted:#64748b;--navy:#172554;--orange:#ea580c;--line:#e5e7eb;--soft:#eff6ff;--warm:#fff1e8}
*{box-sizing:border-box}
body{margin:0;width:420px;height:525px;background:#fff;font-family:Vazirmatn,sans-serif;color:var(--ink);overflow:hidden}
.card{width:420px;height:525px;display:flex;flex-direction:column;background:#fff}
.fl{display:inline-block;width:1.333em;height:1em;background-size:cover;background-position:center;border-radius:4px;box-shadow:0 0 0 1px rgba(0,0,0,.08);flex:none}
svg.i{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex:none}
.hdA{position:relative;height:150px;background-size:cover;background-position:center 55%}
.hdA:after,.hero:after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(23,37,84,.1),rgba(23,37,84,.88))}
.hdA .in{position:absolute;inset:auto 20px 14px 20px;z-index:1;display:flex;align-items:center;gap:12px;color:#fff}
.hdA .fl{font-size:30px;border-radius:6px}
.tt{font-size:20px;font-weight:800}
.dt{margin-inline-start:auto;background:rgba(255,255,255,.16);border-radius:12px;padding:4px 12px;font-size:13px;text-align:center;line-height:1.3}
.dt small{display:block;font-size:11px;opacity:.85}
.kpi{display:flex;gap:10px;padding:12px 20px 4px}
.k{flex:1;border-radius:14px;padding:8px 12px;display:flex;align-items:center;gap:10px}
.k b{font-size:28px;font-weight:900;line-height:1}
.k.s{background:var(--soft);color:var(--navy)} .k.p{background:var(--warm);color:var(--orange)}
.k svg.i{width:24px;height:24px}
.rows{padding:2px 20px;display:flex;flex-direction:column}
.r{display:flex;align-items:center;gap:10px;padding:5px 0;border-bottom:1px solid var(--line)}
.r:last-child{border-bottom:0}
.day{width:52px;text-align:center;flex:none;line-height:1.1}
.day b{display:block;font-size:19px;font-weight:900;color:var(--navy)}
.day span{display:block;font-size:11px;color:var(--muted)}
.day em{display:block;font-style:normal;font-size:10px;color:var(--muted)}
.rt{display:flex;align-items:center;gap:6px;flex:1;min-width:0;font-size:14px;font-weight:600}
.rt .fl{font-size:18px}
.rt .pl{color:var(--muted)}
.c{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:120px}
.ty{width:32px;height:32px;border-radius:10px;display:flex;align-items:center;justify-content:center;flex:none}
.ty.s{background:var(--soft);color:var(--navy)} .ty.p{background:var(--warm);color:var(--orange)}
.ft{margin-top:auto;padding:6px 20px 12px;display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--muted)}
.more{background:var(--navy);color:#fff;border-radius:999px;padding:3px 12px;font-weight:700}
.hero{height:170px;background-size:cover;background-position:center;position:relative}
.hero .in{position:absolute;inset:auto 18px 14px 18px;z-index:1;color:#fff;display:flex;align-items:flex-end;gap:12px}
.hero b{font-size:52px;font-weight:900;line-height:.9}
.hero .lb{font-size:15px;opacity:.95;padding-bottom:4px}
.hero .dt{align-self:center}
.tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;padding:12px 16px}
.t{border:1px solid var(--line);border-radius:16px;padding:9px 12px;display:flex;flex-direction:column;gap:5px}
.t .fls{display:flex;align-items:center;gap:6px}
.t .fl{font-size:24px}
.t .fls svg.i{color:var(--muted);width:16px;height:16px}
.ct{display:flex;gap:12px;font-size:14px;font-weight:700}
.ct span{display:flex;align-items:center;gap:4px}
.ct .s{color:var(--navy)} .ct .p{color:var(--orange)}
.ct svg.i{width:15px;height:15px}
.nx{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:4px}
.nx svg.i{width:13px;height:13px}
"""


def e(value) -> str:
    return html.escape(str(value), quote=True)


def flag(code: str) -> str:
    code = "".join(ch for ch in (code or "").lower() if ch.isalpha())[:2]
    path = ASSETS / "flags" / f"{code}.svg"
    if not path.exists():
        return '<span class="fl" style="background:#e5e7eb"></span>'
    return f'<span class="fl" style="background-image:url(assets/flags/{code}.svg)"></span>'


def icon(name: str) -> str:
    return f'<svg class="i"><use href="#{name}"/></svg>'


def date_chip(day: dict) -> str:
    return f'<span class="dt"><span dir="ltr">{e(day["main"])}</span><small>{e(day["sub"])}</small></span>'


def card_a(d: dict) -> str:
    rows = "".join(
        f'<div class="r"><div class="day"><b>{e(r["day"])}</b><span dir="ltr">{e(r["month"])}</span><em>{e(r["sub"])}</em></div>'
        f'<div class="rt">{flag(r["from_flag"])}<span class="c">{e(r["from"])}</span><span class="pl">{icon("go")}</span>'
        f'{flag(r["to_flag"])}<span class="c">{e(r["to"])}</span></div>'
        f'<div class="ty {"s" if r["type"] == "supply" else "p"}">{icon("plane" if r["type"] == "supply" else "box")}</div></div>'
        for r in d["rows"]
    )
    more = f'<span class="more" dir="ltr">+{e(d["more"])}</span>' if d.get("more") not in ("۰", "0", "", None) else "<span></span>"
    return (
        f'<div class="card"><div class="hdA" style="background-image:url(assets/photos/{e(d["photo"])})">'
        f'<div class="in">{flag(d["country"])}<span class="tt">{e(d["title"])}</span>{date_chip(d["day"])}</div></div>'
        f'<div class="kpi"><div class="k s">{icon("plane")}<div><b>{e(d["supply"])}</b><div style="font-size:12px">مسافر</div></div></div>'
        f'<div class="k p">{icon("box")}<div><b>{e(d["demand"])}</b><div style="font-size:12px">بسته</div></div></div></div>'
        f'<div class="rows">{rows}</div>'
        f'<div class="ft"><span dir="ltr">@{e(d["handle"])}</span>{more}</div></div>'
    )


def card_b(d: dict) -> str:
    tiles = "".join(
        f'<div class="t"><div class="fls">{flag(t["from_flag"])}{icon("go")}{flag(t["to_flag"])}</div>'
        f'<div class="ct"><span class="s">{icon("plane")}{e(t["supply"])}</span><span class="p">{icon("box")}{e(t["demand"])}</span></div>'
        f'<div class="nx">{icon("cal")}<span dir="ltr">{e(t["next_main"])}</span> · {e(t["next_sub"])}</div></div>'
        for t in d["tiles"]
    )
    return (
        f'<div class="card"><div class="hero" style="background-image:url(assets/photos/{e(d["photo"])})">'
        f'<div class="in"><b>{e(d["total_fa"])}</b><span class="lb">سفر و بسته فعال</span>{date_chip(d["day"])}</div></div>'
        f'<div class="tiles">{tiles}</div>'
        f'<div class="ft"><span dir="ltr">@{e(d["handle"])}</span><span>{e(d["routes"])} مسیر</span></div></div>'
    )


def page(d: dict) -> str:
    body = card_a(d) if d["kind"] == "board" else card_b(d)
    return f'<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><style>{CSS}</style></head><body>{ICONS}{body}</body></html>'


def render(data: dict, out: Path) -> None:
    doc = HERE / f"_board_{data['scope']}.html"
    doc.write_text(page(data), encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        tab = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=SCALE)
        tab.goto(doc.as_uri())
        tab.wait_for_load_state("networkidle")
        tab.evaluate("document.fonts.ready")
        tab.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
        browser.close()
    doc.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: render.py board.json board.png")
    render(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")), Path(sys.argv[2]))
