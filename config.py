"""Validated configuration for the Schedule Bot runtime."""
from __future__ import annotations
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _read_port() -> int:
    raw = os.getenv("PORT", "8080").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("PORT must be a valid integer.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535.")
    return port


def _read_admin_ids() -> list[int]:
    values: list[int] = []
    for item in os.getenv("ADMIN_IDS", "").split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdecimal() or int(item) <= 0:
            raise ValueError("ADMIN_IDS must be comma-separated positive numeric Telegram user IDs.")
        values.append(int(item))
    return sorted(set(values))


TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MONGO_URI = os.getenv("MONGODB_URI", "").strip() or os.getenv("MONGO_URI", "").strip()
MONGODB_DATABASE = (
    os.getenv("SCHEDULE_BOT_DATABASE", "").strip()
    or os.getenv("MONGODB_DATABASE", "schedule_bot").strip()
    or "schedule_bot"
)
PORT = _read_port()
ADMIN_IDS = _read_admin_ids()

try:
    SCHEDULER_INTERVAL_SECS = int(os.getenv("SCHEDULER_INTERVAL_SECS", "60"))
except ValueError as exc:
    raise ValueError("SCHEDULER_INTERVAL_SECS must be an integer.") from exc
if not 15 <= SCHEDULER_INTERVAL_SECS <= 300:
    raise ValueError("SCHEDULER_INTERVAL_SECS must be between 15 and 300 seconds.")

if not TELEGRAM_BOT_TOKEN:
    logger.critical("BOT_TOKEN is not configured.")
    sys.exit(1)
if not MONGO_URI:
    logger.critical("MONGODB_URI is not configured.")
    sys.exit(1)

logger.info("Schedule Bot configuration validated (admins configured: %d).", len(ADMIN_IDS))
