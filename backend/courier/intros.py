"""«💬» two-sided intro with consent.

The requester taps «💬 n». The owner of that request gets «… می‌خواهد با شما در ارتباط باشد ·
قبول / نه». Only on accept do both get the other's username. Every message to the other person
is behind the intros switch (off by default). With it off, or when the other person cannot be
reached by the bot (an imported post), the requester sees the contact the owner published
themselves: the link to the original public post.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.html import strip_tags

from courier import conf, search, texts
from courier.models import CourierIntro, IntroStatus
from courier.telegram import send
from item_requests.models import ItemRequest, RequestStatus, RequestType
from matching.contact import SYNTHETIC_TELEGRAM_USER_ID, clean_telegram_username
from matching.models import Match, MatchStatus
from matching.rules import pair_demand_supply
from matching.scoring import calculate_score
from notifications import telegram as courier_bot

logger = logging.getLogger(__name__)

REQUEST_WORD = {RequestType.SUPPLY: "سفر", RequestType.DEMAND: "بسته‌ی"}
# First try plus two retries, inside the 15 s the client waits.
INTRO_SEND_TRIES = 3
INTRO_SEND_TIMEOUT = 4


@dataclass
class Reply:
    text: str
    keyboard: list = field(default_factory=list)
    others: list = field(default_factory=list)  # actions for other chats
    toast: str = ""
    code: str = ""


@dataclass
class IntroStart:
    code: str
    intro: CourierIntro | None = None
    others: list = field(default_factory=list)  # actions for other chats


def reachable_chat(user) -> int | None:
    value = int(user.telegram_user_id or 0)
    return value if 0 < value < SYNTHETIC_TELEGRAM_USER_ID else None


def public_contact(item: ItemRequest) -> tuple[list[str], list]:
    """What the owner published themselves: the original post link and, for imported posts, the handle."""
    lines: list[str] = []
    buttons: list = []
    if item.imported:
        username = clean_telegram_username(item.user.telegram_username)
        if username:
            lines.append(f"👤 @{texts.esc(username)}")
    if item.source_url:
        buttons.append([{"text": "🔗 آگهی اصلی در کانال", "url": item.source_url}])
    return lines, buttons


def mention(user) -> str:
    username = clean_telegram_username(user.telegram_username)
    if username:
        return f"@{texts.esc(username)}"
    chat = reachable_chat(user)
    name = texts.esc(user.first_name or "ایشان")
    return f'<a href="tg://user?id={chat}">{name}</a>' if chat else name


def contact_buttons(user) -> list:
    username = clean_telegram_username(user.telegram_username)
    return [[{"text": f"💬 پیام به {user.first_name or username}", "url": f"https://t.me/{username}"}]] if username else []


def own_request_for(user, target: ItemRequest) -> ItemRequest | None:
    """The requester's own active request on the same route, if they made one."""
    rows = ItemRequest.objects.filter(
        user=user,
        status=RequestStatus.ACTIVE,
        type=search.opposite(target.type),
        origin_country=target.origin_country,
        origin_city=target.origin_city,
    ).order_by("-created_at")
    stops = set(target.destination_stop_pairs())
    for row in rows[:10]:
        if stops & set(row.destination_stop_pairs()):
            return row
    return None


def _expire_old() -> None:
    cutoff = timezone.now() - timedelta(hours=conf.INTRO_EXPIRE_HOURS)
    CourierIntro.objects.filter(status=IntroStatus.PENDING, created_at__lt=cutoff).update(status=IntroStatus.EXPIRED)


def _fallback(item: ItemRequest, note: str) -> Reply:
    lines, buttons = public_contact(item)
    text = [note]
    if lines or buttons:
        text.extend(["", "راه تماسی که خودشان منتشر کرده‌اند:", *lines])
    else:
        text.extend(["", "راه تماس عمومی ندارند. «🔔 خبرم کن» را بزنید تا وقتی معرفی روشن شد اول به شما بگوییم."])
    return Reply("\n".join(text), buttons)


def start_intro(requester, target_request: ItemRequest) -> IntroStart:
    """Every outcome of asking for an intro, for the bot and the Mini App. Writes the intro, sends nothing."""
    target = target_request.user
    if target.pk == requester.pk:
        return IntroStart("own_request")
    if target_request.status != RequestStatus.ACTIVE:
        return IntroStart("request_inactive")
    chat = reachable_chat(target)
    if not conf.intros_enabled():
        return IntroStart("intros_off")
    if chat is None:
        return IntroStart("intro_unreachable")
    _expire_old()
    existing = CourierIntro.objects.filter(requester=requester, target_request=target_request).first()
    if existing is not None:
        if existing.status == IntroStatus.ACCEPTED:
            return IntroStart("intro_accepted", existing)
        if existing.status == IntroStatus.PENDING:
            return IntroStart("intro_pending", existing)
        if existing.status != IntroStatus.UNDELIVERED:
            return IntroStart("intro_closed", existing)
    since = timezone.now() - timedelta(hours=24)
    counted = CourierIntro.objects.filter(created_at__gte=since).exclude(status=IntroStatus.UNDELIVERED)
    if counted.filter(requester=requester).count() >= conf.intro_daily_limit():
        return IntroStart("intro_cap_self")
    if counted.filter(target=target).count() >= conf.INTRO_TARGET_DAILY_LIMIT:
        return IntroStart("intro_cap_target")
    mine = own_request_for(requester, target_request)
    if existing is not None:
        # The target may have reopened the bot since. This counts as a new ask.
        existing.status = IntroStatus.PENDING
        existing.created_at = timezone.now()
        existing.requester_request = mine
        existing.save(update_fields=["status", "created_at", "requester_request"])
        return IntroStart("intro_sent", existing, [send(chat, *intro_request_message(existing))])
    try:
        with transaction.atomic():
            intro = CourierIntro.objects.create(
                requester=requester, target=target, target_request=target_request, requester_request=mine
            )
    except IntegrityError:
        return IntroStart("intro_pending", CourierIntro.objects.filter(requester=requester, target_request=target_request).first())
    return IntroStart("intro_sent", intro, [send(chat, *intro_request_message(intro))])


def deliver_intro(started: IntroStart) -> bool:
    """Send the ask now. A permanent failure, or three failed tries, ends the intro and refunds the daily count."""
    action = started.others[0]
    for _try in range(INTRO_SEND_TRIES):
        body, error = courier_bot.call_telegram_api_detailed(action["method"], action["payload"], timeout=INTRO_SEND_TIMEOUT)
        if body is not None:
            return True
        status = error.get("error_code")
        if isinstance(status, int) and status < 500 and status != 429:
            break
    started.intro.status = IntroStatus.UNDELIVERED
    started.intro.save(update_fields=["status"])
    return False


def request_intro(requester, target_request: ItemRequest) -> Reply:
    started = start_intro(requester, target_request)
    if started.code == "intro_sent" and conf.intro_everywhere_on():
        if not deliver_intro(started):
            started.code = "intro_undelivered"
        started.others = []
    reply = _intro_reply(started, target_request)
    reply.code = started.code
    return reply


def reply_for_page(reply: Reply) -> dict:
    """A reply outside Telegram: plain text, and only the https links of its keyboard."""
    links = [
        {"text": button["text"], "url": button["url"]}
        for row in reply.keyboard
        for button in row
        if button.get("url", "").startswith("https://")
    ]
    return {"code": reply.code, "detail": html.unescape(strip_tags(reply.text)), "links": links}


def _intro_reply(started: IntroStart, target_request: ItemRequest) -> Reply:
    code = started.code
    target = target_request.user
    if code == "own_request":
        return Reply("این درخواست خودتونه 🙂")
    if code == "request_inactive":
        return Reply("این درخواست دیگه فعال نیست.")
    if code == "intros_off":
        return _fallback(target_request, "💬 معرفی دوطرفه هنوز روشن نشده.")
    if code == "intro_unreachable":
        return _fallback(target_request, "💬 این آگهی از کانال‌های عمومی آمده و صاحبش هنوز ربات چمدون را باز نکرده.")
    if code == "intro_accepted":
        return Reply(f"✅ قبلاً وصل شدید: {mention(target)}", contact_buttons(target))
    if code == "intro_pending":
        return Reply("⏳ درخواست معرفی‌تان قبلاً رفته؛ منتظر جواب‌شان هستیم.")
    if code == "intro_closed":
        return Reply("این معرفی قبلاً انجام نشد. مسافرها و بسته‌های دیگر را ببینید.")
    if code == "intro_cap_self":
        return Reply(f"امروز {texts.fa(conf.intro_daily_limit())} درخواست معرفی فرستادید. فردا دوباره امتحان کنید 🙏")
    if code == "intro_cap_target":
        return Reply("امروز درخواست‌های زیادی برای ایشان رفته. فردا دوباره امتحان کنید 🙏")
    if code == "intro_undelivered":
        return Reply("پیام به ایشان نرسید. مسافرها و بسته‌های دیگر همین مسیر را ببینید.")
    return Reply(
        "📨 درخواست معرفی فرستاده شد. اگر قبول کنند، آیدی‌ها همین‌جا رد و بدل می‌شود.",
        others=started.others,
        toast="فرستاده شد",
    )


def expire_old() -> None:
    _expire_old()


def live_intro_status(intro: CourierIntro) -> str:
    """The status as it reads now, without writing: a pending intro past 72 h is expired."""
    cutoff = timezone.now() - timedelta(hours=conf.INTRO_EXPIRE_HOURS)
    if intro.status == IntroStatus.PENDING and intro.created_at < cutoff:
        return IntroStatus.EXPIRED
    return intro.status


def pair_intro(match) -> CourierIntro | None:
    """The intro between these two people about either request of this match. An accepted one comes first."""
    rows = CourierIntro.objects.filter(
        target_request_id__in=(match.demand_request_id, match.supply_request_id),
        requester_id__in=(match.demand_request.user_id, match.supply_request.user_id),
    )
    return rows.filter(status=IntroStatus.ACCEPTED).first() or rows.first()


def intro_accepted(match) -> bool:
    found = pair_intro(match)
    return found is not None and found.status == IntroStatus.ACCEPTED


def contact_released(match) -> bool:
    """For /api/v1/courier/, whatever the switches say: both sides agreed in an intro, or the old rule
    already told both before that API first opened. Never opened means no such match. The bot and templates
    read legacy_contact_open instead.
    """
    if intro_accepted(match):
        return True
    opened = conf.courier_api_opened_at()
    return opened is not None and match.created_at < opened


def legacy_contact_open(match) -> bool:
    """For the bot, templates and old API. Switch off: today's rule, a visible match shows contact.
    Switch on: a match from before it first went on keeps its contact, a later one needs an accepted intro.
    """
    saved = conf.row()
    if not saved.courier_intro_everywhere:
        return True
    since = saved.intro_everywhere_since
    if since is not None and match.created_at < since:
        return True
    return intro_accepted(match)


def intros_for(user):
    """Reads only. A write that depends on expiry calls expire_old first. An undelivered ask stays off the target's list;
    if a timed-out try landed after all, its bot buttons still answer it.
    """
    return (
        CourierIntro.objects.filter(models.Q(requester=user) | models.Q(target=user))
        .exclude(models.Q(target=user) & models.Q(status=IntroStatus.UNDELIVERED))
        .select_related("requester", "target", "target_request", "requester_request")
        .prefetch_related(
            "target_request__item_categories",
            "target_request__excluded_categories",
            "requester_request__item_categories",
            "requester_request__excluded_categories",
        )
    )


def intro_request_message(intro: CourierIntro) -> tuple[str, list]:
    requester = intro.requester
    target_request = intro.target_request
    word = REQUEST_WORD.get(target_request.type, "")
    lines = [
        f"👋 <b>{texts.esc(requester.first_name or 'یک نفر')}</b> می‌خواهد با شما در ارتباط باشد",
        f"درباره‌ی {word} شما: <b>{texts.esc(texts.request_route(target_request))}</b>",
    ]
    mine = intro.requester_request
    if mine is not None:
        label = "📦 بسته‌ی ایشان:" if mine.type == RequestType.DEMAND else "🧳 سفر ایشان:"
        lines.extend(["", label, *search.card_lines(mine)])
    trust = search.trust_line(requester)
    if trust:
        lines.append(trust)
    lines.extend(["", "آیدی‌ها فقط اگر قبول کنید رد و بدل می‌شود."])
    keyboard = [[{"text": "✅ قبول", "callback_data": f"cr:ia:{intro.pk}"}, {"text": "نه، ممنون", "callback_data": f"cr:in:{intro.pk}"}]]
    return "\n".join(lines), keyboard


def _side_for_match(intro: CourierIntro) -> ItemRequest:
    """The requester's side of the match. With none, a hidden request: nobody posted it, so nobody sees it."""
    from item_requests.models import UNMATCHABLE_INTRO_SIDE
    from item_requests.services import create_item_request, opposite_payload_from_listing

    mine = intro.requester_request
    if mine is not None and mine.status == RequestStatus.ACTIVE and not mine.is_expired():
        return mine
    return own_request_for(intro.requester, intro.target_request) or create_item_request(
        intro.requester,
        opposite_payload_from_listing(intro.target_request),
        sync_channel=False,
        sync_matches=False,
        hidden_reason=UNMATCHABLE_INTRO_SIDE,
    )


def _connect_after_accept(intro: CourierIntro) -> None:
    """Finish, rating and the trust badge read Match. The intro's own messages tell both sides, so this sends nothing.
    Without a match, as when the pair's match already ended, the accepted intro still carries the contact.
    """
    try:
        with transaction.atomic():
            demand, supply = pair_demand_supply(_side_for_match(intro), intro.target_request)
            Match.objects.get_or_create(
                demand_request=demand,
                supply_request=supply,
                defaults={"initiated_by": intro.requester, "score": calculate_score(demand, supply), "status": MatchStatus.CONNECTED},
            )
    except (ValidationError, ValueError, IntegrityError):
        logger.exception("No match for accepted intro %s", intro.pk)


def _target_gone(item: ItemRequest) -> bool:
    day = search.request_date(item)
    return item.status != RequestStatus.ACTIVE or item.is_expired() or (day is not None and day < timezone.localdate())


def respond(intro_id: int, user, *, accept: bool) -> Reply:
    _expire_old()
    intro = CourierIntro.objects.select_related("requester", "target", "target_request").filter(pk=intro_id).first()
    if intro is None or intro.target_id != user.pk:
        return Reply("این دکمه قدیمیه.")
    if intro.status == IntroStatus.EXPIRED:
        return Reply("این درخواست معرفی منقضی شده.")
    # A try that timed out can still have landed, so an undelivered ask answers like a pending one, for as long.
    if intro.status == IntroStatus.UNDELIVERED and intro.created_at < timezone.now() - timedelta(hours=conf.INTRO_EXPIRE_HOURS):
        return Reply("این درخواست معرفی منقضی شده.")
    if intro.status not in (IntroStatus.PENDING, IntroStatus.UNDELIVERED):
        if intro.status == IntroStatus.ACCEPTED:
            return Reply(f"✅ قبلاً وصل شدید: {mention(intro.requester)}", contact_buttons(intro.requester))
        return Reply("جواب‌تان قبلاً ثبت شده.")
    everywhere = conf.intro_everywhere_on()
    if everywhere and _target_gone(intro.target_request):
        intro.status = IntroStatus.EXPIRED
        intro.save(update_fields=["status"])
        return Reply("این درخواست دیگه فعال نیست.")
    intro.status = IntroStatus.ACCEPTED if accept else IntroStatus.DECLINED
    intro.answered_at = timezone.now()
    intro.save(update_fields=["status", "answered_at"])
    if accept and everywhere:
        _connect_after_accept(intro)
    requester_chat = reachable_chat(intro.requester)
    route = texts.esc(texts.request_route(intro.target_request))
    if not accept:
        others = []
        if requester_chat and conf.intros_enabled():
            others.append(send(requester_chat, f"🙏 معرفی برای {route} این بار انجام نشد. مسافرها و بسته‌های دیگر را ببینید."))
        return Reply("باشه، ثبت شد. آیدی شما به ایشان داده نمی‌شود.", others=others)
    others = []
    if requester_chat and conf.intros_enabled():
        others.append(
            send(
                requester_chat,
                f"✅ <b>{texts.esc(intro.target.first_name)}</b> قبول کرد ({route}).\nحالا مستقیم پیام بدهید: {mention(intro.target)}",
                contact_buttons(intro.target),
            )
        )
    return Reply(
        f"✅ وصل شدید. با {mention(intro.requester)} هماهنگ کنید.\n{texts.PROHIBITED_HINT}",
        contact_buttons(intro.requester),
        others=others,
    )
