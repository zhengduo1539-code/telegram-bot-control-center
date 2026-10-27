"""Safe, bounded ZIP exports for original Telegram sticker/emoji pack files."""
from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any


MAX_PACK_EXPORT_BYTES = 45 * 1024 * 1024
MAX_PACK_EXPORT_ITEMS = 200
VALID_PACK_NAME = re.compile(r"[A-Za-z0-9_]{1,64}")


class PackExportError(ValueError):
    """A pack cannot be exported as one safe Telegram document."""


def pack_item_filename(index: int, item: dict[str, Any]) -> str:
    """Return a portable, deterministic name; no user-controlled path components."""
    if index < 1 or index > MAX_PACK_EXPORT_ITEMS:
        raise PackExportError("The pack has an unsupported number of items.")
    if item.get("is_animated"):
        extension = "tgs"
    elif item.get("is_video"):
        extension = "webm"
    else:
        extension = "webp"

    emoji = str(item.get("emoji") or "")
    codepoints = "-".join(f"{ord(char):x}" for char in emoji if not char.isspace())
    codepoints = re.sub(r"[^0-9a-f-]", "", codepoints)[:64].strip("-")
    stem = codepoints or "sticker"
    return f"{index:03d}_{stem}.{extension}"


def pack_archive_filename(title: str) -> str:
    """Make a safe ZIP name from a pack title."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", (title or "").strip())
    base = base.strip("_-")[:48] or "telegram_emoji_pack"
    return f"{base}_all_files.zip"


class PackZipBuilder:
    """Stream source assets into one ZIP, keeping original file bytes unchanged."""

    def __init__(self, title: str, short_name: str, kind: str) -> None:
        self.title = (title or "Telegram pack")[:128]
        candidate = (short_name or "").strip()
        self.short_name = candidate if VALID_PACK_NAME.fullmatch(candidate) else ""
        self.kind = "custom_emoji" if kind == "custom_emoji" else "stickers"
        self._buffer = io.BytesIO()
        self._zip = zipfile.ZipFile(
            self._buffer,
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=False,
        )
        self._raw_bytes = 0
        self._entries: list[dict[str, Any]] = []
        self._closed = False

    @property
    def count(self) -> int:
        return len(self._entries)

    @property
    def source_bytes(self) -> int:
        return self._raw_bytes

    def add(self, index: int, item: dict[str, Any], data: bytes) -> str:
        if self._closed:
            raise PackExportError("The ZIP builder is already closed.")
        if len(self._entries) >= MAX_PACK_EXPORT_ITEMS:
            raise PackExportError(f"Exports are limited to {MAX_PACK_EXPORT_ITEMS} files per archive.")
        if not isinstance(data, bytes) or not data:
            raise PackExportError(f"Pack item {index} returned an empty file.")
        if self._raw_bytes + len(data) > MAX_PACK_EXPORT_BYTES:
            raise PackExportError(
                "The pack's combined source files exceed 45 MiB. "
                "Telegram cannot receive this as one ZIP document; export a smaller pack."
            )

        filename = pack_item_filename(index, item)
        self._zip.writestr(filename, data)
        self._raw_bytes += len(data)
        self._entries.append(
            {
                "index": index,
                "filename": filename,
                "emoji": str(item.get("emoji") or ""),
                "format": filename.rsplit(".", 1)[-1].upper(),
                "width": int(item.get("width", 0) or 0),
                "height": int(item.get("height", 0) or 0),
                "source_bytes": len(data),
            }
        )
        return filename

    def finish(self) -> bytes:
        if self._closed:
            raise PackExportError("The ZIP builder is already closed.")
        if not self._entries:
            self.abort()
            raise PackExportError("This pack has no exportable files.")
        source_url = ""
        if self.short_name:
            prefix = "addemoji" if self.kind == "custom_emoji" else "addstickers"
            source_url = f"https://t.me/{prefix}/{self.short_name}"
        manifest = {
            "pack_title": self.title,
            "pack_short_name": self.short_name,
            "pack_kind": self.kind,
            "source_url": source_url,
            "item_count": len(self._entries),
            "files": self._entries,
        }
        try:
            self._zip.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        finally:
            self._zip.close()
            self._closed = True
        result = self._buffer.getvalue()
        self._buffer.close()
        if len(result) > MAX_PACK_EXPORT_BYTES:
            raise PackExportError(
                "The ZIP archive exceeds the 45 MiB send limit. "
                "Please export a smaller pack."
            )
        return result

    def abort(self) -> None:
        if not self._closed:
            self._zip.close()
            self._closed = True
        if not self._buffer.closed:
            self._buffer.close()
