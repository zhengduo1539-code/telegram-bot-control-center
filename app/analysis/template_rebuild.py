"""Create new text variants from a captured Telegram TGS animation template.

Editable Lottie text is replaced exactly. Shape-only templates use font outlines and
adapt the source's animated transform profile plus detected color. This second path is
a controlled reconstruction; it cannot promise pixel-identical lettering/effects.
"""
from __future__ import annotations

import copy
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

from app.analysis.animation import (
    AnimationError,
    _colors_from_property,
    _emit_tgs,
    _load_tgs,
    _walk,
    replace_text_tgs,
)

MAX_TEXT_LENGTH = 20
MAX_GLYPH_CONTOURS = 96
MAX_GLYPH_POINTS = 1800
_VARIATION_CHARS = {"\ufe0e", "\ufe0f"}


class GlyphOutlinePen(BasePen):
    """Capture font outlines as cubic Bezier contours."""
    def __init__(self, glyph_set):
        super().__init__(glyph_set)
        self.contours = []
        self._segments = []
        self._current = None
        self._start = None

    def _moveTo(self, point):
        if self._segments:
            self._flush()
        self._start = (float(point[0]), float(point[1]))
        self._current = self._start
        self._segments = []

    def _lineTo(self, point):
        end = (float(point[0]), float(point[1]))
        if self._current is None:
            self._moveTo(end)
            return
        self._segments.append((self._current, self._current, end, end))
        self._current = end

    def _curveToOne(self, control1, control2, end):
        endpoint = (float(end[0]), float(end[1]))
        if self._current is None:
            self._moveTo(endpoint)
            return
        self._segments.append((
            self._current,
            (float(control1[0]), float(control1[1])),
            (float(control2[0]), float(control2[1])),
            endpoint,
        ))
        self._current = endpoint

    def _qCurveToOne(self, control, end):
        endpoint = (float(end[0]), float(end[1]))
        if self._current is None:
            self._moveTo(endpoint)
            return
        q = (float(control[0]), float(control[1]))
        p0 = self._current
        c1 = (p0[0] + (q[0] - p0[0]) * 2 / 3, p0[1] + (q[1] - p0[1]) * 2 / 3)
        c2 = (endpoint[0] + (q[0] - endpoint[0]) * 2 / 3, endpoint[1] + (q[1] - endpoint[1]) * 2 / 3)
        self._segments.append((p0, c1, c2, endpoint))
        self._current = endpoint

    def _closePath(self):
        if self._current is not None and self._start is not None and self._current != self._start:
            self._segments.append((self._current, self._current, self._start, self._start))
        self._flush()

    def _endPath(self):
        self._flush()

    def _flush(self):
        if self._segments:
            self.contours.append(self._segments)
        self._segments = []
        self._current = None
        self._start = None


def _font_paths() -> list[str]:
    paths = []
    explicit = os.getenv("TEMPLATE_FONT_PATH", "").strip()
    if explicit and Path(explicit).is_file():
        paths.append(explicit)
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
        str(Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf"),
        str(Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"),
    ]
    paths.extend(path for path in candidates if Path(path).is_file())
    for root in (Path("/usr/share/fonts"), Path("/usr/local/share/fonts")):
        if root.is_dir():
            try:
                for path in root.rglob("*.ttf"):
                    if str(path) not in paths:
                        paths.append(str(path))
                    if len(paths) >= 48:
                        break
            except OSError:
                continue
        if len(paths) >= 48:
            break
    return list(dict.fromkeys(paths))


_FONT_CACHE: dict[str, tuple[Any, Any, dict[int, str], int]] = {}


def _load_font(path: str):
    if path in _FONT_CACHE:
        return _FONT_CACHE[path]
    try:
        font = TTFont(path, lazy=False)
        glyph_set = font.getGlyphSet()
        cmap = font.getBestCmap() or {}
        upm = int(font["head"].unitsPerEm)
        if not cmap or upm <= 0:
            return None
        value = (font, glyph_set, cmap, upm)
        _FONT_CACHE[path] = value
        return value
    except Exception:
        return None


def _normalize_input(text: str) -> str:
    value = (text or "").strip()
    value = "".join(ch for ch in value if ch != "\u200d" and ch not in _VARIATION_CHARS)
    if not value:
        raise AnimationError("Enter the text or Unicode symbol you want to create.")
    if len(value) > MAX_TEXT_LENGTH:
        raise AnimationError(f"Text is limited to {MAX_TEXT_LENGTH} Unicode characters so it fits the TGS canvas.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise AnimationError("Text cannot contain control characters or line breaks.")
    return value


def _resolve_glyph(char: str) -> dict[str, Any]:
    for path in _font_paths():
        loaded = _load_font(path)
        if not loaded:
            continue
        font, glyph_set, cmap, upm = loaded
        glyph_name = cmap.get(ord(char))
        if not glyph_name or glyph_name == ".notdef":
            continue
        glyph = glyph_set[glyph_name]
        pen = GlyphOutlinePen(glyph_set)
        try:
            glyph.draw(pen)
        except Exception:
            continue
        contours = [contour for contour in pen.contours if len(contour) >= 2]
        if char.isspace() and not contours:
            advance = font["hmtx"].metrics.get(glyph_name, (upm // 3, 0))[0]
            return {"char": char, "upm": upm, "advance": advance, "contours": []}
        if not contours:
            continue
        point_count = sum(len(contour) for contour in contours)
        if len(contours) > MAX_GLYPH_CONTOURS or point_count > MAX_GLYPH_POINTS:
            raise AnimationError(f"The glyph {char!r} is too detailed for a safe Telegram TGS export.")
        advance = font["hmtx"].metrics.get(glyph_name, (upm // 2, 0))[0]
        return {"char": char, "upm": upm, "advance": advance, "contours": contours}
    raise AnimationError(
        f"No installed outline font supports {char!r}. Configure TEMPLATE_FONT_PATH with a Unicode outline font and retry."
    )


def _path_json(contour, scale: float) -> dict[str, Any]:
    vertices, outgoing, incoming = [], [], []
    for start, c1, _c2, _end in contour:
        vertices.append([round(start[0] * scale, 3), round(-start[1] * scale, 3)])
        outgoing.append([round((c1[0] - start[0]) * scale, 3), round(-(c1[1] - start[1]) * scale, 3)])
    for index, segment in enumerate(contour):
        previous = contour[index - 1]
        vertex = segment[0]
        control = previous[2]
        incoming.append([round((control[0] - vertex[0]) * scale, 3), round(-(control[1] - vertex[1]) * scale, 3)])
    return {"i": incoming, "o": outgoing, "v": vertices, "c": True}


def _primary_color(root: dict[str, Any]) -> list[float]:
    fill_counts: Counter[str] = Counter()
    stroke_counts: Counter[str] = Counter()
    for node in _walk(root):
        kind = node.get("ty")
        if kind not in {"fl", "st"} or not isinstance(node.get("c"), dict):
            continue
        found: list[str] = []
        _colors_from_property(node["c"], found)
        target = fill_counts if kind == "fl" else stroke_counts
        target.update(color for color in found if color)
    # Prefer the fill palette over outline colors. Use stroke color only for
    # templates that have no fill definitions.
    counts = fill_counts or stroke_counts
    if not counts:
        return [1.0, 0.55, 0.16, 1.0]
    value = counts.most_common(1)[0][0].lstrip("#")
    return [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)] + [1.0]


def _iter_layers(root: dict[str, Any]):
    for layer in root.get("layers", []) or []:
        if isinstance(layer, dict):
            yield layer
    for asset in root.get("assets", []) or []:
        if isinstance(asset, dict):
            for layer in asset.get("layers", []) or []:
                if isinstance(layer, dict):
                    yield layer


def _keyframe_count(prop: Any) -> int:
    if isinstance(prop, dict) and prop.get("a") == 1 and isinstance(prop.get("k"), list):
        return len(prop["k"])
    return 0


def _motion_profiles(root: dict[str, Any]) -> list[dict[str, Any]]:
    # Telegram emoji often convert each animated glyph to a group with its own
    # "tr" transform. Prefer those profiles, in original order, so new text can
    # inherit per-character bounce/rotation timing instead of moving in lockstep.
    group_profiles = []
    for order, node in enumerate(_walk(root)):
        if node.get("ty") != "tr":
            continue
        profile = {key: node[key] for key in ("p", "s", "r", "o") if key in node}
        score = sum(_keyframe_count(profile.get(key)) for key in ("p", "s", "r", "o"))
        if score:
            group_profiles.append((order, score, copy.deepcopy(profile)))
    if group_profiles:
        group_profiles.sort(key=lambda entry: entry[0])
        return [entry[2] for entry in group_profiles[:24]]

    # If there are no individually animated groups, use the best animated
    # shape-layer transform as a shared source motion profile.
    layer_profiles = []
    for order, layer in enumerate(_iter_layers(root)):
        if layer.get("ty") != 4 or not isinstance(layer.get("ks"), dict):
            continue
        ks = layer["ks"]
        score = sum(_keyframe_count(ks.get(key)) for key in ("p", "s", "r", "o"))
        if score:
            layer_profiles.append((score, -order, {key: ks[key] for key in ("p", "s", "r", "o") if key in ks}))
    if not layer_profiles:
        return []
    layer_profiles.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [copy.deepcopy(layer_profiles[0][2])]


def _animated_keys(prop: Any):
    if isinstance(prop, dict) and prop.get("a") == 1 and isinstance(prop.get("k"), list):
        return prop["k"]
    return None


def _position_for_glyph(prop: Any, base: list[float]) -> dict[str, Any]:
    result = copy.deepcopy(prop) if isinstance(prop, dict) else {"a": 0, "k": base}
    keys = _animated_keys(result)
    if not keys:
        return {"a": 0, "k": base}
    positions = [
        frame[key]
        for frame in keys if isinstance(frame, dict)
        for key in ("s", "e")
        if isinstance(frame.get(key), list) and len(frame[key]) >= 2
        and all(isinstance(val, (int, float)) for val in frame[key][:2])
    ]
    if not positions:
        return {"a": 0, "k": base}
    origin = positions[0]
    max_delta = max(
        (max(abs(float(p[0]) - float(origin[0])), abs(float(p[1]) - float(origin[1]))) for p in positions),
        default=0.0,
    )
    factor = min(1.0, 28.0 / max_delta) if max_delta > 0 else 1.0
    for frame in keys:
        if not isinstance(frame, dict):
            continue
        for key in ("s", "e"):
            point = frame.get(key)
            if isinstance(point, list) and len(point) >= 2 and all(isinstance(val, (int, float)) for val in point[:2]):
                dx = (float(point[0]) - float(origin[0])) * factor
                dy = (float(point[1]) - float(origin[1])) * factor
                frame[key] = [round(base[0] + dx, 3), round(base[1] + dy, 3), 0]
        for key in ("to", "ti"):
            tangent = frame.get(key)
            if isinstance(tangent, list) and len(tangent) >= 2:
                frame[key] = [round(float(tangent[0]) * factor, 3), round(float(tangent[1]) * factor, 3), 0]
    return result


def _scale_for_glyph(prop: Any) -> dict[str, Any]:
    result = copy.deepcopy(prop) if isinstance(prop, dict) else {"a": 0, "k": [100, 100, 100]}
    keys = _animated_keys(result)
    if not keys:
        return {"a": 0, "k": [100, 100, 100]}
    frames = [frame for frame in keys if isinstance(frame, dict) and isinstance(frame.get("s"), list)]
    if not frames:
        return {"a": 0, "k": [100, 100, 100]}
    baseline = list(frames[0]["s"])
    def convert(values):
        if not isinstance(values, list):
            return values
        output = []
        for index, value in enumerate(values):
            if not isinstance(value, (int, float)):
                output.append(value)
                continue
            base = baseline[index] if index < len(baseline) else baseline[0]
            transformed = float(value) * 100.0 / float(base) if isinstance(base, (int, float)) and abs(float(base)) > 0.001 else float(value)
            output.append(round(max(0.0, min(200.0, transformed)), 3))
        return output
    for frame in keys:
        if isinstance(frame, dict):
            for key in ("s", "e"):
                if key in frame:
                    converted = convert(frame[key])
                    if isinstance(converted, list) and len(converted) == 2:
                        converted = [converted[0], converted[1], 100.0]
                    frame[key] = converted
    return result


def _default_transform(base_x: float, base_y: float, root: dict[str, Any]) -> dict[str, Any]:
    start = float(root.get("ip", 0) or 0)
    end = float(root.get("op", 180) or 180)
    a, b = min(start + 6, end), min(start + 14, end)
    ease_in = {"x": [0.25], "y": [1]}
    ease_out = {"x": [0.4], "y": [0]}
    return {
        "o": {"a": 1, "k": [
            {"t": start, "s": [0], "e": [100], "i": ease_in, "o": ease_out},
            {"t": a, "s": [100], "e": [100], "i": {"x": [0.6], "y": [1]}, "o": {"x": [0.8], "y": [0]}},
            {"t": b, "s": [100]},
        ]},
        "r": {"a": 1, "k": [
            {"t": start, "s": [-8], "e": [3], "i": ease_in, "o": ease_out},
            {"t": a, "s": [3], "e": [0], "i": {"x": [0.6], "y": [1]}, "o": {"x": [0.8], "y": [0]}},
            {"t": b, "s": [0]},
        ]},
        "p": {"a": 0, "k": [base_x, base_y, 0]},
        "a": {"a": 0, "k": [0, 0, 0]},
        "s": {"a": 1, "k": [
            {"t": start, "s": [0, 0, 100], "e": [112, 112, 100], "i": {"x": [0.25, 0.25, 0.25], "y": [1, 1, 1]}, "o": {"x": [0.4, 0.4, 0.4], "y": [0, 0, 0]}},
            {"t": a, "s": [112, 112, 100], "e": [100, 100, 100], "i": {"x": [0.6, 0.6, 0.6], "y": [1, 1, 1]}, "o": {"x": [0.8, 0.8, 0.8], "y": [0, 0, 0]}},
            {"t": b, "s": [100, 100, 100]},
        ]},
    }


def _layer_transform(profile: dict[str, Any] | None, base_x: float, base_y: float, root: dict[str, Any]):
    if profile is None:
        return _default_transform(base_x, base_y, root)
    return {
        "o": copy.deepcopy(profile.get("o", {"a": 0, "k": 100})),
        "r": copy.deepcopy(profile.get("r", {"a": 0, "k": 0})),
        "p": _position_for_glyph(profile.get("p"), [base_x, base_y, 0]),
        "a": {"a": 0, "k": [0, 0, 0]},
        "s": _scale_for_glyph(profile.get("s")),
    }


def _glyph_layer(index: int, char: str, contours, scale: float, color, x: float, y: float, profile, root):
    shapes = []
    for contour in contours:
        path = _path_json(contour, scale)
        if len(path["v"]) >= 2:
            shapes.append({"ty": "sh", "nm": "Glyph outline", "d": 1, "ks": {"a": 0, "k": path}})
    shapes.append({"ty": "fl", "nm": "Template color", "c": {"a": 0, "k": color}, "o": {"a": 0, "k": 100}, "r": 2, "bm": 0})
    return {
        "ddd": 0, "ind": index, "ty": 4, "nm": f"Text glyph {index}: {char}", "sr": 1,
        "ks": _layer_transform(profile, x, y, root), "ao": 0, "shapes": shapes,
        "ip": float(root.get("ip", 0) or 0), "op": float(root.get("op", 180) or 180), "st": 0, "bm": 0,
    }


def rebuild_tgs_text_from_template(data: bytes, text: str) -> tuple[bytes, str]:
    """Return a new TGS file and mode: exact text-layer replace or vector reconstruction."""
    value = _normalize_input(text)
    root = _load_tgs(data)
    text_layers = [node for node in _walk(root) if node.get("ty") == 5 and isinstance(node.get("t"), dict)]
    editable = [node for node in text_layers if isinstance(node.get("t", {}).get("d"), dict)]
    if len(editable) == 1:
        return replace_text_tgs(data, value), "exact_text_layer"
    if len(editable) > 1:
        raise AnimationError(
            f"The template contains {len(editable)} editable text layers. Choose a template with one text layer."
        )
    if int(root.get("w", 0) or 0) != 512 or int(root.get("h", 0) or 0) != 512:
        raise AnimationError("The source canvas is not 512 × 512; it cannot be safely rebuilt as Telegram TGS text.")

    glyphs = [_resolve_glyph(char) for char in value]
    advance_em = sum(glyph["advance"] / glyph["upm"] for glyph in glyphs)
    if advance_em <= 0:
        raise AnimationError("The supplied text has no visible characters.")
    em_pixels = min(68.0, 436.0 / advance_em)
    if em_pixels < 12:
        raise AnimationError("That text is too wide for the source animation. Use fewer characters.")
    total_width = advance_em * em_pixels
    cursor = (512.0 - total_width) / 2.0
    color = _primary_color(root)
    profiles = _motion_profiles(root)
    metrics = []
    min_y, max_y = 0.0, 0.0
    for glyph in glyphs:
        factor = em_pixels / glyph["upm"]
        ys = []
        for contour in glyph["contours"]:
            for p0, c1, c2, p3 in contour:
                ys.extend([-point[1] * factor for point in (p0, c1, c2, p3)])
        glyph_min, glyph_max = min(ys, default=-em_pixels * 0.7), max(ys, default=0.0)
        min_y, max_y = min(min_y, glyph_min), max(max_y, glyph_max)
        metrics.append((glyph, factor))
    baseline_y = 256.0 - (min_y + max_y) / 2.0

    layers = []
    for index, (glyph, factor) in enumerate(metrics, start=1):
        profile = profiles[(index - 1) % len(profiles)] if profiles else None
        layers.append(_glyph_layer(index, glyph["char"], glyph["contours"], factor, color, cursor, baseline_y, profile, root))
        cursor += glyph["advance"] * factor
    root["layers"] = layers
    root["assets"] = []
    root["nm"] = f"Template text: {value[:40]}"
    output = _emit_tgs(root)
    check = _load_tgs(output)
    if len(check.get("layers", [])) != len(value):
        raise AnimationError("The generated text animation did not pass structural validation.")
    return output, "motion_style_reconstruction"
