"""Housing outreach: who gets which first message, and what it says now.

Everything about a person comes from Telegram's poster fields, read through `home.ownership` and `poster_contact`.
A person has no User here until they sign in. One message per person, on their newest ad in a launch city.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from home import alert_store, conf as home_conf, deeplink
from home.alerts import alert_from_wanted, search
from home.models import HomeAlert, HomeChannel, HomeListing, HomePost, ListingKind, ListingStatus, ListerType, PostStatus
from home.ownership import APP_PREFIX, owner_telegram_id
from market.poster import poster_contact
from outreach import cities, conf, templates
from outreach.models import OutreachAccount, OutreachKind, OutreachMessage, OutreachStatus
from outreach.services import AWAITING_YES, already_contacted, is_opted_out, new_message_status, new_token
from telemetry import capture_user
from users.models import User

SOURCE_PREFIX = "o-"
REPOSTER_POSTS = 5
REPOSTER_DAYS = 30
SEEKER_KINDS = (OutreachKind.S1, OutreachKind.S1B, OutreachKind.S2)
HOUSING_KINDS = (*SEEKER_KINDS, OutreachKind.P1, OutreachKind.P1Q, OutreachKind.P6)
# «چند مدل همزمان»: the first one that fits.
PRIORITY = (OutreachKind.S1, OutreachKind.S1B, OutreachKind.P1, OutreachKind.S2, OutreachKind.P6)


@dataclass
class Person:
    telegram_id: int
    username: str
    name: str = ""
    # Newest first, launch cities only.
    listings: list[tuple[HomeListing, HomeChannel]] = field(default_factory=list)


@dataclass
class Plan:
    kind: str
    listing: HomeListing
    channel: HomeChannel
    extra: dict


def fresh_listings(now: datetime):
    """Live crawled ads and requests from the last days. Newest first."""
    return (
        HomeListing.objects.filter(
            status=ListingStatus.ACTIVE,
            needs_review=False,
            canonical__isnull=True,
            post__status=PostStatus.ACCEPTED,
            post__posted_at__gte=now - conf.max_post_age(),
            post__poster_read_at__isnull=False,
        )
        .exclude(post__source__username__startswith=APP_PREFIX)
        .select_related("post", "post__source")
        .order_by("-post__posted_at", "-pk")
    )


def people(now: datetime, result=None) -> list[Person]:
    channels = cities.launch_channels()
    found: dict[int, Person] = {}

    def skip(reason: str) -> None:
        if result is not None:
            result.skip(reason)

    for listing in fresh_listings(now):
        post = listing.post
        telegram_id = owner_telegram_id(post)
        if telegram_id is None:
            skip("not_a_person")
            continue
        username = poster_contact(post, source_username=post.source.username)
        if not username:
            skip("no_username")
            continue
        channel = cities.metro_of_listing(listing, channels)
        if channel is None:
            skip("outside_cities")
            continue
        person = found.setdefault(telegram_id, Person(telegram_id, username, post.poster_name))
        person.listings.append((listing, channel))
    return list(found.values())


def is_member(telegram_id: int, username: str = "") -> bool:
    """Signed in to the Mini App or opened the bot. The bot talks to them, by their own settings; the accounts do not."""
    if User.objects.filter(telegram_user_id=telegram_id).exists() or alert_store.bot_open(telegram_id):
        return True
    return bool(username) and User.objects.filter(telegram_username__iexact=username).exists()


def _is_agent(listing: HomeListing) -> bool:
    return listing.lister_type == ListerType.AGENT or listing.post.source.is_agency


def budget_of(wanted: HomeListing):
    return wanted.price_max or wanted.price_amount


def matches_for(wanted: HomeListing, channel: HomeChannel) -> list[HomeListing]:
    """The real matcher, the one alerts use, on an alert that is not saved."""
    return search(HomeAlert(**alert_from_wanted(wanted, channel.city, telegram_id=0)))


def city_has_offers(channel: HomeChannel) -> bool:
    probe = HomeAlert(country=channel.country, city=channel.city, answered=["budget", "type", "gender", "rooms", "registration", "term", "area"], complete=True)
    return bool(search(probe, limit=1))


def _plan_seeker(person: Person) -> dict[str, Plan]:
    plans: dict[str, Plan] = {}
    for listing, channel in person.listings:
        if listing.kind != ListingKind.WANTED:
            continue
        budget = budget_of(listing)
        if budget:
            found = matches_for(listing, channel)
            if found:
                plans.setdefault(OutreachKind.S1, Plan(OutreachKind.S1, listing, channel, {"budget": str(budget), "currency": listing.price_currency, "count": len(found)}))
                continue
        elif city_has_offers(channel):
            plans.setdefault(OutreachKind.S1B, Plan(OutreachKind.S1B, listing, channel, {}))
            continue
        plans.setdefault(OutreachKind.S2, Plan(OutreachKind.S2, listing, channel, {}))
    return plans


def _reposter(telegram_id: int, now: datetime) -> bool:
    return HomePost.objects.filter(poster_user_id=telegram_id, posted_at__gte=now - timedelta(days=REPOSTER_DAYS)).count() >= REPOSTER_POSTS


def _plan_poster(person: Person, now: datetime) -> dict[str, Plan]:
    plans: dict[str, Plan] = {}
    offers = [(listing, channel) for listing, channel in person.listings if listing.kind == ListingKind.OFFER]
    owner = next(((listing, channel) for listing, channel in offers if not _is_agent(listing)), None)
    if owner is not None:
        plans[OutreachKind.P1] = Plan(OutreachKind.P1, *owner, {"reposter": _reposter(person.telegram_id, now)})
    agency = [(listing, channel) for listing, channel in offers if _is_agent(listing)]
    if agency:
        counts: dict[int, int] = {}
        for _listing, channel in agency:
            counts[channel.pk] = counts.get(channel.pk, 0) + 1
        best = max(counts, key=counts.get)
        listing, channel = next(pair for pair in agency if pair[1].pk == best)
        plans[OutreachKind.P6] = Plan(OutreachKind.P6, listing, channel, {"count": counts[best]})
    return plans


def plans_for(person: Person, now: datetime) -> dict[str, Plan]:
    return {**_plan_poster(person, now), **_plan_seeker(person)}


def choose(person: Person, now: datetime, kinds: set[str]) -> Plan | None:
    plans = plans_for(person, now)
    for kind in PRIORITY:
        if kind not in plans:
            continue
        if kind == OutreachKind.P1 and not ({OutreachKind.P1, OutreachKind.P1Q} & kinds):
            continue
        if kind != OutreachKind.P1 and kind not in kinds:
            continue
        return plans[kind]
    return None


def person_skip_reason(person: Person) -> str:
    if is_member(person.telegram_id, person.username):
        return "member"
    if is_opted_out(username=person.username, telegram_user_id=person.telegram_id):
        return "opted_out"
    if already_contacted(username=person.username, telegram_user_id=person.telegram_id):
        return "contacted"
    return ""


def _p1_or_question(token: str, kinds: set[str]) -> str:
    """Half and half, by token, while both are on. They are compared on the same first people."""
    both = {OutreachKind.P1, OutreachKind.P1Q} <= kinds
    if both:
        return OutreachKind.P1Q if int(token[:2], 16) % 2 else OutreachKind.P1
    return OutreachKind.P1 if OutreachKind.P1 in kinds else OutreachKind.P1Q


def new_message(person: Person, plan: Plan, *, kind: str | None = None, account: OutreachAccount | None = None, batch: str = "", now: datetime | None = None) -> OutreachMessage:
    token = new_token()
    status = new_message_status()
    message = OutreachMessage(
        kind=kind or plan.kind,
        recipient_username=person.username,
        recipient_name=person.name,
        recipient_telegram_id=person.telegram_id,
        listing=plan.listing,
        country=plan.channel.country,
        city=plan.channel.city,
        source_at=plan.listing.post.posted_at,
        extra=plan.extra,
        token=token,
        text="",
        status=status,
        error_code=AWAITING_YES if status == OutreachStatus.HELD else "",
        account=account,
        batch=batch,
    )
    message.text = render(message, now or timezone.now(), first=True)
    return message


def build_housing_queue(result, seen: set[str], *, now: datetime, dry_run: bool, limit: int | None = None) -> None:
    kinds = conf.enabled_kinds() & {str(kind) for kind in (*HOUSING_KINDS,)}
    if not kinds:
        return
    built = 0
    for person in people(now, result):
        if limit is not None and built >= limit:
            break
        reason = person_skip_reason(person)
        if not reason and person.username.lower() in seen:
            reason = "duplicate_person"
        if reason:
            result.skip(reason)
            continue
        plan = choose(person, now, kinds)
        if plan is None:
            result.skip("kind_off")
            continue
        seen.add(person.username.lower())
        message = new_message(person, plan, now=now)
        if plan.kind == OutreachKind.P1:
            message.kind = _p1_or_question(message.token, kinds)
            message.text = render(message, now, first=True)
        if not dry_run:
            message.save()
        result.created.append(message)
        built += 1


# ---------------------------------------------------------------- what the message says now


def listing_live(listing: HomeListing | None) -> bool:
    return listing is not None and listing.status == ListingStatus.ACTIVE and not listing.needs_review and listing.canonical_id is None


def link_for(message: OutreachMessage) -> str:
    """Always with the city, and the listing's own city wins. `src` carries the token, which the sign-in reads."""
    source = f"{SOURCE_PREFIX}{message.token}"
    if message.kind == OutreachKind.S1:
        budget = message.extra.get("budget")
        band = (0, int(float(budget))) if budget else None
        return home_conf.mini_app_link(start=deeplink.build(message.country, message.city, band=band, source=source))
    if message.kind == OutreachKind.P6:
        return home_conf.mini_app_link(start=deeplink.build(message.country, message.city, source=source))
    return home_conf.mini_app_link(start=deeplink.build(message.country, message.city, listing=message.listing_id, source=source))


def _refreshed_s1(message: OutreachMessage) -> None:
    """A match that went away turns S1 into S1b at send time. A fresh count otherwise."""
    channel = HomeChannel.objects.filter(country=message.country, city=message.city).first()
    found = matches_for(message.listing, channel) if channel is not None and message.listing is not None else []
    if found:
        message.extra = {**message.extra, "count": len(found)}
    else:
        message.kind = OutreachKind.S1B
        message.extra = {}


def render(message: OutreachMessage, now: datetime, *, first: bool = False) -> str:
    """The text as it goes out. Changes kind and variant on the row when the facts changed; the claim saves them."""
    if message.kind == OutreachKind.S1 and not first:
        _refreshed_s1(message)
    rng = random.Random(message.token)
    name = templates.valid_name(message.recipient_name, message.recipient_username)
    city = message.city
    if message.kind == OutreachKind.S1:
        text, variant = templates.s1(rng, name, city, budget=_number(message.extra.get("budget")), currency=message.extra.get("currency") or "", count=message.extra.get("count") or 0, url=link_for(message))
    elif message.kind == OutreachKind.S1B:
        text, variant = templates.s1b(rng, name, city)
    elif message.kind == OutreachKind.S2:
        text, variant = templates.s2(rng, name, city)
    elif message.kind == OutreachKind.P1:
        text, variant = templates.p1(rng, name, city, url=link_for(message), reposter=bool(message.extra.get("reposter")))
    elif message.kind == OutreachKind.P1Q:
        text, variant = templates.p1q(rng, name, city, reposter=bool(message.extra.get("reposter")))
    elif message.kind == OutreachKind.P6:
        text, variant = templates.p6(rng, name, city, count=message.extra.get("count") or 1, url=link_for(message))
    else:
        return message.text
    message.variant = variant
    return text


def _number(value):
    if value in (None, ""):
        return 0
    number = float(value)
    return int(number) if number.is_integer() else number


def skip_reason(message: OutreachMessage, now: datetime) -> str:
    telegram_id = message.recipient_telegram_id
    if message.listing is None or not listing_live(message.listing):
        return "listing_closed"
    if message.source_at and message.source_at < now - conf.max_post_age():
        return "stale"
    if is_opted_out(username=message.recipient_username, telegram_user_id=telegram_id):
        return "opted_out"
    if telegram_id and is_member(telegram_id, message.recipient_username):
        return "member"
    if already_contacted(username=message.recipient_username, telegram_user_id=telegram_id, exclude_pk=message.pk):
        return "contacted"
    if cities.metro_of_listing(message.listing) is None:
        return "outside_cities"
    return ""


# ---------------------------------------------------------------- the link was opened


def token_of(src: str) -> str:
    return src[len(SOURCE_PREFIX):] if (src or "").startswith(SOURCE_PREFIX) else ""


def same_person(message: OutreachMessage, user) -> bool:
    if message.recipient_telegram_id and message.recipient_telegram_id == user.telegram_user_id:
        return True
    username = (user.telegram_username or "").strip().lower()
    return bool(username) and username == message.recipient_username.lower()


def note_open(src: str, user, now: datetime | None = None) -> OutreachMessage | None:
    """Called when a person signs in through a link. Someone else's click on a forwarded link counts for nothing."""
    from home.models import HomeUserPrefs

    token = token_of(src)
    if not token:
        return None
    now = now or timezone.now()
    message = OutreachMessage.objects.filter(token=token).first()
    if message is None or not same_person(message, user):
        return None
    if message.opened_at is None:
        message.opened_at = now
        message.opened_by = user
        message.member_at = message.member_at or now
        message.save(update_fields=["opened_at", "opened_by", "member_at", "updated_at"])
        capture_user("outreach_opened", user, source="server", side="housing", properties={"message_id": message.pk, "kind": message.kind, "variant": message.variant, "city": message.city, "account": message.account.name if message.account else ""})
    if message.city:
        # A city chosen earlier stays: the link wins this visit only.
        prefs, _created = HomeUserPrefs.objects.get_or_create(user=user)
        if not prefs.city:
            prefs.country = message.country
            prefs.city = message.city
            prefs.save(update_fields=["country", "city", "updated_at"])
    return message


def mark_bot_start(telegram_id: int, now: datetime | None = None) -> int:
    """They started the housing bot: from now on the bot can write to them for free."""
    now = now or timezone.now()
    rows = OutreachMessage.objects.filter(recipient_telegram_id=telegram_id, status=OutreachStatus.SENT, bot_started_at__isnull=True)
    count = rows.update(bot_started_at=now, updated_at=now)
    OutreachMessage.objects.filter(recipient_telegram_id=telegram_id, status=OutreachStatus.SENT, member_at__isnull=True).update(member_at=now, updated_at=now)
    return count


def mark_active(telegram_id: int, now: datetime | None = None) -> None:
    now = now or timezone.now()
    OutreachMessage.objects.filter(Q(recipient_telegram_id=telegram_id), status=OutreachStatus.SENT, active_at__isnull=True).update(active_at=now, updated_at=now)
