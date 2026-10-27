import gzip
import json

import pytest

from app.analysis.animation import AnimationError, analyze_animation


def test_gzip_lottie_document_with_invalid_root_is_rejected():
    raw = gzip.compress(json.dumps(["not", "a", "lottie", "object"]).encode())
    with pytest.raises(AnimationError):
        analyze_animation(raw, "tgs")
