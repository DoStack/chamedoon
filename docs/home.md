# Chamedoon Home (housing board), Phase 0

Reads housing posts from Persian Telegram channels and groups abroad (Germany, Canada), asks the AI whether each is a real listing, extracts structured fields, drops reposts, stores the photos, and keeps everything in the database. Nothing is published to a channel or the Mini App yet.

```text
HomeSource (enabled channels/groups, per-country defaults)
  → ingest   member Telegram account reads new messages and album parts (home/ingest.py)
  → HomePost raw text, author, link; HomeMedia one row per photo, linked to its post
  → review   exact repost? skip. Otherwise ai.llm.complete: is it a listing? extract fields (home/review.py)
  → HomeListing  structured row, or HomePost.status = rejected with a reason
  → media    download, shrink, hash and store the photos of accepted listings (home/media.py)
  → duplicates linked to the first listing (wording, contact + price, shared photo)
  → vision   optional: the AI reads the photos to fill gaps the text left (home/vision.py)
  → publish  one Persian post per trusted listing, with its album and three buttons, to its city and country channels (home/publish.py)
```

## The AI step

`home/review.py` uses the same endpoint as the courier import: `ai.llm.complete` (OpenRouter free models first, then OpenAI). One call decides `accept` and fills the fields. Country notes are added to the prompt, so Canada's «بیسمنت», «لیز ترنسفر», sqft, first/last month and Germany's Warm/Kalt, Kaution, Anmeldung are read in context.

- Posts under 40 characters are rejected by rule. Exact reposts (same wording in the same country within 45 days) are rejected as `repost` **without a model call** and extend the original listing's `last_seen`. This is most of Toronto's traffic.
- Rejects keep a reason: `ad`, `noise`, `not_housing`, `incomplete`, `expired`, `repost`, `other`.
- Every field the model returns is checked in code: enums, dates, money ranges, the country/currency pair. Doubtful values set `needs_review` with a note. A bare amount takes the currency of the source (Germany EUR, Canada CAD), never a guess from a symbol.
- A missing price is normal («پیام بدید»). It is flagged `price_missing`, not queued.
- Square feet are converted in code (the model never does arithmetic). Phone numbers are normalised (`+1` and ten digits for Canada).
- Province rules are labels, never a reason to hide a listing: a damage deposit in Ontario or any deposit in Quebec sets `flags.deposit_warning`.
- A sender with 15+ posts or 5+ distinct accepted listings in 30 days is labelled `agent` (`flags.repeat_poster`).

## Photos and albums

An album is several Telegram messages sharing a `grouped_id`; only one carries the caption. The reader keeps the captionless parts as `HomeMedia` rows and links them to the captioned post after the pages are read, so a caption on a later page still works. Photos are fetched only for accepted, non-duplicate listings, at most 10 per post.

Each photo is shrunk to 1280 px, a 480 px thumbnail is made, and a 64-bit perceptual hash is taken (watermark strip cropped off). Two listings in the same city that show the same photo are merged when the price is within 10% or the contact is the same. The same photo at a very different price is only noted (`flags.shared_photo_with`), because agents reuse building photos.

Storage is Cloudflare R2 (S3 API, 10 GB free, no egress fee), signed by hand so the Vercel bundle needs no boto3. **Until the R2 variables are set the media step does nothing.**

The optional vision pass (`HOME_VISION_ENABLED` plus an image-capable `HOME_VISION_MODEL`) sends the caption, what was extracted and up to four stored photos to the AI, and only fills fields the text left empty.

## Settings (Admin → Chamedoon Home)

Everything below is edited in the admin and takes effect on the next run, with no redeploy. A blank value falls back to the server environment.

| Where | What |
| --- | --- |
| **Run and status** | KPIs (AI, Telegram, storage, posts waiting, listings, photos), one button per step, the full result of the last run, and every source with its last read and last error |
| **Settings** | Master switch, a switch per step, lookback days, batch sizes, photo storage (R2 account id, bucket, public URL, access key id, secret) and the photo-reading model. The R2 secret is never shown again after saving |
| **Sources** | Switch sources on or off, set country/city/currency defaults, mark an agency catalog, and paste the **invite link** of a private group. Links live in the database, not in the repository |

Still environment only: the Telegram session (`OUTREACH_TG_*` or `MARKET_TG_*`) and the AI keys. `HOME_SOURCE_INVITES` and the `R2_*` variables remain as a fallback.

To read the private Montreal group, open Sources → `khane_montreal` and paste its invite link. The member account must already be in the group.

## Posting to the channels

Each country has its own channel (Admin → Channels, seeded: `@Chamedoon_Germany` for DE, `@Chamedoon_CA` for CA) and posts go out through the housing bot `@chamedoon_home_bot`, a different bot from the courier bot. Posting is **off** until you switch it on.

1. Admin → Home settings → Channel posting: paste the **Home bot token** (from BotFather), keep posting off.
2. Admin → Channels → select both → action *Check that the housing bot can post here*. It confirms the bot is an admin who can post and saves the chat id.
3. Run and status → *Preview posts* shows the next five posts as they will appear. Nothing is sent.
4. When the previews look right, switch *Publish enabled* on in Home settings. Then *Post to channels* (or the cron step `publish`) sends a few per run.

What is posted: active listings the review trusted (not queued for review, not a repost), seen in the last 10 days (*publish window*), oldest first, 5 per run, at most 40 per channel per day. Listings with photos wait until the photos are stored, then go out as one album (or one photo) with the caption; if Telegram refuses the photos the post goes out as text. Every post carries the three buttons below: on a text or single-photo post directly, under an album as a reply message. A listing is posted once. The advertiser's phone number is not shown unless *Show phone* is on; the post links to the original message instead.

### City channels

Admin → Channels holds one row per channel: a row with a blank city is the **country aggregate** and gets every listing of its country; a row with a city gets the listings of that metro. A listing matches a city row when its city or area is the city name or one of the row's **aliases** (suburbs and spellings, comma or line separated), so North York, Richmond Hill or Newmarket go to Toronto and Laval or Longueuil to Montreal. A city without a row (Frankfurt, Bochum, Ottawa) goes to the country channel only. Every listing goes to both its city channel and its country channel; city posts are recorded in *Home publications*, one row per listing and channel, so a listing is never posted twice to the same channel.

Seeded on 2026-10-21 (migration `0014`): Toronto, Montreal, Vancouver, Berlin, Hamburg, Cologne. *Active from* is the moment the city channels went live: listings already in the country channel before it were copied into the city channels by hand and are skipped. Add a city by adding a row with its username, chat id (`-100…`) and aliases; no code change.

### Post text and buttons

The text (`listing_lines` in `home/publish.py`) leads with what a renter scans for, in this order: type and term (`🏠 آپارتمان · سابلت`), city (`📍 تورنتو · North York`: the metro of the channel or of the city channel the listing routes to, in Persian, then the suburb when it differs), neighbourhood (`🏘 محله: …`, from `area`), price with period and warm/cold, deposit, size and rooms, amenities (`✨` furnished, bills included, Anmeldung, and the review's flags parking, pets, no smoking, couples, students only), conditions (`📋` first and last month, credit check, WBS), dates, roommate, lister, summary, provincial note, contact, the photo count (only on a text post whose photos Telegram refused), hashtags (`#Toronto #آپارتمان #سابلت`). The term is the type when it is sublet, lease transfer or short stay; otherwise a weekly or nightly price means short stay, `untermiete` sublet, `temporary` or an end date a temporary rental, and 12+ months or first-and-last a long lease. A caption over 1024 characters drops summary and hashtags first; title, city, price and contact always stay.

Every post has three buttons (`post_buttons`):

| Button | Opens |
| --- | --- |
| «جزئیات و عکس‌ها» | the listing in the Mini App, all photos |
| «مشابه‌ها» | the feed for the same city and type, rent 80–120% of this one (a wanted post: offers up to its budget) |
| «🔔 گوش به زنگ» | the housing bot with `start=alert_home_<cc>_<city>`; the bot side of alerts is a separate change |

**Albums.** The Bot API puts no buttons on an album, so under an album the keyboard goes out as a short reply («👆 جزئیات و عکس‌ها»), the way the single glass button has been sent since `c7a31e1`. Readers keep the swipeable album in the channel. If Telegram refuses the photos, the text post carries the buttons and says how many photos «جزئیات و عکس‌ها» opens. All buttons use the housing bot's Main Mini App link `https://t.me/<home bot>?startapp=…`, the shape the courier glass buttons moved to on 2026-10-18 (`ee27e27`, `df11aed`); a startapp link inside the post text does not open, so the text carries none.

**Start parameter grammar** (`home/deeplink.py`, mirrored in `miniapp/static/miniapp/startapp.js`). Telegram allows `A-Za-z0-9_-`, at most 64 characters; parts are joined by `_`, all lower case:

```text
home                                        landing (old links keep working)
home_<id>                                   listing (old links keep working)
home_<cc>[_<city>][_l<id>][_t-<type>][_p<min>-<max>][_src-<tag>]

<cc>      ca | de
<city>    ASCII slug of the metro: toronto montreal vancouver berlin hamburg cologne (any other city: its name, letters and digits only)
l<id>     open listing <id>
t-<type>  property type, "_" written "-": t-room, t-lease-transfer
p<a>-<b>  rent band in the listing's currency (p0-<b>: up to <b>)
src-<tag> channel the click came from: "ch" + first 3 letters of the city channel's metro (chtor chmon chvan chber chham chcol), or "ch" + country code for the country channel (chca chde)
```

Examples: `home_ca_toronto` (Toronto feed, suburbs included), `home_ca_toronto_l123_src-chtor` (listing 123, clicked in the Toronto channel), `home_ca_toronto_t-room_p750-1150_src-chtor` (similar rooms). Too long a value drops the source, then the band. A city filter on a metro with a city channel matches its aliases too, the same names that route posts there.

Render a sample post (no Telegram call):

```bash
cd backend && python manage.py shell -c "
from home.models import HomeListing, HomeChannel
from home.publish import format_listing_text, post_buttons, metro_of
l = HomeListing.objects.filter(status='active').order_by('-id').first()
ch = HomeChannel.objects.filter(country=l.country, city=metro_of(l)).first() or HomeChannel.objects.get(country=l.country, city='')
print(format_listing_text(l, limit=1024, phone=False, metro=metro_of(l, ch), photos=l.post.media.filter(status='stored').count()))
for row in post_buttons(l, ch): print(' | '.join(f\"{b['text']} -> {b['url']}\" for b in row))
"
```

## Mini App (Persian and English)

`/app/home/` is the housing feed and `/app/home/<id>/` a listing. Both are built from the Chamedoon Mini App pieces (base template, IRANSans, Telegram theme colours, segmented control, filter sheet, card list, list loader), so they look like the rest of Chamedoon, and they speak Persian or English with the same language switch. All new styles are `house-*` classes appended to `app.css`; no existing rule changed, and the main Chamedoon pages are untouched.

- **Feed:** listings or "wanted" posts, a country button (flag and name) that opens the filters, and a filter sheet with country, city (with counts), type, max rent, available-by date, owners only and furnished only. Cards show the first photo and the photo count, the type, the place, the price with its currency, and labels (new, owner or agent, no price).
- **Listing:** a swipeable photo gallery, the facts table, the Persian or English summary, the provincial deposit note, a safety line, and contact: open the Telegram chat, call or copy the phone number, or open the original post (never a private-group link). *Already rented* (two different people expire the listing) and *Report a problem* (sends it to the review queue).
- **Login:** the Mini App accepts Telegram's sign-in from the courier bot or from the housing bot (whose token is in Home settings), so `@chamedoon_home_bot` can open it.
- **Start links:** `startapp=home` opens the housing landing, `startapp=home_<id>` a listing, and the channel links below open the feed filtered to a country, city, type and rent band. The city opened last is remembered with the country: a bare feed link and the landing's buttons reopen it; choosing *any city* in the filters forgets it.

One BotFather step is needed: for `@chamedoon_home_bot` set the main Mini App URL to `https://<site>/app/home/` (BotFather → Bot Settings → Configure Mini App). The channel check fills in the bot username used in the links.

### Feed filters

The filter sheet also has a **text search** (area, city, summary and the original text), a **neighbourhood** box with the most common areas of the chosen city as chips (with counts), a **price range** (min and max monthly rent), and a **term** choice: lease, sublet (a fixed end date or a sublet post) or short stay (a short-stay post or a weekly or nightly price). Under the toolbar, **🔔 گوش به زنگ** opens the housing bot's alert setup with the current filters (see below).

## Alerts («گوش به زنگ») in the housing bot

The housing bot answers in Persian, with no Mini App needed:

- `/start`: a short menu with the six city buttons, *my alerts*, *search* and the Mini App. A city button starts an alert.
- `/start alert_home_<cc>_<city>` (the button under channel posts, e.g. `alert_home_ca_toronto`): the city is filled in and setup starts at the first question. The feed button adds what its filters already say and those questions are skipped: `_g<f|m|c|x>` (who), `_m<max>`, `_t<r|c|s|a|x>` (room, own place, studio, apartment or suite, either), `_s<l|s|t|x>` (lease, sublet or temporary, short stay, either). The city may be the channel's city or any alias (`koln` works for Cologne).
- `/alerts`: the list, with pause/resume, search and delete for each, and *✏️ complete* for an alert left half-way.
- `/search`: the latest matching listings of a city or of an alert, five per page with next/previous buttons, each with the original post, the contact and the Mini App link.
- `/stop` pauses every alert of the user; `/cancel` stops waiting for typed text.

**Questions.** One at a time, in this order, each with a «مهم نیست» button:
1. who is looking: woman, man, couple;
2. budget: **type the number** (`1100`, `۱۱۰۰ یورو`), or a band as a shortcut (CAD 800 to 3,000, EUR 400 to 1,500);
3. room or own place: room, *own place* (studio, apartment, basement, house; never a room), studio, apartment or suite; «مهم نیست» = either;
4. rooms or size: 2+, 3+ or 4+ rooms, or 40+, 60+ or 80+ m². Only asked for own places;
5. term: lease, sublet or temporary (short stays too), short stay only; «مهم نیست» = lease or sublet, not a few nights;
6. Anmeldung needed (Germany only).

The alert is saved when the city is chosen and each answer as it comes (`HomeAlert.answered`), then *neighbourhoods* (one or several, separated by commas or «یا») can be added.

**Answered, «مهم نیست», never answered.** An answer filters. «مهم نیست» is an answer that means no filter on that field. A field **never answered** (an alert from before 2026-10-22, or a setup left half-way) is strict, so only listings that state it match:
- budget never answered: only listings with a price;
- who never answered: only places open to anyone (no women-only, men-only or couples-only);
- type never answered: only listings whose kind is known;
- rooms never answered: only whole homes that give rooms or m²;
- Anmeldung never answered (Germany): only listings that say yes or no.

Migration `0017` marks existing alerts as having answered budget, type and term only.

The alert message writes what the listing does not say as «نامشخص»: the price, the size, and in Germany the Anmeldung. This also covers the fields the user answered with «مهم نیست», so a match is never presented as better than its post.

**Matching.** A new trusted **offer** matches when it is:
- in the metro: the city or a suburb or district from the city channel's aliases, the same rule as channel posting; a city without a channel row matches by name;
- at or under the budget, weekly and nightly prices turned into a monthly figure. A listing **without a price** reaches a budget alert, marked «قیمت: نامشخص»;
- of the chosen type. A sublet, lease transfer or short stay says how long, not what kind of place, so its kind is read from the text: room words (room, اتاق, WG, chambre) or whole-home words (studio, 1-bed, suite, Wohnung, آپارتمان, ۲ خوابه). When the text says neither, the listing reaches room and own-place alerts alike;
- of the chosen term. A post flagged temporary or Untermiete counts as a sublet even without dates;
- for the person: women-only and men-only places reach only that alert; a couple skips places for one person;
- at least the rooms or size asked for, for whole homes only. Unknown values pass, shown as «نامشخص»;
- not marked "no Anmeldung" when Anmeldung is needed. Unknown passes, shown as «نامشخص»;
- in one of the neighbourhoods. Both sides are compared in plain Latin: accents dropped, `&` read as "and", common Persian district names turned into Latin (هاربورگ is Harburg). In Toronto a major intersection becomes its district (North York, Richmond Hill, Downtown, Scarborough, Etobicoke, Mississauga), so *Yonge & Finch* matches *Bayview & Finch* and *Yonge & Steeles*.

**Rooms rule (review).** `rooms` is the number the post writes, never computed. Bedrooms count as written in every country: «۲ خوابه», 2-bed, 2 bedrooms and 2BR are all 2, and 1 bedroom plus den is 1. A German «2-Zimmer» is 2. A living room is never added. It stays empty for a room in a shared home and for Quebec «۴½» sizes.

Migration `0016` added suburbs and districts to the German city channels, which also routes channel posts: Cologne gets Hürth, Frechen, Pulheim, Bergisch Gladbach, Kalk, Nippes, Ehrenfeld, Lindenthal, Sülz, Deutz, Porz, Rodenkirchen and Chorweiler; Hamburg gets Norderstedt, Altona, Eimsbüttel, Harburg, Wandsbek, Bergedorf, Wilhelmsburg, Barmbek and Hamburg-Nord; Berlin gets its districts. Mitte, Mülheim, Potsdam, Leverkusen, Düsseldorf and Neuss were left out on purpose.

**Saved requests:** a user's own active *wanted* post made in the Mini App (`/app/home/new/?kind=wanted`) gets an alert automatically (its city, budget, type and area; what the post does not state stays never answered), shown in `/alerts`, and is notified the same way. Closing the post switches the alert off. Wanted posts read from Telegram groups never get alerts.

**Sending** is the job step `alerts` (`/api/cron/home/?step=alerts`, also run at the end of a whole run), with its own lock. Each user hears about a listing **once**, whatever alerts matched, at most *Alerts daily cap* messages a day (default 10). Only listings accepted after the alert was made, in the last three days, are sent. Each message has *👀 دیدم*, *🔕 دیگه نمی‌خوام* (pauses that alert), the Mini App link and the city channel. A user who blocked the bot (403) has all their alerts switched off; a rate limit stops the run and retries later. Records: Admin → *Home alerts* and *Home alert sents*.

### Turning it on

1. Home settings → *Alerts*: set the **webhook secret** (letters, digits, `_`, `-`; or the `HOME_WEBHOOK_SECRET` env variable). Never in git. Without it the webhook refuses every update.
2. Check what would be sent: `python manage.py home_webhook --url https://<site>` (dry run: prints the setWebhook payload with the secret masked and the bot commands; nothing goes to Telegram).
3. Set it: `python manage.py home_webhook --url https://<site> --apply` (setWebhook to `/api/home/bot/webhook/` with the secret, and setMyCommands). `--info` reads getWebhookInfo; `--delete --apply` removes it.
4. Switch **Alerts enabled** on (off by default). The cron step `alerts` then sends.

## Courier channel is untouched

Housing never posts to the courier channel or group (`TELEGRAM_CHANNEL_*`). The `home` package has no import of the courier notifications code, the courier job never imports `home`, and tests fail if either changes. Housing will publish only to its own per-country channels, set in the admin.

## Running it

```bash
# production: the cron endpoint, Authorization: Bearer $CRON_SECRET
GET /api/cron/home/?step=ingest              # read sources (first run fills 10 days, Home settings → lookback)
GET /api/cron/home/?step=review&limit=15     # AI-review the newest waiting posts
GET /api/cron/home/?step=media&limit=12      # fetch photos of accepted listings
GET /api/cron/home/?step=vision              # read photos for listings with gaps
GET /api/cron/home/?step=alerts              # message users whose alerts match new listings
GET /api/cron/home/                          # all four, within the function's time
```

A Vercel call is short, so call each step repeatedly until `remaining` is 0. Locally, with the env set:

```bash
python manage.py home_job --step ingest --days 10
python manage.py home_job --step review --limit 300 --seconds 1500
python manage.py home_job --step media --media-limit 200 --seconds 1500
# live, on a server: the whole job every 10 minutes until stopped
python manage.py home_job --every 10 --seconds 540
```

Vercel runs the job six times a day. For live posting run `home_job --every 10` on a server (the VM); the job lock keeps it and the Vercel cron from reading at the same time.

Sources are seeded by migration: Germany and Canada, each with a few channels switched on and the rest found by search left off until someone reads a sample. Edit them in Admin → Home sources. `fetch_photos` can be turned off per source.

## Admin lists

- **Sources**: switch on/off right in the list (or the bulk actions), add a source with *Add*. Columns *Posts 7 d* and *Accepted 7 d* count posts by post time.
- **Channels** (the city → channel routing table): one row per channel with country, city, aliases and *active from*. *Enabled* is the publish switch for that channel and can be changed in the list or with the bulk actions. *Posts 24 h* / *Posts 7 d* add city-channel posts (Home publications) and country-channel posts (listings carried by the channel). The run page shows the same numbers.
- **Home posts**: every message with its verdict, reject reason and model. Filter by status to audit the AI.
- **Home listings**: the structured rows. Filter `needs_review` for the queue.
- **Home media**: each photo with its status and error.

## Run now and numbers (Overview in the sidebar)

- **Run now** (`/admin/ops/run/`): one button per cron job: courier market extract, convert and publish; outreach build; the housing job and each of its steps (read, review, photos, read photos, post, alerts). Each runs the same function as its cron inside the admin request (housing steps get a 240 s budget) and shows the JSON result. POST with CSRF, staff only. Switches still apply. Code: `backend/api/admin_ops.py`.
- **Numbers by city** (`/admin/ops/numbers/`): per city, per UTC day, last 14 days: housing listings accepted, housing posts to channels, alerts saved, courier requests (by origin city) and courier matches (by the demand's origin city). Plain tables, busiest cities first, the rest folded into one row after 30.

## Not built yet

Saved (bookmarked) listings in the Mini App, editing a post when the listing expires, freshness checks against the source (edited or deleted messages), matching wanted posts to offers, photo-only posts (an album with no text anywhere), and video or voice posts.
