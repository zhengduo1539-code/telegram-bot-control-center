"""Optional, tiny SQLite registry. Disabled unless DATABASE_PATH is configured."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class PackRegistry:
    def __init__(self, database_path: str | None = None) -> None:
        self.database_path = database_path or None

    @property
    def enabled(self) -> bool:
        return bool(self.database_path)

    def record(self, *, user_id: int, pack_name: str, pack_type: str, display_text: str) -> None:
        """Store pack metadata only; generated binary assets are never stored in SQLite."""
        if not self.database_path:
            return
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(str(path), timeout=3.0) as connection:
            connection.execute("PRAGMA busy_timeout = 3000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pack_registry (
                    pack_name TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    pack_type TEXT NOT NULL CHECK (pack_type IN ('custom_emoji', 'regular')),
                    display_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO pack_registry
                    (pack_name, user_id, pack_type, display_text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (pack_name, int(user_id), pack_type, display_text[:128], created_at),
            )
            connection.commit()
