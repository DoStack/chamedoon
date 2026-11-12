"""@Chamedoon_adminyar_bot: one update in, replies out. Separate token and process from the housing bot (home/bot.py).

Only whitelisted ids get any answer. State (what the bot waits for from each user) lives in memory: a restart
returns everyone to the menu, which is harmless.
"""

from __future__ import annotations

import logging
import urllib.request

from django.utils import timezone

from adminyar import conf, listings
from adminyar.models import AdminRole, AdminUser, AuditLog
from home.bot import BotError, call
from home.models import HomeListing

logger = logging.getLogger("adminyar")

NEXT_PERSON = "📨 نفر بعدی"
GOT_REPLY = "💬 جواب آمد"
NEXT_FORWARD = "🔁 فوروارد بعدی"
PHOTOS = "📷 عکس آگهی"
LISTINGS = "🏠 آگهی‌های یک نفر"
LIMITED = "⛔ اکانت محدود شد"
GROUP_WORK = "👥 کار گروه"
MY_DAY = "📊 امروز من"

MENU_ROWS = [[NEXT_PERSON, GOT_REPLY], [NEXT_FORWARD, PHOTOS], [LISTINGS, LIMITED], [GROUP_WORK, MY_DAY]]
STUBS = {
    NEXT_PERSON: "next_person",
    GOT_REPLY: "got_reply",
    NEXT_FORWARD: "next_forward",
    LIMITED: "limited",
    GROUP_WORK: "group_work",
    MY_DAY: "my_day",
}
KEYBOARD = {"keyboard": [[{"text": label} for label in row] for row in MENU_ROWS], "resize_keyboard": True, "is_persistent": True}

T_HELLO = "سلام {name}. از منوی پایین کار را انتخاب کنید."
T_SOON = "به‌زودی. این بخش هنوز فعال نشده است."
T_ASK_USERNAME = "آیدی تلگرام صاحب آگهی را بفرستید (مثلا @username)."
T_BAD_USERNAME = "این آیدی معتبر نیست. یک آیدی مثل @username بفرستید."
T_NONE = "برای {u} آگهی‌ای پیدا نشد."
T_NONE_PHOTOS = "برای {u} آگهی‌ای پیدا نشد. لینک پست آگهی را بفرستید (مثلا https://t.me/channel/123)."
T_LINK_NOT_FOUND = "آگهی این لینک پیدا نشد. لینک را بررسی کنید یا از منو دوباره شروع کنید."
T_PICK = "کدام آگهی؟"
T_SEND_PHOTOS = "عکس‌های آگهی «{t}» را بفرستید. وقتی تمام شد، از منو کار بعدی را بزنید."
T_PHOTO_RESULT = {
    "ok": "عکس ثبت شد. این آگهی حالا {n} عکس دارد.",
    "full": "این آگهی به سقف ۱۰ عکس رسیده است.",
    "too_big": "این فایل خیلی بزرگ است یا دریافت نشد.",
    "bad_image": "این فایل عکس نیست.",
    "no_storage": "ذخیره عکس فعلا در دسترس نیست. به محمد خبر بدهید.",
    "failed": "ذخیره عکس نشد. دوباره بفرستید.",
}
T_PHOTO_FIRST = "اول از منو «عکس آگهی» را بزنید و آگهی را انتخاب کنید."
T_USE_MENU = "از دکمه‌های منو استفاده کنید."


class Bot:
    def __init__(self, token: str, api=None, fetch=None):
        self.token = token
        self.api = api or (lambda method, payload: call(method, payload, token=token, timeout=45))
        self.fetch = fetch or self._download
        self.state: dict[int, dict] = {}

    # --- Telegram helpers -------------------------------------------------
    def send(self, chat_id: int, text: str, markup: dict | None = None) -> None:
        self.api("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True, "reply_markup": markup or KEYBOARD})

    def _download(self, file_id: str) -> bytes | None:
        try:
            info = self.api("getFile", {"file_id": file_id})
            path = (info or {}).get("file_path")
            if not path or int(info.get("file_size") or 0) > listings.MAX_PHOTO_BYTES:
                return None
            with urllib.request.urlopen(f"https://api.telegram.org/file/bot{self.token}/{path}", timeout=30) as response:
                data = response.read(listings.MAX_PHOTO_BYTES + 1)
            return data if len(data) <= listings.MAX_PHOTO_BYTES else None
        except (BotError, OSError, ValueError):
            return None

    # --- access -----------------------------------------------------------
    def admin_for(self, user: dict) -> AdminUser | None:
        """The AdminUser of an allowed id (created on first contact), or None: no answer at all."""
        user_id = int(user.get("id") or 0)
        if user_id not in conf.allowed_ids():
            return None
        team = conf.TEAM_ACCOUNTS.get(user_id)
        defaults = {
            "name": team[1] if team else (user.get("first_name") or "")[:64],
            "role": AdminRole.OWNER if user_id in conf.owner_ids() else AdminRole.ADMIN,
            "team_account": team[0] if team else "",
        }
        admin, _ = AdminUser.objects.get_or_create(telegram_user_id=user_id, defaults=defaults)
        if not admin.active:
            return None
        admin.last_seen = timezone.now()
        admin.save(update_fields=["last_seen"])
        return admin

    def log(self, admin: AdminUser, action: str, args: dict | None = None, result: str = "") -> None:
        AuditLog.objects.create(telegram_user_id=admin.telegram_user_id, admin_user=admin, action=action, args=args or {}, result=result[:255])

    # --- dispatch ---------------------------------------------------------
    def handle(self, update: dict) -> None:
        message = update.get("message")
        callback = update.get("callback_query")
        user = (message or callback or {}).get("from") or {}
        admin = self.admin_for(user)
        if admin is None:
            return
        if callback:
            self.on_callback(admin, callback)
        elif message and (message.get("chat") or {}).get("type") == "private":
            self.on_message(admin, message)

    def on_message(self, admin: AdminUser, message: dict) -> None:
        chat = message["chat"]["id"]
        uid = admin.telegram_user_id
        text = (message.get("text") or "").strip()
        if text.startswith("/start") or text == "/menu":
            self.state.pop(uid, None)
            self.log(admin, "start")
            self.send(chat, T_HELLO.format(name=admin.name).replace("  ", " "))
            return
        if text in STUBS:
            self.state.pop(uid, None)
            self.log(admin, STUBS[text], result="stub")
            self.send(chat, T_SOON)
            return
        if text in (LISTINGS, PHOTOS):
            mode = "listings" if text == LISTINGS else "photos"
            self.state[uid] = {"wait": f"{mode}_username"}
            self.log(admin, f"{mode}_open")
            self.send(chat, T_ASK_USERNAME)
            return
        document = message.get("document") or {}
        if message.get("photo") or str(document.get("mime_type") or "").startswith("image/"):
            self.on_photo(admin, chat, message)
            return
        wait = self.state.get(uid, {}).get("wait")
        if wait == "listings_username":
            self.show_listings(admin, chat, text)
        elif wait == "photos_username":
            self.pick_listing(admin, chat, text)
        elif wait == "photos_link":
            self.from_link(admin, chat, text)
        else:
            self.send(chat, T_USE_MENU)

    # --- listings -----------------------------------------------------------
    def show_listings(self, admin: AdminUser, chat: int, text: str) -> None:
        username = listings.clean_username(text)
        if not username:
            self.send(chat, T_BAD_USERNAME)
            return
        found = listings.for_username(username)
        self.state.pop(admin.telegram_user_id, None)
        self.log(admin, "listings", {"username": username}, f"{len(found)} found")
        if not found:
            self.send(chat, T_NONE.format(u=f"@{username}"))
            return
        lines = [f"آگهی‌های @{username}:"]
        for index, item in enumerate(found, 1):
            lines.append(f"\n{index}. {listings.title(item)}\nشهر: {item.city or '-'} | قیمت: {listings.price(item)}\n{listings.link(item)}")
        self.send(chat, "\n".join(lines))

    # --- photos ------------------------------------------------------------
    def pick_listing(self, admin: AdminUser, chat: int, text: str) -> None:
        username = listings.clean_username(text)
        if not username:
            self.send(chat, T_BAD_USERNAME)
            return
        found = listings.for_username(username)
        self.log(admin, "photos_lookup", {"username": username}, f"{len(found)} found")
        if not found:
            self.state[admin.telegram_user_id] = {"wait": "photos_link"}
            self.send(chat, T_NONE_PHOTOS.format(u=f"@{username}"))
        elif len(found) == 1:
            self.start_photos(admin, chat, found[0])
        else:
            rows = [
                [{"text": f"{listings.title(item)[:28]} | {item.city or '-'} | {listings.price(item)} | {item.last_seen:%Y-%m-%d}", "callback_data": f"ph:{item.pk}"}]
                for item in found
            ]
            self.state[admin.telegram_user_id] = {"wait": "photos_pick", "ids": [item.pk for item in found]}
            self.send(chat, T_PICK, {"inline_keyboard": rows})

    def from_link(self, admin: AdminUser, chat: int, text: str) -> None:
        listing = listings.from_post_link(text)
        self.log(admin, "photos_link", {"link": text[:200]}, f"listing {listing.pk}" if listing else "not found")
        if listing is None:
            self.send(chat, T_LINK_NOT_FOUND)
            return
        self.start_photos(admin, chat, listing)

    def start_photos(self, admin: AdminUser, chat: int, listing: HomeListing) -> None:
        self.state[admin.telegram_user_id] = {"wait": "photos", "listing": listing.pk}
        self.log(admin, "photos_target", {"listing": listing.pk})
        self.send(chat, T_SEND_PHOTOS.format(t=listings.title(listing)))

    def on_callback(self, admin: AdminUser, callback: dict) -> None:
        data = callback.get("data") or ""
        try:
            self.api("answerCallbackQuery", {"callback_query_id": callback.get("id")})
        except BotError:
            pass
        chat = ((callback.get("message") or {}).get("chat") or {}).get("id") or admin.telegram_user_id
        if not data.startswith("ph:"):
            return
        pk = int(data[3:]) if data[3:].isdigit() else 0
        if pk not in self.state.get(admin.telegram_user_id, {}).get("ids", []):
            self.log(admin, "photos_pick", {"listing": pk}, "stale")
            self.send(chat, T_PHOTO_FIRST)
            return
        listing = HomeListing.objects.filter(pk=pk).first()
        if listing:
            self.start_photos(admin, chat, listing)

    def on_photo(self, admin: AdminUser, chat: int, message: dict) -> None:
        state = self.state.get(admin.telegram_user_id, {})
        if state.get("wait") != "photos":
            self.log(admin, "photo_attach", result="no target")
            self.send(chat, T_PHOTO_FIRST)
            return
        listing = HomeListing.objects.filter(pk=state["listing"]).select_related("post__source").first()
        file_id = message["photo"][-1]["file_id"] if message.get("photo") else message["document"]["file_id"]
        data = self.fetch(file_id) if listing else None
        if listing is None:
            result = "failed"
        elif not data:
            result = "too_big"
        else:
            result = listings.store_photo(listing, data)
        self.log(admin, "photo_attach", {"listing": state["listing"]}, result)
        if listing:
            listing.post.refresh_from_db(fields=["photo_count"])
        self.send(chat, T_PHOTO_RESULT[result].format(n=listing.post.photo_count if listing else 0))
