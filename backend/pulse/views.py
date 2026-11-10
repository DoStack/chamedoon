"""Pulse pages. Every number names its source in the page footer. All reads, no writes."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone

from pulse import people, posthog, telegram
from pulse.db import one, rows, value

TZ = ZoneInfo("America/Toronto")
RANGES = {"1": 1, "7": 7, "30": 30}
FIRST_KINDS = ("C1", "C2", "C3", "S1", "S1B", "S2", "P1", "P1Q", "P6", "G1")
PAGE_SIZE = 50


# ---------------------------------------------------------------- plumbing


def staff_only(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if not (user.is_authenticated and user.is_active and user.is_staff):
            nxt = request.get_full_path()
            return HttpResponseRedirect(f"{settings.PULSE_ADMIN_ORIGIN}/admin/login/?{urlencode({'next': nxt})}")
        return view(request, *args, **kwargs)

    return wrapped


def _days(request) -> int:
    return RANGES.get(request.GET.get("r", ""), 7)


def _day_list(n: int) -> list[str]:
    today = timezone.now().astimezone(TZ).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _series(day_list: list[str], mapping: dict) -> list:
    return [mapping.get(d, 0) for d in day_list]


def _fmt(dt) -> str:
    return f"{dt:%b} {dt.day} {dt:%H:%M}"


def _dt(value):
    """An aware datetime. PostHog's toString() gives naive text in the project's zone, which is Toronto."""
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not hasattr(value, "astimezone"):
        return None
    return value if value.tzinfo else value.replace(tzinfo=TZ)


def _local(value) -> str:
    dt = _dt(value)
    return _fmt(dt.astimezone(TZ)) if dt else ""


def _json(value):
    """jsonb comes back as text from a raw cursor."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _pct(a, b) -> str:
    return f"{round(a * 100 / b)}%" if b else "—"


def _qs(request, **changes) -> str:
    params = request.GET.copy()
    for key, val in changes.items():
        if val in (None, ""):
            params.pop(key, None)
        else:
            params[key] = val
    params.pop("page", None) if "page" not in changes else None
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


def _render(request, template: str, page: str, title: str, charts: dict | None = None, **context):
    days = _days(request)
    context.update(
        {
            "page": page,
            "title": title,
            "days": days,
            "range_links": [(label, _qs(request, r=key), str(days) == key) for key, label in (("1", "24h"), ("7", "7d"), ("30", "30d"))],
            "charts": json.loads(json.dumps(charts or {}, default=str)),
            "now": _fmt(timezone.now().astimezone(TZ)),
            "admin_origin": settings.PULSE_ADMIN_ORIGIN,
            "ph": posthog.project_url(),
            "waiting_badge": _waiting_count(),
            "doubt_badge": value("select count(*) from home_homelisting where needs_review and status='active'"),
            "auto_refresh": page not in {"posts", "post", "user", "users"},
        }
    )
    return render(request, f"pulse/{template}", context)


def _waiting_count() -> int:
    return value(
        "select count(*) from outreach_outreachconversation where state <> 'stopped' and last_inbound_at is not null "
        "and (last_outbound_at is null or last_inbound_at > last_outbound_at)"
    )


def _team_sql() -> tuple[str, list]:
    names = sorted(people.team_usernames())
    return ("and lower(coalesce(u.telegram_username,'')) <> all(%s)", [names])


# ---------------------------------------------------------------- overview


@staff_only
def overview(request):
    days = _days(request)
    team = people.team_ids()
    members = one(
        "select count(distinct m.user_id) filter (where m.chat='channel') ch, count(distinct m.user_id) filter (where m.chat='group') gr "
        "from home_channelmembership m join users_user u on u.id=m.user_id where m.status='member' " + _team_sql()[0],
        _team_sql()[1],
    )
    channels = rows("select city, country, username, chat_id from home_homechannel where enabled order by country, city")
    tg_total = sum(c for c in (telegram.member_count(ch["chat_id"] or "@" + ch["username"]) for ch in channels) if c)
    acquired = people.acquired()
    since = timezone.now() - timedelta(days=days)
    new_users = sum(1 for u in acquired if u["created_at"] >= since)
    active = posthog.active_counts(team)
    pipe = one(
        "select count(*) read, count(*) filter (where status='accepted') acc, count(*) filter (where status='new') queue, count(*) filter (where status='error') err "
        "from home_homepost where ingested_at > now() - make_interval(days => %s)",
        [days],
    )
    listings = one(
        "select count(*) filter (where status='active') live, count(*) filter (where created_at > now() - make_interval(days => %s) and status<>'duplicate') new_r, "
        "count(*) filter (where created_at >= (date_trunc('day', now() at time zone 'America/Toronto') at time zone 'America/Toronto') and status<>'duplicate') today "
        "from home_homelisting",
        [days],
    )
    out = one(
        "select count(*) filter (where status='SENT') sent, count(replied_at) replied, count(opened_at) opened, count(member_at) member, count(bot_started_at) bot, count(active_at) active "
        "from outreach_outreachmessage where kind = any(%s)",
        [list(FIRST_KINDS)],
    )
    d30 = _day_list(30)
    dau = posthog.active_per_day(30, team)
    by_day_src = defaultdict(Counter)
    for u in acquired:
        by_day_src[u["created_at"].astimezone(TZ).date().isoformat()][u["source"]] += 1
    d14 = _day_list(14)
    charts = {
        "active": {
            "type": "line", "x": d30, "series": [{"name": "Active people", "data": [dau.get(d) if dau else None for d in d30]}],
            "missing": dau is None,
        },
        "growth": {
            "type": "funnel", "color": "--s2",
            "steps": [["Sent", out["sent"]], ["Replied", out["replied"]], ["Opened link", out["opened"]], ["Member", out["member"]], ["Bot start", out["bot"]], ["Active", out["active"]]],
            "links": ["/admin/pulse/outreach/"] * 6,
        },
        "newusers": {
            "type": "bars", "x": d14, "stack": True,
            "series": [{"name": s, "data": [by_day_src[d][s] for d in d14]} for s in people.SOURCES],
            "links": [f"/admin/pulse/users/?joined={d}" for d in d14],
        },
    }
    return _render(
        request, "overview.html", "overview", "Overview", charts,
        members=members, tg_total=tg_total, users_total=len(acquired), new_users=new_users, active=active,
        pipe=pipe, listings=listings, out=out, reply_rate=_pct(out["replied"], out["sent"]), jobs=_jobs(),
    )


def _jobs() -> list[dict]:
    src = one("select max(last_run_at) last, count(*) filter (where enabled) en, count(*) filter (where enabled and (last_run_at is null or last_run_at < now() - interval '2 hour')) stale from home_homesource")
    hb = one("select max(last_heartbeat_at) hb from outreach_outreachaccount")
    rep = one("select max(created_at) at from outreach_outreachhealthevent where key='daily_report'")
    queue = value("select count(*) from home_homepost where status='new'")
    accts = rows("select name, enabled, paused_until, session_ok from outreach_outreachaccount order by name")
    now = timezone.now()
    sending = sum(1 for a in accts if a["enabled"] and a["session_ok"] and not (a["paused_until"] and a["paused_until"] > now))
    ph = posthog.last_job_runs() or {}

    def age(dt, warn_minutes):
        if not dt:
            return "bad"
        dt = _dt(dt)
        return "ok" if now - dt < timedelta(minutes=warn_minutes) else "warn"

    return [
        {"name": "Source reading", "value": _local(src["last"]), "state": age(src["last"], 90), "note": f"{src['en']} sources, {src['stale']} stale"},
        {"name": "AI review", "value": _local(ph.get("review_cycle")) or "—", "state": age(ph.get("review_cycle"), 90) if ph else "n", "note": f"queue {queue}"},
        {"name": "Channel publish", "value": _local(ph.get("publish_cycle")) or "—", "state": age(ph.get("publish_cycle"), 180) if ph else "n", "note": "publish_cycle"},
        {"name": "Outreach worker", "value": _local(hb["hb"]), "state": age(hb["hb"], 15), "note": "heartbeat"},
        {"name": "Daily outreach report", "value": _local(rep["at"]), "state": age(rep["at"], 60 * 26), "note": "daily_report"},
        {"name": "Outreach accounts", "value": f"{sending} of {len(accts)} sending", "state": "ok" if sending == len(accts) else ("warn" if sending else "bad"), "note": "paused or off: see Outreach"},
    ]


# ---------------------------------------------------------------- acquisition and users


def _channels() -> list[dict]:
    chans = rows("select id, city, country, username, chat_id, group_chat_id from home_homechannel where enabled and city<>'' order by country, city")
    for ch in chans:
        ch["tg_count"] = telegram.member_count(ch["chat_id"] or "@" + ch["username"])
        ver = one(
            "select count(distinct m.user_id) filter (where m.chat='channel') ch, count(distinct m.user_id) filter (where m.chat='group') gr "
            "from home_channelmembership m join users_user u on u.id=m.user_id where m.status='member' and m.channel_id=%s " + _team_sql()[0],
            [ch["id"], *_team_sql()[1]],
        )
        ch["ver_ch"], ch["ver_gr"] = ver["ch"], ver["gr"]
    return chans


@staff_only
def acquisition(request):
    acquired = people.acquired()
    chans = _channels()
    names = [c["city"] for c in chans]
    table = []
    for ch in chans:
        counts = Counter(u["source"] for u in acquired if u["city"] == ch["city"])
        table.append({**ch, "counts": [counts[s] for s in people.SOURCES[:-1]], "total": sum(counts[s] for s in people.SOURCES[:-1])})
    src_counts = Counter(u["source"] for u in acquired)
    out_by_acct = Counter(u["detail"] for u in acquired if u["source"] == "Outreach")
    d30 = _day_list(30)
    by_day = defaultdict(Counter)
    for u in acquired:
        by_day[u["created_at"].astimezone(TZ).date().isoformat()][u["source"]] += 1
    charts = {
        "bycity": {
            "type": "hbars", "y": names, "stack": True,
            "series": [{"name": s, "data": [Counter(u["source"] for u in acquired if u["city"] == c)[s] for c in names]} for s in people.SOURCES[:-1]],
            "links": [f"/admin/pulse/acquisition/{c}/" for c in names],
        },
        "byday": {
            "type": "bars", "x": d30, "stack": True,
            "series": [{"name": s, "data": [by_day[d][s] for d in d30]} for s in people.SOURCES],
            "links": [f"/admin/pulse/users/?joined={d}" for d in d30],
        },
    }
    verified = sum(1 for u in acquired if u["ver_channel"])
    verified_gr = sum(1 for u in acquired if u["ver_group"])
    return _render(
        request, "acquisition.html", "acquisition", "Acquisition", charts,
        total=len(acquired), known=len(acquired) - src_counts["Unknown"], src_counts=src_counts, out_by_acct=out_by_acct.most_common(),
        verified=verified, verified_gr=verified_gr, table=table, sources=people.SOURCES[:-1],
        unknown=src_counts["Unknown"], team=len(people.users()) - len(acquired),
        via_posts=src_counts["Post link"] + src_counts["Landing"], post_link=src_counts["Post link"], landing=src_counts["Landing"],
    )


@staff_only
def acquisition_city(request, city: str):
    acquired = [u for u in people.acquired() if u["city"].lower() == city.lower()]
    ch = next((c for c in _channels() if c["city"].lower() == city.lower()), None)
    return _render(request, "users_list.html", "acquisition", f"Acquisition · {city}", None, users=acquired, channel=ch, heading=f"People from {city}")


@staff_only
def users_page(request):
    everyone = people.users()
    src, detail, city, joined, q = (request.GET.get(k, "").strip() for k in ("source", "detail", "city", "joined", "q"))
    shown = [
        u for u in everyone
        if (not src or u["source"] == src) and (not detail or u["detail"] == detail) and (not city or u["city"].lower() == city.lower())
        and (not joined or u["created_at"].astimezone(TZ).date().isoformat() == joined)
        and (not q or q.lower() in f"{u['username']} {u['name']} {u['id']} {u['tg']}".lower())
    ]
    acquired = people.acquired()
    sticky = posthog.stickiness(people.team_ids())
    src_counts = Counter(u["source"] for u in acquired)
    charts = {
        "sticky": {"type": "bars", "x": [str(i) for i in range(1, 8)], "series": [{"name": "People", "data": [(sticky or {}).get(i, 0) for i in range(1, 8)]}], "color": "--s3", "missing": sticky is None, "xname": "days"},
        "sources": {"type": "donut", "data": [{"name": s, "value": src_counts[s], "link": f"/admin/pulse/users/?source={s}"} for s in people.SOURCES]},
    }
    filters = [(k, v) for k, v in (("source", src), ("detail", detail), ("city", city), ("joined", joined), ("q", q)) if v]
    return _render(
        request, "users.html", "users", "Users", charts,
        users=shown, total=len(acquired), with_username=sum(1 for u in acquired if u["username"]),
        picked=sum(1 for u in acquired if u["picked"]), revealers=sum(1 for u in acquired if u["reveals"]),
        filters=filters, q=q,
    )


@staff_only
def user_detail(request, user_id: int):
    user = next((u for u in people.users() if u["id"] == user_id), None)
    if not user:
        raise Http404
    reveals = rows(
        "select r.created_at, l.id lid, l.city, l.kind, l.price_amount, l.price_currency, l.post_id from home_contactreveal r join home_homelisting l on l.id=r.listing_id where r.user_id=%s order by r.created_at desc limit 50",
        [user_id],
    )
    alerts = rows("select id, city, country, created_at from home_homealert where user_id=%s order by created_at desc", [user_id])
    saved = rows("select s.created_at, l.id lid, l.city, l.post_id from home_homesaved s join home_homelisting l on l.id=s.listing_id where s.user_id=%s order by s.created_at desc", [user_id])
    messages = rows(
        "select m.id, m.kind, m.status, a.name account, m.city, m.sent_at, m.replied_at, m.member_at, m.bot_started_at, m.active_at, m.stopped_at from outreach_outreachmessage m "
        "left join outreach_outreachaccount a on a.id=m.account_id where m.recipient_telegram_id=%s order by m.created_at",
        [user["tg"]],
    )
    conv = one("select id, state, last_inbound_at, last_outbound_at, (select count(*) from outreach_outreachturn t where t.conversation_id=c.id) turns from outreach_outreachconversation c where telegram_user_id=%s", [user["tg"]])
    events = posthog.person_events(user["tg"])
    timeline = []
    for e in events or []:
        timeline.append({"at": _dt(e["at"]), "what": e["event"], "note": " · ".join(x for x in (e["source"], e["kind"], e["city"]) if x), "session": e["session"], "db": False})
    for r in reveals:
        timeline.append({"at": r["created_at"], "what": f"Contact revealed · listing {r['lid']} · {r['city']}", "note": "home_contactreveal", "post": r["post_id"], "db": True})
    for a in alerts:
        timeline.append({"at": a["created_at"], "what": f"Alert saved · {a['city'] or a['country']}", "note": "home_homealert", "db": True})
    for m in messages:
        if m["sent_at"]:
            timeline.append({"at": m["sent_at"], "what": f"Outreach {m['kind']} from {people.account_label(m['account'] or '')}", "note": "outreach_outreachmessage", "db": True})
        if m["replied_at"]:
            timeline.append({"at": m["replied_at"], "what": "Replied to our outreach", "note": "outreach_outreachmessage.replied_at", "db": True})
    timeline = [t for t in timeline if t["at"]]
    timeline.sort(key=lambda t: t["at"], reverse=True)
    for t in timeline:
        t["local"] = _local(t["at"])
    sessions = sorted({t["session"] for t in timeline if t.get("session")})
    return _render(
        request, "user.html", "user", f"User #{user_id}", None,
        u=user, reveals=reveals, alerts=alerts, saved=saved, messages=messages, conv=conv, timeline=timeline[:200],
        ph_missing=events is None, person_url=posthog.person_url(user["tg"]), sessions=[(s, posthog.replay_url(s)) for s in sessions][:10],
        account_label=people.account_label,
    )


# ---------------------------------------------------------------- flows


@staff_only
def flows(request):
    days = max(_days(request), 7)
    team = people.team_ids()
    browse_steps = ["app_opened", "feed_view", "listing_open", "contact_tap", "contact_revealed"]
    browse = posthog.funnel(browse_steps, days, team)
    post = posthog.funnel(["post_choose", "post_step", "post_submitted"], days, team)
    gate = posthog.people_per_event(["contact_tap", "contact_revealed", "gate_denied"], days, team) or {}
    entry = posthog.entry_kinds(days, team) or []
    top = posthog.top_events(days, team) or []
    labels = ["Opened app", "Saw feed", "Opened a listing", "Tapped contact", "Got contact"]
    ph = posthog.project_url("insights/new")
    charts = {
        "browse": {"type": "funnel", "steps": [[labels[i], (browse or [0] * 5)[i]] for i in range(5)], "missing": browse is None, "links": [ph] * 5},
        "post": {"type": "funnel", "color": "--s4", "steps": [["Chose to post", (post or [0, 0, 0])[0]], ["Filled a step", (post or [0, 0, 0])[1]], ["Submitted", (post or [0, 0, 0])[2]]], "missing": post is None},
        "gate": {"type": "funnel", "color": "--s3", "steps": [["Tapped contact", gate.get("contact_tap", 0)], ["Got contact", gate.get("contact_revealed", 0)], ["Denied by join gate", gate.get("gate_denied", 0)]]},
        "entry": {"type": "donut", "data": [{"name": k, "value": v} for k, v in entry]},
        "events": {"type": "hbars", "y": [e[0] for e in top], "series": [{"name": "People", "data": [e[1] for e in top]}], "mono": True},
    }
    return _render(request, "flows.html", "flows", "Flows", charts, flow_days=days)


# ---------------------------------------------------------------- outreach


@staff_only
def outreach(request):
    now = timezone.now()
    accts = rows(
        """
        select a.id, a.name, a.enabled, a.session_ok, a.paused_until, a.pause_reason, a.last_sent_at, a.last_heartbeat_at,
          count(m.*) filter (where m.status='SENT' and m.sent_at >= (date_trunc('day', now() at time zone 'America/Toronto') at time zone 'America/Toronto')) today,
          count(m.*) filter (where m.status='SENT') sent, count(m.replied_at) replied, count(m.member_at) members,
          (select count(*) from outreach_outreachaccountevent e where e.account_id=a.id and e.code='PEER_FLOOD' and e.created_at > now() - interval '7 day') floods,
          (select count(*) from outreach_outreachconversation c where c.account_id=a.id and c.state<>'stopped') chats,
          (select count(*) from outreach_outreachconversation c where c.account_id=a.id and c.state<>'stopped' and c.last_inbound_at is not null and (c.last_outbound_at is null or c.last_inbound_at > c.last_outbound_at)) waiting
        from outreach_outreachaccount a left join outreach_outreachmessage m on m.account_id=a.id
        group by a.id order by a.name
        """
    )
    for a in accts:
        a["label"] = people.account_label(a["name"])
        paused = a["paused_until"] and a["paused_until"] > now
        if not a["session_ok"]:
            a["state"], a["pill"] = "bad", "Logged out"
        elif not a["enabled"]:
            a["state"], a["pill"] = "bad", f"Off · {a['pause_reason'] or 'disabled'}"
        elif paused:
            a["state"], a["pill"] = "warn", a["pause_reason"] or "Paused"
        else:
            a["state"], a["pill"] = "ok", "Sending"
        a["until"] = _local(a["paused_until"]) if paused else ""
        a["rate"] = _pct(a["replied"], a["sent"])
    d30 = _day_list(30)
    daily = rows(
        "select to_char((sent_at at time zone 'America/Toronto')::date,'YYYY-MM-DD') d, count(*) filter (where status='SENT') sent from outreach_outreachmessage where sent_at > now() - interval '31 day' group by 1"
    )
    rdaily = rows("select to_char((replied_at at time zone 'America/Toronto')::date,'YYYY-MM-DD') d, count(*) n from outreach_outreachmessage where replied_at > now() - interval '31 day' group by 1")
    queue = rows("select status, count(*) n from outreach_outreachmessage group by 1 order by 2 desc")
    waiting = rows(
        """
        select c.id, c.username, c.telegram_user_id tg, c.state, a.name account, c.last_inbound_at,
          (select left(t.text, 160) from outreach_outreachturn t where t.conversation_id=c.id and t.direction='in' order by t.created_at desc limit 1) last_text
        from outreach_outreachconversation c join outreach_outreachaccount a on a.id=c.account_id
        where c.state<>'stopped' and c.last_inbound_at is not null and (c.last_outbound_at is null or c.last_inbound_at > c.last_outbound_at)
        order by c.last_inbound_at desc limit 50
        """
    )
    for w in waiting:
        w["label"] = people.account_label(w["account"])
        w["local"] = _local(w["last_inbound_at"])
    charts = {
        "daily": {"type": "bars", "x": d30, "series": [{"name": "Sent", "data": _series(d30, {r["d"]: r["sent"] for r in daily})}, {"name": "Replied", "data": _series(d30, {r["d"]: r["n"] for r in rdaily})}]},
        "queue": {"type": "donut", "data": [{"name": q["status"].title(), "value": q["n"]} for q in queue]},
        "byacct": {
            "type": "bars", "x": [a["label"] for a in accts],
            "series": [{"name": "Sent", "data": [a["sent"] for a in accts]}, {"name": "Replied", "data": [a["replied"] for a in accts]}, {"name": "Member", "data": [a["members"] for a in accts]}],
            "links": [f"/admin/pulse/users/?source=Outreach&detail={a['label']}" for a in accts], "category": True,
        },
    }
    return _render(request, "outreach.html", "outreach", "Outreach", charts, accts=accts, waiting=waiting)


# ---------------------------------------------------------------- crawler and AI


OUTCOME_SQL = """
  case when p.status='new' then 'queue' when p.status='error' then 'error'
       when p.status='rejected' and p.model='rule' then 'rule'
       when p.status='rejected' then 'ai'
       when l.id is null then 'accepted'
       when l.status='duplicate' then 'dup'
       when exists (select 1 from home_homepublication hp where hp.listing_id=l.id and hp.published_at is not null) then 'posted'
       when l.needs_review then 'review'
       else 'accepted' end
"""
OUTCOMES = {
    "posted": ("Accepted · posted", "ok"),
    "accepted": ("Accepted · not posted", "n"),
    "review": ("Accepted · flagged", "warn"),
    "dup": ("Duplicate", "n"),
    "ai": ("AI rejected", "bad"),
    "rule": ("Rule dropped", "n"),
    "queue": ("In queue", "warn"),
    "error": ("AI error", "bad"),
}


@staff_only
def pipeline(request):
    days = _days(request)
    totals = one(
        """
        select count(*) read, count(*) filter (where status='new') queue, count(*) filter (where status='error') err,
          count(*) filter (where status='rejected' and model='rule') rule, count(*) filter (where model not in ('rule','app','')) ai_reviewed,
          count(*) filter (where status='accepted' and model not in ('rule','app','')) ai_acc, count(*) filter (where status='accepted' and model='app') app,
          count(*) filter (where status='accepted') acc
        from home_homepost where ingested_at > now() - make_interval(days => %s)
        """,
        [days],
    )
    reasons = rows(
        "select (model='rule') is_rule, coalesce(nullif(reject_reason,''),'(none)') r, count(*) n from home_homepost where status='rejected' and ingested_at > now() - make_interval(days => %s) group by 1,2 order by 3 desc",
        [days],
    )
    after = one(
        """
        select count(l.id) made, count(l.id) filter (where l.status<>'duplicate') uniq,
          count(distinct hp.listing_id) posted
        from home_homepost p join home_homelisting l on l.post_id=p.id
        left join home_homepublication hp on hp.listing_id=l.id and hp.published_at is not null
        where p.ingested_at > now() - make_interval(days => %s)
        """,
        [days],
    )
    latency = one(
        "select percentile_cont(0.5) within group (order by extract(epoch from reviewed_at-ingested_at)/60) p50, percentile_cont(0.9) within group (order by extract(epoch from reviewed_at-ingested_at)/60) p90 "
        "from home_homepost where reviewed_at is not null and ingested_at > now() - make_interval(days => %s)",
        [days],
    )
    pub_lat = one(
        "select percentile_cont(0.5) within group (order by extract(epoch from hp.published_at-p.reviewed_at)/3600) p50, percentile_cont(0.9) within group (order by extract(epoch from hp.published_at-p.reviewed_at)/3600) p90 "
        "from home_homepublication hp join home_homelisting l on l.id=hp.listing_id join home_homepost p on p.id=l.post_id where hp.published_at > now() - make_interval(days => %s) and p.reviewed_at is not null",
        [days],
    )
    models = rows(
        "select model, count(*) n, count(*) filter (where status='accepted') acc from home_homepost where model not in ('rule','app','') and ingested_at > now() - make_interval(days => %s) group by 1 order by 2 desc",
        [days],
    )
    src = one("select count(*) filter (where enabled) en, count(*) filter (where enabled and (last_run_at is null or last_run_at < now() - interval '2 hour')) stale from home_homesource")
    sources = rows(
        """
        select s.id, s.username, s.kind, count(p.*) read, count(*) filter (where p.status='accepted') acc, count(*) filter (where p.status='rejected') rej,
          count(*) filter (where p.status='new') queue, count(l.id) filter (where l.status<>'duplicate') listings, count(l.id) filter (where l.status='duplicate') dup,
          count(distinct hp.listing_id) posted
        from home_homesource s join home_homepost p on p.source_id=s.id and p.ingested_at > now() - make_interval(days => %s)
        left join home_homelisting l on l.post_id=p.id left join home_homepublication hp on hp.listing_id=l.id and hp.published_at is not null
        group by s.id order by read desc limit 40
        """,
        [days],
    )
    d15 = _day_list(15)
    per_day = rows(
        "select to_char((ingested_at at time zone 'America/Toronto')::date,'YYYY-MM-DD') d, count(*) filter (where status='accepted') acc, count(*) filter (where status='rejected' and model='rule') rule, "
        "count(*) filter (where status='rejected' and model<>'rule') ai, count(*) filter (where status in ('new','error')) other from home_homepost where ingested_at > now() - interval '16 day' group by 1"
    )
    pubs = rows("select to_char((published_at at time zone 'America/Toronto')::date,'YYYY-MM-DD') d, count(*) n from home_homepublication where published_at > now() - interval '16 day' group by 1")
    pd = {r["d"]: r for r in per_day}
    rule_r = [r for r in reasons if r["is_rule"]]
    ai_r = [r for r in reasons if not r["is_rule"]]
    nodes = [{"name": "Posts read"}, {"name": "Rules"}, {"name": "AI review"}, {"name": "Accepted"}]
    links = [
        {"source": "Posts read", "target": "Rules", "value": totals["rule"]},
        {"source": "Posts read", "target": "AI review", "value": totals["ai_reviewed"]},
        {"source": "AI review", "target": "Accepted", "value": totals["ai_acc"]},
    ]
    node_links = {"Posts read": "/admin/pulse/posts/", "Rules": "/admin/pulse/posts/?o=rule", "AI review": "/admin/pulse/posts/?o=ai", "Accepted": "/admin/pulse/posts/?o=posted"}
    if totals["app"]:
        nodes.append({"name": "Mini App posts"})
        links += [{"source": "Posts read", "target": "Mini App posts", "value": totals["app"]}, {"source": "Mini App posts", "target": "Accepted", "value": totals["app"]}]
    if totals["queue"] or totals["err"]:
        nodes.append({"name": "Waiting / error"})
        links.append({"source": "Posts read", "target": "Waiting / error", "value": totals["queue"] + totals["err"]})
        node_links["Waiting / error"] = "/admin/pulse/posts/?o=queue"
    for r in rule_r:
        name = f"Rule: {r['r']}"
        nodes.append({"name": name})
        links.append({"source": "Rules", "target": name, "value": r["n"]})
        node_links[name] = f"/admin/pulse/posts/?o=rule&reason={r['r']}"
    for r in ai_r[:5]:
        name = f"AI: {r['r']}"
        nodes.append({"name": name})
        links.append({"source": "AI review", "target": name, "value": r["n"]})
        node_links[name] = f"/admin/pulse/posts/?o=ai&reason={r['r']}"
    rest = sum(r["n"] for r in ai_r[5:])
    if rest:
        nodes.append({"name": "AI: other reasons"})
        links.append({"source": "AI review", "target": "AI: other reasons", "value": rest})
        node_links["AI: other reasons"] = "/admin/pulse/posts/?o=ai"
    charts = {
        "sankey": {"type": "sankey", "nodes": nodes, "links": [l for l in links if l["value"]], "nodeLinks": node_links},
        "after": {"type": "funnel", "color": "--s3", "steps": [["Listings made", after["made"]], ["Unique (not duplicate)", after["uniq"]], ["Posted to a city channel", after["posted"]]],
                  "links": ["/admin/pulse/posts/?o=dup", "/admin/pulse/posts/?o=accepted", "/admin/pulse/posts/?o=posted"]},
        "perday": {
            "type": "bars", "x": d15, "stack": True,
            "series": [{"name": "Rule dropped", "data": [pd.get(d, {}).get("rule", 0) for d in d15], "color": "--s6"}, {"name": "AI rejected", "data": [pd.get(d, {}).get("ai", 0) for d in d15], "color": "--s5"},
                       {"name": "Accepted", "data": [pd.get(d, {}).get("acc", 0) for d in d15], "color": "--s3"}, {"name": "Queue / error", "data": [pd.get(d, {}).get("other", 0) for d in d15], "color": "--s2"}],
            "links": [f"/admin/pulse/posts/?day={d}" for d in d15],
        },
        "reasons": {
            "type": "hbars", "y": [f"{'rule' if r['is_rule'] else 'AI'} · {r['r']}" for r in reasons], "series": [{"name": "Posts", "data": [r["n"] for r in reasons]}], "mono": True, "log": True,
            "links": [f"/admin/pulse/posts/?o={'rule' if r['is_rule'] else 'ai'}&reason={r['r']}" for r in reasons],
        },
        "pubs": {"type": "bars", "x": d15, "series": [{"name": "Channel posts", "data": _series(d15, {r["d"]: r["n"] for r in pubs})}], "links": [f"/admin/pulse/posts/?o=posted&day={d}" for d in d15]},
    }
    for m in models:
        m["rate"] = _pct(m["acc"], m["n"])
        m["w"] = round(m["acc"] * 100 / m["n"]) if m["n"] else 0
    for s in sources:
        s["rate"] = f"{s['acc'] * 100 / s['read']:.1f}%" if s["read"] else "—"
        s["w"] = min(100, round(s["acc"] * 1000 / s["read"])) if s["read"] else 0
    return _render(
        request, "pipeline.html", "pipeline", "Crawler & AI", charts,
        t=totals, after=after, src=src, models=models, sources=sources,
        rule_pct=_pct(totals["rule"], totals["read"]), ai_pct=_pct(totals["ai_acc"], totals["ai_reviewed"]), posted_pct=_pct(after["posted"], after["uniq"]),
        lat=latency, pub_lat=pub_lat, rule_parts=" · ".join(f"{r['r']} {r['n']:,}" for r in rule_r),
    )


@staff_only
def posts(request):
    days = _days(request)
    outcome = request.GET.get("o", "")
    src = request.GET.get("src", "")
    city = request.GET.get("city", "")
    model = request.GET.get("model", "")
    reason = request.GET.get("reason", "")
    day = request.GET.get("day", "")
    q = request.GET.get("q", "").strip()
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except ValueError:
        page = 1
    where = ["p.ingested_at > now() - make_interval(days => %s)"] if not day else ["(p.ingested_at at time zone 'America/Toronto')::date = %s::date"]
    params: list = [days] if not day else [day]
    if src:
        where.append("s.username = %s")
        params.append(src)
    if city:
        where.append("l.city ilike %s")
        params.append(city)
    if model:
        where.append("p.model ilike %s")
        params.append(f"%{model}%")
    if reason:
        where.append("p.reject_reason = %s")
        params.append(reason)
    if q:
        if q.isdigit():
            where.append("(p.id = %s or l.id = %s or p.telegram_message_id = %s)")
            params += [int(q)] * 3
        else:
            where.append("(p.text ilike %s or s.username ilike %s)")
            params += [f"%{q}%", f"%{q}%"]
    base = f"from home_homepost p join home_homesource s on s.id=p.source_id left join home_homelisting l on l.post_id=p.id where {' and '.join(where)}"
    counts = {r["o"]: r["n"] for r in rows(f"select {OUTCOME_SQL} o, count(*) n {base} group by 1", params)}
    filtered = f"select * from (select p.id, p.ingested_at, p.posted_at, s.username src, s.kind src_kind, p.telegram_message_id mid, p.model, p.reject_reason, left(p.text, 300) text, " \
               f"l.id lid, l.city, l.price_amount, l.price_currency, l.review_note, {OUTCOME_SQL} o {base}) x"
    fparams = list(params)
    if outcome:
        filtered += " where x.o = %s"
        fparams.append(outcome)
    found = rows(filtered + " order by x.ingested_at desc, x.id desc limit %s offset %s", [*fparams, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE])
    more = len(found) > PAGE_SIZE
    found = found[:PAGE_SIZE]
    for p in found:
        p["label"], p["state"] = OUTCOMES.get(p["o"], (p["o"], "n"))
        p["local"] = _local(p["ingested_at"])
    chips = [("", "All read", sum(counts.values()))] + [(k, OUTCOMES[k][0], counts.get(k, 0)) for k in ("posted", "accepted", "review", "dup", "ai", "rule", "queue", "error") if counts.get(k)]
    sources_list = [r["username"] for r in rows("select username from home_homesource where enabled order by username")]
    return _render(
        request, "posts.html", "posts", "Posts", None,
        posts=found, chips=[(k, label, n, _qs(request, o=k), k == outcome) for k, label, n in chips],
        sources_list=sources_list, f={"src": src, "city": city, "model": model, "reason": reason, "day": day, "q": q, "o": outcome},
        page_no=page, prev_link=_qs(request, page=str(page - 1)) if page > 1 else "", next_link=_qs(request, page=str(page + 1)) if more else "",
    )


_NUM = re.compile(r"(?<![\d.])(\d{3,5})(?![\d.])")
_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def price_doubt(text: str, listings: int) -> list[int]:
    """Distinct amounts between 200 and 20,000 in the text. Several of them with one listing may mean offers were missed."""
    found = sorted({int(n) for n in _NUM.findall((text or "").translate(_FA).replace(",", "")) if 200 <= int(n) <= 20000})
    return found if len(found) > max(1, listings) else []


@staff_only
def post_detail(request, post_id: int):
    p = one(
        "select p.*, s.username src, s.kind src_kind, s.title src_title, s.default_city src_city from home_homepost p join home_homesource s on s.id=p.source_id where p.id=%s",
        [post_id],
    )
    if not p:
        raise Http404
    listings = rows("select * from home_homelisting where post_id=%s", [post_id])
    l = listings[0] if listings else None
    pubs, stats, reveals, reports, canonical = [], {}, [], [], None
    if l:
        pubs = rows(
            "select c.username, c.city, hp.message_ids, hp.published_at, hp.error from home_homepublication hp join home_homechannel c on c.id=hp.channel_id where hp.listing_id=%s order by hp.published_at",
            [l["id"]],
        )
        for pub in pubs:
            ids = _json(pub["message_ids"]) or []
            pub["url"] = f"https://t.me/{pub['username']}/{ids[0]}" if ids else ""
            pub["local"] = _local(pub["published_at"])
        if l.get("channel_message_id") and l.get("channel_id"):
            ch = one("select username from home_homechannel where id=%s", [l["channel_id"]])
            if ch:
                pubs.insert(0, {"username": ch["username"], "city": "country", "url": f"https://t.me/{ch['username']}/{l['channel_message_id']}", "local": _local(l["published_at"]), "error": ""})
        stats = {r["kind"]: r["n"] for r in rows("select kind, count(*) n from home_homelistingstat where listing_id=%s group by 1", [l["id"]])}
        reveals = rows(
            "select r.created_at, u.id uid, coalesce(u.telegram_username,'') username, u.first_name from home_contactreveal r join users_user u on u.id=r.user_id where r.listing_id=%s order by r.created_at",
            [l["id"]],
        )
        reports = rows("select reason, created_at from home_homereport where listing_id=%s order by created_at", [l["id"]])
        if l.get("canonical_id"):
            canonical = one("select id, post_id from home_homelisting where id=%s", [l["canonical_id"]])
        conf = _json(l.get("confidence")) or {}
        l["conf_rows"] = [(k, v, round(float(v) * 100)) for k, v in conf.items() if isinstance(v, (int, float))]
        l["app_link"] = telegram.mini_app_link(l["id"])
    fields = []
    if l:
        for name in ("kind", "property_type", "rental_mode", "country", "city", "area", "price_amount", "price_currency", "price_period", "price_basis", "deposit_amount", "rooms", "size_m2",
                     "furnished", "bills_included", "available_from", "available_to", "min_term_months", "roommate_gender", "lister_type", "contact_phone", "contact_telegram", "status", "needs_review", "review_note"):
            v = l.get(name)
            if v not in (None, "", "unknown"):
                fields.append((name.replace("_", " "), v))
    review = _json(p.get("review_json"))
    journey = [("Posted in source", p["posted_at"]), ("Read by crawler", p["ingested_at"]), ("Reviewed", p["reviewed_at"])]
    if l:
        journey.append(("Listing made", l["created_at"]))
    for pub in pubs:
        if pub.get("published_at"):
            journey.append((f"Posted to @{pub['username']}", pub["published_at"]))
    journey = [(name, _local(at), at) for name, at in journey if at]
    for i in range(1, len(journey)):
        gap = journey[i][2] - journey[i - 1][2]
        journey[i] = (*journey[i], gap)
    journey = [j if len(j) == 4 else (*j, None) for j in journey]
    journey = [(n, loc, ("" if g is None else (f"{g.total_seconds() / 3600:.0f} h later" if g > timedelta(hours=3) else f"{max(0, g.total_seconds()) / 60:.0f} min later"))) for n, loc, _at, g in journey]
    label, state = OUTCOMES.get(
        "queue" if p["status"] == "new" else "error" if p["status"] == "error" else ("rule" if p["model"] == "rule" else "ai") if p["status"] == "rejected"
        else "accepted" if not l else "dup" if l["status"] == "duplicate" else "posted" if pubs else "review" if l["needs_review"] else "accepted",
        ("", "n"),
    )
    return _render(
        request, "post.html", "post", f"Post {post_id}", None,
        p=p, l=l, pubs=pubs, stats=stats, reveals=reveals, reports=reports, canonical=canonical, fields=fields,
        review_pretty=json.dumps(review, ensure_ascii=False, indent=2) if review else "", journey=journey, label=label, state=state,
        doubt=price_doubt(p["text"], len(listings)) if p["status"] == "accepted" else [],
        src_url=f"https://t.me/{p['src']}/{p['telegram_message_id']}",
    )


@staff_only
def doubts(request):
    notes = rows(
        "select regexp_replace(review_note, '[0-9.]+$', '') note_kind, review_note, count(*) n from home_homelisting where needs_review and status='active' group by 1,2 order by 3 desc limit 12"
    )
    flagged = value("select count(*) from home_homelisting where needs_review and status='active'")
    conf = rows(
        "select width_bucket((confidence->>'overall')::numeric, 0, 1.0001, 10) b, count(*) n from home_homelisting where created_at > now() - interval '7 day' "
        "and jsonb_typeof(confidence)='object' and (confidence->>'overall') ~ '^[0-9.]+$' group by 1 order by 1"
    )
    low = sum(r["n"] for r in conf if r["b"] <= 8)
    reports = rows("select reason, count(*) n from home_homereport group by 1 order by 2 desc")
    gate = one("select count(*) total, count(*) filter (where missed) missed, max(created_at) last from home_homegatesample where created_at > now() - interval '7 day'")
    items = rows(
        """
        (select l.id lid, l.post_id pid, 'Flagged: ' || l.review_note why, l.city, s.username src, left(p.text, 200) text, l.updated_at at
         from home_homelisting l join home_homepost p on p.id=l.post_id join home_homesource s on s.id=p.source_id
         where l.needs_review and l.status='active' order by l.updated_at desc limit 60)
        union all
        (select l.id, l.post_id, 'User report: ' || r.reason, l.city, s.username, left(p.text, 200), r.created_at
         from home_homereport r join home_homelisting l on l.id=r.listing_id join home_homepost p on p.id=l.post_id join home_homesource s on s.id=p.source_id
         order by r.created_at desc limit 30)
        union all
        (select null, g.post_id, 'Rule drop the AI would accept', null, s.username, left(p.text, 200), g.created_at
         from home_homegatesample g join home_homepost p on p.id=g.post_id join home_homesource s on s.id=p.source_id where g.missed order by g.created_at desc limit 30)
        order by at desc
        """
    )
    for it in items:
        it["local"] = _local(it["at"])
    buckets = [f"{i / 10:.1f}–{(i + 1) / 10:.1f}" for i in range(10)]
    by_b = {r["b"]: r["n"] for r in conf}
    charts = {
        "notes": {"type": "hbars", "y": [r["review_note"] for r in notes], "series": [{"name": "Listings", "data": [r["n"] for r in notes]}], "color": "--warn"},
        "conf": {"type": "bars", "x": buckets, "series": [{"name": "Listings", "data": [by_b.get(i + 1, 0) for i in range(10)]}], "category": True, "warnBelow": 8},
    }
    return _render(request, "doubts.html", "doubts", "AI doubts", charts, flagged=flagged, low=low, reports=reports, report_total=sum(r["n"] for r in reports), gate=gate, items=items)


# ---------------------------------------------------------------- listings and health


@staff_only
def listings(request):
    days = _days(request)
    k = one(
        "select count(*) filter (where status='active') live, count(*) filter (where status='active' and kind='offer') offer, count(*) filter (where status='active' and kind='wanted') wanted, "
        "count(*) filter (where created_at > now() - make_interval(days => %s) and status<>'duplicate') new_r, count(*) filter (where owner_claimed_at is not null) owner from home_homelisting",
        [days],
    )
    alerts = value("select count(*) from home_homealert")
    d30 = _day_list(30)
    per_day = rows("select to_char((created_at at time zone 'America/Toronto')::date,'YYYY-MM-DD') d, count(*) n from home_homelisting where status<>'duplicate' and created_at > now() - interval '31 day' group by 1")
    cities = rows(
        """
        select l.city, count(*) filter (where l.status='active') live, count(*) filter (where l.created_at > now() - make_interval(days => %s) and l.status<>'duplicate') new_r,
          (select count(*) from home_contactreveal r join home_homelisting l2 on l2.id=r.listing_id where l2.city=l.city) reveals,
          (select count(*) from home_homealert a where a.city=l.city) alerts,
          (select count(*) from outreach_outreachmessage m where m.city=l.city and m.status='SENT') out_sent,
          (select count(*) from outreach_outreachmessage m where m.city=l.city and m.replied_at is not null) out_rep
        from home_homelisting l where l.city<>'' group by l.city having count(*) filter (where l.status='active') > 0 order by live desc limit 20
        """,
        [days],
    )
    users_by_city = Counter(u["city"] for u in people.acquired())
    for c in cities:
        c["users"] = users_by_city.get(c["city"], 0)
    top = cities[:12]
    charts = {
        "perday": {"type": "bars", "x": d30, "series": [{"name": "New listings", "data": _series(d30, {r["d"]: r["n"] for r in per_day})}], "color": "--s2", "links": [f"/admin/pulse/posts/?day={d}" for d in d30]},
        "bycity": {"type": "hbars", "y": [c["city"] for c in top], "series": [{"name": "Live", "data": [c["live"] for c in top]}], "links": [f"/admin/pulse/posts/?city={c['city']}&o=posted" for c in top]},
    }
    return _render(request, "listings.html", "listings", "Listings & cities", charts, k=k, alerts=alerts, cities=cities)


@staff_only
def health(request):
    events = rows("select key, count(*) n from outreach_outreachhealthevent where created_at > now() - interval '7 day' group by 1 order by 2 desc limit 15")
    recent = rows("select key, left(detail, 220) detail, created_at from outreach_outreachhealthevent where created_at > now() - interval '3 day' order by created_at desc limit 25")
    for r in recent:
        r["local"] = _local(r["created_at"])
    gaps = {
        "botstart": value("select count(*) from users_botstart"),
        "gate7": value("select count(*) from home_homegatesample where created_at > now() - interval '7 day'"),
        "botmsg_first": _local(value("select min(created_at) from home_homebotmessage", default=None)),
    }
    charts = {"events": {"type": "hbars", "y": [e["key"] for e in events], "series": [{"name": "Events", "data": [e["n"] for e in events]}], "mono": True, "color": "--s5"}}
    return _render(request, "health.html", "health", "Health", charts, jobs=_jobs(), recent=recent, gaps=gaps)
