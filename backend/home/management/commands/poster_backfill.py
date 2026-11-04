"""Fill in who posted every post already crawled. Makes no AI call and sends no message. Safe to stop and to run again.

Stages, in the order they should run:

  --stored    The columns kept at crawl time say who sent a post and, with Telegram's rule that only a bot's username ends
              in "bot", what they are. Touches only posts whose poster is not worked out yet.
  --contacts  A crawled listing's contact_telegram must be written in its post. What a model made up (`unknown`, a display
              name, the sender copied) is cleared, and the first handle in the text takes its place. A contact that is in
              the text never changes. The model's answer stays in `review_json`, and --log keeps old and new.
  --reread    Ask Telegram only for what the columns cannot say: forwards, and posts with no sender stored. Listings first,
              in small throttled batches, never joining anything. `poster_read_at` marks what is done, so a stopped run resumes.

`--report` prints the numbers per source type at any time.
"""

from __future__ import annotations

import json
import logging
import re
import time

from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from home.contact_text import handle_in_text
from home.models import HomeListing, HomePost, ListingStatus
from home.review import first_handle
from home.telegram import HomeHistoryReader
from market.models import MarketPost
from market.poster import (
    KIND_ANONYMOUS,
    KIND_BOT,
    KIND_CHANNEL,
    KIND_PERSON,
    KIND_UNKNOWN,
    POSTER_FIELDS,
    STAND_IN_IDS,
    VIA_SENDER,
    poster_contact,
    read_poster,
)

logger = logging.getLogger(__name__)

BATCH = 100
RETRIES = 3
_PUBLIC_LINK = re.compile(r"t\.me/([A-Za-z]\w{3,31})/\d+")


class _Reader(HomeHistoryReader):
    """Reads what the account can already see. Unlike a crawl it never joins a chat."""

    def _invites(self) -> dict[str, str]:
        return {}


def open_reader() -> _Reader:
    reader = _Reader()
    reader.open()
    # FloodWait must reach `read_batch`, which waits it out in full. Below this the client would sleep inside the page timeout.
    reader._client.flood_sleep_threshold = 0
    return reader


def fill_from_stored() -> dict:
    """The poster of each post, from the sender columns. Never overwrites what is already worked out."""
    posts = HomePost.objects.exclude(source__username__startswith="app_").filter(poster_kind="")

    def fill(rows, kind: str) -> int:
        return rows.update(
            poster_user_id=F("author_id"),
            poster_username=F("author_username"),
            poster_name=F("author_name"),
            poster_kind=kind,
            poster_via=VIA_SENDER,
        )

    return {
        KIND_ANONYMOUS: fill(posts.filter(author_id__in=STAND_IN_IDS), KIND_ANONYMOUS),
        KIND_CHANNEL: fill(posts.filter(author_id__lt=0), KIND_CHANNEL),
        KIND_BOT: fill(posts.filter(author_id__gt=0, author_username__iendswith="bot"), KIND_BOT),
        KIND_PERSON: fill(posts.filter(author_id__gt=0), KIND_PERSON),
        "no_sender_stored": posts.count(),
    }


def clean_contacts(log_path: str = "") -> dict:
    """Keep only what the post's text gives. Returns what happened, so the owner sees how many were not real contacts."""
    listings = (
        HomeListing.objects.exclude(contact_telegram="")
        .exclude(post__source__username__startswith="app_")
        .select_related("post__source")
        .only("contact_telegram", "post__text", "post__source__username")
    )
    counts = {"kept": 0, "cleared": 0, "replaced": 0}
    changes = []
    for listing in listings.iterator(chunk_size=500):
        source = listing.post.source.username
        old = listing.contact_telegram
        if handle_in_text(old, listing.post.text) and old.lstrip("@").casefold() != source.casefold():
            counts["kept"] += 1
            continue
        new = first_handle(listing.post.text, exclude=source)
        HomeListing.objects.filter(pk=listing.pk, contact_telegram=old).update(contact_telegram=new)
        counts["replaced" if new else "cleared"] += 1
        changes.append({"listing": listing.pk, "old": old, "new": new})
    if log_path and changes:
        with open(log_path, "a", encoding="utf-8") as log:
            for row in changes:
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
    return counts


def read_batch(reader_box: dict, username: str, ids: list[int], *, sleep=time.sleep) -> list:
    """One batch from Telegram. Waits out FloodWait in full, reconnects after a dropped line. Raises what cannot be retried."""
    from telethon.errors import FloodWaitError

    attempt = 0
    while True:
        try:
            return reader_box["reader"].messages(username, ids)
        except FloodWaitError as exc:
            logger.warning("poster_backfill flood wait %s s", exc.seconds)
            sleep(exc.seconds + 5)
        except (TimeoutError, ConnectionError, OSError) as exc:
            attempt += 1
            if attempt >= RETRIES:
                raise
            logger.warning("poster_backfill retry %s: %s", username, type(exc).__name__)
            sleep(10 * attempt)
            if isinstance(exc, ConnectionError) and reader_box.get("reopen"):
                reader_box["reader"].close()
                reader_box["reader"] = reader_box["reopen"]()


def apply_message(post, message, *, chat_id: int | None) -> str:
    """Updates the post in memory. A message gone from Telegram, or one that does not say, keeps what the columns gave."""
    post.poster_read_at = timezone.now()
    if message is None:
        return "gone"
    row = read_poster(message, chat_id=chat_id)
    if row["poster_kind"] == KIND_UNKNOWN and post.poster_kind:
        return "kept"
    for name in POSTER_FIELDS:
        setattr(post, name, row[name])
    return row["poster_via"]


def reread(model, rows, username: str, reader_box: dict, *, delay: float, sleep=time.sleep) -> dict:
    counts: dict[str, int] = {}
    pks = list(rows.values_list("pk", flat=True))
    for start in range(0, len(pks), BATCH):
        posts = list(model.objects.filter(pk__in=pks[start : start + BATCH]))
        messages = read_batch(reader_box, username, [post.telegram_message_id for post in posts], sleep=sleep)
        chat_id = reader_box["reader"].chat_id(username)
        for post, message in zip(posts, messages):
            outcome = apply_message(post, message, chat_id=chat_id)
            counts[outcome] = counts.get(outcome, 0) + 1
        model.objects.bulk_update(posts, POSTER_FIELDS)
        sleep(delay)
    return counts


def _not_readable(exc: Exception) -> bool:
    from telethon.errors import RPCError

    return isinstance(exc, (LookupError, RPCError, ValueError, TimeoutError, ConnectionError, OSError))


def reread_housing(reader_box: dict, *, delay: float, listings_only: bool, out, sleep=time.sleep) -> dict:
    from django.db.models import Count

    unread = HomePost.objects.exclude(source__username__startswith="app_").filter(poster_read_at__isnull=True)
    stages = [("listings", unread.filter(listing__isnull=False))]
    if not listings_only:
        stages.append(("posts", unread))
    totals: dict[str, int] = {}
    failed: dict[str, str] = {}
    for stage, rows in stages:
        sources = rows.values_list("source__username", "source_id").annotate(left=Count("pk")).order_by("-left")
        for username, source_id, left in list(sources):
            if username in failed:
                continue
            try:
                counts = reread(HomePost, rows.filter(source_id=source_id), username, reader_box, delay=delay, sleep=sleep)
            except Exception as exc:  # noqa: BLE001 - one source failing must not stop the others; a later run resumes it
                if not _not_readable(exc):
                    raise
                failed[username] = f"{type(exc).__name__}: {str(exc)[:120]}"
                out.write(f"{stage} {username}: not readable, {failed[username]}")
                continue
            out.write(f"{stage} {username}: {left} {counts}")
            for name, number in counts.items():
                totals[name] = totals.get(name, 0) + number
    return {"read": totals, "not_readable": failed}


def reread_market(reader_box: dict, *, delay: float, out, sleep=time.sleep) -> dict:
    """Courier posts carry no sender id at all, so only Telegram can say. Only sources with a public link can be asked."""
    unread = MarketPost.objects.filter(poster_read_at__isnull=True)
    totals: dict[str, int] = {}
    failed: dict[str, str] = {}
    for channel in list(unread.order_by().values_list("channel_username", flat=True).distinct()):
        rows = unread.filter(channel_username=channel)
        sample = rows.exclude(source_url="").values_list("source_url", flat=True).first() or ""
        match = _PUBLIC_LINK.search(sample)
        if not match or "/c/" in sample:
            failed[channel] = "no public link"
            out.write(f"market {channel}: no public link, skipped")
            continue
        try:
            counts = reread(MarketPost, rows, match.group(1), reader_box, delay=delay, sleep=sleep)
        except Exception as exc:  # noqa: BLE001 - as above
            if not _not_readable(exc):
                raise
            failed[channel] = f"{type(exc).__name__}: {str(exc)[:120]}"
            out.write(f"market {channel}: not readable, {failed[channel]}")
            continue
        out.write(f"market {channel}: {counts}")
        for name, number in counts.items():
            totals[name] = totals.get(name, 0) + number
    return {"read": totals, "not_readable": failed}


def report() -> dict:
    """Active crawled listings per source type: contact from the text, from the poster, or none and why."""
    rows = (
        HomeListing.objects.filter(status=ListingStatus.ACTIVE, needs_review=False)
        .exclude(post__source__username__startswith="app_")
        .select_related("post__source")
        .only(
            "contact_telegram",
            "contact_phone",
            "post__poster_kind",
            "post__poster_username",
            "post__poster_via",
            "post__source_url",
            "post__source__username",
            "post__source__kind",
        )
    )
    result: dict[str, dict[str, int]] = {}
    for listing in rows.iterator(chunk_size=500):
        post = listing.post
        bucket = result.setdefault(post.source.kind, {})
        if listing.contact_telegram or listing.contact_phone:
            name = "from_text"
        elif poster_contact(post, source_username=post.source.username):
            name = "from_poster_forward_author" if post.poster_via == "forward_author" else "from_poster"
        elif post.poster_kind == KIND_PERSON:
            name = "none_no_username"
        else:
            name = f"none_{post.poster_kind or 'not_read'}"
        bucket[name] = bucket.get(name, 0) + 1
        bucket["active"] = bucket.get("active", 0) + 1
    return result


def poster_table() -> dict:
    return {
        "home_posts": HomePost.objects.count(),
        "home_posts_worked_out": HomePost.objects.exclude(poster_kind="").count(),
        "home_posts_read": HomePost.objects.exclude(poster_read_at=None).count(),
        "market_posts": MarketPost.objects.count(),
        "market_posts_read": MarketPost.objects.exclude(poster_read_at=None).count(),
    }


class Command(BaseCommand):
    help = "Fill in who posted the posts already crawled. No AI call, no message sent; safe to run twice."

    def add_arguments(self, parser):
        parser.add_argument("--stored", action="store_true")
        parser.add_argument("--contacts", action="store_true")
        parser.add_argument("--reread", action="store_true")
        parser.add_argument("--market", action="store_true", help="With --reread: courier posts too.")
        parser.add_argument("--listings-only", action="store_true", help="With --reread: only posts that became listings.")
        parser.add_argument("--delay", type=float, default=1.5, help="Seconds between batches of 100.")
        parser.add_argument("--log", default="", help="File to append changed contacts to.")
        parser.add_argument("--report", action="store_true")

    def handle(self, *args, **options):
        out = self.stdout
        if options["stored"]:
            out.write("stored " + json.dumps(fill_from_stored()))
        if options["contacts"]:
            out.write("contacts " + json.dumps(clean_contacts(options["log"])))
        if options["reread"]:
            box = {"reader": open_reader(), "reopen": open_reader}
            try:
                housing = reread_housing(box, delay=options["delay"], listings_only=options["listings_only"], out=out)
                out.write("reread " + json.dumps(housing))
                if options["market"]:
                    out.write("market " + json.dumps(reread_market(box, delay=options["delay"], out=out)))
            finally:
                box["reader"].close()
        if options["report"]:
            out.write("report " + json.dumps(report(), sort_keys=True))
            out.write("table " + json.dumps(poster_table()))
