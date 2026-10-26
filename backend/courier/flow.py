"""The courier bot's chat (@Chamed0on_bot), in Persian, «شما» voice. Separate from the housing bot.

/start shows one line about the service and one question: package or traveler. Then four steps
in one message that is edited each time: role → route (3 busy routes or «مسیر دیگه») → date
(month chips, an exact date or «عجله ندارم») → what and how much (مدرک/نامه, تا ۱ کیلو, ۱ تا ۳,
۳+, plus one optional typed line). Saving creates the ItemRequest through the same service the
API uses, then shows the other side from the existing matcher, nearest date first, with
«💬 n» (two-sided intro), «🔔 خبرم کن» (route alert), «📣 کانال» and «⚠️ ممنوعه‌ها».

A persistent reply keyboard has five buttons: 📦 / 🧳 / 🔔 / 📋 / ❓.

`handle_update` returns Bot API actions (see courier/telegram.py) instead of sending them, so the
Django webhook and the polling bot in bot/ run the same flow.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db.models import Count
from django.utils import timezone

from courier import alerts, conf, intros, search, texts
from courier.models import CourierChat, CourierRouteAlert
from courier.telegram import answer, edit, send
from item_requests.locations import resolve_or_create_city
from item_requests.models import City, Country, CourierChannel, ItemRequest, RequestStatus, RequestType
from users.services import upsert_telegram_user
from users.telegram import TelegramIdentity

logger = logging.getLogger(__name__)

_COMMAND = re.compile(r"^/([A-Za-z_]+)(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$", re.DOTALL)
PAGE_SIZE = 3
CITY_BUTTONS = 12
OPEN_DAYS = 90
OLD_BUTTON = "این دکمه قدیمیه."
STEP_TITLES = {
    RequestType.DEMAND: {2: "مسیر بسته؟", 3: "کِی باید برسه؟", 4: "چی و چقدر؟"},
    RequestType.SUPPLY: {2: "مسیر سفرتون؟", 3: "کِی پرواز دارید؟", 4: "چند کیلو جا دارید؟"},
}
COMMANDS = [
    {"command": "start", "description": "شروع: بسته دارم یا مسافرم"},
    {"command": "send", "description": "بسته دارم، دنبال مسافرم"},
    {"command": "carry", "description": "مسافرم، جا دارم"},
    {"command": "alerts", "description": "گوش به زنگ مسیر"},
    {"command": "mine", "description": "درخواست‌ها و سفرهای من"},
    {"command": "help", "description": "راهنما و ممنوعه‌ها"},
    {"command": "cancel", "description": "لغو"},
]


# ---------------------------------------------------------------------------
# Keyboards and small pieces.


def persistent_keyboard() -> dict:
    rows = [
        [texts.KB_DEMAND, texts.KB_SUPPLY],
        [texts.KB_ALERTS, texts.KB_MINE],
        [texts.KB_HELP],
    ]
    return {"keyboard": [[{"text": label} for label in row] for row in rows], "resize_keyboard": True, "is_persistent": True}


def role_keyboard() -> list:
    return [
        [{"text": texts.ROLE_DEMAND, "callback_data": "cr:r:D"}],
        [{"text": texts.ROLE_SUPPLY, "callback_data": "cr:r:S"}],
    ]


def _mark(on: bool, label: str) -> str:
    return f"✓ {label}" if on else label


def _rows(buttons: list, per_row: int) -> list:
    return [buttons[index : index + per_row] for index in range(0, len(buttons), per_row)]


def _mini_app_row(start: str = "courier", label: str = "🗺 مینی‌اپ باربری") -> list:
    link = conf.mini_app_link(start)
    return [[{"text": label, "url": link}]] if link else []


def _role(data: dict) -> str:
    return RequestType.SUPPLY if data.get("role") == RequestType.SUPPLY else RequestType.DEMAND


def _route(data: dict) -> str:
    if not data.get("o") or not data.get("d"):
        return ""
    return texts.route_text(data["o"][0], data["o"][1], [tuple(data["d"])])


def when_label(data: dict) -> str:
    when = data.get("when") or {}
    kind = when.get("k")
    if kind == "o":
        return "عجله ندارم"
    if kind == "x" and when.get("v"):
        return texts.day_month(date.fromisoformat(when["v"]))
    if kind == "m" and when.get("v"):
        year, month = (int(part) for part in when["v"].split("-"))
        name = texts.month_name(date(year, month, 1))
        today = timezone.localdate()
        if (year, month) == (today.year, today.month):
            return f"تا آخر {name}"
        return name
    return ""


def header(data: dict) -> list[str]:
    lines = [f"{texts.BRAND} · {texts.FREE}"]
    if data.get("role"):
        lines.append(f"✅ {texts.ROLE_DEMAND if _role(data) == RequestType.DEMAND else texts.ROLE_SUPPLY}")
    if _route(data):
        lines.append(f"✅ مسیر: <b>{texts.esc(_route(data))}</b>")
    if when_label(data):
        lines.append(f"✅ زمان: {when_label(data)}")
    if data.get("size"):
        lines.append(f"✅ {texts.size_label(data['size'], _role(data))}")
    if data.get("note"):
        lines.append(f"✅ توضیح: {texts.esc(data['note'])}")
    return lines


def step_line(data: dict, step: int) -> str:
    return f"<b>{texts.fa(step)} از ۴ · {STEP_TITLES[_role(data)][step]}</b>"


# ---------------------------------------------------------------------------
# State.


def _chat(telegram_id: int, chat_id: int) -> CourierChat:
    chat, created = CourierChat.objects.get_or_create(telegram_user_id=telegram_id, defaults={"chat_id": chat_id})
    if not created and chat.chat_id != chat_id:
        chat.chat_id = chat_id
        chat.save(update_fields=["chat_id", "updated_at"])
    return chat


def _save(chat: CourierChat, *, waiting: str | None = None) -> None:
    if waiting is not None:
        chat.waiting = waiting
    chat.save(update_fields=["data", "waiting", "updated_at"])


def _user(from_user: dict):
    identity = TelegramIdentity(
        telegram_user_id=int(from_user["id"]),
        telegram_username=(str(from_user.get("username")).strip() or None) if from_user.get("username") else None,
        first_name=(str(from_user.get("first_name") or "").strip() or "Telegram")[:64],
        last_name=(str(from_user.get("last_name")).strip()[:64] or None) if from_user.get("last_name") else None,
    )
    return upsert_telegram_user(identity)


# ---------------------------------------------------------------------------
# Screens: (text, inline keyboard).


def welcome_text(first_name: str = "") -> str:
    hello = f"سلام {texts.esc(first_name)} 👋\n" if first_name else ""
    return (
        f"{hello}{texts.BRAND}\n"
        "مسافرها و فرستنده‌های بسته بین ایران و کانادا / آلمان را به هم وصل می‌کنیم. "
        f"{texts.FREE}."
    )


def role_screen() -> tuple[str, list]:
    return "<b>بسته دارید یا مسافرید؟</b>", role_keyboard()


def route_screen(chat: CourierChat) -> tuple[str, list]:
    data = chat.data
    routes = [list(route) for route in search.frequent_routes()]
    data["routes"] = routes
    buttons = []
    for index, route in enumerate(routes):
        label = texts.route_text(route[0], route[1], [(route[2], route[3])])
        buttons.append([{"text": label, "callback_data": f"cr:rt:{index}"}])
    buttons.append([{"text": "🗺 مسیر دیگه", "callback_data": "cr:rt:x"}])
    buttons.append([{"text": texts.BACK, "callback_data": "cr:bk:1"}])
    return "\n".join([*header(data), "", step_line(data, 2)]), buttons


def country_screen(chat: CourierChat, which: str) -> tuple[str, list]:
    question = "از کدوم کشور؟" if which == "o" else "به کدوم کشور؟"
    countries = list(Country.objects.filter(is_active=True, cities__is_active=True).distinct())
    pinned = {"IR": 0, "CA": 1, "DE": 2}
    countries.sort(key=lambda country: (pinned.get(country.code, 9), country.name_fa))
    buttons = [{"text": country.name_fa, "callback_data": f"cr:c{which}:{country.code}"} for country in countries]
    rows = _rows(buttons, 3) + [[{"text": texts.BACK, "callback_data": "cr:bk:2"}]]
    return "\n".join([*header(chat.data), "", step_line(chat.data, 2), question]), rows


def city_screen(chat: CourierChat, which: str, country_code: str) -> tuple[str, list]:
    country = Country.objects.filter(code=country_code).first()
    usage = (
        ItemRequest.objects.filter(**{f"{'origin' if which == 'o' else 'destination'}_country": country_code})
        .values_list(f"{'origin' if which == 'o' else 'destination'}_city")
        .annotate(n=Count("id"))
    )
    counts = {slug: count for slug, count in usage}
    cities = list(City.objects.filter(country__code=country_code, is_active=True))
    cities.sort(key=lambda city: (-counts.get(city.slug, 0), city.name_fa))
    origin = tuple(chat.data.get("o") or ())
    buttons = [
        {"text": city.name_fa, "callback_data": f"cr:i{which}:{city.pk}"}
        for city in cities[:CITY_BUTTONS]
        if not (which == "d" and origin == (country_code, city.slug))
    ]
    rows = _rows(buttons, 3) + [[{"text": texts.BACK, "callback_data": f"cr:rt:{'x' if which == 'o' else 'd'}"}]]
    name = country.name_fa if country else country_code
    question = f"کدوم شهر {texts.esc(name)}؟ اگر نیست، اسمش را بنویسید."
    return "\n".join([*header(chat.data), "", step_line(chat.data, 2), question]), rows


def date_screen(chat: CourierChat) -> tuple[str, list]:
    data = chat.data
    role = _role(data)
    when = data.get("when") or {}
    months = texts.upcoming_months()
    chips = []
    for index, (key, start, _end) in enumerate(months):
        name = texts.month_name(start)
        label = f"تا آخر {name}" if index == 0 and role == RequestType.DEMAND else name
        chips.append({"text": _mark(when.get("k") == "m" and when.get("v") == key, label), "callback_data": f"cr:m:{key}"})
    exact = {"text": _mark(when.get("k") == "x", "📅 تاریخ دقیق"), "callback_data": "cr:m:x"}
    if role == RequestType.DEMAND:
        rows = [chips[:2], [chips[2], exact, {"text": _mark(when.get("k") == "o", "عجله ندارم"), "callback_data": "cr:m:o"}]]
    else:
        rows = [chips, [exact]]
    rows.append([{"text": texts.BACK, "callback_data": "cr:bk:2"}])
    return "\n".join([*header(data), "", step_line(data, 3)]), rows


def day_screen(chat: CourierChat, month_key: str) -> tuple[str, list]:
    for key, start, end in texts.upcoming_months():
        if key == month_key:
            break
    else:
        return date_screen(chat)
    days = []
    current = start
    while current <= end:
        days.append({"text": texts.fa(current.day), "callback_data": f"cr:dy:{current.isoformat()}"})
        current += timedelta(days=1)
    rows = _rows(days, 7) + [[{"text": texts.BACK, "callback_data": "cr:bk:3"}]]
    question = f"پرواز چندم {texts.month_name(start)} است؟"
    return "\n".join([*header(chat.data), "", step_line(chat.data, 3), question]), rows


def exact_date_screen(chat: CourierChat) -> tuple[str, list]:
    question = "تاریخ را بنویسید، مثلاً «۱۲ دسامبر» یا ۲۰۲۶-۱۲-۱۲."
    return (
        "\n".join([*header(chat.data), "", step_line(chat.data, 3), question]),
        [[{"text": texts.BACK, "callback_data": "cr:bk:3"}]],
    )


def size_screen(chat: CourierChat) -> tuple[str, list]:
    data = chat.data
    role = _role(data)
    chips = [
        {"text": _mark(data.get("size") == code, texts.size_label(code, role)), "callback_data": f"cr:w:{code}"}
        for code in texts.SIZES
    ]
    lines = [*header(data), "", step_line(data, 4), "اگر خواستید یک خط توضیح هم بنویسید (مثلاً «دو جلد کتاب»)."]
    if role == RequestType.DEMAND:
        lines.extend(["", texts.PROHIBITED_HINT])
    rows = _rows(chips, 2) + [
        [{"text": texts.PROHIBITED_BUTTON, "callback_data": "cr:pr"}],
        [{"text": texts.BACK, "callback_data": "cr:bk:3"}],
    ]
    return "\n".join(lines), rows


def confirm_screen(chat: CourierChat) -> tuple[str, list]:
    data = chat.data
    see = "مسافرها" if _role(data) == RequestType.DEMAND else "بسته‌ها"
    lines = [*header(data), "", "<b>همه چیز درسته؟</b> می‌توانید هنوز یک خط توضیح بنویسید."]
    if _role(data) == RequestType.DEMAND:
        lines.extend(["", texts.PROHIBITED_HINT])
    rows = [
        [{"text": f"✅ ثبت و دیدن {see}", "callback_data": "cr:ok"}],
        [{"text": texts.PROHIBITED_BUTTON, "callback_data": "cr:pr"}, {"text": texts.BACK, "callback_data": "cr:bk:4"}],
    ]
    return "\n".join(lines), rows


def channel_name(item: ItemRequest) -> str:
    """«کانادا»: the country channel the request goes to (the side outside Iran), else the main channel."""
    countries = [country for country in [item.origin_country, *[c for c, _city in item.destination_stop_pairs()]] if country != "IR"]
    for code in countries:
        if CourierChannel.objects.filter(country=code, enabled=True).exists():
            name = Country.objects.filter(code=code).values_list("name_fa", flat=True).first()
            return name or code
    return ""


def results_screen(chat: CourierChat, item: ItemRequest, page: int = 0) -> tuple[str, list]:
    found = search.counterparts(item)
    role = item.type
    noun = "مسافر" if role == RequestType.DEMAND else "بسته"
    emoji = "📦" if role == RequestType.DEMAND else "🧳"
    summary = [texts.request_route(item)]
    if when_label(chat.data):
        summary.append(when_label(chat.data))
    if chat.data.get("size"):
        summary.append(texts.size_label(chat.data["size"], role))
    lines = [f"{emoji} {texts.esc(' · '.join(summary))}"]
    shown = found[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    if found:
        lines.append(f"<b>{texts.fa(len(found))} {noun}</b> پیدا شد، نزدیک‌ترین تاریخ اول:")
    else:
        lines.extend(["", f"هنوز {noun}ی برای این مسیر و زمان پیدا نشد. «🔔 خبرم کن» را بزنید تا همین‌جا خبرتان کنیم."])
    for offset, other in enumerate(shown, start=page * PAGE_SIZE + 1):
        lines.extend(["", f"<b>{texts.fa(offset)}.</b> " + search.card_lines(other)[0], *search.card_lines(other)[1:]])
    chat.data["results"] = [other.pk for other in found]
    rows: list = []
    intro_row = [
        {"text": f"💬 {texts.fa(offset)}", "callback_data": f"cr:i:{other.pk}"}
        for offset, other in enumerate(shown, start=page * PAGE_SIZE + 1)
    ]
    if len(found) > (page + 1) * PAGE_SIZE:
        intro_row.append({"text": "بیشتر ›", "callback_data": f"cr:mo:{page + 1}"})
    elif page:
        intro_row.append({"text": "‹ اول", "callback_data": "cr:mo:0"})
    if intro_row:
        rows.append(intro_row)
    rows.append([{"text": texts.NOTIFY_SUPPLY if role == RequestType.DEMAND else texts.NOTIFY_DEMAND, "callback_data": f"cr:al:n:{item.pk}"}])
    channel = channel_name(item)
    label = f"📣 درخواستم رو در کانال {channel} بذار" if channel else "📣 درخواستم رو در کانال بذار"
    rows.append([{"text": label, "callback_data": f"cr:ch:{item.pk}"}])
    rows.append([{"text": texts.PROHIBITED_BUTTON, "callback_data": "cr:pr"}])
    return "\n".join(lines), rows


def alerts_screen(telegram_id: int, note: str = "") -> tuple[str, list]:
    rows_db = list(alerts.user_alerts(telegram_id))
    lines = []
    if note:
        lines.extend([note, ""])
    lines.append(f"🔔 <b>گوش به زنگ مسیر</b> ({texts.fa(len(rows_db))} از {texts.fa(conf.MAX_ALERTS_PER_USER)})")
    if not rows_db:
        lines.append("هنوز گوش به زنگی ندارید. یک مسیر را بگردید و «🔔 خبرم کن» را بزنید.")
    keyboard = []
    for index, alert in enumerate(rows_db, start=1):
        state = "" if alert.active else " · ⏸ خاموش"
        lines.append(f"{texts.fa(index)}. {texts.esc(alerts.summary(alert))}{state}")
        keyboard.append(
            [
                {"text": f"{'⏸' if alert.active else '▶️'} {texts.fa(index)}", "callback_data": f"cr:al:p:{alert.pk}"},
                {"text": f"🗑 {texts.fa(index)}", "callback_data": f"cr:al:d:{alert.pk}"},
            ]
        )
    if not conf.alerts_enabled():
        lines.extend(["", "ℹ️ فرستادن خبرها به‌زودی روشن می‌شود؛ گوش به زنگ‌های شما ذخیره می‌ماند."])
    if len(rows_db) < conf.MAX_ALERTS_PER_USER:
        keyboard.append([{"text": "➕ گوش به زنگ جدید", "callback_data": "cr:al:new"}])
    keyboard.extend(_mini_app_row("courier_alerts", "🗺 گوش به زنگ در مینی‌اپ"))
    return "\n".join(lines), keyboard


def mine_screen(user) -> tuple[str, list]:
    rows = list(ItemRequest.objects.filter(user=user, status=RequestStatus.ACTIVE).order_by("-created_at")[:8])
    lines = ["📋 <b>درخواست‌ها و سفرهای شما</b>"]
    keyboard = []
    if not rows:
        lines.append("هنوز درخواست فعالی ندارید.")
        keyboard.extend(role_keyboard())
    buttons = []
    for index, item in enumerate(rows, start=1):
        emoji = "📦" if item.type == RequestType.DEMAND else "🧳"
        day = search.request_date(item)
        when = f" · {texts.day_month(day)}" if day else ""
        kg = texts.kg_text(item.kg_value())
        lines.append(f"{texts.fa(index)}. {emoji} {texts.esc(texts.request_route(item))}{when}{' · ' + kg if kg else ''}")
        buttons.append({"text": f"🔎 {texts.fa(index)}", "callback_data": f"cr:rs:{item.pk}"})
    if buttons:
        lines.append("\nبرای دیدن طرف مقابل هر کدام، شماره‌اش را بزنید.")
        keyboard.extend(_rows(buttons, 4))
    keyboard.extend(_mini_app_row("requests", "📋 مدیریت در مینی‌اپ"))
    return "\n".join(lines), keyboard


def help_screen() -> tuple[str, list]:
    lines = [
        "❓ <b>راهنما</b>",
        "📦 <b>بسته دارم</b>: مسیر، زمان و اندازه را بگویید؛ مسافرهای همان مسیر را نزدیک‌ترین تاریخ اول می‌بینید.",
        "🧳 <b>مسافرم</b>: تاریخ پرواز و جای خالی را بگویید؛ بسته‌های همان مسیر را می‌بینید.",
        "💬 <b>معرفی</b>: به طرف مقابل پیام می‌دهیم؛ فقط اگر قبول کند آیدی‌ها رد و بدل می‌شود.",
        f"🔔 <b>گوش به زنگ</b>: تا {texts.fa(conf.MAX_ALERTS_PER_USER)} مسیر؛ مسافر یا بسته‌ی تازه که بیاید خبرتان می‌کنیم.",
        "💬 هزینه را خودتان توافق می‌کنید؛ چمدون کمیسیون نمی‌گیرد.",
        "",
        texts.prohibited_text(),
    ]
    return "\n".join(lines), [*role_keyboard(), *_mini_app_row()]


# ---------------------------------------------------------------------------
# Saving.


def request_payload(data: dict, today: date | None = None) -> dict:
    today = today or timezone.localdate()
    role = _role(data)
    size = data.get("size") or "1"
    _label, package_kg, space_kg, _documents = texts.SIZES[size]
    when = data.get("when") or {}
    payload = {
        "type": role,
        "origin_country": data["o"][0],
        "origin_city": data["o"][1],
        "destination_country": data["d"][0],
        "destination_city": data["d"][1],
        "description": (data.get("note") or "")[:500],
    }
    if role == RequestType.DEMAND:
        payload["item_category_codes"] = texts.DEMAND_CATEGORIES.get(size, texts.DEMAND_PACKAGE_CATEGORIES)
        payload["weight_kg"] = str(package_kg)
        if when.get("k") == "x":
            payload["desired_date"] = when["v"]
        elif when.get("k") == "m":
            year, month = (int(part) for part in when["v"].split("-"))
            payload["desired_date_open"] = True
            payload["date_to"] = texts.month_end(year, month).isoformat()
        else:
            payload["desired_date_open"] = True
            payload["date_to"] = (today + timedelta(days=OPEN_DAYS)).isoformat()
    else:
        flight = when.get("v") if when.get("k") == "x" else None
        if not flight:
            raise ValidationError({"flight_date": "تاریخ پرواز را انتخاب کنید."})
        payload["item_category_codes"] = texts.SUPPLY_CATEGORIES.get(size, texts.SUPPLY_PACKAGE_CATEGORIES)
        payload["capacity_kg"] = str(space_kg)
        payload["flight_date"] = flight
        payload["date_from"] = today.isoformat()
        payload["date_to"] = flight
    return payload


def submit(chat: CourierChat, user) -> tuple[str, list]:
    from item_requests.services import create_item_request

    try:
        item = create_item_request(user, request_payload(chat.data))
    except ValidationError as exc:
        messages = getattr(exc, "messages", None) or [str(exc)]
        return f"ذخیره نشد: {texts.esc(messages[0])}", [[{"text": texts.BACK, "callback_data": "cr:bk:4"}]]
    chat.data["mine"] = item.pk
    _save(chat, waiting="")
    return results_screen(chat, item)


# ---------------------------------------------------------------------------
# Updates.


def handle_update(update: dict) -> list[dict]:
    if update.get("callback_query"):
        return handle_callback(update["callback_query"])
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    if chat.get("type") != "private" or not user.get("id") or user.get("is_bot"):
        return []
    return handle_message(message, int(chat["id"]), user)


def start_role(chat: CourierChat, role: str) -> None:
    chat.data = {"role": role}
    _save(chat, waiting="")


def handle_message(message: dict, chat_id: int, from_user: dict) -> list[dict]:
    text = (message.get("text") or "").strip()
    user = _user(from_user)
    chat = _chat(int(from_user["id"]), chat_id)
    command = _COMMAND.match(text)
    if command:
        name = command.group(1).lower()
        chat.data, chat.waiting = {}, ""
        _save(chat)
        if name in {"send", "carry"}:
            start_role(chat, RequestType.DEMAND if name == "send" else RequestType.SUPPLY)
            return [_route_message(chat, chat_id)]
        if name in {"alerts", "myalerts"}:
            return [send(chat_id, *alerts_screen(user.telegram_user_id))]
        if name in {"mine", "requests"}:
            return [send(chat_id, *mine_screen(user))]
        if name == "help":
            return [send(chat_id, *help_screen())]
        if name == "cancel":
            return [send(chat_id, "باشه، لغو شد.", reply_keyboard=persistent_keyboard())]
        return [
            send(chat_id, welcome_text(from_user.get("first_name") or ""), reply_keyboard=persistent_keyboard()),
            send(chat_id, *role_screen()),
        ]
    if text in texts.KEYBOARD_BUTTONS:
        chat.waiting = ""
        if text in {texts.KB_DEMAND, texts.KB_SUPPLY}:
            start_role(chat, RequestType.DEMAND if text == texts.KB_DEMAND else RequestType.SUPPLY)
            return [_route_message(chat, chat_id)]
        _save(chat)
        if text == texts.KB_ALERTS:
            return [send(chat_id, *alerts_screen(user.telegram_user_id))]
        if text == texts.KB_MINE:
            return [send(chat_id, *mine_screen(user))]
        return [send(chat_id, *help_screen())]
    if chat.waiting == "date" and text:
        day = texts.parse_typed_date(text)
        if day is None:
            return [send(chat_id, "این تاریخ را نفهمیدم. مثل «۱۲ دسامبر» یا ۲۰۲۶-۱۲-۱۲ بنویسید (از امروز تا یک سال بعد).")]
        chat.data["when"] = {"k": "x", "v": day.isoformat()}
        _save(chat, waiting="note")
        return [send(chat_id, *size_screen(chat))]
    if chat.waiting in {"city_o", "city_d"} and text:
        which = chat.waiting[-1]
        country = chat.data.get("pick") or ""
        city = resolve_or_create_city(country, text[:64])
        if city is None:
            return [send(chat_id, "اسم شهر را نفهمیدم؛ دوباره بنویسید یا از دکمه‌ها انتخاب کنید.")]
        return [send(chat_id, *_city_chosen(chat, which, city))]
    if chat.waiting == "note" and text:
        chat.data["note"] = text[:200]
        _save(chat)
        screen = confirm_screen(chat) if chat.data.get("size") else size_screen(chat)
        return [send(chat_id, *screen)]
    return [send(chat_id, *role_screen())]


def _route_message(chat: CourierChat, chat_id: int) -> dict:
    """Step 2 as a new message; route_screen stores the routes it offered, so save after it."""
    screen = route_screen(chat)
    _save(chat)
    return send(chat_id, *screen)


def _city_chosen(chat: CourierChat, which: str, city: City) -> tuple[str, list]:
    pair = [city.country.code, city.slug]
    if which == "o":
        chat.data["o"] = pair
        chat.data.pop("d", None)
        _save(chat, waiting="")
        return country_screen(chat, "d")
    if chat.data.get("o") == pair:
        _save(chat, waiting="city_d")
        return "مقصد باید با مبدا فرق داشته باشد. شهر دیگری انتخاب کنید.", city_screen(chat, "d", city.country.code)[1]
    chat.data["d"] = pair
    _save(chat, waiting="")
    return date_screen(chat)


def handle_callback(callback: dict) -> list[dict]:
    data = str(callback.get("data") or "")
    from_user = callback.get("from") or {}
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id") or from_user.get("id")
    message_id = message.get("message_id")
    callback_id = str(callback.get("id") or "")
    if not data.startswith("cr:") or not from_user.get("id") or not chat_id:
        return [answer(callback_id)] if callback_id else []
    user = _user(from_user)
    chat = _chat(int(from_user["id"]), int(chat_id))
    parts = data.split(":")[1:]
    try:
        toast, screen, others, fresh = _route_callback(parts, chat, user)
    except (ValueError, IndexError, KeyError):
        logger.info("Courier bot: bad callback %s", data)
        return [answer(callback_id, OLD_BUTTON)]
    actions = [answer(callback_id, toast)]
    if screen is not None:
        actions.append(send(chat_id, *screen) if fresh else edit(chat_id, message_id, *screen))
    actions.extend(others)
    return actions


def _route_callback(parts: list[str], chat: CourierChat, user) -> tuple[str, tuple | None, list, bool]:
    """(toast, screen, actions for other chats, send as a new message instead of editing)."""
    kind = parts[0]
    data = chat.data
    if kind == "r":
        start_role(chat, RequestType.DEMAND if parts[1] == "D" else RequestType.SUPPLY)
        screen = route_screen(chat)
        _save(chat)
        return "", screen, [], False
    if kind == "rt":
        if parts[1] == "x":
            _save(chat, waiting="")
            return "", country_screen(chat, "o"), [], False
        if parts[1] == "d":
            _save(chat, waiting="")
            return "", country_screen(chat, "d"), [], False
        route = data.get("routes", [])[int(parts[1])]
        data["o"], data["d"] = [route[0], route[1]], [route[2], route[3]]
        _save(chat, waiting="")
        return "", date_screen(chat), [], False
    if kind in {"co", "cd"}:
        which = kind[1]
        data["pick"] = parts[1].upper()[:2]
        _save(chat, waiting=f"city_{which}")
        return "", city_screen(chat, which, data["pick"]), [], False
    if kind in {"io", "id"}:
        city = City.objects.select_related("country").get(pk=int(parts[1]))
        return "", _city_chosen(chat, kind[1], city), [], False
    if kind == "m":
        if parts[1] == "x":
            _save(chat, waiting="date")
            return "", exact_date_screen(chat), [], False
        if parts[1] == "o":
            data["when"] = {"k": "o"}
            _save(chat, waiting="note")
            return "", size_screen(chat), [], False
        key = parts[1]
        if _role(data) == RequestType.SUPPLY:
            return "", day_screen(chat, key), [], False
        data["when"] = {"k": "m", "v": key}
        _save(chat, waiting="note")
        return "", size_screen(chat), [], False
    if kind == "dy":
        day = date.fromisoformat(parts[1])
        if day < timezone.localdate():
            return "این روز گذشته.", None, [], False
        data["when"] = {"k": "x", "v": day.isoformat()}
        _save(chat, waiting="note")
        return "", size_screen(chat), [], False
    if kind == "w":
        if parts[1] not in texts.SIZES:
            raise ValueError(parts[1])
        data["size"] = parts[1]
        _save(chat, waiting="note")
        return "", confirm_screen(chat), [], False
    if kind == "bk":
        step = int(parts[1])
        _save(chat, waiting="note" if step == 4 else "")
        if step <= 1:
            return "", role_screen(), [], False
        if step == 2:
            return "", route_screen(chat), [], False
        if step == 3:
            return "", date_screen(chat), [], False
        return "", size_screen(chat), [], False
    if kind == "ok":
        if not (data.get("o") and data.get("d") and data.get("when") and data.get("size")):
            return "چند قدم مانده.", role_screen() if not data.get("role") else route_screen(chat), [], False
        return "", submit(chat, user), [], False
    if kind == "mo":
        item = _own_request(user, data.get("mine"))
        if item is None:
            return OLD_BUTTON, None, [], False
        screen = results_screen(chat, item, max(0, int(parts[1])))
        _save(chat)
        return "", screen, [], False
    if kind == "rs":
        item = _own_request(user, parts[1])
        if item is None:
            return OLD_BUTTON, None, [], False
        chat.data = {"role": item.type, "mine": item.pk}
        screen = results_screen(chat, item)
        _save(chat, waiting="")
        return "", screen, [], True
    if kind == "i":
        target = ItemRequest.objects.select_related("user").filter(pk=int(parts[1])).first()
        if target is None:
            return OLD_BUTTON, None, [], False
        reply = intros.request_intro(user, target)
        return reply.toast, (reply.text, reply.keyboard), reply.others, True
    if kind in {"ia", "in"}:
        reply = intros.respond(int(parts[1]), user, accept=kind == "ia")
        return reply.toast, (reply.text, reply.keyboard), reply.others, False
    if kind == "pr":
        return "", (texts.prohibited_text(), []), [], True
    if kind == "ch":
        return "", channel_screen(_own_request(user, parts[1])), [], True
    if kind == "al":
        return _alert_callback(parts[1:], chat, user)
    raise ValueError(kind)


def _own_request(user, raw_id) -> ItemRequest | None:
    try:
        pk = int(raw_id or 0)
    except (TypeError, ValueError):
        return None
    return ItemRequest.objects.filter(pk=pk, user=user).first()


def channel_screen(item: ItemRequest | None) -> tuple[str, list]:
    from notifications.channel import channel_enabled

    if item is None:
        return OLD_BUTTON, []
    if not channel_enabled():
        return "📣 انتشار در کانال فعلاً خاموش است. درخواست‌تان در ربات و مینی‌اپ چمدون دیده می‌شود.", []
    channel = channel_name(item)
    where = f"کانال اصلی چمدون و کانال {channel}" if channel else "کانال اصلی چمدون"
    text = f"📣 درخواست شما خودکار در {where} منتشر می‌شود؛ بدون نام و آیدی شما. {texts.PROHIBITED_HINT}"
    from django.conf import settings

    username = (getattr(settings, "TELEGRAM_CHANNEL_USERNAME", "") or "").lstrip("@")
    rows = [[{"text": "📣 کانال چمدون", "url": f"https://t.me/{username}"}]] if username else []
    for code in item.route_countries():
        row = CourierChannel.objects.filter(country=code, enabled=True).exclude(username="").first()
        if row is not None:
            rows.append([{"text": f"📣 کانال {texts.esc(channel or code)}", "url": f"https://t.me/{row.username}"}])
            break
    return text, rows


def _alert_callback(parts: list[str], chat: CourierChat, user) -> tuple[str, tuple | None, list, bool]:
    action = parts[0]
    telegram_id = user.telegram_user_id
    if action == "new":
        chat.data, chat.waiting = {}, ""
        _save(chat)
        return "", ("🔔 گوش به زنگ: اول بگویید بسته دارید یا مسافرید، بعد مسیر را بگردید و «🔔 خبرم کن» را بزنید.", role_keyboard()), [], True
    if action == "n":
        item = _own_request(user, parts[1])
        if item is None:
            return OLD_BUTTON, None, [], False
        try:
            alert, created = alerts.alert_from_request(item, telegram_user_id=telegram_id, chat_id=chat.chat_id)
        except alerts.AlertCapError:
            note = f"حداکثر {texts.fa(conf.MAX_ALERTS_PER_USER)} گوش به زنگ دارید. یکی را حذف کنید:"
            return "", alerts_screen(telegram_id, note), [], True
        note = f"🔔 گوش به زنگ {'روشن شد' if created else 'دوباره روشن شد'}: {texts.esc(alerts.summary(alert))}"
        return "ذخیره شد", alerts_screen(telegram_id, note), [], True
    alert = CourierRouteAlert.objects.filter(pk=int(parts[1]), telegram_user_id=telegram_id).first()
    if alert is None:
        return OLD_BUTTON, alerts_screen(telegram_id), [], False
    if action == "p":
        alert.active = not alert.active
        alert.save(update_fields=["active"])
        note = "▶️ دوباره روشن شد." if alert.active else "⏸ خاموش شد. هر وقت خواستید روشنش کنید."
        return "", alerts_screen(telegram_id, note), [], False
    if action == "d":
        alert.delete()
        return "حذف شد", alerts_screen(telegram_id, "🗑 حذف شد."), [], False
    raise ValueError(action)
