# Koolbar

C2C Cross-Border Courier MVP.

Match people who need to send items across borders with travelers who have available carrying capacity.

The MVP does not process payments and is not a courier company. Users connect on Telegram after both sides accept a match.

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
GET    /api/explore/
POST   /api/explore/:id/connect/
```

Creating or editing a request runs the matching engine. Only STRONG (80–100) and POSSIBLE (60–79) matches are stored. Contact details are returned only after both sides accept (`CONNECTED`).

`GET /api/explore/` lists other users’ open demand and supply requests (filters: type, origin, destination, dates, category). `POST /api/explore/:id/connect/` proposes a match with one of your opposite requests, including pairs the engine skipped (for example different cities). Both sides still accept before Telegram contact is shown.

## Telegram bot (Phase 5)

The bot uses long polling locally. `/start` shows:

```text
📦 I Need to Send
✈️ I Can Carry
🔍 Browse requests
📋 My Requests
🤝 My Matches
```

Those actions deep-link into the Mini App (`/app`). Match notifications are sent by Django through the Telegram Bot HTTP API.

Set `TELEGRAM_BOT_TOKEN` in `.env`. Optional HTTPS Mini App URL for WebApp buttons:

```text
TELEGRAM_MINI_APP_URL=https://your-public-https-host
```

Without HTTPS, the bot uses `https://t.me/<bot>/app?startapp=...`. In BotFather, set the Mini App URL to your public `/app` origin when you have one.

If you expose local Docker with Cloudflare quick tunnels, set `TELEGRAM_MINI_APP_URL` to the frontend tunnel and `NEXT_PUBLIC_API_URL` to the API tunnel (not `http://localhost:8000`). Recreate the frontend container after changing `NEXT_PUBLIC_*` so Telegram can reach the API.

Open the Mini App locally at http://localhost:3000/app

## Mini App (Phase 6)

http://localhost:3000/app is the Telegram Mini App:

```text
/app                 Home
/app/demand/new      Create send request
/app/supply/new      Create traveler request
/app/requests        My requests
/app/requests/:id    View / edit / cancel
/app/explore         Browse open demand and supply requests
/app/matches         My matches
/app/matches/:id     Accept / reject / Telegram contact
```

Inside Telegram, login uses `initData`. In the local browser (`DEBUG=True`), a local-user form signs in via `dev_user`. Use **Switch user** (or two browser tabs) with different Telegram user IDs to test a match.

Language: EN / فا. Catalog city and category names follow the selected language.

## Back office (Phase 7)

http://localhost:8000/admin/ — local login `admin` / `admin`.

- Users: deactivate (cancels their active requests) or re-activate
- Requests: filter by type, origin/destination country, dates, category; cancel selected
- Matches: filter by status, score band, created date; add a **manual match**; mark CONNECTED / EXPIRED / REJECTED
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
├── apps/web/          Next.js public site and Mini App (`/app`)
├── backend/           Django modular monolith
├── bot/               Telegram bot (aiogram polling)
├── docs/
├── docker-compose.yml
├── .env.example
└── README.md
```

## Deployment notes

See [docs/deployment.md](docs/deployment.md).

- Vercel hosts the Next.js app. Set the Vercel root directory to `apps/web`.
- Set `NEXT_PUBLIC_API_URL` to the public Django API URL.
- Do not deploy Django to Vercel. Host Django, PostgreSQL, and the bot separately (`backend/Dockerfile.prod`).
- Point BotFather Mini App URL at the Vercel `/app` origin.

Telegram bot token, Django `SECRET_KEY`, and database credentials must live in environment variables. Never commit `.env`.

Actual GitHub import and Vercel deploy need your accounts — say when you want those created/pushed.
