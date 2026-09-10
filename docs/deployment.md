# Deployment

Koolbar is **one Django app** (Mini App HTML + APIs + Admin). Deploy it on Vercel the same way as myPanel. Postgres is hosted (Vercel Postgres or Neon). You do **not** need Next.js or a running Telegram bot process.

Never commit `.env`, bot tokens, or database passwords.

## Vercel (recommended)

1. Import `https://github.com/mohammadisaeedir/koolbar` in Vercel.
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
TELEGRAM_CHANNEL_USERNAME=
TELEGRAM_CHANNEL_URL=https://t.me/+26pUh8_5u0w1MTVk
TELEGRAM_CHANNEL_ENABLED=true
TELEGRAM_WEBHOOK_SECRET=<long-random>
CRON_SECRET=<long-random>
MARKET_CHANNEL_USERNAME=koolbar_international
MARKET_CHANNEL_USERNAMES=koolbar_international,koolbarcanada,CoolbarEUIRAN,CoolbarUKIRAN,bahsazadkolbar,HamrahbarUSA
MARKET_INGEST_ENABLED=true
MARKET_INGEST_TELEGRAM_USER_ID=1
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

Production crawls public Telegram previews **once a day** (Vercel 06:00 UTC): `GET /api/cron/market-migrate/`. That job stores posts, then converts real send/carry listings into Explore requests. Message ids are unique per channel, so reruns update views/text instead of duplicating.

Hobby allows only one cron and only a daily schedule. Do not change `vercel.json` to an hourly or 6-hour extract.

Set `CRON_SECRET` on Vercel. Vercel sends it as `Authorization: Bearer $CRON_SECRET`. You do **not** need a GitHub Actions secret for this daily extract.

Public channel previews we can scrape: `@koolbar_international`, `@koolbarcanada`. We also try `@CoolbarEUIRAN`, `@CoolbarUKIRAN`, `@bahsazadkolbar`, and `@HamrahbarUSA` — those are gated groups or a contact page, so the public preview often has zero posts. Private invite links (`t.me/joinchat/…`) cannot be crawled without a Telegram user that is already a member.

Staff can browse ingested posts in Admin → Market posts. Locally: `python manage.py ingest_market_channel`.

Each daily run stores posts on `MarketPost` first. Group ads and invite posts are skipped (no LLM). Real DEMAND/SUPPLY posts go through three free OpenRouter models, then paid `gpt-5-mini` if `OPENAI_API_KEY` is set. If every model fails, the post waits up to 6 hours for another LLM try (`MARKET_LLM_RETRY_HOURS`). After that window, regex rules still insert the request and publish it to the Koolbar channel. The request owner is the source channel (or an @username in the post), not a single system user.

An optional GitHub Action (`.github/workflows/market-llm-retry.yml`) can retry those failed LLM posts during the 6-hour window. It does not crawl channels. Skip it unless you want extra LLM retries the same day; the next daily Vercel run still applies the 6-hour fallback.

Imported requests are published to the official Koolbar channel like any other request. `channel_message_id` / `channel_published_at` / `channel_status` are that Koolbar channel post, not the source group message. They stay empty unless `TELEGRAM_CHANNEL_ENABLED` is on and the bot can post.

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
