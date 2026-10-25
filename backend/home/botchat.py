"""The housing bot's chat: «گوش به زنگ» and search without the Mini App, in Persian, «شما» voice.

Commands: /start (menu, or `alert_home_<cc>_<city>` from a channel post), /alerts (/myalerts),
/search, /help, /stop, /cancel. A persistent reply keyboard (🔎 / 🔔 / ➕ / ✈️ / 📍 city / ❓)
is sent with /start and when the city changes. The city comes from the deep link or one pick,
and is remembered (`HomeChatState.channel`).

Alert setup is three questions: type (room, own place or any), budget (bands of the city's
currency, or a typed number) and neighbourhoods (chips of the city's busiest areas with counts,
several at once, or «همه‌جا»). Then it is saved and the confirmation offers one optional
«🎯 دقیق‌ترش کنید» that asks the rest: who is looking, rooms or size (own places only), term,
and Anmeldung (Germany only), each with «مهم نیست». The alert row is made when the city is
known and each answer is saved as it comes (`HomeAlert.answered`): a field never answered is
strict, so only listings that state it match (see home.alerts.matches). A user has at most
MAX_ALERTS_PER_USER switched-on alerts; the next attempt shows the list with pause and delete.

Free-text state: `HomeChatState` while a typed budget is welcome, `HomeAlert.awaiting_area`
while the bot waits for neighbourhoods.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from home import bot, conf
from home.alerts import (
    ALL_FIELDS,
    BUDGET_BANDS,
    CURRENCY_BY_COUNTRY,
    FEEDBACK_REASONS,
    GENDER_CHOICES,
    MAIN_FIELDS,
    MAX_ALERTS_PER_USER,
    PAUSE_DAYS,
    TERM_CHOICES,
    TYPE_CHOICES,
    active_count,
    alert_keyboard,
    alert_number,
    city_fa,
    city_slug,
    city_zone,
    common_areas,
    find_user,
    is_paused,
    metro_channel,
    next_question,
    next_refine,
    reason_keyboard,
    refine_fields,
    search,
    summary,
    user_alerts,
)
from home.models import AlertTerm, HomeAlert, HomeAlertSent, HomeChannel, HomeChatState, HomeListing
from home.places import split_areas
from home.publish import CURRENCY_SYMBOL, TYPE_FA, esc, fa, money, persian_date

LATIN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

logger = logging.getLogger(__name__)

PAGE_SIZE = 5
UNSET = "_"
DONT_CARE = "مهم نیست"
EVERYWHERE = "📍 همه‌جا"
OLD_BUTTON = "این دکمه قدیمیه."
AREA_CHIPS = 8
_COMMAND = re.compile(r"^/([A-Za-z_]+)(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$", re.DOTALL)
# Rooms or size, one answer: r<n> = at least n rooms, s<n> = at least n m², x = no filter.
SIZE_CHOICES = {"r2": "۲+ اتاق", "r3": "۳+ اتاق", "r4": "۴+ اتاق", "s40": "۴۰+ متر", "s60": "۶۰+ متر", "s80": "۸۰+ متر"}
# Answer codes in the callback data <-> field names in HomeAlert.answered.
FIELD_OF = {"t": "type", "b": "budget", "w": "area", "g": "gender", "z": "rooms", "m": "term", "d": "registration"}
CODE_OF = {field: code for code, field in FIELD_OF.items()}
REFINE_NAMES = {"gender": "برای چه کسی", "rooms": "اتاق یا متراژ", "term": "مدت", "registration": "ملد"}

# The persistent reply keyboard (Main board). The city button carries the city's name.
KB_SEARCH = "🔎 جستجوی خانه"
KB_ALERTS = "🔔 گوش به زنگ‌های من"
KB_POST = "➕ ثبت آگهی"
KB_COURIER = "✈️ باربری"
KB_HELP = "❓ راهنما"
KB_CITY_WORD = "تغییر شهر"
KB_PICK_CITY = "📍 انتخاب شهر"

COMMANDS = [
    {"command": "start", "description": "منو و گوش به زنگ جدید"},
    {"command": "alerts", "description": "گوش به زنگ‌های من"},
    {"command": "search", "description": "جستجوی آگهی‌ها"},
    {"command": "help", "description": "راهنما"},
    {"command": "stop", "description": "توقف همه‌ی پیام‌ها"},
]

HELP_TEXT = (
    "❓ <b>راهنما</b>\n"
    f"🔔 <b>گوش به زنگ</b>: سه سؤال (نوع، بودجه، محله)، بعد هر آگهی تازه که بخوره همون لحظه اینجا میاد. تا {fa(MAX_ALERTS_PER_USER)} گوش به زنگ.\n"
    "🌙 از ۱۱ شب تا ۸ صبح (به وقت شهرتون) پیامی نمی‌فرستم؛ آگهی‌های شب ساعت ۸ صبح می‌رسه.\n"
    "🔕 با /stop همه خاموش می‌شه.\n"
    "⚠️ قبل از دیدن خانه پولی واریز نکنید. ما آگهی‌ها رو جمع می‌کنیم، صاحبشون رو تأیید نکرده‌ایم."
)


# ---------------------------------------------------------------------------
# Telegram helpers. Every reply goes through home.bot.call, so tests mock one function.


def send(chat_id, text: str, keyboard: list | None = None, *, reply_keyboard: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    elif reply_keyboard:
        payload["reply_markup"] = reply_keyboard
    bot.call("sendMessage", payload)


def edit(chat_id, message_id, text: str, keyboard: list | None = None) -> None:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard or []},
    }
    try:
        bot.call("editMessageText", payload)
    except bot.BotError as exc:
        if "not modified" not in exc.description:
            # The message is too old or gone: send a fresh one instead.
            send(chat_id, text, keyboard)


def edit_markup(chat_id, message_id, markup: dict) -> None:
    if not message_id:
        return
    try:
        bot.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": markup})
    except bot.BotError as exc:
        logger.info("editMessageReplyMarkup failed: %s", exc.description)


def answer(callback_id: str, text: str = "") -> None:
    try:
        bot.call("answerCallbackQuery", {"callback_query_id": callback_id, **({"text": text} if text else {})})
    except bot.BotError as exc:
        logger.info("answerCallbackQuery failed: %s", exc.description)


# ---------------------------------------------------------------------------
# Cities, remembered once picked.


def cities() -> list[HomeChannel]:
    return list(HomeChannel.objects.filter(enabled=True).exclude(city="").order_by("country", "id"))


def city_by_slug(country: str, slug: str) -> HomeChannel | None:
    for channel in cities():
        if channel.country.lower() != country.lower():
            continue
        if slug in {city_slug(name) for name in channel.names()}:
            return channel
    return None


def city_rows(prefix: str, *, country: str = "") -> list[list[dict]]:
    flags = {"CA": "🇨🇦", "DE": "🇩🇪"}
    buttons = [
        {"text": f"{flags.get(channel.country, '')} {city_fa(channel.city)}".strip(), "callback_data": prefix.format(ch=channel.pk)}
        for channel in cities()
        if not country or channel.country == country
    ]
    return [buttons[index : index + 3] for index in range(0, len(buttons), 3)]


def remembered_city(telegram_id: int) -> HomeChannel | None:
    state = HomeChatState.objects.filter(telegram_user_id=telegram_id).select_related("channel").first()
    channel = state.channel if state is not None else None
    return channel if channel is not None and channel.enabled and channel.city else None


def remember_city(telegram_id: int, channel: HomeChannel) -> None:
    HomeChatState.objects.update_or_create(telegram_user_id=telegram_id, defaults={"channel": channel})


def persistent_keyboard(channel: HomeChannel | None) -> dict:
    city_button = f"📍 {city_fa(channel.city)} · {KB_CITY_WORD}" if channel is not None else KB_PICK_CITY
    return {
        "keyboard": [
            [{"text": KB_SEARCH}, {"text": KB_ALERTS}],
            [{"text": KB_POST}, {"text": KB_COURIER}],
            [{"text": city_button}, {"text": KB_HELP}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def hello(first_name: str = "") -> str:
    return f"سلام {esc(first_name)} 👋 " if first_name else "سلام 👋 "


def welcome_text(first_name: str = "") -> str:
    return (
        f"{hello(first_name)}من ربات <b>چمدون خانه</b> هستم.\n"
        "آگهی‌های خانه‌ی گروه‌های فارسی شهرتون رو جمع و مرتب می‌کنم، و با <b>🔔 گوش به زنگ</b> هر آگهی تازه‌ای "
        "که به کارتون بیاد همون لحظه براتون می‌فرستم. رایگان.\n"
        "منو همیشه پایین صفحه‌ست 👇"
    )


def city_question() -> tuple[str, list]:
    keyboard = city_rows("ct:{ch}") + [[{"text": "🏠 باز کردن چمدون خانه", "url": conf.mini_app_link()}]]
    return "کدوم شهر دنبال خونه هستید؟", keyboard


def main_menu(first_name: str = "", telegram_id: int | None = None) -> tuple[str, list]:
    channel = remembered_city(telegram_id) if telegram_id else None
    if channel is None:
        text, keyboard = city_question()
        return f"{hello(first_name)}{text}" if first_name else text, keyboard
    text = (
        f"{hello(first_name) if first_name else ''}📍 شهر: <b>{esc(city_fa(channel.city))}</b>\n\n"
        "🔎 <b>جستجو</b>: آگهی‌های همین حالا\n"
        "🔔 <b>گوش به زنگ</b>: ۳ سؤال کوتاه، بعد هر آگهی تازه که بخوره همون لحظه براتون میاد"
    )
    keyboard = [
        [{"text": "🔔 گوش به زنگ", "callback_data": "al:new"}, {"text": "🔎 همین الان بگردید", "callback_data": f"sr:c:{channel.pk}:0"}],
        [{"text": "📋 گوش به زنگ‌های من", "callback_data": "al:list"}, {"text": f"📍 {KB_CITY_WORD}", "callback_data": "ct:pick"}],
        [{"text": "🏠 باز کردن چمدون خانه", "url": conf.mini_app_link()}],
    ]
    return text, keyboard


# ---------------------------------------------------------------------------
# Alert setup.


def parse_start_payload(payload: str) -> dict | None:
    """alert_home_<cc>[_<city>][_g<f|m|c|x>][_m<max>][_t<r|c|s|a|x>][_s<l|s|t|x>] -> the answers it carries, or None."""
    parts = (payload or "").strip().lower().split("_")
    if len(parts) < 2 or parts[0] != "alert" or parts[1] != "home":
        return None
    found: dict = {"country": "", "channel": None, "answers": {}}
    rest = parts[2:]
    if rest and len(rest[0]) == 2 and rest[0].isalpha():
        found["country"] = rest.pop(0).upper()
    if rest and found["country"] and not re.fullmatch(r"(m\d+|t[rsacx]|s[lstx]|g[fmcx])", rest[0]):
        found["channel"] = city_by_slug(found["country"], rest.pop(0))
    for token in rest:
        if re.fullmatch(r"m\d{1,6}", token):
            found["answers"]["b"] = str(int(token[1:]))
        elif re.fullmatch(r"t[rsacx]", token):
            found["answers"]["t"] = token[1]
        elif re.fullmatch(r"s[lstx]", token):
            found["answers"]["m"] = token[1]
        elif re.fullmatch(r"g[fmcx]", token):
            found["answers"]["g"] = token[1]
    return found


def wait_for(telegram_id: int, what: str, data: dict | None = None) -> None:
    HomeChatState.objects.update_or_create(telegram_user_id=telegram_id, defaults={"waiting": what, "data": data or {}})


def stop_waiting(telegram_id: int) -> None:
    HomeChatState.objects.filter(telegram_user_id=telegram_id).exclude(waiting="").update(waiting="", data={})
    HomeAlert.objects.filter(telegram_user_id=telegram_id, awaiting_area=True).update(awaiting_area=False)


def typed_amount(text: str) -> int | None:
    """A budget typed as text: Persian or Latin digits, commas, a currency word. 50 to 99,999."""
    digits = re.sub(r"[^0-9]", "", (text or "").translate(LATIN_DIGITS))
    if not digits or len(digits) > 5:
        return None
    amount = int(digits)
    return amount if 50 <= amount <= 99999 else None


def new_alert(user: dict, chat_id: int, channel: HomeChannel) -> HomeAlert | None:
    """The alert row is made when the city is known. An unfinished one for the same city starts again.
    None when the user already has MAX_ALERTS_PER_USER switched on."""
    telegram_id = int(user["id"])
    HomeAlert.objects.filter(
        telegram_user_id=telegram_id, country=channel.country, city=channel.city, wanted_listing__isnull=True, complete=False
    ).delete()
    if active_count(telegram_id) >= MAX_ALERTS_PER_USER:
        return None
    remember_city(telegram_id, channel)
    return HomeAlert.objects.create(
        telegram_user_id=telegram_id,
        chat_id=chat_id,
        user=find_user(telegram_id),
        country=channel.country,
        city=channel.city,
        currency=CURRENCY_BY_COUNTRY.get(channel.country, ""),
        answered=[],
    )


def apply_answer(alert: HomeAlert, code: str, value: str) -> bool:
    """Save one answer. Returns False when the value is not a valid answer."""
    answered = set(alert.answered or [])
    if code == "g" and value in GENDER_CHOICES:
        alert.gender = GENDER_CHOICES[value][0]
    elif code == "b" and value.isdigit():
        alert.max_price = Decimal(value) if int(value) else None
    elif code == "t" and value in TYPE_CHOICES:
        alert.property_types = TYPE_CHOICES[value][0]
        if alert.property_types == ["room"]:
            alert.min_rooms = None
            alert.min_size_m2 = None
    elif code == "w" and value in {"x", "y"}:
        if value == "x":
            alert.area = ""
        alert.awaiting_area = False
    elif code == "z" and (value == "x" or value in SIZE_CHOICES):
        alert.min_rooms = Decimal(value[1:]) if value.startswith("r") else None
        alert.min_size_m2 = int(value[1:]) if value.startswith("s") else None
    elif code == "m" and value in TERM_CHOICES:
        alert.term = TERM_CHOICES[value][0]
    elif code == "d" and value in {"y", "x"}:
        alert.needs_registration = value == "y"
    else:
        return False
    answered.add(FIELD_OF[code])
    alert.answered = sorted(answered)
    alert.complete = next_question(alert) == ""
    alert.save()
    return True


def next_screen(alert: HomeAlert, code: str, telegram_id: int) -> tuple[str, list]:
    """After an answer: the next setup question, the next optional one, or the confirmation."""
    field = FIELD_OF.get(code, "")
    if field in MAIN_FIELDS or not field:
        upcoming = next_question(alert)
    else:
        upcoming = next_refine(alert, after=field)
    return question(alert, telegram_id, upcoming) if upcoming else saved_message(alert)


def head_line(alert: HomeAlert) -> str:
    head = f"🔔 گوش به زنگ <b>{esc(city_fa(alert.city))}</b>"
    if set(alert.answered or []) & set(MAIN_FIELDS):
        head += f"\n<i>{esc(summary(alert))}</i>"
    return head


def question(alert: HomeAlert, telegram_id: int | None = None, field: str = "") -> tuple[str, list]:
    """A setup question (the next unanswered one unless `field` names it), or the confirmation when none is left."""
    field = field or next_question(alert)
    if not field:
        return saved_message(alert)
    if field in REFINE_NAMES:
        return refine_question(alert, field)
    head = head_line(alert)
    base = f"al:a:{alert.pk}"
    menu = {"text": "↩️ منو", "callback_data": "mn"}
    if field == "type":
        intro = "" if alert.answered else "\n۳ سؤال کوتاه. بعد هر آگهی تازه که بخوره، همون لحظه اینجا می‌بینیدش."
        room = "🚪 اتاق (WG)" if alert.country == "DE" else "🚪 اتاق"
        rows = [
            [{"text": room, "callback_data": f"{base}:t:r"}],
            [{"text": "🏠 واحد مستقل (استودیو، آپارتمان، بیسمنت)", "callback_data": f"{base}:t:c"}],
            [{"text": f"{DONT_CARE} (هر دو)", "callback_data": f"{base}:t:x"}],
            [menu],
        ]
        return f"{head}{intro}\n\n<b>۱ از ۳</b> · دنبال چه جایی هستید؟", rows
    if field == "budget":
        bands = BUDGET_BANDS.get(alert.currency, [])
        buttons = [{"text": f"تا {money(Decimal(amount), alert.currency)}", "callback_data": f"{base}:b:{amount}"} for amount in bands]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append([{"text": DONT_CARE, "callback_data": f"{base}:b:0"}])
        rows.append([{"text": "‹ برگشت", "callback_data": f"al:e:{alert.pk}:t"}, menu])
        if telegram_id:
            wait_for(telegram_id, "budget", {"alert": alert.pk})
        symbol = CURRENCY_SYMBOL.get(alert.currency, alert.currency)
        return (
            f"{head}\n\n<b>۲ از ۳</b> · سقف اجاره‌ی ماهانه ({esc(symbol)})؟\nعددش رو بنویسید (مثلاً ۱۲۰۰) یا یکی رو بزنید.",
            rows,
        )
    return area_question(alert)


def area_options(alert: HomeAlert) -> list[tuple[str, str, int]]:
    return common_areas(alert.country, alert.city, limit=AREA_CHIPS)


def area_question(alert: HomeAlert) -> tuple[str, list]:
    """Step 3: chips of the city's busiest neighbourhoods (with counts), several at once, typed names, or everywhere."""
    if not alert.awaiting_area:
        alert.awaiting_area = True
        alert.save(update_fields=["awaiting_area"])
    picked = {name.casefold() for name in split_areas(alert.area)}
    options = area_options(alert)
    chips = [
        {
            "text": f"{'✓ ' if value.casefold() in picked else ''}{label} ({fa(count)})",
            "callback_data": f"al:at:{alert.pk}:{index}",
        }
        for index, (label, value, count) in enumerate(options)
    ]
    rows = [chips[index : index + 2] for index in range(0, len(chips), 2)]
    rows.append([{"text": EVERYWHERE, "callback_data": f"al:a:{alert.pk}:w:x"}])
    if alert.area:
        rows.append([{"text": f"✅ ذخیره با {fa(len(split_areas(alert.area)))} محله", "callback_data": f"al:a:{alert.pk}:w:y"}])
    rows.append([{"text": "‹ برگشت", "callback_data": f"al:e:{alert.pk}:b"}, {"text": "↩️ منو", "callback_data": "mn"}])
    hint = "چندتا رو انتخاب کنید، یا اسم محله رو بنویسید" if options else "اسم محله رو بنویسید؛ چندتا رو با ویرگول یا «یا» جدا کنید"
    example = "Yonge & Finch، Richmond Hill" if alert.country == "CA" else "Neukölln یا هاربورگ"
    text = f"{head_line(alert)}\n\n<b>۳ از ۳</b> · کدوم محله‌ها؟\n{hint} (مثلاً {example})."
    if alert.area:
        text += f"\n<i>انتخاب شما: {esc(alert.area)}</i>"
    return text, rows


def toggle_area(alert: HomeAlert, index: int) -> bool:
    options = area_options(alert)
    if not 0 <= index < len(options):
        return False
    value = options[index][1]
    names = split_areas(alert.area)
    if value.casefold() in {name.casefold() for name in names}:
        names = [name for name in names if name.casefold() != value.casefold()]
    else:
        names.append(value)
    joined = ", ".join(names)
    if len(joined) > HomeAlert._meta.get_field("area").max_length:
        return False
    alert.area = joined
    alert.save(update_fields=["area"])
    return True


def refine_question(alert: HomeAlert, field: str) -> tuple[str, list]:
    """The optional questions, each with «مهم نیست» (no filter on that field) and «✅ کافیه، ذخیره»."""
    fields = refine_fields(alert)
    step = fields.index(field) + 1 if field in fields else 1
    head = f"🎯 دقیق‌تر کردن گوش به زنگ {fa(alert_number(alert))}\n<i>{esc(summary(alert))}</i>\n\nسؤال اختیاری {fa(step)} از {fa(len(fields))}"
    base = f"al:a:{alert.pk}"
    skip = {"text": DONT_CARE, "callback_data": f"{base}:{CODE_OF[field]}:x"}
    done = [{"text": "✅ کافیه، ذخیره", "callback_data": f"al:sv:{alert.pk}"}]
    if field == "gender":
        buttons = [{"text": f"👤 {label}", "callback_data": f"{base}:g:{code}"} for code, (_v, label) in GENDER_CHOICES.items() if code != "x"]
        return f"{head} · 👤 برای چه کسی؟", [buttons, [{"text": f"{DONT_CARE} (هر کسی)", "callback_data": f"{base}:g:x"}], done]
    if field == "rooms":
        buttons = [{"text": label, "callback_data": f"{base}:z:{code}"} for code, label in SIZE_CHOICES.items()]
        return f"{head} · 🛏 حداقل چند اتاق یا چند متر؟", [buttons[0:3], buttons[3:6], [skip], done]
    if field == "term":
        buttons = [{"text": label, "callback_data": f"{base}:m:{code}"} for code, (_term, label) in TERM_CHOICES.items() if code != "x"]
        return f"{head} · 📅 چه مدتی؟", [buttons[0:2], buttons[2:], [{"text": f"{DONT_CARE} (بلند یا سابلت)", "callback_data": f"{base}:m:x"}], done]
    return f"{head} · 📝 ملد (Anmeldung) لازم دارید؟", [[{"text": "بله، فقط با ملد", "callback_data": f"{base}:d:y"}, skip], done]


def refine_start(alert: HomeAlert) -> tuple[str, list]:
    fields = refine_fields(alert)
    first = next_refine(alert) or fields[0]
    return refine_question(alert, first)


def save_alert(
    user: dict,
    chat_id: int,
    channel: HomeChannel,
    budget: str,
    type_code: str,
    term_code: str,
    **preferences,
) -> HomeAlert:
    """Make an alert in one call with budget, type, area (everywhere unless `area` is given) and term answered
    (scripts and studies). `preferences` sets gender, min_rooms, min_size_m2, needs_registration or area; each one
    given counts as answered, and `answered` (a list of field names) can name more."""
    alert = new_alert(user, chat_id, channel)
    if alert is None:
        raise ValueError(f"user {user['id']} already has {MAX_ALERTS_PER_USER} alerts switched on")
    apply_answer(alert, "b", "0" if budget in {UNSET, ""} else budget)
    apply_answer(alert, "t", type_code)
    apply_answer(alert, "w", "y")
    apply_answer(alert, "m", term_code)
    extra = set(preferences.pop("answered", []))
    for name, value in preferences.items():
        setattr(alert, name, value)
        if name == "gender":
            extra.add("gender")
        elif name in {"min_rooms", "min_size_m2"}:
            extra.add("rooms")
        elif name == "needs_registration":
            extra.add("registration")
    alert.answered = sorted(set(alert.answered) | extra)
    alert.complete = next_question(alert) == ""
    alert.save()
    return alert


def saved_message(alert: HomeAlert) -> tuple[str, list]:
    """The confirmation (Bot-Alerts board): summary in search order, quiet hours, and one optional «🎯 دقیق‌ترش کنید»."""
    number = alert_number(alert)
    city = city_fa(alert.city)
    lines = [
        f"✅ <b>گوش به زنگ {fa(number)} ذخیره شد</b>",
        "",
        f"🏠 {esc(summary(alert, area=False))}",
        f"📍 {esc(alert.area) if alert.area else 'همه‌جا'}",
        "⚡ همون لحظه",
        "",
        "از الان هر آگهی تازه که بخوره، همین‌جا براتون میاد. چیزی که آگهی نگفته باشه «نامشخص» نوشته می‌شه.",
        f"<i>🌙 ۱۱ شب تا ۸ صبح (به وقت {esc(city)}) بی‌صدا می‌مونم؛ آگهی‌های شب ساعت ۸ صبح می‌رسه.</i>",
    ]
    missing = [REFINE_NAMES[field] for field in refine_fields(alert) if field not in set(alert.answered or [])]
    if missing:
        lines.append(
            f"<i>🎯 هنوز نپرسیده‌ام: {'، '.join(missing)}. تا جواب نداده‌اید، فقط آگهی‌هایی میاد که اینها رو گفته باشن.</i>"
        )
    pk = alert.pk
    keyboard = [
        [{"text": "🎯 دقیق‌ترش کنید", "callback_data": f"al:rf:{pk}"}],
        [{"text": "✏️ ویرایش", "callback_data": f"al:ed:{pk}"}, {"text": "➕ یکی دیگه", "callback_data": "al:new"}],
    ]
    channel = metro_channel(alert.country, alert.city)
    if channel is not None and channel.username:
        keyboard.append([{"text": f"📣 کانال {city}", "url": f"https://t.me/{channel.username}"}])
    keyboard.append(
        [{"text": "🔎 آگهی‌های فعلی", "callback_data": f"sr:a:{pk}:0"}, {"text": "📋 همه‌ی گوش به زنگ‌ها", "callback_data": "al:list"}]
    )
    return "\n".join(lines), keyboard


def edit_menu(alert: HomeAlert) -> tuple[str, list]:
    pk = alert.pk
    text = f"✏️ <b>ویرایش گوش به زنگ {fa(alert_number(alert))}</b>\n<i>{esc(summary(alert))}</i>\n\nکدوم رو عوض کنیم؟"
    keyboard = [
        [{"text": "🏷 نوع", "callback_data": f"al:e:{pk}:t"}, {"text": "💰 بودجه", "callback_data": f"al:e:{pk}:b"}],
        [{"text": "📍 محله", "callback_data": f"al:e:{pk}:w"}, {"text": "🎯 سؤال‌های بیشتر", "callback_data": f"al:rf:{pk}"}],
        [{"text": "‹ برگشت به فهرست", "callback_data": "al:list"}],
    ]
    return text, keyboard


def status_line(alert: HomeAlert) -> str:
    if not alert.active:
        return "⏸ خاموش"
    if is_paused(alert):
        local = alert.paused_until.astimezone(city_zone(alert.country, alert.city))
        return f"⏸ مکث تا {persian_date(local)}"
    if next_question(alert):
        return "✏️ نیمه‌کاره: فقط آگهی‌هایی که همه‌چیز رو گفتن"
    week = len(search(alert, days=7))
    return f"⚡ همون لحظه · {fa(week)} آگهی این هفته"


def alerts_message(telegram_id: int, note: str = "") -> tuple[str, list]:
    """My alerts (Bot-Alerts board): numbered list, ✏️ / ⏸ / 🗑 per alert with the same number."""
    alerts = list(user_alerts(telegram_id))
    prefix = f"{note}\n\n" if note else ""
    if not alerts:
        channel = remembered_city(telegram_id)
        if channel is not None:
            keyboard = [[{"text": f"🔔 گوش به زنگ {city_fa(channel.city)}", "callback_data": "al:new"}]]
            keyboard += [[{"text": f"📍 {KB_CITY_WORD}", "callback_data": "ct:pick"}, {"text": "↩️ منو", "callback_data": "mn"}]]
            return f"{prefix}هنوز گوش به زنگی ندارید.", keyboard
        return f"{prefix}هنوز گوش به زنگی ندارید. کدوم شهر؟", city_rows("al:n:{ch}") + [[{"text": "↩️ منو", "callback_data": "mn"}]]
    lines = [f"🔔 <b>گوش به زنگ‌های شما</b> ({fa(active_count(telegram_id))} از {fa(MAX_ALERTS_PER_USER)})"]
    keyboard = []
    for number, alert in enumerate(alerts, start=1):
        n = fa(number)
        source = " (درخواست شما در چمدون خانه)" if alert.wanted_listing_id else ""
        lines.append(f"\n<b>{n}.</b> 🏠 {esc(summary(alert))}{source}")
        lines.append(f"<i>{status_line(alert)}</i>")
        if alert.active and not is_paused(alert):
            toggle = {"text": f"{n} · ⏸", "callback_data": f"al:off:{alert.pk}:l"}
        else:
            toggle = {"text": f"{n} · ▶️ ادامه", "callback_data": f"al:on:{alert.pk}:l"}
        first = (
            {"text": f"{n} · 🔎", "callback_data": f"sr:a:{alert.pk}:0"}
            if alert.wanted_listing_id
            else {"text": f"{n} · ✏️", "callback_data": f"al:ed:{alert.pk}"}
        )
        keyboard.append([first, toggle, {"text": f"{n} · 🗑", "callback_data": f"al:dq:{alert.pk}"}])
    keyboard.append(
        [{"text": "➕ گوش به زنگ جدید", "callback_data": "al:new"}, {"text": "مدیریت در مینی‌اپ", "url": conf.mini_app_link()}]
    )
    return prefix + "\n".join(lines), keyboard


def cap_message(telegram_id: int) -> tuple[str, list]:
    note = f"✋ {fa(MAX_ALERTS_PER_USER)} گوش به زنگ روشن دارید که سقفشه. برای یکی تازه، اول یکی رو مکث یا حذف کنید:"
    return alerts_message(telegram_id, note)


def delete_question(alert: HomeAlert) -> tuple[str, list]:
    text = f"🗑 گوش به زنگ {fa(alert_number(alert))} حذف بشه؟\n<i>{esc(summary(alert))}</i>"
    keyboard = [[{"text": "🗑 بله، حذف", "callback_data": f"al:del:{alert.pk}"}, {"text": "↩️ نه، برگردیم", "callback_data": "al:list"}]]
    return text, keyboard


def contact_message(listing: HomeListing) -> str:
    """Contact details are shown as they are, phone included (Mohammad, 2026-10-25), with the safety line."""
    lines = ["💬 <b>تماس با آگهی‌دهنده</b>"]
    if listing.contact_phone:
        lines.append(f"📞 {esc(listing.contact_phone)}")
    if listing.contact_telegram:
        lines.append(f"✈️ @{esc(listing.contact_telegram.lstrip('@'))}")
    source = listing.post.source_url if listing.post_id else ""
    if source and "/c/" not in source:
        lines.append(f'↗ <a href="{esc(source)}">پست اصلی</a>')
    if len(lines) == 1:
        lines.append(f'راه تماسی در آگهی نیست؛ <a href="{esc(conf.mini_app_link(listing.pk))}">جزئیات</a> رو ببینید.')
    lines.append("⚠️ قبل از دیدن خانه پولی واریز نکنید.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Search in chat.


def result_lines(listing: HomeListing) -> str:
    place = " · ".join(part for part in (listing.city, listing.area) if part)
    price = money(listing.price_amount, listing.price_currency) if listing.price_amount else "قیمت: نامشخص (بپرسید)"
    links = []
    source = listing.post.source_url
    if source and "/c/" not in source:
        links.append(f'<a href="{esc(source)}">پست اصلی</a>')
    if listing.contact_telegram:
        links.append(f"@{esc(listing.contact_telegram)}")
    if listing.contact_phone:
        links.append(f"📞 {esc(listing.contact_phone)}")
    links.append(f'<a href="{esc(conf.mini_app_link(listing.pk))}">جزئیات</a>')
    return f"🏠 <b>{esc(TYPE_FA.get(listing.property_type, 'ملک'))} · {esc(place)}</b>\n💶 {esc(price)}\n{' · '.join(links)}"


def results_message(criteria: HomeAlert, page: int, callback_base: str) -> tuple[str, list]:
    found = search(criteria)
    pages = max(1, (len(found) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    head = f"🔎 <b>{esc(summary(criteria))}</b>"
    if not found:
        text = f"{head}\n\nفعلاً آگهی‌ای پیدا نشد. با گوش به زنگ، آگهی بعدی رو همون لحظه خبرتون می‌کنم."
        keyboard = [[{"text": "↩️ منو", "callback_data": "mn"}]]
        if not criteria.pk:
            keyboard.insert(0, [{"text": "🔔 گوش به زنگ همین شهر", "callback_data": f"al:n:{callback_base.split(':')[-1]}"}])
        return text, keyboard
    shown = found[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    body = "\n\n".join(result_lines(listing) for listing in shown)
    text = f"{head}\n{fa(len(found))} آگهی · صفحه‌ی {fa(page + 1)} از {fa(pages)}\n\n{body}"
    nav = []
    if page > 0:
        nav.append({"text": "▶️ قبلی", "callback_data": f"{callback_base}:{page - 1}"})
    if page + 1 < pages:
        nav.append({"text": "بعدی ◀️", "callback_data": f"{callback_base}:{page + 1}"})
    keyboard = [nav] if nav else []
    if not criteria.pk:
        keyboard.append([{"text": "🔔 گوش به زنگ همین جستجو", "callback_data": f"al:n:{callback_base.split(':')[-1]}"}])
    keyboard.append([{"text": "↩️ منو", "callback_data": "mn"}])
    return text, keyboard


def city_criteria(telegram_id: int, chat_id: int, channel: HomeChannel) -> HomeAlert:
    return HomeAlert(
        telegram_user_id=telegram_id,
        chat_id=chat_id,
        country=channel.country,
        city=channel.city,
        term=AlertTerm.ANY,
        answered=list(ALL_FIELDS),
    )


def search_menu(telegram_id: int) -> tuple[str, list]:
    keyboard = city_rows("sr:c:{ch}:0")
    for alert in HomeAlert.objects.filter(telegram_user_id=telegram_id, active=True).order_by("created_at")[:5]:
        keyboard.append([{"text": f"🔔 {summary(alert)}"[:60], "callback_data": f"sr:a:{alert.pk}:0"}])
    keyboard.append([{"text": "↩️ منو", "callback_data": "mn"}])
    return "🔎 کدوم شهر؟", keyboard


# ---------------------------------------------------------------------------
# Updates.


def handle_update(update: dict) -> None:
    if update.get("callback_query"):
        handle_callback(update["callback_query"])
        return
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    if chat.get("type") != "private" or not user.get("id"):
        return
    handle_message(message, int(chat["id"]), user)


def keyboard_button(text: str, chat_id: int, user: dict) -> bool:
    """A tap on the persistent reply keyboard arrives as plain text."""
    telegram_id = int(user["id"])
    if text not in {KB_SEARCH, KB_ALERTS, KB_POST, KB_COURIER, KB_HELP, KB_PICK_CITY} and KB_CITY_WORD not in text:
        return False
    stop_waiting(telegram_id)
    if text == KB_SEARCH:
        channel = remembered_city(telegram_id)
        if channel is None:
            send(chat_id, *search_menu(telegram_id))
        else:
            send(chat_id, *results_message(city_criteria(telegram_id, chat_id, channel), 0, f"sr:c:{channel.pk}"))
    elif text == KB_ALERTS:
        send(chat_id, *alerts_message(telegram_id))
    elif text == KB_POST:
        send(
            chat_id,
            "➕ آگهی‌تون رو در چمدون خانه ثبت کنید؛ رایگانه و بعد از یک بررسی کوتاه در کانال شهر منتشر می‌شه.",
            [[{"text": "➕ ثبت آگهی رایگان", "url": conf.mini_app_link()}]],
        )
    elif text == KB_COURIER:
        username = (getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").lstrip("@")
        rows = [[{"text": "✈️ ربات باربری چمدون", "url": f"https://t.me/{username}"}]] if username else None
        send(chat_id, "✈️ باربری (فرستادن بار با مسافر) ربات جداگانه‌ی خودش رو داره.", rows)
    elif text == KB_HELP:
        send(chat_id, HELP_TEXT, [[{"text": "🔔 گوش به زنگ", "callback_data": "al:new"}]])
    else:
        send(chat_id, "📍 کدوم شهر؟", city_rows("ct:{ch}"))
    return True


def handle_message(message: dict, chat_id: int, user: dict) -> None:
    text = (message.get("text") or "").strip()
    telegram_id = int(user["id"])
    command = _COMMAND.match(text)
    if command:
        name, arg = command.group(1).lower(), (command.group(2) or "").strip()
        stop_waiting(telegram_id)
        if name == "start":
            start(chat_id, user, arg)
        elif name in {"alerts", "myalerts"}:
            send(chat_id, *alerts_message(telegram_id))
        elif name == "search":
            send(chat_id, *search_menu(telegram_id))
        elif name == "help":
            send(chat_id, HELP_TEXT)
        elif name == "stop":
            stopped = HomeAlert.objects.filter(telegram_user_id=telegram_id, active=True).update(active=False)
            send(
                chat_id,
                "🔕 همه‌ی گوش به زنگ‌ها خاموش شد. دیگه پیامی نمی‌فرستم." if stopped else "گوش به زنگ روشنی نداشتید.",
                [[{"text": "📋 گوش به زنگ‌های من", "callback_data": "al:list"}]],
            )
        elif name == "cancel":
            send(chat_id, "باشه، لغو شد.")
        else:
            send(chat_id, *main_menu(user.get("first_name") or "", telegram_id))
        return
    if keyboard_button(text, chat_id, user):
        return
    waiting = HomeAlert.objects.filter(telegram_user_id=telegram_id, awaiting_area=True).order_by("-created_at").first()
    if waiting is not None and text:
        waiting.area = text[:96]
        waiting.awaiting_area = False
        waiting.answered = sorted(set(waiting.answered or []) | {"area"})
        waiting.complete = next_question(waiting) == ""
        waiting.save(update_fields=["area", "awaiting_area", "answered", "complete"])
        send(chat_id, *next_screen(waiting, "w", telegram_id))
        return
    state = HomeChatState.objects.filter(telegram_user_id=telegram_id, waiting="budget").first()
    if state is not None and text:
        alert = HomeAlert.objects.filter(pk=state.data.get("alert") or 0, telegram_user_id=telegram_id).first()
        amount = typed_amount(text)
        if alert is not None and amount is None:
            send(chat_id, f"یه عدد بنویسید، مثلاً ۱۱۰۰. یا یکی از دکمه‌ها رو بزنید («{DONT_CARE}» هم هست).")
            return
        stop_waiting(telegram_id)
        if alert is not None:
            apply_answer(alert, "b", str(amount))
            send(chat_id, *next_screen(alert, "b", telegram_id))
            return
    send(chat_id, *main_menu(user.get("first_name") or "", telegram_id))


def start(chat_id: int, user: dict, payload: str) -> None:
    telegram_id = int(user["id"])
    first_name = user.get("first_name") or ""
    found = parse_start_payload(payload)
    if found is None:
        send(chat_id, welcome_text(first_name), reply_keyboard=persistent_keyboard(remembered_city(telegram_id)))
        send(chat_id, *main_menu("", telegram_id))
        return
    channel = found["channel"]
    if not HomeChatState.objects.filter(telegram_user_id=telegram_id).exists():
        # First visit from a channel post: the menu keyboard comes with a short hello.
        send(chat_id, welcome_text(first_name), reply_keyboard=persistent_keyboard(channel))
    if channel is None:
        rows = city_rows("al:n:{ch}", country=found["country"]) or city_rows("al:n:{ch}")
        send(chat_id, "🔔 گوش به زنگ: کدوم شهر؟", rows + [[{"text": "↩️ منو", "callback_data": "mn"}]])
        return
    alert = new_alert(user, chat_id, channel)
    if alert is None:
        send(chat_id, *cap_message(telegram_id))
        return
    for code, value in found["answers"].items():
        apply_answer(alert, code, value)
    send(chat_id, *question(alert, telegram_id))


# Buttons under a match DM: they must not cancel a budget or neighbourhood being typed.
_DM_BUTTONS = ("al:seen:", "al:fb:", "al:fr:", "al:ct:", "al:pw:")


def handle_callback(callback: dict) -> None:
    data = str(callback.get("data") or "")
    user = callback.get("from") or {}
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id") or user.get("id")
    message_id = message.get("message_id")
    if not user.get("id") or not chat_id:
        answer(callback.get("id", ""))
        return
    telegram_id = int(user["id"])
    parts = data.split(":")
    note = ""

    def reply(text: str, keyboard: list | None = None) -> None:
        if message_id:
            edit(chat_id, message_id, text, keyboard)
        else:
            send(chat_id, text, keyboard)

    if not data.startswith(_DM_BUTTONS):
        stop_waiting(telegram_id)
    try:
        if data == "mn":
            reply(*main_menu(user.get("first_name") or "", telegram_id))
        elif data == "ct:pick":
            reply("📍 کدوم شهر؟", city_rows("ct:{ch}"))
        elif parts[0] == "ct" and len(parts) == 2 and parts[1].isdigit():
            channel = HomeChannel.objects.filter(pk=int(parts[1]), enabled=True).exclude(city="").first()
            if channel is None:
                note = OLD_BUTTON
            else:
                remember_city(telegram_id, channel)
                reply(*main_menu("", telegram_id))
                send(
                    chat_id,
                    f"✅ شهر شما: <b>{esc(city_fa(channel.city))}</b>. منوی پایین صفحه به‌روز شد 👇",
                    reply_keyboard=persistent_keyboard(channel),
                )
        elif data == "al:new":
            channel = remembered_city(telegram_id)
            if active_count(telegram_id) >= MAX_ALERTS_PER_USER:
                reply(*cap_message(telegram_id))
            elif channel is None:
                reply("🔔 گوش به زنگ: کدوم شهر؟", city_rows("al:n:{ch}") + [[{"text": "↩️ منو", "callback_data": "mn"}]])
            else:
                alert = new_alert(user, int(chat_id), channel)
                reply(*(question(alert, telegram_id) if alert is not None else cap_message(telegram_id)))
        elif data == "al:list":
            reply(*alerts_message(telegram_id))
        elif parts[:2] == ["al", "n"] and len(parts) == 3:
            channel = HomeChannel.objects.filter(pk=int(parts[2]) if parts[2].isdigit() else 0).exclude(city="").first()
            if channel is None:
                note = OLD_BUTTON
            else:
                alert = new_alert(user, int(chat_id), channel)
                reply(*(question(alert, telegram_id) if alert is not None else cap_message(telegram_id)))
        elif parts[:2] == ["al", "a"] and len(parts) == 5:
            alert = own_alert(telegram_id, parts[2])
            if alert is None or parts[3] not in FIELD_OF or not apply_answer(alert, parts[3], parts[4]):
                note = OLD_BUTTON
            else:
                screen = next_screen(alert, parts[3], telegram_id)
                if screen[0].startswith("✅"):
                    note = "ذخیره شد ✅"
                reply(*screen)
        elif parts[:2] == ["al", "at"] and len(parts) == 4 and parts[3].isdigit():
            alert = own_alert(telegram_id, parts[2])
            if alert is None or not toggle_area(alert, int(parts[3])):
                note = OLD_BUTTON
            else:
                reply(*area_question(alert))
        elif parts[:2] == ["al", "e"] and len(parts) == 4 and parts[3] in FIELD_OF:
            alert = own_alert(telegram_id, parts[2])
            if alert is not None:
                reply(*question(alert, telegram_id, FIELD_OF[parts[3]]))
        elif parts[:2] in (["al", "q"], ["al", "ed"], ["al", "rf"], ["al", "sv"], ["al", "ar"], ["al", "dq"]) and len(parts) == 3:
            alert = own_alert(telegram_id, parts[2])
            if alert is None:
                note = OLD_BUTTON
            elif parts[1] == "q" or (parts[1] == "ed" and next_question(alert)):
                reply(*question(alert, telegram_id))
            elif parts[1] == "ed":
                reply(*edit_menu(alert))
            elif parts[1] == "rf":
                reply(*refine_start(alert))
            elif parts[1] == "ar":
                reply(*area_question(alert))
            elif parts[1] == "dq":
                reply(*delete_question(alert))
            else:
                note = "ذخیره شد ✅"
                reply(*saved_message(alert))
        elif parts[:2] in (["al", "off"], ["al", "on"]) and len(parts) in {3, 4}:
            alert = own_alert(telegram_id, parts[2])
            if alert is not None:
                if parts[1] == "on" and not alert.active and active_count(telegram_id) >= MAX_ALERTS_PER_USER and not alert.wanted_listing_id:
                    note = f"سقف {fa(MAX_ALERTS_PER_USER)} گوش به زنگ روشن پره؛ اول یکی رو خاموش کنید."
                else:
                    alert.active = parts[1] == "on"
                    if alert.active:
                        alert.paused_until = None
                    alert.save(update_fields=["active", "paused_until"])
                    note = "ادامه می‌دم ▶️" if alert.active else "دیگه برای این یکی پیامی نمی‌فرستم 🔕"
                if len(parts) == 4:
                    reply(*alerts_message(telegram_id))
        elif parts[:2] == ["al", "pw"] and len(parts) == 3:
            alert = own_alert(telegram_id, parts[2])
            if alert is None:
                note = OLD_BUTTON
            else:
                alert.paused_until = timezone.now() + timedelta(days=PAUSE_DAYS)
                alert.save(update_fields=["paused_until"])
                local = alert.paused_until.astimezone(city_zone(alert.country, alert.city))
                note = f"⏸ تا {persian_date(local)} برای این گوش به زنگ پیامی نمی‌فرستم."
        elif parts[:2] == ["al", "del"] and len(parts) == 3:
            alert = own_alert(telegram_id, parts[2])
            if alert is not None:
                if alert.wanted_listing_id:
                    # A saved request comes back on the next run if deleted, so it is switched off instead.
                    alert.active = False
                    alert.save(update_fields=["active"])
                else:
                    alert.delete()
                note = "حذف شد"
            reply(*alerts_message(telegram_id))
        elif parts[:2] == ["al", "seen"] and len(parts) == 3 and parts[2].isdigit():
            HomeAlertSent.objects.filter(telegram_user_id=telegram_id, listing_id=int(parts[2]), seen_at__isnull=True).update(
                seen_at=timezone.now()
            )
            note = "👍"
        elif parts[:2] == ["al", "fb"] and len(parts) == 4 and parts[2].isdigit() and parts[3] in {"u", "d"}:
            sent = HomeAlertSent.objects.filter(telegram_user_id=telegram_id, listing_id=int(parts[2])).select_related("alert", "listing").first()
            if sent is None:
                note = OLD_BUTTON
            else:
                sent.feedback = "up" if parts[3] == "u" else "down"
                sent.feedback_reason = ""
                sent.feedback_at = timezone.now()
                sent.seen_at = sent.seen_at or sent.feedback_at
                sent.save(update_fields=["feedback", "feedback_reason", "feedback_at", "seen_at"])
                if parts[3] == "u":
                    note = "ممنون! 👍"
                    if sent.alert is not None:
                        edit_markup(chat_id, message_id, alert_keyboard(sent.alert, sent.listing, feedback=False))
                else:
                    note = "چرا به کارتون نیومد؟ یکی رو بزنید."
                    edit_markup(chat_id, message_id, reason_keyboard(sent.listing_id))
        elif parts[:2] == ["al", "fr"] and len(parts) == 4 and parts[2].isdigit() and parts[3] in FEEDBACK_REASONS:
            sent = HomeAlertSent.objects.filter(telegram_user_id=telegram_id, listing_id=int(parts[2])).select_related("alert", "listing").first()
            if sent is None:
                note = OLD_BUTTON
            else:
                sent.feedback = "down"
                sent.feedback_reason = FEEDBACK_REASONS[parts[3]][0]
                sent.feedback_at = timezone.now()
                sent.save(update_fields=["feedback", "feedback_reason", "feedback_at"])
                note = "ممنون، ثبت شد 🙏 تطبیق‌ها رو دقیق‌تر می‌کنه."
                if sent.alert is not None:
                    edit_markup(chat_id, message_id, alert_keyboard(sent.alert, sent.listing, feedback=False))
        elif parts[:2] == ["al", "ct"] and len(parts) == 3 and parts[2].isdigit():
            listing = HomeListing.objects.filter(pk=int(parts[2])).select_related("post").first()
            if listing is None:
                note = OLD_BUTTON
            else:
                send(chat_id, contact_message(listing))
        elif data == "sr:menu":
            reply(*search_menu(telegram_id))
        elif parts[:2] == ["sr", "c"] and len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
            channel = HomeChannel.objects.filter(pk=int(parts[2])).exclude(city="").first()
            if channel is not None:
                remember_city(telegram_id, channel)
                reply(*results_message(city_criteria(telegram_id, chat_id, channel), int(parts[3]), f"sr:c:{channel.pk}"))
        elif parts[:2] == ["sr", "a"] and len(parts) == 4 and parts[3].isdigit():
            alert = own_alert(telegram_id, parts[2])
            if alert is not None:
                reply(*results_message(alert, int(parts[3]), f"sr:a:{alert.pk}"))
        else:
            note = OLD_BUTTON
    finally:
        answer(callback.get("id", ""), note)


def own_alert(telegram_id: int, raw_id: str) -> HomeAlert | None:
    if not raw_id.isdigit():
        return None
    return HomeAlert.objects.filter(pk=int(raw_id), telegram_user_id=telegram_id).first()
