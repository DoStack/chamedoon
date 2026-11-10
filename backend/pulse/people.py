"""Who our users are and where each one came from.

Attribution, first match wins:
1. Outreach: one of our accounts messaged them and they later opened the app or bot (`member_at`). Channel = the message's city.
2. PostHog first touch: their first `deeplink_open` or `app_opened`. A listing or city link from a channel post is "Post link",
   our landing is "Landing", anything else with a city is "Direct".
3. Bot only: no PostHog entry; the city they picked in the bot, else a channel the join gate verified.
4. Unknown.
"""

from __future__ import annotations

import os

from django.core.cache import cache

from pulse import posthog
from pulse.db import SYNTHETIC_TG, rows

SOURCES = ("Outreach", "Post link", "Landing", "Direct", "Bot only", "Unknown")
ACCOUNT_NAMES = {"sina": "Niloofar"}  # the DB key of an account renamed on 2026-11-10


def account_label(name: str) -> str:
    name = (name or "").strip()
    return ACCOUNT_NAMES.get(name, name.title())


def team_usernames() -> set[str]:
    raw = os.environ.get("PULSE_TEAM_USERNAMES", "Mbigdeli,mohammadisaeedir")
    return {u.strip().lstrip("@").lower() for u in raw.split(",") if u.strip()}


def _source(user: dict, touch: dict | None) -> tuple[str, str, str]:
    if user["out_account"] and user["out_member"]:
        return "Outreach", account_label(user["out_account"]), user["out_city"] or user["picked"] or ""
    if touch:
        launch, kind, city = touch["launch"], touch["kind"], touch["city"]
        if kind in {"listing", "city"} and launch.startswith("home"):
            return "Post link", kind, city or user["picked"] or ""
        if kind == "landing":
            return "Landing", "", city or user["picked"] or ""
        if kind and kind.upper() == kind:  # an outreach model code such as S1B or P1Q
            return "Outreach", f"link {kind}", city or user["picked"] or ""
        if city or launch:
            return "Direct", launch.replace("_other", ""), city or user["picked"] or ""
    if user["picked"] or user["verified_city"]:
        return "Bot only", "", user["picked"] or user["verified_city"]
    return "Unknown", "", ""


def users() -> list[dict]:
    found = cache.get("pulse:users")
    if found is not None:
        return found
    base = rows(
        """
        select u.id, u.telegram_user_id tg, coalesce(u.telegram_username,'') username, u.first_name, coalesce(u.last_name,'') last_name,
          u.created_at, u.updated_at last_seen,
          coalesce(c.city,'') picked,
          coalesce((select max(hc.city) from home_channelmembership m join home_homechannel hc on hc.id=m.channel_id
                    where m.user_id=u.id and m.status='member' and hc.city<>''),'') verified_city,
          (select count(*) from home_channelmembership m where m.user_id=u.id and m.status='member' and m.chat='channel') ver_channel,
          (select count(*) from home_channelmembership m where m.user_id=u.id and m.status='member' and m.chat='group') ver_group,
          (select count(*) from home_contactreveal r where r.user_id=u.id) reveals,
          (select count(*) from home_homealert a where a.user_id=u.id) alerts,
          (select count(*) from home_homesaved s2 where s2.user_id=u.id) saved,
          coalesce(o.account,'') out_account, coalesce(o.city,'') out_city, (o.member_at is not null) out_member
        from users_user u
        left join home_homechatstate s on s.telegram_user_id=u.telegram_user_id
        left join home_homechannel c on c.id=s.channel_id
        left join lateral (
          select a.name account, m.city, m.member_at from outreach_outreachmessage m join outreach_outreachaccount a on a.id=m.account_id
          where m.recipient_telegram_id=u.telegram_user_id and m.status='SENT' order by (m.member_at is null), m.sent_at limit 1) o on true
        where u.telegram_user_id < %s
        order by u.created_at desc
        """,
        [SYNTHETIC_TG],
    )
    touches = posthog.first_touch() or {}
    team = team_usernames()
    for user in base:
        user["team"] = user["username"].lower() in team
        if user["team"]:
            user["source"], user["detail"], user["city"] = "Team", "", user["picked"]
        else:
            user["source"], user["detail"], user["city"] = _source(user, touches.get(f"tg_{user['tg']}"))
        user["link"] = f"https://t.me/{user['username']}" if user["username"] else f"tg://user?id={user['tg']}"
        user["name"] = f"{user['first_name']} {user['last_name']}".strip()
    cache.set("pulse:users", base, 120)
    return base


def team_ids() -> list[int]:
    return [u["tg"] for u in users() if u["team"]]


def acquired() -> list[dict]:
    return [u for u in users() if not u["team"]]
