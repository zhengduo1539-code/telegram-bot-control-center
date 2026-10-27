import gzip
import json

import pytest

from app.analysis.animation import AnimationError, _load_tgs
from app.analysis.template_rebuild import rebuild_tgs_text_from_template


def _source_tgs(with_text_layer=False):
    transform = {
        "p": {
            "a": 1,
            "k": [
                {"t": 0, "s": [240, 256, 0], "e": [260, 256, 0], "i": {"x": 0.5, "y": 1}, "o": {"x": 0.5, "y": 0}, "to": [0, 0, 0], "ti": [0, 0, 0]},
                {"t": 20, "s": [260, 256, 0]},
            ],
        },
        "s": {
            "a": 1,
            "k": [
                {"t": 0, "s": [80, 80, 100], "e": [100, 100, 100], "i": {"x": [0.5, 0.5, 0.5], "y": [1, 1, 1]}, "o": {"x": [0.5, 0.5, 0.5], "y": [0, 0, 0]}},
                {"t": 20, "s": [100, 100, 100]},
            ],
        },
        "r": {
            "a": 1,
            "k": [
                {"t": 0, "s": [-8], "e": [0], "i": {"x": [0.5], "y": [1]}, "o": {"x": [0.5], "y": [0]}},
                {"t": 20, "s": [0]},
            ],
        },
        "o": {"a": 0, "k": 100},
        "a": {"a": 0, "k": [0, 0, 0]},
    }
    shape_layer = {
        "ddd": 0,
        "ind": 1,
        "ty": 4,
        "nm": "sample animated object",
        "sr": 1,
        "ks": transform,
        "ao": 0,
        "shapes": [
            {
                "ty": "gr",
                "nm": "source color",
                "it": [
                    {
                        "ty": "sh",
                        "nm": "path",
                        "d": 1,
                        "ks": {
                            "a": 0,
                            "k": {
                                "i": [[0, 0]] * 4,
                                "o": [[0, 0]] * 4,
                                "v": [[0, 0], [10, 0], [10, 10], [0, 10]],
                                "c": True,
                            },
                        },
                    },
                    {"ty": "fl", "nm": "Fill", "c": {"a": 0, "k": [1, 0, 0, 1]}, "o": {"a": 0, "k": 100}, "r": 1, "bm": 0},
                    {"ty": "tr", "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": 0}, "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0}},
                ],
            }
        ],
        "ip": 0,
        "op": 180,
        "st": 0,
        "bm": 0,
    }
    if with_text_layer:
        shape_layer = {
            "ddd": 0,
            "ind": 1,
            "ty": 5,
            "nm": "Editable text",
            "ks": transform,
            "t": {
                "d": {
                    "k": [
                        {"t": 0, "s": {"t": "WELCOME", "s": 48, "f": "Arial", "fc": [1, 0.4, 0.1]}},
                        {"t": 90, "s": {"t": "WELCOME", "s": 48, "f": "Arial", "fc": [1, 0.4, 0.1]}},
                    ]
                },
                "a": [],
            },
            "ip": 0,
            "op": 180,
            "st": 0,
            "bm": 0,
        }
    document = {
        "tgs": 1,
        "v": "5.7.4",
        "fr": 60,
        "ip": 0,
        "op": 180,
        "w": 512,
        "h": 512,
        "nm": "template source",
        "ddd": 0,
        "assets": [],
        "layers": [shape_layer],
    }
    return gzip.compress(json.dumps(document, separators=(",", ":")).encode("utf-8"), mtime=0)


def test_rebuild_text_from_shape_template_builds_vector_glyphs_and_adapts_motion():
    rebuilt, mode = rebuild_tgs_text_from_template(_source_tgs(), "HOWAREYOU")
    root = _load_tgs(rebuilt)
    assert mode == "motion_style_reconstruction"
    assert len(root["layers"]) == len("HOWAREYOU")
    assert root["assets"] == []
    assert root["w"] == root["h"] == 512
    assert root["fr"] == 60
    assert (root["op"] - root["ip"]) / root["fr"] == 3
    for layer in root["layers"]:
        assert layer["ty"] == 4
        assert any(shape["ty"] == "sh" for shape in layer["shapes"])
        fill = next(shape for shape in layer["shapes"] if shape["ty"] == "fl")
        assert fill["c"]["k"][:3] == [1, 0, 0]
        assert layer["ks"]["r"]["a"] == 1
        assert [frame["t"] for frame in layer["ks"]["r"]["k"]] == [0, 20]


def test_editable_text_layer_uses_exact_replacement_mode():
    rebuilt, mode = rebuild_tgs_text_from_template(_source_tgs(with_text_layer=True), "HOWAREYOU")
    root = _load_tgs(rebuilt)
    values = [frame["s"]["t"] for frame in root["layers"][0]["t"]["d"]["k"]]
    assert mode == "exact_text_layer"
    assert values == ["HOWAREYOU", "HOWAREYOU"]
    assert root["layers"][0]["ks"]["r"]["k"][0]["s"] == [-8]


def test_rebuild_rejects_too_long_text():
    with pytest.raises(AnimationError, match="20 Unicode characters"):
        rebuild_tgs_text_from_template(_source_tgs(), "A" * 21)


def test_rebuild_rejects_unsupported_unicode_outline(monkeypatch):
    from app.analysis import template_rebuild

    monkeypatch.setattr(template_rebuild, "_font_paths", lambda: [])
    with pytest.raises(AnimationError, match="No installed outline font supports"):
        rebuild_tgs_text_from_template(_source_tgs(), "中")
