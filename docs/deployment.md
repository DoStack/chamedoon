# Deployment

Koolbar splits hosting on purpose:

| Piece | Where it runs |
| --- | --- |
| Next.js site + Mini App | Vercel (`apps/web`) |
| Django API + Admin | Any Python host (Fly, Render, Railway, a VPS) |
| PostgreSQL | Same host or a managed Postgres |
| Telegram bot | Same machine as Django (long polling is fine for MVP) |

Do **not** deploy Django to Vercel.

Never commit `.env`, bot tokens, or database passwords.

## What you need to provide

1. A GitHub repo (or ask in chat to create/push one)
2. A Vercel account
3. A public HTTPS API URL for Django (example: `https://api.yourdomain.com`)
4. A public HTTPS Mini App URL (the Vercel app URL is enough)
5. Strong values for `SECRET_KEY` and `BOT_SERVICE_SECRET`

## Frontend (Vercel)

1. Import the GitHub repo in Vercel.
2. Set **Root Directory** to `apps/web`.
3. Framework: Next.js (see `apps/web/vercel.json`).
4. Environment variables:

```text
NEXT_PUBLIC_API_URL=https://api.yourdomain.com
NEXT_PUBLIC_TELEGRAM_BOT_USERNAME=CB_koolbarbot
```

5. Deploy. The Mini App lives at `https://<vercel-app>/app`.

## Backend

Use `backend/Dockerfile.prod` (Gunicorn + WhiteNoise static files).

Required environment:

```text
DEBUG=False
SECRET_KEY=<long-random>
ALLOWED_HOSTS=api.yourdomain.com
DATABASE_URL=postgres://user:pass@host:5432/koolbar
CORS_ALLOWED_ORIGINS=https://<vercel-app>
CSRF_TRUSTED_ORIGINS=https://<vercel-app>,https://api.yourdomain.com
TRUST_PROXY=true
TELEGRAM_BOT_TOKEN=<from BotFather>
TELEGRAM_BOT_USERNAME=CB_koolbarbot
BOT_SERVICE_SECRET=<long-random>
TELEGRAM_MINI_APP_URL=https://<vercel-app>
TELEGRAM_MINI_APP_SHORT_NAME=app
```

Then:

```bash
python manage.py migrate
python manage.py createsuperuser
```

Health check: `GET https://api.yourdomain.com/api/health/`

Admin: `https://api.yourdomain.com/admin/`

Change the local `admin` / `admin` password before production.

## Telegram

1. In BotFather, set the Mini App URL to `https://<vercel-app>/app` (or the site origin if BotFather wants origin only).
2. Keep `TELEGRAM_MINI_APP_URL` on the backend/bot in sync so notification buttons open the HTTPS Mini App.
3. Run the bot process on the API host (`python main.py` in `bot/`). Polling is enough for MVP. A webhook is optional later.
4. `BOT_SERVICE_SECRET` on the bot must match Django.

## Back office after deploy

Staff use Django Admin, not the Mini App:

- Filter and cancel requests
- Deactivate users (cancels their active requests)
- Create a **manual match** (Add Match). Check **Override hard rules** only when you intentionally pair requests the engine rejected
- Change match status, including CONNECTED

## Local reminder

```bash
docker compose up --build
docker compose exec backend pytest
```
