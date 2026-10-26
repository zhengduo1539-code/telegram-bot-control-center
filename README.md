# Telegram Animated Text Creator

A Telegram bot that turns a short text line into a coordinated pack of three animated custom emoji. The first version is free-only: each user can create up to three packs per UTC day. There is no bot PRO tier, payment flow, subscription billing, or referral system.

## Features

- Commands: /start, /help, /cancel, and administrator-only /admin.
- Inline controls for font, color, shape, and looped animation.
- PNG preview updates as a user changes settings.
- VP9 WebM output with alpha metadata, 100 × 100 pixels, 30 FPS, two-second loop, and a 256 KB size guard.
- Three coordinated color variants per successful pack.
- MongoDB-backed atomic daily quota, with a 60-day TTL to limit usage-record growth.
- /health is the readiness endpoint for UptimeRobot; /healthz remains available for Render's existing health check.
- One Render Free Web Service runs the FastAPI health server and Telegram long polling in the same process.

## Required environment variables

Set these in Render Dashboard → Service → Environment. Do not add real tokens or database credentials to GitHub.

- ADMIN_IDS: one or more numeric Telegram user IDs separated by commas, spaces, or semicolons.
- BOT_TOKEN: token from BotFather.
- MONGODB_URI: MongoDB connection URI with MongoDB Atlas network access configured.

If values are missing, the HTTP service still starts so Render's /healthz probe can pass, but Telegram polling remains disabled and /health returns HTTP 503 until configuration is complete. The /health endpoint also performs a MongoDB ping to check readiness.

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

Quota is reserved atomically before rendering, so concurrent button taps cannot exceed three generations. If rendering or Telegram upload fails before the pack is created, the reservation is released. The quota resets at 00:00 UTC. Daily records expire after 60 days.
