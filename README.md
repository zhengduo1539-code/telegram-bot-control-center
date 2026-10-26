# Telegram Animated Text Creator

A Telegram bot that turns a short text line into a coordinated pack of three animated custom emoji. The first version is free-only: each user can create up to three packs per UTC day. There is no bot PRO tier, payment flow, subscription billing, or referral system.

## Features

- Commands: /start, /help, /cancel, and administrator-only /admin.
- Inline controls for font, color, shape, and looped animation.
- PNG preview updates as a user changes settings.
- VP9 WebM output with alpha metadata, 100 × 100 pixels, 30 FPS, two-second loop, and a 256 KB size guard.
- Three coordinated color variants per successful pack.
- Compact MongoDB quota records with a unique user/day index, atomic increments, and a 60-day TTL cleanup.
- Safe, idempotent migration from the previous long-field schema to the compact schema at startup.
- Application-level user ID checks and bounded quota values; strict MongoDB schema validation is installed when permissions allow.
- /health is the readiness endpoint for UptimeRobot; /healthz remains available for Render's existing health check.
- HTTP client INFO logs are disabled for `httpx` and `httpcore` because Telegram Bot API request URLs contain the bot token.
- One Render Free Web Service runs the FastAPI health server and Telegram long polling in the same process.

## MongoDB storage format

The only application data stored in MongoDB is the daily quota counter, in the daily_usage collection:

| Field | Meaning | Type |
| --- | --- | --- |
| u | Telegram user ID | Integer |
| d | UTC day (YYYY-MM-DD) | String |
| n | Packs reserved/used on that day | Integer, 0–3 |
| x | Expiration timestamp | BSON Date |

The old fields (user_id, day, count, expires_at) are renamed in-place to (u, d, n, x). Unused created_at and updated_at fields are removed because no feature reads them. The migration keeps the user ID, usage count, day and expiration date; it does not reset a user's quota. Existing legacy indexes are replaced with a unique (u, d) index and TTL index on x. Documents with zero quota are removed because they represent no consumed quota.

If MongoDB rejects schema-validator configuration due to the database user's permissions, the bot logs a warning and continues with application-level validation and the unique/TTL indexes. The bot does not require elevated dbAdmin privileges to run.

## Required environment variables

Set these in Render Dashboard → Service → Environment. Do not add real tokens or database credentials to GitHub.

- ADMIN_IDS: one or more numeric Telegram user IDs separated by commas, spaces, or semicolons.
- BOT_TOKEN: token from BotFather.
- MONGODB_URI: MongoDB connection URI with MongoDB Atlas network access configured. The URI is passed to PyMongo without logging or rewriting its credentials.
- MONGODB_DATABASE (optional): database name for this bot's quota collection. If set, it overrides the database path in the URI. If unset and the URI has no database path, the app uses `animated_text_creator` by default.
- MONGODB_AUTH_SOURCE (optional): authentication database override for setups whose working MongoDB URI uses a non-default authentication database. Leave unset unless you know the other bot uses a custom `authSource`.

If required values are missing, the HTTP service still starts so Render's /healthz probe can pass, but Telegram polling remains disabled and /health returns HTTP 503 until configuration is complete. The /health endpoint also performs a MongoDB ping to check readiness. A `bad auth` error means MongoDB rejected authentication; changing the selected database alone cannot fix invalid credentials. Confirm the Render secret matches the working URI and configure `MONGODB_AUTH_SOURCE` only if that URI uses a custom authentication database.

## Render configuration

- Runtime: Python
- Build command: pip install -r requirements.txt
- Start command: uvicorn main:app --host 0.0.0.0 --port $PORT
- Render health check: /healthz
- UptimeRobot URL: https://YOUR-SERVICE.onrender.com/health

The app logs its public /health URL when RENDER_EXTERNAL_URL is available. UptimeRobot monitoring does not guarantee uninterrupted availability on Render's free plan.

## Telegram platform note

Custom emoji sets are a Telegram feature. Telegram Premium may be required to add and use custom emoji packs; this is not a paid plan in this bot. Video emoji files are rendered in WebM/VP9 format and checked against Telegram's 256 KB size limit.

## Local setup

Create a virtual environment, install requirements-dev.txt, and set the three environment variables in your shell. For production the app reads environment variables directly and intentionally does not load .env automatically.

Run locally with: uvicorn main:app --host 0.0.0.0 --port 8000
Run tests with: pytest -q

## Quota behaviour

Quota is reserved atomically before rendering, so concurrent button taps cannot exceed three generations. If rendering or Telegram upload fails before the pack is created, the reservation is released against the same UTC day it was reserved—even if the process crosses midnight. The quota resets at 00:00 UTC. Daily records expire after 60 days.
