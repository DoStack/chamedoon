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
| P1G | a P1/P1q poster who did not answer, 24–48 h later, only if "P1G" is in Enabled kinds | the ad's one or two biggest gaps and the edit link. Once per person, ever |

Greeting: «سلام مریم جان، وقت بخیر.» when the Telegram name reads as a person's first name, else «سلام وقت بخیر.» or «سلام علیکم، وقت بخیر.», then «از تیم چمدون پیام می‌دم خدمتتون.» No message has an exit line or a button. Code: `backend/outreach/templates.py`, `housing.py`, `courier.py`, `followups.py`.

## Intro video (`backend/outreach/videos.py`)

Right after a first message the same account sends the intro video: seekers (S1, S1b, S2) the seeker video, posters (P1, P1q, P6) the homeowner one, with no caption. The worker uploads each file once per account and reuses it. Text and video are one touch for the "no reply, no more" rule, and each counts as one message toward the account's 3 an hour and 30 a day, so a first message is claimed only when both fit. A first message sent before videos existed gets its video within 3 days, inside the recipient's window. A video is never sent twice; a flood on a video pauses the account like one on a text. Files on bigde-vps: `/opt/chamedoon-outreach/videos/seeker.mp4` and `owner.mp4`.

The daily limit counts first messages per Toronto calendar day (videos not counted): the rollout step 5, 10, 30.

## Accounts and limits (`backend/outreach/accounts.py`)

- A claim must name its account. Each account has its own limits and its own pause.
- `PEER_FLOOD`: only that account waits 48 h, the message goes to another account. Two in 7 days on one account: switched off. Everything stops only when no account can send (all paused, off or logged out), and Mohammad is told.
- `FLOOD_WAIT`: only that account waits what Telegram asks; the same all-accounts rule applies.
- Softer pace: an account with its own *Min gap minutes* waits a random time between that gap and twice it (Sina after her first flood: 3, so 3 to 6 minutes). Without one, the pause is 1 to 3 minutes.
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
| field | price, rooms, area, move-in date, furnished, bills, deposit (several in one answer) | «ثبت شد.» + the next gap, or the thanks and the link |
| photo | joins their only live ad if the photo check says housing; several ads: asks which; else waits for the operator | «عکس‌ها اضافه شد.» + the next gap |
| phone | only after an explicit yes | «می‌ذاریم روی آگهی و همه می‌بیننش، باشه؟» |
| who / is_bot | truthful | «دستیار هوش مصنوعی چمدونم؛ …» |
| money, complaint, anything else | Mohammad is told | «این رو به تیم می‌گم و خبرتون می‌کنیم.» |

One answer per burst, after 1 to 5 minutes. A model that is down answers nothing; after 30 minutes Mohammad is told. Every line goes to the transcript group. In that group an operator sends `/take 12` to take chat 12 from the agent, `/agent 12` to give it back, `/stopchat 12` to close it. Admin → Outreach → Chats does the same.

## Ad completion (`backend/outreach/completion.py`)

When we write to someone whose ad we put on Chamedoon, we get what their ad is missing in the chat and save it on that exact ad.

- **Whose ad.** Only live offers the chat's Telegram id owns (`home/ownership.py`). With several, nothing is saved until they say which: «کدوم آگهی‌تون؟ «برلین، بدون قیمت» یا «برلین، ۱۵۰۰ یورو»». Another person's ad link is never sent.
- **What is missing, in this order:** photo, price, rooms (not for a room or studio), neighbourhood; then move-in date, furnished, bills, deposit. The first four make an ad complete. The phone is never asked; it goes on the ad only when they offer it and say yes. Parking and other amenities have no column and are not asked.
- **Asking.** One or two things per message, no buttons, no exit line. The only claim: ads with photos and a price reach more people, and an ad without a price does not match an alert with a budget.
- **Photos.** Every photo the owner sends in the chat is stored on its turn. It joins an ad when an operator picks the ad, or (agent on) when they have one live ad and the photo check (`HOME_VISION_MODEL`) says housing. Clearly not housing: dropped. Unsure, or no check: waits for the operator. A city channel post that went out without photos is taken down and posted again by the publisher under the channel's daily cap, when the ad is still in the publish window or claimed by its owner; otherwise its text is refreshed.
- **Edit link.** The ad's own link with its city and the token (`home_<cc>_<city>_l<id>_src-o-<token>`). Signed in, the owner sees «ویرایش آگهی», the same `edit_listing` the Mini App uses.
- **Done.** «ممنون، آگهی‌تون الان کامل شد:» and the view link. `extra["completion"]` on the first message keeps what was asked and filled, by whom, and `done_at`; Funnel shows `ad completed`.

**By hand (agent off).** Admin → Outreach → Chats → a chat → *Ad completion*: their ads with what is missing, the photos they sent, forms to save a field, add photos, get the edit link or the thanks. Each action shows the next line to send from the chat's account. Same from a shell:

```bash
python manage.py outreach_complete show 12
python manage.py outreach_complete set 12 --listing 345 price 1200
python manage.py outreach_complete photos 12 --listing 345
python manage.py outreach_complete done 12 --listing 345
```

**Agent (when on).** After «هنوز هست» it asks the first one or two gaps, saves every fact in an answer (`values`), asks the next gaps for up to three rounds, then sends the thanks once.

**No reply (P1G).** Off until `P1G` is added to Enabled kinds. Built from 10 h after an unanswered P1/P1q, held for a yes like every message, sent only by the same account, not before 24 h and not after 48 h, inside the recipient's window and the account's limits (it counts toward 3 an hour, 30 a day and the daily rollout step). Skipped if they answered, opened the link, stopped, the ad closed or is complete. A reply to it belongs to the first message's chat.

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
| `replied`, `complete`, `not_owner`, `no_first_message` | P1G: they answered, the ad has no core gap any more, the ad is not theirs, the first message is gone |
| `USERNAME_NOT_FOUND`, `NOT_A_PERSON`, `PRIVACY_PREMIUM_REQUIRED`, `ALLOW_PAYMENT_REQUIRED` | cannot be reached |
| `lease_expired` | the worker stopped mid-send; not retried |


## Live rules (Mohammad, 2026-11-06)

- Each account sends at most 30 a day, videos included, with no hourly limit and no minimum gap: only a random 1 to 3 minute pause between two sends (Mohammad's correction, 2026-11-06). The total daily limit is 30 × the accounts whose session works (`daily_limit_from_accounts`); Monis counts once logged in.
- Only ads and requests from the last 3 days (`max_post_age_days` 3), newest first. The daily note says how many people qualify.
- P1G (one gap follow-up 24 to 48 hours after an unanswered P1/P1q) and the follow-ups are on.
- The agent is on, to serve people, not to promote. A reply thanks them, says what was done with its link (their ad is on Chamedoon, or the ads that fit their budget), and offers one useful next step: a missing field or photo, else the bot for inquiry news. Anything unclear goes to Mohammad.
- Someone who writes in English is answered in English: the fixed reply is translated by the model, and it is sent only if every link and number survived; otherwise Mohammad is asked to answer.
- A message Mohammad sends by hand from the account is recorded (`/outreach/outbound/`), and the agent never answers what he already answered. After a restart the worker reads the last messages of each chat to catch up.
- Every agent reply is copied to Mohammad's housing-bot chat (`transcript_chat_id` and `alert_chat_id` are his id until a group exists).
