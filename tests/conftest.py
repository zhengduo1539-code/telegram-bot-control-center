"""Test-only environment defaults; never used by the deployed service."""
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_ONLY_TOKEN")
os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("MONGODB_DATABASE", "schedule_bot_test")
os.environ.setdefault("ADMIN_IDS", "123456789")
os.environ.setdefault("SCHEDULER_INTERVAL_SECS", "60")
