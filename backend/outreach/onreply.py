"""A correction that waits for the person's reply (rule 18: never a second message into a chat with no answer).

Stored on the first message as extra["on_reply"]:
    text        the message, with {n} (count), {min} (cheapest rent) and {link} filled at reply time
    text_small  the same without the count, used when the count is under 3
    filters     feed filters (country, city, type, area list, max): the link opens exactly these and {n} counts them
    listing     a listing the text names: it must still be active, else nothing is sent and Mohammad is told
When the person's first reply arrives, the text is queued as an operator turn: the reply guards and the 10:00 to 20:00
window apply, and their message is answered by this text.
"""

from __future__ import annotations

from django.http import QueryDict

from home.models import HomeListing, HomeSearchQuery, ListingStatus
from outreach import templates
from outreach.models import OutreachConversation, OutreachMessage
from outreach.notify import tell_mohammad


def _search(message: OutreachMessage, spec: dict) -> tuple[int, int | None, str]:
    """Count, cheapest rent and a Mini App link that opens exactly these filters."""
    from home import conf as home_conf
    from home.feed import feed_queryset, parse_filters

    get = QueryDict(mutable=True)
    for key, value in spec.items():
        for item in value if isinstance(value, list) else [value]:
            get.appendlist(key, str(item))
    filters = parse_filters(get, [get.get("country") or "CA"])
    listings = feed_queryset(filters)
    count = listings.count()
    prices = [int(p) for p in listings.values_list("price_amount", flat=True) if p]
    query = HomeSearchQuery.objects.create(
        telegram_user_id=message.recipient_telegram_id or 0,
        raw_text=f"outreach #{message.pk} on_reply",
        country=filters.get("country", ""),
        city=filters.get("city", ""),
        parser="outreach",
        parsed={key: value for key, value in filters.items() if key not in ("move", "by")},
        results_count=count,
    )
    return count, (min(prices) if prices else None), home_conf.mini_app_link(start=f"home_q{query.pk}")


def render(message: OutreachMessage) -> str:
    """The text to send now, or "" when it is no longer true."""
    spec = (message.extra or {}).get("on_reply") or {}
    if not spec.get("text") or spec.get("cancelled"):
        return ""
    named = spec.get("listing")
    if named and not HomeListing.objects.filter(pk=named, status=ListingStatus.ACTIVE).exists():
        return ""
    if not spec.get("filters"):
        return spec["text"]
    count, cheapest, link = _search(message, spec["filters"])
    if count == 0:
        return ""
    if count < 3 or cheapest is None:
        return spec.get("text_small", "").format(link=link)
    return spec["text"].format(n=templates.fa_number(count), min=templates.fa_number(cheapest), link=link)


def on_first_reply(conversation: OutreachConversation, message: OutreachMessage) -> None:
    """Queue the waiting correction once, when the person first answers."""
    spec = (message.extra or {}).get("on_reply")
    if not spec or spec.get("done") or spec.get("cancelled"):
        return
    from outreach import agent

    text = render(message)
    message.extra = {**message.extra, "on_reply": {**spec, "done": True}}
    OutreachMessage.objects.filter(pk=message.pk).update(extra=message.extra)
    if not text:
        tell_mohammad(f"FYI #c{conversation.pk}: اصلاحیه‌ی منتظر جواب برای #{message.pk} دیگه درست نیست و نرفت.")
        return
    agent.queue_operator_reply(conversation, text)
