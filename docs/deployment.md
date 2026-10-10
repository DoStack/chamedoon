# Deployment

Koolbar is **one Django app** (Mini App HTML + APIs + Admin). Deploy it on Vercel the same way as myPanel. Postgres is hosted (Vercel Postgres or Neon). You do **not** need Next.js or a running Telegram bot process.

Never commit `.env`, bot tokens, or database passwords.

## Vercel (recommended)

1. Import `https://github.com/DoStack/chamedoon` in Vercel.
2. Set **Root Directory** to `backend`.
3. Framework: **Django** (not Next.js, not Other). Vercel detects `manage.py` and serves the WSGI app.

Production publishes from **`main` only**. Push to `main`; Vercel deploys that. Do not use a second feature-branch deploy.

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
TELEGRAM_CHANNEL_USERNAME=chamed0on
TELEGRAM_CHANNEL_URL=https://t.me/chamed0on
TELEGRAM_CHANNEL_ENABLED=true
TELEGRAM_WEBHOOK_SECRET=<long-random>
CRON_SECRET=<long-random>
MARKET_CHANNEL_USERNAME=koolbar_international
MARKET_CHANNEL_USERNAMES=koolbar_international,koolbarcanada,CoolbarEUIRAN,CoolbarUKIRAN,bahsazadkolbar,HamrahbarUSA
MARKET_INGEST_ENABLED=true
MARKET_INGEST_TELEGRAM_USER_ID=1
# Optional: outreach is on by default and the worker secret falls back to a hash in settings
OUTREACH_SERVICE_SECRET=<long-random>
OPENROUTER_API_KEY=<from openrouter.ai>
OPENROUTER_MODEL=openrouter/free
OPENROUTER_MODEL_FALLBACKS=google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free
OPENAI_API_KEY=<from platform.openai.com>
OPENAI_MODEL=gpt-5-mini
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
4. Set the bot webhook so Accept / Reject buttons work in Telegram:

```text
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<your-app>.vercel.app/api/telegram/webhook/&secret_token=<TELEGRAM_WEBHOOK_SECRET>
```

Also add `TELEGRAM_WEBHOOK_SECRET` in Vercel env.

Users open Koolbar inside Telegram. Django still uses `TELEGRAM_BOT_TOKEN` to verify login, send match DMs, and publish marketplace posts to the official channel.

The bot must be a **channel administrator** with permission to post messages. Channel posts are Demand/Supply discovery only; they do not include private Telegram identity. If publishing fails, the request still saves — retry from Admin.

## Market channel ingest

There are **three** production crons. They match Admin → Manual extract, one step per hour (Hobby can only run daily jobs, and each click has ~60s):

1. `GET /api/cron/market-extract/` at `0 2 * * *` UTC (05:30–06:29 Iran) — Run extraction
2. `GET /api/cron/market-convert/` at `0 3 * * *` UTC (06:30–07:29 Iran) — Convert stored posts
3. `GET /api/cron/market-publish/` at `0 4 * * *` UTC (07:30–08:29 Iran) — Publish to channel

Hobby does not fire at minute 0 exactly. Each job runs sometime in that UTC hour.

That pipeline:

1. Marks past-dated requests `EXPIRED` and crawls `MARKET_CHANNEL_USERNAMES` for the last 1 day
2. Converts stored send/carry posts into Explore requests (rules only, no OpenAI)
3. Publishes unpublished imported requests to the official Koolbar channel

Hobby allows only daily schedules. Do not add a GitHub Actions crawl. A 15-day catch-up still uses Admin → Run extraction / Convert / Publish, clicked until the window is covered.

Set `CRON_SECRET` on Vercel. Vercel sends `Authorization: Bearer $CRON_SECRET`.

Staff can run the same pipeline from Admin → Manual extract → **Run extraction**. That button stops around 52s so Vercel does not return 540.

When `OUTREACH_TG_API_ID`, `OUTREACH_TG_API_HASH`, and `OUTREACH_TG_SESSION` are set, extract reads history with that Telegram account: the usernames in `MARKET_CHANNEL_USERNAMES`, plus the private groups in `MARKET_SOURCE_INVITES` (the Canada group, the US group, and the Q&A group). The account joins an invite only if it is not already a member. Public `t.me/s/` previews are the fallback when that account cannot open a chat. `@koolbar_international` is a contact page, so the preview has no posts. Do **not** crawl `@chamed0on`; that is the official channel we publish *to*.

Staff can browse ingested posts in Admin → Market posts. Locally: `python manage.py ingest_market_channel`.

Each daily extract stores posts on `MarketPost` first. Group ads and invite posts are skipped. Convert uses rules only (no OpenAI). The request owner is the post author when we can read an @username.

Past travel / desired dates are expired during the extract cron. Explore and matching also expire due requests when someone opens those pages.

Imported requests are published to the official Koolbar channel like any other request. `channel_message_id` / `channel_published_at` / `channel_status` are that Koolbar channel post, not the source group message. They stay empty unless `TELEGRAM_CHANNEL_ENABLED` is on and the bot can post.

## Match outreach

A fourth daily cron, `GET /api/cron/outreach-build/` at `0 5 * * *` UTC, queues Persian DMs for imported demanders with strong matches. Set `OUTREACH_ENABLED=false` to turn it off. Four more daily crons, `/api/cron/outreach-send/` to `/api/cron/outreach-send-4/` (07:00, 10:00, 13:00, 16:00 UTC), send one DM each once `OUTREACH_TG_API_ID`, `OUTREACH_TG_API_HASH` and `OUTREACH_TG_SESSION` are set. See [outreach.md](outreach.md).

## LLMs

1. Create an OpenRouter key at https://openrouter.ai/keys and set `OPENROUTER_API_KEY`
2. Create an OpenAI key at https://platform.openai.com/api-keys and set `OPENAI_API_KEY` on Vercel. Never commit it.
3. Defaults: three free models (`openrouter/free`, `google/gemma-4-31b-it:free`, `google/gemma-4-26b-a4b-it:free`), then paid `OPENAI_MODEL=gpt-5-mini` (small GPT-5, lower token use). Override with `OPENROUTER_MODEL_FALLBACKS` / `OPENAI_MODEL` if needed. Use `gpt-4o-mini` if your OpenAI account does not have GPT-5.
4. Optional: `MARKET_LLM_REVIEW_LIMIT` (default 12 reviews per cron)
5. Check config with `python manage.py openrouter_ping` (add `--live` to spend a tiny request)

Never commit API keys. Free OpenRouter models have daily rate limits. Each cron reviews a limited batch so it fits the 60-second Hobby function. Description rewrites use free models only so the paid key is not spent twice.

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
- Change match status, including ACCEPTED

Change the local `admin` / `admin` password before this is public.

## Local

```bash
docker compose up --build
```

Mini App: http://localhost:8000/app  
Admin: http://localhost:8000/admin/  
API: http://localhost:8000/api/health/
