"""Render the courier bot's real conversation, channel posts and Mini App page as HTML (no Telegram call).

Runs the real flow (courier/flow.py) on a seeded in-memory SQLite database, records the Bot API
actions it returns, and writes Telegram-like phones to bot-*.html and channel.html. The Mini App
page is rendered by the real view with a logged-in session (the dev login) to miniapp*.html.
Screenshots in this folder are taken from those pages with headless Edge or Chrome:

    cd backend
    DJANGO_SETTINGS_MODULE=config.test_settings python ../docs/screenshots/courier/render.py
    msedge --headless --window-size=1300,1100 --screenshot=1-start-steps.png bot-1.html
"""

from __future__ import annotations

import copy
import html
import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")
os.environ.setdefault("DEBUG", "1")

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402

call_command("migrate", verbosity=0)

from courier import flow, texts  # noqa: E402
from courier.models import CourierIntro  # noqa: E402
from item_requests.models import ItemRequest  # noqa: E402
from item_requests.seed import seed_catalog  # noqa: E402
from item_requests.services import create_item_request  # noqa: E402
from matching.models import Match, MatchStatus  # noqa: E402
from miniapp.auth import SESSION_USER_KEY  # noqa: E402
from miniapp.i18n import SESSION_LOCALE_KEY  # noqa: E402
from notifications.channel import _format_request_message  # noqa: E402
from users.models import User  # noqa: E402

SARA = 5550001
OMAR = 5550002


def person(uid: int, name: str, username: str | None = None) -> User:
    return User.objects.create(telegram_user_id=uid, first_name=name, telegram_username=username)


def trip(user: User, days: int, kg: str = "5", dest: str = "tehran", **extra) -> ItemRequest:
    today = timezone.localdate()
    flight = (today + timedelta(days=days)).isoformat()
    payload = {
        "type": "SUPPLY",
        "origin_country": "CA",
        "origin_city": "toronto",
        "destination_country": "IR",
        "destination_city": dest,
        "flight_date": flight,
        "date_from": today.isoformat(),
        "date_to": flight,
        "capacity_kg": kg,
        "item_category_codes": ["DOCUMENTS", "CLOTHES", "PERSONAL_ITEMS", "OTHER"],
        **extra.pop("payload", {}),
    }
    return create_item_request(user, payload, **extra)


def setup_data() -> dict:
    seed_catalog()
    sara = person(SARA, "Sara", "sara_t")
    omar = person(OMAR, "Omar", "omar_fly")
    reza = person(5550003, "Reza", "reza_trip")
    shadow = person(9_000_000_000_501, "Ali", "ali_public")
    lida = person(5550004, "Lida", "lida_s")
    found = {
        "omar": trip(omar, 6, payload={"excluded_category_codes": ["MEDICINE", "FOOD"], "item_category_codes": ["DOCUMENTS", "CLOTHES"]}),
        "reza": trip(reza, 14, kg="2"),
        "ali": trip(shadow, 10, imported=True, source_url="https://t.me/example_channel/1234"),
    }
    old_trip = trip(omar, 40, dest="mashhad")
    old = create_item_request(
        lida,
        {
            "type": "DEMAND",
            "origin_country": "CA",
            "origin_city": "toronto",
            "destination_country": "IR",
            "destination_city": "mashhad",
            "desired_date": (timezone.localdate() + timedelta(days=38)).isoformat(),
            "weight_kg": "2",
            "item_category_codes": ["CLOTHES"],
        },
    )
    Match.objects.update_or_create(demand_request=old, supply_request=old_trip, defaults={"score": Decimal("90"), "status": MatchStatus.COMPLETED})
    Match.objects.update_or_create(demand_request=old, supply_request=found["omar"], defaults={"score": Decimal("90"), "status": MatchStatus.COMPLETED})
    ItemRequest.objects.filter(pk=found["omar"].pk).update(created_at=timezone.now() - timedelta(days=1))
    ItemRequest.objects.filter(pk=found["reza"].pk).update(created_at=timezone.now() - timedelta(days=3))
    found["sara"] = sara
    return found


def message(text: str, uid: int = SARA, name: str = "Sara", username: str = "sara_t") -> dict:
    return {"update_id": 1, "message": {"message_id": 10, "from": {"id": uid, "first_name": name, "username": username}, "chat": {"id": uid, "type": "private"}, "text": text}}


def tap(data: str, uid: int = SARA, name: str = "Sara", username: str = "sara_t") -> dict:
    return {
        "update_id": 2,
        "callback_query": {"id": "cb", "from": {"id": uid, "first_name": name, "username": username}, "data": data, "message": {"message_id": 77, "chat": {"id": uid, "type": "private"}}},
    }


class Recorder:
    """Scenes of one chat: what the user did and what the bot sent or edited there."""

    def __init__(self) -> None:
        self.scenes: list[dict] = []
        self.keyboard = None

    def scene(self, title: str, caption: str, steps: list[tuple[str, str, str]], *, chat: int = SARA, who: tuple = (SARA, "Sara", "sara_t"), carry: bool = False, header: str = "چمدون باربری") -> list[dict]:
        bubbles: list[dict] = []
        if carry and self.scenes:
            previous = [b for b in self.scenes[-1]["bubbles"] if "buttons" in b]
            bubbles = [copy.deepcopy(previous[-1])] if previous else []
        elsewhere: list[dict] = []
        for kind, value, label in steps:
            if kind == "msg":
                bubbles.append({"out": True, "text": html.escape(value)})
                actions = flow.handle_update(message(value, *who))
            elif kind == "tap":
                bubbles.append({"tap": label})
                actions = flow.handle_update(tap(value, *who))
            else:
                bubbles.append({"note": label})
                actions = value  # prepared actions
            for action in actions:
                method, payload = action["method"], action["payload"]
                if payload.get("chat_id") not in (None, chat) and method != "answerCallbackQuery":
                    elsewhere.append(action)
                    continue
                markup = payload.get("reply_markup") or {}
                if method == "sendMessage":
                    bubbles.append({"text": payload["text"], "buttons": markup.get("inline_keyboard") or []})
                    if markup.get("keyboard"):
                        self.keyboard = markup["keyboard"]
                elif method == "editMessageText":
                    for bubble in reversed(bubbles):
                        if "buttons" in bubble and not bubble.get("out"):
                            bubble.update(text=payload["text"], buttons=markup.get("inline_keyboard") or [], edited=True)
                            break
                    else:
                        bubbles.append({"text": payload["text"], "buttons": markup.get("inline_keyboard") or [], "edited": True})
                elif method == "answerCallbackQuery" and payload.get("text"):
                    bubbles.append({"toast": payload["text"]})
        self.scenes.append({"title": title, "caption": caption, "bubbles": bubbles, "keyboard": self.keyboard if chat == SARA else None, "header": header})
        return elsewhere


CSS = """
body{margin:0;background:#ECEEF1;font-family:Vazirmatn,Tahoma,system-ui,sans-serif;color:#1B232B}
.wrap{display:flex;flex-wrap:wrap;gap:28px;padding:28px;direction:rtl}
.ph{width:390px;display:flex;flex-direction:column;gap:8px}
.ph h2{margin:0;font-size:16px}
.cap{font-size:12.5px;color:#3D4852;line-height:1.7}
.phone{width:390px;min-height:820px;border-radius:40px;border:9px solid #1B232B;background:#fff;overflow:hidden;display:flex;flex-direction:column}
.tgh{height:56px;flex:none;display:flex;align-items:center;gap:10px;padding:0 14px;border-bottom:1px solid #E3E6EA;direction:ltr}
.av{width:36px;height:36px;border-radius:50%;background:#0E2741;color:#fff;font-weight:800;display:flex;align-items:center;justify-content:center}
.nm{font-weight:700;font-size:15px;direction:rtl}.sub{font-size:12px;color:#5E6A75}
.chat{flex:1;background:#D5DCE6;padding:12px 10px;display:flex;flex-direction:column;justify-content:flex-end;gap:6px;direction:ltr}
.msg{direction:rtl;text-align:right;max-width:330px;padding:8px 11px 6px;border-radius:14px;font-size:13px;line-height:1.75;box-shadow:0 1px 1px rgba(0,0,0,.08);background:#fff;align-self:flex-start;word-wrap:break-word}
.msg a{color:#1F6FB8;text-decoration:none}
.out{align-self:flex-end;background:#DFF5C8}
.ed{display:block;text-align:left;font-size:10px;color:#6B7785}
.ikb{align-self:flex-start;width:330px;display:flex;flex-direction:column;gap:4px;direction:rtl}
.ikr{display:flex;gap:4px}
.ib{flex:1;font-size:12px;font-weight:600;color:#fff;background:#0E2741;border-radius:9px;padding:7px 4px;text-align:center}
.ib.url{background:#1F6FB8}
.tap{align-self:flex-end;direction:rtl;font-size:11.5px;color:#fff;background:rgba(27,35,43,.55);border-radius:999px;padding:3px 10px}
.toast{align-self:center;direction:rtl;font-size:11.5px;color:#fff;background:rgba(27,35,43,.8);border-radius:8px;padding:5px 10px}
.note{align-self:center;direction:rtl;font-size:11px;color:#3D4852;background:#F4E7C5;border-radius:8px;padding:4px 10px}
.kb{flex:none;background:#EEF0F3;padding:6px;display:flex;flex-direction:column;gap:6px;border-top:1px solid #D6DBE0;direction:rtl}
.kbr{display:flex;gap:6px}
.kbb{flex:1;font-size:12.5px;font-weight:600;background:#fff;border-radius:8px;padding:9px 4px;text-align:center;box-shadow:0 1px 0 #BFC6CD}
.post{direction:rtl;text-align:right;align-self:flex-start;width:350px;background:#fff;border-radius:14px;padding:8px 12px;font-size:13.5px;line-height:1.85;box-shadow:0 1px 1px rgba(0,0,0,.08);white-space:pre-line}
.tag{color:#1F6FB8}
"""


def _bubble_html(bubble: dict) -> list[str]:
    if bubble.get("tap"):
        return [f'<div class="tap">👆 {html.escape(bubble["tap"])}</div>']
    if bubble.get("toast"):
        return [f'<div class="toast">{html.escape(bubble["toast"])}</div>']
    if bubble.get("note"):
        return [f'<div class="note">{html.escape(bubble["note"])}</div>']
    if bubble.get("out"):
        return [f'<div class="msg out">{bubble["text"]}</div>']
    edited = '<span class="ed">ویرایش‌شده</span>' if bubble.get("edited") else ""
    parts = [f'<div class="msg">{bubble["text"].replace(chr(10), "<br>")}{edited}</div>']
    rows = []
    for row in bubble.get("buttons") or []:
        cells = "".join(f'<div class="ib{" url" if button.get("url") else ""}">{html.escape(button["text"])}</div>' for button in row)
        rows.append(f'<div class="ikr">{cells}</div>')
    if rows:
        parts.append(f'<div class="ikb">{"".join(rows)}</div>')
    return parts


def phone(title: str, caption: str, body: str, header: str, keyboard=None, sub: str = "ربات") -> str:
    kb = ""
    if keyboard:
        rows = "".join('<div class="kbr">' + "".join(f'<div class="kbb">{html.escape(b["text"])}</div>' for b in row) + "</div>" for row in keyboard)
        kb = f'<div class="kb">{rows}</div>'
    return (
        f'<div class="ph"><h2>{html.escape(title)}</h2><div class="phone">'
        f'<div class="tgh"><div class="av">چ</div><div><div class="nm">{html.escape(header)}</div><div class="sub">{html.escape(sub)}</div></div></div>'
        f'<div class="chat">{body}</div>{kb}</div>'
        f'<div class="cap">{html.escape(caption)}</div></div>'
    )


def page(phones: list[str], title: str) -> str:
    return (
        f'<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        '<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;600;700;800&display=swap" rel="stylesheet">'
        f"<style>{CSS}</style></head><body><div class=\"wrap\">{''.join(phones)}</div></body></html>"
    )


def render_scenes(scenes: list[dict]) -> list[str]:
    return [
        phone(scene["title"], scene["caption"], "".join(part for bubble in scene["bubbles"] for part in _bubble_html(bubble)), scene["header"], scene["keyboard"])
        for scene in scenes
    ]


def bot_pages(data: dict) -> None:
    rec = Recorder()
    month_key = texts.upcoming_months()[1][0]
    rec.scene("1 · /start", "One line about the service, one question, and the 5-button keyboard.", [("msg", "/start", "")])
    rec.scene("2 · قدم ۲: مسیر", "Three busy routes and «مسیر دیگه». The same message is edited each step.", [("tap", "cr:r:D", "📦 بسته دارم، دنبال مسافرم")], carry=True)
    rec.scene("3 · قدم ۳: زمان", "Month chips, an exact date, or «عجله ندارم».", [("tap", "cr:rt:0", "تورنتو ← تهران")], carry=True)
    rec.scene("4 · قدم ۴: چی و چقدر", "Size chips, an optional typed line, and the prohibited hint before posting a package.", [("tap", f"cr:m:{month_key}", texts.month_name(texts.upcoming_months()[1][1])), ("msg", "دو جلد کتاب و یک شال", "")], carry=True)
    write("bot-1.html", render_scenes(rec.scenes), "Courier bot 1")

    rec.scenes = []
    rec.scene("5 · نتیجه", "Saved through the request service; travelers from the existing matcher, nearest date first, trust line only with real completed shipments.", [("tap", "cr:w:3", "۱ تا ۳ کیلو"), ("tap", "cr:ok", "✅ ثبت و دیدن مسافرها")], carry=True)
    rec.scene("6 · 💬 با سوییچ خاموش", "COURIER_INTROS_ENABLED off (default): no message to anyone; the contact the owner published (original post) instead.", [("tap", f"cr:i:{data['ali'].pk}", "💬 ۲")])
    with override_settings(COURIER_INTROS_ENABLED=True):
        elsewhere = rec.scene("7 · 💬 با سوییچ روشن", "Intro request sent; the traveler must accept before usernames are shared.", [("tap", f"cr:i:{data['omar'].pk}", "💬 ۱")])
        omar = Recorder()
        omar.scene("8 · طرف مقابل: قبول / نه", "What the traveler gets. No username is shared yet.", [("run", elsewhere, "پیام از ربات")], chat=OMAR, who=(OMAR, "Omar", "omar_fly"))
        back = omar.scene("9 · قبول شد", "Only after «قبول» both get the other's username.", [("tap", f"cr:ia:{CourierIntro.objects.get().pk}", "✅ قبول")], chat=OMAR, who=(OMAR, "Omar", "omar_fly"), carry=True)
        omar.scene("10 · فرستنده", "Sara gets the traveler's username and a chat button.", [("run", back, "Omar قبول کرد")])
    write("bot-2.html", render_scenes(rec.scenes[:2]), "Courier bot 2")
    write("bot-3.html", render_scenes(rec.scenes[2:]) + render_scenes(omar.scenes), "Courier bot 3")

    rec.scenes = []
    mine = ItemRequest.objects.filter(user=data["sara"]).latest("pk")
    rec.scene("11 · 🔔 خبرم کن", "Route alert saved from the result; the list has pause and delete (up to 5). Sending is off until switched on.", [("tap", f"cr:al:n:{mine.pk}", "🔔 مسافر جدید اومد خبرم کن")])
    rec.scene("12 · ⚠️ ممنوعه‌ها", "The prohibited list (editable in Admin → Courier settings).", [("tap", "cr:pr", "⚠️ چه چیزهایی ممنوعه؟")])
    from courier import alerts

    with override_settings(COURIER_ALERTS_ENABLED=True):
        new_trip = trip(User.objects.get(telegram_user_id=5550003), 20, kg="10", payload={})
        alert = alerts.user_alerts(SARA).first()
        text, keyboard = alerts.alert_message(alert, new_trip)
    rec.scene("13 · پیام گوش به زنگ", "With COURIER_ALERTS_ENABLED on: one message per new matching traveler.", [("run", [{"method": "sendMessage", "payload": {"chat_id": SARA, "text": text, "reply_markup": {"inline_keyboard": keyboard}}}], "مسافر تازه ثبت شد")])
    rec.scene("14 · مسافرم: روز پرواز", "The traveler side picks the flight day after the month.", [("tap", "cr:r:S", "🧳 مسافرم، جا دارم"), ("tap", "cr:rt:0", "تورنتو ← تهران"), ("tap", f"cr:m:{month_key}", texts.month_name(texts.upcoming_months()[1][1]))])
    write("bot-4.html", render_scenes(rec.scenes), "Courier bot 4")


def channel_page(data: dict) -> None:
    package = create_item_request(
        User.objects.get(telegram_user_id=5550004),
        {
            "type": "DEMAND",
            "origin_country": "IR",
            "origin_city": "tehran",
            "destination_country": "CA",
            "destination_city": "montreal",
            "desired_date_open": True,
            "date_to": (timezone.localdate() + timedelta(days=45)).isoformat(),
            "weight_kg": "2",
            "item_category_codes": ["CLOTHES"],
        },
    )
    posts = []
    for item in (data["omar"], package):
        text = html.escape(_format_request_message(item))
        lines = text.split("\n")
        lines[-1] = f'<span class="tag">{lines[-1]}</span>'
        posts.append(f'<div class="post">{chr(10).join(lines)}</div><div class="ikb"><div class="ikr"><div class="ib url">🔎 مشاهده در چمدون</div></div></div>')
    write(
        "channel.html",
        [phone("کانال باربری", "Role and route first, then date, weight, prohibited hint, «توافقی», hashtags #مبدا_مقصد #مسافر|#بسته #ماه. Same text in the main and the country channels.", "".join(posts), "چمدون - باربری | کانادا", sub="کانال")],
        "Courier channel posts",
    )


def miniapp_pages() -> None:
    client = Client()
    user = User.objects.get(telegram_user_id=SARA)
    session = client.session
    session[SESSION_USER_KEY] = user.pk
    session[SESSION_LOCALE_KEY] = "fa"
    session.save()
    static = (BACKEND / "miniapp" / "static").as_uri() + "/"
    month_key = texts.upcoming_months()[1][0]
    for name, url in (
        ("miniapp.html", f"/app/courier/?role=D&from=CA:toronto&to=IR:tehran&month={month_key}"),
        ("miniapp-packages.html", "/app/courier/?role=S"),
        ("miniapp-alerts.html", "/app/courier/alerts/?saved=1"),
    ):
        response = client.get(url)
        assert response.status_code == 200, (url, response.status_code)
        body = response.content.decode().replace('"/static/', f'"{static}').replace("https://telegram.org/js/telegram-web-app.js", "")
        (HERE / name).write_text(body, encoding="utf-8")


def write(name: str, phones: list[str], title: str) -> None:
    (HERE / name).write_text(page(phones, title), encoding="utf-8")
    print(HERE / name)


def main() -> None:
    with override_settings(TELEGRAM_BOT_USERNAME="Chamed0on_bot", TELEGRAM_CHANNEL_ENABLED=False, TELEGRAM_BOT_TOKEN=""):
        data = setup_data()
        bot_pages(data)
        channel_page(data)
        miniapp_pages()


if __name__ == "__main__":
    main()
