"""Environment-backed configuration for the lightweight bot."""
from __future__ import annotations

import os
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
    database_path: str | None
    max_concurrent_renders: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            bot_token=os.getenv("BOT_TOKEN", "").strip(),
            database_path=os.getenv("DATABASE_PATH", "").strip() or None,
            max_concurrent_renders=_bounded_int("MAX_CONCURRENT_RENDERS", 2, 1, 4),
        )
