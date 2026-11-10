# Pulse: read-only analytics at /admin/pulse/

## What it is
Multi-page analytics for the admins: Overview, Acquisition (per channel), Users and one page per user, Flows,
Outreach, Crawler & AI, Posts and one page per post, AI doubts, Listings & cities, Health. Every chart has a hover
tooltip and most numbers open the rows behind them. Footers name the table or event behind each number.

## Where it runs
- Process: Django app `backend/pulse` with its own settings (`pulse.settings`) and WSGI (`pulse.wsgi`), under gunicorn
  on the Chamedoon VM (Proxmox VM 101 on the mini PC), unit `chamedoon-pulse`, `127.0.0.1:8100`.
- Public path: Cloudflare Tunnel `chamedoon-pulse.bigde.li` → `http://127.0.0.1:8100` (only `/admin/pulse/*` is routed). The main backend only forwards
  `/admin/pulse/*` there (`config/pulse_proxy.py`; Vercel ignores vercel.json rewrites for Django), so the admin opens it at
  `https://chamed0on.vercel.app/admin/pulse/`.
- On Vercel only the pass-through runs (no queries, no rendering); on bigde-vps only the Postgres it reads.

## Access
Same login as the admin panel. The browser sends the admin `sessionid` cookie with `/admin/pulse/`; Pulse reads that
session from `django_session` with the production `SECRET_KEY` and lets in active staff users only. Anyone else is
sent to `/admin/login/`. User management stays in the admin panel.

## Read-only by construction
- Postgres role `pulse_ro`: `default_transaction_read_only = on`, `statement_timeout = 20s`, `CONNECTION LIMIT 5`,
  SELECT on public tables except `courier_couriersettings` and `home_homesettings` (secrets). It reads `auth_user` and
  `django_session` only to check the login.
- Each query also runs in `SET TRANSACTION READ ONLY` (transaction level; safe on PgBouncer transaction pooling).
- Sessions are never written (no login happens here; `SESSION_SAVE_EVERY_REQUEST = False`).
- Connects over WireGuard to PgBouncer `10.88.0.1:6432`, `sslmode=require`.

## Freshness
DB numbers are live on every load; pages without inputs reload every 60 s. PostHog (HogQL query API) is cached
5 minutes, Telegram member counts 1 hour, the users list with attribution 2 minutes.

## Secrets (VM only, root 600)
`/etc/chamedoon-pulse/db.env` (`PULSE_DB_URL`), `app.env` (`DJANGO_SECRET_KEY`, `HOME_BOT_TOKEN`, `HOME_BOT_USERNAME`),
`posthog.env` (`POSTHOG_PERSONAL_KEY`, `POSTHOG_PROJECT_ID`, `POSTHOG_API_HOST`). Optional: `PULSE_TEAM_USERNAMES`
(default `Mbigdeli,mohammadisaeedir`), left out of acquisition and active counts.

## Deploy or update
```
git archive --format=tar.gz -o pulse-src.tar.gz HEAD
# copy to the VM as /tmp/pulse-src.tar.gz, then as root:
bash /opt/chamedoon-pulse/app/deploy/pulse/deploy.sh   # first time: extract the tarball and run deploy/pulse/deploy.sh from it
```

## Attribution rule (Acquisition, Users)
First match wins: outreach (`member_at` set) → PostHog first touch (post link, landing, direct, with city) → bot only
(city picked in the bot, else a gate-verified channel) → unknown. Telegram does not tell us who joined a channel
through which link, so this is inferred.
