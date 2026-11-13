"""AI review of one housing post: is it a real listing, and what does it say.

One call decides both. The model sees the raw post and the source's defaults and
returns JSON. Everything it returns is checked here before it reaches the
database: enums, dates, money ranges, and the country/currency pair. A value the
checks cannot trust puts the listing in the review queue instead of publishing
it with a guess. Exact reposts are caught first, without a model call.

With `review_gate_on`, reposts and posts with no housing word are settled the same way,
as soon as they are read. Off, the gate code below is never reached.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from django.db import transaction
from django.db.models import Max, Min, Q
from django.utils import timezone

from ai.llm import llm_enabled
from home.city_names import clean_city
from home.contact_text import _ANNOUNCED
from home.contact_text import FA_DIGITS as _FA_DIGITS
from home.contact_text import HANDLE as _HANDLE
from home.contact_text import INTL as _INTL
from home.contact_text import LINK as _LINK
from home.contact_text import LOCAL_PHONE as _LOCAL_PHONE
from home.contact_text import NANP as _NANP
from home.contact_text import clean_place, handle_in_text, redact_contact, unglue_postcode
from home.images import same_photo
from home.models import (
    Furnished,
    HomeListing,
    HomeMedia,
    HomePost,
    ListerType,
    ListingKind,
    ListingStatus,
    REVIVED_FLAG,
    MediaStatus,
    PostStatus,
    PriceBasis,
    PricePeriod,
    PropertyType,
    RentalMode,
    RoommateGender,
    Tri,
)

logger = logging.getLogger(__name__)

# A verdict lands only on a post still waiting for one, so a run never overwrites what another run settled.
_WAITING = (PostStatus.NEW, PostStatus.ERROR)
# Reviews written before rental_mode existed put the mode in property_type.
LEGACY_MODES = {"sublet": RentalMode.SUBLET, "lease_transfer": RentalMode.LEASE_TRANSFER, "short_stay": RentalMode.SHORT_TERM}
REJECT_REASONS = {"ad", "noise", "not_housing", "sale", "incomplete", "expired", "abroad", "repost", "other"}
BOOL_FLAGS = (
    "untermiete",
    "wg",
    "wgb",
    "pets_ok",
    "students_only",
    "couples_ok",
    "temporary",
    "first_last",
    "damage_deposit",
    "credit_check",
    "parking",
    "no_smoking",
)
MIN_TEXT_CHARS = 40
# A post shorter than MIN_TEXT_CHARS still reaches the model when it has a housing word («2 bed & 1 bath in Burquitlam»,
# «نیاز به خانه دارم دو اتاق»): the photos usually carry the rest. Below this it is noise whatever it says.
MIN_HOUSING_TEXT_CHARS = 20
TEXT_LIMIT = 4000
REVIEW_CONFIDENCE_MIN = 0.7
REPOST_WINDOW_DAYS = 45
REPEAT_POSTER_TOTAL = 15
REPEAT_POSTER_LISTINGS = 5
SQFT_TO_M2 = Decimal("0.092903")
# Plausible monthly asking prices per currency (offer posts). Outside = review queue.
MONTHLY_RANGE = {
    "EUR": (80, 8000),
    "CAD": (200, 12000),
    "USD": (200, 12000),
}
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)

COUNTRY_NOTES = {
    "DE": (
        "Germany notes: Warmmiete / گرم / all inclusive = price_basis warm; Kaltmiete / سرد = cold. "
        "کاتسیون / Kaution is the deposit, often 2 or 3 Monatsmieten (deposit_months). "
        "ملد / ملده / Anmeldung possible = registration yes; بدون ملد = no. "
        "Untermiete = rental_mode sublet (flag untermiete); WG / وگ = shared flat, property_type room (flag wg); "
        "WGB = Wohnberechtigungsschein (flag wgb). Nachmieter = rental_mode lease_transfer. "
        "Zwischenmiete / زوییشن‌میته = rental_mode sublet (flag temporary). «1-Zimmer» / «1-Zi» = studio. "
        "A commission (Provision / کمیسیون) is not the deposit. Prices are monthly euro unless stated."
    ),
    "CA": (
        "Canada notes: a bare number or $ means Canadian dollars per month. «همه‌چیز شامل», all inclusive, "
        "utilities included = bills_included yes; «+ utilities» / «بدون قبوض» = no. "
        "بیسمنت / basement = property_type basement. «لیز ترنسفر» / «انتقال لیز» / lease transfer = rental_mode lease_transfer. "
        "«سابلت» / sublet = rental_mode sublet. کاندو / condo = apartment, تاون‌هاوس / townhouse = house. "
        "«۱ خوابه» = one bedroom (rooms 1); «1 Bedroom plus Den» = rooms 1; استودیو / bachelor / studio = studio. "
        "«۴½» style sizes are Quebec room counts: rooms = bedrooms is not guaranteed, leave rooms empty. "
        "Area in sqft / فوت مربع: fill size_sqft (never convert yourself). "
        "Deposit wording: «ماه اول و آخر» = first_last true; «ودیعه خسارت» / damage deposit = damage_deposit true; "
        "credit check / رفرنس / «نامه کار» = credit_check true. "
        "«فقط آقا» / «فقط خانم» restricts roommate_gender for a room or shared unit. "
        "Contact is usually a phone number (416, 647, 438, 514, 604 and so on); copy it as written. "
        "Realtors, brokers (مشاور املاک، بروکر) and lenders are lister_type agent or an ad. "
        "Municipality in city: Toronto, North York, Scarborough, Etobicoke, Richmond Hill, Thornhill, Markham, Vaughan, "
        "Mississauga, Montreal, Laval, Brossard. Use the province code in region (ON, QC, BC, AB)."
    ),
}

SYSTEM_PROMPT = """You review Telegram posts for Chamedoon Home, a Persian-language housing board for Iranians living abroad.
Decide if the post is ONE real housing post, then fill every field from the post itself. Posts are Persian, German, English, French or a mix.

Accept only:
- offer: someone offers a room, flat, house or basement for rent, long term, as a sublet, a lease transfer or a short stay.
  A realtor, broker, agency or leasing office offering one specific unit, or a few specific units, with a place (area, intersection,
  street or address) and a price, size or bedroom count IS an offer: accept it with lister_type "agent". It is not an ad.
  Someone who HAS a room and looks for a flatmate to fill it is an offer, not wanted: «جویای هم‌خانه», «هم‌خونه می‌خوام»,
  «دنبال هم‌خونه هستم» with a room described, «اتاق آماده اجاره است», «Nachmieter gesucht», «Mitbewohner gesucht» are offers.
- wanted: someone looks for a place to live, in any form: a request, a question («کسی سراغ داره؟», «WG نمی‌شناسید؟»),
  a short message, or with a reward or commission offered for help. Decide by who has the place: if the poster describes a
  room or home they have, it is an offer even when the first line says they are looking for someone.
Reject (accept=false) and give reject_reason:
- ad: promotion with no specific unit to rent: an agency or realtor advertising its services or "all kinds of rentals in the area",
  mortgage/loan/broker pitches, services, moving, cleaning, renovation, flight tickets, jobs, shops, currency exchange.
- noise: greetings, welcome messages, questions about laws, chatter, rules, join links, pinned posts, news.
- sale: a home, flat, condo, house or land offered for sale or wanted to buy (فروش ملک، خرید خانه, for sale, zu verkaufen, à vendre).
  Not shown to the public for now; it is kept for the admins.
- not_housing: anything else not about renting or sharing a home: business premises (salon, restaurant, shop, office)
  and furniture for sale.
- incomplete: housing is mentioned but nothing usable: the post names no city and no area AND the source has no default city.
  A post that names a city or an area, or comes from a single-city board, is never incomplete.
- expired: the post itself says the place is taken or the date has passed relative to Posted at.
- abroad: an offer for a place outside the source default country: a flat in Dubai, Istanbul, Yerevan or Tehran posted in a
  Canadian or German group. A wanted post from someone abroad who looks for a place in the source country is NOT abroad.
- other: none of the above. Never use other for a post that offers or looks for a place: that is offer or wanted.

Rules:
- Never guess. Use "" or null for anything the post does not say. Do not invent prices, dates, areas, phone numbers.
- country: ISO-2 of the place described. Use the source default when the post names no other country.
- city: English name of the city or municipality. Use the source default city only if the post names no city and the source is a single-city board. Never put the source default city on a place the post names abroad (Dubai Creek Harbour is Dubai, AE, never Vancouver). region: province or state code if known, else the source default.
- area: district, neighbourhood, intersection or street as written, Latin script if the post gives it.
- price_amount: the monthly asking price as a number, no symbol. If the post gives several prices, use the total the tenant pays per month and set price_basis. For wanted posts put the budget ceiling in price_max and leave price_amount null. If the price is per night or per week, keep that number and set price_period. A promotion average («یک ماه رایگان», «با احتساب ماه مجانی میانگین ...», "1 month free, effective") is not the rent: use the stated monthly rent.
- price_basis: "warm" if utilities are included, "cold" if utilities are extra, else "unknown".
- price_currency: ISO-4217. If the post shows a bare number, use the source default currency.
- deposit: deposit_amount in the same currency, or deposit_months if the post gives months. Never fill both from one phrase.
- registration: Anmeldung / address registration possible "yes", explicitly not possible "no", silent "unknown".
- property_type: WHAT the place is: room (a room in a shared home, a WG room), studio, apartment (any self-contained flat or condo, also «۱ خوابه», «۲ خوابه»: the bedroom count goes in rooms), house, basement, other. Never a rental mode.
- rental_mode: HOW it is let, separate from property_type: long_term (a normal lease, or the post says long term / «بلندمدت» / unbefristet), sublet (the tenant re-lets for a while: سابلت, Untermiete, Zwischenmiete), lease_transfer (the tenant hands over the lease: لیز ترنسفر, انتقال لیز, Nachmieter), short_term (nights or weeks: کوتاه‌مدت, اقامت موقت, per night), unknown (the post does not say). A «سابلت اتاق» is property_type room with rental_mode sublet.
- roommate_gender: female / male / couple only when the post restricts it, else "any". Never record nationality, religion or ethnicity.
- lister_type: "agent" for agencies, brokers and catalog posts; "owner" for a tenant or owner posting for themselves; for wanted posts "unknown".
- contact_telegram: a @username or t.me link written in the post's text, without @. Not the channel itself. Empty when the text has none: never guess one, and never use a person's name. contact_phone: a phone number written in the post, as written.
- rooms: the number the post writes, never computed. Bedrooms count as written («۲ خوابه», 2-bed, 2 bedrooms, 2BR = 2; 1 bedroom plus den = 1), in every country; a German «2-Zimmer» / «2 Zi» = 2. Never add a living room to a bedroom count: «سالن و اتاق خواب» / «پذیرایی و اتاق» is rooms 1 (سالن، پذیرایی، هال are never bedrooms). Leave it empty for a room in a shared home and for Quebec «۴½» sizes.
- size_m2 only when the post says square metres. size_sqft only when it says square feet.
- available_from / available_to: YYYY-MM-DD. Resolve relative dates («از اول ماه بعد», «ab 1.11.») against Posted at. If only a month is given use the 1st. A day and month with no year is the next one on or after Posted at, never a past date («January 1st» posted in November is next January).
- summary_fa: 1 or 2 plain Persian sentences with the facts a seeker needs (type, area, price, key conditions). No @handles, no URLs, no phone numbers, no greetings, no emojis.
- summary_en: the same facts in 1 or 2 plain English sentences, same rules.
- confidence: per field 0..1 for kind, property_type, rental_mode, city, price, available_from; and overall 0..1. Lower it when the post is vague or contradicts itself.
- flags: booleans only for keys you see in the post: untermiete, wg, wgb, pets_ok, students_only, couples_ok, temporary, first_last, damage_deposit, credit_check, parking, no_smoking. kaution_text: the deposit wording as written, max 80 chars.

Reply with JSON only, no markdown:
{
  "accept": true,
  "reject_reason": "",
  "kind": "offer",
  "property_type": "room",
  "rental_mode": "long_term",
  "country": "DE",
  "region": "",
  "city": "Berlin",
  "area": "Neukölln",
  "price_amount": 520,
  "price_max": null,
  "price_currency": "EUR",
  "price_period": "month",
  "price_basis": "warm",
  "deposit_amount": null,
  "deposit_months": 1,
  "size_m2": 16,
  "size_sqft": null,
  "rooms": 1,
  "furnished": "yes",
  "registration": "yes",
  "bills_included": "yes",
  "available_from": "2026-11-01",
  "available_to": null,
  "min_term_months": null,
  "roommate_gender": "female",
  "occupants_wanted": 1,
  "lister_type": "owner",
  "contact_telegram": "sara_b",
  "contact_phone": "",
  "summary_fa": "اتاق ۱۶ متری مبله در نویکلن برلین، ۵۲۰ یورو گرم، ملد ممکن است، از اول نوامبر.",
  "summary_en": "Furnished 16 m2 room in Neukolln, Berlin, 520 EUR warm, registration possible, from 1 November.",
  "flags": {"wg": true, "kaution_text": "1 Monatsmiete"},
  "confidence": {"kind": 0.97, "property_type": 0.95, "rental_mode": 0.8, "city": 0.99, "price": 0.9, "available_from": 0.8, "overall": 0.92}
}
For a reject, send only {"accept": false, "reject_reason": "..."}."""


@dataclass
class ReviewOutcome:
    accepted: bool
    listing: HomeListing | None = None
    reject_reason: str = ""
    error: str = ""
    model: str = ""
    raw: dict = field(default_factory=dict)


def text_key(text: str) -> str:
    """Stable fingerprint of a post's wording, for exact and near-exact reposts."""
    cleaned = (text or "").translate(_FA_DIGITS).lower()
    cleaned = re.sub(r"https?://\S+|@\w+", " ", cleaned)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:400]
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()


def build_prompt(post: HomePost) -> str:
    source = post.source
    posted = post.posted_at.date().isoformat() if post.posted_at else ""
    notes = COUNTRY_NOTES.get(source.country, "")
    return (
        f"Source: @{source.username} ({source.get_kind_display()})\n"
        f"Source default country: {source.country}\n"
        f"Source default city: {source.default_city or '(general board, no default city)'}\n"
        f"Source default region: {source.default_region or '(none)'}\n"
        f"Source default currency: {source.default_currency}\n"
        f"Source is an agency catalog: {'yes' if source.is_agency else 'no'}\n"
        f"Posted at: {posted}\n"
        f"{notes}\n\n"
        f"Post:\n{post.text[:TEXT_LIMIT]}"
    )


# The review gate. A post with none of these words, in any language the sources use, is not about housing.
GATE_RULE_NO_WORD = "no_housing_word"
# The model's changeable verdicts: replayed on real posts, they made 20 of the 22 cases where a later copy of a rejected post was accepted.
GATE_SECOND_LOOK_REASONS = ("noise", "incomplete", "other")
GATE_COPIES_COMPARED = 200
_COPIES_PAGE = 10
GATE_REQUEUE_LIMIT = 50
_LINK_HANDLE = re.compile(r"(?:(?:t\.me|telegram\.me|telegram\.dog|instagram\.com)/@?|[?&]domain=)([\w+\-]+)", re.I)
_LINK_PHONE = re.compile(r"\d{9,15}")
_GATE_CHARS = str.maketrans(
    {
        **{ord(persian): latin for persian, latin in zip("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")},
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        chr(0x200c): " ",
    }
)
_HOUSING_WORD = re.compile(
    r"اجار|کرایه|اطاق|سوییت|جای خواب|جا خواب|خوابگاه|هاستل|واحد|متری|طبقه|رهن|سابلت|ساب لت|لیز|لیس|واگذار|اتاق|سوئیت|سویت|"
    r"استودیو|استدیو|آپارتمان|اپارتمان|بیسمنت|زیرزمین|خوابه|(?:یک|یه|دو|سه|چهار|\d) ?خواب|هم ?خانه|همخونه|هم ?خونه|خانه|خونه|"
    r"منزل|مسکن|ویلا|کاندو|کندو|یونیت|\bوگه?\b|دنبال جا|دنبال یه جا|متراژ|مستاجر|مبله|فرنیش|یوتیلیتی|واشروم|لاندری|دیتچ|تاون ?هاوس|(?:متر|فوت) ?مربع|"
    r"ودیعه|وام|مله|ملد|انملدونگ|انمالدونگ|وی جی|دبلیو جی|موقت|کوتاه مدت|بلند مدت|بلندمدت|جا برای|محل اقامت|اسکان|نخ[وا]ابی|"
    r"تاونهاوس|دوپلکس|پنت هاوس|پارکینگ|"
    r"(?<![a-z])(?:rent|rooms?|bed|br|bd|bdrm|bdrs?|bsmt|bach|wg|unit|apt|flat|loyer|zi|whg|zkb|qm|warm|kalt|sq ?\.? ?ft)(?![a-z])|(?<![a-z])m²|(?<![a-z])m2(?![a-z0-9])|townhome|furnished|utilities|washroom|laundry|rental|basement|apartment|condo|studio|sublet|sublease|lease|bedroom|"
    r"tenant|landlord|roommate|housing|townhouse|"
    r"khune|khane|otagh|ejare|keraye|miete|wohnung|zimmer|wbs|untermiete|nachmieter|kaltmiete|warmmiete|anmeldung|kaution|"
    r"logement|appartement|colocation|chambre|suite|location|louer|meubl[eé]|m[öo]bliert|\d ?1/2|½",
    re.I,
)
# sha1 of nothing: the `text_key` of a post that is only links, handles or emoji. Those posts are not copies of each other.
EMPTY_KEY = hashlib.sha1(b"").hexdigest()


def housing_word(text: str) -> bool:
    return bool(_HOUSING_WORD.search((text or "").translate(_GATE_CHARS)))


def too_short(text: str) -> bool:
    """Too short to be a housing post: under MIN_TEXT_CHARS with no housing word, or under MIN_HOUSING_TEXT_CHARS at all."""
    cleaned = (text or "").strip()
    if len(cleaned) < MIN_HOUSING_TEXT_CHARS:
        return True
    return len(cleaned) < MIN_TEXT_CHARS and not housing_word(cleaned)


def _phone_digits(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def copy_facts(text: str, source_username: str = "") -> tuple[frozenset, frozenset, frozenset]:
    """Handles, phones and other numbers anywhere in a post, links included.

    `text_key` ignores handles and links and reads 400 characters, so two posts with one key can
    still name a different contact or a later price. A link can hold the contact, as in wa.me/<phone>
    or t.me/<handle>, so those are read before links are dropped. The source's own handle is its
    signature, not a contact, as in `normalize_listing`.
    """
    cleaned = (text or "").translate(_FA_DIGITS)
    handles = {name.lower() for pattern in (_HANDLE, _ANNOUNCED, _LINK_HANDLE) for name in pattern.findall(cleaned)}
    handles.discard(source_username.lower())
    phones = {_phone_digits(number) for link in _LINK.finditer(cleaned) for number in _LINK_PHONE.findall(link.group(0))}
    rest = _HANDLE.sub(" ", _LINK.sub(" ", cleaned))
    for pattern in (_NANP, _INTL, _LOCAL_PHONE):
        phones.update(_phone_digits(match.group(0)) for match in pattern.finditer(rest))
        rest = pattern.sub(" ", rest)
    numbers = {re.sub(r"\D", "", number) for number in re.findall(r"\d(?:[\d,٬.]*\d)?", rest)}
    return frozenset(handles), frozenset(phones), frozenset(numbers)


def _live_copy(pk: int) -> Q:
    """Which posts another post can be settled against.

    A post still waiting counts only if it came first, so of two waiting copies only one ever
    settles, whichever run looks first, and the other reaches the model. A repost is never an
    original, nor a verdict the model may change.
    """
    waiting = Q(status__in=_WAITING, pk__lt=pk)
    decided = Q(status=PostStatus.REJECTED) & ~Q(reject_reason="repost") & (Q(model="rule") | ~Q(reject_reason__in=GATE_SECOND_LOOK_REASONS))
    return waiting | Q(status=PostStatus.ACCEPTED) | decided


def _first_with_facts(copies, facts: tuple):
    """The first of these (pk, text, source username) rows saying the same, reading few of them when, as usual, the first does."""
    for start in range(0, GATE_COPIES_COMPARED, _COPIES_PAGE):
        rows = list(copies[start : start + _COPIES_PAGE])
        for pk, text, username in rows:
            if copy_facts(text, username) == facts:
                return pk
        if len(rows) < _COPIES_PAGE:
            return None
    return None


def find_original(post: HomePost) -> int | None:
    """The post this one repeats, word for word and with the same contact and numbers, if any.

    A listing still being seen comes first, however old its first post: its `last_seen` must move,
    or it expires while the owner keeps reposting it. Otherwise the earliest copy inside the window,
    on either side, because a read walks from newest to oldest.
    """
    if not post.text_key or post.text_key == EMPTY_KEY or len(post.text or "") < MIN_TEXT_CHARS * 2:
        return None
    facts = copy_facts(post.text, post.source.username)
    floor = post.posted_at - timedelta(days=REPOST_WINDOW_DAYS)
    ceiling = post.posted_at + timedelta(days=REPOST_WINDOW_DAYS)
    same = HomePost.objects.filter(text_key=post.text_key, source__country=post.source.country).exclude(pk=post.pk)
    still_seen = same.filter(status=PostStatus.ACCEPTED, listing__last_seen__gte=floor).order_by("-listing__last_seen", "-pk")
    near = same.filter(_live_copy(post.pk), posted_at__gte=floor, posted_at__lte=ceiling).order_by("posted_at", "pk")
    for copies in (still_seen, near):
        original = _first_with_facts(copies.values_list("pk", "text", "source__username"), facts)
        if original is not None:
            return original
    return None


class GateDecision(NamedTuple):
    kind: str
    original_pk: int | None = None


def _send_back(pks: list[int]) -> int:
    """Copies settled by rule return to the queue. Only while they are still settled that way, so nothing else is overwritten."""
    return HomePost.objects.filter(pk__in=pks, status=PostStatus.REJECTED, reject_reason="repost", model="rule").update(
        status=PostStatus.NEW, reject_reason="", model="", review_json=None, error="", reviewed_at=None
    )


def repost_can_return(post: HomePost) -> bool:
    """A copy settled against a post still waiting may come back, so its photos stay."""
    raw = post.review_json if isinstance(post.review_json, dict) else {}
    return HomePost.objects.filter(pk=raw.get("repost_of"), status__in=_WAITING).exists()


def _after_model_verdict(post: HomePost, reject_reason: str = "") -> None:
    """Copies settled while this post waited learn its verdict.

    A changeable verdict sends the newest of them back, so one of them reaches the model, and if it is
    rejected the same way the next does. They do not wait in the queue meanwhile, or an original beyond
    the queue's scan would starve them. A firm verdict stands for them too, and they drop their photos.
    The limit bounds the work and the calls: past that many looks the verdict is safe to keep.
    """
    from home import conf

    if not post.text_key or not conf.review_gate_on():
        return
    copies = HomePost.objects.filter(text_key=post.text_key, status=PostStatus.REJECTED, reject_reason="repost", review_json__repost_of=post.pk)
    pks = list(copies.order_by("-posted_at", "-pk").values_list("pk", flat=True)[:GATE_REQUEUE_LIMIT])
    if not pks:
        return
    if reject_reason in GATE_SECOND_LOOK_REASONS:
        logger.info("review_gate_copies_sent_back post=%s copies=%s", post.pk, _send_back(pks))
    else:
        HomeMedia.objects.filter(post_id__in=pks).delete()


def gate_decision(post: HomePost) -> GateDecision:
    """What the gate would do with a waiting post. Reads only."""
    if too_short(post.text):
        return GateDecision("too_short")
    if post.review_json:
        # Back in the queue after a verdict: someone sent it to the model on purpose, so no rule stops it.
        return GateDecision("sent_back")
    original_pk = find_original(post)
    if original_pk is not None:
        return GateDecision("duplicate", original_pk)
    if not housing_word(post.text):
        return GateDecision("no_housing_word")
    return GateDecision("reach_model")


def apply_gate_decision(post: HomePost, decision: GateDecision) -> ReviewOutcome | None:
    """Store a decision. None means the model must decide."""
    if decision.kind == "duplicate":
        reason = "repost"
        settled = settle_duplicate(post, HomePost.objects.get(pk=decision.original_pk))
    elif decision.kind == "no_housing_word":
        reason = "noise"
        settled = _finish_rejected(post, "noise", model="rule", raw={"rule": GATE_RULE_NO_WORD})
    elif decision.kind == "too_short":
        reason = "noise"
        settled = _finish_rejected(post, "noise", model="rule")
    else:
        return None
    if settled:
        return ReviewOutcome(accepted=False, reject_reason=reason, model="rule")
    post.refresh_from_db()
    return ReviewOutcome(accepted=post.status == PostStatus.ACCEPTED, reject_reason=post.reject_reason, model=post.model)


def settle_duplicate(post: HomePost, original: HomePost) -> bool:
    """Same result as `_finish_repost`, and the listing it was merged into is seen again too. False if another run got there first.

    An original still waiting may yet get a verdict that sends this copy back, so the copy keeps its photos until then.
    """
    if original.status in _WAITING:
        if not _finish_rejected(post, "repost", model="rule", raw={"repost_of": original.pk}):
            return False
        verdict_landed_meanwhile = HomePost.objects.filter(pk=original.pk, status=PostStatus.REJECTED, reject_reason__in=GATE_SECOND_LOOK_REASONS).exclude(model="rule")
        if verdict_landed_meanwhile.exists():
            _send_back([post.pk])
        return True
    if not _finish_repost(post, original):
        return False
    listing = getattr(original, "listing", None)
    canonical = listing.canonical if listing is not None else None
    if canonical is not None:
        _seen_in_repost(canonical, post.posted_at)
    return True


def repost_original(post: HomePost) -> HomePost | None:
    """The earliest earlier post with the same wording in the same country, if any."""
    if not post.text_key or len(post.text or "") < MIN_TEXT_CHARS * 2:
        return None
    earlier = Q(posted_at__lt=post.posted_at) | Q(posted_at=post.posted_at, pk__lt=post.pk)
    return (
        HomePost.objects.filter(
            text_key=post.text_key,
            source__country=post.source.country,
            posted_at__gte=post.posted_at - timedelta(days=REPOST_WINDOW_DAYS),
        )
        .filter(earlier)
        .exclude(pk=post.pk)
        .order_by("posted_at", "pk")
        .first()
    )


def asked_for_removal(post: HomePost) -> bool:
    """This poster told the agent to take their ad down: their new posts are not imported either."""
    from outreach.models import OutreachOptOut

    query = Q(pk__in=[])
    if post.poster_user_id:
        query |= Q(telegram_user_id=post.poster_user_id)
    if post.author_id:
        query |= Q(telegram_user_id=post.author_id)
    if post.poster_username:
        query |= Q(telegram_username__iexact=post.poster_username)
    return OutreachOptOut.objects.filter(query, block_listings=True).exists()


def precheck(post: HomePost, *, gate: bool | None = None) -> ReviewOutcome | None:
    """Rule verdicts that need no model: too short, an exact repost, or with the gate on the gate's rules.

    None means the model must decide. A caller going through many posts passes `gate`, to read the setting once.
    """
    if too_short(post.text):
        _finish_rejected(post, "noise", model="rule")
        return ReviewOutcome(accepted=False, reject_reason="noise", model="rule")
    if asked_for_removal(post):
        _finish_rejected(post, "owner_removed", model="rule")
        return ReviewOutcome(accepted=False, reject_reason="owner_removed", model="rule")
    if gate is None:
        from home import conf

        gate = conf.review_gate_on()
    if gate:
        return apply_gate_decision(post, gate_decision(post))
    original = repost_original(post)
    if original is not None:
        _finish_repost(post, original)
        return ReviewOutcome(accepted=False, reject_reason="repost", model="rule")
    return None


def ask_model(prompt: str, *, model: str = ""):
    """One housing review call. HTTP only, safe to run in a worker thread."""
    from home.llm import review_completion

    return review_completion(prompt, system=SYSTEM_PROMPT, model=model)


def finish_with_result(post: HomePost, result) -> ReviewOutcome:
    """Store what the model said about a post (or why it failed)."""
    if not result.ok:
        _finish_error(post, result.error or "llm_error", result.model)
        return ReviewOutcome(accepted=False, error=result.error or "llm_error", model=result.model)
    data = parse_json(result.text)
    if data is None:
        _finish_error(post, "Invalid model JSON.", result.model)
        return ReviewOutcome(accepted=False, error="Invalid model JSON.", model=result.model)
    return apply_review(post, data, model=result.model)


def review_post(post: HomePost, *, model: str | None = None) -> ReviewOutcome:
    """Review one post and store the verdict. Never raises on model trouble."""
    verdict = precheck(post)
    if verdict is not None:
        return verdict
    if not llm_enabled():
        return ReviewOutcome(accepted=False, error="No LLM is configured.")
    if model is None:
        from home import conf

        model = conf.review_model()
    return finish_with_result(post, ask_model(build_prompt(post), model=model))


def apply_review(post: HomePost, data: dict, *, model: str = "") -> ReviewOutcome:
    if not bool(data.get("accept")):
        reason = str(data.get("reject_reason") or "").strip().lower()
        if reason not in REJECT_REASONS or reason == "repost":
            reason = "other"
        if _finish_rejected(post, reason, model=model, raw=data):
            _after_model_verdict(post, reason)
        return ReviewOutcome(accepted=False, reject_reason=reason, model=model, raw=data)

    fields, notes = normalize_listing(post, data)
    if fields is None:
        if _finish_rejected(post, "incomplete", model=model, raw=data):
            _after_model_verdict(post, "incomplete")
        return ReviewOutcome(accepted=False, reject_reason="incomplete", model=model, raw=data)
    from home.abroad import abroad_reason, foreign_phone

    if fields["kind"] == ListingKind.OFFER:
        if abroad_reason(post.text, post.source.country, fields["country"]):
            # Rule 47: a flat in Dubai posted in a Vancouver group is never a Vancouver listing (#314).
            if _finish_rejected(post, "abroad", model=model, raw=data):
                _after_model_verdict(post, "abroad")
            return ReviewOutcome(accepted=False, reject_reason="abroad", model=model, raw=data)
        code = foreign_phone(post.text, post.source.country, fields["country"])
        if code:
            notes.append(f"only phone is foreign {code}")

    first_seen, last_seen = _seen_window(post)
    now = timezone.now()
    with transaction.atomic():
        claimed = HomePost.objects.filter(pk=post.pk, status__in=_WAITING).update(
            status=PostStatus.ACCEPTED, reject_reason="", review_json=data, model=model[:96], error="", reviewed_at=now
        )
        if not claimed:
            # Settled during the model call. That verdict stands and no listing is made.
            post.refresh_from_db()
            return ReviewOutcome(accepted=False, reject_reason=post.reject_reason, model=post.model, raw=data)
        post.status = PostStatus.ACCEPTED
        post.reject_reason = ""
        post.review_json = data
        post.model = model[:96]
        post.error = ""
        post.reviewed_at = now
        listing, _ = HomeListing.objects.update_or_create(
            post=post,
            defaults={
                **fields,
                "needs_review": bool(notes),
                "review_note": "; ".join(notes)[:255],
                "first_seen": first_seen,
                "last_seen": last_seen,
            },
        )
        # The place may have changed: the link below works it out again, or the next run if that fails.
        HomeListing.objects.filter(pk=listing.pk).update(area_checked_at=None)
        link_duplicate(listing)
        expire_if_old(listing, now)
    from home.areas import link_area

    link_area(listing)
    _after_model_verdict(post)
    return ReviewOutcome(accepted=True, listing=listing, model=model, raw=data)


def expire_if_old(listing: HomeListing, now) -> bool:
    """A listing last seen longer ago than its kind's expiry (a late review, a requeue) is born expired, as the freshness step would make it."""
    from home import conf

    days = conf.expire_wanted_days() if listing.kind == ListingKind.WANTED else conf.expire_offer_days()
    if listing.last_seen is None or listing.last_seen >= now - timedelta(days=days):
        return False
    done = HomeListing.objects.filter(pk=listing.pk, status=ListingStatus.ACTIVE).update(status=ListingStatus.EXPIRED, updated_at=now)
    if done:
        listing.status = ListingStatus.EXPIRED
    return bool(done)


def _seen_window(post: HomePost):
    """First and last time this wording appeared, so a repost extends the listing instead of adding one."""
    if not post.text_key:
        return post.posted_at, post.posted_at
    window = HomePost.objects.filter(text_key=post.text_key, source__country=post.source.country).aggregate(
        first=Min("posted_at"), last=Max("posted_at")
    )
    return min(window["first"] or post.posted_at, post.posted_at), max(window["last"] or post.posted_at, post.posted_at)


# A bedroom count written as a number: «۲ خوابه», «دو اتاق», 2 bed, 3BR, 2-Zimmer.
_ROOM_COUNT = re.compile(
    r"(?<![\w؀-ۿ])(?:\d+|یک|دو|سه|چهار|پنج|one|two|three|four)\s*[-‌]?\s*(?:خواب|اتاق|bed|br(?![a-z])|bd(?![a-z])|room|zi)",
    re.IGNORECASE,
)
_LIVING = re.compile(r"سالن|پذیرایی|هال(?![\w؀-ۿ])")
_ONE_BEDROOM = re.compile(r"اتاق[‌ ]?خواب(?![‌ ]?(?:ها|های)|\s*\d)")


def bedrooms_written(text: str, rooms: Decimal | None) -> Decimal | None:
    """One bedroom when the post lists a living room and «اتاق خواب» with no count, whatever the model read: a
    «سالن و اتاق خواب» is not 2 bedrooms (audit 2026-11-10 #324, rule 50)."""
    text = (text or "").translate(_FA_DIGITS)
    if rooms is None or rooms <= 1 or _ROOM_COUNT.search(text):
        return rooms
    if _LIVING.search(text) and _ONE_BEDROOM.search(text):
        return Decimal("1")
    return rooms


# A Quebec size «4½», «4 1/2», «۴ و نیم» counts every room (kitchen, living room, bath as the half): bedrooms are
# N - 2, and a 1½ or 2½ has no separate bedroom (audit 2026-11-11 13:17 #355, rule 67: «آپارتمان ۲½» was stored as
# rooms 2 and «۲.۵ خوابه»).
_QC_SIZE = re.compile(
    r"(?<![\d.,/])([1-9])\s*(?:½|1/2(?!\d)|و\s*نیم(?!\s*(?:متر|ماه|سال|ساعت|هفته|دلار|هزار|میلیون|کیلو|km|کیلومتر|دقیقه)))",
    re.IGNORECASE,
)
_HALF_BEDS = re.compile(r"\d+(?:\s*[.٫/]\s*\d+|\s*½)?\s*(?:خوابه|خواب)")


def quebec_size(text: str) -> int | None:
    """N of a Quebec «N½» size the post writes, or None."""
    found = _QC_SIZE.search((text or "").translate(_FA_DIGITS))
    return int(found.group(1)) if found else None


def quebec_rooms(fields: dict, text: str) -> None:
    """In Canada a «N½» is N - 2 bedrooms, never N; 1½ and 2½ have none, so rooms stays empty and the summary says the
    size as written, not «۲.۵ خوابه»."""
    size = quebec_size(text) if fields.get("country") == "CA" else None
    if size is None:
        return
    if _ROOM_COUNT.search(_QC_SIZE.sub(" ", (text or "").translate(_FA_DIGITS))):
        # The post writes its bedrooms too («4½, 2 bedrooms»): that count stands.
        return
    beds = size - 2
    fields["rooms"] = Decimal(beds) if beds >= 1 else None
    if fields.get("summary_fa"):
        fields["summary_fa"] = _HALF_BEDS.sub(f"{size}½", fields["summary_fa"], count=1)


# «یک اتاق خواب + یک دن», «1 bed + den», «1+1»: the den is never a bedroom (audit 2026-11-12 15:20 #383, rule 65:
# «یک اتاق خواب اصلی + یک دن» was stored as rooms 2 and «۲ خوابه»).
DEN = re.compile(r"(?:\+|و|با)\s*(?:یک\s*|یه\s*|1\s*|a\s+)?(?:دن|den)(?![a-z؀-ۿ])|(?<![\d.])[1-9]\s*\+\s*1(?![\d.])", re.IGNORECASE)
_DEN_BASE = re.compile(
    r"(?<![\w؀-ۿ])(\d|یک|یه|دو|سه|one|two|three)\s*(?:اتاق[\s‌]*خواب(?:ه)?|خوابه|خواب|bed(?:room)?s?|br|bd)(?![a-z])"
    r"|(?<![\d.])([1-9])\s*\+\s*1(?![\d.])",
    re.IGNORECASE,
)
_DEN_WORDS = {"یک": 1, "یه": 1, "دو": 2, "سه": 3, "one": 1, "two": 2, "three": 3}


def has_den(text: str) -> bool:
    return bool(DEN.search((text or "").translate(_FA_DIGITS)))


def den_rooms(fields: dict, text: str) -> None:
    """A den in the post: bedrooms are the base count written before it, never base + 1."""
    text = (text or "").translate(_FA_DIGITS)
    if not DEN.search(text):
        return
    found = _DEN_BASE.search(text)
    if not found:
        return
    word = (found.group(1) or found.group(2) or "").lower()
    base = int(word) if word.isdigit() else _DEN_WORDS.get(word)
    if not base:
        return
    if fields.get("rooms") is not None and Decimal(fields["rooms"]) != Decimal(base):
        fields["rooms"] = Decimal(base)
        if fields.get("summary_fa"):
            fields["summary_fa"] = _HALF_BEDS.sub(f"{base} خوابه با دن", fields["summary_fa"], count=1)


# A spot in the living room of a shared flat («هم‌خانه سالن یک آپارتمان یک خوابه», «اجاره سالن», living room
# share) is let, not the flat: a room-type listing with no bedroom count (audit 2026-11-12 16:18 #392, post 117423).
# A flat that only lists «سالن» as a feature does not match.
LIVING_SPOT = re.compile(
    r"هم[\s‌]*خ(?:ا|و)نه(?:ی)?\s*(?:در\s*)?(?:سالن|هال|پذیرایی)"
    r"|(?:اجاره|کرایه)(?:ی)?\s*(?:سالن|هال|پذیرایی)(?![\w؀-ۿ]*\s*(?:ورزش|ژیم|اجتماع))"
    r"|(?:سالن|هال|پذیرایی)\s*(?:برای|جهت)\s*(?:اجاره|هم[\s‌]*خ(?:ا|و)نه)"
    r"|living[\s-]*room\s*(?:share|for\s+rent|spot|available)|share\s+(?:the|a|my)?\s*living[\s-]*room"
    # A shared studio is a spot too (audit 2026-11-13 00:18 #403, post 118914).
    r"|shared\s+studio|studio\s+share|not\s+a\s+private\s+room|استودیو(?:ی)?\s*مشترک|استدیو(?:ی)?\s*مشترک",
    re.IGNORECASE,
)


def living_spot(text: str) -> bool:
    return bool(LIVING_SPOT.search(text or ""))


def living_room_spot(fields: dict, text: str) -> None:
    if not living_spot(text):
        return
    fields["property_type"] = "room"
    fields["rooms"] = None
    fields["flags"] = {**(fields.get("flags") or {}), "living_room": True}


# «اتاق برای دو هفته ... کسی سراغ داره؟», «کسی اتاق داره؟», «موجود دارید؟»: a housing word, then someone asked if
# anyone has one. A post like that wants, never offers (audit 2026-11-12 20:16 #398, post 118229). A post with a
# price (a number of three digits or more) is left to the model.
_ASK_HOUSING = r"(?:اتاق|خونه|خانه|آپارتمان|سوئیت|سوییت|استودیو|استدیو|واحد|بیسمنت|جا(?:یی)?)"
SEEKS_HOUSING = re.compile(
    _ASK_HOUSING + r"[^\n؟?]{0,80}?(?:(?:کسی|کسیو|کسیرو)\s*)?(?:سراغ\s*دار(?:ه|ید|ین|ن)|کسی\s*دار(?:ه|ید|ین)|موجود\s*دار(?:ید|ین|ه))\s*[؟?]?"
    r"|(?:کسی|کسیو)\s*" + _ASK_HOUSING + r"\s*(?:سراغ\s*)?دار(?:ه|ید|ین)\s*[؟?]",
)


_BIG_NUMBER = re.compile(r"\d[\d,.٬]{2,}")


def _numbers(text: str) -> set[str]:
    clean = (text or "").translate(_FA_DIGITS)
    found = set()
    for raw in _BIG_NUMBER.findall(clean):
        digits = re.sub(r"[,.٬]", "", raw)
        if len(digits) >= 3:
            found.add(digits.lstrip("0") or "0")
    return found


def summary_numbers(fields: dict, text: str, notes: list[str]) -> None:
    """Every number of three digits or more in the summary is one the post writes (audit 2026-11-12 20:16, 7459:
    «اجاره 21205 دلار» for a post that says $2,125). One wrong number that should be the rent is replaced by it;
    anything else drops the summary, and the listing gets a fresh one at the next review."""
    summary = fields.get("summary_fa") or ""
    if not summary:
        return
    post = _numbers(text)
    # A number the post writes in words («تا هزار یورو») is the parsed field, so the parsed fields count too.
    for name in ("price_amount", "price_max", "size_m2", "deposit_amount"):
        value = fields.get(name)
        if value:
            post.add(str(int(value)))
    wrong = [number for number in _numbers(summary) if number not in post]
    if not wrong:
        return
    amount = fields.get("price_amount")
    rent = str(int(amount)) if amount else ""
    if len(wrong) == 1 and rent and rent in post:
        fixed = re.sub(r"\d[\d,.٬]*", lambda m: rent if re.sub(r"[,.٬]", "", m.group(0).translate(_FA_DIGITS)) == wrong[0] else m.group(0), summary.translate(_FA_DIGITS))
        if wrong[0] not in _numbers(fixed):
            fields["summary_fa"] = fixed
            notes.append(f"summary number {wrong[0]} not in post: set to {rent}")
            return
    fields["summary_fa"] = ""
    notes.append(f"summary numbers not in post: {', '.join(sorted(wrong))}")


def seeks_housing(text: str) -> bool:
    text = (text or "").translate(_FA_DIGITS)
    return bool(SEEKS_HOUSING.search(text)) and not re.search(r"\d{3,}", text.replace(",", "").replace(".", ""))


# Shared groups file every post under one city (almankoln, KoelnDuesseldorf: Cologne). A city the post names wins
# over the group's (audit 2026-11-10 #328, rule 52: «استودیو ... دوسلدورف Friedrichstadt» stored as Cologne).
# Latin «essen» is a German verb and Persian «بن» a word, so neither counts.
NAMED_CITIES = {
    "Cologne": {
        "Dusseldorf": ("dusseldorf", "düsseldorf", "duesseldorf", "دوسلدورف", "دوسلدرف", "دوسلدوف"),
        "Bonn": ("bonn", "بون"),
        "Leverkusen": ("leverkusen", "لورکوزن"),
        "Aachen": ("aachen", "آخن"),
        "Essen": ("اسن",),
        "Dortmund": ("dortmund", "دورتموند"),
        "Bochum": ("bochum", "بوخوم"),
        "Wuppertal": ("wuppertal", "ووپرتال", "وپرتال"),
        "Duisburg": ("duisburg", "دویسبورگ", "دوئیسبورگ"),
        "Neuss": ("neuss",),
    },
}
_OWN_NAMES = {"Cologne": ("cologne", "köln", "koeln", "koln", "کلن", "کلون")}
_HASHTAG = re.compile(r"#\S+|^.*🏙.*$", re.MULTILINE)


def _says(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) for word in words)


def named_city(text: str, city: str, default_city: str, notes: list[str]) -> str:
    """The city an offer's text names when the group's default city was stored: one other city and not the group's
    own wins; more than one, or both, holds for an admin. Hashtags and the group's «🏙 شهر:» footer do not count."""
    others = NAMED_CITIES.get(default_city)
    if not others or city != default_city:
        return city
    body = _HASHTAG.sub(" ", (text or "")).casefold()
    named = sorted(name for name, words in others.items() if _says(body, words))
    if not named:
        return city
    if len(named) == 1 and not _says(body, _OWN_NAMES.get(default_city, ())):
        return named[0]
    notes.append(f"post names {', '.join(named)} but stored {city}")
    return city


def normalize_listing(post: HomePost, data: dict) -> tuple[dict | None, list[str]]:
    """Return clean model fields plus review notes, or (None, []) when unusable."""
    source = post.source
    notes: list[str] = []
    kind = _choice(data.get("kind"), ListingKind)
    if not kind:
        return None, []
    if kind == ListingKind.OFFER and seeks_housing(post.text):
        # «... کسی سراغ داره؟» asks for a place (audit 2026-11-12 20:16, post 118229).
        kind = ListingKind.WANTED
        notes.append("asks for a place: wanted")
    property_type = _choice(data.get("property_type"), PropertyType) or PropertyType.OTHER
    rental_mode = _choice(data.get("rental_mode"), RentalMode) or LEGACY_MODES.get(str(data.get("property_type") or "")) or RentalMode.UNKNOWN

    country = _country(data.get("country")) or source.country
    city, area, country = clean_city(clean_place(_clean(data.get("city"), 64)), clean_place(unglue_postcode(_clean(data.get("area"), 96))), country, source.default_city)
    if not city and not area:
        return None, []
    region = _region(data.get("region")) or (source.default_region if country == source.country else "")
    if country != source.country:
        notes.append(f"country {country} differs from source {source.country}")
    if kind == ListingKind.OFFER:
        city = named_city(post.text, city, source.default_city, notes)

    period = _choice(data.get("price_period"), PricePeriod) or PricePeriod.MONTH
    amount = _money(data.get("price_amount"))
    price_max = _money(data.get("price_max"))
    currency = _currency(data.get("price_currency")) or source.default_currency
    if currency != source.default_currency:
        notes.append(f"currency {currency} differs from source default {source.default_currency}")
    for value, label in ((amount, "price"), (price_max, "budget")):
        if value is not None and period == PricePeriod.MONTH and currency in MONTHLY_RANGE:
            low, high = MONTHLY_RANGE[currency]
            if not (low <= value <= high):
                notes.append(f"{label} {value} {currency} outside {low}-{high}")

    deposit_amount = _money(data.get("deposit_amount"))
    deposit_months = _decimal(data.get("deposit_months"), maximum=Decimal("12"))
    if deposit_amount is not None and deposit_months is not None:
        deposit_months = None
        notes.append("deposit given as amount and months; kept the amount")

    size_m2 = _decimal(data.get("size_m2"), maximum=Decimal("2000"))
    if size_m2 is None:
        sqft = _decimal(data.get("size_sqft"), maximum=Decimal("20000"))
        if sqft is not None:
            size_m2 = (sqft * SQFT_TO_M2).quantize(Decimal("0.1"))

    available_from = _iso_date(data.get("available_from"))
    available_to = _iso_date(data.get("available_to"))
    if available_from and available_to and available_to < available_from:
        available_to = None
        notes.append("end date before start date")
    available_from, available_to = _next_year(post, available_from, available_to, notes)
    if available_from and post.posted_at and available_from < post.posted_at.date() - timedelta(days=365):
        available_from = None
        notes.append("start date more than a year before the post")

    confidence = _confidence(data.get("confidence"))
    overall = confidence.get("overall")
    if overall is not None and overall < REVIEW_CONFIDENCE_MIN:
        notes.append(f"low confidence {overall:.2f}")

    contact_tg = _clean(str(data.get("contact_telegram") or "").lstrip("@"), 64)
    if contact_tg.lower() == source.username.lower():
        contact_tg = ""
    # A crawled post's contact must be in its text. Who posted it comes from Telegram, on the post, never from here.
    if contact_tg and not source.username.startswith("app_") and not handle_in_text(contact_tg, post.text):
        contact_tg = ""
    if not contact_tg:
        contact_tg = first_handle(post.text, exclude=source.username)
    contact_phone = _phone(data.get("contact_phone"), country) or _first_phone(post.text, country)

    flags = _flags(data.get("flags"))
    if kind == ListingKind.OFFER and amount is None:
        flags["price_missing"] = True  # normal for «پیام بدید» posts: shown as "ask for price", not queued
    warning = _deposit_warning(region, flags, deposit_amount, deposit_months)
    if warning:
        flags["deposit_warning"] = warning

    lister = _choice(data.get("lister_type"), ListerType) or ListerType.UNKNOWN
    if kind == ListingKind.WANTED:
        lister = ListerType.UNKNOWN
    elif source.is_agency:
        lister = ListerType.AGENT
    elif _repeat_poster(post):
        lister = ListerType.AGENT
        flags["repeat_poster"] = True

    gender = _choice(data.get("roommate_gender"), RoommateGender) or RoommateGender.ANY
    if property_type not in {PropertyType.ROOM, PropertyType.BASEMENT} and rental_mode not in {RentalMode.SUBLET, RentalMode.SHORT_TERM}:
        gender = RoommateGender.ANY if gender != RoommateGender.COUPLE else gender

    fields = {
        "kind": kind,
        "property_type": property_type,
        "rental_mode": rental_mode,
        "country": country,
        "region": region,
        "city": city,
        "area": area,
        "price_amount": amount,
        "price_max": price_max,
        "price_currency": currency,
        "price_period": period,
        "price_basis": _choice(data.get("price_basis"), PriceBasis) or PriceBasis.UNKNOWN,
        "deposit_amount": deposit_amount,
        "deposit_months": deposit_months,
        "size_m2": size_m2,
        "rooms": bedrooms_written(post.text, _decimal(data.get("rooms"), maximum=Decimal("20"))),
        "furnished": _choice(data.get("furnished"), Furnished) or Furnished.UNKNOWN,
        "registration": _choice(data.get("registration"), Tri) or Tri.UNKNOWN,
        "bills_included": _choice(data.get("bills_included"), Tri) or Tri.UNKNOWN,
        "available_from": available_from,
        "available_to": available_to,
        "min_term_months": _small_int(data.get("min_term_months"), maximum=60),
        "roommate_gender": gender,
        "occupants_wanted": _small_int(data.get("occupants_wanted"), maximum=10),
        "lister_type": lister,
        "contact_telegram": contact_tg,
        "contact_phone": contact_phone,
        "summary_fa": _summary(data.get("summary_fa")),
        "summary_en": _summary(data.get("summary_en")),
        "flags": flags,
        "confidence": confidence,
        "dedup_key": post.text_key or text_key(post.text),
    }
    quebec_rooms(fields, post.text)
    den_rooms(fields, post.text)
    living_room_spot(fields, post.text)
    summary_numbers(fields, post.text, notes)
    if not fields["summary_fa"]:
        notes.append("no summary")
    return fields, notes


def _deposit_warning(region: str, flags: dict, amount, months) -> str:
    """Rules a seeker should know about. Shown as a label, never used to hide the listing."""
    if region == "ON" and flags.get("damage_deposit"):
        return "Ontario allows only a last-month deposit, not a damage deposit"
    if region == "QC" and (flags.get("damage_deposit") or amount is not None or months is not None):
        return "Quebec landlords cannot ask for a deposit"
    return ""


def _repeat_poster(post: HomePost) -> bool:
    """A sender who keeps posting is almost always an agent or a bumping landlord."""
    if not post.author_id:
        return False
    since = post.posted_at - timedelta(days=30)
    posts = HomePost.objects.filter(
        author_id=post.author_id, source__country=post.source.country, posted_at__gte=since, posted_at__lte=post.posted_at
    )
    if posts.count() >= REPEAT_POSTER_TOTAL:
        return True
    return posts.filter(status=PostStatus.ACCEPTED).count() >= REPEAT_POSTER_LISTINGS


def link_duplicate(listing: HomeListing) -> None:
    """Merge a listing with an earlier one that has the same wording, or the same contact, city and price."""
    base = HomeListing.objects.exclude(pk=listing.pk).exclude(status=ListingStatus.DUPLICATE).filter(country=listing.country)
    match = None
    if listing.dedup_key:
        match = _same_text_listing(listing, base.filter(dedup_key=listing.dedup_key).order_by("first_seen"))
    if match is None and listing.price_amount is not None and (listing.contact_telegram or listing.contact_phone):
        low = listing.price_amount * Decimal("0.95")
        high = listing.price_amount * Decimal("1.05")
        same_person = base.filter(
            kind=listing.kind,
            city=listing.city,
            property_type=listing.property_type,
            price_amount__gte=low,
            price_amount__lte=high,
        )
        if listing.contact_telegram:
            match = same_person.filter(contact_telegram__iexact=listing.contact_telegram).order_by("first_seen").first()
        if match is None and listing.contact_phone:
            match = same_person.filter(contact_phone=listing.contact_phone).order_by("first_seen").first()
    if match is not None:
        merge_listings(listing, match)
        return
    copy = same_poster_copy(listing)
    if copy is not None:
        keep, drop = keeper(listing, copy)
        merge_into(keep, drop)


SAME_POSTER_DAYS = 14
SAME_POSTER_RATIO = 0.85


def _plain_text(text: str) -> str:
    return re.sub(r"[\W_]+", " ", (text or "").casefold()).strip()


def same_poster_copy(listing: HomeListing) -> HomeListing | None:
    """The same person's live offer of the same place, posted again within 14 days with near-identical words: one
    house, not three (audit 2026-11-10 17:15, User030X: 6920, 6923, 7048 all Eichwalde)."""
    import difflib

    from home.contact_text import unglue_postcode

    post = listing.post
    if listing.kind != ListingKind.OFFER or not post.author_id or listing.status != ListingStatus.ACTIVE:
        return None
    area = _plain_text(unglue_postcode(listing.area))
    mine = _plain_text(post.text)
    if len(mine) < 40:
        return None
    since = post.posted_at - timedelta(days=SAME_POSTER_DAYS)
    until = post.posted_at + timedelta(days=SAME_POSTER_DAYS)
    others = (
        HomeListing.objects.exclude(pk=listing.pk)
        .filter(kind=ListingKind.OFFER, status=ListingStatus.ACTIVE, country=listing.country, city=listing.city, post__author_id=post.author_id)
        .filter(post__posted_at__gte=since, post__posted_at__lte=until)
        .select_related("post")
        .order_by("-post__posted_at")[:10]
    )
    for other in others:
        their_area = _plain_text(unglue_postcode(other.area))
        if area and their_area and area != their_area:
            continue
        # An agent's template for two units differs in type, price or rooms: those stay apart.
        if other.property_type != listing.property_type:
            continue
        if any(getattr(other, name) is not None and getattr(listing, name) is not None and getattr(other, name) != getattr(listing, name) for name in ("price_amount", "rooms", "size_m2")):
            continue
        if difflib.SequenceMatcher(None, mine, _plain_text(other.post.text)).ratio() >= SAME_POSTER_RATIO:
            return other
    return None


def keeper(left: HomeListing, right: HomeListing) -> tuple[HomeListing, HomeListing]:
    """The one we wrote to its poster about stays (its link is in their chat), else the newest. (keep, drop)."""
    from outreach.models import OutreachMessage

    messaged = set(OutreachMessage.objects.filter(listing_id__in=(left.pk, right.pk)).values_list("listing_id", flat=True))
    if len(messaged) == 1:
        return (left, right) if left.pk in messaged else (right, left)
    newest = max((left, right), key=lambda item: (item.post.posted_at, item.pk))
    return newest, (right if newest is left else left)


def merge_into(keep: HomeListing, drop: HomeListing) -> HomeListing:
    """Mark `drop` a copy of `keep`, keeping the facts only the copy has (price, deposit, size, rooms)."""
    updates = {}
    for name in ("price_amount", "deposit_amount", "deposit_months", "size_m2", "rooms", "available_from"):
        if getattr(keep, name) in (None, "") and getattr(drop, name) not in (None, ""):
            updates[name] = getattr(drop, name)
    if updates.get("price_amount") is not None:
        updates["price_currency"] = drop.price_currency
        flags = {key: value for key, value in (keep.flags or {}).items() if key != "price_missing"}
        updates["flags"] = flags
    drop.status = ListingStatus.DUPLICATE
    drop.canonical = keep
    drop.flags = {**(drop.flags or {}), "superseded_by": keep.pk}
    drop.save(update_fields=["status", "canonical", "flags", "updated_at"])
    HomeListing.objects.filter(canonical=drop).update(canonical=keep)
    first_seen = min(keep.first_seen, drop.first_seen) if keep.first_seen and drop.first_seen else keep.first_seen
    HomeListing.objects.filter(pk=keep.pk).update(first_seen=first_seen, **updates)
    keep.refresh_from_db()
    return keep


def _same_text_listing(listing: HomeListing, same_text) -> HomeListing | None:
    """With the gate on, a copy with another contact or price went to the model to be its own listing, not to merge back into the old one."""
    from home import conf

    first = same_text.first()
    if first is None or not conf.review_gate_on():
        return first
    facts = copy_facts(listing.post.text, listing.post.source.username)
    for other in same_text.select_related("post__source")[:GATE_COPIES_COMPARED]:
        if copy_facts(other.post.text, other.post.source.username) == facts:
            return other
    return None


def merge_listings(left: HomeListing, right: HomeListing) -> HomeListing:
    """Keep the earlier listing as the original and mark the other as its repost. Returns the original."""
    def order(item: HomeListing):
        return (item.first_seen, item.post.posted_at, item.pk)

    original, repost = (left, right) if order(left) <= order(right) else (right, left)
    repost.status = ListingStatus.DUPLICATE
    repost.canonical = original
    repost.save(update_fields=["status", "canonical", "updated_at"])
    HomeListing.objects.filter(canonical=repost).update(canonical=original)
    if original.last_seen is None or repost.last_seen > original.last_seen:
        original.last_seen = repost.last_seen
    if repost.first_seen < original.first_seen:
        original.first_seen = repost.first_seen
    original.save(update_fields=["last_seen", "first_seen", "updated_at"])
    return original


def link_photo_duplicates(listing: HomeListing) -> HomeListing | None:
    """Merge with a listing in the same city that shows the same photo at a compatible price.

    A shared photo alone is not enough: agents reuse building photos across units. The price
    must be within 10% (or unknown) or the contact must be the same. A shared photo with a
    different price is only noted on the listing.
    """
    if listing.status == ListingStatus.DUPLICATE:
        return None
    mine = list(
        HomeMedia.objects.filter(post_id=listing.post_id, status=MediaStatus.STORED, phash__isnull=False).values_list(
            "phash", flat=True
        )
    )
    if not mine:
        return None
    candidates = (
        HomeListing.objects.exclude(pk=listing.pk)
        .exclude(status=ListingStatus.DUPLICATE)
        .filter(
            country=listing.country,
            city=listing.city,
            kind=listing.kind,
            first_seen__gte=listing.first_seen - timedelta(days=REPOST_WINDOW_DAYS),
            first_seen__lte=listing.last_seen + timedelta(days=REPOST_WINDOW_DAYS),
            post__media__status=MediaStatus.STORED,
        )
        .distinct()
        .order_by("first_seen")
    )
    for other in candidates:
        theirs = HomeMedia.objects.filter(post_id=other.post_id, status=MediaStatus.STORED, phash__isnull=False).values_list(
            "phash", flat=True
        )
        if not any(same_photo(a, b) for a in mine for b in theirs):
            continue
        if _prices_compatible(listing, other) or _same_contact(listing, other):
            return merge_listings(listing, other)
        flags = dict(listing.flags or {})
        flags["shared_photo_with"] = other.pk
        listing.flags = flags
        listing.save(update_fields=["flags", "updated_at"])
    return None


def _prices_compatible(left: HomeListing, right: HomeListing) -> bool:
    if left.price_amount is None or right.price_amount is None:
        return True
    low, high = sorted((left.price_amount, right.price_amount))
    return high <= low * Decimal("1.10")


def _same_contact(left: HomeListing, right: HomeListing) -> bool:
    if left.contact_telegram and left.contact_telegram.lower() == right.contact_telegram.lower():
        return True
    return bool(left.contact_phone and left.contact_phone == right.contact_phone)


def _finish_rejected(post: HomePost, reason: str, *, model: str = "", raw: dict | None = None) -> bool:
    """Store a rejection. False, with nothing written, if the post no longer waits for a verdict."""
    now = timezone.now()
    stored = HomePost.objects.filter(pk=post.pk, status__in=_WAITING).update(
        status=PostStatus.REJECTED, reject_reason=reason[:64], review_json=raw or None, model=model[:96], error="", reviewed_at=now
    )
    if not stored:
        return False
    post.status = PostStatus.REJECTED
    post.reject_reason = reason[:64]
    post.review_json = raw or None
    post.model = model[:96]
    post.error = ""
    post.reviewed_at = now
    return True


def _finish_repost(post: HomePost, original: HomePost) -> bool:
    if not _finish_rejected(post, "repost", model="rule", raw={"repost_of": original.pk}):
        return False
    post.media.all().delete()
    listing = getattr(original, "listing", None)
    if listing is not None:
        _seen_in_repost(listing, post.posted_at)
    return True


def _seen_in_repost(listing: HomeListing, when) -> None:
    if when > listing.last_seen:
        listing.last_seen = when
        listing.save(update_fields=["last_seen", "updated_at"])
    revive_from_repost(listing.canonical or listing, when)


def why_not_revived(listing: HomeListing, when, now=None) -> str:
    """Why a repost posted at `when` does not bring this listing back. Blank: it does. Reads only.

    An app listing never comes back this way: an owner's close stores the same status as a timeout, so the two look
    alike, and the owner has a renew button. Rented, reported and removed have their own status and stay as they are.
    """
    from home import conf
    from home.freshness import APP_PREFIX

    if listing.status != ListingStatus.EXPIRED:
        return listing.status
    if listing.rented_at is not None:
        return "rented"
    if listing.post.source.username.startswith(APP_PREFIX):
        return "app_listing"
    days = conf.expire_wanted_days() if listing.kind == ListingKind.WANTED else conf.expire_offer_days()
    if when < (now or timezone.now()) - timedelta(days=days):
        return "repost_too_old"
    if listing.post.text_key and HomeListing.objects.filter(status=ListingStatus.ACTIVE, country=listing.country, post__text_key=listing.post.text_key).exists():
        return "live_copy_exists"
    return ""


def revive_from_repost(listing: HomeListing, when) -> bool:
    """A person reposted a live ad: its expired listing is active again, last seen at the repost. No model, no new row.

    The mark keeps it off the channel posting, which would otherwise take a never-posted listing as new, and off the
    photo reading, which calls a model. Its old channel post, which expiry never edited, is what readers still have.
    """
    if why_not_revived(listing, when):
        return False
    flags = {**(listing.flags or {}), REVIVED_FLAG: timezone.now().isoformat()}
    revived = HomeListing.objects.filter(pk=listing.pk, status=ListingStatus.EXPIRED).update(
        status=ListingStatus.ACTIVE, last_seen=max(when, listing.last_seen), flags=flags, updated_at=timezone.now()
    )
    if revived:
        listing.refresh_from_db()
        logger.info("listing_revived_by_repost listing=%s", listing.pk)
    return bool(revived)


def _finish_error(post: HomePost, error: str, model: str) -> None:
    now = timezone.now()
    stored = HomePost.objects.filter(pk=post.pk, status__in=_WAITING).update(
        status=PostStatus.ERROR, error=(error or "")[:255], model=(model or "")[:96], reviewed_at=now
    )
    if stored:
        post.status = PostStatus.ERROR
        post.error = (error or "")[:255]
        post.model = (model or "")[:96]
        post.reviewed_at = now


def parse_json(text: str) -> dict | None:
    cleaned = _JSON_FENCE.sub("", (text or "").strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        body = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _clean(value: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _choice(value: object, enum) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {choice.value for choice in enum} else ""


def _country(value: object) -> str:
    raw = str(value or "").strip()
    return raw.upper() if len(raw) == 2 and raw.isalpha() else ""


def _region(value: object) -> str:
    raw = str(value or "").strip()
    return raw.upper() if 2 <= len(raw) <= 3 and raw.isalpha() else ""


def _currency(value: object) -> str:
    raw = str(value or "").strip().upper()
    return raw if len(raw) == 3 and raw.isalpha() else ""


def _decimal(value: object, *, maximum: Decimal) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).translate(_FA_DIGITS).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if number <= 0 or number > maximum:
        return None
    return number.quantize(Decimal("0.1"))


def _money(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).translate(_FA_DIGITS).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if number <= 0 or number > Decimal("10000000"):
        return None
    return number.quantize(Decimal("0.01"))


def _small_int(value: object, *, maximum: int) -> int | None:
    try:
        number = int(Decimal(str(value).translate(_FA_DIGITS)))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if 0 < number <= maximum else None


def _iso_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()[:10]
    if len(raw) != 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _confidence(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, float] = {}
    for key, raw in value.items():
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= number <= 1:
            clean[str(key)[:24]] = round(number, 2)
    return clean


def _flags(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, object] = {}
    for key in BOOL_FLAGS:
        if isinstance(value.get(key), bool):
            clean[key] = value[key]
    text = _clean(value.get("kaution_text"), 80)
    if text:
        clean["kaution_text"] = text
    return clean


def _summary(value: object) -> str:
    text = redact_contact(_clean(value, 500).translate(_FA_DIGITS))
    # A letter of another script (Devanagari in «اسکایت्रेन», audit 2026-11-10 #327, rule 51) drops the summary:
    # the post is then held for an admin («no summary»).
    return "" if any(_foreign_letter(char) for char in text) else text


def _foreign_letter(char: str) -> bool:
    import unicodedata

    if not unicodedata.category(char).startswith(("L", "M")):
        return False
    code = ord(char)
    return not (code < 0x250 or 0x600 <= code <= 0x6FF or 0x750 <= code <= 0x77F or 0xFB50 <= code <= 0xFEFF or 0x1E00 <= code <= 0x1EFF)


_YEAR = re.compile(r"(?<!\d)(?:20\d\d|14\d\d)(?!\d)")


def _next_year(post: HomePost, start: date | None, end: date | None, notes: list[str]) -> tuple[date | None, date | None]:
    """A date the post wrote without a year is the next one, never a past one: «January 1st to January 31st» posted on
    2026-11-10 is 2027 (audit 2026-11-10 #327, rule 51). A start still more than 30 days before the post is held."""
    if not start or not post.posted_at:
        return start, end
    floor = post.posted_at.date() - timedelta(days=30)
    if start >= floor:
        return start, end
    if not _YEAR.search((post.text or "").translate(_FA_DIGITS)):
        try:
            moved = start.replace(year=start.year + 1)
            moved_end = end.replace(year=end.year + 1) if end else None
        except ValueError:  # 29 February
            moved, moved_end = None, end
        if moved and moved >= floor:
            return moved, moved_end
    notes.append(f"start date {start} more than 30 days before the post")
    return start, end


def _phone(value: object, country: str) -> str:
    """Digits in international form. North American numbers become +1 followed by ten digits."""
    raw = str(value or "").translate(_FA_DIGITS)
    digits = re.sub(r"\D", "", raw)
    if country in {"CA", "US"}:
        # A number written with its own country code (+98, 0049) is not North American: keep it whole.
        if raw.strip().startswith(("+", "00")):
            whole = digits[2:] if raw.strip().startswith("00") else digits
            if not whole.startswith("1"):
                return f"+{whole}" if 8 <= len(whole) <= 15 else ""
            digits = whole
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return f"+1{digits}" if len(digits) == 10 else ""
    if len(digits) < 9:
        return ""
    return f"+{digits}"[:24] if raw.strip().startswith("+") else digits[:24]


def _first_phone(text: str, country: str) -> str:
    cleaned = (text or "").translate(_FA_DIGITS)
    if country in {"CA", "US"}:
        match = _NANP.search(cleaned)
        return f"+1{match.group(1)}{match.group(2)}{match.group(3)}" if match else ""
    match = _INTL.search(cleaned)
    return _phone(match.group(0), country) if match else ""


def first_handle(text: str, *, exclude: str) -> str:
    for match in _HANDLE.finditer(text or ""):
        handle = match.group(1)
        if handle.lower() != exclude.lower() and not handle.lower().endswith("bot") and handle_in_text(handle, text):
            return handle
    return ""
