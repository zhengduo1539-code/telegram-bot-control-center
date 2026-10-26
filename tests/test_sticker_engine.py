from sticker_engine import ANIMATION_OPTIONS, FONT_OPTIONS, PALETTES, SHAPE_OPTIONS, Style, render_frame, render_preview_png


def test_style_normalization_clamps_text_and_options():
    style = Style(text="   Hello    World  " + "!" * 30, font="bad", color="bad", shape="bad", animation="bad").normalized()
    assert style.text == "Hello World !!!!!!!!"
    assert style.font == FONT_OPTIONS[0]
    assert style.color in PALETTES
    assert style.shape == SHAPE_OPTIONS[0]
    assert style.animation == ANIMATION_OPTIONS[0]


def test_frame_is_transparent_rgba_and_sized_correctly():
    frame = render_frame(Style(text="Hello"), time_seconds=0.25, size=100)
    assert frame.size == (100, 100)
    assert frame.mode == "RGBA"
    assert frame.getchannel("A").getbbox() is not None


def test_preview_is_png():
    content = render_preview_png(Style(text="Hello", color="Orange", shape="Heart", animation="Bounce"))
    assert content.startswith(b"\x89PNG\r\n\x1a\n")


def test_video_pack_has_three_telegram_compatible_webm_files():
    from sticker_engine import MAX_WEBM_BYTES, render_pack_webm

    pack = render_pack_webm(Style(text="Test", color="Cyan", animation="Float"))
    assert len(pack) == 3
    assert all(item.startswith(b"\x1aE\xdf\xa3") for item in pack)
    assert all(0 < len(item) <= MAX_WEBM_BYTES for item in pack)
