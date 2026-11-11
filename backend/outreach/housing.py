"""Housing outreach: who gets which first message, and what it says now.

Everything about a person comes from Telegram's poster fields, read through `home.ownership` and `poster_contact`.
A person has no User here until they sign in. One message per person, on their newest ad in a launch city.
"""

from __future__ import annotations

import difflib
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from home import alert_store, conf as home_conf, deeplink
from home.alerts import alert_from_wanted, search
from home.models import HomeAlert, HomeChannel, HomeListing, HomePost, ListingKind, ListingStatus, ListerType, PostStatus, PropertyType, RentalMode
from home.ownership import APP_PREFIX, owner_telegram_id
from market.poster import poster_contact
from outreach import cities, conf, templates
from outreach.models import OutreachAccount, OutreachKind, OutreachMessage, OutreachStatus
from outreach.services import AWAITING_YES, already_contacted, is_opted_out, new_message_status, new_token
from telemetry import capture_user
from users.models import User

SOURCE_PREFIX = "o-"
REPOSTER_POSTS = 5
REPOSTER_DAYS = 14
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


def priced_fits(found: list[HomeListing], budget) -> list[HomeListing]:
    """Matches we may count and name to them: a known rent at or under their budget (audit 2026-11-08, #138: an ad
    with no price was named as fitting a 500 EUR budget)."""
    if not budget:
        return []
    top = float(budget)
    return [item for item in found if item.price_amount and float(item.price_amount) <= top]


def teaser_pick(fits: list[HomeListing]) -> HomeListing | None:
    """The ad named in S1: priced, and in a known neighbourhood of the city (a town outside it has no area row)."""
    return next((item for item in fits if item.home_area_id), None)


def city_has_offers(channel: HomeChannel) -> bool:
    probe = HomeAlert(country=channel.country, city=channel.city, answered=["budget", "type", "gender", "rooms", "registration", "term", "area"], complete=True)
    return _any_match(probe)


def matches_any(wanted: HomeListing, channel: HomeChannel | None) -> bool:
    """Any live offer for their area and type, whatever the price: S2's «nothing fits» is checked against this."""
    if channel is None:
        return False
    data = alert_from_wanted(wanted, channel.city, telegram_id=0)
    data["max_price"] = None
    # What their post never said (budget, gender, rooms, registration) does not rule an offer out here: S2 says
    # «nothing fits», so anything in their area and type counts (#309: male-only and priceless rooms were excluded).
    data["answered"] = sorted(set(data.get("answered", [])) | {"budget", "type", "gender", "rooms", "registration", "term"})
    return _any_match(HomeAlert(**data))


def _any_match(alert: HomeAlert) -> bool:
    """Any shown offer matches, looking through all of them: `search(limit=1)` looked at the country's newest offer only
    (audit 2026-11-09, #309: S2 «nothing fits» in Hellersdorf while seven offers there were live)."""
    from home.alerts import SEARCH_DAYS, matches, metro_channel, shown_offers

    channel = metro_channel(alert.country, alert.city)
    rows = shown_offers().filter(country=alert.country, last_seen__gte=timezone.now() - timedelta(days=SEARCH_DAYS)).order_by("-last_seen", "-id")
    return any(matches(alert, listing, channel) for listing in rows.iterator(chunk_size=200))


def _plan_seeker(person: Person) -> dict[str, Plan]:
    """S1 when their budget has matches; S1B (asks the budget) when anything fits their area and type; S2 only when
    nothing does (audit 2026-11-08: S2 told someone «nothing fits» while rooms in his areas were live)."""
    plans: dict[str, Plan] = {}
    for listing, channel in person.listings:
        if listing.kind != ListingKind.WANTED:
            continue
        budget = budget_of(listing)
        if budget:
            found = priced_fits(matches_for(listing, channel), budget)
            if len(found) >= templates.MIN_COUNT and teaser_pick(found) is not None:
                plans.setdefault(OutreachKind.S1, Plan(OutreachKind.S1, listing, channel, {"budget": str(budget), "currency": listing.price_currency, "count": len(found)}))
                continue
        if matches_any(listing, channel) or (not budget and city_has_offers(channel)):
            plans.setdefault(OutreachKind.S1B, Plan(OutreachKind.S1B, listing, channel, {}))
            continue
        plans.setdefault(OutreachKind.S2, Plan(OutreachKind.S2, listing, channel, {}))
    return plans


def _reposter(telegram_id: int, now: datetime, listing: HomeListing | None = None) -> bool:
    """5 or more posts of THIS ad in 14 days (OUTREACH-HOOKS.md): the same text, or a listing merged into this one.
    Other ads of the same person do not count (audit 2026-11-08)."""
    if listing is None:
        return False
    same_ad = Q(listing__canonical=listing) | Q(listing=listing)
    if listing.post.text_key:
        same_ad |= Q(text_key=listing.post.text_key)
    recent = HomePost.objects.filter(poster_user_id=telegram_id, posted_at__gte=now - timedelta(days=REPOSTER_DAYS))
    return recent.filter(same_ad).distinct().count() >= REPOSTER_POSTS


def reposter_now(message: OutreachMessage, now: datetime) -> bool:
    """Checked again at send time: a row planned under the old rule is not trusted."""
    listing = message.listing
    return bool(listing is not None and message.recipient_telegram_id and _reposter(message.recipient_telegram_id, now, listing))


def _plan_poster(person: Person, now: datetime) -> dict[str, Plan]:
    plans: dict[str, Plan] = {}
    offers = [(listing, channel) for listing, channel in person.listings if listing.kind == ListingKind.OFFER]
    owner = next(((listing, channel) for listing, channel in offers if not _is_agent(listing)), None)
    if owner is not None:
        plans[OutreachKind.P1] = Plan(OutreachKind.P1, *owner, {"reposter": _reposter(person.telegram_id, now, owner[0])})
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


# Separate municipalities posted under a metro channel: named by the town, not the metro (audit 2026-11-09, rule 39).
TOWNS = (
    ("north vancouver", "نورث ونکوور"),
    ("west vancouver", "وست ونکوور"),
    ("new westminster", "نیو وستمینستر"),
    ("port moody", "پورت مودی"),
    ("burnaby", "برنابی"),
    ("coquitlam", "کوکیتلام"),
    ("surrey", "ساری"),
    ("richmond hill", "ریچموند هیل"),
    ("richmond", "ریچموند"),
    ("mississauga", "میسیساگا"),
    ("markham", "مارکام"),
    ("vaughan", "وان"),
    ("thornhill", "تورنهیل"),
    ("aurora", "آرورا"),
    ("newmarket", "نیومارکت"),
    ("oakville", "اوکویل"),
    ("brampton", "برامپتون"),
    ("laval", "لاوال"),
    # Montreal's south and north shores (audit 2026-11-10 #324, rule 50): a borough of Longueuil by its own name.
    ("saint-hubert", "سنت هوبرت"),
    ("saint hubert", "سنت هوبرت"),
    ("st-hubert", "سنت هوبرت"),
    ("st hubert", "سنت هوبرت"),
    ("greenfield park", "گرینفیلد پارک"),
    ("vieux-longueuil", "لانگوی"),
    ("vieux longueuil", "لانگوی"),
    ("saint-lambert", "سنت لمبرت"),
    ("saint lambert", "سنت لمبرت"),
    ("st-lambert", "سنت لمبرت"),
    ("boucherville", "بوشرویل"),
    ("terrebonne", "تربن"),
    ("longueuil", "لانگوی"),
    ("brossard", "بروسارد"),
    ("potsdam", "پوتسدام"),
)


# A neighbourhood is named by its municipality, only inside its own metro: Brentwood is also in Calgary
# (audit 2026-11-10 #327, rule 51: «Burquitlam» is Coquitlam).
NEIGHBOURHOODS = {
    "Vancouver": (
        ("burquitlam", "کوکیتلام"),
        ("austin heights", "کوکیتلام"),
        ("maillardville", "کوکیتلام"),
        ("lonsdale", "نورث ونکوور"),
        ("lynn valley", "نورث ونکوور"),
        ("metrotown", "برنابی"),
        ("brentwood", "برنابی"),
        ("edmonds", "برنابی"),
        ("steveston", "ریچموند"),
        ("guildford", "ساری"),
        ("whalley", "ساری"),
        ("fleetwood", "ساری"),
    ),
    # Brandenburg towns on Berlin's edge are their own places (audit 2026-11-10 #331, rule 53: Eichwalde).
    "Berlin": (
        ("eichwalde", "آیشوالده"),
        ("schönefeld", "شونفلد"),
        ("schoenefeld", "شونفلد"),
        ("zeuthen", "تسویتن"),
        ("falkensee", "فالکنزه"),
        ("teltow", "تلتو"),
        ("kleinmachnow", "کلاین‌ماخنو"),
        ("stahnsdorf", "اشتانسدورف"),
        ("bernau", "برناو"),
        ("hennigsdorf", "هنیگزدورف"),
        ("oranienburg", "اورانینبورگ"),
        ("königs wusterhausen", "کونیگس ووسترهاوزن"),
        ("koenigs wusterhausen", "کونیگس ووسترهاوزن"),
        ("blankenfelde", "بلانکنفلده"),
        ("ludwigsfelde", "لودویگسفلده"),
        ("erkner", "ارکنر"),
        ("hoppegarten", "هوپه‌گارتن"),
        ("wildau", "ویلداو"),
        ("panketal", "پانکه‌تال"),
        ("neuenhagen", "نوین‌هاگن"),
    ),
}


def town_of(listing: HomeListing | None) -> str:
    """The Persian name of a separate town the ad's area starts with, or "": a metro town (TOWNS), or a known city
    outside the metro the group filed it under («Offenbach Bieber» in the Cologne group, audit 2026-11-09 #310)."""
    from home.contact_text import unglue_postcode

    area = _spelled(unglue_postcode((listing.area if listing is not None else "") or "").strip().casefold())
    found = next((fa for latin, fa in TOWNS if area.startswith(latin)), "")
    if not found and listing is not None:
        hoods = NEIGHBOURHOODS.get(listing.city or "", ())
        # The whole word: «Teltower Damm» is a street in Zehlendorf, not Teltow.
        found = next((fa for latin, fa in hoods if re.match(rf"{re.escape(latin)}(?![a-zäöüß])", area)), "")
    if found or listing is None:
        return found
    from home.areas import other_town
    from home.city_names import city_fa

    channel = cities.metro_of_listing(listing)
    town = other_town(area, channel) if channel is not None else ""
    return city_fa(town) if town else (post_town(listing) or home_area_town(listing))


def post_town(listing: HomeListing | None) -> str:
    """The one metro town the post itself names, when the resolved home area is another place the post never names:
    the post's word beats an alias match (audit 2026-11-11 #216: «هایلند نورث ونکوور» was stored as «Highland North»,
    an alias of Burnaby). "" when the post names no town, several, or the home area's own name too."""
    if listing is None or not listing.post_id:
        return ""
    text = (listing.post.text or "").replace("ي", "ی").replace("ك", "ک").replace("‌", " ").casefold()
    text = re.sub(r"[_#]", " ", text)
    named = []
    for latin, fa in sorted(TOWNS, key=lambda item: -len(item[0])):
        for word in (latin, fa):
            if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", text):
                named.append((latin, fa))
                text = re.sub(rf"(?<![\w]){re.escape(word)}(?![\w])", " ", text)
    towns = {fa for _latin, fa in named}
    if len(towns) != 1:
        return ""
    town = towns.pop()
    area = getattr(listing, "home_area", None) if listing.home_area_id else None
    if area is None or area.fa == town:
        return ""
    own = [(area.fa or "").casefold(), (area.canonical or "").casefold()]
    if any(name and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text) for name in own):
        return ""
    return town


# Boroughs of the metro's own city: a home area by these names is a part of the city, not a separate town.
CITY_DISTRICTS = {"north york", "etobicoke", "scarborough", "east york", "york", "downtown", "midtown", "old toronto"}


def home_area_town(listing: HomeListing | None) -> str:
    """The Persian name of the listing's resolved home area when that area is a separate municipality («upper canada mall»
    resolves to Newmarket, «King City» to King City; audit 2026-11-10 20:15 #338/#339), else ""."""
    area = getattr(listing, "home_area", None) if listing is not None and listing.home_area_id else None
    if area is None or not area.fa:
        return ""
    from home.areas import fold
    from home.city_names import _NAMES

    slug = fold(area.canonical or "")
    if not slug or slug in CITY_DISTRICTS or slug == fold(listing.city or ""):
        return ""
    towns = {latin for latin, _fa in TOWNS} | {fold(name) for name in _NAMES}
    return area.fa if slug in towns else ""


def _spelled(area: str) -> str:
    """The area with a misspelled town at its start corrected: «kezwick» is Keswick, «mississuaga» Mississauga
    (audit 2026-11-10 #319). Only words of 6 letters or more, and only a close match."""
    from home.areas import fold
    from home.city_names import _NAMES

    vocabulary = {latin for latin, _fa in TOWNS} | {fold(name) for name in _NAMES}
    words = area.split()
    for size in range(min(3, len(words)), 0, -1):
        head = " ".join(words[:size])
        if len(head) < 6 or head in vocabulary:
            continue
        match = difflib.get_close_matches(head, vocabulary, n=1, cutoff=0.85)
        if match:
            return " ".join([match[0], *words[size:]])
    return area


def area_unresolved(message: OutreachMessage) -> bool:
    """A Berlin first message whose ad names an area that is neither a Berlin district nor a known town around it
    («Adalbertstraße10179»): held for a person, never sent as «تو برلین» (audit 2026-11-10 #331, rule 53)."""
    listing = message.listing
    if message.kind not in (OutreachKind.P1, OutreachKind.P1Q) or listing is None:
        return False
    if (listing.country, listing.city) != ("DE", "Berlin") or not (listing.area or "").strip():
        return False
    if any(10115 <= int(code) <= 14199 for code in re.findall(r"(?<!\d)\d{5}(?!\d)", listing.area)):
        # A Berlin postcode («Adalbertstraße10179») is Berlin proper: «تو برلین» is right (audit 2026-11-10 #277).
        return False
    return listing.home_area_id is None and not (message.extra or {}).get("town") and not town_of(listing)


UNCLEAR_SKIPS = ("unclear_post", "not_housing", "area_mismatch", "kind_mismatch")


def person_skip_reason(person: Person) -> str:
    if is_member(person.telegram_id, person.username):
        return "member"
    listing_ids = [listing.pk for listing, _channel in person.listings]
    if listing_ids and OutreachMessage.objects.filter(listing_id__in=listing_ids, status=OutreachStatus.SKIPPED, error_code__in=UNCLEAR_SKIPS).exists():
        # Read once as unclear or not housing: never queued again for the same ad (rule 41).
        return "unclear_before"
    if is_opted_out(username=person.username, telegram_user_id=person.telegram_id):
        return "opted_out"
    if already_contacted(username=person.username, telegram_user_id=person.telegram_id):
        return "contacted"
    return ""


def _p1_or_question(token: str, kinds: set[str]) -> str:
    """Half and half, by token, while both are on. They are compared on the same first people."""
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


def _refreshed_s1(message: OutreachMessage) -> list[HomeListing]:
    """Today's real matches. Fewer than three turn S1 into S1b: a number under three is never written."""
    channel = HomeChannel.objects.filter(country=message.country, city=message.city).first()
    found = matches_for(message.listing, channel) if channel is not None and message.listing is not None else []
    # The count and the named ad are priced fits only; the named ad sits in a known area of the city.
    found = priced_fits(found, message.extra.get("budget"))
    pick = teaser_pick(found)
    if pick is not None:
        found = [pick, *[item for item in found if item.pk != pick.pk]]
    if len(found) >= templates.MIN_COUNT and pick is not None:
        message.extra = {**message.extra, "count": len(found)}
    else:
        message.kind = OutreachKind.S1B
        message.extra = {}
    return found


def _place_q(country: str, city: str):
    from home.feed import city_q

    return city_q({"country": country, "city": city})


def week_offers(country: str, city: str, now: datetime) -> int:
    """Live offers in this city first seen in the last 7 days."""
    return (
        HomeListing.objects.filter(
            kind=ListingKind.OFFER, status=ListingStatus.ACTIVE, needs_review=False, canonical__isnull=True, country=country, first_seen__gte=now - timedelta(days=7)
        )
        .filter(_place_q(country, city))
        .count()
    )


def week_seekers(country: str, city: str, now: datetime) -> int:
    """Distinct people who posted a wanted ad in this city in the last 7 days."""
    return (
        HomeListing.objects.filter(kind=ListingKind.WANTED, country=country, first_seen__gte=now - timedelta(days=7), post__poster_user_id__isnull=False)
        .filter(_place_q(country, city))
        .exclude(flags__has_key="not_housing")
        .values("post__poster_user_id")
        .distinct()
        .count()
    )


SHARED_UNIT = ("هم‌اتاقی", "هم اتاقی", "هم‌اطاقی", "هم اطاقی", "هم‌خونه", "هم خونه", "هم‌خانه", "تقسیم می‌شود", "تقسیم میشه", "تقسیم میشود", "shared")


COUNT_WORDS = {1: "یک", 2: "دو", 3: "سه", 4: "چهار", 5: "پنج", 6: "شش"}
# A room let inside a unit: «اجاره اتاق مستر در بیسمنت», «یه اتاق تو آپارتمان». Never «۳ اتاق خواب» (bedrooms).
# The whole unit, or its rooms one by one («اجاره واحد دوخوابه یا اجاره اتاق بصورت مجزا», #320): the unit is the ad.
UNIT_OR_ROOMS = re.compile(r"یا\s+(?:اجاره\s+)?(?:اتاق|اطاق)|(?:اتاق|اطاق)(?:‌|\s)?ها\s+(?:هم\s+)?(?:جدا|جداگانه|بصورت|به\s*صورت)")
ROOM_IN_UNIT = re.compile(r"(?:اجاره|یک|یه)\s+(?:اتاق|اطاق)(?!\s*(?:خواب|خوابه))|(?:اتاق|اطاق)\s+(?:مستر|master)|(?:اتاق|اطاق)\s+(?:در|تو|توی)\s")


# A bed let on its own (audit 2026-11-10 #332: «#اجاره تخت جهت اقامت یک یا ۲ شب»), not a bed as furniture.
BED_LET = re.compile(r"(?:اجاره|یک|یه)\s*_?\s*تخت(?!\s*(?:خواب|دونفره|دو نفره|یک نفره|یکنفره))|تخت\s+خالی")


# Rooms let one by one (audit 2026-11-10 19:15 #335): «اتاق‌های بیسمنت», «اجاره هر اتاق» / «هراتاق».
ROOMS_LET = re.compile(r"هر\s*(?:اتاق|اطاق)(?!\s*خواب)|(?:اتاق|اطاق)(?:‌|\s)?های\s+(?:بیسمنت|زیرزمین|basement)")
# A basement whose kitchen or bath is shared is let by the room.
SHARED_PART = re.compile(r"(?:آشپزخانه|آشپزخونه|حمام|حموم|سرویس|kitchen|bath(?:room)?)(?:\s+\S+){0,2}?\s+(?:مشترک|shared)|(?:مشترک|shared)\s+(?:kitchen|bath)")
# A bedroom count the post writes itself: «۲ اتاق خواب», «دو خوابه», «2 Schlafzimmer» (audit 2026-11-10 19:15 #336).
_NUM = r"(?P<n>\d|[۰-۹]|یک|دو|سه|چهار|پنج)"
BEDROOMS_WRITTEN = re.compile(rf"{_NUM}\s*(?:-\s*)?(?:اتاق\s*خواب|خوابه|schlafzimmer|bedrooms?)")
_NUM_WORDS = {"یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5}


def written_bedrooms(text: str) -> int | None:
    """The bedroom count the post states, or None."""
    found = BEDROOMS_WRITTEN.search(text or "")
    if not found:
        return None
    raw = found.group("n")
    return _NUM_WORDS.get(raw) or int(raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))


# Nights or weeks (rule 55) as opposed to a monthly or renewable let (audit 2026-11-10 19:15 #336).
NIGHTLY = re.compile(r"شب(?:ی|انه)?\b|\bشب\s|هفتگی|هفته(?:‌|\s)?ای|nightly|per night|/night|weekly|per week|pro nacht|pro woche")
MONTHLY = re.compile(r"ماهانه|ماهیانه|بلند\s*مدت|بلندمدت|تمدید|monthly|per month|/month|long[- ]?term|langfristig|monatlich|verlängerung")


def only_short_stays(listing: HomeListing) -> bool:
    """«برای اقامت کوتاه» only for an ad that offers nights or weeks and nothing longer. A weekly or nightly price counts
    as such an offer («1175$ هفتگي», #342); Arabic ي and ك are read as Persian ی and ک."""
    text = ((listing.post.text if listing.post_id else "") or "").casefold().replace("ي", "ی").replace("ك", "ک")
    if MONTHLY.search(text):
        return False
    return listing.price_period in ("week", "night") or bool(NIGHTLY.search(text))


# A place named only as part of a campus, station or landmark («پردیس برنابی», «SFU Burnaby») is not where the ad is
# (audit 2026-11-10 19:15 #335, rules 24/51).
LANDMARK = re.compile(
    r"(?:پردیس|ایستگاه|دانشگاه|کالج|فرودگاه|بیمارستان|مرکز\s*خرید|campus|station|university|college|airport|hospital|mall|bcit|sfu|ubc)"
)


def town_only_landmark(listing: HomeListing | None) -> bool:
    """True when the ad's area or town shows up in the post, and every time only next to a landmark word."""
    if listing is None or not listing.post_id or not listing.area:
        return False
    from home.city_names import city_fa

    text = (listing.post.text or "").casefold()
    names = {name for name in (listing.area.strip().casefold(), town_of(listing), city_fa(listing.area.strip())) if name and len(name) >= 3}
    spans = [match.span() for name in names for match in re.finditer(re.escape(name.casefold()), text)]
    if not spans:
        return False
    return all(LANDMARK.search(text[max(0, start - 25) : start]) or LANDMARK.match(text[end : end + 12].strip()) for start, end in spans)


def _type_word(listing: HomeListing) -> str:
    """A shared unit, or a room let inside one, is a room to the person who rents it (audit 2026-11-09, rules 39/43)."""
    text = ((listing.post.text if listing.post_id else "") or "").casefold()
    if listing.property_type == PropertyType.ROOM and BED_LET.search(text):
        return "تخت"
    whole_unit = listing.property_type in {PropertyType.APARTMENT, PropertyType.HOUSE} and bool(UNIT_OR_ROOMS.search(text))
    if not whole_unit and (any(word in text for word in SHARED_UNIT) or ROOM_IN_UNIT.search(text) or ROOMS_LET.search(text)):
        return "اتاق"
    if listing.property_type == PropertyType.BASEMENT and SHARED_PART.search(text):
        return "اتاق"
    kind = listing.property_type
    if kind in {PropertyType.APARTMENT, PropertyType.HOUSE} and listing.rooms:
        rooms = listing.rooms
        number = int(rooms) if rooms == int(rooms) else rooms
        bedrooms = written_bedrooms(text) if listing.country == "DE" else None
        if bedrooms:
            # The post counts bedrooms itself: «آپارتمان ۲ خوابه», not «دو اتاقه» (2 Zimmer is one bedroom).
            unit = "آپارتمان" if kind == PropertyType.APARTMENT else "خونه‌ی"
            return f"{unit} {templates.fa_number(bedrooms)} خوابه"
        if listing.country == "DE":
            # In Germany rooms are Zimmer (bedrooms and the living room): «آپارتمان دو اتاقه», never «۲ خوابه» (rule 46).
            unit = "آپارتمان" if kind == PropertyType.APARTMENT else "خونه‌ی"
            return f"{unit} {COUNT_WORDS.get(number, templates.fa_number(number))} اتاقه"
        return f"{templates.fa_number(number)} خوابه"
    if kind == PropertyType.BASEMENT and listing.rooms and listing.country != "DE":
        # «بیسمنت ۱ خوابه» (audit 2026-11-10 #319): the bedroom count is what a basement seeker filters by.
        rooms = listing.rooms
        return f"بیسمنت {templates.fa_number(int(rooms) if rooms == int(rooms) else rooms)} خوابه"
    return {PropertyType.ROOM: "اتاق", PropertyType.STUDIO: "استودیو", PropertyType.BASEMENT: "بیسمنت", PropertyType.HOUSE: "خونه", PropertyType.APARTMENT: "آپارتمان"}.get(kind, "")


PERIOD_WORDS = {"month": "", "week": "هفته‌ای ", "night": "شبی "}


def listing_price(listing: HomeListing) -> str:
    """The listing's price with its period: «۲۴۰۰ دلار» a month, «هفته‌ای ۱۱۷۵ دلار», «شبی ۸۰ دلار». A price whose period
    is not known is left out: a weekly amount must never read as monthly (audit 2026-11-10 22:15 #342, rule 60)."""
    if not listing.price_amount or (listing.price_period or "") not in PERIOD_WORDS:
        return ""
    return PERIOD_WORDS[listing.price_period] + templates.money(_number(listing.price_amount), listing.price_currency)


def _price(listing: HomeListing) -> str:
    return listing_price(listing)


def teaser(listing: HomeListing) -> str:
    """«۳ خوابه تو پلاتو، ۱۱۵۰ دلار، از ۱ دسامبر خالیه»: only the fields the ad has."""
    from home.publish import place_fa, short_date

    head = " ".join(part for part in (_type_word(listing), f"تو {place_fa(listing.area)}" if listing.area else "") if part)
    upcoming = listing.available_from and listing.available_from > timezone.localdate()
    parts = [head, _price(listing), f"از {short_date(listing.available_from)} خالیه" if upcoming else ""]
    return "، ".join(part for part in parts if part)


def ad_desc(listing: HomeListing) -> str:
    """«۲ خوابه‌تون (۲۴۰۰ دلار)»; «خونه‌تون» when the type is unknown."""
    word = _type_word(listing)
    desc = f"{word}‌تون" if word else "خونه‌تون"
    if listing.rental_mode == RentalMode.SHORT_TERM and only_short_stays(listing):
        # «آگهی ۲ خوابه‌تون برای اقامت کوتاه (هفته‌ای ۱۱۷۵ دلار)» (#342): the demand text names a short stay too.
        desc = f"{desc} برای اقامت کوتاه"
    price = _price(listing)
    return f"{desc} ({price})" if price else desc


def p1_variant(message: OutreachMessage, now: datetime) -> tuple[str, int]:
    """Repost, then one gap (photo first, then price), then demand when 3 or more looked this week; else P1q."""
    listing = message.listing
    if listing is None:
        return "", 0
    if reposter_now(message, now):
        return "repost", 0
    if not (listing.post.has_photo or listing.post.photo_count):
        return "photo", 0
    if not listing.price_amount:
        return "price", 0
    seekers = week_seekers(message.country, message.city, now)
    return ("demand", seekers) if seekers >= templates.MIN_COUNT else ("", 0)


def render(message: OutreachMessage, now: datetime, *, first: bool = False) -> str:
    """The text as it goes out, with today's numbers. Changes kind and variant on the row when the facts changed."""
    found: list[HomeListing] = []
    if message.kind == OutreachKind.S1:
        found = _refreshed_s1(message)
    name = templates.valid_name(message.recipient_name, message.recipient_username)
    city = message.city
    if message.kind == OutreachKind.P1:
        variant, seekers = p1_variant(message, now)
        if variant == "repost":
            text, code = templates.p1_repost(name, city)
        elif variant in {"photo", "price"}:
            text, code = templates.p1_gap(name, city, gap=variant)
        elif variant == "demand":
            text, code = templates.p1_demand(name, city, ad_desc=ad_desc(message.listing), seekers_week=seekers)
        else:
            message.kind = OutreachKind.P1Q
        if message.kind == OutreachKind.P1:
            message.variant = code
            if variant in {"repost", "photo", "price"}:
                # The gap and repost texts name the ad by its type, like P1-demand (rule 39): «اتاق‌تون», «۲ خوابه‌تون».
                word = _type_word(message.listing)
                if word:
                    text = text.replace("آگهی خونه‌تون", f"آگهی {word}‌تون", 1)
                if word and message.listing.rental_mode == RentalMode.SHORT_TERM and only_short_stays(message.listing):
                    text = text.replace(f"آگهی {word}‌تون", f"آگهی {word}‌تون برای اقامت کوتاه", 1)
            town = (message.extra or {}).get("town") or ("" if town_only_landmark(message.listing) else town_of(message.listing))
            own_city = message.listing.city or ""
            if not town and own_city and own_city != city:
                # The listing's own city wins over the group's (7137: Richmond Hill, not Toronto; audit 20:15 #339).
                from home.city_names import city_fa as known_city_fa

                named = known_city_fa(own_city)
                town = named if named and named != own_city else ""
            if town:
                # A town outside the stored city is named by the town (rule 39).
                text = text.replace(f"تو {templates.city_fa(city)}", f"تو {town}", 1)
            return templates.no_emoji(text)
    rng = random.Random(message.token)
    if message.kind == OutreachKind.S1:
        text, variant = templates.s1(
            name, city, budget=_number(message.extra.get("budget")), currency=message.extra.get("currency") or "", count=len(found), teaser=teaser(found[0])
        )
    elif message.kind == OutreachKind.S1B:
        text, variant = templates.s1b(name, city, week_count=week_offers(message.country, city, now))
    elif message.kind == OutreachKind.S2 and message.listing is not None and matches_any(
        message.listing, HomeChannel.objects.filter(country=message.country, city=message.city).first()
    ):
        # «Nothing fits» is checked at send time: something does now, so ask the budget instead.
        message.kind = OutreachKind.S1B
        text, variant = templates.s1b(name, city, week_count=week_offers(message.country, city, now))
    elif message.kind == OutreachKind.S2:
        text, variant = templates.s2(name, city)
    elif message.kind == OutreachKind.P1Q:
        text, variant = templates.p1q(rng, name, city, reposter=reposter_now(message, now))
    elif message.kind == OutreachKind.P6:
        text, variant = templates.p6(rng, name, city, count=message.extra.get("count") or 1, url=link_for(message))
    else:
        return message.text
    message.variant = variant
    return templates.no_emoji(text)


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
    from home.abroad import listing_abroad

    if listing_abroad(message.listing):
        return "not_target_city"
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


def owner_opened(listing: HomeListing, user, now: datetime | None = None) -> None:
    """The owner opened their own ad in the Mini App: it is claimed («تأیید صاحب آگهی») and they count as active."""
    now = now or timezone.now()
    if listing.owner_claimed_at is None:
        HomeListing.objects.filter(pk=listing.pk, owner_claimed_at__isnull=True).update(owner_claimed_at=now)
        listing.owner_claimed_at = now
    mark_active(user.telegram_user_id, now)


def landing(src: str, user) -> dict:
    """What the Mini App shows after an outreach link: for a seeker, their ready alert, off until one tap."""
    from home import alert_store
    from home.alerts import alert_from_wanted

    empty = {"is_recipient": False, "kind": "", "listing_id": None, "country": "", "city": "", "alert": None, "bot_url": home_conf.bot_start_link(""), "ask_write_access": False}
    token = token_of(src)
    message = OutreachMessage.objects.select_related("listing", "listing__post").filter(token=token).first() if token else None
    if message is None or not same_person(message, user):
        return empty
    out = {
        **empty,
        "is_recipient": True,
        "kind": message.kind,
        "listing_id": message.listing_id,
        "country": message.country,
        "city": message.city,
        "bot_url": home_conf.bot_start_link(f"o{message.token}"),
        "ask_write_access": message.bot_started_at is None and not alert_store.bot_open(user.telegram_user_id),
    }
    wanted = message.listing if message.listing is not None and message.listing.kind == ListingKind.WANTED else None
    if wanted is not None and message.kind in SEEKER_KINDS:
        alert = HomeAlert.objects.filter(wanted_listing=wanted).first()
        if alert is None:
            fields = alert_from_wanted(wanted, message.city or wanted.city, telegram_id=user.telegram_user_id)
            alert = HomeAlert.objects.create(**fields, user=user, active=False)
        out["alert"] = {
            "id": alert.pk,
            "active": alert.active,
            "city": alert.city,
            "max_price": float(alert.max_price) if alert.max_price is not None else None,
            "currency": alert.currency,
            "match_count": len(search(alert)),
        }
    return out


def confirm_alert(alert_id: int, user) -> HomeAlert | None:
    """The one tap: the alert made from their own request goes on."""
    alert = HomeAlert.objects.filter(pk=alert_id, telegram_user_id=user.telegram_user_id).first()
    if alert is None:
        return None
    if not alert.active or alert.user_id != user.pk:
        alert.active = True
        alert.user = user
        alert.save(update_fields=["active", "user"])
    mark_active(user.telegram_user_id)
    return alert


def write_access_given(user) -> int:
    """requestWriteAccess said yes: the bot may write to them now, free, like a bot start."""
    return mark_bot_start(user.telegram_user_id)
