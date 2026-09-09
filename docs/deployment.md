# Deployment

Koolbar is **one Django app** (Mini App HTML + APIs + Admin). Deploy it on Vercel the same way as myPanel. Postgres is hosted (Vercel Postgres or Neon). You do **not** need Next.js or a running Telegram bot process.

Never commit `.env`, bot tokens, or database passwords.

## Vercel (recommended)

1. Import `https://github.com/mohammadisaeedir/koolbar` in Vercel.
2. Set **Root Directory** to `backend`.
3. Framework: **Django** (not Next.js, not Other). Vercel detects `manage.py` and serves the WSGI app.
4. Add a Vercel Postgres (or Neon) database and copy the connection env vars.
5. Environment variables:

```text
DEBUG=0
SECRET_KEY=<long-random>
TELEGRAM_BOT_TOKEN=<from BotFather>
TELEGRAM_BOT_USERNAME=CB_koolbarbot
TELEGRAM_MINI_APP_URL=https://<your-app>.vercel.app
TELEGRAM_MINI_APP_SHORT_NAME=app
TELEGRAM_CHANNEL_ID=-100...
TELEGRAM_CHANNEL_USERNAME=
TELEGRAM_CHANNEL_ENABLED=true
CRON_SECRET=<long-random>
MARKET_CHANNEL_USERNAME=koolbar_international
MARKET_CHANNEL_USERNAMES=koolbar_international,koolbarcanada,CoolbarEUIRAN,CoolbarUKIRAN,bahsazadkolbar,HamrahbarUSA
MARKET_INGEST_ENABLED=true
MARKET_INGEST_TELEGRAM_USER_ID=1
OPENROUTER_API_KEY=<from openrouter.ai>
OPENROUTER_MODEL=openrouter/free
```

Vercel Storage may prefix vars with the store name (`koolbar_POSTGRES_URL`). The app reads both the prefixed and unprefixed names.

```text
POSTGRES_HOST=...
POSTGRES_DATABASE=...
POSTGRES_USER=...
POSTGRES_PASSWORD=...
POSTGRES_PORT=5432
POSTGRES_SSLMODE=require
```

Staff admin login (created on Vercel startup when these are set):

```text
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_PASSWORD=<long-random>
DJANGO_SUPERUSER_EMAIL=you@example.com
```

Optional: `DJANGO_SECRET_KEY` is accepted as an alias of `SECRET_KEY`.

6. Deploy. Then:

```text
https://<your-app>.vercel.app/           Landing
https://<your-app>.vercel.app/app        Mini App
https://<your-app>.vercel.app/admin/     Back office
https://<your-app>.vercel.app/api/health/
```

Then open `https://<your-app>.vercel.app/admin/` and sign in with `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD`.

Migrations and the staff user run automatically on Vercel cold start.

## Telegram

You only need BotFather. Do **not** deploy `bot/`.

1. Mini App URL → `https://<your-app>.vercel.app/app`
2. Menu Button → open that Mini App
3. Keep short name `app`

Users open Koolbar inside Telegram. Django still uses `TELEGRAM_BOT_TOKEN` to verify login, send match DMs, and publish marketplace posts to the official channel.

The bot must be a **channel administrator** with permission to post messages. Channel posts are Demand/Supply discovery only; they do not include private Telegram identity. If publishing fails, the request still saves — retry from Admin.

## Market channel ingest

Production uses **one daily Vercel cron** at 06:00 UTC: `GET /api/cron/market-migrate/`. That job crawls public Telegram previews in code, then migrates cleaned posts into Explore. Channels rotate by last-run time so a 60-second function still covers every source over a few days. Message ids are unique per channel, so reruns update views/text instead of duplicating.

Hobby allows only one cron and only daily schedules. Hourly (`0 * * * *`) needs Pro and will fail deploy on Hobby.

Set `CRON_SECRET` on Vercel. Vercel sends it as `Authorization: Bearer $CRON_SECRET`. There is no GitHub Actions crawl job.

Public channel previews we can scrape: `@koolbar_international`, `@koolbarcanada`. We also try `@CoolbarEUIRAN`, `@CoolbarUKIRAN`, `@bahsazadkolbar`, and `@HamrahbarUSA` — those are gated groups or a contact page, so the public preview often has zero posts. Private invite links (`t.me/joinchat/…`) cannot be crawled without a Telegram user that is already a member.

Staff can browse ingested posts in Admin → Market posts. Locally: `python manage.py ingest_market_channel`.

Each daily run (or `python manage.py migrate_market_posts`) turns cleaned posts into live ACTIVE Explore requests owned by a system user (`MARKET_INGEST_TELEGRAM_USER_ID`, default 1) and publishes them to the official Koolbar channel like any other request. Missing kg defaults to 10; documents is the default category.

## OpenRouter

Koolbar can call free OpenRouter models (`openrouter/free` by default) for later review of crawled channel/group posts. This is **not** wired into migrate yet.

1. Create a key at https://openrouter.ai/keys
2. Set `OPENROUTER_API_KEY` on Vercel (and locally in `.env`)
3. Optional: `OPENROUTER_MODEL` and `OPENROUTER_MODEL_FALLBACKS`
4. Check config with `python manage.py openrouter_ping` (add `--live` to spend a tiny free-model request)

Never commit the API key. Free models have daily rate limits.

## Docker / VPS (optional)

`backend/Dockerfile.prod` still works if you prefer a long-running server:

```text
DEBUG=False
SECRET_KEY=...
ALLOWED_HOSTS=your-domain.com
DATABASE_URL=postgres://...
TRUST_PROXY=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_BOT_USERNAME=CB_koolbarbot
TELEGRAM_MINI_APP_URL=https://your-domain.com
```

## Back office

Staff use Django Admin, not the Mini App:

- Filter and cancel requests
- Deactivate users
- Create a **manual match**
- Change match status, including CONNECTED

Change the local `admin` / `admin` password before this is public.

## Local

```bash
docker compose up --build
```

Mini App: http://localhost:8000/app  
Admin: http://localhost:8000/admin/  
API: http://localhost:8000/api/health/
