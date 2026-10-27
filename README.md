# Animated Emoji Analyzer & Rebuilder Bot

A dedicated Telegram bot for analyzing Telegram custom animated emojis and sticker packs. It accepts custom emoji IDs embedded in messages, animated stickers, TGS/WEBM uploads, and official Telegram links in these forms:
- https://t.me/addemoji/PACK_SHORT_NAME
- https://t.me/addstickers/PACK_SHORT_NAME

## Features

- **Pack-link browser:** resolve a Telegram sticker/emoji pack through the Bot API and browse its items with paginated inline buttons.
- **Custom emoji ID lookup:** read custom_emoji_id message entities and resolve them with getCustomEmojiStickers.
- **Source-preserving reuse:** send the original sticker back or export the original file without rendering it again.
- **TGS inspection:** analyze Lottie dimensions, FPS, duration, layer counts, shape types, detected fill/stroke/gradient colors, keyframes and effects where those values are present in the source.
- **WEBM inspection:** sample video frames for dominant colors and use decoder metadata for dimensions, FPS and duration. WEBM is a raster video; its original vector layers and keyframes cannot be recovered from pixels.
- **Constrained TGS editing:** recolor supported fill/stroke/gradient properties or change timeline speed, then validate 512×512 dimensions, 60 FPS, a maximum three-second duration, expanded JSON limits and Telegram's 64 KiB TGS compressed-size limit.
- **MongoDB metadata only:** store user activity, pack browsing sessions, file IDs and analysis reports. Raw binaries are processed in memory and are not saved to MongoDB.
- **Webhook-first on Render:** uses the Render public URL and a Telegram webhook secret, avoiding duplicate long-polling getUpdates processes during deploys. Local development uses polling if no public URL is configured.
- **No payment tiers or AI API required.**

## Architecture

- app/bot/service.py: Telegram commands, link and entity handling, paging, download/export and constrained edit workflows.
- app/telegram/links.py: strict parsing for official Telegram pack links only.
- app/analysis/animation.py: bounded TGS parsing, safe color/timing edits, WEBM frame sampling and metadata summaries.
- app/storage/mongo.py: asynchronous PyMongo repository, indexes and expiring metadata.
- app/config.py: environment configuration and limits.
- app/main.py: FastAPI lifecycle, health endpoints and webhook validation.
- main.py: Render-compatible ASGI entry point.

## Environment setup

Set these in the deployment environment (for Render: Dashboard → Service → Environment):

1. BOT_TOKEN: token from BotFather.
2. MONGODB_URI: MongoDB Atlas or another reachable MongoDB connection string.
3. MONGODB_DATABASE: optional database name; defaults to emoji_rebuilder.
4. WEBHOOK_SECRET: optional. If empty, a random secret is generated at startup.
5. PUBLIC_BASE_URL: optional; Render's RENDER_EXTERNAL_URL is used automatically.

Keep database credentials and bot tokens out of GitHub. Ensure the MongoDB deployment's network access rules allow connections from the Render service. Avoid broad public network access unless your environment requires it and is appropriately protected.

## Render deployment

- Build command: pip install -r requirements.txt
- Start command: uvicorn main:app --host 0.0.0.0 --port $PORT
- Health-check path: /healthz

/healthz is a liveness probe. /health is a readiness probe: it returns HTTP 503 until both Telegram and MongoDB are configured and connected. If MONGODB_URI is missing, the web process remains live for setup, but the Telegram bot remains disabled.

On Render, the app prefers a webhook. When running locally without PUBLIC_BASE_URL or RENDER_EXTERNAL_URL, it uses long polling.

## Usage

- /start — overview.
- /help — supported inputs and editing.
- /status — service readiness.
- /recent — recent analysis references.
- Send a custom animated emoji in a message, an animated sticker, or a .TGS/.WEBM/.WEBP file.
- Paste a Telegram addemoji or addstickers pack link.
- Choose a pack item, then use Send original, Export file, Recolor TGS, or Adjust speed.

Editing actions are available only for supported TGS Lottie files. Exported TGS files are not automatically inserted into a third-party pack; the user publishes them through Telegram's supported sticker/emoji workflow. WEBM files can be reused byte-for-byte, but raster frames do not provide editable source vectors.

## Data and retention

- User profile metadata is limited to Telegram user ID, username and timestamps.
- Pack browsing sessions expire after 12 hours.
- Analysis metadata/file references expire after 30 days.
- Binary input files are not stored in MongoDB.
- Files are limited to 20 MiB for processing; downloads are validated before analysis.
- Only official t.me / telegram.me pack links are accepted. The bot never fetches arbitrary URLs supplied by users.

## Development and verification

Install dependencies with:
    python -m pip install -r requirements-dev.txt

Compile and run tests with:
    python -m compileall -q main.py app
    python -m pytest -q

Unit tests use synthetic Lottie documents and do not require a Telegram token or live MongoDB credentials. A deployment with real credentials still needs an end-to-end chat test for pack lookup, file download and any target Telegram pack upload workflow.
