"""Numbers for the Home run page: what is configured, what is waiting, what each source did."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from ai.llm import llm_status
from home import conf
from home.job import pending_posts
from home.publish import city_publishable, publishable
from home.models import (
    HomeChannel,
    HomeListing,
    HomeMedia,
    HomePost,
    HomePublication,
    HomeSource,
    ListingStatus,
    MediaStatus,
    PostStatus,
)
from home.storage import get_storage
from home.vision import vision_candidates, vision_enabled
from market.telegram_history import history_configured


def home_status() -> dict:
    current = conf.row()
    values = conf.storage_values()
    missing = conf.missing_storage_values()
    posts = dict(HomePost.objects.values_list("status").annotate(n=Count("id")))
    listings = HomeListing.objects.aggregate(
        active=Count("id", filter=Q(status=ListingStatus.ACTIVE)),
        duplicate=Count("id", filter=Q(status=ListingStatus.DUPLICATE)),
        review=Count("id", filter=Q(needs_review=True, status=ListingStatus.ACTIVE)),
        offers=Count("id", filter=Q(kind="offer", status=ListingStatus.ACTIVE)),
        wanted=Count("id", filter=Q(kind="wanted", status=ListingStatus.ACTIVE)),
    )
    media = dict(HomeMedia.objects.values_list("status").annotate(n=Count("id")))
    sources = []
    for source in sources_with_recent_counts(
        HomeSource.objects.annotate(
            post_count=Count("posts", distinct=True),
            accepted_count=Count("posts", filter=Q(posts__status=PostStatus.ACCEPTED), distinct=True),
        )
    ):
        sources.append(
            {
                "username": source.username,
                "country": source.country,
                "enabled": source.enabled,
                "invite_saved": bool(source.invite_link),
                "posts": source.post_count,
                "accepted": source.accepted_count,
                "posts_7d": source.posts_recent,
                "accepted_7d": source.accepted_recent,
                "last_run_at": source.last_run_at,
                "last_error": source.last_error,
                "backfill_complete": source.backfill_complete,
            }
        )
    return {
        "enabled": conf.home_enabled(),
        "switches": {
            "ingest": conf.step_enabled("ingest"),
            "review": conf.step_enabled("review"),
            "media": conf.step_enabled("media"),
            "vision": conf.step_enabled("vision"),
            "publish": conf.step_enabled("publish"),
        },
        "lookback_days": conf.lookback_days(),
        "review_batch": conf.review_batch(),
        "media_batch": conf.media_batch(),
        "llm": llm_status(),
        "telegram": history_configured(),
        "storage": {
            "backend": values["backend"] or "off",
            "ready": get_storage() is not None,
            "missing": missing,
            "secret_saved": bool(current.r2_secret_access_key),
        },
        "vision": {"model": conf.vision_model(), "ready": vision_enabled(), "candidates": vision_candidates().count()},
        "posts": {
            "waiting": pending_posts().count(),
            "accepted": posts.get(PostStatus.ACCEPTED, 0),
            "rejected": posts.get(PostStatus.REJECTED, 0),
            "errors": posts.get(PostStatus.ERROR, 0),
            "reposts": HomePost.objects.filter(status=PostStatus.REJECTED, reject_reason="repost").count(),
        },
        "listings": listings,
        "publish": {
            "token": bool(conf.bot_token()),
            "enabled": current.publish_enabled,
            "blocker": conf.publish_blocker(),
            "waiting": publishable().count(),
            "published": HomeListing.objects.filter(published_at__isnull=False, channel__isnull=False).count(),
            "published_city": HomePublication.objects.filter(published_at__isnull=False).count(),
            "waiting_city": len(city_publishable()),
            "channels": [
                {
                    "country": channel.country,
                    "city": channel.city,
                    "username": channel.username,
                    "enabled": channel.enabled,
                    "verified": bool(channel.verified_at),
                    "error": channel.last_error,
                    "posts_24h": channel.posts_24h,
                    "posts_7d": channel.posts_7d,
                }
                for channel in channels_with_recent_counts()
            ],
        },
        "media": {
            "pending": media.get(MediaStatus.PENDING, 0),
            "stored": media.get(MediaStatus.STORED, 0),
            "failed": media.get(MediaStatus.FAILED, 0),
        },
        "sources": sources,
    }


def sources_with_recent_counts(queryset=None, days: int = 7):
    """Sources annotated with posts read and posts accepted in the last `days` days (by post time)."""
    since = timezone.now() - timedelta(days=days)
    queryset = HomeSource.objects.all() if queryset is None else queryset
    return queryset.annotate(
        posts_recent=Count("posts", filter=Q(posts__posted_at__gte=since), distinct=True),
        accepted_recent=Count(
            "posts", filter=Q(posts__posted_at__gte=since, posts__status=PostStatus.ACCEPTED), distinct=True
        ),
    )


def _published_since(model, since):
    counted = (
        model.objects.filter(channel=OuterRef("pk"), published_at__gte=since)
        .order_by()
        .values("channel")
        .annotate(n=Count("pk"))
        .values("n")
    )
    return Coalesce(Subquery(counted, output_field=IntegerField()), 0)


def channels_with_recent_counts(queryset=None):
    """Channels annotated with posts in the last 24 hours and 7 days.

    A city channel's posts are HomePublication rows; a country channel's are the listings it
    carries (HomeListing.channel). posts_24h / posts_7d add both, so either kind shows its number.
    """
    queryset = HomeChannel.objects.all() if queryset is None else queryset
    now = timezone.now()
    day, week = now - timedelta(days=1), now - timedelta(days=7)
    return queryset.annotate(
        posts_24h=_published_since(HomePublication, day) + _published_since(HomeListing, day),
        posts_7d=_published_since(HomePublication, week) + _published_since(HomeListing, week),
    )
