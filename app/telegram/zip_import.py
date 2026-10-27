"""Safe ingestion and extraction of animation assets from uploaded ZIP files."""
from __future__ import annotations

import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Any


MAX_ZIP_ITEMS = 200
MAX_ZIP_EXPANDED_BYTES = 45 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 20 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
ALLOWED_EXTENSIONS = {".tgs", ".webm", ".webp"}
VALID_PACK_NAME = re.compile(r"[A-Za-z0-9_]{1,64}")


class PackImportError(ValueError):
    """The supplied archive is invalid or exceeds safe import limits."""


def _safe_member_name(name: str) -> str:
    if not name or "\x00" in name or "\\" in name:
        raise PackImportError("The ZIP contains an invalid path.")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PackImportError("The ZIP contains an unsafe path.")
    if path.parts and ":" in path.parts[0]:
        raise PackImportError("The ZIP contains a drive-like path.")
    return str(path)


def parse_pack_archive(data: bytes, fallback_title: str = "Imported animation pack") -> dict[str, Any]:
    """Inspect a ZIP's metadata without extracting all asset bytes into memory."""
    if not data:
        raise PackImportError("The uploaded ZIP is empty.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise PackImportError("This file is not a valid ZIP archive.") from exc

    try:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ITEMS + 32:
            raise PackImportError(f"The archive has too many entries; a maximum of {MAX_ZIP_ITEMS} animation files is supported.")
        seen_names: set[str] = set()
        candidates: list[tuple[zipfile.ZipInfo, str]] = []
        total_expanded = 0
        manifest_info: zipfile.ZipInfo | None = None

        for info in infos:
            if info.is_dir():
                continue
            safe_name = _safe_member_name(info.filename)
            if safe_name in seen_names:
                raise PackImportError("The ZIP contains duplicate file paths, so the source files are ambiguous.")
            seen_names.add(safe_name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise PackImportError("The ZIP contains a symbolic link; this is not allowed.")
            if info.flag_bits & 0x1:
                raise PackImportError("Encrypted ZIP entries are not supported.")
            if safe_name == "manifest.json":
                manifest_info = info
                if info.file_size > MAX_MANIFEST_BYTES:
                    raise PackImportError("The ZIP manifest is too large.")
                continue

            suffix = PurePosixPath(safe_name).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                continue
            if info.file_size < 1:
                raise PackImportError(f"Animation entry {safe_name!r} is empty.")
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                raise PackImportError(f"Animation entry {safe_name!r} exceeds the 20 MiB per-file limit.")
            total_expanded += info.file_size
            if total_expanded > MAX_ZIP_EXPANDED_BYTES:
                raise PackImportError("The uncompressed animation files exceed the 45 MiB archive limit.")
            candidates.append((info, safe_name))

        if not candidates:
            raise PackImportError("No .TGS, .WEBM, or .WEBP animation files were found in the ZIP.")
        if len(candidates) > MAX_ZIP_ITEMS:
            raise PackImportError(f"The archive contains more than {MAX_ZIP_ITEMS} animation files.")

        manifest: dict[str, Any] = {}
        if manifest_info is not None:
            try:
                parsed = json.loads(archive.read(manifest_info).decode("utf-8"))
                if isinstance(parsed, dict):
                    manifest = parsed
            except (UnicodeDecodeError, json.JSONDecodeError, OSError, zipfile.BadZipFile):
                # A broken manifest must not stop valid files from being imported.
                manifest = {}

        metadata: dict[str, dict[str, Any]] = {}
        manifest_files = manifest.get("files", [])
        if isinstance(manifest_files, list):
            for entry in manifest_files[:MAX_ZIP_ITEMS]:
                if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
                    continue
                try:
                    name = _safe_member_name(entry["filename"])
                except PackImportError:
                    continue
                metadata[name] = entry

        items: list[dict[str, Any]] = []
        for info, safe_name in sorted(candidates, key=lambda pair: pair[1].casefold()):
            entry = metadata.get(safe_name, {})
            suffix = PurePosixPath(safe_name).suffix.lower()
            emoji = entry.get("emoji", "")
            if not isinstance(emoji, str):
                emoji = ""
            if len(emoji) > 32:
                emoji = emoji[:32]
            items.append(
                {
                    "source_member": safe_name,
                    "file_name": PurePosixPath(safe_name).name,
                    "emoji": emoji,
                    "is_animated": suffix == ".tgs",
                    "is_video": suffix == ".webm",
                    "width": int(entry.get("width", 0) or 0) if isinstance(entry.get("width", 0), (int, float)) else 0,
                    "height": int(entry.get("height", 0) or 0) if isinstance(entry.get("height", 0), (int, float)) else 0,
                    "source_bytes": info.file_size,
                }
            )

        title = manifest.get("pack_title")
        title = title if isinstance(title, str) and title.strip() else fallback_title
        title = title.strip()[:128]
        short_name = manifest.get("pack_short_name", "")
        if not isinstance(short_name, str) or not VALID_PACK_NAME.fullmatch(short_name):
            short_name = ""
        kind = manifest.get("pack_kind", "stickers")
        kind = "custom_emoji" if kind == "custom_emoji" else "stickers"
        return {
            "title": title,
            "short_name": short_name,
            "kind": kind,
            "items": items,
            "expanded_bytes": total_expanded,
        }
    finally:
        archive.close()


def extract_zip_member(data: bytes, member_name: str, max_member_bytes: int = MAX_ZIP_MEMBER_BYTES) -> bytes:
    """Extract exactly one prevalidated member while rechecking its actual path and size."""
    safe_name = _safe_member_name(member_name)
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            matches = [info for info in archive.infolist() if not info.is_dir() and info.filename == safe_name]
            if len(matches) != 1:
                raise PackImportError("The original ZIP member is missing or ambiguous.")
            info = matches[0]
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode) or info.flag_bits & 0x1:
                raise PackImportError("The original ZIP member is not safe to extract.")
            if info.file_size < 1 or info.file_size > max_member_bytes:
                raise PackImportError("The requested ZIP member is empty or exceeds the per-file limit.")
            output = archive.read(info)
            if len(output) != info.file_size or len(output) > max_member_bytes:
                raise PackImportError("The extracted file size does not match the archive metadata.")
            return output
    except PackImportError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
        raise PackImportError("The ZIP file or selected animation is damaged.") from exc
