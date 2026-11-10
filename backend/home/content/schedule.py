"""The 15-minute tick on bigde-vps (spec §3): per chat, what is due in its local time and not sent yet.

Idempotent by `home_contentpost (kind, chat_id, period_key)`: the row is claimed before the work starts, so two ticks never send twice.
A missed slot is still sent up to 3 hours late, then dropped.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone

from home import bot
from home.content import daily as daily_view
from home.content import data, render, send, voice
from home.content.text import city_slug
from home.models import ContentKind, HomeChannel, HomeChannelCount, HomeContentPost, HomeSettings

logger = logging.getLogger(__name__)

TZ = {"toronto": "America/Toronto", "montreal": "America/Toronto", "vancouver": "America/Vancouver"}
DEFAULT_TZ = "Europe/Berlin"
DAILY_HOUR = 20
LATE = timedelta(hours=3)
STALE_CLAIM = timedelta(minutes=40)
MAX_TRIES = 3


def tz_of(channel: HomeChannel) -> ZoneInfo:
    if not channel.city:
        return ZoneInfo("America/Toronto" if channel.country == "CA" else DEFAULT_TZ)
    return ZoneInfo(TZ.get(city_slug(channel.city), DEFAULT_TZ))


def music() -> Path:
    return Path(os.environ.get("CONTENT_MUSIC") or "/content/music/daily_theme.mp3")


def work_dir() -> Path:
    return Path(os.environ.get("CONTENT_DIR") or tempfile.gettempdir()) / "content"


def city_channels() -> list[HomeChannel]:
    return list(HomeChannel.objects.filter(enabled=True).exclude(city="").exclude(chat_id__isnull=True).order_by("id"))


def daily_due(channel: HomeChannel, now: datetime) -> str | None:
    """The period key when the channel's 20:00 local slot has passed less than 3 h ago."""
    local = now.astimezone(tz_of(channel))
    slot = local.replace(hour=DAILY_HOUR, minute=0, second=0, microsecond=0)
    return f"{local:%Y%m%d}" if slot <= local < slot + LATE else None


def claim(kind: str, channel: HomeChannel, period: str, now: datetime) -> HomeContentPost | None:
    """The row to work on, or None when it is sent, skipped, given up, or another tick holds it."""
    try:
        with transaction.atomic():
            return HomeContentPost.objects.create(kind=kind, chat_id=channel.chat_id, channel=channel, period_key=period, payload={"tries": 1, "claimed_at": now.isoformat()})
    except IntegrityError:
        pass
    with transaction.atomic():
        row = HomeContentPost.objects.select_for_update().get(kind=kind, chat_id=channel.chat_id, period_key=period)
        tries = int((row.payload or {}).get("tries") or 0)
        if row.sent_at or (row.payload or {}).get("skipped") or tries >= MAX_TRIES:
            return None
        if not row.error and str((row.payload or {}).get("claimed_at", "")) > (now - STALE_CLAIM).isoformat():
            return None  # another tick is working on it
        row.payload = {**(row.payload or {}), "tries": tries + 1, "claimed_at": now.isoformat()}
        row.error = ""
        row.save(update_fields=["payload", "error"])
        return row


def run_daily(channel: HomeChannel, row: HomeContentPost, now: datetime, *, chat=None, channels=None) -> HomeContentPost:
    """Numbers -> album (video with voice + music, stats image) -> send. Video failure falls back to the still card; never a skipped post."""
    local_day = now.astimezone(tz_of(channel)).date()
    numbers = data.daily(channel, now, channels)
    view = daily_view.build(numbers, local_day, channel.username)
    payload = {**(row.payload or {}), **view.payload, "local_day": local_day.isoformat()}
    if numbers.skip:
        row.payload = {**payload, "skipped": True}
        row.error = f"skipped: {numbers.n} new listings (< {data.MIN_DAILY})"
        row.save(update_fields=["payload", "error"])
        return row
    out = work_dir() / f"{city_slug(channel.city)}_{row.period_key}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    target = chat if chat is not None else channel.chat_id
    stats_png = render.still("stats.html", view.stats, out, "stats")
    try:
        silent, card_png, cover, thumb = render.video("daily.html", view.card, out, "card", daily_view.ANIM_SECONDS)
        clip, check = voice.checked_voice(view.script, view.expected, out / "voice.mp3")
        payload["voice"] = check
        final = out / "daily.mp4"
        seconds = voice.mix(silent, clip, music(), final, daily_view.ANIM_SECONDS)
        ids = send.album(target, final, cover, thumb, seconds, stats_png, view.caption)
        payload["format"] = "album" if clip else "album_music_only"
        row.media_path = str(final)
    except bot.BotError:
        raise
    except Exception as exc:  # noqa: BLE001 - render or audio broke: still card, same caption
        logger.exception("daily video failed for %s", channel.username)
        payload["video_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        card_png = out / "card.png"
        if not card_png.exists():
            card_png = render.still("daily.html", view.card, out, "card")
        ids = send.photos(target, [card_png, stats_png], view.caption)
        payload["format"] = "photos"
    row.message_id, row.message_ids, row.sent_at = ids[0], ids, timezone.now()
    row.payload, row.error = payload, ""
    row.save(update_fields=["message_id", "message_ids", "sent_at", "payload", "error", "media_path"])
    return row


def switch_off_single_posts() -> bool:
    """Spec §1: per-listing channel posts stop in the deploy that sends the first automatic daily."""
    settings_row = HomeSettings.load()
    if not settings_row.publish_enabled:
        return False
    settings_row.publish_enabled = False
    settings_row.save(update_fields=["publish_enabled"])
    logger.warning("publish_enabled switched off: the first automatic daily digest went out")
    return True


def count_members(now: datetime) -> int:
    """Members of every housing channel, once a day (spec §6, §7 baseline)."""
    stored = 0
    for channel in HomeChannel.objects.filter(enabled=True).exclude(chat_id__isnull=True):
        if HomeChannelCount.objects.filter(chat_id=channel.chat_id, day=now.date()).exists():
            continue
        try:
            members = int(bot.call("getChatMemberCount", {"chat_id": channel.chat_id}))
        except bot.BotError:
            continue
        HomeChannelCount.objects.get_or_create(chat_id=channel.chat_id, day=now.date(), defaults={"members": members})
        stored += 1
    return stored


def tick(now: datetime | None = None, *, kinds: set[str] | None = None) -> dict:
    now = now or timezone.now()
    kinds = kinds or {ContentKind.DAILY}
    report: dict = {"at": now.isoformat(), "sent": [], "skipped": [], "failed": []}
    channels = city_channels()
    if ContentKind.DAILY in kinds:
        for channel in channels:
            period = daily_due(channel, now)
            if not period:
                continue
            row = claim(ContentKind.DAILY, channel, period, now)
            if row is None:
                continue
            try:
                row = run_daily(channel, row, now, channels=channels)
            except Exception as exc:  # noqa: BLE001 - one city must not stop the others
                row.error = f"{type(exc).__name__}: {str(exc)[:400]}"
                row.payload = {**(row.payload or {}), "trace": traceback.format_exc()[-1500:]}
                row.save(update_fields=["error", "payload"])
                report["failed"].append([channel.username, row.error])
                continue
            if row.sent_at:
                report["sent"].append([channel.username, f"https://t.me/{channel.username}/{row.message_id}"])
                if switch_off_single_posts():
                    report["publish_enabled"] = False
            else:
                report["skipped"].append([channel.username, row.error])
    try:
        report["member_counts"] = count_members(now)
    except Exception as exc:  # noqa: BLE001
        report["member_counts"] = f"failed: {exc}"
    return report
