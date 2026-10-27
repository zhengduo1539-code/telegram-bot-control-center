"""Strict Telegram sticker-pack link parsing; never download arbitrary user URLs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


ALLOWED_HOSTS = {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}
PACK_PREFIXES = {"addemoji": "custom_emoji", "addstickers": "stickers"}


@dataclass(frozen=True, slots=True)
class PackLink:
    short_name: str
    kind: str
    source_url: str


def parse_pack_link(value: str) -> PackLink | None:
    """Parse t.me/addemoji/<short_name> or t.me/addstickers/<short_name>."""
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith(("t.me/", "telegram.me/", "www.t.me/", "www.telegram.me/")):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in {"http", "https"} or host not in ALLOWED_HOSTS:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or parts[0].lower() not in PACK_PREFIXES:
        return None
    short_name = parts[1]
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", short_name):
        return None
    return PackLink(
        short_name=short_name,
        kind=PACK_PREFIXES[parts[0].lower()],
        source_url=f"https://t.me/{parts[0].lower()}/{short_name}",
    )


def normalize_custom_emoji_id(value: str) -> str | None:
    """Accept numeric Telegram custom emoji identifiers, not file IDs."""
    candidate = (value or "").strip()
    if re.fullmatch(r"[0-9]{10,32}", candidate):
        return candidate
    return None
