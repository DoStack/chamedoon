"""Which admin brought whom, and what came of it.

Every admin has a code (AdminUser.code, e.g. "mahtab"). It rides on every link the adminyar bot hands that admin:

    home bot      t.me/chamedoon_home_bot?start=a_<code>             -> AdminTouch via=bot (housing bot /start hook)
    Mini App      ...?startapp=home_<cc>_<city>_l<id>_src-a-<code>   -> AdminTouch via=app (sign-in src hook)
    channel       the admin's own invite link, named a:<code>         -> AdminTouch via=join (chat_member update)

People the admin messaged are already theirs: OutreachMessage rows with sent_by=human on the admin's team account.
Results are matched by Telegram id: a recipient or touched person who later starts the bot, opens the app, joins a
channel or posts a listing counts for that admin. A person reached by two admins counts for both; `first` on
AdminTouch marks who reached them first.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from adminyar.models import AdminInvite, AdminTouch, AdminUser, AuditLog, TouchVia

logger = logging.getLogger("adminyar")

BOT_PREFIX = "a_"
SRC_PREFIX = "a-"
INVITE_PREFIX = "a:"
_CODE = re.compile(r"^[a-z][a-z0-9]{1,15}$")


# --- codes and links ---------------------------------------------------------
def code_for(admin: AdminUser) -> str:
    """The admin's link code, set once on first use: the team account, else the name in ASCII, else u<last digits>."""
    if admin.code:
        return admin.code
    base = admin.team_account or slugify(admin.name or "").replace("-", "")
    base = re.sub(r"[^a-z0-9]", "", base.lower())[:12]
    if not _CODE.match(base):
        base = f"u{str(admin.telegram_user_id)[-6:]}"
    code, n = base, 1
    while AdminUser.objects.filter(code=code).exclude(pk=admin.pk).exists():
        n += 1
        code = f"{base[:12]}{n}"
    admin.code = code
    admin.save(update_fields=["code"])
    return code


def bot_link(admin: AdminUser) -> str:
    from home import conf as home_conf

    return home_conf.bot_start_link(f"{BOT_PREFIX}{code_for(admin)}")


def listing_link(admin: AdminUser, listing) -> str:
    """The Mini App link of one listing, tagged with this admin (falls back to the plain link when it would not fit)."""
    from home import conf as home_conf
    from home import deeplink

    if not listing.country:
        return home_conf.mini_app_link(listing.pk)
    start = deeplink.build(listing.country, listing.city or "", listing=listing.pk, source=f"{SRC_PREFIX}{code_for(admin)}")
    return home_conf.mini_app_link(start=start)


def invite_link(admin: AdminUser, channel, api=None) -> str:
    """This admin's own invite link to the channel, made once by the housing bot. Public link when the bot cannot."""
    found = AdminInvite.objects.filter(admin_user=admin, channel=channel).first()
    if found:
        return found.link
    fallback = f"https://t.me/{channel.username}" if channel.username else ""
    target = channel.chat_id or (f"@{channel.username}" if channel.username else None)
    if target is None:
        return fallback
    if api is None:
        from home.bot import call

        api = call
    name = f"{INVITE_PREFIX}{code_for(admin)}"[:32]
    try:
        result = api("createChatInviteLink", {"chat_id": target, "name": name})
    except Exception as exc:  # noqa: BLE001 - no rights in that channel: the public link still works, just uncounted
        logger.warning("No invite link for %s in %s: %s", name, channel.username, getattr(exc, "description", exc))
        return fallback
    link = (result or {}).get("invite_link") or ""
    if not link:
        return fallback
    try:
        AdminInvite.objects.create(admin_user=admin, channel=channel, link=link, name=name)
    except IntegrityError:
        return AdminInvite.objects.filter(admin_user=admin, channel=channel).values_list("link", flat=True).first() or link
    return link


def admin_by_code(code: str) -> AdminUser | None:
    code = (code or "").strip().lower()
    return AdminUser.objects.filter(code=code).first() if _CODE.match(code) else None


# --- capture -------------------------------------------------------------------
def _known(telegram_user_id: int) -> bool:
    from home.models import HomeChatState
    from users.models import User

    if HomeChatState.objects.filter(telegram_user_id=telegram_user_id).exists():
        return True
    created = User.objects.filter(telegram_user_id=telegram_user_id).values_list("created_at", flat=True).first()
    return created is not None and created < timezone.now() - timedelta(minutes=10)


def _touch(telegram_user_id: int, admin: AdminUser, via: str, *, start_param: str = "", channel=None, known: bool | None = None) -> bool:
    if not telegram_user_id or telegram_user_id == admin.telegram_user_id:
        return False
    if AdminTouch.objects.filter(telegram_user_id=telegram_user_id, admin_user=admin, via=via, channel=channel).exists():
        return False
    first = not AdminTouch.objects.filter(telegram_user_id=telegram_user_id).exists()
    new_user = (not _known(telegram_user_id)) if known is None else not known
    try:
        with transaction.atomic():
            AdminTouch.objects.create(
                telegram_user_id=telegram_user_id, admin_user=admin, via=via, start_param=start_param[:64], channel=channel, first=first, new_user=new_user
            )
    except IntegrityError:
        return False
    return True


def note_start(telegram_user_id: int, payload: str) -> bool:
    """Housing bot /start <payload>, before the bot answers. Only a_<code> payloads count."""
    payload = (payload or "").strip()
    if not payload.lower().startswith(BOT_PREFIX):
        return False
    admin = admin_by_code(payload[len(BOT_PREFIX) :].split("_")[0])
    return bool(admin) and _touch(int(telegram_user_id), admin, TouchVia.BOT, start_param=payload)


def note_app(telegram_user_id: int | None, src: str, *, known: bool | None = None) -> bool:
    """Mini App sign-in with src a-<code> (the link's src- tag)."""
    src = (src or "").strip().lower()
    if not telegram_user_id or not src.startswith(SRC_PREFIX):
        return False
    admin = admin_by_code(src[len(SRC_PREFIX) :])
    return bool(admin) and _touch(int(telegram_user_id), admin, TouchVia.APP, start_param=f"src-{src}", known=known)


JOINED = {"member", "restricted"}
OUTSIDE = {"left", "kicked"}


def note_join(chat_member: dict) -> bool:
    """A chat_member update of a channel: someone joined through an a:<code> invite link."""
    link = chat_member.get("invite_link") or {}
    name = str(link.get("name") or "")
    old = (chat_member.get("old_chat_member") or {}).get("status")
    new = chat_member.get("new_chat_member") or {}
    if not name.startswith(INVITE_PREFIX) or new.get("status") not in JOINED or old not in OUTSIDE:
        return False
    user = new.get("user") or {}
    if user.get("is_bot") or not user.get("id"):
        return False
    from home.models import HomeChannel

    chat = chat_member.get("chat") or {}
    invite = AdminInvite.objects.filter(link=link.get("invite_link") or "-").select_related("admin_user", "channel").first()
    admin = invite.admin_user if invite else admin_by_code(name[len(INVITE_PREFIX) :])
    if admin is None:
        return False
    channel = invite.channel if invite else HomeChannel.objects.filter(chat_id=chat.get("id")).first()
    return _touch(int(user["id"]), admin, TouchVia.JOIN, start_param=name, channel=channel)


# --- metrics -------------------------------------------------------------------
@dataclass
class Stats:
    messaged: int = 0
    replied: int = 0
    starts: int = 0
    new_users: int = 0
    joins: int = 0
    listings: int = 0
    forwards: int = 0
    agent_sent: int = 0
    people: set[int] = field(default_factory=set)

    @property
    def result(self) -> int:
        """People who did something after the admin reached them: started the bot or app, or joined a channel."""
        return len(self.people)


def stats(admin: AdminUser, since: datetime, until: datetime | None = None) -> Stats:
    """One admin's numbers for [since, until). Messages count by send time; results count by when they happened."""
    from home.models import HomePost
    from outreach.models import OutreachMessage, OutreachStatus, SentBy

    until = until or timezone.now()
    out = Stats()
    first_contact: dict[int, datetime] = {}

    if admin.team_account:
        mine = OutreachMessage.objects.filter(account__name=admin.team_account, sent_by=SentBy.HUMAN, status=OutreachStatus.SENT)
        window = mine.filter(sent_at__gte=since, sent_at__lt=until)
        out.messaged = window.count()
        out.replied = window.filter(replied_at__isnull=False).count()
        out.agent_sent = OutreachMessage.objects.filter(
            account__name=admin.team_account, sent_by=SentBy.AGENT, status=OutreachStatus.SENT, sent_at__gte=since, sent_at__lt=until
        ).count()
        for tid, sent_at, started, member in mine.exclude(recipient_telegram_id__isnull=True).values_list("recipient_telegram_id", "sent_at", "bot_started_at", "member_at"):
            first_contact[tid] = min(first_contact.get(tid, sent_at), sent_at)
            came = started or member
            if came and since <= came < until:
                out.people.add(tid)

    touches = AdminTouch.objects.filter(admin_user=admin)
    for tid, via, created, new_user in touches.values_list("telegram_user_id", "via", "created_at", "new_user"):
        first_contact[tid] = min(first_contact.get(tid, created), created)
        if since <= created < until:
            out.people.add(tid)
            if via == TouchVia.JOIN:
                out.joins += 1
            if new_user and via != TouchVia.JOIN:
                out.new_users += 1
    window_touches = touches.filter(created_at__gte=since, created_at__lt=until).exclude(via=TouchVia.JOIN)
    started_ids = set(window_touches.values_list("telegram_user_id", flat=True))
    if admin.team_account:
        started_ids |= set(
            OutreachMessage.objects.filter(
                account__name=admin.team_account, sent_by=SentBy.HUMAN, status=OutreachStatus.SENT, bot_started_at__gte=since, bot_started_at__lt=until
            ).exclude(recipient_telegram_id__isnull=True).values_list("recipient_telegram_id", flat=True)
        )
    out.starts = len(started_ids)

    if first_contact:
        posts = HomePost.objects.filter(author_id__in=list(first_contact), posted_at__gte=since, posted_at__lt=until, listing__isnull=False)
        out.listings = sum(1 for author, posted in posts.values_list("author_id", "posted_at") if posted >= first_contact[author])

    out.forwards = AuditLog.objects.filter(admin_user=admin, action="ready_forward", result="forwarded", created_at__gte=since, created_at__lt=until).count()
    return out
