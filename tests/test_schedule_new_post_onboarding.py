"""Regression tests for Schedule New Post onboarding and navigation."""
import asyncio
from datetime import datetime, timedelta, timezone
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
        assert "template" not in catalog["intro"].lower(), language
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



def test_schedule_content_step_has_clear_back_and_keep_actions():
    labels = [button.text for button in _buttons(wizard._content_step_keyboard())]
    assert labels == ["⬅️ Back to Destination", "❌ Cancel"]

    saved_labels = [button.text for button in _buttons(wizard._content_step_keyboard(True))]
    assert saved_labels == [
        "✅ Keep Current Content",
        "⬅️ Back to Destination",
        "❌ Cancel",
    ]
    assert not hasattr(wizard, "WIZARD_TMPL_PICK")

    conversation = wizard.build_schedule_wizard()
    handlers = conversation.states[wizard.WIZARD_CONTENT]
    assert any(
        isinstance(handler, wizard.MessageHandler)
        and handler.callback.__name__ == "recv_content"
        for handler in handlers
    )
    assert not any(
        "template" in getattr(handler.callback, "__name__", "").lower()
        for handler in handlers
    )

def test_manual_first_run_input_uses_timezone_parser_and_displays_resolved_date(monkeypatch):
    async def scenario():
        parsed = datetime(2027, 12, 25, 2, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(wizard, "_parse_run_time", lambda raw, tz: parsed)
        message = SimpleNamespace(text="09:00", reply_text=AsyncMock())
        context = SimpleNamespace(user_data={"language": "en", "wizard": {"timezone": "Asia/Bangkok"}})

        state = await wizard.recv_first_run(SimpleNamespace(message=message), context)

        assert state == wizard.WIZARD_MAX_RUNS
        assert context.user_data["wizard"]["first_run"] == parsed
        assert "25/12/2027 09:00" in message.reply_text.await_args.args[0]

    asyncio.run(scenario())


def test_manual_first_run_rejects_invalid_or_past_time_without_advancing(monkeypatch):
    async def scenario():
        monkeypatch.setattr(wizard, "_parse_run_time", lambda *_: None)
        message = SimpleNamespace(text="25/12/2025 09:00", reply_text=AsyncMock())
        context = SimpleNamespace(user_data={"language": "en", "wizard": {"timezone": "UTC"}})

        state = await wizard.recv_first_run(SimpleNamespace(message=message), context)

        assert state == wizard.WIZARD_FIRST_RUN
        assert "first_run" not in context.user_data["wizard"]
        assert "future time" in message.reply_text.await_args.args[0].lower()

    asyncio.run(scenario())


def test_confirm_post_requires_first_run_still_in_future():
    async def scenario():
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={
            "language": "en",
            "wizard": {
                "chat_id": -1001234567890,
                "content_text": "A scheduled post",
                "recurrence_type": "once",
                "timezone": "UTC",
                "first_run": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
        })

        state = await wizard.cb_confirm_post(SimpleNamespace(callback_query=query), context)

        assert state == wizard.WIZARD_FIRST_RUN
        assert "first-post time" in query.edit_message_text.await_args.args[0].lower()
        assert "reply_markup" in query.edit_message_text.await_args.kwargs

    asyncio.run(scenario())


def test_inline_button_plain_label_then_link_flow_is_easy_to_follow():
    async def scenario():
        context = SimpleNamespace(user_data={"wizard": {"inline_buttons": []}})
        label_message = SimpleNamespace(text="Join Channel", reply_text=AsyncMock())

        state = await wizard.recv_ib_entry(SimpleNamespace(message=label_message), context)

        assert state == wizard.WIZARD_INLINE_BTNS
        assert context.user_data["wizard"]["pending_inline_button_label"] == "Join Channel"
        label_prompt = label_message.reply_text.await_args.args[0]
        assert "Step 2 of 2" in label_prompt
        assert "@mychannel" in label_prompt

        link_message = SimpleNamespace(text="https://t.me/mychannel", reply_text=AsyncMock())
        state = await wizard.recv_ib_entry(SimpleNamespace(message=link_message), context)

        assert state == wizard.WIZARD_INLINE_BTNS
        assert context.user_data["wizard"]["inline_buttons"] == [[
            {"text": "Join Channel", "url": "https://t.me/mychannel"}
        ]]
        result_text = link_message.reply_text.await_args.args[0]
        assert "Button added" in result_text
        assert "Join Channel" in result_text
        assert "add another" in result_text.lower()
        assert "Done" in result_text
        assert "pending_inline_button_label" not in context.user_data["wizard"]

    asyncio.run(scenario())


def test_inline_button_invalid_link_keeps_the_label_and_retries():
    async def scenario():
        context = SimpleNamespace(user_data={"wizard": {
            "inline_buttons": [],
            "pending_inline_button_label": "Visit Website",
        }})
        message = SimpleNamespace(text="not a valid link", reply_text=AsyncMock())

        state = await wizard.recv_ib_entry(SimpleNamespace(message=message), context)

        assert state == wizard.WIZARD_INLINE_BTNS
        assert context.user_data["wizard"]["pending_inline_button_label"] == "Visit Website"
        assert not context.user_data["wizard"]["inline_buttons"]
        assert "couldn't recognize" in message.reply_text.await_args.args[0].lower()

    asyncio.run(scenario())


def test_inline_button_legacy_label_pipe_url_entry_remains_supported():
    async def scenario():
        context = SimpleNamespace(user_data={"wizard": {"inline_buttons": []}})
        message = SimpleNamespace(
            text="Visit Website | https://example.com",
            reply_text=AsyncMock(),
        )

        state = await wizard.recv_ib_entry(SimpleNamespace(message=message), context)

        assert state == wizard.WIZARD_INLINE_BTNS
        assert context.user_data["wizard"]["inline_buttons"] == [[
            {"text": "Visit Website", "url": "https://example.com"}
        ]]

    asyncio.run(scenario())


def test_max_runs_custom_count_accepts_positive_integer_and_rejects_zero():
    async def scenario():
        invalid_message = SimpleNamespace(text="0", reply_text=AsyncMock())
        context = SimpleNamespace(user_data={"wizard": {}})

        state = await wizard.recv_max_runs(SimpleNamespace(message=invalid_message), context)

        assert state == wizard.WIZARD_MAX_RUNS
        assert "max_runs" not in context.user_data["wizard"]
        assert "whole number" in invalid_message.reply_text.await_args.args[0].lower()

        valid_message = SimpleNamespace(text="27", reply_text=AsyncMock())
        state = await wizard.recv_max_runs(SimpleNamespace(message=valid_message), context)

        assert state == wizard.WIZARD_TIME_WINDOW
        assert context.user_data["wizard"]["max_runs"] == 27
        assert "27 sends" in valid_message.reply_text.await_args.args[0]

    asyncio.run(scenario())


def test_one_time_preset_stores_a_single_run():
    async def scenario():
        query = SimpleNamespace(
            data="mr:preset:1",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"wizard": {"recurrence_type": "once"}})

        state = await wizard.cb_mr_preset(SimpleNamespace(callback_query=query), context)

        assert state == wizard.WIZARD_TIME_WINDOW
        assert context.user_data["wizard"]["max_runs"] == 1
        assert "One send" in query.edit_message_text.await_args.args[0]

    asyncio.run(scenario())


def test_max_runs_guidance_explains_all_three_choices_in_every_locale():
    for language in LANGUAGES:
        prompt = wizard.tr(language, "max_runs_prompt")
        assert "One time" in prompt
        assert "Unlimited" in prompt
        assert "1" in prompt



def _valid_schedule_draft(**overrides):
    draft = {
        "chat_id": -1001234567890,
        "chat_title": "Announcements",
        "content_text": "A scheduled announcement",
        "recurrence_type": "once",
        "timezone": "UTC",
        "first_run": datetime.now(timezone.utc) + timedelta(hours=2),
        "max_runs": 1,
        "time_window_enabled": False,
        "auto_delete": False,
        "self_destruct": False,
        "auto_pin": False,
        "inline_buttons": [],
    }
    draft.update(overrides)
    return draft


def test_schedule_draft_validation_accepts_complete_one_time_post():
    draft = _valid_schedule_draft()
    assert wizard._validate_schedule_draft(draft) is None
    assert draft["max_runs"] == 1


def test_schedule_draft_validation_rejects_conflicting_delete_modes():
    draft = _valid_schedule_draft(
        auto_delete=True,
        ad_hours=2,
        self_destruct=True,
        sd_secs=60,
    )
    assert "either Auto-Delete or Self-Destruct" in wizard._validate_schedule_draft(draft)


def test_schedule_draft_validation_rejects_non_finite_durations_and_incomplete_pin():
    draft = _valid_schedule_draft(auto_delete=True, ad_hours=float("nan"))
    assert "Auto-Delete duration" in wizard._validate_schedule_draft(draft)

    draft = _valid_schedule_draft(auto_pin=True)
    assert "Auto-Pin duration" in wizard._validate_schedule_draft(draft)

    draft = _valid_schedule_draft(auto_pin=True, ap_hours=float("inf"))
    assert "Auto-Pin duration" in wizard._validate_schedule_draft(draft)


def test_schedule_draft_validation_accepts_permanent_pin_and_overnight_window():
    draft = _valid_schedule_draft(
        auto_pin=True,
        ap_hours=None,
        time_window_enabled=True,
        tw_start="22:00",
        tw_end="06:00",
    )
    assert wizard._validate_schedule_draft(draft) is None
    summary = wizard._build_summary(draft)
    assert "Keep pinned until manually unpinned" in summary
    assert "22:00 to 06:00" in summary


def test_schedule_summary_escapes_user_supplied_markdown_and_shows_one_send():
    draft = _valid_schedule_draft(
        chat_title="News_*[Group]",
        content_text="Breaking_*[news]",
        recurrence_type="once",
        inline_buttons=[[{"text": "Open_*[link]", "url": "https://example.com"}]],
    )
    summary = wizard._build_summary(draft)
    assert "1 send" in summary
    assert "Breaking\\_\\*\\[news]" in summary
    assert "News\\_\\*\\[Group]" in summary
    assert "Open\\_\\*\\[link]" in summary


def test_auto_delete_and_self_destruct_toggles_are_mutually_exclusive():
    async def scenario():
        query = SimpleNamespace(
            data=wizard.CB_LC_SD,
            answer=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"wizard": {
            "auto_delete": True,
            "ad_hours": 2,
            "ad_duration_label": "2 hours",
            "self_destruct": False,
            "auto_pin": False,
        }})
        state = await wizard.cb_lc_toggle(SimpleNamespace(callback_query=query), context)
        draft = context.user_data["wizard"]
        assert state == wizard.WIZARD_TIME_WINDOW
        assert draft["self_destruct"] is True
        assert draft["auto_delete"] is False
        assert "ad_hours" not in draft
        assert "ad_duration_label" not in draft
        query.edit_message_reply_markup.assert_awaited_once()

    asyncio.run(scenario())


def test_permanent_auto_pin_choice_shows_review_and_stores_none():
    async def scenario():
        query = SimpleNamespace(
            data=wizard.CB_AP_FOREVER,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"wizard": _valid_schedule_draft(auto_pin=True)})
        state = await wizard.cb_ap_forever(SimpleNamespace(callback_query=query), context)
        assert state == wizard.WIZARD_SUMMARY
        assert context.user_data["wizard"]["ap_hours"] is None
        assert "Keep pinned until manually unpinned" in query.edit_message_text.await_args.args[0]

    asyncio.run(scenario())


def test_schedule_save_failure_keeps_draft_for_recovery(monkeypatch):
    async def scenario():
        monkeypatch.setattr(wizard, "ensure_chat_access", AsyncMock())
        monkeypatch.setattr(wizard, "build_scheduled_post", lambda **kwargs: kwargs)
        monkeypatch.setattr(wizard, "insert_post", AsyncMock(side_effect=RuntimeError("database unavailable")))
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat_id=42, message_id=99),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(bot=object(), user_data={"wizard": _valid_schedule_draft()})

        state = await wizard.cb_confirm_post(SimpleNamespace(callback_query=query), context)

        assert state == wizard.WIZARD_SUMMARY
        assert context.user_data["wizard"]["content_text"] == "A scheduled announcement"
        assert "_saving" not in context.user_data["wizard"]
        assert "check *My Posts*" in query.edit_message_text.await_args.args[0]

    asyncio.run(scenario())



def test_confirm_post_success_uses_message_fallback_and_ignores_delayed_duplicate(monkeypatch):
    async def scenario():
        monkeypatch.setattr(wizard, "ensure_chat_access", AsyncMock())
        monkeypatch.setattr(wizard, "build_scheduled_post", lambda **kwargs: kwargs)
        insert = AsyncMock(return_value=123)
        monkeypatch.setattr(wizard, "insert_post", insert)
        monkeypatch.setattr(wizard.chat_cleanup, "cleanup", AsyncMock())

        message = SimpleNamespace(
            chat_id=42,
            message_id=101,
            reply_text=AsyncMock(),
        )
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=message,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(side_effect=Exception("Message can't be edited")),
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock()),
            user_data={"wizard": _valid_schedule_draft()},
        )
        update = SimpleNamespace(callback_query=query)

        state = await wizard.cb_confirm_post(update, context)

        assert state == wizard.ConversationHandler.END
        insert.assert_awaited_once()
        message.reply_text.assert_awaited_once()
        assert "Post scheduled successfully" in message.reply_text.await_args.args[0]
        assert "wizard" not in context.user_data
        assert context.user_data["_completed_schedule_confirmations"] == {"101": "123"}

        # A delayed click on the same old Confirm button must not recreate the
        # cleared draft, run validation against it, or insert another schedule.
        duplicate_state = await wizard.cb_confirm_post(update, context)
        assert duplicate_state == wizard.ConversationHandler.END
        insert.assert_awaited_once()
        assert query.answer.await_args.kwargs["show_alert"] is True

    asyncio.run(scenario())


def test_confirm_post_lock_is_acquired_before_async_permission_check(monkeypatch):
    async def scenario():
        entered_access_check = asyncio.Event()
        release_access_check = asyncio.Event()

        async def delayed_access_check(*_args):
            entered_access_check.set()
            await release_access_check.wait()

        monkeypatch.setattr(wizard, "ensure_chat_access", delayed_access_check)
        monkeypatch.setattr(wizard, "build_scheduled_post", lambda **kwargs: kwargs)
        insert = AsyncMock(return_value=456)
        monkeypatch.setattr(wizard, "insert_post", insert)
        monkeypatch.setattr(wizard.chat_cleanup, "cleanup", AsyncMock())

        message = SimpleNamespace(
            chat_id=42,
            message_id=102,
            reply_text=AsyncMock(),
        )
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=message,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock()),
            user_data={"wizard": _valid_schedule_draft()},
        )

        first = asyncio.create_task(wizard.cb_confirm_post(update, context))
        await entered_access_check.wait()

        duplicate_state = await wizard.cb_confirm_post(update, context)
        assert duplicate_state == wizard.WIZARD_SUMMARY
        assert insert.await_count == 0
        assert context.user_data["wizard"]["_saving"] is True

        release_access_check.set()
        first_state = await first

        assert first_state == wizard.ConversationHandler.END
        insert.assert_awaited_once()

    asyncio.run(scenario())


def test_confirm_post_with_missing_draft_acknowledges_stale_callback_without_edit():
    async def scenario():
        query = SimpleNamespace(
            message=SimpleNamespace(chat_id=42, message_id=103),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={})

        state = await wizard.cb_confirm_post(
            SimpleNamespace(callback_query=query), context
        )

        assert state == wizard.ConversationHandler.END
        query.answer.assert_awaited_once()
        assert query.answer.await_args.kwargs["show_alert"] is True
        query.edit_message_text.assert_not_awaited()

    asyncio.run(scenario())
