"""Verify the Render ASGI entry point remains importable."""
import main


def test_render_asgi_entrypoint_is_callable():
    assert callable(main.app)
