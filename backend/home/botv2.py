"""The housing bot v2 (Bot2 boards), behind `HomeSettings.bot_v2` and its allowlist.

home/botchat.py hands a person's whole update here when `conf.bot_v2_for` says so; with the flag off nothing here
runs. This module holds the conversation only: matching, gate, freshness and saved listings stay in their own
modules, the same ones the Mini App uses. Buttons on messages from before the switch keep working: `al:`, `sr:` and
`mn` go to botchat, `hx:` to botactions.

Callback data, all under `v2:`:

    cc:<CC>  ct:<id>  city  other       country, city, the city list, «شهر دیگر؟»
    p:<n>  d:<id>  c:<id>  s:<id>  t:<id>   results page, detail, contact, bookmark, «بسته شد»
    rm:<field>  fix  fx:<field>  fs:<field>:<value>  clear  show   chips and «✏️ اصلاح»
    wiz  ar:<canonical index>  aw  nq     typed search: rescue, area chosen, «همه‌ی شهر», as written
    a…                                     the alert setup and the alert screens, see `alert_callback`
    saved  sx  help  safety:<id>  forget  fy  lang:<fa|en>  menu  ask  post  mine
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from home import bot, botactions, botchat, botlog, botparse, channel_format, conf, freshness, gate
from home.alerts import (
    ALL_FIELDS,
    CURRENCY_BY_COUNTRY,
    MAX_ALERTS_PER_USER,
    PAUSE_DAYS,
    active_count,
    city_fa,
    city_zone,
    common_areas,
    fits_line,
    is_paused,
    metro_channel,
    posted_at,
    search,
    user_alerts,
)
from home.areas import metro_rows
from home.botchat import stop_waiting, typed_amount, wait_for
from home.models import (
    HomeAlert,
    HomeAlertSent,
    HomeArea,
    HomeChannel,
    HomeChatState,
    HomeCityWaitlist,
    HomeListing,
    HomeSaved,
    HomeSearchQuery,
    ListingKind,
    ListingStatus,
    SearchQueryStatus,
)
from home.places import fold
from home.publish import LEASE_TRANSFER_FA, MONTHS, esc, fa, money_fa, persian_date, photo_urls, place_fa, type_word
from telemetry import capture
from telemetry.events import HOUSING
from users.services import is_blocked, upsert_telegram_user
from users.telegram import TelegramIdentity

logger = logging.getLogger(__name__)

PAGE_SIZE = 5
CAPTION_LIMIT = 1024
OLD_BUTTON = "این دکمه قدیمی است."
NOT_TEXT = "این نوع پیام را متوجه نمی‌شوم؛ لطفاً متن بنویسید."
SAFETY_SHORT = "⚠️ قبل از دیدن خانه پولی نفرستید."
MENU_BUTTON = {"text": "↩️ منو", "callback_data": "v2:menu"}
FLAGS = {"CA": "🇨🇦", "DE": "🇩🇪"}
COUNTRY_FA = {"CA": "کانادا", "DE": "آلمان"}
CURRENCY_NAME = {"CAD": "دلار کانادا", "EUR": "یورو، اجاره‌ی گرم"}
BANDS = channel_format.BANDS
TYPE_LABEL = {"room": "اتاق", "studio": "استودیو", "basement": "بیسمنت", "apartment": "آپارتمان", "house": "خانه‌ی کامل"}
GENDER_LABEL = {"female": "فقط خانم", "male": "فقط آقا", "couple": "زوج"}
TERM_LABEL = {"sublet": "سابلت", "lease_transfer": LEASE_TRANSFER_FA, "short": "کوتاه‌مدت", "lease": "بلندمدت"}
TERM_LABEL_DE = {**TERM_LABEL, "sublet": "Zwischenmiete", "lease_transfer": "Nachmieter"}
# The alert's term field keeps three values; a lease transfer counts under sublet there.
ALERT_TERM = {"lease": "lease", "sublet": "sublet", "lease_transfer": "sublet", "short": "short"}
REPORT_OUT = "برای مشکل در یک آگهی به پشتیبانی پیام دهید."

COMMANDS = [
    {"command": "start", "description": "منو"},
    {"command": "alerts", "description": "گوش به زنگ‌های من"},
    {"command": "search", "description": "جستجوی آگهی‌ها"},
    {"command": "saved", "description": "نشان‌شده‌ها"},
    {"command": "lang", "description": "زبان · Language"},
    {"command": "help", "description": "راهنما"},
    {"command": "stop", "description": "توقف همه‌ی پیام‌ها"},
    {"command": "forget", "description": "پاک کردن پیام‌ها و جستجوها"},
]

# The persistent keyboard. English exists for the menu and the first hello; every other message is Persian for now.
KB = {
    "fa": {"search": "🔎 جستجوی خانه", "alerts": "🔔 گوش به زنگ‌های من", "post": "➕ ثبت آگهی", "saved": "🔖 نشان‌شده‌ها", "city": "تغییر شهر", "pick": "📍 انتخاب شهر", "help": "❓ راهنما"},
    "en": {"search": "🔎 Find a home", "alerts": "🔔 My alerts", "post": "➕ Post a listing", "saved": "🔖 Bookmarks", "city": "Change city", "pick": "📍 Pick a city", "help": "❓ Help"},
}
CITY_EN = {"تورنتو": "Toronto", "مونترال": "Montreal", "ونکوور": "Vancouver", "برلین": "Berlin", "هامبورگ": "Hamburg", "کلن": "Cologne"}


# ---------------------------------------------------------------------------
# Telegram, through one function, and every reply logged.


def send(chat_id, text: str, keyboard: list | None = None, *, reply_keyboard: dict | None = None, kind: str = "other", query: HomeSearchQuery | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    elif reply_keyboard:
        payload["reply_markup"] = reply_keyboard
    bot.call("sendMessage", payload)
    botlog.record(int(chat_id), "out", kind, text, query)


def edit(chat_id, message_id, text: str, keyboard: list | None = None, *, kind: str = "other") -> None:
    if not message_id:
        send(chat_id, text, keyboard, kind=kind)
        return
    botchat.edit(chat_id, message_id, text, keyboard)
    botlog.record(int(chat_id), "out", kind, text)


def edit_keyboard(chat_id, message_id, keyboard: list) -> None:
    botchat.edit_markup(chat_id, message_id, {"inline_keyboard": keyboard})


def state_of(telegram_id: int) -> HomeChatState:
    return HomeChatState.objects.select_related("channel").get_or_create(telegram_user_id=telegram_id)[0]


def locale_of(telegram_id: int) -> str:
    return state_of(telegram_id).locale or "fa"


def person(user: dict):
    """The account of a bot user, made on first need, so gate and bookmarks are the Mini App's own. None when blocked."""
    identity = TelegramIdentity(
        telegram_user_id=int(user["id"]), telegram_username=user.get("username"), first_name=user.get("first_name") or "", last_name=user.get("last_name")
    )
    return None if is_blocked(identity) else upsert_telegram_user(identity)


def rows_of(buttons: list[dict], size: int) -> list[list[dict]]:
    return [buttons[index : index + size] for index in range(0, len(buttons), size)]


# ---------------------------------------------------------------------------
# Start, city, menu.


def persistent_keyboard(channel: HomeChannel | None, locale: str = "fa") -> dict:
    words = KB["en" if locale == "en" else "fa"]
    if channel is None:
        city = words["pick"]
    elif locale == "en":
        name = city_fa(channel.city)
        city = f"📍 {CITY_EN.get(name, channel.city)} · {words['city']}"
    else:
        city = f"📍 {city_fa(channel.city)} · {words['city']}"
    return {
        "keyboard": [
            [{"text": words["search"]}, {"text": words["alerts"]}],
            [{"text": words["post"]}, {"text": words["saved"]}],
            [{"text": city}, {"text": words["help"]}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def hello(first_name: str) -> str:
    return f"سلام {esc(first_name)} 👋" if first_name else "سلام 👋"


def country_rows() -> list[list[dict]]:
    countries = list(dict.fromkeys(channel.country for channel in botchat.cities()))
    return [[{"text": f"{FLAGS.get(code, '')} {COUNTRY_FA.get(code, code)}".strip(), "callback_data": f"v2:cc:{code}"} for code in countries]]


def city_rows(country: str = "") -> list[list[dict]]:
    buttons = [
        {"text": f"{FLAGS.get(channel.country, '')} {city_fa(channel.city)}".strip(), "callback_data": f"v2:ct:{channel.pk}"}
        for channel in botchat.cities()
        if not country or channel.country == country
    ]
    return rows_of(buttons, 3)


def write_prompt() -> tuple[str, list]:
    return (
        "بنویسید دنبال چه خانه‌ای هستید؛ مثلاً:\n«اتاق نورث یورک تا ۱۲۰۰ از دسامبر»\n\nیا از دکمه‌های پایین صفحه استفاده کنید.",
        [[{"text": "✍️ بنویسید چه می‌خواهید", "callback_data": "v2:ask"}]],
    )


def start(chat_id: int, user: dict, payload: str) -> None:
    telegram_id = int(user["id"])
    first_name = user.get("first_name") or ""
    known = HomeChatState.objects.filter(telegram_user_id=telegram_id).exists()
    found = botchat.parse_start_payload(payload)
    channel = botchat.remembered_city(telegram_id)
    if found is not None:
        target = found["channel"]
        intro = f"{hello(first_name)}" if known else f"{hello(first_name)} من ربات <b>چمدون خانه</b> هستم. رایگان است."
        send(chat_id, intro, reply_keyboard=persistent_keyboard(target or channel, locale_of(telegram_id)), kind="menu")
        if target is None:
            send(chat_id, "🔔 گوش به زنگ: <b>کدام شهر؟</b>", city_rows(found["country"]) + [[MENU_BUTTON]], kind="menu")
            return
        begin_alert(user, chat_id, target, found["answers"])
        return
    if channel is None:
        text = (
            f"{hello(first_name)} من ربات <b>چمدون خانه</b> هستم.\n"
            "آگهی‌های خانه‌ی گروه‌های فارسی شهر شما را جمع و مرتب می‌کنم. رایگان است.\n\n<b>کشور شما؟</b>"
        )
        send(chat_id, text, country_rows(), kind="menu")
        return
    send(chat_id, hello(first_name), reply_keyboard=persistent_keyboard(channel, locale_of(telegram_id)), kind="menu")
    set_search(telegram_id, {"ch": channel.pk})
    send(chat_id, *results_screen(telegram_id, 0), kind="results")


def pick_city(chat_id: int, telegram_id: int, channel: HomeChannel) -> None:
    botchat.remember_city(telegram_id, channel)
    set_search(telegram_id, {"ch": channel.pk})
    send(chat_id, f"شهر شما: <b>{esc(city_fa(channel.city))}</b>", reply_keyboard=persistent_keyboard(channel, locale_of(telegram_id)), kind="menu")
    send(chat_id, *write_prompt(), kind="menu")


def city_list(telegram_id: int) -> tuple[str, list]:
    note = "📍 <b>کدام شهر؟</b> گوش به زنگ‌های شما با تغییر شهر حفظ می‌شود."
    return note, city_rows() + [[{"text": "🌍 شهر دیگر؟ لیست انتظار", "callback_data": "v2:other"}]]


# ---------------------------------------------------------------------------
# The current search: a small dict on the chat state, so its buttons survive a restart.


def set_search(telegram_id: int, values: dict) -> None:
    HomeChatState.objects.update_or_create(telegram_user_id=telegram_id, defaults={"search": values})


def get_search(telegram_id: int) -> dict:
    return dict(state_of(telegram_id).search or {})


def search_channel(values: dict, telegram_id: int) -> HomeChannel | None:
    channel = HomeChannel.objects.filter(pk=values.get("ch") or 0, enabled=True).exclude(city="").first()
    return channel or botchat.remembered_city(telegram_id)


def criteria(values: dict, telegram_id: int, channel: HomeChannel) -> HomeAlert:
    """The search as an unsaved alert, so `alerts.search` answers it with the same rules as a real alert."""
    extra: dict = {}
    if values.get("furnished"):
        extra["furnished"] = True
    if values.get("bills"):
        extra["bills"] = True
    if values.get("move"):
        # «از ۱ دسامبر»: available by a week after that, or no date stated.
        extra["by"] = (date.fromisoformat(values["move"]) + timedelta(days=7)).isoformat()
    if values.get("q"):
        extra["q"] = values["q"]
    term = values.get("term") or ""
    if term == "lease_transfer":
        extra["term"] = "lease_transfer"
    return HomeAlert(
        telegram_user_id=telegram_id,
        chat_id=telegram_id,
        country=channel.country,
        city=channel.city,
        currency=CURRENCY_BY_COUNTRY.get(channel.country, ""),
        max_price=Decimal(values["max"]) if values.get("max") else None,
        property_types=list(values.get("types") or []),
        area=", ".join(values.get("areas") or []),
        gender=values.get("gender") or "any",
        min_rooms=Decimal(values["rooms"]) if values.get("rooms") else None,
        term=ALERT_TERM.get(term, "any"),
        answered=list(ALL_FIELDS),
        search=extra,
    )


def area_label(canonical: str) -> str:
    row = HomeArea.objects.filter(canonical=fold(canonical)).first()
    return row.fa if row is not None else place_fa(canonical)


def date_label(iso: str) -> str:
    moved = date.fromisoformat(iso)
    return f"{fa(moved.day)} {MONTHS[moved.month - 1]}"


def chips(values: dict, channel: HomeChannel) -> list[tuple[str, str]]:
    """Each set filter as (field, its words), in the order of the board."""
    out: list[tuple[str, str]] = []
    if values.get("rooms"):
        out.append(("rooms", f"{fa(values['rooms'])} خوابه"))
    elif values.get("types"):
        out.append(("types", "، ".join(TYPE_LABEL.get(value, value) for value in values["types"])))
    if values.get("areas"):
        out.append(("areas", "، ".join(area_label(value) for value in values["areas"])))
    if values.get("max"):
        out.append(("max", f"تا {money_fa(Decimal(values['max']), CURRENCY_BY_COUNTRY.get(channel.country, ''))}"))
    if values.get("move"):
        out.append(("move", f"از {date_label(values['move'])}"))
    if values.get("gender"):
        out.append(("gender", GENDER_LABEL.get(values["gender"], values["gender"])))
    if values.get("term"):
        out.append(("term", TERM_LABEL.get(values["term"], values["term"])))
    if values.get("furnished"):
        out.append(("furnished", "مبله"))
    if values.get("bills"):
        out.append(("bills", "قبوض داخل"))
    if values.get("q"):
        out.append(("q", f"«{values['q']}»"))
    return out


def summary_line(values: dict, channel: HomeChannel) -> str:
    return " · ".join([f"<b>{esc(city_fa(channel.city))}</b>", *[esc(words) for _field, words in chips(values, channel)]])


def result_line(listing: HomeListing, number: int, channel: HomeChannel) -> str:
    price = f"<b>{esc(money_fa(listing.price_amount, listing.price_currency))}</b>" if listing.price_amount else "<b>قیمت: بپرسید</b>"
    kind = channel_format.rooms_word(listing) or type_word(listing) or "ملک"
    place = channel_format.district(listing, channel.city)
    return f"<b>{fa(number)}.</b> {price} · {esc(kind)} · {esc(place)} · {freshness.relative_time(listing.last_seen)}"


def rescue_screen(values: dict, channel: HomeChannel, telegram_id: int) -> tuple[str, list]:
    """Nothing matched: what would, if the person widened one thing."""
    options: list[tuple[str, dict, str]] = []
    currency = CURRENCY_BY_COUNTRY.get(channel.country, "")
    higher = next((band for band in BANDS.get(currency, ()) if values.get("max") and band > int(values["max"])), None)
    if higher:
        options.append((f"بودجه تا {money_fa(Decimal(higher), currency)}", {**values, "max": higher}, f"v2:wd:max:{higher}"))
    if values.get("types") and "studio" not in values["types"]:
        options.append(("با استودیو", {**values, "types": [*values["types"], "studio"]}, "v2:wd:types:+studio"))
    if values.get("areas"):
        options.append((f"همه‌ی محله‌های {city_fa(channel.city)}", {**values, "areas": []}, "v2:rm:areas"))
    counted = [(label, len(search(criteria(wider, telegram_id, channel))), data) for label, wider, data in options]
    useful = [item for item in counted if item[1]]
    head = f"🔎 {summary_line(values, channel)}"
    alert = {"text": "🔔 گوش به زنگ همین", "callback_data": "v2:asearch"}
    fix = {"text": "✏️ اصلاح", "callback_data": "v2:fix"}
    if not useful:
        text = f"{head}\n\nهنوز آگهی‌ای با این شرایط نیامده، حتی با شرط‌های بازتر.\nاگر گوش به زنگ بگذارید، اولین آگهی که بخورد همان لحظه می‌رسد."
        wide = [[{"text": f"📍 همه‌ی {city_fa(channel.city)}", "callback_data": "v2:rm:areas"}]] if values.get("areas") else []
        return text, [[alert, fix], *wide]
    lines = "\n".join(f"• {esc(label)}: {fa(count)} آگهی" for label, count, _data in useful)
    buttons = [{"text": label.replace("همه‌ی محله‌های " + city_fa(channel.city), "همه‌ی محله‌ها"), "callback_data": data} for label, _count, data in useful]
    return f"{head}\n\nآگهی‌ای با این شرایط نیست. اگر کمی بازتر کنید:\n{lines}", [*rows_of(buttons, 3), [fix, alert]]


def results_screen(telegram_id: int, page: int, *, head: str = "", query: HomeSearchQuery | None = None) -> tuple[str, list]:
    values = get_search(telegram_id)
    channel = search_channel(values, telegram_id)
    if channel is None:
        return "<b>کشور شما؟</b>", country_rows()
    values["ch"] = channel.pk
    found = search(criteria(values, telegram_id, channel))
    if query is not None:
        HomeSearchQuery.objects.filter(pk=query.pk).update(results_count=len(found), status=SearchQueryStatus.ZERO if not found else query.status)
    if not found:
        return rescue_screen(values, channel, telegram_id)
    pages = max(1, (len(found) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    shown = found[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    first = page * PAGE_SIZE + 1
    last = first + len(shown) - 1
    set_chips = chips(values, channel)
    title = head or ("🔎 " + summary_line(values, channel) + ("" if set_chips else " · تازه‌ترین‌ها"))
    body = "\n".join(result_line(listing, first + index, channel) for index, listing in enumerate(shown))
    text = f"{title}\n{fa(len(found))} آگهی · {fa(first)} تا {fa(last)}\n\n{body}"
    keyboard: list[list[dict]] = rows_of([{"text": f"✕ {words}"[:40], "callback_data": f"v2:rm:{name}"} for name, words in set_chips], 3)
    keyboard += rows_of([{"text": fa(first + index), "callback_data": f"v2:d:{listing.pk}"} for index, listing in enumerate(shown)], 3)
    nav = []
    if page > 0:
        nav.append({"text": "‹ قبلی", "callback_data": f"v2:p:{page - 1}"})
    if page + 1 < pages:
        nav.append({"text": "بعدی ›", "callback_data": f"v2:p:{page + 1}"})
    if nav:
        keyboard.append(nav)
    if set_chips:
        keyboard.append([{"text": "🔔 گوش به زنگ همین", "callback_data": "v2:asearch"}, {"text": "✏️ اصلاح", "callback_data": "v2:fix"}])
    return text, keyboard


# ---------------------------------------------------------------------------
# Free text.


def free_text(chat_id: int, telegram_id: int, text: str) -> None:
    channel = botchat.remembered_city(telegram_id)
    if channel is None:
        send(chat_id, "اول شهر را بگویید.\n\n<b>کشور شما؟</b>", country_rows(), kind="menu")
        return
    try:
        bot.call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    except bot.BotError:
        logger.info("sendChatAction failed")
    parsed = botparse.parse(text, channel, telegram_id)
    if parsed.ai_failed or parsed.capped:
        status = SearchQueryStatus.CAPPED if parsed.capped else SearchQueryStatus.FALLBACK
    elif parsed.confidence < botparse.LOW_CONFIDENCE:
        status = SearchQueryStatus.LOW
    else:
        status = SearchQueryStatus.OK
    query = HomeSearchQuery.objects.create(
        telegram_user_id=telegram_id,
        raw_text=text[:2000],
        country=channel.country,
        city=channel.city,
        parser=parsed.parser,
        confidence=Decimal(str(round(parsed.confidence, 2))),
        field_confidence=parsed.field_confidence,
        parsed={**parsed.fields, "unmapped_areas": parsed.unmapped},
        latency_ms=parsed.latency_ms,
        status=status,
    )
    botlog.record(telegram_id, "in", "text", text, query)
    if parsed.capped:
        send(
            chat_id,
            "امروز سقف جستجوی متنی پر شد. با «🎛 جستجو با سؤال‌ها» ادامه دهید؛ فردا دوباره می‌توانید بنویسید.",
            [[{"text": "🎛 جستجو با سؤال‌ها", "callback_data": "v2:anew"}, MENU_BUTTON]],
            kind="other",
            query=query,
        )
        return
    values = {"ch": channel.pk, "query": query.pk, **parsed.fields}
    if parsed.unmapped:
        values["raw_area"] = parsed.unmapped[0]
        set_search(telegram_id, values)
        send(chat_id, *clarify_screen(parsed.unmapped[0], channel), kind="clarify", query=query)
        return
    set_search(telegram_id, values)
    head = ""
    if not parsed.fields:
        send(chat_id, f"متوجه نشدم دنبال چه خانه‌ای هستید. {write_prompt()[0]}", [[{"text": "🎛 جستجو با سؤال‌ها", "callback_data": "v2:anew"}]], kind="clarify", query=query)
        return
    if parsed.ai_failed or (parsed.parser == botparse.SIMPLE and parsed.not_understood):
        missed = "، ".join(f"«{esc(word)}»" for word in parsed.not_understood[:4])
        tail = f"\n<i>{missed} را متوجه نشدم.</i>" if missed else ""
        head = f"فعلاً نمی‌توانم متن را کامل بخوانم؛ <b>جستجوی ساده</b> انجام شد:\n{summary_line(values, channel)}{tail}"
    else:
        head = f"فهمیدم:\n{summary_line(values, channel)}"
    text_out, keyboard = results_screen(telegram_id, 0, head=head, query=query)
    if head.startswith("فعلاً"):
        keyboard = [[*row] for row in keyboard]
        for row in keyboard:
            for button in row:
                if button.get("callback_data") == "v2:asearch":
                    button.update({"text": "🎛 جستجو با سؤال‌ها", "callback_data": "v2:anew"})
    send(chat_id, text_out, keyboard, kind="results", query=query)


def clarify_screen(raw: str, channel: HomeChannel) -> tuple[str, list]:
    options = common_areas(channel.country, channel.city, limit=3)
    if not options:
        options = [(row.fa, row.canonical, 0) for row in metro_rows(channel)[:3]]
    buttons = [{"text": label, "callback_data": f"v2:ar:{index}"} for index, (label, _value, _count) in enumerate(options)]
    buttons.append({"text": f"📍 همه‌ی {city_fa(channel.city)}", "callback_data": "v2:aw"})
    rows = rows_of(buttons, 2) + [[{"text": "همین را جستجو کنید", "callback_data": "v2:nq"}]]
    return f"«{esc(raw)}» را در محله‌های {esc(city_fa(channel.city))} پیدا نکردم. منظورتان کدام است؟", rows


# ---------------------------------------------------------------------------
# «✏️ اصلاح»


FIX_FIELDS = (("types", "نوع خانه"), ("max", "بودجه"), ("areas", "محله"), ("term", "نوع اجاره"), ("move", "تاریخ ورود"), ("gender", "هم‌خانه"), ("furnished", "مبله"), ("bills", "قبوض داخل"))


def fix_menu(telegram_id: int) -> tuple[str, list]:
    values = get_search(telegram_id)
    channel = search_channel(values, telegram_id)
    if channel is None:
        return "<b>کشور شما؟</b>", country_rows()
    line = " · ".join([city_fa(channel.city), *[words for _name, words in chips(values, channel)]])
    buttons = [{"text": label, "callback_data": f"v2:fx:{name}"} for name, label in FIX_FIELDS]
    keyboard = rows_of(buttons, 2) + [[{"text": "🗑 پاک کردن همه", "callback_data": "v2:clear"}, {"text": "نمایش آگهی‌ها", "callback_data": "v2:show"}]]
    return f"✏️ <b>اصلاح جستجو</b>\n<i>{esc(line)}</i>\n\nکدام را عوض کنیم؟", keyboard


def fix_options(name: str, values: dict, channel: HomeChannel) -> tuple[str, list]:
    currency = CURRENCY_BY_COUNTRY.get(channel.country, "")
    back = [{"text": "‹ برگشت", "callback_data": "v2:fix"}]
    if name == "types":
        kinds = {"room": "اتاق WG", "apartment": "آپارتمان"} if channel.country == "DE" else TYPE_LABEL
        picked = values.get("types") or []
        buttons = [{"text": f"{'✓ ' if kind in picked else ''}{label}", "callback_data": f"v2:fs:types:{'-' if kind in picked else '+'}{kind}"} for kind, label in kinds.items()]
        return "نوع خانه؟ یک یا چند مورد را بزنید.", [*rows_of(buttons, 3), back]
    if name == "max":
        buttons = [{"text": f"تا {money_fa(Decimal(band), currency)}", "callback_data": f"v2:fs:max:{band}"} for band in BANDS.get(currency, ())]
        return "سقف اجاره؟", [*rows_of(buttons, 3), [{"text": "همه", "callback_data": "v2:rm:max"}], back]
    if name == "areas":
        picked = values.get("areas") or []
        options = common_areas(channel.country, channel.city, limit=8) or [(row.fa, row.canonical, 0) for row in metro_rows(channel)[:8]]
        buttons = []
        for index, (label, value, _count) in enumerate(options):
            on = fold(value) in {fold(item) for item in picked}
            buttons.append({"text": f"{'✓ ' if on else ''}{label}", "callback_data": f"v2:fs:areas:{index}"})
        return "کدام محله‌ها؟", [*rows_of(buttons, 2), [{"text": "📍 همه‌جا", "callback_data": "v2:rm:areas"}], back]
    if name == "term":
        labels = TERM_LABEL_DE if channel.country == "DE" else TERM_LABEL
        buttons = [{"text": label, "callback_data": f"v2:fs:term:{code}"} for code, label in labels.items()]
        return "نوع اجاره؟", [*rows_of(buttons, 2), [{"text": "همه", "callback_data": "v2:rm:term"}], back]
    if name == "move":
        today = timezone.localdate()
        months = []
        for step in range(4):
            month = (today.month - 1 + step) % 12 + 1
            year = today.year + (today.month - 1 + step) // 12
            months.append({"text": f"از ۱ {MONTHS[month - 1]}", "callback_data": f"v2:fs:move:{date(year, month, 1).isoformat()}"})
        return "از کی؟", [*rows_of(months, 2), [{"text": "همه", "callback_data": "v2:rm:move"}], back]
    if name == "gender":
        buttons = [{"text": label, "callback_data": f"v2:fs:gender:{code}"} for code, label in GENDER_LABEL.items()]
        return "برای چه کسی؟", [buttons, [{"text": "همه", "callback_data": "v2:rm:gender"}], back]
    label = "مبله" if name == "furnished" else "قبوض داخل"
    on = bool(values.get(name))
    return f"{label}؟", [[{"text": f"{'✓ ' if on else ''}{label}", "callback_data": f"v2:fs:{name}:{'0' if on else '1'}"}], back]


def fix_set(name: str, value: str, values: dict, channel: HomeChannel) -> dict | None:
    """The search with one field changed, or None when the button is not a real choice."""
    out = dict(values)
    if name == "max" and value.isdigit():
        out["max"] = int(value)
    elif name == "types" and value[:1] in {"+", "-"} and value[1:] in TYPE_LABEL:
        picked = [item for item in out.get("types") or [] if item != value[1:]]
        out["types"] = picked + [value[1:]] if value[0] == "+" else picked
        out.pop("rooms", None)
    elif name == "areas" and value.isdigit():
        options = common_areas(channel.country, channel.city, limit=8) or [(row.fa, row.canonical, 0) for row in metro_rows(channel)[:8]]
        if int(value) >= len(options):
            return None
        chosen = options[int(value)][1]
        picked = out.get("areas") or []
        same = [item for item in picked if fold(item) == fold(chosen)]
        out["areas"] = [item for item in picked if item not in same] if same else [*picked, chosen]
    elif name == "term" and value in TERM_LABEL:
        out["term"] = value
    elif name == "move":
        try:
            out["move"] = date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    elif name == "gender" and value in GENDER_LABEL:
        out["gender"] = value
    elif name in {"furnished", "bills"} and value in {"0", "1"}:
        if value == "1":
            out[name] = True
        else:
            out.pop(name, None)
    else:
        return None
    return out


# ---------------------------------------------------------------------------
# A listing: card, contact, bookmark, closed.


def card_text(listing: HomeListing, *, limit: int = 3500) -> str:
    """Same layout as the channel post, plus how fresh it is. Never the contact."""
    metro = channel_format.metro_of(listing)
    state = ""
    if listing.status == ListingStatus.REPORTED:
        state = "⚠️ <b>این آگهی گزارش شده و در حال بررسی است.</b>\nقبل از دیدن خانه هیچ پولی نفرستید.\n\n"
    body = channel_format.post_text(listing, limit=max(100, limit - len(state) - 80), metro=metro)
    fresh = f"🕒 {freshness.relative_time(listing.last_seen)}"
    if listing.status == ListingStatus.ACTIVE:
        fresh += " · موجود"
    return f"{state}{body}\n\n<i>{fresh}</i>"


def is_saved(user, listing: HomeListing) -> bool:
    return user is not None and HomeSaved.objects.filter(user=user, listing=listing).exists()


def card_keyboard(listing: HomeListing, *, saved: bool, back: bool = True) -> list[list[dict]]:
    if listing.status not in {ListingStatus.ACTIVE, ListingStatus.REPORTED}:
        return closed_keyboard(back=back)
    rows = [
        [
            {"text": "📞 تماس", "callback_data": f"v2:c:{listing.pk}"},
            {"text": "🔖 نشان شد" if saved else "🔖 نشان کردن", "callback_data": f"v2:s:{listing.pk}"},
        ]
    ]
    if listing.status == ListingStatus.ACTIVE:
        rows.append([{"text": "🚫 بسته شد؟ خبر دهید", "callback_data": f"v2:t:{listing.pk}"}])
    rows.append([{"text": "📷 عکس‌ها و جزئیات", "url": conf.mini_app_link(listing.pk)}])
    if back:
        rows.append([{"text": "‹ برگشت به نتایج", "callback_data": "v2:p:0"}])
    return rows


def closed_keyboard(*, back: bool = True) -> list[list[dict]]:
    rows = [[{"text": "🔔 گوش به زنگ مشابه", "callback_data": "v2:asearch"}, {"text": "🔎 آگهی‌های مشابه", "callback_data": "v2:p:0"}]]
    if back:
        rows.append([{"text": "‹ برگشت به نتایج", "callback_data": "v2:p:0"}])
    return rows


def closed_card(listing: HomeListing) -> str:
    title = channel_format.closed_text(listing).split("\n")[1]
    return f"🚫 <b>این آگهی بسته شد.</b>\n\n{title}"


def show_listing(chat_id: int, telegram_id: int, user: dict, listing: HomeListing) -> None:
    values = get_search(telegram_id)
    if values.get("query"):
        row = HomeSearchQuery.objects.filter(pk=values["query"], telegram_user_id=telegram_id).first()
        if row is not None and listing.pk not in (row.clicked_listing_ids or []):
            row.clicked_listing_ids = [*(row.clicked_listing_ids or []), listing.pk]
            row.save(update_fields=["clicked_listing_ids"])
    if listing.status not in {ListingStatus.ACTIVE, ListingStatus.REPORTED}:
        send(chat_id, closed_card(listing), closed_keyboard(), kind="other")
        return
    keyboard = card_keyboard(listing, saved=is_saved(person(user), listing))
    urls = photo_urls(listing)
    if urls:
        try:
            bot.call(
                "sendPhoto",
                {"chat_id": chat_id, "photo": urls[0], "caption": card_text(listing, limit=CAPTION_LIMIT), "parse_mode": "HTML", "reply_markup": {"inline_keyboard": keyboard}},
            )
            botlog.record(telegram_id, "out", "other", f"listing {listing.pk}")
            return
        except bot.BotError as exc:
            if exc.retry_after or exc.code in {401, 403, 429}:
                raise
            logger.info("Bot card photo skipped: %s", exc.description)
    send(chat_id, card_text(listing), keyboard, kind="other")


def contact_screen(listing: HomeListing, user: dict) -> tuple[str, list, str]:
    """Through the gate, the Mini App's own: text, keyboard, and the toast when membership has just been confirmed."""
    account = person(user)
    if account is None:
        return "حساب شما مسدود است. برای پیگیری به پشتیبانی پیام دهید.", [], ""
    try:
        found = gate.reveal(account, listing, source="bot_housing")
    except gate.GateRefused as refused:
        if refused.code == "not_member" and refused.channel is not None:
            name = refused.channel.title or f"چمدون خانه · {city_fa(refused.channel.city)}"
            return (
                f"📢 برای دیدن راه تماس، عضو کانال <b>{esc(name)}</b> شوید.",
                [[{"text": "📢 عضویت در کانال", "url": gate.join_url(refused.channel)}], [{"text": "✅ عضو شدم", "callback_data": f"v2:c:{listing.pk}"}]],
                "",
            )
        if refused.code == "gate_banned":
            return "شما در این کانال مسدود هستید، پس راه تماس باز نمی‌شود. برای پیگیری به پشتیبانی پیام دهید.", [], ""
        if refused.code == "reveal_cap":
            return f"امروز به سقف {fa(gate.REVEAL_CAP)} راه تماس رسیدید. فردا دوباره می‌توانید ببینید.", [], ""
        if refused.code in {"listing_gone", "listing_closed"}:
            return "این آگهی دیگر فعال نیست.", closed_keyboard(), ""
        return "راه تماس این آگهی را در چمدون خانه ببینید.", [[{"text": "🏠 باز کردن چمدون خانه", "url": conf.mini_app_link(listing.pk)}]], ""
    lines = [SAFETY_SHORT, ""]
    reach = []
    if found.telegram:
        reach.append(f"@{esc(found.telegram)}")
    if found.phone:
        reach.append(f"<code>{esc(found.phone)}</code>")
    if reach:
        lines.append("📞 " + " · ".join(reach))
    if found.source_url:
        lines.append(f'🔗 <a href="{esc(found.source_url)}">پست اصلی</a>')
    if not found.has_any:
        lines.append("راه تماسی در آگهی نیست. جزئیات را در چمدون خانه ببینید.")
    keyboard = []
    if found.telegram:
        keyboard.append([{"text": "💬 پیام در تلگرام", "url": found.telegram_url}])
    second = []
    if found.phone:
        second.append({"text": "📋 کپی شماره", "copy_text": {"text": found.phone}})
    if found.source_url:
        second.append({"text": "🔗 پست اصلی", "url": found.source_url})
    if second:
        keyboard.append(second)
    toast = "✅ عضویت تأیید شد" if found.membership_checked else ""
    return "\n".join(lines), keyboard, toast


def saved_screen(user: dict) -> tuple[str, list]:
    account = person(user)
    rows = list(HomeSaved.objects.filter(user=account).select_related("listing", "listing__post")[:10]) if account else []
    if not rows:
        return "🔖 <b>نشان‌شده‌ها</b>\n\nهنوز آگهی‌ای نشان نکرده‌اید. در هر آگهی «🔖 نشان کردن» را بزنید.", []
    channel = botchat.remembered_city(int(user["id"]))
    lines = []
    for number, row in enumerate(rows, start=1):
        metro = channel or HomeChannel(city=row.listing.city, country=row.listing.country)
        lines.append(result_line(row.listing, number, metro))
    buttons = [{"text": fa(number), "callback_data": f"v2:d:{row.listing_id}"} for number, row in enumerate(rows, start=1)]
    head = f"🔖 <b>نشان‌شده‌ها</b>\n{fa(len(rows))} آگهی · ۱ تا {fa(len(rows))}\n\n"
    return head + "\n".join(lines), [*rows_of(buttons, 3), [{"text": "🗑 پاک کردن فهرست", "callback_data": "v2:sx"}]]


# ---------------------------------------------------------------------------
# Alerts: setup, list, one alert.


def alert_areas(alert: HomeAlert) -> list[str]:
    """The areas of an alert as the person gave them. An intersection («Yonge و Finch») stays one."""
    return [name.strip() for name in (alert.area or "").split(",") if name.strip()]


def alert_summary(alert: HomeAlert) -> str:
    parts = []
    names = [TYPE_LABEL.get(kind, kind) for kind in alert.property_types or []]
    if alert.min_rooms and "apartment" in (alert.property_types or []):
        names = [f"{fa(int(alert.min_rooms))} خوابه" if name == "آپارتمان" else name for name in names]
    if alert.country == "DE":
        names = ["اتاق WG" if name == "اتاق" else name for name in names]
    if names:
        parts.append("، ".join(names))
    if alert.area:
        parts.append("، ".join(place_fa(name) for name in alert_areas(alert)))
    if alert.max_price:
        parts.append(f"تا {money_fa(alert.max_price, alert.currency)}" + (" گرم" if alert.country == "DE" else ""))
    return " · ".join(parts) or "همه‌ی آگهی‌ها"


def alert_head(alert: HomeAlert) -> str:
    return f"🔔 <b>گوش به زنگ {esc(city_fa(alert.city))}</b>"


def begin_alert(user: dict, chat_id: int, channel: HomeChannel, answers: dict | None = None) -> None:
    alert = botchat.new_alert(user, chat_id, channel)
    telegram_id = int(user["id"])
    if alert is None:
        send(chat_id, *cap_screen(telegram_id), kind="other")
        return
    for code, value in (answers or {}).items():
        botchat.apply_answer(alert, code, value)
    send(chat_id, *next_alert_screen(alert, telegram_id), kind="other")


def next_alert_screen(alert: HomeAlert, telegram_id: int) -> tuple[str, list]:
    answered = set(alert.answered or [])
    if "type" not in answered:
        return type_screen(alert)
    if "budget" not in answered:
        return budget_screen(alert, telegram_id)
    if "area" not in answered:
        return area_screen(alert)
    return saved_alert_screen(alert)


def type_options(alert: HomeAlert) -> list[tuple[str, str]]:
    if alert.country == "DE":
        return [("r", "اتاق WG"), ("a", "آپارتمان")]
    return [("r", "اتاق"), ("s", "استودیو"), ("b", "بیسمنت"), ("1", "۱ خوابه"), ("2", "۲ خوابه"), ("3", "۳ خوابه+"), ("h", "خانه‌ی کامل")]


def picked_types(alert: HomeAlert) -> set[str]:
    kinds = alert.property_types or []
    codes = {code for code, kind in (("r", "room"), ("s", "studio"), ("b", "basement"), ("h", "house")) if kind in kinds}
    if "apartment" in kinds:
        codes.add(str(int(alert.min_rooms)) if alert.min_rooms else "a")
    return codes


def toggle_type(alert: HomeAlert, code: str) -> None:
    kinds = list(alert.property_types or [])
    single = {"r": "room", "s": "studio", "b": "basement", "h": "house"}
    if code in single:
        kind = single[code]
        kinds = [item for item in kinds if item != kind] if kind in kinds else [*kinds, kind]
    elif code == "a" or code in {"1", "2", "3"}:
        # Bedrooms are one number on the alert (at least n), so a second tap moves it instead of adding one.
        same = "apartment" in kinds and (code == "a" or (alert.min_rooms and int(alert.min_rooms) == int(code)))
        kinds = [item for item in kinds if item != "apartment"]
        alert.min_rooms = None
        if not same:
            kinds.append("apartment")
            alert.min_rooms = None if code == "a" else Decimal(code)
    alert.property_types = kinds
    alert.save(update_fields=["property_types", "min_rooms"])


def type_screen(alert: HomeAlert) -> tuple[str, list]:
    picked = picked_types(alert)
    pk = alert.pk
    buttons = [{"text": f"{'✓ ' if code in picked else ''}{label}", "callback_data": f"v2:at:{pk}:{code}"} for code, label in type_options(alert)]
    rows = rows_of(buttons, 3 if alert.country != "DE" else 2)
    rows.append([{"text": "همه", "callback_data": f"v2:at:{pk}:x"}])
    rows.append([{"text": "ادامه", "callback_data": f"v2:ac:{pk}"}, MENU_BUTTON])
    text = f"{alert_head(alert)}\n۳ سؤال کوتاه؛ بعد هر آگهی تازه که بخورد همان لحظه اینجا می‌آید.\n\n<b>۱ از ۳</b> · دنبال چه جایی هستید؟\nیک یا چند مورد را بزنید."
    return text, rows


def budget_screen(alert: HomeAlert, telegram_id: int) -> tuple[str, list]:
    wait_for(telegram_id, "v2budget", {"alert": alert.pk})
    bands = BANDS.get(alert.currency, ())
    german = alert.country == "DE"
    label = (lambda band: f"تا {money_fa(Decimal(band), alert.currency)}") if german else (lambda band: f"تا {fa(f'{band:,}'.replace(',', '٬'))}")
    buttons = [{"text": label(band), "callback_data": f"v2:ab:{alert.pk}:{band}"} for band in bands]
    rows = rows_of(buttons, 3 if not german else 2)
    rows.append([{"text": "همه", "callback_data": f"v2:ab:{alert.pk}:0"}])
    rows.append([{"text": "‹ برگشت", "callback_data": f"v2:ag:{alert.pk}:t"}, MENU_BUTTON])
    example = "۷۰۰" if german else "۱۲۰۰"
    text = (
        f"{alert_head(alert)}\n<i>{esc(alert_summary(alert))}</i>\n\n"
        f"<b>۲ از ۳</b> · سقف اجاره‌ی ماهانه ({CURRENCY_NAME.get(alert.currency, alert.currency)})؟\n"
        f"عددش را بنویسید (مثلاً {example}) یا یکی را بزنید."
    )
    return text, rows


def area_screen(alert: HomeAlert) -> tuple[str, list]:
    if not alert.awaiting_area:
        alert.awaiting_area = True
        alert.save(update_fields=["awaiting_area"])
    picked = {fold(name) for name in alert_areas(alert)}
    pk = alert.pk
    options = botchat.area_options(alert)
    buttons = [
        {"text": f"{'✓ ' if fold(value) in picked else ''}{label} {fa(count)}", "callback_data": f"v2:aa:{pk}:{index}"}
        for index, (label, value, count) in enumerate(options)
    ]
    rows = [[{"text": "📍 همه‌جا", "callback_data": f"v2:aw2:{pk}"}], *rows_of(buttons, 2)]
    channel = metro_channel(alert.country, alert.city)
    if channel is not None and metro_rows(channel):
        rows.append([{"text": "بیشتر…", "callback_data": f"v2:am:{pk}"}])
    names = alert_areas(alert)
    if names:
        rows.append([{"text": f"ذخیره با {fa(len(names))} محله", "callback_data": f"v2:as:{pk}"}])
    rows.append([{"text": "‹ برگشت", "callback_data": f"v2:ag:{pk}:b"}, MENU_BUTTON])
    hint = "چند مورد را بزنید، یا اسم محله یا کد پستی را بنویسید (مثلاً ۱۲۰۴۳)." if alert.country == "DE" else "چند مورد را بزنید، یا اسم محله، خیابان یا تقاطع را بنویسید."
    text = f"{alert_head(alert)}\n<i>{esc(alert_summary(alert))}</i>\n\n<b>۳ از ۳</b> · کدام محله‌ها؟\n{hint}"
    if names:
        text += "\n<i>انتخاب شما: " + esc(" · ".join(place_fa(name) for name in names)) + "</i>"
    return text, rows


def more_areas(alert: HomeAlert, group: str = "") -> tuple[str, list]:
    channel = metro_channel(alert.country, alert.city)
    rows_all = metro_rows(channel) if channel is not None else []
    groups = list(dict.fromkeys(row.group or "دیگر" for row in rows_all))
    current = group if group in groups else (groups[0] if groups else "")
    tabs = [
        {"text": f"{'✓ ' if name == current else ''}{name}", "callback_data": f"v2:am:{alert.pk}:{index}"}
        for index, name in enumerate(groups)
    ]
    picked = {fold(name) for name in alert_areas(alert)}
    chips_row = [
        {"text": f"{'✓ ' if fold(row.canonical) in picked else ''}{row.fa}", "callback_data": f"v2:ao:{alert.pk}:{row.pk}"}
        for row in rows_all
        if (row.group or "دیگر") == current
    ]
    keyboard = [*rows_of(tabs, 3), *rows_of(chips_row, 2), [{"text": "‹ برگشت", "callback_data": f"v2:ag:{alert.pk}:w"}, {"text": "تأیید", "callback_data": f"v2:ag:{alert.pk}:w"}]]
    return f"📍 <b>محله‌های بیشتر · {esc(city_fa(alert.city))}</b>\nاول ناحیه را بزنید، بعد محله‌ها را.", keyboard


def toggle_area_name(alert: HomeAlert, value: str) -> bool:
    names = alert_areas(alert)
    same = [name for name in names if fold(name) == fold(value)]
    names = [name for name in names if name not in same] if same else [*names, value]
    joined = ", ".join(names)
    if len(joined) > HomeAlert._meta.get_field("area").max_length:
        return False
    alert.area = joined
    alert.save(update_fields=["area"])
    return True


def finish_answer(alert: HomeAlert, field: str) -> None:
    """The three setup questions were answered; «rooms» came with the type question, so it is never asked again."""
    answered = set(alert.answered or []) | {field}
    if {"type", "budget", "area"} <= answered:
        answered.add("rooms")
    alert.answered = sorted(answered)
    alert.complete = all(name in answered for name in ("type", "budget", "area"))
    alert.save(update_fields=["answered", "complete"])


def quiet_line(alert: HomeAlert) -> str:
    return f"🌙 از ۲۳ تا ۸ (وقت {esc(city_fa(alert.city))}) بی‌صدا می‌مانم؛ آگهی‌های شب ساعت ۸ می‌رسند."


def saved_alert_screen(alert: HomeAlert) -> tuple[str, list]:
    lines = ["🔔 <b>گوش به زنگ شما ثبت شد</b>", ""]
    kinds = [TYPE_LABEL.get(kind, kind) for kind in alert.property_types or []]
    lines.append(f"🏠 {esc(alert_summary_types(alert)) if kinds else 'همه'}")
    lines.append("📍 " + esc("، ".join(place_fa(name) for name in alert_areas(alert)) or "همه‌جا"))
    if alert.max_price:
        lines.append(f"💶 تا {esc(money_fa(alert.max_price, alert.currency))}")
    lines += ["⚡ همان لحظه", "", "از الان هر آگهی تازه که بخورد، همین‌جا می‌آید.", f"<i>{quiet_line(alert)}</i>"]
    answered = set(alert.answered or [])
    strict = [label for field, label in (("gender", "برای چه کسی"), ("registration", "ملد")) if field not in answered and (field != "registration" or alert.country == "DE")]
    if strict:
        # `alerts.matches` holds an unanswered question strict; the person should know that, whatever the board omits.
        lines.append(f"<i>🎯 تا «{'، '.join(strict)}» را جواب نداده‌اید، فقط آگهی‌هایی می‌آید که آن را گفته باشند.</i>")
    pk = alert.pk
    keyboard = [
        [{"text": "🎯 دقیق‌ترش کنید", "callback_data": f"v2:rf:{pk}"}],
        [{"text": "✏️ ویرایش", "callback_data": f"v2:ae:{pk}"}, {"text": "➕ یکی دیگر", "callback_data": "v2:anew"}],
        [{"text": "🔎 آگهی‌های فعلی", "callback_data": f"v2:aq:{pk}"}, {"text": "📋 همه‌ی گوش به زنگ‌ها", "callback_data": "v2:al"}],
    ]
    channel = metro_channel(alert.country, alert.city)
    if channel is not None and channel.username:
        keyboard.append([{"text": f"📣 کانال {city_fa(alert.city)}", "url": gate.join_url(channel)}])
    return "\n".join(lines), keyboard


def alert_summary_types(alert: HomeAlert) -> str:
    keep = HomeAlert(country=alert.country, property_types=alert.property_types, min_rooms=alert.min_rooms)
    return alert_summary(keep)


def refine_list(alert: HomeAlert) -> list[str]:
    return ["gender", "term", *(["registration"] if alert.country == "DE" else [])]


def refine_screen(alert: HomeAlert, field: str = "") -> tuple[str, list]:
    fields = refine_list(alert)
    answered = set(alert.answered or [])
    field = field or next((name for name in fields if name not in answered), fields[0])
    step = fields.index(field) + 1
    pk = alert.pk
    head = f"🎯 <b>دقیق‌تر کردن گوش به زنگ</b>\n<i>{esc(alert_summary(alert))}</i>\n\nسؤال اختیاری {fa(step)} از {fa(len(fields))}"
    done = {"text": "کافی است؛ ذخیره", "callback_data": f"v2:av:{pk}"}
    back = {"text": "‹ برگشت", "callback_data": f"v2:rq:{pk}:{fields[step - 2]}"}
    last = [back, done] if step > 1 else [done]
    if field == "gender":
        buttons = [{"text": label, "callback_data": f"v2:ra:{pk}:g:{code}"} for code, label in (("f", "خانم"), ("m", "آقا"), ("c", "زوج"))]
        chosen = {"female": 0, "male": 1, "couple": 2}.get(alert.gender) if "gender" in answered else None
        if chosen is not None:
            buttons[chosen]["text"] = "✓ " + buttons[chosen]["text"]
        return f"{head} · 👤 برای چه کسی؟", [buttons, [{"text": "همه", "callback_data": f"v2:ra:{pk}:g:x"}], last]
    if field == "term":
        german = alert.country == "DE"
        options = [("l", "بلندمدت"), ("s", "Zwischenmiete" if german else "سابلت")]
        options.append(("t", "Nachmieter" if german else LEASE_TRANSFER_FA))
        if not german:
            options.append(("c", "کوتاه‌مدت"))
        current = {"lease": "l", "sublet": "s", "short": "c"}.get(alert.term, "")
        buttons = [{"text": f"{'✓ ' if code == current else ''}{label}", "callback_data": f"v2:ra:{pk}:m:{code}"} for code, label in options]
        return f"{head} · 📅 نوع اجاره؟", [*rows_of(buttons, 3 if not german else 2), last]
    return f"{head} · 📝 ملد (Anmeldung) لازم دارید؟", [[{"text": "بله، فقط با ملد", "callback_data": f"v2:ra:{pk}:d:y"}, {"text": "همه", "callback_data": f"v2:ra:{pk}:d:x"}], last]


def status_words(alert: HomeAlert) -> str:
    if not alert.active:
        return "⏸ خاموش"
    if is_paused(alert):
        local = alert.paused_until.astimezone(city_zone(alert.country, alert.city))
        return f"⏸ مکث تا {fa(local.day)} {MONTHS[local.month - 1]}"
    return f"⚡ همان لحظه · {fa(len(search(alert, days=7)))} آگهی این هفته"


def alerts_screen(telegram_id: int, note: str = "") -> tuple[str, list]:
    alerts = list(user_alerts(telegram_id))
    prefix = f"{note}\n\n" if note else ""
    new_button = {"text": "➕ گوش به زنگ جدید", "callback_data": "v2:anew"}
    if not alerts:
        return f"{prefix}🔔 <b>گوش به زنگ‌های شما</b>\n\nهنوز گوش به زنگی ندارید.", [[new_button]]
    lines = [f"🔔 <b>گوش به زنگ‌های شما</b>\n{fa(active_count(telegram_id))} مورد از حداکثر {fa(MAX_ALERTS_PER_USER)}"]
    for alert in alerts:
        lines.append(f"\n<b>{esc(alert_summary(alert))}</b>\n<i>{status_words(alert)}</i>")
    buttons = [{"text": alert_summary(alert)[:30], "callback_data": f"v2:ad:{alert.pk}"} for alert in alerts]
    keyboard = [*rows_of(buttons, 2), [new_button, {"text": "🏠 مدیریت در مینی‌اپ", "url": conf.mini_app_link(start="home_alerts")}]]
    return prefix + "\n".join(lines), keyboard


def cap_screen(telegram_id: int) -> tuple[str, list]:
    return (
        f"✋ {fa(MAX_ALERTS_PER_USER)} گوش به زنگ روشن دارید که سقف است. برای یکی تازه، اول یکی را مکث یا حذف کنید.",
        [[{"text": "📋 گوش به زنگ‌های من", "callback_data": "v2:al"}]],
    )


def alert_screen(alert: HomeAlert) -> tuple[str, list]:
    lines = ["🔔 <b>گوش به زنگ شما</b>", "", f"🏠 {esc(alert_summary_types(alert))}"]
    lines.append("📍 " + esc("، ".join(place_fa(name) for name in alert_areas(alert)) or "همه‌جا"))
    if alert.max_price:
        lines.append(f"💶 تا {esc(money_fa(alert.max_price, alert.currency))}")
    lines.append(status_words(alert))
    pk = alert.pk
    paused = not alert.active or is_paused(alert)
    toggle = {"text": "▶️ ادامه", "callback_data": f"v2:ay:{pk}"} if paused else {"text": "⏸ مکث", "callback_data": f"v2:ap:{pk}"}
    keyboard = [
        [{"text": "✏️ ویرایش", "callback_data": f"v2:ae:{pk}"}, toggle, {"text": "🗑 حذف", "callback_data": f"v2:ax:{pk}"}],
        [{"text": "🔎 آگهی‌ها", "callback_data": f"v2:aq:{pk}"}, {"text": "‹ برگشت", "callback_data": "v2:al"}],
    ]
    return "\n".join(lines), keyboard


def alert_edit_menu(alert: HomeAlert) -> tuple[str, list]:
    pk = alert.pk
    keyboard = [
        [{"text": "🏠 نوع", "callback_data": f"v2:ag:{pk}:t"}, {"text": "💶 بودجه", "callback_data": f"v2:ag:{pk}:b"}],
        [{"text": "📍 محله", "callback_data": f"v2:ag:{pk}:w"}, {"text": "🎯 سؤال‌های بیشتر", "callback_data": f"v2:rf:{pk}"}],
        [{"text": "‹ برگشت", "callback_data": f"v2:ad:{pk}"}],
    ]
    return f"✏️ <b>ویرایش گوش به زنگ</b>\n<i>{esc(alert_summary(alert))}</i>\n\nکدام را عوض کنیم؟", keyboard


def search_from_alert(alert: HomeAlert) -> dict:
    channel = metro_channel(alert.country, alert.city)
    values: dict = {"ch": channel.pk if channel is not None else 0}
    if alert.property_types:
        values["types"] = list(alert.property_types)
    if alert.min_rooms:
        values["rooms"] = int(alert.min_rooms)
    if alert.max_price:
        values["max"] = int(alert.max_price)
    if alert.area:
        values["areas"] = alert_areas(alert)
    if alert.gender and alert.gender != "any":
        values["gender"] = alert.gender
    if alert.term and alert.term != "any":
        values["term"] = alert.term
    return values


def alert_from_search(user: dict, chat_id: int, values: dict, channel: HomeChannel) -> HomeAlert | None:
    """«🔔 گوش به زنگ همین»: the current search becomes an alert, with nothing left to ask."""
    alert = botchat.new_alert(user, chat_id, channel)
    if alert is None:
        return None
    model = criteria(values, int(user["id"]), channel)
    alert.property_types = model.property_types
    alert.min_rooms = model.min_rooms
    alert.max_price = model.max_price
    alert.area = model.area[: HomeAlert._meta.get_field("area").max_length]
    alert.gender = model.gender
    alert.term = model.term
    alert.search = {key: value for key, value in model.search.items() if key != "by"}
    alert.answered = sorted({"type", "budget", "area", "rooms", "term", *(["gender"] if values.get("gender") else [])})
    alert.complete = True
    alert.save()
    return alert


# ---------------------------------------------------------------------------
# Help, safety, waitlist, posting.


HELP_TEXT = (
    "❓ <b>راهنما</b>\n"
    "🔎 بنویسید چه خانه‌ای می‌خواهید؛ من آگهی‌ها را پیدا می‌کنم.\n"
    f"🔔 گوش به زنگ: ۳ سؤال، بعد هر آگهی که بخورد همان لحظه می‌رسد (تا {fa(MAX_ALERTS_PER_USER)} مورد).\n"
    "🌙 از ۲۳ تا ۸ (وقت شهر شما) پیام نمی‌فرستم؛ آگهی‌های شب ساعت ۸ می‌رسند.\n"
    "🔕 /stop همه‌ی پیام‌ها را خاموش می‌کند.\n"
    "🔒 پیام‌ها برای بهتر شدن جستجو ذخیره می‌شوند؛ /forget آن‌ها را پاک می‌کند."
)
SAFETY_BASE = "قبل از دیدن خانه و امضای قرارداد، هیچ پولی (ودیعه، رزرو، کارت هدیه) نفرستید.\nما آگهی‌ها را جمع می‌کنیم و صاحبشان را تأیید نکرده‌ایم."


def help_keyboard() -> list[list[dict]]:
    support = conf.mini_app_link(start="home_support")
    return [
        [{"text": "📍 شهر من", "callback_data": "v2:city"}, {"text": "🌐 زبان", "callback_data": "v2:langs"}],
        [{"text": "🔔 گوش به زنگ‌ها", "callback_data": "v2:al"}, {"text": "🔖 نشان‌شده‌ها", "callback_data": "v2:saved"}],
        [{"text": "📋 آگهی‌های من", "callback_data": "v2:mine"}, {"text": "🛡 ایمنی", "callback_data": "v2:safety:0"}],
        [{"text": "💬 پشتیبانی", "url": support}, {"text": "🗑 پاک کردن پیام‌ها", "callback_data": "v2:forget"}],
    ]


def safety_screen(channel: HomeChannel | None) -> tuple[str, list]:
    channel = channel or next(iter(botchat.cities()), None)
    if channel is None:
        return f"🛡 <b>ایمنی</b>\n{SAFETY_BASE}", [[MENU_BUTTON]]
    lines = [f"🛡 <b>ایمنی در {esc(city_fa(channel.city))}</b>", SAFETY_BASE, "اگر کسی پیش از بازدید پول خواست، نشانه‌ی کلاهبرداری است؛ " + REPORT_OUT]
    others = [{"text": city_fa(other.city), "callback_data": f"v2:safety:{other.pk}"} for other in botchat.cities() if other.pk != channel.pk]
    return "\n".join(lines), [*rows_of(others, 3), [{"text": "‹ برگشت", "callback_data": "v2:help"}]]


def join_waitlist(chat_id: int, user: dict, name: str) -> None:
    telegram_id = int(user["id"])
    clean = re.sub(r"\s+", " ", name).strip()[:64]
    account = person(user)
    if account is not None and clean:
        HomeCityWaitlist.objects.get_or_create(user=account, country="", city_key=fold(clean) or clean.casefold(), defaults={"city_name": clean})
    channel = botchat.remembered_city(telegram_id)
    buttons = [MENU_BUTTON]
    if channel is not None:
        buttons.insert(0, {"text": f"📍 {city_fa(channel.city)} را ببینید", "callback_data": "v2:p:0"})
    send(chat_id, f"<b>{esc(clean)}</b> هنوز در چمدون نیست. به لیست انتظار اضافه شدید و وقتی آماده شد خبرتان می‌کنیم.", [buttons], kind="other")


def post_screen(telegram_id: int) -> tuple[str, list]:
    channel = botchat.remembered_city(telegram_id)
    where = f"کانال {city_fa(channel.city)}" if channel is not None else "کانال شهر"
    text = f"آگهی خود را در <b>چمدون خانه</b> ثبت کنید؛ رایگان است و بعد از یک بررسی کوتاه در {esc(where)} منتشر می‌شود.\n\nچه چیزی دارید؟"
    return text, [
        [{"text": "🏠 خانه دارم", "url": conf.mini_app_link()}, {"text": "🔎 دنبال خانه‌ام", "url": conf.mini_app_link()}],
        [MENU_BUTTON],
    ]


def mine_screen(telegram_id: int) -> tuple[str, list]:
    rows = list(
        HomeListing.objects.filter(post__author_id=telegram_id, post__source__username__startswith="app_").select_related("post").order_by("-first_seen")[:5]
    )
    if not rows:
        return "🏠 <b>آگهی‌های من</b>\n\nهنوز آگهی‌ای ثبت نکرده‌اید.", [[{"text": "➕ ثبت آگهی", "callback_data": "v2:post"}]]
    lines, keyboard = ["🏠 <b>آگهی‌های من</b>"], []
    words = {ListingStatus.ACTIVE: "فعال", ListingStatus.RENTED: "بسته شد", ListingStatus.EXPIRED: "منقضی", ListingStatus.REPORTED: "در بررسی"}
    for number, listing in enumerate(rows, start=1):
        state = "در بررسی" if listing.needs_review else words.get(listing.status, "")
        lines.append(f"{fa(number)}. {esc(freshness.listing_title(listing))}\n<i>{state}</i>")
        row = [{"text": f"{fa(number)} · ✏️ ویرایش", "url": conf.mini_app_link(listing.pk)}]
        if listing.status == ListingStatus.ACTIVE:
            row.append({"text": "🚫 بسته شد", "callback_data": f"v2:t:{listing.pk}"})
        keyboard.append(row)
    return "\n".join(lines), keyboard


# ---------------------------------------------------------------------------
# The match message: Bot2-06. home/alerts.py sends it through these for a person on v2.


def match_text(alert: HomeAlert, listing: HomeListing) -> str:
    fits = fits_line(alert).replace("✓ می‌خوره", "می‌خورد")
    tail = f"\n\n<i>{esc(fits)}</i>" if fits else ""
    return f"🔔 <b>آگهی تازه برای گوش به زنگ شما</b>\n<i>{esc(alert_summary(alert))}</i>\n\n{card_text(listing, limit=3300)}{tail}"


def match_keyboard(alert: HomeAlert, listing: HomeListing, *, feedback: bool = True) -> dict:
    rows = [[{"text": "📞 جزئیات و تماس", "callback_data": f"v2:m:{listing.pk}"}, {"text": "🔖 نشان کردن", "callback_data": f"v2:s:{listing.pk}"}]]
    if feedback:
        rows.append([{"text": "👍 به کارم آمد", "callback_data": f"v2:fu:{listing.pk}"}, {"text": "👎 به کارم نمی‌آید", "callback_data": f"v2:fd:{listing.pk}"}])
    rows.append([{"text": "⏸ یک هفته مکث", "callback_data": f"v2:ap:{alert.pk}"}, {"text": "🔕 خاموش", "callback_data": f"v2:ao0:{alert.pk}"}])
    return {"inline_keyboard": rows}


def match_summary(items: list[tuple[HomeAlert, HomeListing]]) -> str:
    lines = []
    for number, (_alert, listing) in enumerate(items[:3], start=1):
        price = money_fa(listing.price_amount, listing.price_currency) if listing.price_amount else "قیمت: بپرسید"
        kind = channel_format.rooms_word(listing) or type_word(listing) or "ملک"
        place = channel_format.district(listing, channel_format.metro_of(listing))
        lines.append(f"{fa(number)}. 💶 {esc(price)} · {esc(kind)} · {esc(place)} · {freshness.relative_time(posted_at(listing))}")
    more = len(items) - 3
    if more > 0:
        lines.append(f"… و {fa(more)} آگهی دیگر در «🔎 آگهی‌های فعلی».")
    return f"🔔 <b>{fa(len(items))} آگهی تازه برای گوش به زنگ شما</b>\n\n<blockquote>" + "\n".join(lines) + "</blockquote>"


def match_summary_keyboard(alert: HomeAlert) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🔎 آگهی‌های فعلی", "callback_data": f"v2:aq:{alert.pk}"}],
            [{"text": "⏸ یک هفته مکث", "callback_data": f"v2:ap:{alert.pk}"}, {"text": "🔕 خاموش", "callback_data": f"v2:ao0:{alert.pk}"}],
        ]
    }


def reason_rows(listing_id: int) -> list[list[dict]]:
    options = (("p", "💶 قیمت"), ("a", "📍 محله"), ("t", "🏷 نوع"), ("o", "🕰 قدیمی بود"))
    return [[{"text": label, "callback_data": f"v2:fr:{listing_id}:{code}"} for code, label in options[:2]], [{"text": label, "callback_data": f"v2:fr:{listing_id}:{code}"} for code, label in options[2:]]]


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
    telegram_id = int(user["id"])
    words = next((set_ for set_ in KB.values() if text in (set_["search"], set_["alerts"], set_["post"], set_["saved"], set_["pick"], set_["help"]) or set_["city"] in text), None)
    if words is None:
        return False
    stop_waiting(telegram_id)
    botlog.record(telegram_id, "in", "menu", text)
    if text == words["search"]:
        if botchat.remembered_city(telegram_id) is None:
            send(chat_id, "<b>کشور شما؟</b>", country_rows(), kind="menu")
        else:
            send(chat_id, *write_prompt(), kind="menu")
    elif text == words["alerts"]:
        send(chat_id, *alerts_screen(telegram_id), kind="menu")
    elif text == words["post"]:
        send(chat_id, *post_screen(telegram_id), kind="menu")
    elif text == words["saved"]:
        send(chat_id, *saved_screen(user), kind="menu")
    elif text == words["help"]:
        send(chat_id, HELP_TEXT, help_keyboard(), kind="menu")
    else:
        send(chat_id, *city_list(telegram_id), kind="menu")
    return True


def handle_message(message: dict, chat_id: int, user: dict) -> None:
    text = (message.get("text") or "").strip()
    telegram_id = int(user["id"])
    if not text:
        botlog.record(telegram_id, "in", "other", "(not text)")
        send(chat_id, NOT_TEXT)
        return
    command = botchat._COMMAND.match(text)
    if command:
        run_command(command.group(1).lower(), (command.group(2) or "").strip(), chat_id, user, text)
        return
    if keyboard_button(text, chat_id, user):
        return
    state = state_of(telegram_id)
    if state.waiting == "v2city":
        stop_waiting(telegram_id)
        botlog.record(telegram_id, "in", "text", text)
        join_waitlist(chat_id, user, text)
        return
    waiting = HomeAlert.objects.filter(telegram_user_id=telegram_id, awaiting_area=True).order_by("-created_at").first()
    if waiting is not None:
        botlog.record(telegram_id, "in", "text", text)
        added = [name.strip() for name in re.split(r"[,،]| یا ", text) if name.strip()]
        for name in added[:3]:
            toggle_area_name(waiting, name[:48])
        send(chat_id, *area_screen(waiting))
        return
    if state.waiting == "v2budget":
        alert = HomeAlert.objects.filter(pk=(state.data or {}).get("alert") or 0, telegram_user_id=telegram_id).first()
        if alert is not None:
            botlog.record(telegram_id, "in", "text", text)
            amount = typed_amount(text)
            if amount is None:
                send(chat_id, f"یک عدد بنویسید، مثلاً {'۷۰۰' if alert.country == 'DE' else '۱۱۰۰'}. یا یکی از دکمه‌ها را بزنید («همه» هم هست).")
                return
            stop_waiting(telegram_id)
            botchat.apply_answer(alert, "b", str(amount))
            send(chat_id, *next_alert_screen(alert, telegram_id))
            return
        stop_waiting(telegram_id)
    free_text(chat_id, telegram_id, text)


def run_command(name: str, arg: str, chat_id: int, user: dict, raw: str) -> None:
    telegram_id = int(user["id"])
    stop_waiting(telegram_id)
    botlog.record(telegram_id, "in", "command", raw)
    if name == "start":
        start(chat_id, user, arg)
    elif name in {"alerts", "myalerts"}:
        send(chat_id, *alerts_screen(telegram_id), kind="menu")
    elif name == "search":
        send(chat_id, *write_prompt(), kind="menu")
    elif name == "saved":
        send(chat_id, *saved_screen(user), kind="menu")
    elif name == "help":
        send(chat_id, HELP_TEXT, help_keyboard(), kind="menu")
    elif name == "lang":
        send(chat_id, *lang_screen(telegram_id), kind="menu")
    elif name == "forget":
        send(chat_id, *forget_screen(), kind="menu")
    elif name == "stop":
        stopped = HomeAlert.objects.filter(telegram_user_id=telegram_id, active=True).update(active=False)
        capture("alerts_stopped", telegram_id=telegram_id, properties={"count": stopped}, **HOUSING)
        if stopped:
            send(
                chat_id,
                "🔕 همه‌ی گوش به زنگ‌ها خاموش شد. دیگر پیامی نمی‌فرستم.",
                [[{"text": "🔔 روشن کردن دوباره", "callback_data": f"v2:re:{stopped}"}, {"text": "📋 گوش به زنگ‌ها", "callback_data": "v2:al"}]],
            )
        else:
            send(chat_id, "گوش به زنگ روشنی نداشتید.", [[{"text": "📋 گوش به زنگ‌ها", "callback_data": "v2:al"}]])
    elif name == "cancel":
        send(chat_id, "لغو شد.")
    else:
        start(chat_id, user, "")


def lang_screen(telegram_id: int) -> tuple[str, list]:
    current = locale_of(telegram_id)
    return "زبان را انتخاب کنید · Choose your language", [
        [
            {"text": f"{'✓ ' if current != 'en' else ''}فارسی", "callback_data": "v2:lang:fa"},
            {"text": f"{'✓ ' if current == 'en' else ''}English", "callback_data": "v2:lang:en"},
        ]
    ]


def forget_screen() -> tuple[str, list]:
    return "🗑 همه‌ی پیام‌ها و جستجوهای ذخیره‌شده‌ی شما پاک شود؟\nگوش به زنگ‌ها و نشان‌شده‌ها می‌مانند.", [
        [{"text": "🗑 بله، پاک شود", "callback_data": "v2:fy"}, {"text": "‹ نه، برگردیم", "callback_data": "v2:menu"}]
    ]


# Buttons on a match message or a card, which can come in the middle of a question, and the area picker itself.
KEEPS_WAITING = {"m", "d", "c", "s", "fu", "fd", "fr", "ap", "ao0", "am", "ao"}


def handle_callback(callback: dict) -> None:
    data = str(callback.get("data") or "")
    if data.startswith("hx:"):
        botactions.handle_callback(callback)
        return
    if not data.startswith("v2:"):
        # A button from before the switch keeps doing what it did.
        botchat.handle_callback(callback)
        return
    user = callback.get("from") or {}
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id") or user.get("id")
    message_id = message.get("message_id")
    if not user.get("id") or not chat_id:
        botchat.answer(callback.get("id", ""))
        return
    telegram_id = int(user["id"])
    botlog.record(telegram_id, "in", "callback", data)
    parts = data.split(":")[1:]
    if parts and parts[0] not in KEEPS_WAITING:
        # Whatever was being typed is over, or the next message would land in the old question.
        stop_waiting(telegram_id)
    note = ""
    try:
        note = dispatch(parts, user, int(chat_id), message_id, telegram_id)
    finally:
        botchat.answer(callback.get("id", ""), note or "")


def _listing(raw: str) -> HomeListing | None:
    """A listing a button may open. One held for review is hidden, as in `public.visible_listing`."""
    listing = botactions._listing(raw)
    return None if listing is None or listing.needs_review else listing


def dispatch(parts: list[str], user: dict, chat_id: int, message_id, telegram_id: int) -> str:
    action = parts[0] if parts else ""
    if action.startswith(("a", "r")) and action not in {"ask", "ar", "aw"}:
        result = alert_callback(action, parts[1:], user, chat_id, message_id, telegram_id)
        if result is not None:
            return result
    if action == "menu":
        stop_waiting(telegram_id)
        channel = botchat.remembered_city(telegram_id)
        send(chat_id, *(write_prompt() if channel is not None else ("<b>کشور شما؟</b>", country_rows())), kind="menu")
        return ""
    if action == "cc" and len(parts) == 2:
        rows = city_rows(parts[1].upper())
        if not rows:
            return OLD_BUTTON
        edit(chat_id, message_id, "<b>کدام شهر؟</b>", [*rows, [{"text": "↩️ کشور", "callback_data": "v2:countries"}, {"text": "🌍 شهر دیگر؟", "callback_data": "v2:other"}]], kind="menu")
        return ""
    if action == "countries":
        edit(chat_id, message_id, "<b>کشور شما؟</b>", country_rows(), kind="menu")
        return ""
    if action == "ct" and len(parts) == 2 and parts[1].isdigit():
        channel = HomeChannel.objects.filter(pk=int(parts[1]), enabled=True).exclude(city="").first()
        if channel is None:
            return OLD_BUTTON
        pick_city(chat_id, telegram_id, channel)
        return ""
    if action == "city":
        send(chat_id, *city_list(telegram_id), kind="menu")
        return ""
    if action == "other":
        wait_for(telegram_id, "v2city")
        send(chat_id, "اسم شهرتان را بنویسید تا به لیست انتظار اضافه شوید.")
        return ""
    if action == "ask":
        send(chat_id, "بنویسید دنبال چه خانه‌ای هستید؛ مثلاً:\n«۱ خوابه ریچموند هیل تا ۲۰۰۰»")
        return ""
    if action == "p" and len(parts) == 2 and parts[1].isdigit():
        edit(chat_id, message_id, *results_screen(telegram_id, int(parts[1])), kind="results")
        return ""
    if action in {"d", "m"} and len(parts) == 2:
        listing = _listing(parts[1])
        if listing is None:
            return OLD_BUTTON
        if action == "m":
            HomeAlertSent.objects.filter(telegram_user_id=telegram_id, listing=listing, seen_at__isnull=True).update(seen_at=timezone.now())
        show_listing(chat_id, telegram_id, user, listing)
        return ""
    if action == "c" and len(parts) == 2:
        listing = _listing(parts[1])
        if listing is None:
            return OLD_BUTTON
        text, keyboard, toast = contact_screen(listing, user)
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        bot.call("sendMessage", payload)
        # Never the contact itself in the log.
        botlog.record(telegram_id, "out", "contact", f"contact screen, listing {listing.pk}")
        return toast
    if action == "s" and len(parts) == 2:
        return toggle_saved(parts[1], user, chat_id, message_id)
    if action == "t" and len(parts) == 2:
        return report_closed(parts[1], chat_id, telegram_id)
    if action == "rm" and len(parts) == 2:
        values = get_search(telegram_id)
        if parts[1] == "types":
            values.pop("rooms", None)
        values.pop(parts[1], None)
        set_search(telegram_id, values)
        edit(chat_id, message_id, *results_screen(telegram_id, 0), kind="results")
        return ""
    if action == "fix":
        edit(chat_id, message_id, *fix_menu(telegram_id), kind="other")
        return ""
    if action == "fx" and len(parts) == 2:
        values = get_search(telegram_id)
        channel = search_channel(values, telegram_id)
        if channel is None or parts[1] not in dict(FIX_FIELDS):
            return OLD_BUTTON
        edit(chat_id, message_id, *fix_options(parts[1], values, channel), kind="other")
        return ""
    if action == "fs" and len(parts) >= 3:
        values = get_search(telegram_id)
        channel = search_channel(values, telegram_id)
        changed = fix_set(parts[1], ":".join(parts[2:]), values, channel) if channel is not None else None
        if changed is None:
            return OLD_BUTTON
        set_search(telegram_id, changed)
        if parts[1] in {"types", "areas", "furnished", "bills"}:
            edit(chat_id, message_id, *fix_options(parts[1], changed, channel), kind="other")
        else:
            edit(chat_id, message_id, *fix_menu(telegram_id), kind="other")
        return ""
    if action == "wd" and len(parts) >= 3:
        # A rescue button: widen one thing and show what that finds.
        values = get_search(telegram_id)
        channel = search_channel(values, telegram_id)
        changed = fix_set(parts[1], ":".join(parts[2:]), values, channel) if channel is not None else None
        if changed is None:
            return OLD_BUTTON
        set_search(telegram_id, changed)
        edit(chat_id, message_id, *results_screen(telegram_id, 0), kind="results")
        return ""
    if action == "clear":
        set_search(telegram_id, {"ch": get_search(telegram_id).get("ch")})
        edit(chat_id, message_id, *fix_menu(telegram_id), kind="other")
        return ""
    if action == "show":
        edit(chat_id, message_id, *results_screen(telegram_id, 0), kind="results")
        return ""
    if action in {"ar", "aw", "nq"}:
        return clarify_answer(action, parts[1:], chat_id, message_id, telegram_id)
    if action == "saved":
        send(chat_id, *saved_screen(user), kind="menu")
        return ""
    if action == "sx":
        account = person(user)
        if account is not None:
            HomeSaved.objects.filter(user=account).delete()
            capture("saved_cleared", telegram_id=telegram_id, **HOUSING)
        edit(chat_id, message_id, *saved_screen(user), kind="menu")
        return "پاک شد"
    if action == "help":
        edit(chat_id, message_id, HELP_TEXT, help_keyboard(), kind="menu")
        return ""
    if action == "safety" and len(parts) == 2 and parts[1].isdigit():
        chosen = HomeChannel.objects.filter(pk=int(parts[1]), enabled=True).exclude(city="").first() or botchat.remembered_city(telegram_id)
        edit(chat_id, message_id, *safety_screen(chosen), kind="other")
        return ""
    if action == "forget":
        edit(chat_id, message_id, *forget_screen(), kind="other")
        return ""
    if action == "fy":
        botlog.forget(telegram_id)
        values = get_search(telegram_id)
        set_search(telegram_id, {"ch": values.get("ch")} if values.get("ch") else {})
        edit_keyboard(chat_id, message_id, [])
        return "پاک شد"
    if action == "langs":
        send(chat_id, *lang_screen(telegram_id), kind="menu")
        return ""
    if action == "lang" and len(parts) == 2 and parts[1] in {"fa", "en"}:
        return set_language(parts[1], user, chat_id, message_id, telegram_id)
    if action == "post":
        send(chat_id, *post_screen(telegram_id), kind="menu")
        return ""
    if action == "mine":
        send(chat_id, *mine_screen(telegram_id), kind="menu")
        return ""
    if action in {"fu", "fd"} and len(parts) == 2 and parts[1].isdigit():
        return feedback(action, int(parts[1]), chat_id, message_id, telegram_id)
    if action == "fr" and len(parts) == 3 and parts[1].isdigit():
        return feedback_reason(int(parts[1]), parts[2], chat_id, message_id, telegram_id)
    return OLD_BUTTON


def toggle_saved(raw: str, user: dict, chat_id: int, message_id) -> str:
    listing = _listing(raw)
    account = person(user)
    if listing is None or account is None:
        return OLD_BUTTON
    row, created = HomeSaved.objects.get_or_create(user=account, listing=listing)
    if not created:
        row.delete()
    capture("listing_saved" if created else "listing_unsaved", telegram_id=int(user["id"]), properties={"listing_id": listing.pk}, **HOUSING)
    if message_id:
        edit_keyboard(chat_id, message_id, card_keyboard(listing, saved=created))
    return "🔖 نشان شد" if created else "از نشان‌شده‌ها برداشته شد"


def report_closed(raw: str, chat_id: int, telegram_id: int) -> str:
    listing = _listing(raw)
    if listing is None:
        return OLD_BUTTON
    outcome = freshness.mark_taken(listing, telegram_id)
    listing.refresh_from_db()
    if outcome == "rented":
        send(chat_id, closed_card(listing), closed_keyboard(), kind="other")
        return "ممنون! ثبت شد."
    return "ممنون! ثبت شد." if outcome == "noted" else "این آگهی دیگر فعال نیست."


def clarify_answer(action: str, rest: list[str], chat_id: int, message_id, telegram_id: int) -> str:
    values = get_search(telegram_id)
    channel = search_channel(values, telegram_id)
    if channel is None:
        return OLD_BUTTON
    raw = values.pop("raw_area", "")
    if action == "ar":
        options = common_areas(channel.country, channel.city, limit=3) or [(row.fa, row.canonical, 0) for row in metro_rows(channel)[:3]]
        if not rest or not rest[0].isdigit() or int(rest[0]) >= len(options):
            return OLD_BUTTON
        values["areas"] = [*(values.get("areas") or []), options[int(rest[0])][1]]
    elif action == "nq" and raw:
        values["q"] = raw[:48]
    set_search(telegram_id, values)
    edit(chat_id, message_id, *results_screen(telegram_id, 0), kind="results")
    return ""


def set_language(locale: str, user: dict, chat_id: int, message_id, telegram_id: int) -> str:
    HomeChatState.objects.update_or_create(telegram_user_id=telegram_id, defaults={"locale": locale})
    channel = botchat.remembered_city(telegram_id)
    edit_keyboard(chat_id, message_id, [])
    if locale == "en":
        send(chat_id, "Language: <b>English</b>. Your menu is updated.", reply_keyboard=persistent_keyboard(channel, "en"), kind="menu")
        name = esc(user.get("first_name") or "there")
        send(
            chat_id,
            f"Hi {name}! Describe the home you want, for example “a room in North York under 1200 from December”.",
            [
                [{"text": "✍️ Write what you want", "callback_data": "v2:ask"}],
                [{"text": "🔎 Latest listings", "callback_data": "v2:p:0"}, {"text": "🔔 Alerts", "callback_data": "v2:al"}],
            ],
            kind="menu",
        )
        return ""
    send(chat_id, "زبان: <b>فارسی</b>. منوی شما به‌روز شد.", reply_keyboard=persistent_keyboard(channel, "fa"), kind="menu")
    return ""


def feedback(action: str, listing_id: int, chat_id: int, message_id, telegram_id: int) -> str:
    sent = HomeAlertSent.objects.filter(telegram_user_id=telegram_id, listing_id=listing_id).select_related("alert", "listing").first()
    if sent is None:
        return OLD_BUTTON
    sent.feedback = "up" if action == "fu" else "down"
    sent.feedback_reason = ""
    sent.feedback_at = timezone.now()
    sent.seen_at = sent.seen_at or sent.feedback_at
    sent.save(update_fields=["feedback", "feedback_reason", "feedback_at", "seen_at"])
    if action == "fu":
        if sent.alert is not None:
            botchat.edit_markup(chat_id, message_id, match_keyboard(sent.alert, sent.listing, feedback=False))
        return "ممنون!"
    edit_keyboard(chat_id, message_id, reason_rows(listing_id))
    return "چرا به کارتان نیامد؟ یکی را بزنید."


def feedback_reason(listing_id: int, code: str, chat_id: int, message_id, telegram_id: int) -> str:
    from home.alerts import FEEDBACK_REASONS

    sent = HomeAlertSent.objects.filter(telegram_user_id=telegram_id, listing_id=listing_id).select_related("alert", "listing").first()
    if sent is None or code not in FEEDBACK_REASONS:
        return OLD_BUTTON
    sent.feedback = "down"
    sent.feedback_reason = FEEDBACK_REASONS[code][0]
    sent.feedback_at = timezone.now()
    sent.save(update_fields=["feedback", "feedback_reason", "feedback_at"])
    if sent.alert is not None:
        botchat.edit_markup(chat_id, message_id, match_keyboard(sent.alert, sent.listing, feedback=False))
    send(chat_id, "ممنون، ثبت شد. تطبیق‌ها دقیق‌تر می‌شود.")
    return ""


def alert_callback(action: str, rest: list[str], user: dict, chat_id: int, message_id, telegram_id: int) -> str | None:
    """Alert buttons. None when `action` is not one of them."""
    if action == "anew":
        channel = botchat.remembered_city(telegram_id)
        if channel is None:
            send(chat_id, "<b>کشور شما؟</b>", country_rows(), kind="menu")
            return ""
        begin_alert(user, chat_id, channel)
        return ""
    if action == "al":
        send(chat_id, *alerts_screen(telegram_id), kind="menu")
        return ""
    if action == "asearch":
        values = get_search(telegram_id)
        channel = search_channel(values, telegram_id)
        if channel is None:
            return OLD_BUTTON
        alert = alert_from_search(user, chat_id, values, channel)
        send(chat_id, *(saved_alert_screen(alert) if alert is not None else cap_screen(telegram_id)), kind="other")
        return ""
    if action == "re":
        restored = 0
        for alert in HomeAlert.objects.filter(telegram_user_id=telegram_id, active=False).order_by("-id"):
            if active_count(telegram_id) >= MAX_ALERTS_PER_USER:
                break
            alert.active = True
            alert.save(update_fields=["active"])
            restored += 1
        capture("alerts_restored", telegram_id=telegram_id, properties={"count": restored}, **HOUSING)
        send(chat_id, *alerts_screen(telegram_id, f"🔔 {fa(restored)} گوش به زنگ دوباره روشن شد."), kind="menu")
        return ""
    if action not in {"at", "ac", "ab", "aa", "aw2", "as", "am", "ao", "ag", "ae", "ad", "ap", "ay", "ax", "ax2", "aq", "av", "rf", "rq", "ra", "ao0"}:
        return None
    alert = botchat.own_alert(telegram_id, rest[0]) if rest else None
    if alert is None:
        return OLD_BUTTON
    if action == "at" and len(rest) == 2:
        if rest[1] == "x":
            alert.property_types = []
            alert.min_rooms = None
            alert.save(update_fields=["property_types", "min_rooms"])
            finish_answer(alert, "type")
            botchat.alert_event("alert_edited", alert, field="type", complete=alert.complete)
            edit(chat_id, message_id, *next_alert_screen(alert, telegram_id))
            return ""
        toggle_type(alert, rest[1])
        edit(chat_id, message_id, *type_screen(alert))
        return ""
    if action == "ac":
        if not alert.property_types:
            return "یکی را بزنید، یا «همه»."
        finish_answer(alert, "type")
        botchat.alert_event("alert_edited", alert, field="type", complete=alert.complete)
        edit(chat_id, message_id, *next_alert_screen(alert, telegram_id))
        return ""
    if action == "ab" and len(rest) == 2 and rest[1].isdigit():
        stop_waiting(telegram_id)
        botchat.apply_answer(alert, "b", rest[1])
        finish_answer(alert, "budget")
        edit(chat_id, message_id, *next_alert_screen(alert, telegram_id))
        return ""
    if action == "aa" and len(rest) == 2 and rest[1].isdigit():
        options = botchat.area_options(alert)
        if int(rest[1]) >= len(options):
            return OLD_BUTTON
        if not toggle_area_name(alert, options[int(rest[1])][1]):
            return "بیشتر از این جا نمی‌شود."
        edit(chat_id, message_id, *area_screen(alert))
        return ""
    if action == "ao" and len(rest) == 2 and rest[1].isdigit():
        row = HomeArea.objects.filter(pk=int(rest[1])).first()
        if row is None:
            return OLD_BUTTON
        if not toggle_area_name(alert, row.canonical):
            return "بیشتر از این جا نمی‌شود."
        group = list(dict.fromkeys(item.group or "دیگر" for item in metro_rows(metro_channel(alert.country, alert.city))))
        edit(chat_id, message_id, *more_areas(alert, row.group or ("دیگر" if "دیگر" in group else "")))
        return ""
    if action == "am":
        channel = metro_channel(alert.country, alert.city)
        groups = list(dict.fromkeys(row.group or "دیگر" for row in metro_rows(channel))) if channel is not None else []
        index = int(rest[1]) if len(rest) == 2 and rest[1].isdigit() else 0
        edit(chat_id, message_id, *more_areas(alert, groups[index] if index < len(groups) else ""))
        return ""
    if action in {"aw2", "as"}:
        botchat.apply_answer(alert, "w", "x" if action == "aw2" else "y")
        finish_answer(alert, "area")
        edit(chat_id, message_id, *saved_alert_screen(alert))
        return "ذخیره شد"
    if action == "ag" and len(rest) == 2 and rest[1] in {"t", "b", "w"}:
        screen = {"t": type_screen, "w": area_screen}.get(rest[1])
        edit(chat_id, message_id, *(screen(alert) if screen else budget_screen(alert, telegram_id)))
        return ""
    if action == "ae":
        edit(chat_id, message_id, *alert_edit_menu(alert))
        return ""
    if action == "ad":
        edit(chat_id, message_id, *alert_screen(alert))
        return ""
    if action == "ap":
        alert.paused_until = timezone.now() + timedelta(days=PAUSE_DAYS)
        alert.save(update_fields=["paused_until"])
        botchat.alert_event("alert_toggled", alert, active=alert.active, paused=True)
        local = alert.paused_until.astimezone(city_zone(alert.country, alert.city))
        return f"⏸ تا {persian_date(local)} برای این گوش به زنگ پیامی نمی‌فرستم."
    if action == "ao0":
        alert.active = False
        alert.save(update_fields=["active"])
        botchat.alert_event("alert_toggled", alert, active=False)
        return "دیگر برای این یکی پیامی نمی‌فرستم."
    if action == "ay":
        if not alert.active and active_count(telegram_id) >= MAX_ALERTS_PER_USER and not alert.wanted_listing_id:
            return f"سقف {fa(MAX_ALERTS_PER_USER)} گوش به زنگ پر است؛ اول یکی را خاموش کنید."
        alert.active = True
        alert.paused_until = None
        alert.save(update_fields=["active", "paused_until"])
        botchat.alert_event("alert_toggled", alert, active=True)
        edit(chat_id, message_id, *alert_screen(alert))
        return ""
    if action == "ax":
        text = f"🗑 این گوش به زنگ حذف شود؟\n<i>{esc(alert_summary(alert))}</i>"
        edit(chat_id, message_id, text, [[{"text": "🗑 بله، حذف", "callback_data": f"v2:ax2:{alert.pk}"}, {"text": "‹ نه، برگردیم", "callback_data": f"v2:ad:{alert.pk}"}]])
        return ""
    if action == "ax2":
        botchat.alert_event("alert_deleted", alert, switched_off_only=bool(alert.wanted_listing_id))
        if alert.wanted_listing_id:
            # A saved request comes back on the next run if deleted, so it is switched off instead.
            alert.active = False
            alert.save(update_fields=["active"])
        else:
            alert.delete()
        edit(chat_id, message_id, *alerts_screen(telegram_id))
        return "حذف شد"
    if action == "aq":
        set_search(telegram_id, search_from_alert(alert))
        send(chat_id, *results_screen(telegram_id, 0), kind="results")
        return ""
    if action == "av":
        edit(chat_id, message_id, *saved_alert_screen(alert))
        return "ذخیره شد"
    if action == "rf":
        edit(chat_id, message_id, *refine_screen(alert))
        return ""
    if action == "rq" and len(rest) == 2 and rest[1] in refine_list(alert):
        edit(chat_id, message_id, *refine_screen(alert, rest[1]))
        return ""
    if action == "ra" and len(rest) == 3:
        code, value = rest[1], rest[2]
        if code == "m":
            if value == "t":
                alert.term = "sublet"
                alert.search = {**(alert.search or {}), "term": "lease_transfer"}
            elif value in {"l", "s", "c"}:
                alert.term = {"l": "lease", "s": "sublet", "c": "short"}[value]
                alert.search = {key: item for key, item in (alert.search or {}).items() if key != "term"}
            else:
                return OLD_BUTTON
            alert.answered = sorted({*(alert.answered or []), "term"})
            alert.save(update_fields=["term", "search", "answered"])
            botchat.alert_event("alert_edited", alert, field="term", complete=alert.complete)
        elif code not in {"g", "d"} or not botchat.apply_answer(alert, code, value):
            return OLD_BUTTON
        field = {"g": "gender", "m": "term", "d": "registration"}[code]
        fields = refine_list(alert)
        following = fields[fields.index(field) + 1 :]
        if following:
            edit(chat_id, message_id, *refine_screen(alert, following[0]))
        else:
            edit(chat_id, message_id, *saved_alert_screen(alert))
        return ""
    return OLD_BUTTON
