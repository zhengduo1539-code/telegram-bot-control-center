# Telegram Animated Custom Emoji Generator

A free Telegram bot that turns short text into animated custom emoji packs using a live GIF preview and an inline customization editor. Pack generation uses vector outlines, fontTools, Pillow and Telegram-compatible animated TGS files.

## Features

- Inline customization for eight font labels, seven color palettes, three visual shape states, and Heartbeat, Wave and Pop motion.
- A live animated GIF preview that is refreshed after a setting changes.
- Three complete-text animated custom emoji variants per pack.
- Telegram Bot API calls use custom_emoji as the sticker-set type and animated as the InputSticker format.
- Pack links use the addemoji deep link.
- Progress status during vector generation and publication.
- No PRO subscription, payment handler, Telegram Stars invoice, referral credits, database dependency, or daily quota.
- FastAPI health endpoints for Render. Both GET and HEAD are supported at /health and /healthz.

## Setup

1. Create a Telegram bot with BotFather.
2. Set BOT_TOKEN in the Render service environment. No MongoDB variables are required.
3. Set the Render build command to: pip install -r requirements.txt
4. Set the start command to: uvicorn main:app --host 0.0.0.0 --port $PORT
5. Set the health check path to: /healthz

Locally, install requirements.txt and set BOT_TOKEN in your environment before starting Uvicorn.

## Custom emoji asset profile

- TGS vector canvas: 512 by 512.
- Frame rate: 60 FPS.
- Duration: 3 seconds, looping.
- Each generated TGS asset must be smaller than 64 KiB.
- Text is converted to vector glyph outlines so the selected font does not need to exist on the recipient's Telegram client.
- Font families that are proprietary or unavailable on the host use documented compatible system-font fallbacks.

## Notes

Bot-level generation is free. Telegram account-level rules for adding or using custom emojis are set by Telegram and are not bypassed by this bot. The source validates the TGS envelope, canvas, timing and size; final acceptance still needs a live Telegram upload using the configured bot token.

## Tests

Install requirements-dev.txt and run pytest -q. No real Telegram token is needed for the local structural tests.
