"""Regression tests for Schedule New Post onboarding and navigation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from handlers import schedule_wizard as wizard
from localization import LANGUAGES
from schedule_wizard_localization import _MESSAGES, translate_schedule_wizard


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_schedule_wizard_onboarding_catalog_covers_all_supported_languages():
    assert set(_MESSAGES) == set(LANGUAGES)
    for language, catalog in _MESSAGES.items():
        assert set(catalog) == {"intro", "continue"}, language
        assert all(isinstance(value, str) and value.strip() for value in catalog.values())
    assert "confirm" in translate_schedule_wizard("en", "intro").lower()
    assert translate_schedule_wizard("unknown", "continue") == _MESSAGES["en"]["continue"]


def test_intro_keyboard_has_cancel_and_continue_actions():
    buttons = _buttons(wizard._wizard_intro_keyboard("my"))
    assert len(buttons) == 2
    assert buttons[0].callback_data == wizard.CB_CANCEL
    assert buttons[1].callback_data == wizard.CB_WIZARD_CONTINUE
    assert buttons[1].text == translate_schedule_wizard("my", "continue")


def test_schedule_new_post_entry_shows_intro_before_setup(monkeypatch):
    async def scenario():
        monkeypatch.setattr(wizard, "resolve_user_language", AsyncMock(return_value="my"))
        monkeypatch.setattr(wizard.chat_cleanup, "reset", lambda *_: None)
        monkeypatch.setattr(wizard.chat_cleanup, "track", lambda *_: None)
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat_id=42, message_id=88),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={},
            application=SimpleNamespace(bot_data={}),
        )
        state = await wizard.enter_wizard(
            SimpleNamespace(callback_query=query), context
        )
        assert state == wizard.WIZARD_INTRO
        assert query.edit_message_text.await_args.args[0] == translate_schedule_wizard("my", "intro")
        assert context.user_data["wizard"] == {}
        callbacks = [button.callback_data for button in _buttons(
            query.edit_message_text.await_args.kwargs["reply_markup"]
        )]
        assert callbacks == [wizard.CB_CANCEL, wizard.CB_WIZARD_CONTINUE]

    asyncio.run(scenario())


def test_continue_opens_existing_destination_picker(monkeypatch):
    async def scenario():
        monkeypatch.setattr(wizard, "resolve_user_language", AsyncMock(return_value="en"))
        monkeypatch.setattr(wizard, "list_groups_for_user", AsyncMock(return_value=[
            {"chat_id": -1001234567890, "title": "Announcements", "chat_type": "channel"}
        ]))
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="ScheduleBot")))
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat_id=42, message_id=88),
            get_bot=lambda: bot,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"wizard": {}}, bot=bot)
        state = await wizard.cb_wizard_continue(
            SimpleNamespace(callback_query=query), context
        )
        assert state == wizard.WIZARD_CHAT_ID
        text = query.edit_message_text.await_args.args[0]
        assert "Choose a group or channel" in text
        callbacks = [button.callback_data for button in _buttons(
            query.edit_message_text.await_args.kwargs["reply_markup"]
        )]
        assert callbacks == [f"{wizard.CB_CHAT_PICK_PREFIX}-1001234567890", wizard.CB_MANUAL_CHAT_ID, wizard.CB_CANCEL]

    asyncio.run(scenario())


def test_schedule_conversation_registers_intro_state():
    conversation = wizard.build_schedule_wizard()
    assert wizard.WIZARD_INTRO in conversation.states
    assert any(
        handler.callback.__name__ == "cb_wizard_continue"
        for handler in conversation.states[wizard.WIZARD_INTRO]
    )


def test_schedule_template_preview_shows_full_saved_content():
    template = {
        "_id": "0123456789abcdef01234567",
        "name": "Weekly Notice",
        "content": {"media_type": "video", "media_file_id": "file-id", "text": "The complete saved caption"},
    }
    preview = wizard._wizard_template_page_text([template], 0)
    assert "Weekly Notice" in preview
    assert "video" in preview
    assert "The complete saved caption" in preview
    assert wizard._TMPL_WIZ_PAGE_SIZE == 1


def test_schedule_empty_template_preview_explains_manual_entry():
    preview = wizard._wizard_template_page_text([], 0)
    assert "No User Templates Found" in preview
    assert "send your post content manually" in preview
