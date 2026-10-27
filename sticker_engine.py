"""Render small, loopable VP9 WebM custom-emoji assets for Telegram."""
from __future__ import annotations

import colorsys
import io
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

CANVAS_SIZE = 512
CUSTOM_EMOJI_SIZE = 100
CUSTOM_EMOJI_COUNT = 3
FPS = 30
DURATION_SECONDS = 2
FRAME_COUNT = FPS * DURATION_SECONDS
MAX_WEBM_BYTES = 256 * 1024

PALETTES: dict[str, str] = {
    "Orange": "#F52A10",
    "Pink": "#F12C82",
    "Lime": "#86D915",
    "Violet": "#9955F4",
    "Cyan": "#13C7DC",
    "Gold": "#FFC33A",
    "Rainbow": "#FF315B",
}
PALETTE_GRADIENTS: dict[str, tuple[str, str]] = {
    "Orange": ("#F51E11", "#FF9D19"),
    "Pink": ("#E91E79", "#FF6CAF"),
    "Lime": ("#7ED80D", "#D9FF3B"),
    "Violet": ("#743AF0", "#D05BFF"),
    "Cyan": ("#00B6D8", "#52E4CC"),
    "Gold": ("#FFAD22", "#FFE65A"),
    "Rainbow": ("#FF315B", "#7A39FF"),
}
# The reference selector cycles through these named type styles from left to right.
FONT_OPTIONS: tuple[str, ...] = (
    "Raleway italic", "Arial", "Roboto", "Madefor", "Monaco", "Impact", "Rubik",
)
# These are stable local IDs for the three shape/animation states visible in the
# supplied recording. They intentionally do not assert the source bot's hidden
# option names; the clip does not show a complete cycle.
SHAPE_OPTIONS: tuple[str, ...] = ("shape_0", "shape_1", "shape_2")
ANIMATION_OPTIONS: tuple[str, ...] = ("anim_0", "anim_1", "anim_2")

_LEGACY_SHAPE_IDS = {"Pill": "shape_0", "Circle": "shape_1", "Heart": "shape_2"}
_LEGACY_ANIMATION_IDS = {"Wave": "anim_0", "None": "anim_1", "Float": "anim_2"}

_FONT_FILES: dict[str, tuple[str, ...]] = {
    "Raleway italic": ("/usr/share/fonts/truetype/raleway/Raleway-Italic.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Italic.ttf"),
    "Arial": ("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    "Roboto": ("/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    # "Madefor" is not openly redistributed, so use a high-quality geometric-sans substitute.
    "Madefor": ("/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    "Monaco": ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf"),
    "Impact": ("/usr/share/fonts/truetype/msttcorefonts/Impact.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    "Rubik": ("/usr/share/fonts/truetype/rubik/Rubik-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
}

@dataclass(frozen=True, slots=True)
class Style:
    text: str
    # The default visual state matches the start of the supplied editor recording.
    font: str = "Raleway italic"
    color: str = "Orange"
    shape: str = "shape_0"
    animation: str = "anim_0"

    def normalized(self) -> "Style":
        return replace(
            self,
            text=" ".join(self.text.split())[:20],
            font=self.font if self.font in FONT_OPTIONS else FONT_OPTIONS[0],
            color=self.color if self.color in PALETTES else next(iter(PALETTES)),
            shape=(
                self.shape if self.shape in SHAPE_OPTIONS
                else _LEGACY_SHAPE_IDS.get(self.shape, SHAPE_OPTIONS[0])
            ),
            animation=(
                self.animation if self.animation in ANIMATION_OPTIONS
                else _LEGACY_ANIMATION_IDS.get(self.animation, ANIMATION_OPTIONS[0])
            ),
        )


_FONT_PACKAGE_SPECS: dict[str, tuple[str, int, str]] = {
    "Raleway italic": ("Raleway", 700, "italic"),
    "Arial": ("Arimo", 400, "normal"),
    "Roboto": ("Roboto", 700, "normal"),
    # The original Madefor typeface is not openly packaged; Montserrat is used as a close geometric alternative.
    "Madefor": ("Montserrat", 700, "normal"),
    "Rubik": ("Rubik", 700, "normal"),
}


def _font(style_name: str, size: int) -> ImageFont.ImageFont:
    package_spec = _FONT_PACKAGE_SPECS.get(style_name)
    if package_spec:
        try:
            import fontpkg

            family, weight, font_style = package_spec
            font_path = fontpkg.path(family, weight=weight, style=font_style)
            font = ImageFont.truetype(str(font_path), size=size)
            # Pillow supports variation fonts. Keep the family/weight in step
            # with the visible option label when a package returns a variable
            # font rather than a pre-instanced file.
            if hasattr(font, "get_variation_axes") and hasattr(font, "set_variation_by_axes"):
                try:
                    axes = font.get_variation_axes()
                    normalized_names = []
                    for axis in axes:
                        name = axis.get("name", "")
                        if isinstance(name, bytes):
                            name = name.decode("utf-8", "ignore")
                        normalized_names.append(str(name).lower())
                    if axes and any(name in ("weight", "wght") for name in normalized_names):
                        values = [axis.get("default", 400) for axis in axes]
                        for index, name in enumerate(normalized_names):
                            if name in ("weight", "wght"):
                                values[index] = weight
                        font.set_variation_by_axes(values)
                except (OSError, ValueError, TypeError):
                    pass
            return font
        except Exception:
            # Preserve service availability if an optional font package cannot
            # be resolved; the local/system fallbacks below remain available.
            pass

    for candidate in _FONT_FILES.get(style_name, ()):
        if os.path.isfile(candidate):
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _variant_hex(hex_color: str, variant: int) -> str:
    """Return a coordinated but visibly distinct color for each pack item."""
    r, g, b = (part / 255 for part in _rgb(hex_color))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if variant == 1:
        s, v = max(0.35, s * 0.78), min(1.0, v * 1.12)
    elif variant == 2:
        h, s, v = (h + 0.045) % 1.0, min(1.0, s * 1.05), max(0.58, v * 0.92)
    else:
        h = (h + 0.085 * variant) % 1.0
        s = min(1.0, max(0.58, s * (0.88 + (variant % 3) * 0.08)))
        v = min(1.0, max(0.62, v * (0.88 + (variant % 2) * 0.12)))
    rr, gg, bb = colorsys.hsv_to_rgb(h, s, v)
    return "#{:02X}{:02X}{:02X}".format(round(rr * 255), round(gg * 255), round(bb * 255))



def _rgb_tuple(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _mix_rgb(first: tuple[int, int, int], second: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, amount))
    return tuple(round(first[i] * (1.0 - amount) + second[i] * amount) for i in range(3))


def _palette_pair(color: str, color_override: str | None = None) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if color_override is not None:
        first = _rgb_tuple(color_override)
        second = _rgb_tuple(_variant_hex(color_override, 1))
        return first, second
    pair = PALETTE_GRADIENTS.get(color, PALETTE_GRADIENTS["Orange"])
    return _rgb_tuple(pair[0]), _rgb_tuple(pair[1])


def _gradient_layer(
    width: int,
    height: int,
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    phase: float = 0.0,
    *,
    rainbow: bool = False,
) -> Image.Image:
    """Render a smooth animated gradient; Rainbow uses a continuous multicolor spectrum."""
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rainbow_stops: tuple[tuple[float, tuple[int, int, int]], ...] = (
        (0.00, (255, 49, 91)),
        (0.17, (255, 135, 35)),
        (0.34, (255, 224, 55)),
        (0.51, (52, 211, 105)),
        (0.68, (31, 188, 255)),
        (0.84, (116, 77, 255)),
        (1.00, (255, 49, 91)),
    )
    for x in range(width):
        position = x / max(1, width - 1)
        if rainbow:
            # A small phase shift makes the color spectrum drift smoothly in the loop.
            position = (position + phase / (2 * math.pi) * 0.16) % 1.0
            rgb = rainbow_stops[-1][1]
            for index in range(1, len(rainbow_stops)):
                start, start_rgb = rainbow_stops[index - 1]
                end, end_rgb = rainbow_stops[index]
                if position <= end:
                    ratio = (position - start) / max(1e-9, end - start)
                    rgb = _mix_rgb(start_rgb, end_rgb, ratio)
                    break
        else:
            position = max(
                0.0,
                min(1.0, position + 0.035 * math.sin(2 * math.pi * position + phase)),
            )
            rgb = _mix_rgb(first, second, position)
        draw.line((x, 0, x, height), fill=(*rgb, 255))
    return layer


def _draw_wavy_pill_mask(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    phase: float = 0.0,
    amplitude: float = 3.0,
) -> None:
    """Draw the reference bot's soft, slightly irregular capsule silhouette."""
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    if width <= 1 or height <= 1:
        draw.rounded_rectangle(box, radius=max(1, height // 2), fill=255)
        return
    radius = min(height / 2.0, width / 2.0)
    center_y = (top + bottom) / 2.0
    span = max(0.0, width - 2.0 * radius)
    points: list[tuple[float, float]] = []
    steps = max(24, int(span / 3))
    for index in range(steps + 1):
        ratio = index / steps
        x = left + radius + span * ratio
        wave = math.sin(2 * math.pi * ratio * 1.7 + phase) * 0.72
        wave += math.sin(2 * math.pi * ratio * 2.9 - phase * 0.7) * 0.28
        points.append((x, top + amplitude * wave))
    right_center_x = right - radius
    for index in range(1, 25):
        angle = -math.pi / 2 + math.pi * index / 24
        cap = radius * (1.0 + 0.018 * math.sin(phase + index * 0.22))
        points.append((right_center_x + cap * math.cos(angle), center_y + cap * math.sin(angle)))
    for index in range(steps, -1, -1):
        ratio = index / steps
        x = left + radius + span * ratio
        wave = math.sin(2 * math.pi * ratio * 1.7 + phase + math.pi) * 0.72
        wave += math.sin(2 * math.pi * ratio * 2.9 - phase * 0.7 + math.pi) * 0.28
        points.append((x, bottom + amplitude * wave))
    left_center_x = left + radius
    for index in range(1, 25):
        angle = math.pi / 2 + math.pi * index / 24
        cap = radius * (1.0 + 0.018 * math.sin(phase + index * 0.22))
        points.append((left_center_x + cap * math.cos(angle), center_y + cap * math.sin(angle)))
    draw.polygon(points, fill=255)


def _shape_mask(
    size: tuple[int, int],
    shape: str,
    box: tuple[int, int, int, int],
    phase: float = 0.0,
    wave_amount: float = 0.0,
) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if shape == "shape_0":
        _draw_wavy_pill_mask(draw, box, phase, max(0.8, wave_amount))
    elif shape == "shape_1":
        draw.ellipse(box, fill=255)
    elif shape == "shape_2":
        _draw_shape(draw, "Heart", box, 255)
    return mask


def _text_color(first: tuple[int, int, int], second: tuple[int, int, int]) -> tuple[int, int, int, int]:
    average = _mix_rgb(first, second, 0.5)
    brightness = (0.2126 * average[0] + 0.7152 * average[1] + 0.0722 * average[2]) / 255
    return (18, 18, 18, 255) if brightness >= 0.60 else (255, 255, 255, 255)

def _draw_shape(draw: ImageDraw.ImageDraw, shape: str, box: tuple[int, int, int, int], color: tuple[int, int, int, int] | int) -> None:
    left, top, right, bottom = box
    w, h = right - left, bottom - top
    if shape == "Heart":
        cx, cy = (left + right) / 2, (top + bottom) / 2
        points = []
        for step in range(96):
            t = (2 * math.pi * step) / 96
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            points.append((cx + (x / 32) * w, cy - (y / 30) * h))
        draw.polygon(points, fill=color)


def render_frame(style: Style, time_seconds: float = 0.0, size: int = CANVAS_SIZE, color_override: str | None = None) -> Image.Image:
    """Render a transparent square sticker with a compact color shape behind the text."""
    style = style.normalized()
    # Supersample the 512px video sticker twice; previews use the same high-quality path.
    scale = 2 if size >= 512 else 3
    large_size = size * scale
    image = Image.new("RGBA", (large_size, large_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    center = large_size / 2

    text = style.text or " "
    max_text_w, max_text_h = large_size * 0.72, large_size * 0.38
    font_size = int(large_size * 0.40)
    stroke_width = max(1, large_size // 260)
    chosen_font: ImageFont.ImageFont = _font(style.font, max(10, font_size))
    bounds = draw.textbbox((0, 0), text, font=chosen_font, stroke_width=stroke_width)
    minimum_font_size = max(10, int(large_size * 0.045))
    while font_size > minimum_font_size:
        chosen_font = _font(style.font, font_size)
        bounds = draw.textbbox((0, 0), text, font=chosen_font, stroke_width=stroke_width)
        if (bounds[2] - bounds[0]) <= max_text_w and (bounds[3] - bounds[1]) <= max_text_h:
            break
        font_size = max(minimum_font_size, font_size - 2)
    chosen_font = _font(style.font, max(10, font_size))
    bounds = draw.textbbox((0, 0), text, font=chosen_font, stroke_width=stroke_width)
    text_w = max(1, bounds[2] - bounds[0])
    text_h = max(1, bounds[3] - bounds[1])
    pad_x = max(large_size * 0.045, text_h * 0.30)
    pad_y = max(large_size * 0.025, text_h * 0.12)
    box_w = min(large_size * 0.90, text_w + pad_x * 2)
    box_h = min(large_size * 0.50, text_h + pad_y * 2)
    box = (
        int(center - box_w / 2),
        int(center - box_h / 2),
        int(center + box_w / 2),
        int(center + box_h / 2),
    )
    phase = (time_seconds / DURATION_SECONDS) * (2 * math.pi)
    palette_first, palette_second = _palette_pair(style.color, color_override)
    shape_phase = phase if style.animation == "anim_0" else 0.0
    shape_amplitude = box_h * 0.075 if style.animation == "anim_0" and style.shape == "shape_0" else 0.8
    mask = _shape_mask((large_size, large_size), style.shape, box, shape_phase, shape_amplitude)
    gradient = _gradient_layer(
        large_size,
        large_size,
        palette_first,
        palette_second,
        phase if style.animation != "anim_1" else 0.0,
        rainbow=style.color == "Rainbow" and color_override is None,
    )
    gradient.putalpha(mask)
    image.alpha_composite(gradient)

    text_color = _text_color(palette_first, palette_second)
    if text_color[:3] == (255, 255, 255):
        draw.text(
            (center + large_size * 0.006, center + large_size * 0.012),
            text,
            font=chosen_font,
            anchor="mm",
            fill=(0, 0, 0, 72),
            stroke_width=0,
        )
    draw.text(
        (center, center),
        text,
        font=chosen_font,
        anchor="mm",
        fill=text_color,
        stroke_width=0,
    )

    phase = (time_seconds / DURATION_SECONDS) * (2 * math.pi)
    factor, dx, dy, angle = 1.0, 0.0, 0.0, 0.0
    if style.animation == "anim_0":
        dx = large_size * 0.012 * math.sin(phase)
    elif style.animation == "anim_2":
        # The visible down-arrow state is represented by a gentle downward bob.
        dy = large_size * 0.045 * abs(math.sin(phase))

    if abs(angle) > 0.1:
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, center=(center, center))
    if abs(factor - 1.0) > 0.005:
        resized_size = max(1, int(large_size * factor))
        resized = image.resize((resized_size, resized_size), Image.Resampling.LANCZOS)
        image = Image.new("RGBA", (large_size, large_size), (0, 0, 0, 0))
        image.alpha_composite(resized, ((large_size - resized_size) // 2, (large_size - resized_size) // 2))
    if dx or dy:
        shifted = Image.new("RGBA", (large_size, large_size), (0, 0, 0, 0))
        shifted.alpha_composite(image, (int(dx), int(dy)))
        image = shifted
    return image.resize((size, size), Image.Resampling.LANCZOS)

def render_preview_png(style: Style) -> bytes:
    image = render_frame(style, time_seconds=0.55, size=512)
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()


def _render_preview_frame(style: Style, time_seconds: float) -> Image.Image:
    """Render the wide, white editor preview shown by the reference bot."""
    style = style.normalized()
    width, height = 660, 220
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    content = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(content)
    text = style.text or " "
    # Match the source clip: at a 660x220 GIF canvas, "Hello" is about
    # 110px wide and the colored badge is about 165px wide (roughly one
    # quarter of the canvas). Large text or a 300-500px badge is not faithful.
    font_size = 52
    chosen_font = _font(style.font, font_size)
    bounds = draw.textbbox((0, 0), text, font=chosen_font)
    while font_size > 22 and (
        bounds[2] - bounds[0] > width * 0.70 or bounds[3] - bounds[1] > height * 0.44
    ):
        font_size -= 2
        chosen_font = _font(style.font, font_size)
        bounds = draw.textbbox((0, 0), text, font=chosen_font)
    bounds = draw.textbbox((0, 0), text, font=chosen_font)
    text_w = max(1, bounds[2] - bounds[0])
    text_h = max(1, bounds[3] - bounds[1])
    center_x, center_y = width / 2, height / 2
    phase = (time_seconds / DURATION_SECONDS) * (2 * math.pi)
    pulse = 1.0
    dx = dy = angle = 0.0
    if style.animation == "anim_0":
        dx = 2.0 * math.sin(phase)
    elif style.animation == "anim_2":
        dy = 4.0 * abs(math.sin(phase))
    else:
        pulse, dx, dy, angle = 1.0, 0.0, 0.0, 0.0

    # The source's capsule has moderate side padding and stays compact within
    # the wide white canvas; its height is close to a third of the canvas.
    pad_x = max(20.0, text_h * 0.45)
    pad_y = max(5.0, text_h * 0.15)
    badge_w = min(width * 0.62, max(160.0, text_w + 2 * pad_x))
    badge_h = min(height * 0.38, max(56.0, text_h + 2 * pad_y))
    badge_x = center_x - badge_w / 2
    badge_y = center_y - badge_h / 2
    box = (int(badge_x), int(badge_y), int(badge_x + badge_w), int(badge_y + badge_h))
    palette_first, palette_second = _palette_pair(style.color)
    mask = _shape_mask(
        (width, height),
        style.shape,
        box,
        phase if style.animation == "anim_0" else 0.0,
        badge_h * 0.08 if style.animation == "anim_0" and style.shape == "shape_0" else 0.8,
    )
    gradient = _gradient_layer(
        width,
        height,
        palette_first,
        palette_second,
        phase if style.animation != "anim_1" else 0.0,
        rainbow=style.color == "Rainbow",
    )
    gradient.putalpha(mask)
    content.alpha_composite(gradient)
    text_color = _text_color(palette_first, palette_second)
    draw = ImageDraw.Draw(content)
    if text_color[:3] == (255, 255, 255):
        draw.text((center_x + 1.2, center_y + 1.4), text, font=chosen_font, anchor="mm", fill=(0, 0, 0, 65))
    draw.text((center_x, center_y), text, font=chosen_font, anchor="mm", fill=text_color)

    if abs(angle) > 0.01:
        content = content.rotate(angle, resample=Image.Resampling.BICUBIC, center=(center_x, center_y))
    if abs(pulse - 1.0) > 0.002:
        resized = content.resize((max(1, int(width * pulse)), max(1, int(height * pulse))), Image.Resampling.LANCZOS)
        content = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        content.alpha_composite(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    if dx or dy:
        shifted = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        shifted.alpha_composite(content, (round(dx), round(dy)))
        content = shifted
    canvas.alpha_composite(content)
    return canvas.convert("RGB")


def render_preview_gif(style: Style) -> bytes:
    """Render a looping GIF with the original bot's wide preview proportions."""
    frames = [
        _render_preview_frame(style, index * (DURATION_SECONDS / 15))
        for index in range(15)
    ]
    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=round(DURATION_SECONDS * 1000 / len(frames)),
        loop=0,
        disposal=2,
        optimize=True,
    )
    data = output.getvalue()
    if not data or not data.startswith((b"GIF87a", b"GIF89a")):
        raise RuntimeError("Could not render the animated preview.")
    return data



def render_loading_gif(style: Style) -> bytes:
    """Render the small centered spinner shown while a selector updates the preview."""
    style = style.normalized()
    width, height = 660, 220
    frame_count = 16
    frames: list[Image.Image] = []
    center_x, center_y = width // 2, height // 2
    radius = 13
    spinner_color = (112, 112, 112)
    for index in range(frame_count):
        frame = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(frame)
        start_angle = index * (360 / frame_count)
        draw.arc(
            (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
            start=start_angle,
            end=start_angle + 265,
            fill=spinner_color,
            width=4,
        )
        frames.append(frame)

    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=55,
        loop=0,
        disposal=2,
        optimize=True,
    )
    data = output.getvalue()
    if not data or not data.startswith((b"GIF87a", b"GIF89a")):
        raise RuntimeError("Could not render the loading preview.")
    return data
def _ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        executable = shutil.which("ffmpeg")
        if executable:
            return executable
        raise RuntimeError("FFmpeg is not available. Ensure imageio-ffmpeg is installed.")


def _encode_webm(frames: Iterable[Image.Image], *, size: int = CANVAS_SIZE) -> bytes:
    if size < 1:
        raise ValueError("Video dimensions must be positive.")
    executable = _ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="animated-text-sticker-") as temp_dir:
        raw_path = Path(temp_dir) / "frames.rgba"
        output_path = Path(temp_dir) / "sticker.webm"
        with raw_path.open("wb") as raw_file:
            for frame in frames:
                if frame.size != (size, size):
                    frame = frame.resize((size, size), Image.Resampling.LANCZOS)
                raw_file.write(frame.convert("RGBA").tobytes())

        if not raw_path.exists() or raw_path.stat().st_size == 0:
            raise RuntimeError("No animation frames were rendered.")

        data: bytes | None = None
        last_size = 0
        # Start with high visual quality, then step down only if Telegram's
        # 256 KB video-sticker limit would otherwise be exceeded.
        for bitrate_kbps, crf in ((220, 30), (180, 32), (140, 34), (110, 36)):
            output_path.unlink(missing_ok=True)
            command = [
                executable, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "rgba", "-video_size", f"{size}x{size}",
                "-framerate", str(FPS), "-i", str(raw_path), "-an", "-frames:v", str(FRAME_COUNT),
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
                "-deadline", "good", "-cpu-used", "5", "-b:v", f"{bitrate_kbps}k", "-crf", str(crf),
                "-metadata:s:v:0", "alpha_mode=1", "-f", "webm", str(output_path),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
            if result.returncode != 0 or not output_path.exists():
                details = (result.stderr or "FFmpeg did not produce an output file")[-1200:]
                raise RuntimeError(f"Could not render the animated sticker: {details}")
            candidate = output_path.read_bytes()
            last_size = len(candidate)
            if candidate and last_size <= MAX_WEBM_BYTES:
                data = candidate
                break

        if data is None:
            raise RuntimeError(
                f"The rendered animation exceeds Telegram's 256 KB video-sticker limit "
                f"even after quality reduction ({last_size} bytes)."
            )
    return data


def render_single_webm(style: Style) -> bytes:
    """Render the complete text as one transparent 512px VP9 video sticker."""
    normalized = style.normalized()
    if not normalized.text:
        raise ValueError("Sticker text cannot be empty.")
    frames = (
        render_frame(normalized, time_seconds=index / FPS, size=CANVAS_SIZE)
        for index in range(FRAME_COUNT)
    )
    return _encode_webm(frames)


def render_single_custom_emoji_webm(style: Style, color_override: str | None = None) -> bytes:
    """Render the complete text into a Telegram-compliant 100×100 custom emoji."""
    normalized = style.normalized()
    if not normalized.text:
        raise ValueError("Custom emoji text cannot be empty.")
    frames = (
        render_frame(
            normalized,
            time_seconds=index / FPS,
            size=CUSTOM_EMOJI_SIZE,
            color_override=color_override,
        )
        for index in range(FRAME_COUNT)
    )
    return _encode_webm(frames, size=CUSTOM_EMOJI_SIZE)


def render_pack_webm(style: Style) -> list[bytes]:
    """Render three custom emoji variants, each containing the full original text."""
    normalized = style.normalized()
    if not normalized.text:
        return []
    base_color = PALETTES[normalized.color]
    variants: tuple[str | None, ...] = (
        None,
        _variant_hex(base_color, 1),
        _variant_hex(base_color, 2),
    )
    return [
        render_single_custom_emoji_webm(normalized, color_override=color)
        for color in variants
    ]
