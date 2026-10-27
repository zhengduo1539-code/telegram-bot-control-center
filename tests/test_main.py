import asyncio
import time

from emoji_engine import Style
from main import BotService, EditorSession


def test_editor_keyboard_has_custom_emoji_action_and_inline_selectors():
    service = BotService()
    session = EditorSession(
        token="abc12345",
        owner_id=123,
        chat_id=123,
        message_id=50,
        style=Style("Hello").normalized(),
        created_at=time.monotonic(),
        lock=asyncio.Lock(),
    )
    keyboard = service._keyboard(session)

    assert len(keyboard.inline_keyboard) == 5
    assert keyboard.inline_keyboard[-1][0].text == "✅ Create Custom Emoji Pack"
    assert keyboard.inline_keyboard[-1][0].callback_data == "e:abc12345:create:0"
    assert keyboard.inline_keyboard[0][1].text == "Raleway Italic"
    assert keyboard.inline_keyboard[1][1].text == "🟠"
    assert keyboard.inline_keyboard[2][1].text == "🎟️"
    assert keyboard.inline_keyboard[3][1].text == "❤️"
