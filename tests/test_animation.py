import gzip
import json

import pytest

from app.analysis.animation import (
    AnimationError,
    analyze_animation,
    change_tgs_speed,
    recolor_tgs,
    replace_text_tgs,
)


def make_tgs():
    root = {
        "v": "5.7.4",
        "fr": 60,
        "ip": 0,
        "op": 60,
        "w": 512,
        "h": 512,
        "nm": "synthetic-test",
        "layers": [
            {
                "ddd": 0,
                "ind": 1,
                "ty": 4,
                "nm": "shape layer",
                "ks": {},
                "ao": 0,
                "shapes": [
                    {"ty": "fl", "c": {"a": 0, "k": [1, 0, 0, 1]}, "o": {"a": 0, "k": 100}},
                    {
                        "ty": "sh",
                        "ks": {
                            "a": 1,
                            "k": [
                                {
                                    "t": 30,
                                    "s": [{"c": 0}],
                                    "e": [{"c": 1}],
                                    "i": {"x": 0.5, "y": 1},
                                    "o": {"x": 0.5, "y": 0},
                                }
                            ],
                        },
                    },
                ],
            }
        ],
    }
    return gzip.compress(json.dumps(root, separators=(",", ":")).encode(), mtime=0)


def decode_tgs(data):
    return json.loads(gzip.decompress(data))


def test_analyze_tgs_extracts_motion_structure_and_colors():
    report = analyze_animation(make_tgs(), "tgs")
    assert report["format"] == "TGS"
    assert report["width"] == 512
    assert report["height"] == 512
    assert report["frame_rate"] == 60
    assert report["duration_seconds"] == 1
    assert report["layer_count"] == 1
    assert report["shape_types"]["fl"] == 1
    assert report["keyframe_count"] == 1
    assert "#FF0000" in report["colors"]
    assert report["editable"] is True


def test_recolor_tgs_rewrites_solid_fill_and_preserves_format():
    rebuilt = recolor_tgs(make_tgs(), "#00FF00")
    report = analyze_animation(rebuilt, "tgs")
    assert "#00FF00" in report["colors"]
    assert report["width"] == 512
    root = decode_tgs(rebuilt)
    fill = root["layers"][0]["shapes"][0]
    assert fill["c"]["k"][:3] == [0.0, 1.0, 0.0]


def test_speed_adjustment_scales_timeline_and_keeps_60_fps():
    rebuilt = change_tgs_speed(make_tgs(), 2.0)
    root = decode_tgs(rebuilt)
    assert root["fr"] == 60
    assert root["op"] == 30
    assert root["layers"][0]["shapes"][1]["ks"]["k"][0]["t"] == 15
    assert analyze_animation(rebuilt, "tgs")["duration_seconds"] == 0.5


@pytest.mark.parametrize("value", ["red", "#12", "#GG0000", "12345", "#12345678"])
def test_recolor_rejects_invalid_hex(value):
    with pytest.raises(AnimationError):
        recolor_tgs(make_tgs(), value)


def test_speed_rejects_invalid_multiplier():
    with pytest.raises(AnimationError):
        change_tgs_speed(make_tgs(), 3.0)


def test_unsupported_format_is_rejected():
    with pytest.raises(AnimationError):
        analyze_animation(b"not an animation")



def make_text_tgs(text="WELCOME"):
    root = {
        "v": "5.7.4",
        "fr": 60,
        "ip": 0,
        "op": 180,
        "w": 512,
        "h": 512,
        "nm": "synthetic-text-test",
        "layers": [
            {
                "ddd": 0,
                "ind": 1,
                "ty": 5,
                "nm": "editable headline",
                "ks": {"o": {"a": 1, "k": [{"t": 0, "s": [100], "e": [50], "i": {"x": 0.5, "y": 1}, "o": {"x": 0.5, "y": 0}}, {"t": 90, "s": [50]}]}},
                "t": {
                    "d": {
                        "k": [
                            {"t": 0, "s": {"t": text, "s": 36, "f": "Arial", "fc": [1, 0.5, 0]}},
                            {"t": 90, "s": {"t": text, "s": 36, "f": "Arial", "fc": [1, 0.5, 0]}},
                        ]
                    },
                    "a": [],
                },
            }
        ],
    }
    return gzip.compress(json.dumps(root, separators=(",", ":")).encode(), mtime=0)


def test_replace_text_preserves_text_styling_and_animation_timeline():
    original = make_text_tgs()
    rebuilt = replace_text_tgs(original, "HOWAREYOU")
    root = decode_tgs(rebuilt)
    layer = root["layers"][0]
    assert [keyframe["s"]["t"] for keyframe in layer["t"]["d"]["k"]] == ["HOWAREYOU", "HOWAREYOU"]
    assert [keyframe["t"] for keyframe in layer["t"]["d"]["k"]] == [0, 90]
    assert layer["t"]["d"]["k"][0]["s"]["s"] == 36
    assert layer["t"]["d"]["k"][0]["s"]["f"] == "Arial"
    assert layer["ks"]["o"]["k"][0]["e"] == [50]
    report = analyze_animation(rebuilt, "tgs")
    assert report["text_replace_supported"] is True
    assert report["text_contents"] == ["HOWAREYOU"]


@pytest.mark.parametrize("value", ["", " ", "A" * 21, "HELLO\nWORLD"])
def test_replace_text_rejects_empty_oversized_or_control_text(value):
    with pytest.raises(AnimationError):
        replace_text_tgs(make_text_tgs(), value)


def test_replace_text_rejects_vector_only_animation():
    with pytest.raises(AnimationError, match="no editable Lottie text layer"):
        replace_text_tgs(make_tgs(), "HOWAREYOU")


def test_replace_text_rejects_multiple_editable_layers():
    root = decode_tgs(make_text_tgs())
    second = json.loads(json.dumps(root["layers"][0]))
    second["ind"] = 2
    root["layers"].append(second)
    source = gzip.compress(json.dumps(root, separators=(",", ":")).encode(), mtime=0)
    with pytest.raises(AnimationError, match="contains 2 editable text layers"):
        replace_text_tgs(source, "HOWAREYOU")
