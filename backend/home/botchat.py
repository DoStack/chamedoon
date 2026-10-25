"""The housing bot's chat: alerts and search without the Mini App, in Persian.

Commands: /start (menu, or `alert_home_<cc>_<city>` from a channel post), /alerts,
/search, /stop, /cancel.

Alert setup asks, one question at a time and in this order, for what makes matching
accurate: who is looking, the budget (bands as shortcuts, or type the number), room or
own place, rooms or size (own places only), term, and Anmeldung (Germany only). Every
question has «مهم نیست», which means no filter on that field. The alert row is made when
the city is chosen and each answer is saved as it comes (`HomeAlert.answered`), so an
alert left half-way still works: a field never answered is strict and only listings that
state it match (see home.alerts.matches).

Free-text state: `HomeChatState` while a typed budget is welcome, `HomeAlert.awaiting_area`
while the bot waits for neighbourhoods.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from home import bot, conf
from home.alerts import (
    ALL_FIELDS,
    BUDGET_BANDS,
    CURRENCY_BY_COUNTRY,
    GENDER_CHOICES,
    TERM_CHOICES,
    TYPE_CHOICES,
    city_fa,
    city_slug,
    find_user,
    next_question,
    search,
    summary,
)
from home.models import AlertTerm, HomeAlert, HomeAlertSent, HomeChannel, HomeChatState, HomeListing
from home.publish import TYPE_FA, esc, fa, money

LATIN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

logger = logging.getLogger(__name__)

PAGE_SIZE = 5
MAX_ALERTS_PER_USER = 10
UNSET = "_"
DONT_CARE = "مهم نیست"
_COMMAND = re.compile(r"^/([A-Za-z_]+)(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$", re.DOTALL)
# Rooms or size, one answer: r<n> = at least n rooms, s<n> = at least n m², x = no filter.
SIZE_CHOICES = {"r2": "۲+ اتاق", "r3": "۳+ اتاق", "r4": "۴+ اتاق", "s40": "۴۰+ متر", "s60": "۶۰+ متر", "s80": "۸۰+ متر"}

COMMANDS = [
    {"command": "start", "description": "منو و گوش به زنگ جدید"},
    {"command": "alerts", "description": "گوش به زنگ‌های من"},
    {"command": "search", "description": "جستجوی آگهی‌ها"},
    {"command": "stop", "description": "توقف همه‌ی پیام‌ها"},
]


# ---------------------------------------------------------------------------
# Telegram helpers. Every reply goes through home.bot.call, so tests mock one function.


def send(chat_id, text: str, keyboard: list | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
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


def answer(callback_id: str, text: str = "") -> None:
    try:
        bot.call("answerCallbackQuery", {"callback_query_id": callback_id, **({"text": text} if text else {})})
    except bot.BotError as exc:
        logger.info("answerCallbackQuery failed: %s", exc.description)


# ---------------------------------------------------------------------------
# Cities.


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
    buttons = [
        {"text": f"{city_fa(channel.city)}", "callback_data": prefix.format(ch=channel.pk)}
        for channel in cities()
        if not country or channel.country == country
    ]
    return [buttons[index : index + 3] for index in range(0, len(buttons), 3)]


def main_menu(first_name: str = "") -> tuple[str, list]:
    hello = f"سلام {esc(first_name)}! " if first_name else "سلام! "
    text = (
        f"{hello}من ربات <b>چمدون خانه</b>‌ام.\n"
        "🔔 <b>گوش به زنگ</b>: شهرت رو انتخاب کن، هر آگهی تازه‌ای که بخوره همین‌جا خبرت می‌کنم."
    )
    keyboard = city_rows("al:n:{ch}") + [
        [{"text": "📋 گوش به زنگ‌های من", "callback_data": "al:list"}, {"text": "🔎 جستجو", "callback_data": "sr:menu"}],
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


def new_alert(user: dict, chat_id: int, channel: HomeChannel) -> HomeAlert:
    """The alert row is made when the city is chosen. An unfinished one for the same city starts again."""
    telegram_id = int(user["id"])
    HomeAlert.objects.filter(
        telegram_user_id=telegram_id, country=channel.country, city=channel.city, wanted_listing__isnull=True, complete=False
    ).delete()
    mine = HomeAlert.objects.filter(telegram_user_id=telegram_id, wanted_listing__isnull=True).order_by("created_at")
    if mine.count() >= MAX_ALERTS_PER_USER:
        mine.first().delete()
    return HomeAlert.objects.create(
        telegram_user_id=telegram_id,
        chat_id=chat_id,
        user=find_user(telegram_id),
        country=channel.country,
        city=channel.city,
        currency=CURRENCY_BY_COUNTRY.get(channel.country, ""),
        answered=[],
    )


def apply_answer(alert: HomeAlert, field: str, value: str) -> bool:
    """Save one answer. Returns False when the value is not a valid answer."""
    answered = set(alert.answered or [])
    if field == "g" and value in GENDER_CHOICES:
        alert.gender = GENDER_CHOICES[value][0]
        answered.add("gender")
    elif field == "b" and value.isdigit():
        alert.max_price = Decimal(value) if int(value) else None
        answered.add("budget")
    elif field == "t" and value in TYPE_CHOICES:
        alert.property_types = TYPE_CHOICES[value][0]
        answered.add("type")
        if alert.property_types == ["room"]:
            alert.min_rooms = None
            alert.min_size_m2 = None
    elif field == "z" and (value == "x" or value in SIZE_CHOICES):
        alert.min_rooms = Decimal(value[1:]) if value.startswith("r") else None
        alert.min_size_m2 = int(value[1:]) if value.startswith("s") else None
        answered.add("rooms")
    elif field == "m" and value in TERM_CHOICES:
        alert.term = TERM_CHOICES[value][0]
        answered.add("term")
    elif field == "d" and value in {"y", "x"}:
        alert.needs_registration = value == "y"
        answered.add("registration")
    else:
        return False
    alert.answered = sorted(answered)
    alert.complete = next_question(alert) == ""
    alert.save()
    return True


def question(alert: HomeAlert, telegram_id: int | None = None) -> tuple[str, list]:
    """The next unanswered question, or the saved message when nothing is left."""
    field = next_question(alert)
    if not field:
        return saved_message(alert)
    head = f"🔔 گوش به زنگ <b>{esc(city_fa(alert.city))}</b>"
    if alert.answered:
        head += f"\n<i>{esc(summary(alert))}</i>"
    base = f"al:a:{alert.pk}"
    skip = {"text": DONT_CARE, "callback_data": f"{base}:{field_code(field)}:x"}
    back = [{"text": "↩️ منو", "callback_data": "mn"}]
    if field == "gender":
        buttons = [{"text": f"👤 {label}", "callback_data": f"{base}:g:{code}"} for code, (_v, label) in GENDER_CHOICES.items() if code != "x"]
        return f"{head}\n\n👤 برای کی؟", [buttons, [{"text": f"{DONT_CARE} (هر کسی)", "callback_data": f"{base}:g:x"}], back]
    if field == "budget":
        bands = BUDGET_BANDS.get(alert.currency, [])
        buttons = [{"text": f"تا {money(Decimal(amount), alert.currency)}", "callback_data": f"{base}:b:{amount}"} for amount in bands]
        rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        rows.append([{"text": DONT_CARE, "callback_data": f"{base}:b:0"}])
        if telegram_id:
            wait_for(telegram_id, "budget", {"alert": alert.pk})
        return f"{head}\n\n💰 سقف اجاره‌ی ماهانه؟ عددش رو بفرست (مثلاً ۱۱۰۰) یا یکی رو بزن.", rows + [back]
    if field == "type":
        labels = {code: label for code, (_types, label) in TYPE_CHOICES.items()}
        rows = [
            [{"text": labels["r"], "callback_data": f"{base}:t:r"}, {"text": labels["c"], "callback_data": f"{base}:t:c"}],
            [{"text": labels["s"], "callback_data": f"{base}:t:s"}, {"text": labels["a"], "callback_data": f"{base}:t:a"}],
            [{"text": f"{DONT_CARE} (هر دو)", "callback_data": f"{base}:t:x"}],
        ]
        return f"{head}\n\n🏷 اتاق یا خونه‌ی مستقل؟", rows + [back]
    if field == "rooms":
        buttons = [{"text": label, "callback_data": f"{base}:z:{code}"} for code, label in SIZE_CHOICES.items()]
        return f"{head}\n\n🛏 حداقل چند اتاق یا چند متر؟", [buttons[0:3], buttons[3:6], [skip], back]
    if field == "term":
        buttons = [{"text": label, "callback_data": f"{base}:m:{code}"} for code, (_term, label) in TERM_CHOICES.items() if code != "x"]
        return f"{head}\n\n📅 چه مدتی؟", [buttons[0:2], buttons[2:], [{"text": f"{DONT_CARE} (بلند یا سابلت)", "callback_data": f"{base}:m:x"}], back]
    return f"{head}\n\n📝 ملد (Anmeldung) لازم داری؟", [[{"text": "بله، فقط با ملد", "callback_data": f"{base}:d:y"}, skip], back]


def field_code(field: str) -> str:
    return {"gender": "g", "budget": "b", "type": "t", "rooms": "z", "term": "m", "registration": "d"}[field]


def save_alert(
    user: dict,
    chat_id: int,
    channel: HomeChannel,
    budget: str,
    type_code: str,
    term_code: str,
    **preferences,
) -> HomeAlert:
    """Make an alert in one call with budget, type and term answered (scripts and studies).
    `preferences` sets gender, min_rooms, min_size_m2, needs_registration or area; each one given counts as answered,
    and `answered` (a list of field names) can name more."""
    alert = new_alert(user, chat_id, channel)
    apply_answer(alert, "b", "0" if budget in {UNSET, ""} else budget)
    apply_answer(alert, "t", type_code)
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
    text = (
        f"✅ گوش به زنگ ذخیره شد:\n<b>{esc(summary(alert))}</b>\n\n"
        "هر آگهی تازه‌ای که بخوره همین‌جا خبرت می‌کنم. چیزی که آگهی نگفته باشه «نامشخص» نوشته می‌شه."
    )
    pk = alert.pk
    keyboard = [
        [{"text": "📍 محله‌ها (یک یا چند)", "callback_data": f"al:ar:{pk}"}],
        [{"text": "🔎 آگهی‌های فعلی", "callback_data": f"sr:a:{pk}:0"}, {"text": "📋 همه‌ی گوش به زنگ‌ها", "callback_data": "al:list"}],
    ]
    return text, keyboard


def alerts_message(telegram_id: int) -> tuple[str, list]:
    alerts = list(HomeAlert.objects.filter(telegram_user_id=telegram_id).order_by("created_at"))
    if not alerts:
        return "هنوز گوش به زنگی نداری. شهرت رو انتخاب کن:", city_rows("al:n:{ch}") + [[{"text": "↩️ منو", "callback_data": "mn"}]]
    lines = ["📋 <b>گوش به زنگ‌های تو</b>"]
    keyboard = []
    for number, alert in enumerate(alerts, start=1):
        state = "" if alert.active else " ⏸"
        source = " (درخواست تو در چمدون خانه)" if alert.wanted_listing_id else ""
        unfinished = " (نیمه‌کاره: فقط آگهی‌هایی که همه‌چیز رو گفتن)" if next_question(alert) else ""
        lines.append(f"{fa(number)}. {esc(summary(alert))}{source}{unfinished}{state}")
        toggle = (
            {"text": f"⏸ توقف {fa(number)}", "callback_data": f"al:off:{alert.pk}"}
            if alert.active
            else {"text": f"▶️ ادامه {fa(number)}", "callback_data": f"al:on:{alert.pk}"}
        )
        row = [toggle, {"text": f"🔎 {fa(number)}", "callback_data": f"sr:a:{alert.pk}:0"}, {"text": f"🗑 حذف {fa(number)}", "callback_data": f"al:del:{alert.pk}"}]
        keyboard.append(row)
        if unfinished and not alert.wanted_listing_id:
            keyboard.append([{"text": f"✏️ تکمیل {fa(number)}", "callback_data": f"al:q:{alert.pk}"}])
    keyboard.append([{"text": "➕ گوش به زنگ جدید", "callback_data": "al:new"}])
    return "\n".join(lines), keyboard


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
    links.append(f'<a href="{esc(conf.mini_app_link(listing.pk))}">جزئیات</a>')
    from home.freshness import freshness_line

    return f"🏠 <b>{esc(TYPE_FA.get(listing.property_type, 'ملک'))} · {esc(place)}</b>\n💶 {esc(price)}\n{freshness_line(listing)}\n{' · '.join(links)}"


def results_message(criteria: HomeAlert, page: int, callback_base: str) -> tuple[str, list]:
    found = search(criteria)
    pages = max(1, (len(found) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    head = f"🔎 <b>{esc(summary(criteria))}</b>"
    if not found:
        text = f"{head}\n\nفعلاً آگهی‌ای پیدا نشد. با گوش به زنگ، آگهی بعدی رو زود خبرت می‌کنم."
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
    from home.botactions import detail_row

    keyboard = [detail_row(shown)] + ([nav] if nav else [])
    keyboard.append([{"text": "↩️ منو", "callback_data": "mn"}])
    return text, keyboard


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


def handle_message(message: dict, chat_id: int, user: dict) -> None:
    text = (message.get("text") or "").strip()
    telegram_id = int(user["id"])
    command = _COMMAND.match(text)
    if command:
        name, arg = command.group(1).lower(), (command.group(2) or "").strip()
        stop_waiting(telegram_id)
        if name == "start":
            start(chat_id, user, arg)
        elif name == "alerts":
            send(chat_id, *alerts_message(telegram_id))
        elif name == "search":
            send(chat_id, *search_menu(telegram_id))
        elif name == "stop":
            stopped = HomeAlert.objects.filter(telegram_user_id=telegram_id, active=True).update(active=False)
            send(
                chat_id,
                "🔕 همه‌ی گوش به زنگ‌ها متوقف شد. دیگه پیامی نمی‌فرستم." if stopped else "گوش به زنگ فعالی نداشتی.",
                [[{"text": "📋 گوش به زنگ‌های من", "callback_data": "al:list"}]],
            )
        elif name == "cancel":
            send(chat_id, "باشه، لغو شد.")
        else:
            send(chat_id, *main_menu(user.get("first_name") or ""))
        return
    waiting = HomeAlert.objects.filter(telegram_user_id=telegram_id, awaiting_area=True).order_by("-created_at").first()
    if waiting is not None and text:
        waiting.area = text[:96]
        waiting.awaiting_area = False
        waiting.save(update_fields=["area", "awaiting_area"])
        send(chat_id, *saved_message(waiting))
        return
    state = HomeChatState.objects.filter(telegram_user_id=telegram_id, waiting="budget").first()
    if state is not None and text:
        alert = HomeAlert.objects.filter(pk=state.data.get("alert") or 0, telegram_user_id=telegram_id).first()
        amount = typed_amount(text)
        if alert is not None and amount is None:
            send(chat_id, f"یه عدد بفرست، مثلاً ۱۱۰۰. یا یکی از دکمه‌ها رو بزن («{DONT_CARE}» هم هست).")
            return
        stop_waiting(telegram_id)
        if alert is not None:
            apply_answer(alert, "b", str(amount))
            send(chat_id, *question(alert, telegram_id))
            return
    send(chat_id, *main_menu(user.get("first_name") or ""))


def start(chat_id: int, user: dict, payload: str) -> None:
    found = parse_start_payload(payload)
    if found is None:
        send(chat_id, *main_menu(user.get("first_name") or ""))
        return
    channel = found["channel"]
    if channel is None:
        rows = city_rows("al:n:{ch}", country=found["country"]) or city_rows("al:n:{ch}")
        send(chat_id, "🔔 گوش به زنگ: کدوم شهر؟", rows + [[{"text": "↩️ منو", "callback_data": "mn"}]])
        return
    alert = new_alert(user, chat_id, channel)
    for field, value in found["answers"].items():
        apply_answer(alert, field, value)
    send(chat_id, *question(alert, int(user["id"])))


def refresh_complete(alert: HomeAlert) -> None:
    complete = next_question(alert) == ""
    if alert.complete != complete:
        alert.complete = complete
        alert.save(update_fields=["complete"])


def handle_callback(callback: dict) -> None:
    data = str(callback.get("data") or "")
    if data.startswith("hx:"):
        # Listing buttons (contact, rented, report, still available, owner check): home/botactions.py.
        from home.botactions import handle_callback as listing_callback

        listing_callback(callback)
        return
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

    if not data.startswith("al:seen:"):
        stop_waiting(telegram_id)
    try:
        if data == "mn":
            reply(*main_menu(user.get("first_name") or ""))
        elif data == "al:new":
            reply("🔔 گوش به زنگ: کدوم شهر؟", city_rows("al:n:{ch}") + [[{"text": "↩️ منو", "callback_data": "mn"}]])
        elif data == "al:list":
            reply(*alerts_message(telegram_id))
        elif parts[:2] == ["al", "n"] and len(parts) == 3:
            channel = HomeChannel.objects.filter(pk=int(parts[2]) if parts[2].isdigit() else 0).exclude(city="").first()
            if channel is None:
                note = "این دکمه قدیمیه."
            else:
                alert = new_alert(user, int(chat_id), channel)
                reply(*question(alert, telegram_id))
        elif parts[:2] == ["al", "a"] and len(parts) == 5:
            alert = own_alert(telegram_id, parts[2])
            if alert is None or not apply_answer(alert, parts[3], parts[4]):
                note = "این دکمه قدیمیه."
            else:
                if not next_question(alert):
                    note = "ذخیره شد ✅"
                reply(*question(alert, telegram_id))
        elif parts[:2] == ["al", "q"] and len(parts) == 3:
            alert = own_alert(telegram_id, parts[2])
            if alert is not None:
                reply(*question(alert, telegram_id))
        elif parts[:2] == ["al", "ar"] and len(parts) == 3:
            alert = own_alert(telegram_id, parts[2])
            if alert is not None:
                alert.awaiting_area = True
                alert.save(update_fields=["awaiting_area"])
                send(
                    chat_id,
                    "📍 اسم محله‌ها رو بفرست؛ چندتا رو با ویرگول یا «یا» جدا کن (مثلاً Yonge & Finch، Richmond Hill یا هاربورگ). برای لغو: /cancel",
                )
        elif parts[:2] in (["al", "off"], ["al", "on"]) and len(parts) == 3:
            alert = own_alert(telegram_id, parts[2])
            if alert is not None:
                alert.active = parts[1] == "on"
                alert.save(update_fields=["active"])
                note = "ادامه می‌دم ▶️" if alert.active else "دیگه برای این یکی پیام نمی‌دم 🔕"
                if message.get("text", "").startswith("📋"):
                    reply(*alerts_message(telegram_id))
        elif parts[:2] == ["al", "del"] and len(parts) == 3:
            alert = own_alert(telegram_id, parts[2])
            if alert is not None:
                if alert.wanted_listing_id:
                    # A saved request comes back on the next run if deleted, so it is paused instead.
                    alert.active = False
                    alert.save(update_fields=["active"])
                else:
                    alert.delete()
                note = "حذف شد"
            reply(*alerts_message(telegram_id))
        elif parts[:2] == ["al", "seen"] and len(parts) == 3 and parts[2].isdigit():
            from django.utils import timezone

            HomeAlertSent.objects.filter(telegram_user_id=telegram_id, listing_id=int(parts[2]), seen_at__isnull=True).update(
                seen_at=timezone.now()
            )
            note = "👍"
        elif data == "sr:menu":
            reply(*search_menu(telegram_id))
        elif parts[:2] == ["sr", "c"] and len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
            channel = HomeChannel.objects.filter(pk=int(parts[2])).exclude(city="").first()
            if channel is not None:
                criteria = HomeAlert(
                    telegram_user_id=telegram_id,
                    chat_id=chat_id,
                    country=channel.country,
                    city=channel.city,
                    term=AlertTerm.ANY,
                    answered=list(ALL_FIELDS),
                )
                reply(*results_message(criteria, int(parts[3]), f"sr:c:{channel.pk}"))
        elif parts[:2] == ["sr", "a"] and len(parts) == 4 and parts[3].isdigit():
            alert = own_alert(telegram_id, parts[2])
            if alert is not None:
                reply(*results_message(alert, int(parts[3]), f"sr:a:{alert.pk}"))
        else:
            note = "این دکمه قدیمیه."
    finally:
        answer(callback.get("id", ""), note)


def own_alert(telegram_id: int, raw_id: str) -> HomeAlert | None:
    if not raw_id.isdigit():
        return None
    return HomeAlert.objects.filter(pk=int(raw_id), telegram_user_id=telegram_id).first()
