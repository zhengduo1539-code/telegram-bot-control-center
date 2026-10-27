# Telegram Animated Custom Emoji Generator

A free, quota-free, Telegram bot that turns short text into animated **custom emoji packs**. It uses a live GIF preview and an inline customization menu, then creates a Telegram custom-emoji set with animated `.tgs` vector assets.

## Features

- Interactive inline menu: Raleway Italic, Arial, Roboto, Madefor, Monaco, Impact, Rubik, Comic Sans; vibrant color palettes; three visual shape states; Heartbeat, Wave and Pop motion states.
- Re-rendered animated GIF preview when a setting changes.
- Three complete-text custom emoji variants per pack.
- Telegram Bot API call uses `sticker_type="custom_emoji"` and `InputSticker(format="animated")` for `.tgs` data.
- Direct pack URL uses `https://t.me/addemoji/<short_name>`.
- Progress updates during vector generation and publication.
- No PRO plan, payment handlers, Stars invoices, referral credits, database, or daily limit.
- FastAPI `/healthz` liveness endpoint for Render and `/health` readiness endpoint.

## Important platform note

The bot itself is free and has no paywall. Whether a user can add or use a custom emoji pack is still controlled by Telegram account features and Telegram Premium rules. The bot cannot remove Telegram's platform-level requirements.

## Setup

1. Create a bot with BotFather and copy its token.
2. Set `BOT_TOKEN` in Render's environment (or your local shell). No MongoDB settings are needed.
3. Deploy as a Render Web Service with Python.
4. Build command: `pip install -r requirements.txt`
5. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
6. Health check path: `/healthz`

Locally:

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
# Set BOT_TOKEN in your environment
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Use

Send `/start`, then send text of at most 20 characters. Use the inline arrows to adjust font, color, shape, and motion. Tap **Create Custom Emoji Pack**. The bot produces three full-text animated custom emojis and replies with an `addemoji` link and share button.

## Technical profile

- `.tgs`: gzip-compressed Lottie vector JSON, 512×512 canvas, 60 FPS, 180 frames (3 seconds max), loop-safe animation and a hard 64 KiB file size guard.
- Text is converted to vector glyph contours with fontTools so the `.tgs` is self-contained and does not rely on the Telegram client having the selected font installed.
- Monaco, Impact, Madefor, and Comic Sans use their exact typeface if installed; otherwise a compatible local fallback is used. Madefor is mapped to Montserrat on the default free deployment because its proprietary font is not bundled.
- GIF preview is rendered locally with Pillow. CPU-heavy rendering runs in worker threads, not in the Telegram event loop.
- Preferences and edit sessions are held in memory. Restarting the process clears active sessions and cached user preferences; the bot does not persist user data.

## Validation

Run tests:

```bash
pip install -r requirements-dev.txt
pytest -q
```

A successful local structural validation proves the TGS gzip/Lottie envelope, canvas, timing, and size constraints. Final acceptance by Telegram still requires testing with a real bot token and a Telegram client.