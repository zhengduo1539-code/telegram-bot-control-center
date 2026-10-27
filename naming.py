"""Telegram custom emoji pack naming helpers."""
from __future__ import annotations

import re
import secrets


def make_pack_name(text: str, bot_username: str, *, unique: str | None = None) -> str:
    """Return a Bot API-valid sticker-set short name ending in _by_<bot_username>."""
    username = re.sub(r"[^A-Za-z0-9_]", "", (bot_username or "").lstrip("@"))
    if not username:
        raise ValueError("A valid bot username is required to create a pack name.")
    if "__" in username:
        username = re.sub(r"_+", "_", username)
    suffix = f"_by_{username}"
    token = re.sub(r"[^A-Za-z0-9]", "", unique or secrets.token_hex(3))[:8] or secrets.token_hex(3)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text or "Text").strip("_")
    slug = re.sub(r"_+", "_", slug) or "Text"
    fixed = "Emoji_"
    max_slug_len = 64 - len(fixed) - 1 - len(token) - len(suffix)
    if max_slug_len < 1:
        # Telegram usernames are bounded, but preserve a useful error if the value is invalid.
        raise ValueError("Bot username is too long for a valid Telegram pack name.")
    slug = slug[:max_slug_len].strip("_") or "Text"
    name = f"{fixed}{slug}_{token}{suffix}"
    name = re.sub(r"_+", "_", name)
    if len(name) > 64 or not name[0].isalpha() or "__" in name:
        raise ValueError("Could not construct a valid Telegram custom emoji pack name.")
    return name