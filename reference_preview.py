"""Reference-faithful wide animated preview used by @EmojiTitleBot."""
from __future__ import annotations

import io
import math
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 660, 220
DURATION_SECONDS = 2.0
_GRADIENTS = {
    "Orange": ("#F51E11", "#FF9D19"),
    "Pink": ("#E91E79", "#FF6CAF"),
    "Lime": ("#7ED80D", "#D9FF3B"),
    "Violet": ("#743AF0", "#D05BFF"),
    "Cyan": ("#00B6D8", "#52E4CC"),
    "Gold": ("#FFAD22", "#FFE65A"),
}
_FONTS = {
    "Raleway italic": (
        "/usr/share/fonts/truetype/raleway/Raleway-Italic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Italic.ttf",
    ),
    "Arial": (
        "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    "Rubik": (
        "/usr/share/fonts/truetype/rubik/Rubik-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ),
    "Monaco": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    ),
    "Impact": (
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    "Condensed Bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSansNarrow-Bold.ttf",
    ),
}


def _font(name: str, size: int) -> ImageFont.ImageFont:
    for candidate in _FONTS.get(name, _FONTS["Raleway italic"]):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] * (1 - t) + b[i] * t) for i in range(3))


def _mask_shape(shape: str, box: tuple[int, int, int, int], phase: float) -> Image.Image:
    mask = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(mask)
    left, top, right, bottom = box
    w, h = right - left, bottom - top
    if shape == "None":
        return mask
    if shape == "Circle":
        draw.ellipse(box, fill=255)
    elif shape == "Rounded":
        draw.rounded_rectangle(box, radius=int(h * .25), fill=255)
    elif shape == "Star":
        cx, cy = (left + right) / 2, (top + bottom) / 2
        points = []
        for i in range(10):
            radius = min(w, h) * (.49 if i % 2 == 0 else .22)
            angle = -math.pi / 2 + i * math.pi / 5
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        draw.polygon(points, fill=255)
    elif shape == "Heart":
        cx, cy = (left + right) / 2, (top + bottom) / 2
        points = []
        for i in range(96):
            t = 2 * math.pi * i / 96
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2*t) - 2 * math.cos(3*t) - math.cos(4*t)
            points.append((cx + x * w / 32, cy - y * h / 30))
        draw.polygon(points, fill=255)
    else:  # Pill: slightly irregular capsule, as in the original preview
        r = h / 2
        span = max(1, int(w - h))
        pts = []
        for i in range(span + 1):
            ratio = i / span
            x = left + r + (w - h) * ratio
            wobble = math.sin(2 * math.pi * ratio * 1.65 + phase) * h * .055
            wobble += math.sin(2 * math.pi * ratio * 2.8 - phase * .7) * h * .025
            pts.append((x, top + wobble))
        for i in range(1, 33):
            a = -math.pi/2 + math.pi*i/32
            rr = r * (1 + .015*math.sin(phase + i*.2))
            pts.append((right-r + rr*math.cos(a), (top+bottom)/2 + rr*math.sin(a)))
        for i in range(span, -1, -1):
            ratio = i / span
            x = left + r + (w - h) * ratio
            wobble = math.sin(2 * math.pi * ratio * 1.65 + phase + math.pi) * h * .055
            wobble += math.sin(2 * math.pi * ratio * 2.8 - phase * .7 + math.pi) * h * .025
            pts.append((x, bottom + wobble))
        for i in range(1, 33):
            a = math.pi/2 + math.pi*i/32
            rr = r * (1 + .015*math.sin(phase + i*.2))
            pts.append((left+r + rr*math.cos(a), (top+bottom)/2 + rr*math.sin(a)))
        draw.polygon(pts, fill=255)
    return mask


def render_preview_gif(style) -> bytes:
    """Render the source bot's wide white canvas, gradient capsule, and live effect."""
    text = style.text or " "
    font_size = 80
    font = _font(style.font, font_size)
    temp = Image.new("RGB", (1, 1), "white")
    measure = ImageDraw.Draw(temp)
    while font_size > 24:
        bounds = measure.textbbox((0, 0), text, font=font)
        if bounds[2] - bounds[0] < 245 and bounds[3] - bounds[1] < 86:
            break
        font_size -= 2
        font = _font(style.font, font_size)
    bounds = measure.textbbox((0, 0), text, font=font)
    text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    # Original badge has generous horizontal padding and sits centered in a
    # white 3:1 preview, with a white word and a red-to-orange wavy capsule.
    badge_w = min(500, max(300, text_width + 170))
    badge_h = min(82, max(62, text_height + 20))
    cx, cy = WIDTH / 2, HEIGHT / 2
    first_hex, second_hex = _GRADIENTS.get(style.color, _GRADIENTS["Orange"])
    first, second = _rgb(first_hex), _rgb(second_hex)
    frames = []
    for frame_index in range(15):
        t = frame_index * DURATION_SECONDS / 15
        phase = 2 * math.pi * t / DURATION_SECONDS
        motion = style.animation
        pulse, dx, dy, angle = 1.0, 0.0, 0.0, 0.0
        if motion == "Pulse":
            pulse = .965 + .035 * (0.5 + .5 * math.sin(phase))
        elif motion == "Bounce":
            dy = -5 * abs(math.sin(phase))
        elif motion == "Wave":
            dx = 1.5 * math.sin(phase)
        elif motion == "Float":
            dy = 4 * math.sin(phase)
        elif motion == "Shake":
            dx = 3 * math.sin(phase * 3)
        elif motion == "Tilt":
            angle = 3 * math.sin(phase)
        elif motion == "None":
            pass
        bw, bh = badge_w * pulse, badge_h * pulse
        box = (int(cx-bw/2), int(cy-bh/2), int(cx+bw/2), int(cy+bh/2))
        canvas = Image.new("RGBA", (WIDTH, HEIGHT), (255, 255, 255, 255))
        if style.shape != "None":
            mask = _mask_shape(style.shape, box, phase if motion == "Wave" else 0.0)
            gradient = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            gd = ImageDraw.Draw(gradient)
            for x in range(WIDTH):
                rgb = _blend(first, second, x / (WIDTH - 1))
                gd.line((x, 0, x, HEIGHT), fill=(*rgb, 255))
            gradient.putalpha(mask)
            canvas.alpha_composite(gradient)
        draw = ImageDraw.Draw(canvas)
        tx = cx + dx
        ty = cy + dy
        if angle:
            # Render glyph into a transparent layer for gentle whole-word tilt.
            glyph = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            ImageDraw.Draw(glyph).text((cx, cy), text, font=font, anchor="mm", fill="white")
            glyph = glyph.rotate(angle, resample=Image.Resampling.BICUBIC, center=(cx, cy))
            canvas.alpha_composite(glyph)
        else:
            draw.text((tx, ty), text, font=font, anchor="mm", fill="#FFFFFF")
        frames.append(canvas.convert("RGB"))
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                   duration=round(DURATION_SECONDS*1000/len(frames)),
                   loop=0, disposal=2, optimize=True)
    result = out.getvalue()
    if not result.startswith((b"GIF87a", b"GIF89a")):
        raise RuntimeError("Could not render the animated preview.")
    return result
