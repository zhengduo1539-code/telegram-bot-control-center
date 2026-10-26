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

CANVAS_SIZE = 100
FPS = 30
DURATION_SECONDS = 2
FRAME_COUNT = FPS * DURATION_SECONDS
MAX_WEBM_BYTES = 256 * 1024

PALETTES: dict[str, str] = {
    "Violet": "#9B5CFF",
    "Orange": "#FF7A28",
    "Cyan": "#19C8E5",
    "Pink": "#FF4FA3",
    "Lime": "#9BE15D",
    "Gold": "#FFC857",
}
FONT_OPTIONS: tuple[str, ...] = (
    "Impact", "Rubik", "Monaco", "Railway Italic", "Condensed Bold", "Sans Regular",
)
SHAPE_OPTIONS: tuple[str, ...] = ("Rounded", "Circle", "Heart", "Star", "Pill", "None")
ANIMATION_OPTIONS: tuple[str, ...] = ("Pulse", "Bounce", "Wave", "Float", "Shake", "Tilt")

_FONT_FILES: dict[str, tuple[str, ...]] = {
    "Impact": ("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    "Rubik": ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    "Monaco": ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf"),
    "Railway Italic": ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Italic.ttf"),
    "Condensed Bold": ("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSansNarrow-Bold.ttf"),
    "Sans Regular": ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
}


@dataclass(frozen=True, slots=True)
class Style:
    text: str
    # These defaults match the reference bot's first editor state.
    font: str = "Monaco"
    color: str = "Gold"
    shape: str = "Rounded"
    animation: str = "Wave"

    def normalized(self) -> "Style":
        return replace(
            self,
            text=" ".join(self.text.split())[:20],
            font=self.font if self.font in FONT_OPTIONS else FONT_OPTIONS[0],
            color=self.color if self.color in PALETTES else next(iter(PALETTES)),
            shape=self.shape if self.shape in SHAPE_OPTIONS else SHAPE_OPTIONS[0],
            animation=self.animation if self.animation in ANIMATION_OPTIONS else ANIMATION_OPTIONS[0],
        )


def _font(style_name: str, size: int) -> ImageFont.ImageFont:
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


def _draw_shape(draw: ImageDraw.ImageDraw, shape: str, box: tuple[int, int, int, int], color: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    w, h = right - left, bottom - top
    if shape == "None":
        return
    if shape == "Circle":
        draw.ellipse(box, fill=color)
    elif shape == "Rounded":
        draw.rounded_rectangle(box, radius=max(4, int(min(w, h) * 0.24)), fill=color)
    elif shape == "Pill":
        draw.rounded_rectangle(box, radius=max(4, int(h * 0.48)), fill=color)
    elif shape == "Star":
        cx, cy = (left + right) / 2, (top + bottom) / 2
        points = []
        for i in range(10):
            radius = min(w, h) * (0.49 if i % 2 == 0 else 0.22)
            angle = -math.pi / 2 + i * math.pi / 5
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        draw.polygon(points, fill=color)
    elif shape == "Heart":
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
    scale = 3
    large_size = size * scale
    image = Image.new("RGBA", (large_size, large_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    color_hex = color_override or PALETTES[style.color]
    rgb = _rgb(color_hex)
    fill = (rgb[0], rgb[1], rgb[2], 255)
    center = large_size / 2
    text = style.text or " "

    # Keep the lettering centered and scale it to the canvas first. The
    # selected shape is then fitted to the text instead of filling the entire
    # emoji canvas with a large tile.
    max_text_w = large_size * 0.82
    max_text_h = large_size * 0.42
    font_size = int(large_size * 0.50)
    stroke_width = max(1, large_size // 260)
    chosen_font: ImageFont.ImageFont = _font(style.font, max(10, font_size))
    bounds = draw.textbbox((0, 0), text, font=chosen_font, stroke_width=stroke_width)
    while font_size > max(10, int(large_size * 0.12)):
        chosen_font = _font(style.font, font_size)
        bounds = draw.textbbox((0, 0), text, font=chosen_font, stroke_width=stroke_width)
        if (bounds[2] - bounds[0]) <= max_text_w and (bounds[3] - bounds[1]) <= max_text_h:
            break
        font_size -= 2

    text_w = max(1, bounds[2] - bounds[0])
    text_h = max(1, bounds[3] - bounds[1])
    pad_x = max(large_size * 0.045, text_h * 0.34)
    pad_y = max(large_size * 0.025, text_h * 0.22)
    box_w = min(large_size * 0.94, text_w + pad_x * 2)
    box_h = min(large_size * 0.78, text_h + pad_y * 2)
    box = (
        int(center - box_w / 2),
        int(center - box_h / 2),
        int(center + box_w / 2),
        int(center + box_h / 2),
    )
    _draw_shape(draw, style.shape, box, fill)

    brightness = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255
    if style.shape == "None":
        text_color = (rgb[0], rgb[1], rgb[2], 255)
    elif brightness >= 0.62:
        text_color = (16, 16, 16, 255)
    else:
        text_color = (255, 255, 255, 255)

    # A restrained shadow keeps the word readable without the heavy outline
    # used by the previous square-tile renderer.
    draw.text(
        (center + large_size * 0.006, center + large_size * 0.012),
        text,
        font=chosen_font,
        anchor="mm",
        fill=(0, 0, 0, 70),
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
    if style.animation == "Pulse":
        factor = 0.92 + 0.08 * (0.5 + 0.5 * math.sin(phase))
    elif style.animation == "Bounce":
        dy = -large_size * 0.045 * abs(math.sin(phase))
    elif style.animation == "Wave":
        angle = 7.0 * math.sin(phase)
    elif style.animation == "Float":
        dy = -large_size * 0.045 * math.sin(phase)
    elif style.animation == "Shake":
        dx = large_size * 0.035 * math.sin(phase * 3.0)
    elif style.animation == "Tilt":
        angle = 11.0 * math.sin(phase)

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


def render_preview_gif(style: Style) -> bytes:
    """Render the same text treatment in the wide, white preview used by the reference bot."""
    width, height = 660, 220
    sticker_size = 220
    frames: list[Image.Image] = []
    for index in range(15):
        sticker_frame = render_frame(
            style,
            time_seconds=index * (DURATION_SECONDS / 15),
            size=sticker_size,
        )
        # The source bot shows a wide white GIF canvas, with the compact text
        # sticker centered rather than a square tile filling the whole preview.
        canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
        canvas.alpha_composite(sticker_frame, ((width - sticker_size) // 2, (height - sticker_size) // 2))
        frames.append(canvas.convert("RGB"))

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

def _ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        executable = shutil.which("ffmpeg")
        if executable:
            return executable
        raise RuntimeError("FFmpeg is not available. Ensure imageio-ffmpeg is installed.")


def _encode_webm(frames: Iterable[Image.Image]) -> bytes:
    executable = _ffmpeg_executable()
    with tempfile.TemporaryDirectory(prefix="animated-emoji-") as temp_dir:
        raw_path = Path(temp_dir) / "frames.rgba"
        output_path = Path(temp_dir) / "emoji.webm"
        with raw_path.open("wb") as raw_file:
            for frame in frames:
                if frame.size != (CANVAS_SIZE, CANVAS_SIZE):
                    frame = frame.resize((CANVAS_SIZE, CANVAS_SIZE), Image.Resampling.LANCZOS)
                raw_file.write(frame.convert("RGBA").tobytes())
        command = [
            executable, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-video_size", f"{CANVAS_SIZE}x{CANVAS_SIZE}",
            "-framerate", str(FPS), "-i", str(raw_path), "-an", "-frames:v", str(FRAME_COUNT),
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
            "-deadline", "good", "-cpu-used", "5", "-b:v", "110k", "-crf", "35",
            "-metadata:s:v:0", "alpha_mode=1", "-f", "webm", str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
        if result.returncode != 0 or not output_path.exists():
            details = (result.stderr or "FFmpeg did not produce an output file")[-1200:]
            raise RuntimeError(f"Could not render the animated emoji: {details}")
        data = output_path.read_bytes()
    if not data:
        raise RuntimeError("The animation encoder returned an empty file.")
    if len(data) > MAX_WEBM_BYTES:
        raise RuntimeError("The rendered animation exceeds Telegram's 256 KB video-emoji limit.")
    return data


def render_pack_webm(style: Style) -> list[bytes]:
    base_hex = PALETTES[style.normalized().color]
    variants = [base_hex] + [_variant_hex(base_hex, index) for index in range(1, 6)]
    pack: list[bytes] = []
    for override in variants:
        frames = (
            render_frame(style, time_seconds=index / FPS, size=CANVAS_SIZE, color_override=override)
            for index in range(FRAME_COUNT)
        )
        pack.append(_encode_webm(frames))
    return pack
