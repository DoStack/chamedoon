from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from item_requests.models import ItemRequest, RequestStatus, RequestType
from matching.contact import (
    SYNTHETIC_TELEGRAM_USER_ID,
    clean_telegram_username,
    is_person_username,
    is_shadow_user,
)
from matching.models import Match, MatchStatus
from matching.rules import is_person_request, is_superseded_duplicate
from outreach.messages import outreach_text, posted_at_for
from outreach.models import FIRST_CONTACT_KINDS, OutreachKind, OutreachMessage, OutreachOptOut, OutreachState, OutreachStatus, SentBy
from outreach import cities, conf
from telemetry import capture_user
from users.models import User

PEER_FLOOD = "PEER_FLOOD"
PEER_FLOOD_PAUSE = timedelta(hours=48)
LEASE = timedelta(minutes=15)
HUMAN_LEASE = timedelta(hours=6)
CLAIM_BATCH = 100
ROLLING_DAY = timedelta(hours=24)
ATTEMPTED_STATUSES = (OutreachStatus.SENDING, OutreachStatus.SENT, OutreachStatus.FAILED)


class OutreachConflict(Exception):
    pass


# Admin row first, then the environment: see outreach/conf.py.
def building_enabled() -> bool:
    return conf.building_enabled()


def sending_enabled() -> bool:
    return conf.sending_enabled()


def daily_limit() -> int:
    return conf.daily_limit()


def _min_score() -> Decimal:
    return conf.min_score()


def _max_post_age() -> timedelta:
    return conf.max_post_age()


def _cooldown() -> timedelta:
    return conf.cooldown()


def new_message_status() -> str:
    """Nothing leaves without a yes: a new message waits as Held until its batch is approved."""
    return OutreachStatus.QUEUED if conf.auto_queue() else OutreachStatus.HELD


AWAITING_YES = "awaiting_yes"


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
    """Every model that is switched on: housing first, then courier. Nothing is sent from here."""
    from outreach.courier import build_courier_waves
    from outreach.housing import build_housing_queue

    now = now or timezone.now()
    result = BuildResult()
    seen: set[str] = set()
    build_housing_queue(result, seen, now=now, dry_run=dry_run)
    if OutreachKind.C1 in conf.enabled_kinds():
        _build_c1(result, seen, now, dry_run)
    build_courier_waves(result, seen, now=now, dry_run=dry_run)
    from outreach import followups

    followups.build(result, now=now, dry_run=dry_run)
    from outreach import completion

    completion.build_followups(result, now=now, dry_run=dry_run)
    if not dry_run:
        state = OutreachState.load()
        state.last_built_at = now
        state.save(update_fields=["last_built_at", "updated_at"])
    return result


def _build_c1(result: BuildResult, seen: set[str], now: datetime, dry_run: bool) -> None:
    """One DM per imported demand that has fresh strong matches."""
    channels = cities.launch_channels()
    for demand in eligible_demands(now):
        username = clean_telegram_username(demand.user.telegram_username)
        reason = _recipient_skip_reason(username, now, seen) or _demand_skip_reason(demand)
        if reason:
            result.skip(reason)
            continue
        matches = valid_matches(demand, now)
        if not matches:
            result.skip("no_match")
            continue
        seen.add(username.lower())
        place = courier_metro(demand, channels)
        if place is None:
            result.skip("outside_cities")
            continue
        token = new_token()
        status = new_message_status()
        message = OutreachMessage(
            kind=OutreachKind.C1,
            recipient=demand.user,
            recipient_username=username,
            recipient_name=demand.user.first_name or "",
            demand_request=demand,
            text=outreach_text(demand, matches, token, now=now),
            token=token,
            status=status,
            error_code=AWAITING_YES if status == OutreachStatus.HELD else "",
            country=place.country,
            city=place.city,
            source_at=posted_at_for(demand),
        )
        if not dry_run:
            with transaction.atomic():
                message.save()
                message.matches.set(matches)
        result.created.append(message)


def new_token() -> str:
    return secrets.token_hex(8)  # no "_" or "-": safe inside any markup


def courier_metro(demand: ItemRequest, channels=None):
    """A courier route counts when one of its ends is a launch city. The sender's side wins the clock."""
    channels = cities.launch_channels() if channels is None else channels
    pairs = [(demand.origin_country, demand.origin_city), (demand.destination_country, demand.destination_city), *demand.destination_stop_pairs()]
    for country, city in pairs:
        found = cities.metro_of_name(country, city, channels)
        if found is not None:
            return found
    return None


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
    """Strong live matches whose traveler is a real person (never a channel, bot or business)."""
    matches = (
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
    return [match for match in matches if is_person_request(match.supply_request)]


def candidate_report(now: datetime | None = None, limit: int = 200) -> list[dict]:
    """Every active imported demand with a live match, and whether it would get a DM (and if not, why)."""
    now = now or timezone.now()
    demands = (
        ItemRequest.objects.filter(
            type=RequestType.DEMAND,
            status=RequestStatus.ACTIVE,
            imported=True,
            expires_at__gt=now,
            demand_matches__status=MatchStatus.CONNECTED,
            demand_matches__supply_request__status=RequestStatus.ACTIVE,
            demand_matches__supply_request__expires_at__gt=now,
        )
        .select_related("user", "market_post")
        .distinct()
        .order_by("-created_at")[:limit]
    )
    rows = []
    for demand in demands:
        matches = list(
            Match.objects.filter(
                demand_request=demand,
                status=MatchStatus.CONNECTED,
                supply_request__status=RequestStatus.ACTIVE,
                supply_request__expires_at__gt=now,
            )
            .select_related("supply_request", "supply_request__user")
            .order_by("-score", "pk")
        )
        matches = [match for match in matches if is_person_request(match.supply_request)]
        rows.append(
            {
                "request_id": demand.pk,
                "username": clean_telegram_username(demand.user.telegram_username),
                "route": f"{demand.origin_city} ({demand.origin_country}) -> "
                f"{demand.destination_city} ({demand.destination_country})",
                "desired_date": (demand.desired_date or demand.date_from).isoformat(),
                "posted_at": posted_at_for(demand).isoformat(),
                "status": _candidate_status(demand, matches, now),
                "travelers": [
                    {
                        "username": match.supply_request.user.telegram_username or "",
                        "flight_date": match.supply_request.flight_date.isoformat()
                        if match.supply_request.flight_date
                        else None,
                        "date_from": match.supply_request.date_from.isoformat(),
                        "date_to": match.supply_request.date_to.isoformat(),
                        "score": str(match.score),
                    }
                    for match in matches
                ],
            }
        )
    return rows


def _candidate_status(demand: ItemRequest, matches: list[Match], now: datetime) -> str:
    existing = OutreachMessage.objects.filter(demand_request=demand).first()
    if existing is not None:
        return f"messaged:{existing.status}"
    if not is_shadow_user(demand.user):
        return "registered_user"  # the bot already tells them about matches
    username = clean_telegram_username(demand.user.telegram_username)
    reason = _recipient_skip_reason(username, now, set()) or _demand_skip_reason(demand)
    if reason:
        return reason
    if courier_metro(demand) is None:
        return "outside_cities"
    if posted_at_for(demand) < now - _max_post_age():
        return "post_too_old"
    if not any(match.score >= _min_score() for match in matches):
        return "score_below_min"
    return "eligible"


def _recipient_skip_reason(username: str, now: datetime, seen: set[str]) -> str:
    if not username:
        return "no_username"
    if not is_person_username(username):
        return "not_a_person"
    if username.lower() in seen:
        return "duplicate_person"
    if is_opted_out(username=username):
        return "opted_out"
    if already_contacted(username=username):
        return "contacted"
    return ""


def _demand_skip_reason(demand: ItemRequest) -> str:
    if not is_person_request(demand):
        return "not_a_person"
    if is_superseded_duplicate(demand):
        return "duplicate_request"
    return ""


def already_contacted(*, username: str = "", telegram_user_id: int | None = None, exclude_pk: int | None = None) -> bool:
    """One first message per person, ever: unanswered it is the last one, and an answer moves to the chat.

    A message that was skipped never reached them, so it does not count; except one Telegram refused with PEER_FLOOD:
    that person is never tried again from any account (2026-11-08: three were re-queued on another account, and the
    last one brought saeid its real limit).
    """
    query = _recipient_q(clean_telegram_username(username), telegram_user_id, username_field="recipient_username", id_field="recipient_telegram_id")
    if query is None:
        return False
    counted = OutreachMessage.objects.filter(query, kind__in=FIRST_CONTACT_KINDS).exclude(pk=exclude_pk)
    return counted.exclude(Q(status=OutreachStatus.SKIPPED) & ~Q(error_code__startswith="peer_flood")).exists()


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
    if attempted_today(now) >= daily_limit():
        return "daily_limit"
    return ""


def attempted_today(now: datetime) -> int:
    """First messages since midnight in Toronto: the rollout step counts people per Toronto day. Videos are not counted."""
    from zoneinfo import ZoneInfo

    midnight = now.astimezone(ZoneInfo("America/Toronto")).replace(hour=0, minute=0, second=0, microsecond=0)
    # A message someone answered no longer counts: a reply frees its slot (Mohammad, 2026-11-08).
    return OutreachMessage.objects.filter(status__in=ATTEMPTED_STATUSES, claimed_at__gte=midnight, replied_at__isnull=True).count()


VIDEO_FOR = {
    OutreachKind.S1: "seeker",
    OutreachKind.S1B: "seeker",
    OutreachKind.S2: "seeker",
    OutreachKind.P1: "owner",
    OutreachKind.P1Q: "owner",
    OutreachKind.P6: "owner",
}


def video_for(message: OutreachMessage) -> str:
    """Which intro video follows this first message, or nothing."""
    return VIDEO_FOR.get(message.kind, "")


# First messages the model writes from the hook's draft. Courier and the guide (G1, written by its own model) are not.
WRITTEN_FIRST = {OutreachKind.S1, OutreachKind.S1B, OutreachKind.S2, OutreachKind.P1, OutreachKind.P1Q, OutreachKind.P5, OutreachKind.P6}


def person_account_id(message: OutreachMessage) -> int | None:
    """The account that already has this person: their chat's account, else the account of a message that reached or
    tried to reach them (sending, sent, failed). None for someone nobody wrote to yet."""
    from outreach.models import OutreachConversation

    username = clean_telegram_username(message.recipient_username or "")
    chat = Q(telegram_user_id=message.recipient_telegram_id) if message.recipient_telegram_id else Q(pk__in=[])
    if username:
        chat |= Q(username__iexact=username)
    found = OutreachConversation.objects.filter(chat).values_list("account_id", flat=True).first()
    if found is not None:
        return found
    query = _recipient_q(username, message.recipient_telegram_id, username_field="recipient_username", id_field="recipient_telegram_id")
    if query is None:
        return None
    return (
        OutreachMessage.objects.filter(query, status__in=[OutreachStatus.SENDING, OutreachStatus.SENT, OutreachStatus.FAILED], account__isnull=False)
        .exclude(pk=message.pk)
        .order_by("claimed_at")
        .values_list("account_id", flat=True)
        .first()
    )


def claim_next_message(
    now: datetime | None = None, *, account=None, other_ready=None, room_for_video: bool = True, rewrite: bool = True
) -> tuple[OutreachMessage | None, str]:
    """The next message this account may send. Newest ads first; old ones fall out as `stale`.

    A follow-up belongs to its chat's account. A message planned for another account waits for it while that account
    can send, and moves here once it cannot.
    """
    now = now or timezone.now()
    gate = sending_gate(now)
    if gate:
        return None, gate
    _expire_stale_claims(now)
    outside_window = False
    model_down = False
    with transaction.atomic():
        queued = (
            OutreachMessage.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(status=OutreachStatus.QUEUED)
            # A first message or P1G whose kind is switched off waits in the queue; it is not dropped.
            .exclude(kind__in=[kind for kind in (*FIRST_CONTACT_KINDS, OutreachKind.P1G) if kind not in conf.enabled_kinds()])
            .select_related("recipient", "demand_request", "listing", "listing__post", "listing__post__source", "conversation", "account")
            .order_by(F("source_at").desc(nulls_last=True), "-pk")[:CLAIM_BATCH]
        )
        for message in queued:
            if message.conversation_id and (account is None or message.conversation.account_id != account.pk):
                continue
            if (
                account is not None
                and account.fresh_hours
                and message.kind in FIRST_CONTACT_KINDS
                and (message.source_at is None or message.source_at < now - timedelta(hours=account.fresh_hours))
            ):
                # This account writes only to the newest posters (monis: posted in the last 24 hours).
                continue
            if not room_for_video and video_for(message):
                continue
            if message.kind == OutreachKind.P1G:
                from outreach import completion

                # The gap follow-up stays with the account of the first message, and never before 24 hours.
                if account is None or message.account_id != account.pk:
                    continue
                if not completion.followup_due(message, now) and not completion.followup_skip_reason(message, now):
                    continue
            owner = person_account_id(message)
            if owner is not None and (account is None or owner != account.pk):
                # One person, one account: whoever already wrote to them or chats with them sends everything after,
                # and the message waits for that account, never moves (Mohammad, 2026-11-11).
                continue
            if (message.extra or {}).get("account_pinned") and (account is None or message.account_id != account.pk):
                # Requeued for its own account on Mohammad's word: it waits for that account only.
                continue
            planned = message.account
            if account is not None and planned is not None and planned.pk != account.pk and other_ready is not None and other_ready(planned):
                continue
            reason = skip_reason(message, now)
            if reason:
                _finish(message, OutreachStatus.SKIPPED, error_code=reason, error_detail=skip_detail(message))
                continue
            if message.listing is not None and message.kind in AREA_CITY_KINDS:
                from home.areas import area_metro, link_area, move_to_metro

                target, verdict = area_metro(message.listing)
                if verdict == "unclear":
                    # The area names places of several cities and the group says another: nothing goes (#308).
                    _finish(message, OutreachStatus.SKIPPED, error_code="area_city_unclear", error_detail=skip_detail(message))
                    continue
                if verdict == "moved":
                    # The area is in another target city: the listing and this message take it, counted there.
                    move_to_metro(message.listing, target)
                    link_area(message.listing)
                    message.country, message.city = target.country, target.city
                    message.save(update_fields=["country", "city", "updated_at"])
            country, city = message_place(message)
            if not cities.in_send_window(country, city, now):
                outside_window = True
                continue
            from outreach import guard, guide, kindcheck

            if message.kind == OutreachKind.G1 and guide.write(message) is None:
                # The model is down: the guide waits, nothing goes out unwritten.
                model_down = True
                break
            text = message.text
            try:
                text = current_text(message, now)
                if rewrite and message.kind in WRITTEN_FIRST:
                    from outreach import writer

                    # The hook says what to say; the model writes it for this person (operating plan v1).
                    text = writer.rewrite(text, purpose=f"first message to someone we never wrote to ({message.kind}); no link; keep its question word for word", first=True)
                message.text = guard.check(text)
            except guard.Blocked as exc:
                _finish(message, OutreachStatus.SKIPPED, error_code=f"blocked_{exc.reason}"[:64], error_detail=f"text: {text}"[:500])
                continue
            if message.kind == OutreachKind.G1 and guide.reveals(message.text):
                # Never the poster outside the Mini App: no @, no other link, no phone.
                _finish(message, OutreachStatus.SKIPPED, error_code=f"blocked_{guide.reveals(message.text)}"[:64], error_detail=f"text: {message.text}"[:500])
                continue
            verdict = kindcheck.agrees(message)
            if verdict is None:
                # The model is down: this one waits, nothing goes out unchecked, and the claim stops here instead of
                # asking the model for every queued row (2026-11-08: 100 rows in a row timed the claim out).
                model_down = True
                break
            if not verdict:
                kindcheck.flag(message)
                checked = (message.extra or {}).get("kind_checked")
                _finish(message, OutreachStatus.SKIPPED, error_code="kind_mismatch", error_detail=f"post reads as {checked}, message is {message.kind}", extra=["extra"])
                continue
            if kindcheck.not_housing(message):
                # They want no room or home (an address to register, say): no housing message at all, and the
                # listing leaves the seeker counts.
                kindcheck.mark_not_housing(message.listing)
                _finish(message, OutreachStatus.SKIPPED, error_code="not_housing", error_detail=f"post_housing={(message.extra or {}).get('post_housing')}", extra=["extra"])
                continue
            if not kindcheck.clear(message):
                # Several areas, or unsure of kind, area or city: skip the person, leave the listing as it is.
                # The model's count, names and doubt stay on the row, so an audit can check the skip (#342).
                _finish(message, OutreachStatus.SKIPPED, error_code="unclear_post", error_detail=kindcheck.unclear_detail(message), extra=["extra"])
                continue
            place = kindcheck.area_ok(message)
            if place is None:
                model_down = True
                break
            if not place:
                # The teaser and the count would be for a place they did not ask for: nothing goes.
                _finish(message, OutreachStatus.SKIPPED, error_code="area_mismatch", error_detail=f"stored area {message.listing.area!r} is not a place the post names", extra=["extra"])
                continue
            from outreach.housing import HOUSING_KINDS, area_unresolved, town_only_landmark

            if area_unresolved(message):
                _finish(message, OutreachStatus.HELD, error_code="area_unresolved")
                continue
            if message.kind in HOUSING_KINDS and town_only_landmark(message.listing):
                # The post names the place only inside a campus or station name: a person picks the place (#335).
                _finish(message, OutreachStatus.HELD, error_code="town_landmark")
                continue
            message.status = OutreachStatus.SENDING
            message.claimed_at = now
            message.attempts += 1
            message.save(update_fields=["kind", "variant", "extra", "text", "status", "claimed_at", "attempts", "updated_at"])
            return message, ""
    if model_down:
        return None, "model_down"
    return None, "outside_window" if outside_window else "empty"


def message_place(message: OutreachMessage) -> tuple[str, str]:
    """Whose clock this message goes by. A row from before cities were stored goes by its route."""
    if message.country:
        return message.country, message.city
    if message.demand_request_id or message.courier_request_id:
        place = courier_metro(message.demand_request or message.courier_request)
        if place is not None:
            return place.country, place.city
    return "CA", "Toronto"


def skip_detail(message: OutreachMessage) -> str:
    """The facts a skip at claim time was read from, so an audit can check it (#342)."""
    listing = message.listing
    if listing is None:
        return f"kind={message.kind} source_at={message.source_at:%Y-%m-%d %H:%M}" if message.source_at else f"kind={message.kind}"
    posted = f" source_at={message.source_at:%Y-%m-%d %H:%M}" if message.source_at else ""
    return f"kind={message.kind} listing={listing.pk} status={listing.status} city={listing.city} area={listing.area!r}{posted}"


def skip_reason(message: OutreachMessage, now: datetime) -> str:
    if message.kind != OutreachKind.C1:
        from outreach import courier, followups, housing

        if message.kind in (OutreachKind.P3, OutreachKind.P5):
            return followups.skip_reason(message, now)
        if message.kind == OutreachKind.G1:
            from outreach import guide

            return guide.skip_reason(message, now)
        if message.kind == OutreachKind.P1G:
            from outreach import completion

            return completion.followup_skip_reason(message, now)
        if message.kind in (OutreachKind.C2, OutreachKind.C3):
            return courier.skip_reason(message, now)
        return housing.skip_reason(message, now)
    demand = message.demand_request
    if demand.status != RequestStatus.ACTIVE or demand.expires_at <= now:
        return "demand_closed"
    if not message.recipient.is_active or not is_shadow_user(message.recipient):
        return "claimed"
    if posted_at_for(demand) < now - _max_post_age():
        return "stale"
    if courier_metro(demand) is None:
        return "outside_cities"
    if not valid_matches(demand, now):
        return "no_match"
    if is_opted_out(username=message.recipient_username):
        return "opted_out"
    if already_contacted(username=message.recipient_username, exclude_pk=message.pk):
        return "contacted"
    return ""


def _expire_stale_claims(now: datetime) -> int:
    # A worker that died mid-send may have delivered; never send the same DM twice.
    expired = OutreachMessage.objects.filter(
        status=OutreachStatus.SENDING,
        sent_by=SentBy.AGENT,
        claimed_at__lt=now - LEASE,
    ).update(status=OutreachStatus.FAILED, error_code="lease_expired", updated_at=now)
    # A human claim nobody reported on goes back to the pool after HUMAN_LEASE (plan section 3): the admin taps
    # Sent or Failed after each one, so an unreported claim was never worked.
    returned = OutreachMessage.objects.filter(
        status=OutreachStatus.SENDING,
        sent_by=SentBy.HUMAN,
        claimed_at__lt=now - HUMAN_LEASE,
    ).update(status=OutreachStatus.QUEUED, sent_by=SentBy.AGENT, claimed_at=None, account=None, error_code="human_claim_expired", updated_at=now)
    return expired + returned


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
    account = message.account
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
    if error_code == PEER_FLOOD and account is not None:
        # Never handed to the next account: one recipient refused three accounts in a row on 2026-11-08.
        _finish(message, OutreachStatus.SKIPPED, error_code="peer_flood", error_detail=error_detail, extra=["recipient_telegram_id"])
        from outreach import accounts

        accounts.record_peer_flood(account, now, error_detail)
        return message
    message.claimed_at = None
    # Back in the queue for the next account; a follow-up stays with its chat through `conversation`.
    message.account = None
    _finish(
        message,
        OutreachStatus.QUEUED,
        error_code=error_code,
        error_detail=error_detail,
        extra=["claimed_at", "recipient_telegram_id", "account"],
    )
    if account is None:
        # The old sender, which has no account: everything waits.
        if error_code == PEER_FLOOD:
            pause_sending(now + PEER_FLOOD_PAUSE, reason=PEER_FLOOD)
        elif retry_after_seconds:
            pause_sending(now + timedelta(seconds=int(retry_after_seconds)), reason=error_code or "FLOOD_WAIT")
        return message
    from outreach import accounts

    if error_code == PEER_FLOOD:
        accounts.record_peer_flood(account, now)
    elif error_code == accounts.SESSION_INVALID:
        accounts.record_session_invalid(account)
    elif retry_after_seconds:
        accounts.record_flood_wait(account, retry_after_seconds, now)
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


def hold_message(message: OutreachMessage, *, reason: str = "") -> OutreachMessage:
    """Keep a queued message out of claims until someone reviews it."""
    if message.status != OutreachStatus.QUEUED:
        raise OutreachConflict(f"message {message.pk} is {message.status}, not QUEUED")
    _finish(message, OutreachStatus.HELD, error_code=reason or "held")
    return message


def release_message(message: OutreachMessage) -> OutreachMessage:
    if message.status != OutreachStatus.HELD:
        raise OutreachConflict(f"message {message.pk} is {message.status}, not HELD")
    _finish(message, OutreachStatus.QUEUED)
    return message


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
        .select_related("recipient", "demand_request", "demand_request__user", "demand_request__market_post", "listing", "listing__post", "listing__post__source")
        .order_by(F("source_at").desc(nulls_last=True), "-pk")[: max(1, min(limit, 50))]
    )
    for message in messages:
        message.text = current_text(message, now)
    return messages


def current_text(message: OutreachMessage, now: datetime) -> str:
    """Re-render with today's facts and wording, with no emoji in anything sent to a person."""
    from outreach.templates import no_emoji

    return no_emoji(_current_text(message, now))


def _current_text(message: OutreachMessage, now: datetime) -> str:
    """Re-render with today's facts and wording; keeps the stored text if nothing matches."""
    if message.kind != OutreachKind.C1:
        from outreach import courier, followups, housing

        if message.kind in (OutreachKind.P3, OutreachKind.P5, OutreachKind.G1):
            return message.text
        if message.kind == OutreachKind.P1G:
            from outreach import completion

            return completion.followup_render(message)
        if message.kind in (OutreachKind.C2, OutreachKind.C3):
            return courier.render(message)
        return housing.render(message, now)
    matches = valid_matches(message.demand_request, now)
    if not matches:
        return message.text
    return outreach_text(message.demand_request, matches, message.token, now=now)


# ---------------------------------------------------------------- replies


# Housing messages that name the listing's city or count its seekers.
AREA_CITY_KINDS = (OutreachKind.P1, OutreachKind.P1Q, OutreachKind.P6, OutreachKind.S1, OutreachKind.S1B, OutreachKind.S2)


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
    block_listings: bool = False,
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
        block_listings=block_listings,
    )
    if block_listings and not opt_out.block_listings:
        opt_out.block_listings = True
        opt_out.save(update_fields=["block_listings"])
    pending = _recipient_q(
        username,
        telegram_user_id,
        username_field="recipient_username",
        id_field="recipient_telegram_id",
    )
    OutreachMessage.objects.filter(pending, status__in=(OutreachStatus.QUEUED, OutreachStatus.HELD)).update(
        status=OutreachStatus.SKIPPED,
        error_code="opted_out",
        updated_at=now,
    )
    OutreachMessage.objects.filter(pending, status=OutreachStatus.SENT, stopped_at__isnull=True).update(stopped_at=now, updated_at=now)
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

    now = now or timezone.now()
    message = (
        OutreachMessage.objects.select_related("recipient", "demand_request", "courier_request")
        .filter(token=token)
        .filter(Q(demand_request__isnull=False) | Q(courier_request__isnull=False))
        .first()
    )
    if message is None:
        return "/app/"
    if message.demand_request_id is None:
        return _open_own_request(message, user, now)
    matches = valid_matches(message.demand_request, now)
    if not _same_person(message, user):
        # Someone else (the operator, a forwarded link): show the same travelers, read-only, and
        # do not count it as the recipient opening their link.
        return _travelers_path(matches)
    if message.opened_at is None:
        message.opened_at = now
        message.opened_by = user
        message.save(update_fields=["opened_at", "opened_by", "updated_at"])
        capture_user("outreach_opened", user, source="server", side="courier", properties={"message_id": message.pk, "request_id": message.demand_request_id, "matches": len(matches)})
    merge_shadow_user(message.recipient, user)
    message.demand_request.refresh_from_db(fields=["user"])
    if message.demand_request.user_id != user.id:
        return _travelers_path(matches)
    if len(matches) == 1:
        # Straight to the traveler: contact details and the prefilled «message» button.
        return f"/app/matches/{matches[0].pk}/"
    return f"/app/requests/{message.demand_request_id}/"


def _open_own_request(message: OutreachMessage, user: User, now: datetime) -> str:
    """C2 and C3: their imported trip or request becomes theirs on sign-in. Anyone else sees it read-only."""
    from users.services import merge_shadow_user

    request = message.courier_request
    if not _same_person(message, user):
        return f"/app/explore/{request.pk}/"
    if message.opened_at is None:
        message.opened_at = now
        message.opened_by = user
        message.member_at = message.member_at or now
        message.save(update_fields=["opened_at", "opened_by", "member_at", "updated_at"])
        capture_user("outreach_opened", user, source="server", side="courier", properties={"message_id": message.pk, "kind": message.kind, "variant": message.variant})
    if message.recipient is not None:
        merge_shadow_user(message.recipient, user)
    request.refresh_from_db(fields=["user"])
    return f"/app/requests/{request.pk}/" if request.user_id == user.id else f"/app/explore/{request.pk}/"


def _travelers_path(matches: list[Match]) -> str:
    supply_ids = sorted({match.supply_request_id for match in matches})
    if not supply_ids:
        return "/app/explore/"
    if len(supply_ids) == 1:
        return f"/app/explore/{supply_ids[0]}/"
    return f"/app/explore/?type=SUPPLY&ids={','.join(str(pk) for pk in supply_ids)}"


def _same_person(message: OutreachMessage, user: User) -> bool:
    if message.recipient_telegram_id and message.recipient_telegram_id == user.telegram_user_id:
        return True
    username = (user.telegram_username or "").strip().lower()
    return bool(username) and username == message.recipient_username.lower()
