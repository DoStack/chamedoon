"""Render the housing bot's real conversation as Telegram-like HTML (no Telegram call).

Runs the real handlers (home.botchat, home.alerts) on an in-memory database with a fake
Bot API that records every call, and writes one phone per scene to bot.html next to this
file. Screenshots in this folder are taken from that page.

    cd backend
    DJANGO_SETTINGS_MODULE=config.test_settings python ../docs/screenshots/bot/render.py
"""

from __future__ import annotations

import html
import os
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402
from django.utils import timezone  # noqa: E402

call_command("migrate", verbosity=0)

from home import alerts  # noqa: E402
from home.botchat import handle_update  # noqa: E402
from home.models import HomeAlert, HomeChannel, HomeListing, HomeSettings, HomeSource  # noqa: E402

USER = 5550001


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 100

    def __call__(self, method: str, payload: dict | None = None, **_kwargs):
        payload = payload or {}
        self.calls.append((method, payload))
        if method in {"sendMessage", "editMessageText"}:
            self.next_id += 1
            return {"message_id": self.next_id}
        return True


def message(text: str) -> dict:
    return {"update_id": 1, "message": {"message_id": 10, "from": {"id": USER, "first_name": "Sara"}, "chat": {"id": USER, "type": "private"}, "text": text}}


def tap(data: str) -> dict:
    return {
        "update_id": 2,
        "callback_query": {"id": "cb", "from": {"id": USER, "first_name": "Sara"}, "data": data, "message": {"message_id": 77, "chat": {"id": USER, "type": "private"}}},
    }


def setup_data() -> None:
    from tests.test_home_publish import ca_listing

    HomeSource.objects.all().delete()
    HomeSettings.objects.update_or_create(pk=1, defaults={"bot_token": "x", "alerts_enabled": True, "webhook_secret": "x"})
    ca_listing(1, roommate_gender="any", area="Yonge & Finch", contact_phone="647-555-0142")
    ca_listing(2, roommate_gender="any", city="Toronto", area="Bayview & Sheppard")
    ca_listing(3, roommate_gender="any", city="Toronto", area="Kennedy & Sheppard")
    ca_listing(4, roommate_gender="any", city="Richmond Hill", area="Hillcrest")
    ca_listing(5, roommate_gender="any", city="Toronto", area="King St W", price_amount=1350)


class Recorder:
    """Collects the scenes: what the user did and what the bot sent or edited."""

    def __init__(self, fake: FakeBot) -> None:
        self.fake = fake
        self.scenes: list[dict] = []

    def scene(self, title: str, caption: str, steps: list[tuple[str, str, str]], *, carry: bool = False) -> None:
        """steps: (kind, value, label) — kind is 'msg' (typed text), 'tap' (callback data, label = button text) or 'run'.
        `carry` starts from the last bot message of the previous scene (for taps on it)."""
        import copy

        bubbles: list[dict] = []
        if carry and self.scenes:
            last = [b for b in self.scenes[-1]["bubbles"] if "buttons" in b]
            bubbles = [copy.deepcopy(last[-1])] if last else []
        keyboard = None
        for kind, value, label in steps:
            start = len(self.fake.calls)
            if kind == "msg":
                bubbles.append({"out": True, "text": html.escape(value)})
                handle_update(message(value))
            elif kind == "tap":
                bubbles.append({"tap": label})
                handle_update(tap(value))
            elif kind == "run":
                bubbles.append({"note": label})
                with patch("home.alerts.QUIET_HOURS", None):
                    alerts.notify_pending()
            for method, payload in self.fake.calls[start:]:
                markup = payload.get("reply_markup") or {}
                if method == "sendMessage":
                    bubbles.append({"text": payload["text"], "buttons": markup.get("inline_keyboard") or []})
                    if markup.get("keyboard"):
                        keyboard = markup["keyboard"]
                elif method == "editMessageText":
                    replaced = False
                    for bubble in reversed(bubbles):
                        if "buttons" in bubble and not bubble.get("out"):
                            bubble.update(text=payload["text"], buttons=markup.get("inline_keyboard") or [], edited=True)
                            replaced = True
                            break
                    if not replaced:
                        bubbles.append({"text": payload["text"], "buttons": markup.get("inline_keyboard") or [], "edited": True})
                elif method == "editMessageReplyMarkup":
                    for bubble in reversed(bubbles):
                        if "buttons" in bubble:
                            bubble["buttons"] = markup.get("inline_keyboard") or []
                            break
                elif method == "answerCallbackQuery" and payload.get("text"):
                    bubbles.append({"toast": payload["text"]})
        self.scenes.append({"title": title, "caption": caption, "bubbles": bubbles, "keyboard": keyboard})


CSS = """
body{margin:0;background:#ECEEF1;font-family:Vazirmatn,Tahoma,system-ui,sans-serif;color:#1B232B}
.wrap{display:flex;flex-wrap:wrap;gap:28px;padding:28px;direction:rtl}
.ph{width:390px;display:flex;flex-direction:column;gap:8px}
.ph h2{margin:0;font-size:16px}
.cap{font-size:12.5px;color:#3D4852;line-height:1.7}
.phone{width:390px;min-height:760px;border-radius:40px;border:9px solid #1B232B;background:#fff;overflow:hidden;display:flex;flex-direction:column}
.tgh{height:56px;flex:none;display:flex;align-items:center;gap:10px;padding:0 14px;border-bottom:1px solid #E3E6EA;direction:ltr}
.av{width:36px;height:36px;border-radius:50%;background:#E8590C;color:#fff;font-weight:800;display:flex;align-items:center;justify-content:center}
.nm{font-weight:700;font-size:15px}.sub{font-size:12px;color:#5E6A75}
.chat{flex:1;background:#D3DFE8;padding:12px 10px;display:flex;flex-direction:column;justify-content:flex-end;gap:6px;direction:ltr}
.msg{direction:rtl;text-align:right;max-width:320px;padding:8px 11px 6px;border-radius:14px;font-size:13px;line-height:1.75;box-shadow:0 1px 1px rgba(0,0,0,.08);background:#fff;align-self:flex-start;white-space:normal;word-wrap:break-word}
.msg a{color:#1F6FB8;text-decoration:none}
.out{align-self:flex-end;background:#DFF5C8}
.ed{display:block;text-align:left;font-size:10px;color:#6B7785}
.ikb{align-self:flex-start;width:320px;display:flex;flex-direction:column;gap:4px;direction:rtl}
.ikr{display:flex;gap:4px}
.ib{flex:1;font:inherit;font-size:12px;font-weight:600;color:#fff;background:#4A6273;border-radius:9px;padding:7px 4px;text-align:center}
.ib.url{background:#1F6FB8}
.tap{align-self:flex-end;direction:rtl;font-size:11.5px;color:#fff;background:rgba(27,35,43,.55);border-radius:999px;padding:3px 10px}
.toast{align-self:center;direction:rtl;font-size:11.5px;color:#fff;background:rgba(27,35,43,.8);border-radius:8px;padding:5px 10px}
.note{align-self:center;direction:rtl;font-size:11px;color:#3D4852;background:#F4E7C5;border-radius:8px;padding:4px 10px}
.kb{flex:none;background:#EEF0F3;padding:6px;display:flex;flex-direction:column;gap:6px;border-top:1px solid #D6DBE0;direction:rtl}
.kbr{display:flex;gap:6px}
.kbb{flex:1;font-size:12.5px;font-weight:600;background:#fff;border-radius:8px;padding:9px 4px;text-align:center;box-shadow:0 1px 0 #BFC6CD}
"""


def render(scenes: list[dict]) -> str:
    phones = []
    for scene in scenes:
        parts = []
        for bubble in scene["bubbles"]:
            if bubble.get("tap"):
                parts.append(f'<div class="tap">👆 {html.escape(bubble["tap"])}</div>')
            elif bubble.get("toast"):
                parts.append(f'<div class="toast">{html.escape(bubble["toast"])}</div>')
            elif bubble.get("note"):
                parts.append(f'<div class="note">{html.escape(bubble["note"])}</div>')
            elif bubble.get("out"):
                parts.append(f'<div class="msg out">{bubble["text"]}</div>')
            else:
                edited = '<span class="ed">ویرایش‌شده</span>' if bubble.get("edited") else ""
                parts.append(f'<div class="msg">{bubble["text"].replace(chr(10), "<br>")}{edited}</div>')
                rows = []
                for row in bubble.get("buttons") or []:
                    cells = "".join(
                        f'<div class="ib{" url" if button.get("url") else ""}">{html.escape(button["text"])}</div>' for button in row
                    )
                    rows.append(f'<div class="ikr">{cells}</div>')
                if rows:
                    parts.append(f'<div class="ikb">{"".join(rows)}</div>')
        keyboard = ""
        if scene["keyboard"]:
            rows = "".join(
                '<div class="kbr">' + "".join(f'<div class="kbb">{html.escape(b["text"])}</div>' for b in row) + "</div>"
                for row in scene["keyboard"]
            )
            keyboard = f'<div class="kb">{rows}</div>'
        phones.append(
            f'<div class="ph"><h2>{html.escape(scene["title"])}</h2><div class="phone">'
            '<div class="tgh"><div class="av">چ</div><div><div class="nm">چمدون خانه</div><div class="sub">ربات</div></div></div>'
            f'<div class="chat">{"".join(parts)}</div>{keyboard}</div>'
            f'<div class="cap">{html.escape(scene["caption"])}</div></div>'
        )
    return (
        '<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><title>Housing bot transcript</title>'
        '<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;600;700;800&display=swap" rel="stylesheet">'
        f"<style>{CSS}</style></head><body><div class=\"wrap\">{''.join(phones)}</div></body></html>"
    )


def main() -> None:
    fake = FakeBot()
    with patch("home.bot.call", fake):
        setup_data()
        toronto = HomeChannel.objects.get(city="Toronto")
        rec = Recorder(fake)
        rec.scene("1 · /start", "Hello with the persistent keyboard, then the city is picked once.", [("msg", "/start", ""), ("tap", f"ct:{toronto.pk}", "🇨🇦 تورنتو")])
        rec.scene("2 · ورود از کانال", "From the 🔔 button under a channel post: the city comes from the link.", [("msg", "/start alert_home_ca_toronto", "")])
        alert = HomeAlert.objects.latest("pk")
        rec.scene("3 · سؤال ۱ و ۲", "Type, then budget: a band or a typed number.", [("tap", f"al:a:{alert.pk}:t:r", "🚪 اتاق"), ("msg", "1200", "")])
        rec.scene(
            "4 · سؤال ۳: محله",
            "Chips of the busiest areas with counts, several at once, or «همه‌جا».",
            [("tap", f"al:at:{alert.pk}:0", "نورث یورک"), ("tap", f"al:at:{alert.pk}:2", "ریچموند هیل")],
        )
        rec.scene("5 · ذخیره", "Saved right away; one optional «🎯 دقیق‌ترش کنید».", [("tap", f"al:a:{alert.pk}:w:y", "✅ ذخیره")])
        rec.scene("6 · دقیق‌ترش کنید", "Optional questions, each with «مهم نیست».", [("tap", f"al:rf:{alert.pk}", "🎯 دقیق‌ترش کنید"), ("tap", f"al:a:{alert.pk}:g:x", "مهم نیست (هر کسی)")])
        HomeAlert.objects.filter(pk=alert.pk).update(created_at=timezone.now() - timedelta(days=2))
        from tests.test_home_publish import ca_listing

        HomeListing.objects.exclude(pk=HomeListing.objects.order_by("pk").first().pk).update(created_at=timezone.now() - timedelta(days=5))
        rec.scene("7 · پیام تطبیق", "Which alert and how fresh, the card with the phone, and the buttons.", [("run", "", "job run: alerts step")])
        first = HomeListing.objects.order_by("pk").first()
        rec.scene("8 · 👎", "👎 swaps the buttons for one-tap reasons; the tap is stored as feedback_reason and the buttons come back.", [("tap", f"al:fb:{first.pk}:d", "👎 به کارم نمیاد")], carry=True)
        for number in range(11, 18):
            ca_listing(number, roommate_gender="any", city="Toronto", area="Finch & Bathurst", price_amount=900 + number * 10)
        rec.scene("9 · بیش از ۵ آگهی", "More than five matches at once: one summary.", [("run", "", "job run: 7 new listings")])
        rec.scene("10 · گوش به زنگ‌های من", "Numbered list, ✏️ / ⏸ / 🗑 with the same number.", [("msg", "🔔 گوش به زنگ‌های من", "")])
        rec.scene("11 · حذف", "Delete asks to confirm.", [("tap", f"al:dq:{alert.pk}", "۱ · 🗑")], carry=True)
        for city, country in (("Montreal", "CA"), ("Vancouver", "CA"), ("Berlin", "DE"), ("Hamburg", "DE")):
            HomeAlert.objects.create(telegram_user_id=USER, chat_id=USER, country=country, city=city, currency="EUR" if country == "DE" else "CAD", answered=list(alerts.ALL_FIELDS), complete=True)
        rec.scene("12 · سقف ۵", "The sixth attempt shows the list with pause and delete.", [("tap", "al:new", "➕ گوش به زنگ جدید")])
    out = HERE / "bot.html"
    out.write_text(render(rec.scenes), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
