from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from item_requests.models import ItemRequest, RequestStatus, RequestType
from matching.contact import (
    SYNTHETIC_TELEGRAM_USER_ID,
    clean_telegram_username,
    is_person_username,
    is_shadow_user,
)
from matching.models import Match, MatchStatus
from outreach.messages import outreach_text, posted_at_for
from outreach.models import OutreachMessage, OutreachOptOut, OutreachState, OutreachStatus
from outreach.persian import TEHRAN_TZ
from users.models import User

PEER_FLOOD = "PEER_FLOOD"
PEER_FLOOD_PAUSE = timedelta(hours=48)
LEASE = timedelta(minutes=15)
CLAIM_BATCH = 25
ATTEMPTED_STATUSES = (OutreachStatus.SENDING, OutreachStatus.SENT, OutreachStatus.FAILED)


class OutreachConflict(Exception):
    pass


def building_enabled() -> bool:
    return bool(getattr(settings, "OUTREACH_ENABLED", False))


def sending_enabled() -> bool:
    return bool(getattr(settings, "OUTREACH_SENDING_ENABLED", False))


def daily_limit() -> int:
    return int(getattr(settings, "OUTREACH_DAILY_LIMIT", 10))


def _min_score() -> Decimal:
    return Decimal(str(getattr(settings, "OUTREACH_MIN_SCORE", 80)))


def _max_post_age() -> timedelta:
    return timedelta(days=int(getattr(settings, "OUTREACH_MAX_POST_AGE_DAYS", 5)))


def _cooldown() -> timedelta:
    return timedelta(days=int(getattr(settings, "OUTREACH_COOLDOWN_DAYS", 14)))


def _send_window() -> tuple[int, int]:
    return (
        int(getattr(settings, "OUTREACH_SEND_START_HOUR", 10)),
        int(getattr(settings, "OUTREACH_SEND_END_HOUR", 21)),
    )


# ---------------------------------------------------------------- building


@dataclass
class BuildResult:
    created: list[OutreachMessage] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def as_dict(self) -> dict:
        return {"created": len(self.created), "skipped": self.skipped}


def build_outreach_queue(*, now: datetime | None = None, dry_run: bool = False) -> BuildResult:
    """Queue one Persian DM per imported demand that has fresh strong matches."""
    now = now or timezone.now()
    result = BuildResult()
    seen: set[str] = set()
    for demand in eligible_demands(now):
        username = clean_telegram_username(demand.user.telegram_username)
        reason = _recipient_skip_reason(username, now, seen)
        if reason:
            result.skip(reason)
            continue
        matches = valid_matches(demand, now)
        if not matches:
            result.skip("no_match")
            continue
        seen.add(username.lower())
        token = secrets.token_urlsafe(12)
        message = OutreachMessage(
            recipient=demand.user,
            recipient_username=username,
            demand_request=demand,
            text=outreach_text(demand, matches, token, now=now),
            token=token,
        )
        if not dry_run:
            with transaction.atomic():
                message.save()
                message.matches.set(matches)
        result.created.append(message)
    if not dry_run:
        state = OutreachState.load()
        state.last_built_at = now
        state.save(update_fields=["last_built_at", "updated_at"])
    return result


def eligible_demands(now: datetime):
    cutoff = now - _max_post_age()
    return (
        ItemRequest.objects.filter(
            type=RequestType.DEMAND,
            status=RequestStatus.ACTIVE,
            imported=True,
            expires_at__gt=now,
            user__is_active=True,
            user__telegram_user_id__gte=SYNTHETIC_TELEGRAM_USER_ID,
            outreach_message__isnull=True,
        )
        .filter(Q(market_post__posted_at__gte=cutoff) | Q(market_post__isnull=True, created_at__gte=cutoff))
        .filter(
            demand_matches__status=MatchStatus.CONNECTED,
            demand_matches__score__gte=_min_score(),
            demand_matches__supply_request__status=RequestStatus.ACTIVE,
            demand_matches__supply_request__expires_at__gt=now,
        )
        .select_related("user", "market_post")
        .distinct()
        .order_by("-created_at")
    )


def valid_matches(demand: ItemRequest, now: datetime) -> list[Match]:
    return list(
        Match.objects.filter(
            demand_request=demand,
            status=MatchStatus.CONNECTED,
            score__gte=_min_score(),
            supply_request__status=RequestStatus.ACTIVE,
            supply_request__expires_at__gt=now,
        )
        .select_related("supply_request", "supply_request__user")
        .order_by("-score", "supply_request__flight_date", "pk")
    )


def _recipient_skip_reason(username: str, now: datetime, seen: set[str]) -> str:
    if not username:
        return "no_username"
    if not is_person_username(username):
        return "not_a_person"
    if username.lower() in seen:
        return "duplicate_person"
    if is_opted_out(username=username):
        return "opted_out"
    if _contacted_recently(username, now):
        return "cooldown"
    return ""


def _contacted_recently(username: str, now: datetime, *, exclude_pk: int | None = None) -> bool:
    busy = Q(status__in=(OutreachStatus.QUEUED, OutreachStatus.SENDING)) | Q(
        status=OutreachStatus.SENT,
        sent_at__gte=now - _cooldown(),
    )
    return (
        OutreachMessage.objects.filter(busy, recipient_username__iexact=username)
        .exclude(pk=exclude_pk)
        .exists()
    )


# ---------------------------------------------------------------- sending


def sending_gate(now: datetime | None = None) -> str:
    """Empty string when the worker may send now, else the reason it may not."""
    now = now or timezone.now()
    if not sending_enabled():
        return "disabled"
    state = OutreachState.load()
    if state.stopped:
        return "stopped"
    if state.paused_until and state.paused_until > now:
        return "paused"
    start, end = _send_window()
    if not start <= now.astimezone(TEHRAN_TZ).hour < end:
        return "outside_window"
    if attempted_today(now) >= daily_limit():
        return "daily_limit"
    return ""


def attempted_today(now: datetime) -> int:
    midnight = now.astimezone(TEHRAN_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return OutreachMessage.objects.filter(status__in=ATTEMPTED_STATUSES, claimed_at__gte=midnight).count()


def claim_next_message(now: datetime | None = None) -> tuple[OutreachMessage | None, str]:
    now = now or timezone.now()
    gate = sending_gate(now)
    if gate:
        return None, gate
    _expire_stale_claims(now)
    with transaction.atomic():
        queued = (
            OutreachMessage.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(status=OutreachStatus.QUEUED)
            .select_related("recipient", "demand_request")
            .order_by("-demand_request__created_at", "pk")[:CLAIM_BATCH]
        )
        for message in queued:
            reason = skip_reason(message, now)
            if reason:
                _finish(message, OutreachStatus.SKIPPED, error_code=reason)
                continue
            message.text = current_text(message, now)
            message.status = OutreachStatus.SENDING
            message.claimed_at = now
            message.attempts += 1
            message.save(update_fields=["text", "status", "claimed_at", "attempts", "updated_at"])
            return message, ""
    return None, "empty"


def skip_reason(message: OutreachMessage, now: datetime) -> str:
    demand = message.demand_request
    if demand.status != RequestStatus.ACTIVE or demand.expires_at <= now:
        return "demand_closed"
    if not message.recipient.is_active or not is_shadow_user(message.recipient):
        return "claimed"
    if posted_at_for(demand) < now - _max_post_age():
        return "stale"
    if not valid_matches(demand, now):
        return "no_match"
    if is_opted_out(username=message.recipient_username):
        return "opted_out"
    if _contacted_recently(message.recipient_username, now, exclude_pk=message.pk):
        return "cooldown"
    return ""


def _expire_stale_claims(now: datetime) -> int:
    # A worker that died mid-send may have delivered; never send the same DM twice.
    return OutreachMessage.objects.filter(
        status=OutreachStatus.SENDING,
        claimed_at__lt=now - LEASE,
    ).update(status=OutreachStatus.FAILED, error_code="lease_expired", updated_at=now)


def record_result(
    message: OutreachMessage,
    *,
    outcome: str,
    error_code: str = "",
    error_detail: str = "",
    recipient_telegram_id: int | None = None,
    telegram_message_id: int | None = None,
    retry_after_seconds: int | None = None,
    now: datetime | None = None,
) -> OutreachMessage:
    now = now or timezone.now()
    if message.status != OutreachStatus.SENDING:
        raise OutreachConflict(f"message {message.pk} is {message.status}, not SENDING")
    if outcome not in {"sent", "failed", "retry"}:
        raise ValueError(f"unknown outcome {outcome!r}")
    if recipient_telegram_id:
        message.recipient_telegram_id = recipient_telegram_id
    if outcome == "sent":
        message.sent_at = now
        message.telegram_message_id = telegram_message_id
        _finish(message, OutreachStatus.SENT, extra=["sent_at", "telegram_message_id", "recipient_telegram_id"])
        return message
    if outcome == "failed":
        _finish(
            message,
            OutreachStatus.FAILED,
            error_code=error_code,
            error_detail=error_detail,
            extra=["recipient_telegram_id"],
        )
        return message
    message.claimed_at = None
    _finish(
        message,
        OutreachStatus.QUEUED,
        error_code=error_code,
        error_detail=error_detail,
        extra=["claimed_at", "recipient_telegram_id"],
    )
    if error_code == PEER_FLOOD:
        pause_sending(now + PEER_FLOOD_PAUSE, reason=PEER_FLOOD)
    elif retry_after_seconds:
        pause_sending(now + timedelta(seconds=int(retry_after_seconds)), reason=error_code or "FLOOD_WAIT")
    return message


def _finish(
    message: OutreachMessage,
    status: str,
    *,
    error_code: str = "",
    error_detail: str = "",
    extra: list[str] | None = None,
) -> None:
    message.status = status
    message.error_code = (error_code or "")[:64]
    message.error_detail = (error_detail or "")[:500]
    message.save(update_fields=["status", "error_code", "error_detail", "updated_at", *(extra or [])])


def pause_sending(until: datetime, *, reason: str) -> OutreachState:
    state = OutreachState.load()
    if not state.paused_until or state.paused_until < until:
        state.paused_until = until
        state.pause_reason = reason[:64]
        state.save(update_fields=["paused_until", "pause_reason", "updated_at"])
    return state


def record_heartbeat(now: datetime | None = None) -> OutreachState:
    state = OutreachState.load()
    state.last_heartbeat_at = now or timezone.now()
    state.save(update_fields=["last_heartbeat_at", "updated_at"])
    return state


def status_summary(now: datetime | None = None) -> dict:
    now = now or timezone.now()
    state = OutreachState.load()
    return {
        "gate": sending_gate(now) or "open",
        "attempted_today": attempted_today(now),
        "daily_limit": daily_limit(),
        "queued": OutreachMessage.objects.filter(status=OutreachStatus.QUEUED).count(),
        "paused_until": state.paused_until.isoformat() if state.paused_until else None,
    }


def preview_messages(limit: int = 5, now: datetime | None = None) -> list[OutreachMessage]:
    """Next queued messages with the text they would be sent with now. Nothing is saved."""
    now = now or timezone.now()
    messages = list(
        OutreachMessage.objects.filter(status=OutreachStatus.QUEUED)
        .select_related("recipient", "demand_request", "demand_request__user", "demand_request__market_post")
        .order_by("-demand_request__created_at", "pk")[: max(1, min(limit, 50))]
    )
    for message in messages:
        message.text = current_text(message, now)
    return messages


def current_text(message: OutreachMessage, now: datetime) -> str:
    """Re-render with today's matches and wording; keeps the stored text if nothing matches."""
    matches = valid_matches(message.demand_request, now)
    if not matches:
        return message.text
    return outreach_text(message.demand_request, matches, message.token, now=now)


# ---------------------------------------------------------------- replies


def _recipient_q(username: str, telegram_user_id: int | None, *, username_field: str, id_field: str) -> Q | None:
    query = None
    if username:
        query = Q(**{f"{username_field}__iexact": username})
    if telegram_user_id:
        by_id = Q(**{id_field: telegram_user_id})
        query = by_id if query is None else query | by_id
    return query


def is_opted_out(*, username: str = "", telegram_user_id: int | None = None) -> bool:
    query = _recipient_q(
        clean_telegram_username(username),
        telegram_user_id,
        username_field="telegram_username",
        id_field="telegram_user_id",
    )
    return query is not None and OutreachOptOut.objects.filter(query).exists()


def record_opt_out(
    *,
    username: str = "",
    telegram_user_id: int | None = None,
    source: str = "reply",
    now: datetime | None = None,
) -> OutreachOptOut | None:
    now = now or timezone.now()
    username = clean_telegram_username(username)
    query = _recipient_q(username, telegram_user_id, username_field="telegram_username", id_field="telegram_user_id")
    if query is None:
        return None
    opt_out = OutreachOptOut.objects.filter(query).first() or OutreachOptOut.objects.create(
        telegram_username=username,
        telegram_user_id=telegram_user_id,
        source=source[:16],
    )
    pending = _recipient_q(
        username,
        telegram_user_id,
        username_field="recipient_username",
        id_field="recipient_telegram_id",
    )
    OutreachMessage.objects.filter(pending, status=OutreachStatus.QUEUED).update(
        status=OutreachStatus.SKIPPED,
        error_code="opted_out",
        updated_at=now,
    )
    return opt_out


def record_reply(
    *,
    username: str = "",
    telegram_user_id: int | None = None,
    now: datetime | None = None,
) -> OutreachMessage | None:
    query = _recipient_q(
        clean_telegram_username(username),
        telegram_user_id,
        username_field="recipient_username",
        id_field="recipient_telegram_id",
    )
    if query is None:
        return None
    message = OutreachMessage.objects.filter(query, status=OutreachStatus.SENT).order_by("-sent_at").first()
    if message is not None and message.replied_at is None:
        message.replied_at = now or timezone.now()
        message.save(update_fields=["replied_at", "updated_at"])
    return message


# ---------------------------------------------------------------- Mini App


def open_outreach(token: str, user: User, now: datetime | None = None) -> str:
    """Record the click, hand the imported demand to its owner, return where to go."""
    from users.services import merge_shadow_user

    message = (
        OutreachMessage.objects.select_related("recipient", "demand_request")
        .filter(token=token)
        .first()
    )
    if message is None:
        return "/app/"
    if message.opened_at is None:
        message.opened_at = now or timezone.now()
        message.opened_by = user
        message.save(update_fields=["opened_at", "opened_by", "updated_at"])
    if _same_person(message, user):
        merge_shadow_user(message.recipient, user)
        message.demand_request.refresh_from_db(fields=["user"])
    if message.demand_request.user_id == user.id:
        return f"/app/requests/{message.demand_request_id}/"
    return "/app/explore/"


def _same_person(message: OutreachMessage, user: User) -> bool:
    if message.recipient_telegram_id and message.recipient_telegram_id == user.telegram_user_id:
        return True
    username = (user.telegram_username or "").strip().lower()
    return bool(username) and username == message.recipient_username.lower()
