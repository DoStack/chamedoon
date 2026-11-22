"""Per-admin numbers as Persian text: the admin's own day, and the owners' table of every admin."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from adminyar import attribution
from adminyar.models import AdminRole, AdminUser
from adminyar.posts import G_MONTHS, J_MONTHS, fa
from adminyar.tasks import LOCAL

PERIODS = {"d": ("امروز", 0), "w": ("۷ روز", 6), "m": ("۳۰ روز", 29)}


def window(period: str, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Toronto midnight `days` ago until now, and the label."""
    now = now or timezone.now()
    label, days = PERIODS.get(period, PERIODS["d"])
    local = timezone.localtime(now, LOCAL)
    start = (local - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now, label


def date_label(when: datetime) -> str:
    import jdatetime

    local = timezone.localtime(when, LOCAL).date()
    j = jdatetime.date.fromgregorian(date=local)
    return f"{fa(local.day)} {G_MONTHS[local.month - 1]} {fa(local.year)} ({fa(j.day)} {J_MONTHS[j.month - 1]})"


def admin_card(admin: AdminUser, period: str = "d", now: datetime | None = None) -> str:
    since, until, label = window(period, now)
    s = attribution.stats(admin, since, until)
    lines = [f"📊 {label} شما · {date_label(until)}", ""]
    if admin.team_account:
        lines.append(f"📨 پیام: {fa(s.messaged)} · 💬 جواب: {fa(s.replied)}")
    lines += [
        f"🤖 استارت ربات: {fa(s.starts)} (جدید: {fa(s.new_users)})",
        f"📢 عضو کانال: {fa(s.joins)}",
        f"🏠 آگهی: {fa(s.listings)}",
        f"📚 فوروارد پست: {fa(s.forwards)}",
    ]
    if s.agent_sent:
        lines.append(f"🤖 ایجنت روی این اکانت: {fa(s.agent_sent)} پیام")
    lines += ["", f"🔗 لینک ربات شما:\n{attribution.bot_link(admin).replace('https://', '')}"]
    return "\n".join(lines)


def owners_card(period: str = "d", now: datetime | None = None) -> str:
    since, until, label = window(period, now)
    admins = AdminUser.objects.filter(active=True).exclude(role=AdminRole.OWNER).order_by("name")
    rows, total = [], attribution.Stats()
    for admin in admins:
        s = attribution.stats(admin, since, until)
        for key in ("messaged", "replied", "starts", "new_users", "joins", "listings", "forwards", "agent_sent"):
            setattr(total, key, getattr(total, key) + getattr(s, key))
        agent = f" · ایجنت {fa(s.agent_sent)}" if s.agent_sent else ""
        rows.append(
            f"👤 {admin.name or admin.telegram_user_id}\n"
            f"پیام {fa(s.messaged)} · جواب {fa(s.replied)} · ربات {fa(s.starts)} · کانال {fa(s.joins)} · آگهی {fa(s.listings)} · فوروارد {fa(s.forwards)}{agent}"
        )
    head = f"📈 ادمین‌ها · {label} · {date_label(until)}"
    foot = (
        f"جمع: پیام {fa(total.messaged)} · جواب {fa(total.replied)} · ربات {fa(total.starts)} (جدید {fa(total.new_users)}) · "
        f"کانال {fa(total.joins)} · آگهی {fa(total.listings)} · فوروارد {fa(total.forwards)}"
    )
    return "\n\n".join([head, *rows, foot])
