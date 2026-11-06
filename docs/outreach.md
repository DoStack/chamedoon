# Outreach (Telegram accounts)

Chamedoon writes one warm first message to people in the six launch cities who posted a housing ad or request, or a courier trip or request, in a Telegram group. An AI agent carries the chat if they answer. Nobody who does not answer hears from us again. The goal is a start of the housing bot, which makes later messages free. Spec: `projects/chamedoon/OUTREACH-PROPOSAL-2026-11-04.md` in the hub.

```text
housing job / market crawl ──> HomeListing, ItemRequest (poster fields from Telegram)
outreach-build cron (05:00 UTC) ──> OutreachMessage, HELD "awaiting_yes" until a batch is approved
Admin: Approve ──> QUEUED
outreach_worker on bigde-vps (sina, sara, monis) ──> claim ──> send ──> result
reply ──> worker ──> /outreach/inbound/ ──> agent (OmniRoute) ──> /outreach/agent/next/ ──> worker sends
link tap ──> Mini App, src "o-<token>" ──> opened, city, ready alert, owner claim
```

## Switches (Admin → Outreach → Settings)

Every new message waits as **Held** (`awaiting_yes`) until a person approves it. Mohammad gives the yes per batch.

| Field | Default | What it does |
| --- | --- | --- |
| Outreach on/off | off (`OUTREACH_ENABLED`) | Build the queue |
| Sending on/off | off (`OUTREACH_SENDING_ENABLED`) | Hand queued messages to the worker |
| Enabled kinds | `S1,S1B,S2,P1,P1Q` | Models that may be built. Add `P6`, `C1`, `C2`, `C3` by name |
| Auto queue | off | On: new messages skip the Held step. Keep off |
| Daily limit | 5 | First messages per Toronto day, all accounts together: the rollout step (5, 10, 30) |
| Per account | 3 an hour, 30 a day, 15 min + random 0–10 min apart | An account row can override |
| Window | 10:00–20:00 | On the **recipient city's** clock |
| Max post age | 14 days | Older ads and requests are skipped, newest go first |
| Agent enabled | off | Off: replies are stored and shown, nothing is answered |
| Follow-ups enabled | off | P3, P5 and the contact notice |
| Transcript chat, alert chat, operator ids | blank | Group that sees every chat; who may take a chat over |

Admin → Outreach → **State** → *Stopped* stops everything at the next claim. The stop rules also set it, and only a person clears it.

## Who gets a message

- Only the six cities: Toronto, Montreal, Vancouver, Berlin, Hamburg, Cologne, matched by `HomeChannel` name and aliases. The listing's own city wins; an empty city falls back to the group's default city. Courier: one end of the route in a launch city.
- Only a verified person: `poster_kind=person`, `poster_read_at` set, `poster_via` `sender` or `forward_author`, a valid username. No channel posts, bots, anonymous admins or forwarders. No message by phone number.
- Not a member (signed in to the Mini App, or opened the housing bot), not opted out, never sent a first message before (one unanswered message, ever).
- One message per person, about their newest ad. Priority: S1, S1b, P1/P1q, S2, P6. Agent listings get P6.
- Re-checked at send time: a closed, duplicate or in-review ad is skipped (`listing_closed`); S1 whose matches went away becomes S1b.

| Kind | To | Says |
| --- | --- | --- |
| S1 | seeker with budget and matching ads | how many ads fit, link to the filtered city feed |
| S1b | seeker without budget | asks the budget |
| S2 | seeker, nothing fits | offers to tell them |
| P1 / P1q | owner or tenant with an ad | ad is on Chamedoon, link / «هنوز موجوده؟». Half and half by token. The reposter line is added at 5+ posts in 30 days |
| P6 | agent | count of their ads in the city |
| C1 | courier sender with a matched traveler | as before, new greeting |
| C2 | imported traveler | their trip is on Chamedoon |
| C3 | February and March only | Nowruz wave |
| P3, P5 | only people who chatted or signed up | the week's real numbers; «هنوز موجوده؟» on day 12 |

Greeting: «سلام مریم جان، وقت بخیر.» when the Telegram name reads as a person's first name, else «سلام وقت بخیر.» or «سلام علیکم، وقت بخیر.», then «از تیم چمدون پیام می‌دم خدمتتون.» No message has an exit line or a button. Code: `backend/outreach/templates.py`, `housing.py`, `courier.py`, `followups.py`.

## Intro video (`backend/outreach/videos.py`)

Right after a first message the same account sends the intro video: seekers (S1, S1b, S2) the seeker video, posters (P1, P1q, P6) the homeowner one, with no caption. The worker uploads each file once per account and reuses it. Text and video are one touch for the "no reply, no more" rule, and each counts as one message toward the account's 3 an hour and 30 a day, so a first message is claimed only when both fit. A first message sent before videos existed gets its video within 3 days, inside the recipient's window. A video is never sent twice; a flood on a video pauses the account like one on a text. Files on bigde-vps: `/opt/chamedoon-outreach/videos/seeker.mp4` and `owner.mp4`.

The daily limit counts first messages per Toronto calendar day (videos not counted): the rollout step 5, 10, 30.

## Accounts and limits (`backend/outreach/accounts.py`)

- A claim must name its account. Each account has its own limits and its own pause.
- `PEER_FLOOD`: that account waits 48 h, the message goes to the next account. Two in 7 days on one account: switched off. Two accounts in a week, or every account limited at once: everything stops and Mohammad is told.
- `FLOOD_WAIT`: that account waits what Telegram asks.
- Premium-only or paid messages, a gone username: failed with the code, not retried.
- A worker that dies mid-send: `lease_expired`, never sent again.
- A revoked session: that account stops, Mohammad is told. Only he logs it in again.
- A chat stays with the account that started it; its follow-ups too.

## The agent (`backend/outreach/agent.py`, `actions.py`)

The model (OmniRoute, `OPENROUTER_BASE_URL`, model from settings) only names one intent from a closed list. The program does the rest, on that person's own listings (`home/ownership.py`), with numbers and links from the database and fixed sentences:

| Intent | Action | Reply |
| --- | --- | --- |
| available | extend (or renew an expired ad) | «عالی، تمدیدش کردیم.» + once: one missing field (H4) + bot start link (H3) |
| rented | rented, channel posts edited | «مبارکه! از لیست برداشتیم.» |
| several ads, unclear | asks | «کدوم آگهی، تورنتو یا ونکوور؟» |
| remove | removes their ads, blocks their new posts at import | «حق با شماست، برداشتیم. ببخشید.» |
| stop | opt-out for good | «باشه، ببخشید مزاحم شدم.» |
| budget / yes, tell me | alert from their request, filtered link | «۶ تا آگهی زیر ۱۵۰۰ دلار دارم: …» |
| field | price, rooms or area | «ثبت شد.» |
| photo | joins their only live ad | «عکس‌ها اضافه شد.» |
| phone | only after an explicit yes | «می‌ذاریم روی آگهی و همه می‌بیننش، باشه؟» |
| who / is_bot | truthful | «دستیار هوش مصنوعی چمدونم؛ …» |
| money, complaint, anything else | Mohammad is told | «این رو به تیم می‌گم و خبرتون می‌کنیم.» |

One answer per burst, after 1 to 5 minutes. A model that is down answers nothing; after 30 minutes Mohammad is told. Every line goes to the transcript group. In that group an operator sends `/take 12` to take chat 12 from the agent, `/agent 12` to give it back, `/stopchat 12` to close it. Admin → Outreach → Chats does the same.

## After the link (`housing.note_open`, `landing`)

The link carries the city and `src-o-<token>`. On sign-in: opened and member counted (only for the recipient; a forwarded link counts nothing), `HomeUserPrefs.city` set if empty, `first_src` stored. The Mini App asks `me/outreach/landing/`: a seeker gets a ready alert from their request, off until one tap; the app asks Telegram's write access once. An owner opening their own ad claims it: the badge «تأیید صاحب آگهی», and it is posted to its city channel first, past the 3-day window, under the daily cap. Their ads, crawled ones included, are in «آگهی‌های من» with views, saves and contacts taken (`HomeListingStat`, counts only).

## Funnel

Admin → Outreach → **Funnel**: sent, replied, opened, member, bot start, active, back after 7 and 30 days, stopped, by model, account, wording and country. Under 30 sent a row decides nothing. Rule after 100 messages or 4 weeks: member ≥ 10% send more; 5–10% change the text; < 5% keep the model; stopped > 5% stop and review.

## The worker on bigde-vps

`/opt/chamedoon-outreach`, Docker, `restart: unless-stopped`. One process, one session per account (`SINA_TG_SESSION`, `SARA_TG_SESSION`, `MONIS_TG_SESSION`), each session in this one place only. `.env` holds the sessions, `TG_API_ID`/`TG_API_HASH`, `API_BASE_URL`, `OUTREACH_SERVICE_SECRET`; it is never committed or printed.

```bash
docker compose run --rm outreach python worker.py --whoami    # which account each session is; sends nothing
docker compose run --rm outreach python worker.py --dry-run   # next queued messages
docker compose up -d --build
```

Logging an account in (Monis, or after a revoke) is Mohammad's step: `python login.py` on a terminal, then move the session line into the VPS `.env` and remove it from the laptop.

The old Vercel send crons stay scheduled and send nothing unless `OUTREACH_VERCEL_SENDER` is set.

## Error codes

| Code | Meaning |
| --- | --- |
| `awaiting_yes` | held until the batch is approved |
| `listing_closed`, `request_closed`, `demand_closed` | the ad or request is gone |
| `stale` | older than the max post age |
| `member`, `claimed` | already uses Chamedoon |
| `contacted`, `opted_out` | one message ever / asked not to be contacted |
| `outside_cities` | not one of the six |
| `USERNAME_NOT_FOUND`, `NOT_A_PERSON`, `PRIVACY_PREMIUM_REQUIRED`, `ALLOW_PAYMENT_REQUIRED` | cannot be reached |
| `lease_expired` | the worker stopped mid-send; not retried |
