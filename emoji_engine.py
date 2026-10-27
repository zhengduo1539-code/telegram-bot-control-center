"""Render Telegram Animated Custom Emoji (.tgs) and GIF previews.

The TGS files are built from vector outlines (fontTools) and Lottie shape layers,
then gzip-compressed as required by Telegram's animated sticker format.
"""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import shutil
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen, DecomposingRecordingPen
from fontTools.pens.qu2cuPen import Qu2CuPen

CANVAS = 512
FPS = 60
DURATION_SECONDS = 3
END_FRAME = FPS * DURATION_SECONDS
MAX_TGS_BYTES = 64 * 1024
CUSTOM_EMOJI_COUNT = 3

# Stable IDs are implementation identifiers; the video only shows visual states.
FONT_OPTIONS = (
    "Raleway Italic", "Arial", "Roboto", "Madefor", "Monaco", "Impact", "Rubik", "Comic Sans",
)
COLOR_OPTIONS = ("Orange", "Pink", "Lime", "Violet", "Cyan", "Gold", "Rainbow")
SHAPE_OPTIONS = ("shape_0", "shape_1", "shape_2")  # pill, ellipse, heart
ANIMATION_OPTIONS = ("anim_0", "anim_1", "anim_2")  # heartbeat, wave, pop
SHAPE_LABELS = {"shape_0": "Pill", "shape_1": "Ellipse", "shape_2": "Heart"}
ANIMATION_LABELS = {"anim_0": "Heartbeat", "anim_1": "Wave", "anim_2": "Pop"}

PALETTES: dict[str, dict[str, str]] = {
    "Orange": {"bg": "#F52A10", "bg2": "#FF9D19", "stroke": "#FFE45C", "stroke2": "#FFFFFF", "ink": "#FFFFFF"},
    "Pink": {"bg": "#E91E79", "bg2": "#FF6CAF", "stroke": "#FFE4F0", "stroke2": "#FFC2DC", "ink": "#FFFFFF"},
    "Lime": {"bg": "#7ED80D", "bg2": "#D9FF3B", "stroke": "#F5FFB5", "stroke2": "#FFFFFF", "ink": "#17212D"},
    "Violet": {"bg": "#743AF0", "bg2": "#D05BFF", "stroke": "#83F5FF", "stroke2": "#FFFFFF", "ink": "#FFFFFF"},
    "Cyan": {"bg": "#00B6D8", "bg2": "#52E4CC", "stroke": "#FFF27A", "stroke2": "#FFFFFF", "ink": "#12333A"},
    "Gold": {"bg": "#FFAD22", "bg2": "#FFE65A", "stroke": "#FF5C93", "stroke2": "#FFFFFF", "ink": "#30200B"},
    "Rainbow": {"bg": "#FF315B", "bg2": "#7A39FF", "stroke": "#44F4DF", "stroke2": "#FFFFFF", "ink": "#FFFFFF"},
}
COLOR_ICONS = {"Orange": "🟠", "Pink": "💗", "Lime": "🟢", "Violet": "🟣", "Cyan": "🔵", "Gold": "🟡", "Rainbow": "🌈"}
SHAPE_ICONS = {"shape_0": "🎟️", "shape_1": "🟣", "shape_2": "💟"}
ANIMATION_ICONS = {"anim_0": "❤️", "anim_1": "🌊", "anim_2": "✨"}

# Open/free font packages are installed by requirements.txt. For unavailable or
# proprietary families, use a close Linux-compatible substitute without shipping fonts.
FONT_PACKAGE: dict[str, tuple[str, int, str]] = {
    "Raleway Italic": ("Raleway", 700, "italic"),
    "Arial": ("Arimo", 400, "normal"),
    "Roboto": ("Roboto", 700, "normal"),
    "Madefor": ("Montserrat", 700, "normal"),
    "Rubik": ("Rubik", 700, "normal"),
}
SYSTEM_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "Raleway Italic": ("/usr/share/fonts/truetype/raleway/Raleway-Italic.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Italic.ttf"),
    "Arial": ("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    "Roboto": ("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    "Madefor": ("/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    "Monaco": ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf"),
    "Impact": ("/usr/share/fonts/truetype/msttcorefonts/Impact.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    "Rubik": ("/usr/share/fonts/truetype/rubik/Rubik-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    "Comic Sans": ("/usr/share/fonts/truetype/msttcorefonts/Comic_Sans_MS.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
}


@dataclass(frozen=True, slots=True)
class Style:
    text: str
    font: str = "Raleway Italic"
    color: str = "Orange"
    shape: str = "shape_0"
    animation: str = "anim_0"

    def normalized(self) -> "Style":
        clean = " ".join((self.text or "").split())[:20]
        return replace(
            self,
            text=clean,
            font=self.font if self.font in FONT_OPTIONS else FONT_OPTIONS[0],
            color=self.color if self.color in PALETTES else COLOR_OPTIONS[0],
            shape=self.shape if self.shape in SHAPE_OPTIONS else SHAPE_OPTIONS[0],
            animation=self.animation if self.animation in ANIMATION_OPTIONS else ANIMATION_OPTIONS[0],
        )


def _rgb(hex_color: str) -> list[float]:
    value = hex_color.lstrip("#")
    return [int(value[i:i+2], 16) / 255.0 for i in (0, 2, 4)]


def _rgba(hex_color: str) -> list[float]:
    return _rgb(hex_color) + [1.0]


@lru_cache(maxsize=32)
def font_path(font_name: str) -> str:
    package = FONT_PACKAGE.get(font_name)
    if package:
        try:
            import fontpkg  # type: ignore
            family, weight, style = package
            candidate = str(fontpkg.path(family, weight=weight, style=style))
            if Path(candidate).is_file():
                return candidate
        except Exception:
            pass
    for candidate in SYSTEM_FONT_CANDIDATES.get(font_name, ()):
        if Path(candidate).is_file():
            return candidate
    # Try other known system sans fonts; do not fail because a branded family is unavailable.
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        if Path(candidate).is_file():
            return candidate
    raise FileNotFoundError("No suitable font was found. Install the font packages from requirements.txt.")


@lru_cache(maxsize=16)
def _ttfont(font_name: str) -> TTFont:
    return TTFont(font_path(font_name), recalcBBoxes=False, recalcTimestamp=False)


@lru_cache(maxsize=256)
def _glyph_contours(font_name: str, char: str) -> tuple[list[dict[str, Any]], int, int, int]:
    """Return cubic contours, advance width and vertical bounds for one glyph."""
    tt = _ttfont(font_name)
    cmap = tt.getBestCmap() or {}
    glyph_name = cmap.get(ord(char), ".notdef")
    glyph_set = tt.getGlyphSet()
    glyph = glyph_set[glyph_name]
    recording = DecomposingRecordingPen(glyph_set, skipMissingComponents=True)
    glyph.draw(recording)
    cubic = RecordingPen()
    recording.replay(Qu2CuPen(cubic, max_err=1.0, all_cubic=True))

    contours: list[dict[str, Any]] = []
    commands: list[tuple[str, tuple[Any, ...]]] = []
    points: list[tuple[float, float]] = []

    def flush(closed: bool) -> None:
        nonlocal commands, points
        if not commands:
            return
        verts: list[list[float]] = []
        ins: list[list[float]] = []
        outs: list[list[float]] = []
        current: tuple[float, float] | None = None
        for op, args in commands:
            if op == "moveTo":
                p = args[0]
                current = (float(p[0]), float(p[1]))
                verts.append([current[0], current[1]])
                ins.append([0.0, 0.0]); outs.append([0.0, 0.0])
            elif op == "lineTo":
                p = args[0]
                current = (float(p[0]), float(p[1]))
                verts.append([current[0], current[1]])
                ins.append([0.0, 0.0]); outs.append([0.0, 0.0])
                points.append(current)
            elif op == "curveTo":
                c1, c2, end = args
                if not verts:
                    continue
                start = verts[-1]
                outs[-1] = [float(c1[0]) - start[0], float(c1[1]) - start[1]]
                end_pt = (float(end[0]), float(end[1]))
                verts.append([end_pt[0], end_pt[1]])
                ins.append([float(c2[0]) - end_pt[0], float(c2[1]) - end_pt[1]])
                outs.append([0.0, 0.0])
                points.append(end_pt)
                current = end_pt
        if verts:
            contours.append({"v": verts, "i": ins, "o": outs, "c": bool(closed)})
        commands = []

    for op, args in cubic.value:
        if op == "moveTo":
            flush(False)
            p = args[0]
            commands = [(op, args)]
            points.append((float(p[0]), float(p[1])))
        elif op in ("lineTo", "curveTo"):
            commands.append((op, args))
            for p in args:
                points.append((float(p[0]), float(p[1])))
        elif op in ("closePath", "endPath"):
            flush(op == "closePath")
    flush(False)

    # A glyph may have several contours (e.g. the hole inside O). Paths are kept
    # together in one Lottie shape group and use the even-odd fill rule.
    advance = int(tt["hmtx"][glyph_name][0]) if glyph_name in tt["hmtx"].metrics else int(tt["head"].unitsPerEm * 0.5)
    ys = [p[1] for p in points]
    ymin = int(min(ys)) if ys else 0
    ymax = int(max(ys)) if ys else int(tt["head"].unitsPerEm * 0.7)
    return contours, advance, ymin, ymax


def _static(value: Any) -> dict[str, Any]:
    return {"a": 0, "k": value}


def _animated(frames: list[tuple[int, Any]]) -> dict[str, Any]:
    """Make smoothly eased Lottie keyframes; include final loop keyframe."""
    result = []
    for idx, (frame, value) in enumerate(frames):
        item: dict[str, Any] = {"t": frame, "s": value}
        if idx + 1 < len(frames):
            item["e"] = frames[idx + 1][1]
            item["o"] = {"x": [0.333], "y": [0.0]}
            item["i"] = {"x": [0.667], "y": [1.0]}
        result.append(item)
    return {"a": 1, "k": result}


def _layer(index: int, name: str, position: tuple[float, float], *, scale_frames=None, rotation_frames=None, opacity_frames=None, position_frames=None) -> dict[str, Any]:
    return {
        "ddd": 0, "ind": index, "ty": 4, "nm": name,
        "sr": 1,
        "ks": {
            "o": _animated(opacity_frames) if opacity_frames else _static(100),
            "r": _animated(rotation_frames) if rotation_frames else _static(0),
            "p": _animated(position_frames) if position_frames else _static([round(position[0], 3), round(position[1], 3)]),
            "a": _static([0, 0]),
            "s": _animated(scale_frames) if scale_frames else _static([100, 100]),
        },
        "ao": 0, "shapes": [], "ip": 0, "op": END_FRAME, "st": 0, "bm": 0,
    }


def _shape_path(contour: dict[str, Any], scale: float, index: int = 0) -> dict[str, Any]:
    return {
        "ty": "sh", "ind": index,
        "ks": _static({
            "i": [[round(p[0] * scale, 3), round(-p[1] * scale, 3)] for p in contour["i"]],
            "o": [[round(p[0] * scale, 3), round(-p[1] * scale, 3)] for p in contour["o"]],
            "v": [[round(p[0] * scale, 3), round(-p[1] * scale, 3)] for p in contour["v"]],
            "c": contour["c"],
        }),
        "nm": "Glyph outline", "mn": "ADBE Vector Shape - Group", "hd": False,
    }


def _rect_path(width: float, height: float, radius: float) -> dict[str, Any]:
    return {"ty": "rc", "d": 1, "s": _static([round(width, 3), round(height, 3)]), "p": _static([0, 0]), "r": _static(round(radius, 3)), "nm": "Capsule path", "mn": "ADBE Vector Shape - Rect", "hd": False}


def _ellipse_path(width: float, height: float) -> dict[str, Any]:
    return {"ty": "el", "d": 1, "s": _static([round(width, 3), round(height, 3)]), "p": _static([0, 0]), "nm": "Ellipse path", "mn": "ADBE Vector Shape - Ellipse", "hd": False}


def _heart_path(width: float, height: float) -> dict[str, Any]:
    # Cubic heart with a broad top and tapered bottom. Handles are relative to vertices.
    w, h = width, height
    points = [(-0.5*w, -0.12*h), (-0.25*w, -0.48*h), (0, -0.31*h), (0.25*w, -0.48*h), (0.5*w, -0.12*h), (0, 0.5*h)]
    outs = [[0.0, -0.22*h], [0.16*w, -0.04*h], [0.12*w, -0.18*h], [0.16*w, 0.04*h], [0.0, 0.22*h], [-0.3*w, -0.12*h]]
    ins = [[-0.22*w, -0.12*h], [-0.12*w, -0.1*h], [-0.12*w, 0.1*h], [-0.16*w, -0.04*h], [0.22*w, 0.12*h], [0.3*w, -0.12*h]]
    return {"ty": "sh", "ind": 0, "ks": _static({"v": [[round(x,3),round(y,3)] for x,y in points], "i": [[round(x,3),round(y,3)] for x,y in ins], "o": [[round(x,3),round(y,3)] for x,y in outs], "c": True}), "nm": "Heart path", "mn": "ADBE Vector Shape - Group", "hd": False}


def _transform_group(items: list[dict[str, Any]]) -> dict[str, Any]:
    items.append({"ty": "tr", "p": _static([0, 0]), "a": _static([0, 0]), "s": _static([100, 100]), "r": _static(0), "o": _static(100), "sk": _static(0), "sa": _static(0), "nm": "Transform"})
    return {"ty": "gr", "it": items, "nm": "Vector group", "np": len(items)-1, "cix": 2, "bm": 0, "ix": 1, "mn": "ADBE Vector Group", "hd": False}


def _color_frames(primary: str, secondary: str) -> list[tuple[int, list[float]]]:
    return [(0, _rgba(primary)), (60, _rgba(secondary)), (120, _rgba(primary)), (180, _rgba(primary))]


def _motion_frames(animation: str, index: int, count: int, y: float) -> tuple[list[tuple[int, list[float]]], list[tuple[int, float]]]:
    scale: list[tuple[int, list[float]]] = []
    position: list[tuple[int, list[float]]] = []
    rotation: list[tuple[int, float]] = []
    phase = index / max(count, 1)
    for t in (0, 15, 30, 45, 60, 90, 120, 150, 165, 180):
        if animation == "anim_0":  # heartbeat
            p = (t % 60) / 60.0
            key_scale = 100 + (4.0 * math.sin(math.pi * p) if p <= 0.5 else -2.0 * math.sin(math.pi * (p - 0.5) * 2))
            # Use a three-beat cycle that returns exactly to 100 at loop boundaries.
            if t % 60 == 0: key_scale = 100
            scale.append((t, [round(key_scale, 3), round(key_scale, 3)]))
            position.append((t, [0, round(y, 3)]))
        elif animation == "anim_1":  # flowing wave
            wave = math.sin((t / END_FRAME) * (2 * math.pi) + phase * (2 * math.pi))
            scale.append((t, [100, 100]))
            position.append((t, [0, round(y + 7.0 * wave, 3)]))
        else:  # pop: soft, staggered pop-and-settle every second
            p = ((t / 60.0) + phase) % 1.0
            if p < 0.22:
                s = 100 + 9 * (p / 0.22)
            elif p < 0.48:
                s = 109 - 9 * ((p - 0.22) / 0.26)
            else:
                s = 100 + 1.5 * math.sin((p - 0.48) / 0.52 * math.pi)
            scale.append((t, [round(s, 3), round(s, 3)]))
            position.append((t, [0, round(y, 3)]))
    # Drop duplicate frame entries; Lottie expects ascending distinct times.
    def dedupe(seq):
        out = {}
        for t, val in seq: out[t] = val
        return sorted(out.items())
    return dedupe(scale), dedupe(rotation)


def _background_scale_frames(animation: str, *, halo: bool = False) -> list[tuple[int, list[float]]]:
    """Background motion matching the selected animation while closing the loop."""
    if animation == "anim_0":  # heartbeat: 3 light pulses, exact match at frame 0/180
        return [(t, [v, v]) for t, v in ((0, 100), (15, 103 if not halo else 102.5), (30, 100), (45, 98 if not halo else 98.5), (60, 100), (75, 103 if not halo else 102.5), (90, 100), (105, 98 if not halo else 98.5), (120, 100), (135, 103 if not halo else 102.5), (150, 100), (165, 98 if not halo else 98.5), (180, 100))]
    if animation == "anim_1":  # wave: gentle horizontal/vertical breathing
        return [(0, [100, 100]), (45, [102 if not halo else 101.5, 98 if not halo else 98.5]), (90, [100, 100]), (135, [98 if not halo else 98.5, 102 if not halo else 101.5]), (180, [100, 100])]
    # pop: three soft squash-and-settle cycles; loop starts/ends in the same pose.
    return [(0, [95, 95]), (12, [106 if not halo else 104, 106 if not halo else 104]), (24, [100, 100]), (42, [100, 100]), (60, [95, 95]), (72, [106 if not halo else 104, 106 if not halo else 104]), (84, [100, 100]), (102, [100, 100]), (120, [95, 95]), (132, [106 if not halo else 104, 106 if not halo else 104]), (144, [100, 100]), (162, [100, 100]), (180, [95, 95])]


def _shape_dimensions(text_width: float, text_height: float, shape: str) -> tuple[float, float, float]:
    if shape == "shape_1":
        width = max(180.0, min(410.0, text_width + 72.0))
        height = max(165.0, min(250.0, text_height + 90.0))
    elif shape == "shape_2":
        width = max(200.0, min(440.0, text_width + 82.0))
        height = max(185.0, min(260.0, text_height + 115.0))
    else:
        # Keep the animated halo + stroke inside the 512 px canvas even at the
        # largest Pop scale (104% halo scale).
        width = max(190.0, min(455.0, text_width + 72.0))
        height = max(140.0, min(190.0, text_height + 70.0))
    radius = height * 0.49
    return width, height, radius


def build_tgs(style: Style, variant: int = 0) -> bytes:
    """Build one 512×512 60-FPS animated vector custom emoji as TGS bytes."""
    style = style.normalized()
    if not style.text:
        raise ValueError("Text is required to render an animated custom emoji.")
    tt = _ttfont(style.font)
    units = int(tt["head"].unitsPerEm)
    # Measure from glyph advances while preserving spaces. Very long labels shrink to fit.
    glyphs = []
    raw_width = 0
    y_mins, y_maxs = [], []
    for ch in style.text:
        contours, advance, ymin, ymax = _glyph_contours(style.font, ch)
        glyphs.append((ch, contours, advance, ymin, ymax))
        raw_width += advance
        if contours:
            y_mins.append(ymin); y_maxs.append(ymax)
    if not y_mins:
        raise ValueError("This font has no vector outlines for the supplied text. Try a different font.")
    raw_height = max(y_maxs) - min(y_mins)
    max_width = 365.0 if style.shape != "shape_0" else 405.0
    scale = min(66.0 / max(raw_height, 1), max_width / max(raw_width, 1))
    text_width = raw_width * scale
    text_height = raw_height * scale
    badge_w, badge_h, radius = _shape_dimensions(text_width, text_height, style.shape)
    center_x = CANVAS / 2
    baseline_y = CANVAS / 2 + (max(y_mins) + max(y_maxs)) * scale / 2
    left_x = center_x - text_width / 2

    # Create two harmonizing variants in addition to the selected palette. Same text,
    # font, shape and motion; colorways differ subtly as in a compact emoji set.
    color_name = style.color
    palette = dict(PALETTES[color_name])
    if variant == 1:
        palette["bg"], palette["bg2"] = palette["bg2"], palette["bg"]
    elif variant == 2 and color_name != "Rainbow":
        palette["stroke"], palette["stroke2"] = palette["stroke2"], palette["stroke"]
    elif variant == 2:
        palette["bg"], palette["bg2"] = "#7A39FF", "#FF315B"

    if style.shape == "shape_0":
        main_path = _rect_path(badge_w, badge_h, radius)
        halo_path = _rect_path(badge_w + 18, badge_h + 18, radius + 9)
    elif style.shape == "shape_1":
        main_path = _ellipse_path(badge_w, badge_h)
        halo_path = _ellipse_path(badge_w + 18, badge_h + 18)
    else:
        main_path = _heart_path(badge_w, badge_h)
        halo_path = _heart_path(badge_w + 18, badge_h + 18)

    layers: list[dict[str, Any]] = []
    # Pulse halo remains behind the badge and stays subtle.
    halo_layer = _layer(1, "Accent halo", (center_x, CANVAS / 2), scale_frames=_background_scale_frames(style.animation, halo=True))
    halo_layer["shapes"] = [_transform_group([halo_path, {"ty":"st","c":_animated([(t, _rgba(palette["stroke"])) for t in (0,60,120,180)],),"o":_static(28),"w":_static(8),"lc":2,"lj":2,"ml":4,"nm":"Soft halo","mn":"ADBE Vector Graphic - Stroke","hd":False}])]
    layers.append(halo_layer)

    bg_layer = _layer(2, "Animated background capsule", (center_x, CANVAS / 2), scale_frames=_background_scale_frames(style.animation))
    bg_items = [main_path,
        {"ty":"fl","c":_animated(_color_frames(palette["bg"], palette["bg2"])),"o":_static(100),"r":2,"nm":"Animated fill","mn":"ADBE Vector Graphic - Fill","hd":False},
        {"ty":"st","c":_animated(_color_frames(palette["stroke"], palette["stroke2"])),"o":_static(100),"w":_static(5),"lc":2,"lj":2,"ml":4,"nm":"Border","mn":"ADBE Vector Graphic - Stroke","hd":False}]
    bg_layer["shapes"] = [_transform_group(bg_items)]
    layers.append(bg_layer)

    # Glyph outlines are positioned in their own local font coordinates; only the
    # advance cursor is relative to the centered line origin. Starting at left_x
    # here would apply the centering offset twice and push text off-canvas.
    cursor = 0.0
    for glyph_index, (ch, contours, advance, _ymin, _ymax) in enumerate(glyphs):
        glyph_x = left_x + cursor
        cursor += advance * scale
        if not contours:
            continue
        scale_frames, _unused = _motion_frames(style.animation, glyph_index, max(len(glyphs), 1), 0.0)
        position_frames = []
        for t in (0, 30, 60, 90, 120, 150, 180):
            if style.animation == "anim_1":
                dy = 7.0 * math.sin((t / END_FRAME) * 2 * math.pi + glyph_index / max(len(glyphs),1) * 2 * math.pi)
            else:
                dy = 0.0
            position_frames.append((t, [round(glyph_x,3), round(baseline_y + dy,3)]))
        layer = _layer(10 + glyph_index, f"Glyph {glyph_index+1}", (glyph_x, baseline_y), scale_frames=scale_frames, position_frames=position_frames)
        glyph_items = [_shape_path(contour, scale, path_index) for path_index, contour in enumerate(contours)]
        glyph_items.extend([
            {"ty":"st","c":_static(_rgba("#182033" if palette["ink"] == "#FFFFFF" else "#FFFFFF")),"o":_static(100),"w":_static(1.1),"lc":2,"lj":2,"ml":4,"nm":"Text edge","mn":"ADBE Vector Graphic - Stroke","hd":False},
            {"ty":"fl","c":_static(_rgba(palette["ink"])),"o":_static(100),"r":2,"nm":"Text fill","mn":"ADBE Vector Graphic - Fill","hd":False},
        ])
        layer["shapes"] = [_transform_group(glyph_items)]
        layers.append(layer)

    document = {
        "v": "5.7.4", "fr": FPS, "ip": 0, "op": END_FRAME,
        "w": CANVAS, "h": CANVAS, "nm": style.text, "ddd": 0,
        "assets": [], "layers": layers, "markers": [],
    }
    raw = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(raw)
    data = output.getvalue()
    validate_tgs(data)
    if len(data) >= MAX_TGS_BYTES:
        raise ValueError(f"Generated TGS exceeds Telegram's 64 KB limit ({len(data)} bytes). Use shorter text or a simpler font.")
    return data


def validate_tgs(data: bytes) -> dict[str, Any]:
    """Validate the gzip/Lottie envelope and hard size/timing constraints."""
    try:
        document = json.loads(gzip.decompress(data).decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid TGS gzip/Lottie document: {exc}") from exc
    assert document.get("w") == CANVAS and document.get("h") == CANVAS, "TGS canvas must be 512×512"
    assert document.get("fr") == FPS, "TGS frame rate must be 60 FPS"
    assert document.get("op") <= END_FRAME, "TGS duration exceeds 3 seconds"
    assert isinstance(document.get("layers"), list) and document["layers"], "TGS has no vector layers"
    assert len(data) < MAX_TGS_BYTES, "TGS file exceeds 64 KB"
    return {"bytes": len(data), "width": document["w"], "height": document["h"], "fps": document["fr"], "frames": document["op"], "layers": len(document["layers"])}


def _draw_heart(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str, outline: str, width: int) -> None:
    l, t, r, b = box
    w, h = r-l, b-t
    pts = [(l+w*.50,t+h*.94),(l+w*.08,t+h*.52),(l+w*.06,t+h*.27),(l+w*.20,t+h*.08),(l+w*.37,t+h*.08),(l+w*.50,t+h*.25),(l+w*.63,t+h*.08),(l+w*.80,t+h*.08),(l+w*.94,t+h*.27),(l+w*.92,t+h*.52)]
    draw.polygon(pts, fill=fill)
    draw.line(pts+[pts[0]], fill=outline, width=width, joint="curve")


def _draw_preview_frame(style: Style, frame_index: int, size: int = 512, variant: int = 0, total_frames: int = 24) -> Image.Image:
    style = style.normalized()
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pal = dict(PALETTES[style.color])
    if variant == 1: pal["bg"], pal["bg2"] = pal["bg2"], pal["bg"]
    elif variant == 2 and style.color != "Rainbow": pal["stroke"], pal["stroke2"] = pal["stroke2"], pal["stroke"]
    phase = frame_index / max(1, total_frames - 1) * 2 * math.pi
    if style.animation == "anim_0":
        pulse = 1.0 + 0.015 * math.sin(phase * 2)
        dx, dy = 0, 0
    elif style.animation == "anim_1":
        pulse = 1.0
        dx, dy = 3 * math.sin(phase), 5 * math.sin(phase)
    else:
        pulse = 0.96 + 0.04 * (0.5 + 0.5 * math.sin(phase * 3))
        dx, dy = 0, -2 * abs(math.sin(phase * 1.5))
    # Dimensions are consistent with final vector pack.
    font_size = max(18, int(size * 0.13))
    try:
        font = ImageFont.truetype(font_path(style.font), font_size)
    except Exception:
        font = ImageFont.load_default()
    bbox_text = d.textbbox((0, 0), style.text, font=font, stroke_width=0)
    text_w = max(1, bbox_text[2]-bbox_text[0]); text_h = max(1, bbox_text[3]-bbox_text[1])
    max_w = size * (0.70 if style.shape != "shape_0" else 0.80)
    fit = min(1.0, max_w / text_w, (size * 0.16) / text_h)
    if fit < 1:
        font_size = max(12, int(font_size * fit))
        try: font = ImageFont.truetype(font_path(style.font), font_size)
        except Exception: font = ImageFont.load_default()
        bbox_text = d.textbbox((0,0), style.text, font=font)
        text_w=max(1,bbox_text[2]-bbox_text[0]); text_h=max(1,bbox_text[3]-bbox_text[1])
    w = min(size*.91, max(size*.36, text_w + size*.18))
    h = max(size*.27, text_h + size*.14)
    if style.shape == "shape_1": h = max(size*.34, min(size*.47, h + size*.06))
    if style.shape == "shape_2": h = max(size*.38, min(size*.50, h + size*.12)); w = min(size*.86, w + size*.08)
    w *= pulse; h *= pulse
    cx, cy = size/2 + dx, size/2 + dy
    box = (int(cx-w/2), int(cy-h/2), int(cx+w/2), int(cy+h/2))
    if style.shape == "shape_1":
        d.ellipse((box[0]-5,box[1]-5,box[2]+5,box[3]+5), outline=pal["stroke"], width=max(2,size//75))
        d.ellipse(box, fill=pal["bg"], outline=pal["stroke2"], width=max(2,size//95))
    elif style.shape == "shape_2":
        _draw_heart(d, (box[0]-5,box[1]-5,box[2]+5,box[3]+5), pal["stroke"], pal["stroke"], max(2,size//75))
        _draw_heart(d, box, pal["bg"], pal["stroke2"], max(2,size//95))
    else:
        d.rounded_rectangle((box[0]-5,box[1]-5,box[2]+5,box[3]+5), radius=int(h*.52), fill=pal["stroke"])
        d.rounded_rectangle(box, radius=int(h*.49), fill=pal["bg"], outline=pal["stroke2"], width=max(2,size//95))
    text_color = pal["ink"]
    tx, ty = cx-text_w/2-bbox_text[0], cy-text_h/2-bbox_text[1]
    d.text((tx+1,ty+1), style.text, font=font, fill=(0,0,0,85), stroke_width=max(0,size//240), stroke_fill=(0,0,0,75))
    d.text((tx,ty), style.text, font=font, fill=text_color, stroke_width=max(0,size//240), stroke_fill=text_color)
    return img


def render_preview_gif(style: Style, *, variant: int = 0, frame_count: int = 24, size: int = 512) -> bytes:
    """Render a compact animated GIF preview using the same visible design state."""
    style = style.normalized()
    if not style.text:
        raise ValueError("Text is required to render a preview.")
    rgba_frames = [_draw_preview_frame(style, i, size=size, variant=variant, total_frames=frame_count) for i in range(frame_count)]
    palette_frames: list[Image.Image] = []
    for rgba in rgba_frames:
        rgb = Image.new("RGB", rgba.size, (255, 0, 255))
        rgb.paste(rgba, mask=rgba.getchannel("A"))
        quant = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        pal = quant.getpalette() or []
        pal = (pal + [0] * 768)[:768]
        pal[765:768] = [255, 0, 255]  # palette index 255 is transparent chroma key
        quant.putpalette(pal)
        get_pixels = lambda image: list(image.get_flattened_data()) if hasattr(image, "get_flattened_data") else list(image.getdata())
        data = get_pixels(quant)
        alpha = get_pixels(rgba.getchannel("A"))
        data = [255 if a < 8 else p for p, a in zip(data, alpha)]
        quant.putdata(data)
        quant.info["transparency"] = 255
        palette_frames.append(quant)
    out = io.BytesIO()
    palette_frames[0].save(out, format="GIF", save_all=True, append_images=palette_frames[1:], duration=100, loop=0, transparency=255, disposal=2, optimize=True)
    return out.getvalue()


def generate_pack(style: Style, progress: Callable[[str], None] | None = None) -> list[bytes]:
    """Build three custom-emoji TGS variants from the selected style."""
    style = style.normalized()
    assets: list[bytes] = []
    for i in range(CUSTOM_EMOJI_COUNT):
        if progress:
            progress(f"Rendering animated custom emoji {i + 1}/{CUSTOM_EMOJI_COUNT}…")
        assets.append(build_tgs(style, variant=i))
    if progress:
        progress("Validating all animated vector files…")
    for asset in assets:
        validate_tgs(asset)
    return assets
