# Match outreach (Telegram account)

People whose requests we import from public channels never used Chamedoon, so the bot cannot message them. When one of their imported **send** requests gets a strong match, the Chamedoon Telegram account sends them one Persian DM. The DM reads like a person wrote it: it mentions their post (never the channel), when the first traveler goes, and has a Mini App link. Wording varies per message. Registered users are unchanged: the bot already messages them on every match.

```text
market-convert cron → imported demand → matching (unchanged)
outreach-build cron → OutreachMessage (QUEUED)
outreach_worker     → claims one message → sends it from the Chamedoon account → reports back
recipient taps link → Mini App login → imported request moves to their real account → travelers + DM buttons
replies             → you answer them yourself from the Telegram app; a reply that is exactly «لغو» or "stop" is recorded as an opt-out (no automatic answer)
```

## Rules built in

- Only imported **DEMAND** requests, posted in the last `OUTREACH_MAX_POST_AGE_DAYS` (14) days, with a CONNECTED match of score ≥ `OUTREACH_MIN_SCORE` (80) to an active, unexpired traveler.
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
| `OUTREACH_ENABLED` | default `true`: builds the queue daily (05:00 UTC cron). Messages are visible in Admin → Outreach messages |
| `OUTREACH_SENDING_ENABLED` | default `true`: lets the worker send. Nothing is sent unless a worker with the secret runs |
| `OUTREACH_SERVICE_SECRET` | optional. Without it, the backend accepts the secret whose SHA-256 is `OUTREACH_SERVICE_SECRET_SHA256` (set in settings; only the hash is in the repo). Set this variable, or a new hash, to rotate the secret |
| `OUTREACH_DAILY_LIMIT` | optional, default `10` |
| `OUTREACH_MIN_SCORE` | optional, default `80` |
| `OUTREACH_MAX_POST_AGE_DAYS` | optional, default `14` |
| `OUTREACH_COOLDOWN_DAYS` | optional, default `14` |
| `OUTREACH_SEND_START_HOUR` / `OUTREACH_SEND_END_HOUR` | optional, default `10` / `21` (Tehran) |

Environment changes need a redeploy. To stop sending immediately, use Admin → **Outreach state** → *Stopped* instead: it takes effect on the next claim, with no redeploy.

## Sending from Vercel (no PC needed)

Vercel can send by itself. Hobby crons run once a day each, so there are two send slots:

| Cron | UTC | Tehran | Does |
| --- | --- | --- | --- |
| `/api/cron/outreach-build/` | 05:00 | 08:30 | queue new DMs |
| `/api/cron/outreach-send/` | 07:00 | 10:30 | send one DM |
| `/api/cron/outreach-send-2/` | 12:00 | 15:30 | send one DM |

So at most two DMs a day, all limits above still apply, and replies stay with you in the Telegram app.
Each run connects, sends one message and disconnects, well inside the 60 s function limit.

Setup, once:

1. On the PC, double-click `outreach_worker/login_server.bat` (or `python login.py --server`) and log in.
   It creates a **separate** session for the server and writes `OUTREACH_TG_SESSION=...` to `outreach_worker/.env`.
   Never reuse the PC's `TG_SESSION` on the server: Telegram revokes a session used from two places at once.
2. In Vercel → Settings → Environment Variables (Production) add `OUTREACH_TG_API_ID` and `OUTREACH_TG_API_HASH`
   (same values as `TG_API_ID` / `TG_API_HASH`) and `OUTREACH_TG_SESSION` from step 1. Redeploy.
3. Stop running `worker.py` on the PC. Its other commands (`--dry-run`, `--test-to`, `--hold`, `--release`) still work.

`OUTREACH_TG_SESSION` is full access to the account: hand it to whoever edits Vercel through a secure channel
(a password manager share, not a chat). Until the three variables are set, the send crons answer 503 and send nothing.
If Telegram revokes the session, sending pauses for 24 h with `SESSION_INVALID`; repeat steps 1–2.

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

- `python worker.py --build` queues messages now instead of waiting for the 05:00 UTC cron.
- `python worker.py --test-to yourusername --limit 5` sends the next 5 queued DMs to that username only, each headed by its real recipient, matched travelers, source post and whether it will be sent. Their link opens the first matched traveler's listing instead of the tracked link; the queue is not touched. Real recipients land on their match (one traveler) or their request with all matches.
- On Windows you can double-click `login.bat` instead of running `python login.py`.

## 3. Moving to a server

The worker needs a stable, always-on machine, not Vercel: Hobby functions stop after 60 s, crons run once a day, and a user session that hops between data-center IPs gets flagged by Telegram.

```bash
docker build -t chamedoon-outreach outreach_worker
docker run -d --restart unless-stopped --env-file outreach_worker/.env --name outreach chamedoon-outreach
```

Run `login.py` once on the server, or copy the `.env` over a secure channel.

## Admin

- **Outreach messages**: every DM with its text and status: `QUEUED`, `SENDING`, `SENT`, `FAILED`, `SKIPPED`. Also shows `error_code`, `opened_at` (link tapped) and `replied_at`. *Do not send selected* skips queued messages.
- **Outreach opt-outs**: people who replied exactly «لغو» / "stop". Add anyone else who asks not to be contacted by hand.
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
