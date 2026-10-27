from io import BytesIO

from PIL import Image, ImageFont

from sticker_engine import (
    ANIMATION_OPTIONS,
    CANVAS_SIZE,
    CUSTOM_EMOJI_COUNT,
    CUSTOM_EMOJI_SIZE,
    DURATION_SECONDS,
    FPS,
    FONT_OPTIONS,
    PALETTES,
    SHAPE_OPTIONS,
    Style,
    render_frame,
    render_loading_gif,
    render_preview_gif,
    render_preview_png,
    _font,
)


def test_default_style_uses_the_first_observed_visual_presets():
    style = Style(text="Hello").normalized()
    assert style.font == "Raleway italic"
    assert style.color == "Orange"
    assert style.shape == "shape_0"
    assert style.animation == "anim_0"
    assert SHAPE_OPTIONS == ("shape_0", "shape_1", "shape_2")
    assert ANIMATION_OPTIONS == ("anim_0", "anim_1", "anim_2")


def test_known_legacy_style_names_migrate_but_unverified_names_fall_back_safely():
    assert Style(text="Hi", shape="Pill", animation="Wave").normalized().shape == "shape_0"
    assert Style(text="Hi", shape="Circle", animation="None").normalized().animation == "anim_1"
    assert Style(text="Hi", shape="Heart", animation="Float").normalized().animation == "anim_2"
    # Removed presets had no verified matching state in the reference recording.
    normalized = Style(text="Hi", shape="Star", animation="Pulse").normalized()
    assert normalized.shape == "shape_0"
    assert normalized.animation == "anim_0"


def test_reference_font_options_load_the_intended_typefaces():
    expected_families = {
        "Raleway italic": "raleway",
        "Arial": "arimo",
        "Roboto": "roboto",
        "Madefor": "montserrat",
        "Rubik": "rubik",
    }
    for option, expected in expected_families.items():
        font = _font(option, 36)
        assert isinstance(font, ImageFont.FreeTypeFont)
        family_name, _style_name = font.getname()
        assert expected in family_name.lower()


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
    content = render_preview_png(Style(text="Hello", color="Orange", shape="shape_2", animation="anim_2"))
    assert content.startswith(b"\x89PNG\r\n\x1a\n")


def test_preview_is_wide_white_looping_gif_with_full_width_artwork():
    content = render_preview_gif(Style(text="Hello", color="Orange", shape="shape_0", animation="anim_0"))
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
        artwork_width = max(colored_x) - min(colored_x) if colored_x else 0
        # The original red-orange pill is compact: approximately a quarter
        # of the wide preview, not a full-width banner or a tiny square inset.
        assert 140 <= artwork_width <= 240


def test_custom_emoji_pack_keeps_the_full_text_in_all_three_variants(monkeypatch):
    import sticker_engine

    rendered = []

    def fake_render(style, color_override=None):
        rendered.append((style.text, color_override))
        return f"webm-{len(rendered)}".encode()

    monkeypatch.setattr(sticker_engine, "render_single_custom_emoji_webm", fake_render)
    pack = sticker_engine.render_pack_webm(Style(text="Hello", color="Cyan"))

    assert len(pack) == CUSTOM_EMOJI_COUNT == 3
    assert [text for text, _color in rendered] == ["Hello", "Hello", "Hello"]
    assert len({color for _text, color in rendered}) == 3


def test_video_pack_uses_three_100px_custom_emoji_webm_files():
    from sticker_engine import MAX_WEBM_BYTES, render_pack_webm

    assert CANVAS_SIZE == 512  # standalone preview media remains sticker-sized
    assert CUSTOM_EMOJI_SIZE == 100
    assert FPS == 30
    assert DURATION_SECONDS == 2
    pack = render_pack_webm(Style(text="Test", color="Cyan", animation="anim_2"))
    assert len(pack) == CUSTOM_EMOJI_COUNT == 3
    assert all(item.startswith(b"\x1aE\xdf\xa3") for item in pack)
    assert all(0 < len(item) <= MAX_WEBM_BYTES for item in pack)


def test_rainbow_palette_is_available():
    assert "Rainbow" in PALETTES
    preview = render_preview_gif(Style(text="Hi", color="Rainbow"))
    assert preview.startswith((b"GIF87a", b"GIF89a"))


def test_loading_preview_is_a_centered_looping_spinner_on_white_card():
    content = render_loading_gif(Style(text="Hello", color="Orange"))
    assert content.startswith((b"GIF87a", b"GIF89a"))
    with Image.open(BytesIO(content)) as image:
        assert image.size == (660, 220)
        assert image.n_frames >= 12
        image.seek(0)
        first = image.convert("RGB")
        assert first.getpixel((0, 0)) == (255, 255, 255)
        image.seek(image.n_frames // 2)
        second = image.convert("RGB")
        assert list(first.getdata()) != list(second.getdata())
