# EmojiTitleBot | Text from Emojis

EmojiTitleBot turns a short text line into an animated Telegram **custom emoji pack**. Each pack contains three 100×100 VP9 WebM custom emoji variants, and every variant preserves the complete text rather than splitting it into character fragments. The editor keeps the selected appearance for the next text, previews the design as a looping GIF, and creates a text-named `addemoji` pack. The existing free daily allowance, referral bonuses, and Telegram Stars PRO subscription remain in place.

## Features

- Welcome and user-facing reply copy aligned with the reference video.
- Animated GIF preview that updates when users change font, color, shape, or animation.
- Shape and animation selectors expose only the three visual states seen in the supplied recording; option IDs are local renderer identifiers, not claims about the source bot's undisclosed internal names. Additional states are not inferred from incomplete footage.
- Fixed-size custom emoji packs containing three full-text design variants (not character-split fragments).
- Custom emoji rendered as transparent VP9 WebM at exactly 100 × 100, 30 FPS, and two seconds per loop, with a 256 KB size guard per item.
- High-quality gradient rendering, including a moving Rainbow palette; after creation the bot previews the result using a real custom-emoji message entity rather than sending a regular sticker.
- Three free packs per user per UTC day, plus one-time referral bonus credits.
- PRO recurring subscription billed with 100 Telegram Stars per 30-day period; PRO permits unlimited pack creation and uses the supplied text as the pack title.
- Payment verification through Telegram pre-checkout and successful-payment updates, with unique charge IDs persisted to prevent duplicate entitlement grants.
- MongoDB daily quota records with atomic increments and TTL cleanup; compact user entitlement and payment records.
- Safe migration from the previous quota schema, strict quota-document validation where permissions allow, and bounded user-provided identifiers.
- /health readiness endpoint (including a MongoDB ping) and /healthz for Render health checks.
- HTTP client INFO logs are disabled for httpx and httpcore because Telegram Bot API request URLs contain the bot token.
- A single Render Web Service runs FastAPI health endpoints and Telegram long polling in the same process.

## MongoDB collections

### daily_usage

| Field | Meaning |
| --- | --- |
| u | Telegram user ID |
| d | UTC day (YYYY-MM-DD) |
| n | Free packs used that day (0–3) |
| x | Expiration timestamp, cleaned by a TTL index |

The startup migration renames the old quota fields (user_id, day, count, expires_at) to the compact schema without resetting quota. A unique (u, d) index protects each daily record.

### `extra_usage`

Stores PRO and referral-bonus pack usage beyond the three free daily slots. It has a unique `(u, d)` index and a TTL index. Using a separate collection keeps the historic `daily_usage` validator and existing quota data compatible with database accounts that do not have `collMod` permission. Usage shown in bot replies and admin statistics combines both buckets.

### user_state

One small record per user stores the bonus-pack balance, PRO expiry, and the referrer ID when present. New-user registration is idempotent; repeated /start messages do not award repeated referral credits.

### star_payments

Stores the Telegram payment charge ID, user ID, amount, payload and subscription expiry. A unique charge-ID index prevents duplicate Stars updates from granting PRO multiple times. This also preserves charge references needed for payment support or refunds.

## Required environment variables

Set these in Render Dashboard → Service → Environment. Do not put live tokens or database credentials in GitHub or chat messages.

- BOT_TOKEN: token issued by BotFather.
- ADMIN_IDS: one or more numeric Telegram user IDs separated by commas, spaces or semicolons.
- MONGODB_URI: MongoDB connection URI with Atlas network access configured.
- MONGODB_DATABASE (optional): database name override; defaults to animated_text_creator if the URI has no database path.
- MONGODB_AUTH_SOURCE (optional): authentication database override, only if required by the working URI.

If required settings are missing, the HTTP service can remain available for Render's /healthz probe while Telegram polling stays disabled; /health returns HTTP 503 until the bot and database are ready. The URI is passed to PyMongo without logging its credentials.

## Render configuration

- Runtime: Python
- Build command: pip install -r requirements.txt
- Start command: uvicorn main:app --host 0.0.0.0 --port $PORT
- Render health check: /healthz
- UptimeRobot URL: https://YOUR-SERVICE.onrender.com/health

## Telegram payments and platform constraints

Digital goods must be sold using Telegram Stars (XTR). PRO uses Telegram's 30-day recurring Stars subscription period. The app only activates PRO after a successful-payment update, not just after pre-checkout approval. Custom emoji packs use Telegram's `custom_emoji` sticker-set type and the `t.me/addemoji/` deep link. Telegram Premium may be needed to add or use custom emoji sets; that is separate from this bot's PRO subscription.

## Local setup and tests

Create a virtual environment, install requirements-dev.txt, and set environment variables in your shell. Production intentionally reads environment variables directly and does not auto-load .env.

Run the service:

    uvicorn main:app --host 0.0.0.0 --port 8000

Run tests:

    pytest -q

The quota resets at 00:00 UTC. If rendering or Telegram upload fails before a pack is created, the quota reservation is released; if a referral bonus was used for that attempt, it is restored.
