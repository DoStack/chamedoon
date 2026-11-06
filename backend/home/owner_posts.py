"""F18, F19, F20: what a person does with their own listings in the new Mini App.

Posting and editing go through the same automatic review as the old form (decision 7). A clean edit goes live at
once; one that review holds waits in HomeListingEdit while the old version stays up.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from home import conf, inbox, status_posts, uploads
from home.alert_store import lock_person
from home.alerts import sync_wanted_alerts
from home.areas import link_area
from home.contact_text import has_contact
from home.freshness import listing_title, restore, set_rented
from home.models import (
    HomeArea,
    HomeListing,
    HomeListingEdit,
    HomePost,
    HomePostKey,
    ListingKind,
    ListingStatus,
    NotificationKind,
    StatusPost,
)
from home.public import closed_reason, public_card
from home.review import normalize_listing, text_key
from home.snapshots import after_submit
from home.stats import owner_stats
from home.submit import APP_PREFIX, SubmitError, finish_listing, review_data, save_listing, user_listings
from telemetry import capture_user


class PostRefused(Exception):
    """`code` is the API's error code; `extra` joins the error body."""

    def __init__(self, code: str, **extra) -> None:
        super().__init__(code)
        self.code = code
        self.extra = extra


class FieldProblem(Exception):
    def __init__(self, field: str, code: str) -> None:
        super().__init__(field)
        self.field = field
        self.code = code


OWNER_FLAGS = ("wanted_types", "household")
# SubmitError's codes, as the field and code of `validation_failed`.
FIELD_OF = {
    "country": ("country", "invalid_choice"),
    "kind": ("kind", "required"),
    "type": ("property_type", "required"),
    "city": ("city", "required"),
    "description": ("description", "too_short"),
    "price": ("price", "required"),
    "contact": ("phone", "no_contact"),
}


def with_area_name(form: dict) -> dict:
    """A chosen area counts as its name, which review and matching read."""
    if not form.get("area_id") or form.get("area"):
        return form
    area = HomeArea.objects.filter(pk=form["area_id"]).first()
    return {**form, "area": area.canonical} if area else form


def _checked_data(user, form: dict, *, locale: str, countries: list[str], original_text: str = "") -> tuple[dict, str]:
    try:
        data, description = review_data(user, form, locale=locale, countries=countries)
    except SubmitError as exc:
        raise FieldProblem(*FIELD_OF[exc.code]) from exc
    if data["kind"] == ListingKind.WANTED and not data["price_max"]:
        raise FieldProblem("price", "required")
    # A crawled ad's own text may hold its contact; only new text is checked.
    if has_contact(description) and description.strip() != original_text.strip():
        capture_user("ai_review_result", user, source="server", side="housing", properties={"result": "rejected", "reason": "contact_in_text", "by": "rule"})
        raise FieldProblem("description", "contact_in_text")
    return data, description


def _find_duplicate(user, description: str, *, leave_out: int | None = None) -> None:
    """F18#16, F18#21: the same text is live already. Says only whose it is, never a contact."""
    same = HomeListing.objects.filter(post__text_key=text_key(description), status=ListingStatus.ACTIVE, needs_review=False).select_related("post")
    if leave_out:
        same = same.exclude(pk=leave_out)
    found = same.first()
    if found is not None:
        from home.ownership import owns

        owner = "self" if owns(found, user.telegram_user_id) else "other"
        capture_user("ai_review_result", user, source="server", side="housing", properties={"result": "rejected", "reason": "duplicate_listing", "by": "rule", "duplicate_owner": owner, "edit": bool(leave_out)})
        raise PostRefused("duplicate_listing", duplicate_of=found.pk, duplicate_owner=owner)


def _extra_flags(listing: HomeListing, form: dict) -> None:
    flags = dict(listing.flags or {})
    flags.update({key: form[key] for key in OWNER_FLAGS if form.get(key)})
    if flags != (listing.flags or {}):
        listing.flags = flags
        listing.save(update_fields=["flags", "updated_at"])


def _posts_today(user) -> int:
    return HomePost.objects.filter(author_id=user.telegram_user_id, source__username__startswith=APP_PREFIX, posted_at__gte=timezone.now() - timedelta(days=1)).count()


def _result(listing: HomeListing, photos_stored: int) -> dict:
    return {"post_outcome": "held" if listing.needs_review else "live", "listing_id": listing.pk, "photos_stored": photos_stored}


def create_post(user, key: str, form: dict, *, locale: str, countries: list[str], photo_bytes: list[bytes] | None = None) -> tuple[dict, bool]:
    """Returns the result and whether this call made it. The same key answers the first result, and nothing is posted twice.

    `photo_bytes`: photos sent to the housing bot, stored like the form's uploads."""
    done = HomePostKey.objects.filter(user=user, key=key).select_related("listing").first()
    if done is not None:
        return _result(done.listing, done.photos_stored), False
    data, description = _checked_data(user, form, locale=locale, countries=countries)
    if not form.get("force_new"):
        _find_duplicate(user, description)
    rows = uploads.usable(user, form.get("photo_ids") or [])
    try:
        with transaction.atomic():
            # One post at a time for a person, so two at once cannot both pass the cap.
            lock_person(user)
            if _posts_today(user) >= max(1, min(50, conf.row().post_daily_cap or 5)):
                raise PostRefused("post_cap")
            offer = data["kind"] == ListingKind.OFFER
            listing = save_listing(user, form, list(photo_bytes or []) if offer else [], locale=locale, countries=countries)
            if offer and photo_bytes:
                stored = listing.post.photo_count  # attach() would make the photos exactly the (empty) uploads
            else:
                stored = uploads.attach(listing.post, listing.post.source, rows) if offer else 0
            HomePostKey.objects.create(user=user, key=key, listing=listing, photos_stored=stored)
    except IntegrityError:
        # Only a lost race for this key has an answer here; anything else is a real failure.
        done = HomePostKey.objects.select_related("listing").filter(user=user, key=key).first()
        if done is None:
            raise
        return _result(done.listing, done.photos_stored), False
    _extra_flags(listing, form)
    finish_listing(listing, force_new=bool(form.get("force_new")))
    if listing.kind == ListingKind.WANTED:
        sync_wanted_alerts()
    kind = NotificationKind.LISTING_HELD if listing.needs_review else NotificationKind.LISTING_LIVE
    inbox.write(user.pk, kind, listing=listing, title=listing_title(listing))
    return _result(listing, stored), True


def own_listing(user, pk: int) -> HomeListing:
    listing = user_listings(user).exclude(status=ListingStatus.REMOVED).select_related("post__source").filter(pk=pk).first()
    if listing is None:
        raise PostRefused("listing_gone")
    return listing


def owner_state(listing: HomeListing) -> str:
    if listing.status != ListingStatus.ACTIVE:
        return "closed"
    return "in_review" if listing.needs_review else "live"


def mine_row(listing: HomeListing, channels, storage) -> dict:
    reason = closed_reason(listing)
    is_active = listing.status == ListingStatus.ACTIVE
    fresh_since = listing.last_confirmed_at or listing.first_seen
    return {
        "id": listing.pk,
        "kind": listing.kind,
        "owner_state": owner_state(listing),
        "closed_reason": reason,
        "reject_code": None,
        "card": public_card(listing, channels, storage=storage),
        "edit_pending": HomeListingEdit.objects.filter(listing=listing).exists(),
        "can_edit": is_active,
        "can_renew": listing.status == ListingStatus.EXPIRED,
        "can_restore": listing.status == ListingStatus.RENTED and reason == "owner",
        "owner_check": is_active and not listing.needs_review and fresh_since < timezone.now() - timedelta(days=conf.confirm_after_days()),
        "updated_at": listing.updated_at,
        "stats": owner_stats(listing),
        "crawled": not listing.post.source.username.startswith(APP_PREFIX),
    }


def _money(value) -> float | None:
    return None if value is None else float(value)


def form_of(user, listing: HomeListing) -> dict:
    flags = listing.flags if isinstance(listing.flags, dict) else {}
    photos = uploads.for_post(user, listing.post)
    return {
        "kind": listing.kind,
        "property_type": listing.property_type,
        "wanted_types": flags.get("wanted_types") or [],
        "rental_mode": listing.rental_mode,
        "country": listing.country,
        "city": listing.city,
        "area": listing.area,
        "area_id": listing.home_area_id,
        "price": _money(listing.price_amount if listing.kind == ListingKind.OFFER else listing.price_max),
        "price_period": listing.price_period,
        "price_basis": listing.price_basis,
        "bills_included": listing.bills_included,
        "deposit": _money(listing.deposit_amount),
        "size_m2": _money(listing.size_m2),
        "rooms": _money(listing.rooms),
        "furnished": listing.furnished,
        "registration": listing.registration,
        "available_from": listing.available_from,
        "available_to": listing.available_to,
        "min_term_months": listing.min_term_months,
        "roommate_gender": listing.roommate_gender,
        "household": flags.get("household") or "",
        "description": listing.post.text,
        "phone": listing.contact_phone,
        "photos": [uploads.payload(row) for row in photos],
        "updated_at": listing.updated_at,
    }


def _same_time(left, right) -> bool:
    return abs((left - right).total_seconds()) < 0.001


def _uuid_strings(rows) -> list[str]:
    return [str(row.pk) for row in rows]


def _write_edit(listing: HomeListing, data: dict, description: str, photo_ids: list, user) -> None:
    """The rows of the new version. The caller holds the listing's lock and has checked it is active."""
    post = listing.post
    post.text = description
    post.text_key = text_key(description)
    post.review_json = data
    fields, _notes = normalize_listing(post, data)
    if fields is None:
        raise FieldProblem("city", "required")
    post.save(update_fields=["text", "text_key", "review_json"])
    flags = {key: value for key, value in (listing.flags or {}).items() if key in OWNER_FLAGS}
    flags = {**(fields.pop("flags", None) or {}), **flags, **{key: data[key] for key in OWNER_FLAGS if data.get(key)}}
    # Only what this edit changes, so anything else on the row is not written over.
    changed = [name for name, value in fields.items() if getattr(listing, name) != value]
    for name in changed:
        setattr(listing, name, fields[name])
    listing.flags = flags
    listing.needs_review = False
    listing.review_note = ""
    listing.save(update_fields=[*changed, "flags", "needs_review", "review_note", "updated_at"])
    uploads.attach(post, post.source, uploads.usable(user, photo_ids))
    HomeListingEdit.objects.filter(listing=listing).delete()


def _after_edit(listing: HomeListing) -> None:
    """These can reach storage and Telegram, so they run after the lock is gone."""
    link_area(listing)
    after_submit(listing)
    if listing.published_at:
        status_posts.apply(listing, StatusPost.RESTORED)


def _locked(pk: int) -> HomeListing:
    return HomeListing.objects.select_for_update(of=("self",)).select_related("post__source").get(pk=pk)


def apply_edit(listing: HomeListing, data: dict, description: str, photo_ids: list, user) -> None:
    """An admin approved a held edit. A listing closed since then stays closed: `listing_closed`."""
    with transaction.atomic():
        current = _locked(listing.pk)
        is_open = current.status == ListingStatus.ACTIVE
        if is_open:
            _write_edit(current, data, description, photo_ids, user)
    if not is_open:
        # Out here, since an error raised inside the block would roll this back too.
        HomeListingEdit.objects.filter(listing=current).delete()
        raise PostRefused("listing_closed")
    _after_edit(current)


def edit_listing(user, listing: HomeListing, form: dict, *, locale: str, countries: list[str]) -> dict:
    original = listing.post.text if not listing.post.source.username.startswith(APP_PREFIX) else ""
    data, description = _checked_data(user, form, locale=locale, countries=countries, original_text=original)
    # Review ignores these two; they join the row only once the edit goes live.
    data = {**data, **{key: form[key] for key in OWNER_FLAGS if form.get(key)}}
    with transaction.atomic():
        current = _locked(listing.pk)
        if current.status != ListingStatus.ACTIVE:
            raise PostRefused("listing_closed")
        # The form writes a summary in the person's language only; the other one stays, or review would hold the edit for having none.
        data = {**data, "summary_fa": data["summary_fa"] or current.summary_fa, "summary_en": data["summary_en"] or current.summary_en}
        pending = HomeListingEdit.objects.filter(listing=current).first()
        # What was already saved answers again, so a lost response can be retried past `edit_conflict`.
        if pending is not None and pending.form == data:
            return {"post_outcome": "held", "listing_id": current.pk, "photos_stored": len(pending.photo_ids)}
        if current.post.review_json == data and current.post.text == description:
            return _result(current, current.post.photo_count or 0)
        if not _same_time(form["base_updated_at"], current.updated_at):
            raise PostRefused("edit_conflict")
        if not form.get("force_new"):
            _find_duplicate(user, description, leave_out=current.pk)
        rows = uploads.usable(user, form.get("photo_ids") or [])
        draft = HomePost(source=current.post.source, text=description, posted_at=current.post.posted_at, author_id=current.post.author_id)
        fields, notes = normalize_listing(draft, data)
        if fields is None:
            raise FieldProblem("city", "required")
        if notes:
            HomeListingEdit.objects.update_or_create(listing=current, defaults={"form": data, "text": description, "photo_ids": _uuid_strings(rows), "review_note": "; ".join(notes)[:255]})
            held = True
        else:
            _write_edit(current, data, description, [row.pk for row in rows], user)
            held = False
    if held:
        inbox.write(user.pk, NotificationKind.LISTING_HELD, listing=current, title=listing_title(current))
        return {"post_outcome": "held", "listing_id": current.pk, "photos_stored": len(rows)}
    _after_edit(current)
    return _result(current, len(rows))


def change_status(listing: HomeListing, action: str) -> None:
    """Sending what is already true changes nothing, so a retry is safe."""
    if action == "close":
        if listing.status == ListingStatus.RENTED:
            return
        if not set_rented(listing):
            raise PostRefused("status_not_allowed")
        return
    if listing.status == ListingStatus.ACTIVE:
        return
    if action == "restore":
        if listing.status != ListingStatus.RENTED or closed_reason(listing) != "owner":
            raise PostRefused("status_not_allowed")
        listing.rented_at = None
        listing.save(update_fields=["rented_at", "updated_at"])
        restore(listing)
        return
    if listing.status != ListingStatus.EXPIRED:
        raise PostRefused("status_not_allowed")
    now = timezone.now()
    listing.status = ListingStatus.ACTIVE
    listing.last_seen = now
    listing.last_confirmed_at = now
    listing.save(update_fields=["status", "last_seen", "last_confirmed_at", "updated_at"])
    if listing.status_post:
        status_posts.apply(listing, StatusPost.RESTORED)


def delete_listing(user, pk: int) -> None:
    """Gone for everyone: the old link answers `listing_gone`, and the channel post changes to closed.

    Its contact, key and photos go too, here and in what review kept. Duplicate checks look at every status, so
    otherwise a new post by the same person would merge into this row and vanish with it.
    """
    if not user_listings(user).exclude(status=ListingStatus.REMOVED).filter(pk=pk).exists():
        return
    with transaction.atomic():
        listing = _locked(pk)
        if listing.status == ListingStatus.REMOVED:
            return
        was_open = listing.status == ListingStatus.ACTIVE
        listing.status = ListingStatus.REMOVED
        listing.dedup_key = ""
        listing.contact_telegram = ""
        listing.contact_phone = ""
        listing.save(update_fields=["status", "dedup_key", "contact_telegram", "contact_phone", "updated_at"])
        post = listing.post
        post.review_json = {key: value for key, value in (post.review_json or {}).items() if key not in {"contact_telegram", "contact_phone"}}
        post.author_username = ""
        post.poster_username = ""
        post.save(update_fields=["review_json", "author_username", "poster_username"])
        HomeListingEdit.objects.filter(listing=listing).delete()
        uploads.attach(post, post.source, [])
    if was_open:
        inbox.listing_closed(listing)
    if listing.published_at:
        status_posts.apply(listing, StatusPost.RENTED)
    if listing.kind == ListingKind.WANTED:
        sync_wanted_alerts()
