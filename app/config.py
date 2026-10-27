"""Validated environment configuration."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    mongodb_uri: str
    mongodb_database: str
    public_base_url: str
    webhook_secret: str
    max_download_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        public_url = (
            os.getenv("PUBLIC_BASE_URL", "").strip()
            or os.getenv("RENDER_EXTERNAL_URL", "").strip()
        ).rstrip("/")
        secret = os.getenv("WEBHOOK_SECRET", "").strip()
        if secret and (len(secret) > 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", secret)):
            raise ValueError("WEBHOOK_SECRET must contain only A-Z, a-z, 0-9, '_' or '-' and be at most 256 characters.")
        return cls(
            bot_token=os.getenv("BOT_TOKEN", "").strip(),
            mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
            mongodb_database=os.getenv("MONGODB_DATABASE", "emoji_rebuilder").strip() or "emoji_rebuilder",
            public_base_url=public_url,
            webhook_secret=secret,
            max_download_bytes=_bounded_int("MAX_DOWNLOAD_BYTES", 20 * 1024 * 1024, 1024 * 1024, 20 * 1024 * 1024),
        )
