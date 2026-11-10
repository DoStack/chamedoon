"""Start params of the content posts (spec §6) and the `home_entry` row each Mini App open writes.

    dg_{city}_{yyyymmdd}[_alert|_post]   daily digest
    deal_{city}_{listing_id}             deal of the day / newest listing
    wk_{city}_{yyyyww}[_alert]           weekly report
    mo_{city}_{yyyymm}[_alert]           monthly report
    cc_{country}_{yyyyww}_{city}         country weekly
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

_PARAM = re.compile(
    r"^(?:(?P<src>dg|wk|mo)_(?P<city>[a-z][a-z0-9]{1,23})_(?P<period>\d{6,8})(?:_(?P<action>alert|post))?"
    r"|deal_(?P<dcity>[a-z][a-z0-9]{1,23})_(?P<listing>\d{1,12})"
    r"|cc_(?P<cc>[a-z]{2})_(?P<week>\d{6})_(?P<ccity>[a-z][a-z0-9]{1,23}))$"
)
REPEAT_WINDOW = timedelta(minutes=30)


@dataclass
class ContentStart:
    source: str
    city: str
    action: str = ""
    listing: int | None = None
    country: str = ""


def parse(value: str | None) -> ContentStart | None:
    found = _PARAM.match((value or "").strip().lower())
    if not found:
        return None
    if found["src"]:
        if found["action"] == "post" and found["src"] != "dg":
            return None
        return ContentStart(source=found["src"], city=found["city"], action=found["action"] or "all")
    if found["listing"]:
        return ContentStart(source="deal", city=found["dcity"], action="all", listing=int(found["listing"]))
    return ContentStart(source="cc", city=found["ccity"], action="all", country=found["cc"].upper())


def record(telegram_user_id: int | None, start_param: str, *, first_open: bool = False) -> bool:
    """One `home_entry` row per open of a content link; a retry of the same open within 30 minutes is not a second open."""
    from home.models import HomeEntry

    start = parse(start_param)
    if start is None:
        return False
    since = timezone.now() - REPEAT_WINDOW
    if telegram_user_id and HomeEntry.objects.filter(telegram_user_id=telegram_user_id, start_param=start_param[:64], created_at__gte=since).exists():
        return False
    HomeEntry.objects.create(
        telegram_user_id=telegram_user_id, start_param=start_param[:64], source=start.source, city=start.city, action=start.action, first_open=first_open
    )
    return True
