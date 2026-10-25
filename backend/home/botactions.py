"""Listing buttons of the housing bot: detail, contact (after the scam warning), «اجاره رفت»,
«هنوز موجوده», «🚩 گزارش» with its reasons, and the owner's answer to «هنوز موجوده؟».

Kept out of home/botchat.py on purpose; botchat hands every `hx:` callback to
`handle_callback` here. Callback data:

    hx:d:<id>          detail of a listing (a new message)
    hx:c:<id>          contact: scam warning (full the first time), deposit note, then contact
    hx:t:<id>          «اجاره رفته؟ خبر بده»
    hx:s:<id>          «هنوز موجوده» (crawled listings)
    hx:r:<id>          report reasons; hx:r:<id>:<s|w|d|o> records one
    hx:k:<id>          back from the reasons to the listing buttons
    hx:o:<id>:<y|r>    the owner's answer: yes still there / rented
"""

from __future__ import annotations

import logging

from home import bot, conf, freshness
from home.models import HomeListing, ListingStatus, PostStatus, ReportReason

logger = logging.getLogger(__name__)

PREFIX = "hx:"
OLD_BUTTON = "این دکمه قدیمیه."
REPORT_REASONS = {
    "s": (ReportReason.SCAM, "کلاهبرداری"),
    "w": (ReportReason.WRONG, "اطلاعات اشتباه"),
    "d": (ReportReason.DUPLICATE, "تکراری"),
    "o": (ReportReason.OTHER, "دیگر"),
}
STATE_LINE = {
    ListingStatus.RENTED: "✅ <b>این آگهی اجاره رفته است.</b>",
    ListingStatus.REPORTED: "⚠️ <b>این آگهی گزارش شده و در حال بررسی است.</b>",
    ListingStatus.EXPIRED: "⌛ این آگهی قدیمی است و شاید دیگر موجود نباشد.",
}


def handles(data: str) -> bool:
    return str(data or "").startswith(PREFIX)


def _send(chat_id, text: str, keyboard: list | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    bot.call("sendMessage", payload)


def _edit_text(chat_id, message_id, text: str, keyboard: list | None = None) -> None:
    try:
        bot.call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": keyboard or []},
            },
        )
    except bot.BotError as exc:
        if "not modified" not in exc.description:
            _send(chat_id, text, keyboard)


def _edit_keyboard(chat_id, message_id, keyboard: list) -> None:
    try:
        bot.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": keyboard}})
    except bot.BotError as exc:
        logger.info("editMessageReplyMarkup failed: %s", exc.description)


def _answer(callback_id: str, text: str = "") -> None:
    try:
        bot.call("answerCallbackQuery", {"callback_query_id": callback_id, **({"text": text} if text else {})})
    except bot.BotError as exc:
        logger.info("answerCallbackQuery failed: %s", exc.description)


def _listing(raw_id: str) -> HomeListing | None:
    if not str(raw_id).isdigit():
        return None
    listing = HomeListing.objects.select_related("post", "post__source").filter(pk=int(raw_id)).first()
    if listing is None or listing.post.status != PostStatus.ACCEPTED:
        return None
    if listing.status == ListingStatus.DUPLICATE and listing.canonical_id:
        return _listing(str(listing.canonical_id))
    return listing


def _original_link(listing: HomeListing) -> str:
    source = listing.post.source_url
    return source if source and "/c/" not in source else ""


# ---------------------------------------------------------------------------
# Messages.


def detail_row(listings: list[HomeListing]) -> list[dict]:
    """One numbered detail button per search result, for the results keyboard."""
    from home.publish import fa

    return [{"text": f"🏠 {fa(number)}", "callback_data": f"hx:d:{item.pk}"} for number, item in enumerate(listings, start=1)]


def detail_keyboard(listing: HomeListing) -> list[list[dict]]:
    rows = []
    first = [{"text": "💬 تماس", "callback_data": f"hx:c:{listing.pk}"}]
    link = _original_link(listing)
    if link:
        first.append({"text": "↗ پست اصلی", "url": link})
    rows.append(first)
    if listing.status == ListingStatus.ACTIVE:
        rows.append(
            [
                {"text": "✅ اجاره رفته؟ خبر بده", "callback_data": f"hx:t:{listing.pk}"},
                {"text": "🚩 گزارش", "callback_data": f"hx:r:{listing.pk}"},
            ]
        )
        if not freshness.is_app_listing(listing):
            rows.append([{"text": "👍 هنوز موجوده", "callback_data": f"hx:s:{listing.pk}"}])
    rows.append([{"text": "🏠 جزئیات و عکس‌ها در چمدون خانه", "url": conf.mini_app_link(listing.pk)}])
    return rows


def detail_text(listing: HomeListing) -> str:
    from home.publish import format_listing_text

    state = STATE_LINE.get(listing.status, "")
    head = f"{state}\n\n" if state else ""
    return f"{head}{format_listing_text(listing, limit=3500)}\n\n{freshness.freshness_line(listing)}"


def report_keyboard(listing: HomeListing) -> list[list[dict]]:
    buttons = [{"text": label, "callback_data": f"hx:r:{listing.pk}:{code}"} for code, (_reason, label) in REPORT_REASONS.items()]
    return [buttons[0:2], buttons[2:4], [{"text": "↩️ برگشت", "callback_data": f"hx:k:{listing.pk}"}]]


def contact_text(listing: HomeListing, telegram_id: int) -> tuple[str, list]:
    """Scam warning (full the first time), the provincial deposit note, then every way to reach the advertiser."""
    from home.publish import esc

    warning = freshness.contact_warning(telegram_id)
    lines = [esc(warning["text"])]
    note = freshness.deposit_note(listing)
    if note:
        lines.append(f"⚠️ {esc(note)}")
    lines.append("")
    contact = []
    if listing.contact_telegram:
        contact.append(f"@{esc(listing.contact_telegram)}")
    if listing.contact_phone:
        contact.append(f"<code>{esc(listing.contact_phone)}</code>")
    link = _original_link(listing)
    if contact:
        lines.append("📞 تماس: " + " · ".join(contact))
    if link:
        lines.append(f'↗ <a href="{esc(link)}">پست اصلی</a>')
    if not contact and not link:
        lines.append("راه تماسی در آگهی نیست. جزئیات را در چمدون خانه ببینید.")
    keyboard = []
    if listing.contact_telegram:
        keyboard.append([{"text": "💬 پیام به آگهی‌دهنده", "url": f"https://t.me/{listing.contact_telegram}"}])
    keyboard.append([{"text": "🚩 گزارش", "callback_data": f"hx:r:{listing.pk}"}])
    return "\n".join(lines), keyboard


# ---------------------------------------------------------------------------
# Callbacks.


def handle_callback(callback: dict) -> None:
    data = str(callback.get("data") or "")
    user = callback.get("from") or {}
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id") or user.get("id")
    message_id = message.get("message_id")
    note = ""
    try:
        if not user.get("id") or not chat_id:
            return
        note = _dispatch(data.split(":"), int(user["id"]), chat_id, message_id)
    finally:
        _answer(callback.get("id", ""), note)


def _dispatch(parts: list[str], telegram_id: int, chat_id, message_id) -> str:
    action = parts[1] if len(parts) > 1 else ""
    listing = _listing(parts[2]) if len(parts) > 2 else None
    if listing is None:
        return OLD_BUTTON
    if action == "d" and len(parts) == 3:
        _send(chat_id, detail_text(listing), detail_keyboard(listing))
        return ""
    if action == "k" and len(parts) == 3 and message_id:
        _edit_keyboard(chat_id, message_id, detail_keyboard(listing))
        return ""
    if action == "c" and len(parts) == 3:
        _send(chat_id, *contact_text(listing, telegram_id))
        return ""
    if action == "t" and len(parts) == 3:
        outcome = freshness.mark_taken(listing, telegram_id)
        listing.refresh_from_db()
        if message_id:
            _edit_keyboard(chat_id, message_id, detail_keyboard(listing))
        return {"rented": "ممنون! آگهی «اجاره رفت» علامت خورد.", "noted": "ممنون، ثبت شد ✅"}.get(outcome, "این آگهی دیگر فعال نیست.")
    if action == "s" and len(parts) == 3:
        if not freshness.confirm_available(listing, telegram_id):
            return "این آگهی دیگر فعال نیست."
        return "ممنون! ثبت شد که هنوز موجود است ✅"
    if action == "r" and len(parts) == 3:
        if message_id:
            _edit_keyboard(chat_id, message_id, report_keyboard(listing))
            return "دلیل گزارش را انتخاب کنید."
        _send(chat_id, "🚩 دلیل گزارش را انتخاب کنید:", report_keyboard(listing))
        return ""
    if action == "r" and len(parts) == 4 and parts[3] in REPORT_REASONS:
        freshness.report(listing, REPORT_REASONS[parts[3]][0], telegram_id)
        listing.refresh_from_db()
        if message_id:
            _edit_keyboard(chat_id, message_id, detail_keyboard(listing))
        return "ممنون، گزارش شما ثبت شد."
    if action == "o" and len(parts) == 4 and parts[3] in {"y", "r"}:
        return _owner_answer(listing, parts[3], telegram_id, chat_id, message_id)
    return OLD_BUTTON


def _owner_answer(listing: HomeListing, answer: str, telegram_id: int, chat_id, message_id) -> str:
    if not freshness.is_owner(listing, telegram_id):
        return OLD_BUTTON
    from home.publish import esc

    title = esc(freshness.listing_title(listing))
    if answer == "y":
        if not freshness.confirm_available(listing, telegram_id):
            text = f"آگهی <b>{title}</b> دیگر فعال نیست."
        else:
            text = (
                f"✅ ممنون! آگهی <b>{title}</b> فعال ماند.\n"
                f"{freshness._number(conf.confirm_after_days(), 'fa')} روز دیگر دوباره از شما می‌پرسیم."
            )
    else:
        outcome = freshness.mark_taken(listing, telegram_id)
        if outcome == "rented":
            text = f"🏁 ثبت شد. آگهی <b>{title}</b> «اجاره رفت» علامت خورد و از فهرست‌ها برداشته شد. مبارک باشد!"
        else:
            text = f"آگهی <b>{title}</b> دیگر فعال نیست."
    if message_id:
        _edit_text(chat_id, message_id, text)
    else:
        _send(chat_id, text)
    return "ثبت شد ✅"
