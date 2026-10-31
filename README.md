# Schedule Bot

A Telegram scheduling bot for group and channel publishers. It supports one-time posts, recurring schedules, timezone-aware daily queues, reusable templates and message lifecycle actions.

## Main features

- **Schedule New Post:** text, photos, videos, documents, audio, animations and voice messages.
- **Recurring schedules:** repeat every N minutes, hours or days, or on selected weekdays.
- **Daily Queue:** configure posting times such as 09:00, 14:00 and 20:00, then add content. The bot publishes one pending item per configured slot.
- **Timezone support:** choose an IANA timezone; execution timestamps are persisted in UTC.
- **Inline URL buttons:** add optional links below posts.
- **Templates:** save and reuse common content.
- **Message lifecycle:** auto-pin/unpin, auto-delete and self-destruct.
- **Owner Panel:** service statistics, user/group lists, broadcast and data cleanup.

## How users get started

1. Add the bot to a group or channel.
2. Give it permission to send messages. For channels, make it an administrator with **Post Messages** permission.
3. Open a private chat with the bot and send /start.
4. Choose **Schedule New Post**, **Manage Queue**, or **Media Pool**.
5. Follow the guided steps, review the summary and confirm.

Only a group administrator or channel owner can create schedules for a target chat. The bot re-checks target permissions before saving.

## Project structure

- main.py: Telegram application startup, ASGI lifecycle and health-server integration.
- config.py: validated environment configuration.
- database.py: MongoDB persistence, indexes, atomic queue claims and retention.
- scheduler.py: recurring post execution, daily queue delivery and message lifecycle.
- schedule_utils.py: pure timezone-aware daily-slot calculations.
- handlers/: guided user flows, owner tools, templates and permission checks.
- keep_alive.py: Render liveness/readiness endpoints.
- tests/: automated unit tests.

## Environment variables

Configure these in Render → Service → Environment:

| Variable | Required | Description |
|---|---|---|
| BOT_TOKEN | Yes | Telegram token from @BotFather |
| MONGODB_URI | Yes | MongoDB connection string |
| MONGODB_DATABASE | No | Defaults to schedule_bot |
| ADMIN_IDS | Recommended | Comma-separated numeric owner Telegram IDs |
| SCHEDULER_INTERVAL_SECS | No | 15–300 seconds; default 60 |
| PORT | Render supplied | Local default 8080 |

Never commit credentials. Use .env.example only as a template.

## Render deployment

The included render.yaml uses build command pip install -r requirements.txt, start command uvicorn main:app --host 0.0.0.0 --port $PORT, liveness /healthz and readiness /health.

Run one service instance only. This bot uses an in-process polling scheduler; multiple service instances would run multiple scheduler loops. MongoDB atomic queue claims provide a second layer of protection against duplicate queue claims.

## Local development

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements-dev.txt
    cp .env.example .env
    # Fill BOT_TOKEN, MONGODB_URI and ADMIN_IDS in .env
    python -m compileall -q main.py handlers
    python -m pytest -q
    python main.py

## Retention and operational behavior

- Completed and permanently failed scheduled posts expire after 30 days.
- Paused schedules are retained until the user resumes or deletes them.
- Completed lifecycle tasks expire automatically after their retention period.
- When the bot is removed from a group, the group's scheduled posts, queues, media pools, activity and lifecycle tasks are cleaned up.
- Daily Queue publishes one pending item per slot. Missed slots are not burst-sent; the next item waits for the next available slot.
- Delivery can be delayed by up to one scheduler polling interval plus Telegram/network latency.
- Render free instances may sleep; an HTTP health ping is not a guarantee that polling will never be interrupted.
