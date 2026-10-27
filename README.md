# Telegram Animated Emoji & Sticker Generator

A Python Telegram bot that converts short text into vector-animated TGS assets and publishes either a Custom Emoji Pack or a Regular Animated Sticker Pack through the Telegram Bot API.

## Highlights

- Custom vector animation engine built on fontTools, Pillow and a constrained Lottie/TGS exporter.
- Inline editor for font, color palette, shape, motion and output pack type.
- GIF previews and TGS assets are handled in memory; generated binary files are not stored on disk.
- Telegram publishing is isolated behind app/telegram/sticker_api.py.
- SQLite is optional and disabled unless DATABASE_PATH is set. If enabled, it stores pack metadata only—not generated files or chat history.
- CPU work is bounded by MAX_CONCURRENT_RENDERS.
- No AI API, paid rendering API, PRO tier, payments, subscriptions, referral credits or daily quota.
- FastAPI health endpoints for Render at /health and /healthz; both accept GET and HEAD.

## Architecture

app/
- main.py: FastAPI lifecycle and health endpoints
- config.py: environment-backed settings
- bot/service.py: Telegram handlers and editor sessions
- telegram/sticker_api.py: Telegram Bot API publishing adapter
- storage/pack_registry.py: optional metadata-only SQLite registry

The animation engine and pack-name helper remain in the dedicated root modules emoji_engine.py and naming.py. main.py remains the Render-compatible entry point.

The engine does not call external AI or rendering services. The Telegram Bot API is used to publish packs and return their deep links.

## Setup

1. Create a bot with BotFather.
2. Set BOT_TOKEN in the Render environment.
3. Build command: pip install -r requirements.txt
4. Start command: uvicorn main:app --host 0.0.0.0 --port $PORT
5. Health check path: /healthz

Optional environment values are shown in .env.example. Leave DATABASE_PATH empty to disable SQLite. If pack history is needed, configure a path on persistent storage; the database is not required for generation.

## Telegram animation constraints

Generated TGS assets target a 512 × 512 canvas, 60 FPS, a looping animation of at most 3 seconds, and a compressed size below 64 KiB. The validator checks the gzip/Lottie envelope and key constraints before the file is uploaded.

The same TGS rendering pipeline can publish a regular animated sticker pack (addstickers) or a custom emoji pack (addemoji). Telegram account-level access rules for custom emoji remain controlled by Telegram. A successful local test cannot guarantee Telegram will accept every design, so a real upload with a configured test bot is part of release verification.

## Tests

Install development requirements and run:

    python -m compileall -q main.py app emoji_engine.py naming.py
    python -m pytest -q

Tests cover rendering constraints, pack naming, adapter behavior and optional SQLite behavior. Tests do not require a real Bot API token.
