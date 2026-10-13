# Chamedoon Home (housing board), Phase 0

Reads housing posts from Persian Telegram channels abroad, asks the AI whether each is a real listing, extracts structured fields, drops reposts, and stores everything. Nothing is published to a channel or the Mini App yet.

```text
HomeSource (enabled channels, per-country defaults)
  → ingest   member Telegram account reads new messages (home/ingest.py)
  → HomePost raw text, author, link, one row per message
  → review   ai.llm.complete: is it a listing? extract fields (home/review.py)
  → HomeListing  structured row, or HomePost.status = rejected with a reason
  → duplicates linked to the first listing; low confidence → needs_review
```

## The AI step

`home/review.py` uses the same endpoint as the courier import: `ai.llm.complete` (OpenRouter free models first, then OpenAI). One call decides `accept` and fills the fields. Posts under 40 characters are rejected by rule without a call. Rejects keep a reason: `ad`, `noise`, `not_housing`, `incomplete`, `expired`, `other`.

The code checks every field the model returns. Enums, dates, money ranges, and the country/currency pair are validated. Anything doubtful sets `needs_review` and a note instead of being trusted. A bare amount takes the currency of the source (Germany: EUR), never a guess from a symbol.

Duplicates: same normalised wording, or same contact + city + type + price within 5%, marks the newer listing `duplicate` and extends the first listing's `last_seen`.

## Running it

Needs a Telegram session (`MARKET_TG_*` or `OUTREACH_TG_*`) and an LLM key (`OPENROUTER_API_KEY` / `OPENAI_API_KEY`), which Vercel already has.

```bash
# production: the cron endpoint, Authorization: Bearer $CRON_SECRET
GET /api/cron/home/?step=ingest              # read sources (first run fills 30 days)
GET /api/cron/home/?step=review&limit=15     # AI-review the newest waiting posts
GET /api/cron/home/                          # both
```

A Vercel call is short, so call `review` repeatedly until `remaining` is 0. Locally, with the env set:

```bash
python manage.py home_job --step ingest --days 30
python manage.py home_job --step review --limit 300 --seconds 1500
```

Sources are seeded by migration (Germany). Edit them in Admin → Home sources: the Hamburg and Cologne channels start disabled until a sample has been read.

## Admin

- **Home posts**: every message with its verdict, reject reason and model. Filter by status to audit the AI.
- **Home listings**: the structured rows. Filter `needs_review` for the queue.

## Not built yet

Photos and albums (Germany posts are text), channel publishing, the Mini App tab, freshness checks against the source, and matching wanted posts to offers.
