"""MongoDB metadata repository using PyMongo's native async client.

Binary animation files are deliberately not persisted in MongoDB. Telegram file IDs
and compact analysis metadata are retained with TTL indexes to control storage growth.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient


ANALYSIS_TTL_SECONDS = 30 * 24 * 60 * 60
PACK_SESSION_TTL_SECONDS = 12 * 60 * 60
USER_TTL_SECONDS = 180 * 24 * 60 * 60


class MongoStore:
    def __init__(self, uri: str, database_name: str) -> None:
        if not uri:
            raise ValueError("MONGODB_URI is required.")
        self._client = AsyncMongoClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            tz_aware=True,
        )
        self.db = self._client[database_name]
        self.connected = False

    async def connect(self) -> None:
        await self._client.admin.command("ping")
        await self.db.users.create_index([("user_id", ASCENDING)], unique=True, name="user_id_unique")
        await self.db.users.create_index([("last_seen", ASCENDING)], expireAfterSeconds=USER_TTL_SECONDS, name="inactive_user_ttl")
        await self.db.assets.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_asset_history")
        await self.db.assets.create_index([("created_at", ASCENDING)], expireAfterSeconds=ANALYSIS_TTL_SECONDS, name="asset_ttl")
        await self.db.pack_sessions.create_index([("created_at", ASCENDING)], expireAfterSeconds=PACK_SESSION_TTL_SECONDS, name="pack_session_ttl")
        await self.db.pack_sessions.create_index([("user_id", ASCENDING)], name="pack_session_owner")
        self.connected = True

    async def close(self) -> None:
        await self._client.close()
        self.connected = False

    async def touch_user(self, user_id: int, username: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        await self.db.users.update_one(
            {"user_id": int(user_id)},
            {
                "$setOnInsert": {"created_at": now},
                "$set": {"last_seen": now, "username": (username or "")[:64]},
            },
            upsert=True,
        )

    async def save_pack_session(
        self,
        session_id: str,
        user_id: int,
        short_name: str,
        title: str,
        kind: str,
        items: list[dict[str, Any]],
    ) -> None:
        await self.db.pack_sessions.replace_one(
            {"_id": session_id},
            {
                "_id": session_id,
                "user_id": int(user_id),
                "short_name": short_name[:128],
                "title": title[:128],
                "kind": kind,
                "items": items,
                "created_at": datetime.now(timezone.utc),
            },
            upsert=True,
        )

    async def get_pack_session(self, session_id: str, user_id: int) -> dict[str, Any] | None:
        return await self.db.pack_sessions.find_one(
            {"_id": session_id, "user_id": int(user_id)},
            {"_id": False},
        )

    async def save_asset(
        self,
        asset_id: str,
        user_id: int,
        file_id: str,
        file_unique_id: str,
        source: str,
        format_name: str,
        report: dict[str, Any],
        emoji: str = "",
        source_member: str = "",
    ) -> None:
        await self.db.assets.insert_one(
            {
                "_id": asset_id,
                "user_id": int(user_id),
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "source": source[:32],
                "format": format_name[:16],
                "emoji": emoji[:32],
                "source_member": source_member[:512],
                "report": report,
                "created_at": datetime.now(timezone.utc),
            }
        )

    async def get_asset(self, asset_id: str, user_id: int) -> dict[str, Any] | None:
        return await self.db.assets.find_one(
            {"_id": asset_id, "user_id": int(user_id)},
            {"_id": False},
        )

    async def recent_assets(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 20))
        cursor = self.db.assets.find(
            {"user_id": int(user_id)},
            {"_id": True, "format": True, "emoji": True, "created_at": True, "source": True},
        ).sort("created_at", DESCENDING).limit(safe_limit)
        return await cursor.to_list(length=safe_limit)
