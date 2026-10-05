# Koolbar

C2C Cross-Border Courier MVP.

Match people who need to send items across borders with travelers who have available carrying capacity.

The MVP does not process payments and is not a courier company. Users message each other on Telegram as soon as a match is found.

## Local URLs

| Service | URL |
| --- | --- |
| Frontend | http://localhost:3000 |
| Mini App | http://localhost:3000/app |
| Backend API | http://localhost:8000 |
| Health | http://localhost:8000/api/health/ |
| Django Admin | http://localhost:8000/admin/ |

## Requirements

- Docker and Docker Compose
- Node.js 22+ (for running the frontend outside Docker)
- Python 3.12+ (for running the backend outside Docker)

Django Admin (local only):

```text
http://localhost:8000/admin/
username: admin
password: admin
```

Do not use this password in production.

## Telegram token

Put the bot token in `.env` only. Never commit it or paste it into chat.

```text
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_BOT_USERNAME=CB_koolbarbot
NEXT_PUBLIC_TELEGRAM_BOT_USERNAME=CB_koolbarbot
```

Then restart:

```bash
docker compose up -d --force-recreate bot frontend
```

## Quick start (Docker)

```bash
cp .env.example .env
docker compose up --build
```

Then open http://localhost:3000 and confirm the homepage shows **API connected**.

Health check:

```bash
curl http://localhost:8000/api/health/
```

Expected:

```json
{"status":"ok","service":"koolbar-backend","database":"ok"}
```

## Telegram auth (Phase 2)

```text
POST /api/auth/telegram/
GET  /api/me/
```

Mini App sends Telegram `initData`. Identity is **telegram_user_id**, never username.

Local browser testing (`DEBUG=True` only):

```bash
curl -s http://localhost:8000/api/auth/telegram/ \
  -H 'Content-Type: application/json' \
  -d '{"dev_user":{"telegram_user_id":1,"first_name":"Dev","telegram_username":"localdev"}}'
```

Then:

```bash
docker compose down
```

## Requests (Phase 3)

```text
GET    /api/categories/
GET    /api/locations/
POST   /api/requests/
GET    /api/requests/
GET    /api/requests/:id/
PATCH  /api/requests/:id/
POST   /api/requests/:id/cancel/
```

Demand requires origin, destination, date range, weight, and at least one category.
Supply requires origin, destination, travel dates, and capacity. Exclusions are optional.

Locations are a seeded country/city list (ISO country code + city slug). Categories are stored in the database, not hard-coded in matching later.

## Matching (Phase 4)

```text
GET    /api/matches/
GET    /api/matches/:id/
POST   /api/matches/:id/accept/
POST   /api/matches/:id/reject/
POST   /api/matches/:id/cancel/
GET    /api/explore/
POST   /api/explore/:id/connect/
```

Creating or editing a request runs the matching engine. Only STRONG (80–100) and POSSIBLE (60–79) matches are stored. New matches are created as `ACCEPTED` and Telegram contact is returned immediately.

`GET /api/explore/` lists other users’ open demand and supply requests (filters: type, origin, destination, dates, category). `POST /api/explore/:id/connect/` creates a match with one of your opposite requests, including pairs the engine skipped (for example different cities). Telegram contact is shown as soon as the match exists.

## Telegram bot (Phase 5)

The bot uses long polling locally. `/start` shows:

```text
📦 Send Package    ✈️ Can Carry
🔍 Browse requests
📋 My Requests     🤝 My Matches
```

Those actions deep-link into the Mini App (`/app`). Match notifications are sent by Django through the Telegram Bot HTTP API.

Set `TELEGRAM_BOT_TOKEN` in `.env`. Optional HTTPS Mini App URL for WebApp buttons:

```text
TELEGRAM_MINI_APP_URL=https://your-public-https-host
```

Without HTTPS, the bot uses `https://t.me/<bot>/app?startapp=...`. In BotFather, set the Mini App URL to your public `/app` origin when you have one.

Open the Mini App locally at http://localhost:8000/app

## Telegram Channel (marketplace feed)

The Mini App and matching engine stay the source of truth. The official Koolbar Telegram Channel is a public discovery feed for **active Demand and Supply** only. Matches and ratings stay private.

1. Create or select the official Koolbar Telegram Channel.
2. Add the Koolbar Bot as an **administrator**.
3. Give the bot permission to **post messages**.
4. Put the channel id and username in `.env` (never put the bot token in docs or git):

```text
TELEGRAM_CHANNEL_ID=-100...
TELEGRAM_CHANNEL_USERNAME=chamed0on
TELEGRAM_CHANNEL_ENABLED=true
```

Private channels (`t.me/+…` invite links) **must** use the numeric id. It should look like `-1001234567890` (leading minus). A public `@username` only works if the channel is public and the bot is already a member. Keep using the same `TELEGRAM_BOT_TOKEN`.

5. Restart the backend (`docker compose up -d backend`).
6. Create a test Demand in the Mini App and confirm a channel post with **🔎 View on Koolbar**.
7. Create a test Supply and confirm a second post.
8. Edit a published request: the existing channel message should update (no duplicate post).
9. Cancel one request and confirm the channel message is marked **No longer available**.
10. Let a request expire, or wait until its end date: the channel message should get the same unavailable mark.

If Telegram fails, the request still saves as ACTIVE. Check `/admin/` → Requests for `channel_status` and use **Publish to Channel** / **Retry Channel Publication** / **Update Channel Post**.

Channel posts never include Telegram user id, username, description, or other private contact details. The button opens `?startapp=request_<id>` in the Mini App.

There is no Telegram discussion group in this phase.

## Match outreach (Telegram account)

Imported demanders never used Chamedoon, so the bot cannot reach them. When their imported request gets a strong match, `outreach_worker/` sends them one Persian DM from the Chamedoon Telegram account, with a Mini App link. Nothing is sent unless the worker runs. Setup, limits and admin: [docs/outreach.md](docs/outreach.md).

## Mini App

http://localhost:8000/app is the Telegram Mini App (Django HTML, same origin as the API):

```text
/                        Landing
/app                     Home
/app/demand/new          Create send request
/app/supply/new          Create traveler request
/app/explore             Browse open demand and supply requests
/app/requests            My requests
/app/requests/:id        View / edit / cancel
/app/matches             My matches
/app/matches/:id         Telegram contact / finish / rate
```

Inside Telegram, login uses `initData`. In the local browser (`DEBUG=True`), a local-user form signs in. Use **Switch user** with different Telegram user IDs to test a match.

Language: EN / فا.

## Deployment notes

See [docs/deployment.md](docs/deployment.md).

- Deploy **one** Vercel project with Root Directory `backend` and Framework **Django**.
- Production deploys from **`main` only** (`git push origin main`).
- Mini App URL for BotFather: `https://<your-app>.vercel.app/app`
- You do not need Next.js (`apps/web`) or the polling bot (`bot/`) for production.
- Point BotFather at the Vercel `/app` URL.

Telegram bot token, Django `SECRET_KEY`, and database credentials must live in environment variables. Never commit `.env`.

## Back office

http://localhost:8000/admin/ — local login `admin` / `admin`.

- Users: deactivate (cancels their active requests) or re-activate
- Requests: filter by type, origin/destination country, dates, category; cancel selected; publish / retry / update the Telegram channel post
- Matches: filter by status, score band, created date; add a **manual match**; mark ACCEPTED / EXPIRED / REJECTED
- Check **Override hard rules** only to force a pair the engine would skip

## Backend tests

```bash
docker compose exec backend pytest
```

Or, after `docker compose up -d`:

```bash
docker compose run --rm backend pytest
```

## Frontend build

From `apps/web`:

```bash
npm install
npm run build
```

Inside Docker:

```bash
docker compose exec frontend npm run build
```

## Repository layout

```text
koolbar/
├── apps/web/          Legacy Next.js app (not required for deploy)
├── backend/           Django Mini App, API, and Admin
├── bot/               Optional Telegram chat wizard (not required)
├── outreach_worker/   Telegram-account sender for match outreach (runs outside Vercel)
├── docs/
├── docker-compose.yml
├── .env.example
└── README.md
```

## Deployment notes

See [docs/deployment.md](docs/deployment.md).

- Vercel hosts the Django Mini App, API, and Admin. Set the Vercel root directory to `backend`.
- BotFather Mini App URL is `https://<your-app>.vercel.app/app`.
- Do not deploy the Next.js app or the polling bot for production.

Telegram bot token, Django `SECRET_KEY`, and database credentials must live in environment variables. Never commit `.env`.

Actual GitHub import and Vercel deploy need your accounts — say when you want those created/pushed.
