"""«💬» two-sided intro with consent.

The requester taps «💬 n». The owner of that request gets «… می‌خواهد با شما در ارتباط باشد ·
قبول / نه». Only on accept do both get the other's username. Every message to the other person
is behind the intros switch (off by default). With it off, or when the other person cannot be
reached by the bot (an imported post), the requester sees the contact the owner published
themselves: the link to the original public post.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from courier import conf, search, texts
from courier.models import CourierIntro, IntroStatus
from courier.telegram import send
from item_requests.models import ItemRequest, RequestStatus, RequestType
from matching.contact import SYNTHETIC_TELEGRAM_USER_ID, clean_telegram_username

REQUEST_WORD = {RequestType.SUPPLY: "سفر", RequestType.DEMAND: "بسته‌ی"}


@dataclass
class Reply:
    text: str
    keyboard: list = field(default_factory=list)
    others: list = field(default_factory=list)  # actions for other chats
    toast: str = ""


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


def request_intro(requester, target_request: ItemRequest) -> Reply:
    target = target_request.user
    if target.pk == requester.pk:
        return Reply("این درخواست خودتونه 🙂")
    if target_request.status != RequestStatus.ACTIVE:
        return Reply("این درخواست دیگه فعال نیست.")
    chat = reachable_chat(target)
    if not conf.intros_enabled():
        return _fallback(target_request, "💬 معرفی دوطرفه هنوز روشن نشده.")
    if chat is None:
        return _fallback(target_request, "💬 این آگهی از کانال‌های عمومی آمده و صاحبش هنوز ربات چمدون را باز نکرده.")
    _expire_old()
    existing = CourierIntro.objects.filter(requester=requester, target_request=target_request).first()
    if existing is not None:
        if existing.status == IntroStatus.ACCEPTED:
            return Reply(f"✅ قبلاً وصل شدید: {mention(target)}", contact_buttons(target))
        if existing.status == IntroStatus.PENDING:
            return Reply("⏳ درخواست معرفی‌تان قبلاً رفته؛ منتظر جواب‌شان هستیم.")
        return Reply("این معرفی قبلاً انجام نشد. مسافرها و بسته‌های دیگر را ببینید.")
    since = timezone.now() - timedelta(hours=24)
    if CourierIntro.objects.filter(requester=requester, created_at__gte=since).count() >= conf.intro_daily_limit():
        return Reply(f"امروز {texts.fa(conf.intro_daily_limit())} درخواست معرفی فرستادید. فردا دوباره امتحان کنید 🙏")
    if CourierIntro.objects.filter(target=target, created_at__gte=since).count() >= conf.INTRO_TARGET_DAILY_LIMIT:
        return Reply("امروز درخواست‌های زیادی برای ایشان رفته. فردا دوباره امتحان کنید 🙏")
    mine = own_request_for(requester, target_request)
    try:
        with transaction.atomic():
            intro = CourierIntro.objects.create(
                requester=requester, target=target, target_request=target_request, requester_request=mine
            )
    except IntegrityError:
        return Reply("⏳ درخواست معرفی‌تان قبلاً رفته؛ منتظر جواب‌شان هستیم.")
    others = [send(chat, *intro_request_message(intro))]
    return Reply(
        "📨 درخواست معرفی فرستاده شد. اگر قبول کنند، آیدی‌ها همین‌جا رد و بدل می‌شود.",
        others=others,
        toast="فرستاده شد",
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


def respond(intro_id: int, user, *, accept: bool) -> Reply:
    _expire_old()
    intro = CourierIntro.objects.select_related("requester", "target", "target_request").filter(pk=intro_id).first()
    if intro is None or intro.target_id != user.pk:
        return Reply("این دکمه قدیمیه.")
    if intro.status == IntroStatus.EXPIRED:
        return Reply("این درخواست معرفی منقضی شده.")
    if intro.status != IntroStatus.PENDING:
        if intro.status == IntroStatus.ACCEPTED:
            return Reply(f"✅ قبلاً وصل شدید: {mention(intro.requester)}", contact_buttons(intro.requester))
        return Reply("جواب‌تان قبلاً ثبت شده.")
    intro.status = IntroStatus.ACCEPTED if accept else IntroStatus.DECLINED
    intro.answered_at = timezone.now()
    intro.save(update_fields=["status", "answered_at"])
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
