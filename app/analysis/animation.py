"""TGS/WebM inspection and conservative, validated TGS edits."""
from __future__ import annotations

import gzip
import io
import json
import math
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image
import imageio_ffmpeg


MAX_TGS_JSON_BYTES = 4 * 1024 * 1024
MAX_TGS_OUTPUT_BYTES = 64 * 1024
SHAPE_TYPES = {
    "sh", "el", "rc", "tm", "sr", "fl", "st", "gr", "tr", "gf", "gs",
    "mm", "rd", "op", "zz", "pb", "rp",
}


class AnimationError(ValueError):
    """An asset is unsupported, malformed, or cannot safely be rebuilt."""


def detect_format(data: bytes, preferred: str | None = None) -> str:
    if preferred:
        value = preferred.lower().lstrip(".")
        aliases = {"tgsticker": "tgs", "application/x-tgsticker": "tgs", "video/webm": "webm"}
        value = aliases.get(value, value)
        if value in {"tgs", "webm", "webp"}:
            return value
    if data[:2] == b"\x1f\x8b":
        return "tgs"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "unknown"


def _load_tgs(data: bytes) -> dict[str, Any]:
    if not data or len(data) > 2 * 1024 * 1024:
        raise AnimationError("TGS input is empty or exceeds the safe compressed-file limit.")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as handle:
            raw = handle.read(MAX_TGS_JSON_BYTES + 1)
        if len(raw) > MAX_TGS_JSON_BYTES:
            raise AnimationError("The expanded TGS document exceeds the 4 MiB safety limit.")
        root = json.loads(raw.decode("utf-8"))
    except AnimationError:
        raise
    except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnimationError("The TGS file is not a valid gzip-compressed Lottie document.") from exc
    if not isinstance(root, dict) or not isinstance(root.get("layers", []), list):
        raise AnimationError("The TGS document has an invalid Lottie structure.")
    return root


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _text_documents(layer: dict[str, Any]) -> list[dict[str, Any]]:
    """Return editable Lottie text document states for a single text layer."""
    text_block = layer.get("t")
    if not isinstance(text_block, dict):
        return []
    data_block = text_block.get("d")
    if not isinstance(data_block, dict):
        return []
    keyframes = data_block.get("k")
    if not isinstance(keyframes, list):
        return []
    documents: list[dict[str, Any]] = []
    for keyframe in keyframes:
        if not isinstance(keyframe, dict):
            continue
        document = keyframe.get("s")
        if isinstance(document, dict) and isinstance(document.get("t"), str):
            documents.append(document)
    return documents


def _editable_text_layers(root: dict[str, Any]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    found: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for node in _walk(root):
        if node.get("ty") == 5:
            documents = _text_documents(node)
            if documents:
                found.append((node, documents))
    return found


def _rgb_hex(values: list[float]) -> str | None:
    if len(values) < 3 or not all(isinstance(x, (int, float)) and math.isfinite(float(x)) for x in values[:3]):
        return None
    if any(float(x) < 0 or float(x) > 1 for x in values[:3]):
        return None
    rgb = [round(float(x) * 255) for x in values[:3]]
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _colors_from_property(value: Any, output: list[str]) -> None:
    if len(output) >= 128:
        return
    if isinstance(value, dict):
        for key in ("k", "s", "e"):
            if key in value:
                _colors_from_property(value[key], output)
                if len(output) >= 128:
                    return
    elif isinstance(value, list):
        if len(value) == 4 and all(isinstance(x, (int, float)) for x in value):
            color = _rgb_hex(value)
            if color:
                output.append(color)
                return
        for item in value:
            _colors_from_property(item, output)
            if len(output) >= 128:
                return


def _analyze_tgs(data: bytes) -> dict[str, Any]:
    root = _load_tgs(data)
    text_layers = [node for node in _walk(root) if node.get("ty") == 5]
    editable_text_layers = _editable_text_layers(root)
    text_contents = list(dict.fromkeys(
        document["t"][:40]
        for _, documents in editable_text_layers
        for document in documents
        if document.get("t")
    ))[:5]
    fr = float(root.get("fr", 0) or 0)
    ip = float(root.get("ip", 0) or 0)
    op = float(root.get("op", 0) or 0)
    colors: list[str] = []
    shape_counts: Counter[str] = Counter()
    layer_types: Counter[str] = Counter()
    keyframes = 0
    effect_count = 0
    for node in _walk(root):
        shape_type = node.get("ty")
        if isinstance(shape_type, str) and shape_type in SHAPE_TYPES:
            shape_counts[shape_type] += 1
        if isinstance(shape_type, int) and "ind" in node and "ks" in node:
            layer_types[str(shape_type)] += 1
            effect_count += len(node.get("ef", []) or [])
        if isinstance(node.get("t"), (int, float)) and ("s" in node or "e" in node) and ("i" in node or "o" in node):
            keyframes += 1
        if isinstance(shape_type, str) and shape_type in {"fl", "st"} and isinstance(node.get("c"), dict):
            _colors_from_property(node["c"], colors)
        if isinstance(shape_type, str) and shape_type in {"gf", "gs"} and isinstance(node.get("g"), dict):
            _colors_from_property(node["g"], colors)
    duration = max(0.0, (op - ip) / fr) if fr > 0 else 0.0
    return {
        "format": "TGS",
        "width": int(root.get("w", 0) or 0),
        "height": int(root.get("h", 0) or 0),
        "frame_rate": round(fr, 3),
        "duration_seconds": round(duration, 3),
        "frame_count": max(0, int(round(op - ip))),
        "layer_count": len(root.get("layers", []) or []),
        "layer_types": dict(layer_types),
        "shape_types": dict(shape_counts),
        "keyframe_count": keyframes,
        "effect_count": effect_count,
        "colors": list(dict.fromkeys(colors))[:16],
        "text_layer_count": len(text_layers),
        "editable_text_layer_count": len(editable_text_layers),
        "text_contents": text_contents,
        "text_replace_supported": len(editable_text_layers) == 1,
        "editable": True,
        "exact_reuse": True,
        "source_bytes": len(data),
    }


def _palette_from_rgb_frame(frame: bytes, width: int, height: int) -> list[str]:
    if width < 1 or height < 1 or len(frame) != width * height * 3:
        return []
    image = Image.frombytes("RGB", (width, height), frame).resize((64, 64))
    quantized = image.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    colors = quantized.getcolors(maxcolors=64 * 64) or []
    palette = quantized.getpalette() or []
    result: list[str] = []
    for _, index in sorted(colors, reverse=True):
        offset = int(index) * 3
        if offset + 2 >= len(palette):
            continue
        color = "#{:02X}{:02X}{:02X}".format(*palette[offset:offset + 3])
        if color not in result:
            result.append(color)
        if len(result) == 8:
            break
    return result


def _analyze_webm(data: bytes) -> dict[str, Any]:
    if len(data) > 20 * 1024 * 1024:
        raise AnimationError("The WEBM file exceeds the 20 MiB processing limit.")
    try:
        with tempfile.TemporaryDirectory(prefix="emoji-rebuilder-") as temp_dir:
            path = Path(temp_dir) / "source.webm"
            path.write_bytes(data)
            reader = imageio_ffmpeg.read_frames(
                str(path),
                pix_fmt="rgb24",
                output_params=["-vf", "fps=1", "-frames:v", "3"],
            )
            meta = next(reader)
            size = meta.get("size") or (0, 0)
            width, height = int(size[0]), int(size[1])
            colors: list[str] = []
            sampled_frames = 0
            try:
                for frame in reader:
                    if sampled_frames >= 3:
                        break
                    colors.extend(_palette_from_rgb_frame(frame, width, height))
                    sampled_frames += 1
            finally:
                reader.close()
        return {
            "format": "WEBM",
            "width": width,
            "height": height,
            "frame_rate": round(float(meta.get("fps") or 0), 3),
            "duration_seconds": round(float(meta.get("duration") or 0), 3),
            "codec": str(meta.get("codec") or "unknown"),
            "sampled_frames": sampled_frames,
            "colors": list(dict.fromkeys(colors))[:16],
            "editable": False,
            "exact_reuse": True,
            "source_bytes": len(data),
            "note": "Colors are sampled from decoded frames. WEBM pixels do not expose the original vector layers or keyframes.",
        }
    except AnimationError:
        raise
    except Exception as exc:
        raise AnimationError("Could not decode the WEBM file. The original can still be reused; check that it is a valid Telegram video sticker.") from exc


def _analyze_webp(data: bytes) -> dict[str, Any]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            rgb = image.convert("RGB").resize((64, 64))
            quantized = rgb.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
            counts = quantized.getcolors(maxcolors=64 * 64) or []
            palette = quantized.getpalette() or []
            colors: list[str] = []
            for _, index in sorted(counts, reverse=True):
                offset = int(index) * 3
                if offset + 2 < len(palette):
                    color = "#{:02X}{:02X}{:02X}".format(*palette[offset:offset + 3])
                    if color not in colors:
                        colors.append(color)
                if len(colors) >= 8:
                    break
            return {
                "format": "WEBP",
                "width": image.width,
                "height": image.height,
                "colors": colors,
                "editable": False,
                "exact_reuse": True,
                "source_bytes": len(data),
                "note": "Static image: no motion or keyframe data is present.",
            }
    except Exception as exc:
        raise AnimationError("The WebP file could not be decoded.") from exc


def analyze_animation(data: bytes, preferred_format: str | None = None) -> dict[str, Any]:
    if not data:
        raise AnimationError("The animation file is empty.")
    format_name = detect_format(data, preferred_format)
    if format_name == "tgs":
        return _analyze_tgs(data)
    if format_name == "webm":
        return _analyze_webm(data)
    if format_name == "webp":
        return _analyze_webp(data)
    raise AnimationError("Unsupported file format. Send a Telegram custom animated emoji, a TGS/WEBM sticker, or a valid Telegram sticker-pack link.")


def _emit_tgs(root: dict[str, Any]) -> bytes:
    width, height = int(root.get("w", 0) or 0), int(root.get("h", 0) or 0)
    fr = float(root.get("fr", 0) or 0)
    duration = (float(root.get("op", 0) or 0) - float(root.get("ip", 0) or 0)) / fr if fr > 0 else 0
    if (width, height) != (512, 512):
        raise AnimationError("Telegram TGS export requires a 512 × 512 canvas.")
    if abs(fr - 60) > 0.01:
        raise AnimationError("Telegram TGS export requires 60 FPS; this source cannot safely be exported as-is.")
    if duration <= 0 or duration > 3.0:
        raise AnimationError("Telegram TGS export must loop within 3 seconds.")
    raw = json.dumps(root, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_TGS_JSON_BYTES:
        raise AnimationError("The rebuilt Lottie document exceeds the safe size limit.")
    packed = gzip.compress(raw, compresslevel=9, mtime=0)
    if len(packed) > MAX_TGS_OUTPUT_BYTES:
        raise AnimationError("The rebuilt animation exceeds Telegram's 64 KiB TGS size limit.")
    return packed


def _replace_color_value(value: Any, rgb: list[float]) -> None:
    if isinstance(value, list):
        if len(value) in {3, 4} and all(isinstance(x, (int, float)) for x in value):
            value[:3] = rgb
            return
        for item in value:
            _replace_color_value(item, rgb)
    elif isinstance(value, dict):
        for key in ("k", "s", "e"):
            if key in value:
                _replace_color_value(value[key], rgb)


def _replace_gradient_value(value: Any, rgb: list[float]) -> None:
    if isinstance(value, dict):
        for key in ("k", "s", "e"):
            if key in value:
                _replace_gradient_value(value[key], rgb)
    elif isinstance(value, list):
        if value and isinstance(value[0], (int, float)):
            stops = int(value[0])
            if 1 <= stops <= 64 and len(value) >= 1 + stops * 4:
                for stop in range(stops):
                    offset = 1 + stop * 4
                    value[offset + 1:offset + 4] = rgb
                return
        for item in value:
            _replace_gradient_value(item, rgb)


def recolor_tgs(data: bytes, hex_color: str) -> bytes:
    match = re.fullmatch(r"#?([0-9A-Fa-f]{6})", (hex_color or "").strip())
    if not match:
        raise AnimationError("Enter a six-digit hex color, for example #FF8800.")
    hex_value = match.group(1)
    rgb = [int(hex_value[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    root = _load_tgs(data)
    changed = 0
    for node in _walk(root):
        shape_type = node.get("ty")
        if isinstance(shape_type, str) and shape_type in {"fl", "st"} and isinstance(node.get("c"), dict):
            _replace_color_value(node["c"], rgb)
            changed += 1
        elif isinstance(shape_type, str) and shape_type in {"gf", "gs"} and isinstance(node.get("g"), dict):
            _replace_gradient_value(node["g"], rgb)
            changed += 1
    if not changed:
        raise AnimationError("No editable fill, stroke, or gradient color properties were found in this TGS file.")
    return _emit_tgs(root)


def replace_text_tgs(data: bytes, replacement_text: str) -> bytes:
    """Replace text only when the TGS contains exactly one editable Lottie text layer.

    Shape outlines are not guessed or reverse-engineered: changing those reliably
    requires rebuilding the glyph paths, which is a different operation.
    """
    text = (replacement_text or "").strip()
    if not text:
        raise AnimationError("Replacement text cannot be empty.")
    if len(text) > 20:
        raise AnimationError("Replacement text must be 20 characters or fewer.")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise AnimationError("Replacement text cannot contain control characters.")

    root = _load_tgs(data)
    layers = _editable_text_layers(root)
    if not layers:
        raise AnimationError(
            "This animation has no editable Lottie text layer. Its lettering is likely converted to vector shapes; "
            "the bot cannot safely replace those letters while preserving the original animation exactly."
        )
    if len(layers) != 1:
        raise AnimationError(
            f"This animation contains {len(layers)} editable text layers. "
            "Automatic replacement is stopped so unrelated text is not accidentally changed."
        )

    _, documents = layers[0]
    changed = 0
    for document in documents:
        if isinstance(document.get("t"), str):
            document["t"] = text
            changed += 1
    if not changed:
        raise AnimationError("No editable text values were found in the selected layer.")
    return _emit_tgs(root)


def change_tgs_speed(data: bytes, multiplier: float) -> bytes:
    if not math.isfinite(multiplier) or not 0.5 <= multiplier <= 2.0:
        raise AnimationError("Speed must be between 0.5 and 2.0 (for example 1.5).")
    root = _load_tgs(data)
    fr = float(root.get("fr", 0) or 0)
    if fr <= 0:
        raise AnimationError("The TGS frame rate is missing or invalid.")
    base_frame = float(root.get("ip", 0) or 0)

    def scale_timing(node: Any) -> None:
        if isinstance(node, dict):
            time_value = node.get("t")
            if isinstance(time_value, (int, float)) and ("s" in node or "e" in node):
                node["t"] = round(base_frame + (float(time_value) - base_frame) / multiplier, 4)
            for key in ("ip", "op"):
                value = node.get(key)
                if isinstance(value, (int, float)):
                    node[key] = round(base_frame + (float(value) - base_frame) / multiplier, 4)
            for value in node.values():
                scale_timing(value)
        elif isinstance(node, list):
            for value in node:
                scale_timing(value)

    scale_timing(root)
    return _emit_tgs(root)
