import gzip
import json

import pytest

from emoji_engine import (
    ANIMATION_OPTIONS, CANVAS, END_FRAME, FPS, MAX_TGS_BYTES, SHAPE_OPTIONS,
    FONT_OPTIONS, Style, build_tgs, render_preview_gif, validate_tgs,
)


def test_style_normalization():
    assert Style("  Hello   World ").normalized().text == "Hello World"
    assert Style("Hello", shape="unknown").normalized().shape == SHAPE_OPTIONS[0]
    assert Style("Hello", animation="unknown").normalized().animation == ANIMATION_OPTIONS[0]


def test_tgs_envelope_and_limits():
    data = build_tgs(Style("Hello"))
    meta = validate_tgs(data)
    doc = json.loads(gzip.decompress(data).decode("utf-8"))
    assert meta["width"] == CANVAS and meta["height"] == CANVAS
    assert meta["fps"] == FPS == 60
    assert meta["frames"] <= END_FRAME == 180
    assert len(data) < MAX_TGS_BYTES
    assert doc["layers"]


@pytest.mark.parametrize("animation", ANIMATION_OPTIONS)
def test_all_motion_states_generate(animation):
    validate_tgs(build_tgs(Style("Motion", animation=animation)))


@pytest.mark.parametrize("shape", SHAPE_OPTIONS)
def test_all_shape_states_generate(shape):
    validate_tgs(build_tgs(Style("Emoji", shape=shape)))


def test_preview_is_animated_gif():
    data = render_preview_gif(Style("Hello"), frame_count=8, size=128)
    assert data[:6] in (b"GIF87a", b"GIF89a")
    assert len(data) > 100


def test_longest_supported_text_stays_under_tgs_limit_for_all_fonts():
    for font_name in FONT_OPTIONS:
        data = build_tgs(Style("ABCDEFGHIJKLMNOPQRST", font=font_name))
        assert validate_tgs(data)["bytes"] < MAX_TGS_BYTES


def test_three_variants_are_valid_and_not_identical():
    from emoji_engine import generate_pack
    assets = generate_pack(Style("Hola"))
    assert len(assets) == 3
    assert all(validate_tgs(item)["bytes"] < MAX_TGS_BYTES for item in assets)
    assert len({hash(item) for item in assets}) == 3


def test_glyph_layers_are_positioned_within_the_canvas():
    data = build_tgs(Style("HELLO"))
    doc = json.loads(gzip.decompress(data).decode("utf-8"))
    glyph_layers = [layer for layer in doc["layers"] if layer["nm"].startswith("Glyph ")]
    assert glyph_layers
    x_positions = [layer["ks"]["p"]["k"][0]["s"][0] for layer in glyph_layers]
    assert min(x_positions) > 0
    assert max(x_positions) < CANVAS
    assert max(x_positions) - min(x_positions) < CANVAS * 0.8
