from io import BytesIO

from PIL import Image

from sticker_engine import (
    ANIMATION_OPTIONS,
    FONT_OPTIONS,
    PALETTES,
    SHAPE_OPTIONS,
    Style,
    render_frame,
    render_preview_gif,
    render_preview_png,
)


def test_reference_default_style_uses_raleway_orange_pill_and_wave():
    style = Style(text="Hello").normalized()
    assert style.font == "Raleway italic"
    assert style.color == "Orange"
    assert style.shape == "Pill"
    assert style.animation == "Wave"


def test_style_normalization_clamps_text_and_options():
    style = Style(
        text="   Hello    World  " + "!" * 30,
        font="bad",
        color="bad",
        shape="bad",
        animation="bad",
    ).normalized()
    assert style.text == "Hello World !!!!!!!!"
    assert style.font == FONT_OPTIONS[0]
    assert style.color in PALETTES
    assert style.shape == SHAPE_OPTIONS[0]
    assert style.animation == ANIMATION_OPTIONS[0]


def test_frame_is_transparent_and_text_fitted_instead_of_full_canvas_tile():
    frame = render_frame(Style(text="Hello"), time_seconds=0.25, size=100)
    assert frame.size == (100, 100)
    assert frame.mode == "RGBA"
    assert frame.getchannel("A").getbbox() is not None
    assert frame.getpixel((0, 0))[3] == 0
    # The rendered graphic should leave transparent canvas around the word.
    alpha_bbox = frame.getchannel("A").getbbox()
    assert alpha_bbox is not None
    assert alpha_bbox[0] > 0 and alpha_bbox[1] > 0
    assert alpha_bbox[2] < 100 and alpha_bbox[3] < 100


def test_preview_is_png():
    content = render_preview_png(Style(text="Hello", color="Orange", shape="Heart", animation="Bounce"))
    assert content.startswith(b"\x89PNG\r\n\x1a\n")


def test_preview_is_wide_white_looping_gif_with_full_width_artwork():
    content = render_preview_gif(Style(text="Hello", color="Orange", shape="Pill", animation="Wave"))
    assert content.startswith((b"GIF87a", b"GIF89a"))
    assert len(content) > 100
    with Image.open(BytesIO(content)) as image:
        assert image.width == 660
        assert image.height == 220
        image_rgb = image.convert("RGB")
        assert image_rgb.getpixel((0, 0)) == (255, 255, 255)
        pixels = image_rgb.load()
        colored_x = [
            x for x in range(image.width)
            if any(pixels[x, y] != (255, 255, 255) for y in range(50, 171))
        ]
        assert colored_x and max(colored_x) - min(colored_x) > 260


def test_video_pack_has_six_telegram_compatible_webm_files():
    from sticker_engine import MAX_WEBM_BYTES, render_pack_webm

    pack = render_pack_webm(Style(text="Test", color="Cyan", animation="Float"))
    assert len(pack) == 6
    assert all(item.startswith(b"\x1aE\xdf\xa3") for item in pack)
    assert all(0 < len(item) <= MAX_WEBM_BYTES for item in pack)
