# Match outreach (Telegram account)

People whose requests we import from public channels never used Chamedoon, so the bot cannot message them. When one of their imported **send** requests gets a strong match, the Chamedoon Telegram account sends them one Persian DM. The DM says where we saw their post, how many travelers we found, and has a Mini App link. Registered users are unchanged: the bot already messages them on every match.

```text
market-convert cron → imported demand → matching (unchanged)
outreach-build cron → OutreachMessage (QUEUED)
outreach_worker     → claims one message → sends it from the Chamedoon account → reports back
recipient taps link → Mini App login → imported request moves to their real account → travelers + DM buttons
replies             → «لغو» = opt-out (auto-confirmed); anything else you answer from the Telegram app
```

## Rules built in

- Only imported **DEMAND** requests, posted in the last `OUTREACH_MAX_POST_AGE_DAYS` (5) days, with a CONNECTED match of score ≥ `OUTREACH_MIN_SCORE` (80) to an active, unexpired traveler.
- One message per request, ever. At most one message per person every `OUTREACH_COOLDOWN_DAYS` (14) days.
- Never to channel handles, our own handles, or usernames ending in `bot`.
- Everything is checked again right before sending. A request that closed, a match that expired, or a person who opted out or signed up in the meantime is skipped.
- At most `OUTREACH_DAILY_LIMIT` (10) attempts per Tehran day, only between `OUTREACH_SEND_START_HOUR` (10) and `OUTREACH_SEND_END_HOUR` (21) Tehran time, with a random 150–330 s pause between sends.
- If Telegram says the account is sending too much to strangers (`PEER_FLOOD`), sending pauses for 48 hours. On `FLOOD_WAIT` it pauses for the time Telegram asks.
- A message is never sent twice. If the worker dies mid-send, that message is marked `lease_expired` and not retried.

Raise the daily limit slowly, and only while opt-outs stay low and Telegram has not limited the account. Use one account only.

## 1. Vercel environment

| Variable | Value |
| --- | --- |
| `OUTREACH_ENABLED` | `true` builds the queue daily (05:00 UTC cron). Messages are visible in Admin → Outreach messages |
| `OUTREACH_SENDING_ENABLED` | `true` lets the worker send. Leave `false` for a day first if you want to read the real messages in Admin |
| `OUTREACH_SERVICE_SECRET` | long random string; the worker sends it as `X-Outreach-Secret` |
| `OUTREACH_DAILY_LIMIT` | optional, default `10` |
| `OUTREACH_MIN_SCORE` | optional, default `80` |
| `OUTREACH_MAX_POST_AGE_DAYS` | optional, default `5` |
| `OUTREACH_COOLDOWN_DAYS` | optional, default `14` |
| `OUTREACH_SEND_START_HOUR` / `OUTREACH_SEND_END_HOUR` | optional, default `10` / `21` (Tehran) |

Environment changes need a redeploy. To stop sending immediately, use Admin → **Outreach state** → *Stopped* instead: it takes effect on the next claim, with no redeploy.

## 2. The worker on your PC (pilot)

Use a dedicated Telegram account with 2FA on and a clear profile (name «چمدون | Chamedoon», logo, bio with the bot link).

```bash
cd outreach_worker
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Fill `.env`: `TG_API_ID` and `TG_API_HASH` from https://my.telegram.org, `API_BASE_URL` (your Vercel URL) and `OUTREACH_SERVICE_SECRET` (same value as on Vercel). Then:

```bash
python login.py
```

Telegram asks for the phone number, the login code and the 2FA password. Type them yourself. The session is saved to `.env` as `TG_SESSION`. That value is full access to the account: never paste it anywhere or commit it (`.env` is git-ignored).

```bash
python worker.py --dry-run
python worker.py
```

`--dry-run` prints the next queued messages and whether sending is open. It does not touch Telegram or the queue. `worker.py` then runs until you stop it (Ctrl+C). Keep the same account logged in on your phone to answer replies.

## 3. Moving to a server

The worker needs a stable, always-on machine, not Vercel: Hobby functions stop after 60 s, crons run once a day, and a user session that hops between data-center IPs gets flagged by Telegram.

```bash
docker build -t chamedoon-outreach outreach_worker
docker run -d --restart unless-stopped --env-file outreach_worker/.env --name outreach chamedoon-outreach
```

Run `login.py` once on the server, or copy the `.env` over a secure channel.

## Admin

- **Outreach messages**: every DM with its text and status: `QUEUED`, `SENDING`, `SENT`, `FAILED`, `SKIPPED`. Also shows `error_code`, `opened_at` (link tapped) and `replied_at`. *Do not send selected* skips queued messages.
- **Outreach opt-outs**: people who replied «لغو». You can add one by hand.
- **Outreach state**: *Stopped* switch, current pause and reason, last worker heartbeat, last queue build.

Common `error_code` values:

| Code | Meaning |
| --- | --- |
| `USERNAME_NOT_FOUND` | the handle no longer exists |
| `NOT_A_PERSON` | the handle is a channel, group or bot |
| `PRIVACY_PREMIUM_REQUIRED` | the person only accepts messages from Premium users |
| `ALLOW_PAYMENT_REQUIRED` | the person charges for messages |
| `demand_closed`, `stale`, `no_match`, `claimed`, `cooldown`, `opted_out` | skipped at send time |
| `lease_expired` | the worker stopped mid-send; not retried |

Locally: `python manage.py build_outreach_queue --dry-run` prints what would be queued.
