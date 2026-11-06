"""The daily outreach note in Persian: sent, failed with code, replies, stops. With --step, the rollout rule too.

Rollout (Mohammad, 2026-11-05): 10 a day, then 30 a day once a full day at the cap passed with no flood, no request to stop and
no error worth stopping for. --step raises the daily limit to the next step only on such a day; it never lowers it,
never goes past 30, and changes nothing else.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.utils import timezone

from outreach import conf
from outreach.models import OutreachAccountEvent, OutreachMessage, OutreachOptOut, OutreachSettings, OutreachState, OutreachStatus

TORONTO = ZoneInfo("America/Toronto")
STEPS = (5, 10, 30)
# Codes that are about the recipient, not the account: they do not stop a rollout.
HARMLESS = {"USERNAME_NOT_FOUND", "NOT_A_PERSON", "PRIVACY_PREMIUM_REQUIRED", "ALLOW_PAYMENT_REQUIRED", "USER_IS_BLOCKED", "INPUT_USER_DEACTIVATED"}


class Command(BaseCommand):
    help = __doc__.splitlines()[0]

    def add_arguments(self, parser) -> None:
        parser.add_argument("--step", action="store_true", help="Apply the rollout rule to the daily limit.")
        parser.add_argument("--days-ago", type=int, default=0, help="Report an earlier Toronto day.")

    def handle(self, *args, **options) -> None:
        now = timezone.now()
        day = (now.astimezone(TORONTO) - timedelta(days=options["days_ago"])).replace(hour=0, minute=0, second=0, microsecond=0)
        end = day + timedelta(days=1)
        sent = OutreachMessage.objects.filter(status=OutreachStatus.SENT, sent_at__gte=day, sent_at__lt=end)
        failed = OutreachMessage.objects.filter(status=OutreachStatus.FAILED, claimed_at__gte=day, claimed_at__lt=end)
        videos = OutreachMessage.objects.filter(video_sent_at__gte=day, video_sent_at__lt=end).count()
        replies = OutreachMessage.objects.filter(replied_at__gte=day, replied_at__lt=end).count()
        stops = OutreachOptOut.objects.filter(created_at__gte=day, created_at__lt=end).count()
        events = list(OutreachAccountEvent.objects.filter(created_at__gte=day, created_at__lt=end).select_related("account"))
        codes = Counter(failed.values_list("error_code", flat=True))
        video_errors = Counter(
            OutreachMessage.objects.filter(updated_at__gte=day, updated_at__lt=end).exclude(video_error__in=["", "claimed"]).values_list("video_error", flat=True)
        )
        queued = OutreachMessage.objects.filter(status=OutreachStatus.QUEUED).count()
        by_kind = Counter(sent.values_list("kind", flat=True))
        by_account_city = Counter(f"{account or '-'}/{city or '-'}" for account, city in sent.values_list("account__name", "city"))
        from outreach.models import OutreachTurn, TurnDirection

        agent_turns = OutreachTurn.objects.filter(direction=TurnDirection.IN, created_at__gte=day, created_at__lt=end).exclude(action="")
        actions = Counter(agent_turns.values_list("action", flat=True))
        answered = OutreachTurn.objects.filter(direction=TurnDirection.OUT, created_at__gte=day, created_at__lt=end, telegram_message_id__isnull=False)
        by_person = answered.filter(action="by_person").count()
        filled = sum(1 for detail in agent_turns.filter(action__in=["fill_field", "add_photo"]).values_list("action_detail", flat=True))
        qualifying = self.qualifying(now)
        lines = [
            f"گزارش outreach چمدون، {day:%Y-%m-%d} (روز تورنتو)",
            f"ارسال شد: {sent.count()} نفر ({', '.join(f'{k} {v}' for k, v in sorted(by_kind.items())) or '-'})، ویدیو: {videos}",
            f"شکست: {sum(codes.values())}" + (f" ({', '.join(f'{k} {v}' for k, v in codes.items())})" if codes else ""),
            f"خطای ویدیو: {', '.join(f'{k} {v}' for k, v in video_errors.items())}" if video_errors else "خطای ویدیو: 0",
            f"به تفکیک اکانت/شهر: {', '.join(f'{k} {v}' for k, v in sorted(by_account_city.items())) or '-'}",
            f"جواب: {replies}، پاسخ ما: {answered.count() - by_person} از agent و {by_person} دستی ({', '.join(f'{k} {v}' for k, v in actions.items()) or '-'})",
            f"آگهی تکمیل شد (فیلد یا عکس): {filled}، درخواست توقف یا حذف: {stops}",
            f"واجد شرایط الان (آگهی ۳ روز اخیر، ۶ شهر): {qualifying}",
            f"محدودیت تلگرام: {', '.join(f'{e.account.name} {e.code}' for e in events) or 'هیچ'}",
            f"سقف روزانه: {conf.daily_limit()}، در صف: {queued}، ارسال {'روشن' if conf.sending_enabled() else 'خاموش'}",
        ]
        if options["step"]:
            lines.append(self.step(sent.count(), stops, events, codes))
        self.stdout.write("\n".join(lines))

    @staticmethod
    def qualifying(now) -> int:
        """People who could get a first message now: fresh ads, launch cities, not members, never contacted."""
        from outreach.housing import people, person_skip_reason

        return sum(1 for person in people(now) if not person_skip_reason(person))

    def step(self, sent: int, stops: int, events: list, codes: Counter) -> str:
        current = conf.daily_limit()
        serious = [code for code in codes if code not in HARMLESS]
        if OutreachState.load().stopped or not conf.sending_enabled():
            return "پله: بدون تغییر (ارسال خاموش یا متوقف است)."
        if events or stops or serious:
            return "پله: بدون تغییر (محدودیت، درخواست توقف یا خطای جدی)."
        if sent < current:
            # A step is a full day at the current level: a day that did not reach its cap proves nothing yet.
            return f"پله: بدون تغییر (امروز {sent} از سقف {current} رفت)."
        higher = [value for value in STEPS if value > current]
        if not higher:
            return f"پله: سقف همین {current} می‌ماند (بالاترین پله)."
        OutreachSettings.objects.filter(pk=1).update(daily_limit=higher[0], updated_at=timezone.now())
        return f"پله: سقف روزانه از {current} به {higher[0]} رفت."
