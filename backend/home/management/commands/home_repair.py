"""Correct listings hurt before a fix went out. Prints counts only, never a contact. Writes only with --apply. Calls no model.

contacts: a real contact the old `handle_in_text` dropped because a Persian letter sat right before the @ («تماس@ali»).
reposts: an expired listing a person reposted while we kept it dead. It comes back as a new repost would bring it back:
no new row, and no new channel post.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from home.contact_text import handle_in_text
from home.freshness import APP_PREFIX
from home.models import HomeListing, HomePost, ListingStatus, PostStatus
from home.review import first_handle, revive_from_repost, why_not_revived

GLUED_HANDLE = r"[؀-ۿ][@＠][A-Za-z]"
DEFAULT_DAYS = 14


def wanted_contact(listing: HomeListing) -> tuple[str, bool]:
    """The handle the review would keep today, and whether the model named it, so it may replace another handle."""
    post, source = listing.post, listing.post.source.username
    raw = post.review_json if isinstance(post.review_json, dict) else {}
    said = str(raw.get("contact_telegram") or "").strip().lstrip("@")[:64]
    if said and said.lower() != source.lower() and handle_in_text(said, post.text):
        return said, True
    return first_handle(post.text, exclude=source), False


def repair_contacts(*, apply: bool) -> dict:
    rows = (
        HomeListing.objects.filter(status__in=[ListingStatus.ACTIVE, ListingStatus.EXPIRED], post__text__regex=GLUED_HANDLE)
        .exclude(post__source__username__startswith=APP_PREFIX)
        .select_related("post__source")
    )
    out = {"apply": apply, "glued_in_text": 0, "would_fill": 0, "would_replace": 0, "written": 0}
    for listing in rows.iterator(chunk_size=500):
        out["glued_in_text"] += 1
        wanted, named = wanted_contact(listing)
        current = listing.contact_telegram
        if not wanted or wanted.casefold() == current.casefold() or (current and not named):
            continue
        out["would_replace" if current else "would_fill"] += 1
        if apply:
            out["written"] += HomeListing.objects.filter(pk=listing.pk, contact_telegram=current).update(contact_telegram=wanted, updated_at=timezone.now())
    return out


def repair_reposts(*, days: int, apply: bool) -> dict:
    since = timezone.now() - timedelta(days=days)
    posts = HomePost.objects.filter(status=PostStatus.REJECTED, reject_reason="repost", posted_at__gte=since, review_json__has_key="repost_of").values_list("review_json", "posted_at", "text_key")
    latest: dict[int, object] = {}
    scanned = 0
    texts = set()
    for raw, posted_at, key in posts.iterator(chunk_size=1000):
        scanned += 1
        texts.add(key)
        original = raw.get("repost_of")
        if original and (original not in latest or posted_at > latest[original]):
            latest[original] = posted_at
    behind: dict[int, tuple[HomeListing, object]] = {}
    for listing in HomeListing.objects.filter(post_id__in=list(latest)).select_related("post__source", "canonical__post__source"):
        target = listing.canonical or listing
        when = latest[listing.post_id]
        if target.pk not in behind or when > behind[target.pk][1]:
            behind[target.pk] = (target, when)
    reasons: Counter = Counter()
    eligible_texts: dict[str, int] = {}
    out = {"apply": apply, "days": days, "reposts": scanned, "distinct_texts": len(texts), "listings_behind": len(behind), "would_revive": 0, "never_on_a_channel": 0, "revived": 0}
    for target, when in behind.values():
        why = why_not_revived(target, when)
        if not why and target.post.text_key in eligible_texts:
            why = "live_copy_exists"
        if why:
            reasons[why] += 1
            continue
        eligible_texts[target.post.text_key] = target.pk
        out["would_revive"] += 1
        out["never_on_a_channel"] += 0 if target.published_at else 1
        if apply and revive_from_repost(target, when):
            out["revived"] += 1
    out["not_revived"] = dict(reasons)
    return out


class Command(BaseCommand):
    help = "Correct listings hurt before a fix: dropped glued contacts, or expired listings that were reposted. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("what", choices=["contacts", "reposts"])
        parser.add_argument("--apply", action="store_true", help="Write. Without it, print what would change.")
        parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="reposts: how far back to read.")

    def handle(self, *args, **options):
        if options["what"] == "contacts":
            report = repair_contacts(apply=options["apply"])
        else:
            report = repair_reposts(days=options["days"], apply=options["apply"])
        self.stdout.write(json.dumps(report))
