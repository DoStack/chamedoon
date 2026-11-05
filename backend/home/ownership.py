"""Whose listing is this. The one definition; everything that asks goes through here.

- A listing posted in the app belongs to its author.
- A crawled listing belongs to its poster only when Telegram told us (`poster_read_at`), the poster is a person, and
  they wrote it (`sender`, or `forward_author` of a forward). Someone who only forwarded it (`forwarder`) is not the owner.
"""

from __future__ import annotations

from django.db.models import Q

from home.models import HomeListing, HomePost
from market.poster import KIND_PERSON, VIA_FORWARD_AUTHOR, VIA_SENDER

APP_PREFIX = "app_"
OWNER_VIAS = (VIA_SENDER, VIA_FORWARD_AUTHOR)


def owner_telegram_id(post: HomePost) -> int | None:
    if post.source.username.startswith(APP_PREFIX):
        return post.author_id or None
    if post.poster_kind != KIND_PERSON or post.poster_read_at is None or post.poster_via not in OWNER_VIAS:
        return None
    return post.poster_user_id if post.poster_user_id and post.poster_user_id > 0 else None


def owns(listing: HomeListing, telegram_id: int | None) -> bool:
    return bool(telegram_id) and owner_telegram_id(listing.post) == int(telegram_id)


def owned_q(telegram_id: int | None) -> Q:
    """The same rule for a query on HomeListing."""
    if not telegram_id:
        return Q(pk__in=[])
    return Q(post__source__username__startswith=APP_PREFIX, post__author_id=telegram_id) | (
        ~Q(post__source__username__startswith=APP_PREFIX)
        & Q(post__poster_user_id=telegram_id, post__poster_kind=KIND_PERSON, post__poster_read_at__isnull=False, post__poster_via__in=OWNER_VIAS)
    )


def owned_listings(telegram_id: int | None):
    return HomeListing.objects.filter(owned_q(telegram_id)).select_related("post", "post__source")
