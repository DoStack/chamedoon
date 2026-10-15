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

## Courier channel is untouched

Housing never posts to the courier channel or group (`TELEGRAM_CHANNEL_*`). The `home` package has no import of the courier notifications code, the courier job never imports `home`, and tests fail if either changes. Housing will publish only to its own per-country channels, set in the admin.

## Running it

```bash
# production: the cron endpoint, Authorization: Bearer $CRON_SECRET
GET /api/cron/home/?step=ingest              # read sources (first run fills 30 days)
GET /api/cron/home/?step=review&limit=15     # AI-review the newest waiting posts
GET /api/cron/home/?step=media&limit=12      # fetch photos of accepted listings
GET /api/cron/home/?step=vision              # read photos for listings with gaps
GET /api/cron/home/                          # all four, within the function's time
```

A Vercel call is short, so call each step repeatedly until `remaining` is 0. Locally, with the env set:

```bash
python manage.py home_job --step ingest --days 30
python manage.py home_job --step review --limit 300 --seconds 1500
python manage.py home_job --step media --media-limit 200 --seconds 1500
```

Sources are seeded by migration: Germany and Canada, each with a few channels switched on and the rest found by search left off until someone reads a sample. Edit them in Admin → Home sources. `fetch_photos` can be turned off per source.

## Admin lists

- **Home posts**: every message with its verdict, reject reason and model. Filter by status to audit the AI.
- **Home listings**: the structured rows. Filter `needs_review` for the queue.
- **Home media**: each photo with its status and error.

## Not built yet

Channel publishing, the Mini App tab, freshness checks against the source (edited or deleted messages), matching wanted posts to offers, photo-only posts (an album with no text anywhere), and video or voice posts.
