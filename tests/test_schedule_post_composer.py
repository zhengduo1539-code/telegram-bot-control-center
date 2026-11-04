"""Regression tests for the non-linear Schedule Post Composer."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from handlers import schedule_post_flow as composer


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _valid_draft(**overrides):
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


def test_workspace_exposes_independent_sections_instead_of_a_linear_step_sequence():
    buttons = _buttons(composer._home_keyboard({}))
    callbacks = {button.callback_data for button in buttons}

    assert {
        composer.CB_CONTENT,
        composer.CB_DESTINATION,
        composer.CB_SCHEDULE,
        composer.CB_OPTIONS,
        composer.CB_BUTTONS,
        composer.CB_REVIEW,
        composer.CB_CANCEL,
    } <= callbacks
    assert "1/8" not in composer._home_text({})
    assert "any order" in composer._home_text({}).lower()


def test_composer_conversation_registers_workspace_and_independent_section_states():
    conversation = composer.build_schedule_wizard()

    assert composer.HOME in conversation.states
    assert composer.CONTENT in conversation.states
    assert composer.DESTINATION in conversation.states
    assert composer.SCHEDULE in conversation.states
    assert composer.OPTIONS in conversation.states
    assert composer.BUTTONS in conversation.states
    assert composer.REVIEW in conversation.states
    assert not hasattr(composer, "WIZARD_INTRO")


def test_schedule_ready_requires_a_complete_repeat_pattern_and_future_datetime():
    draft = _valid_draft()
    assert composer._schedule_is_ready(draft)

    interval = _valid_draft(recurrence_type="interval", max_runs=None)
    assert not composer._schedule_is_ready(interval)
    interval.update(interval_value=6, interval_unit="hours")
    assert composer._schedule_is_ready(interval)

    weekly = _valid_draft(recurrence_type="days_of_week", max_runs=None, days_of_week=set())
    assert not composer._schedule_is_ready(weekly)
    weekly["days_of_week"] = {0, 2, 4}
    assert composer._schedule_is_ready(weekly)

    weekly["first_run"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert not composer._schedule_is_ready(weekly)


def test_workspace_marks_required_sections_and_optional_sections_separately():
    empty = composer._home_text({})
    assert "Content" in empty and "Needs setup" in empty
    assert "Destination" in empty
    assert "Publish Schedule" in empty
    assert "Optional" in empty

    populated = composer._home_text(_valid_draft())
    assert "Content* — Ready" in populated
    assert "Destination* — Ready" in populated
    assert "Schedule* — Ready" in populated


def test_schedule_validation_accepts_complete_post_with_optional_rules_off():
    draft = _valid_draft()
    assert composer._validate(draft) == []


def test_schedule_validation_rejects_incomplete_interval_weekdays_and_time_window():
    interval = _valid_draft(recurrence_type="interval", max_runs=None)
    assert "Complete the repeat interval." in composer._validate(interval)
    interval.update(interval_value=12, interval_unit="hours")
    assert composer._validate(interval) == []

    weekly = _valid_draft(recurrence_type="days_of_week", max_runs=None, days_of_week=set())
    assert "Choose at least one weekday." in composer._validate(weekly)
    weekly["days_of_week"] = {1, 3}
    assert composer._validate(weekly) == []

    window = _valid_draft(time_window_enabled=True, tw_start="09:00", tw_end="09:00")
    assert "Complete the posting window." in composer._validate(window)


def test_post_button_batch_is_atomic_and_normalizes_telegram_usernames():
    parsed = composer._parse_button_batch(
        "Visit Website | https://example.com\nJoin Channel | @mychannel",
        0,
    )
    assert parsed == [
        [{"text": "Visit Website", "url": "https://example.com"}],
        [{"text": "Join Channel", "url": "https://t.me/mychannel"}],
    ]

    assert composer._parse_button_batch("Valid | https://example.com\nInvalid line", 0) is None
    assert composer._parse_button_batch("New | https://example.com", 10) is None
    assert composer._parse_button_batch("| https://example.com", 0) is None


def test_auto_pin_duration_accepts_minutes_and_hours_with_five_minute_minimum():
    assert composer._duration_to_hours("5 minutes", max_minutes=87600 * 60) == 5 / 60
    assert composer._duration_to_hours("30m", max_minutes=87600 * 60) == 0.5
    assert composer._duration_to_hours("2 hours", max_minutes=87600 * 60) == 2
    assert composer._duration_to_hours("12h", max_minutes=87600 * 60) == 12
    assert composer._duration_to_hours("4 minutes", max_minutes=87600 * 60) is None
    assert composer._duration_to_hours("two hours", max_minutes=87600 * 60) is None


def test_auto_pin_label_is_clear_for_timed_and_permanent_pinning():
    assert composer._pin_label({"auto_pin": False}) == "Off"
    assert composer._pin_label({"auto_pin": True, "ap_hours": None}) == "Keep pinned until manually unpinned"
    assert composer._pin_label({
        "auto_pin": True,
        "ap_hours": 0.5,
        "ap_duration_label": "30 minutes",
    }) == "Unpin after 30 minutes"


def test_workspace_screen_sends_replacement_when_existing_message_cannot_be_edited():
    async def scenario():
        bot = SimpleNamespace(
            edit_message_text=AsyncMock(side_effect=RuntimeError("Message can't be edited")),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=902)),
        )
        context = SimpleNamespace(bot=bot)
        draft = {"ui_chat_id": 42, "ui_message_id": 100}

        await composer._show(
            context,
            draft,
            "Composer",
            InlineKeyboardMarkup([[InlineKeyboardButton("Continue", callback_data="continue")]]),
        )

        bot.edit_message_text.assert_awaited_once()
        bot.send_message.assert_awaited_once()
        assert draft["ui_message_id"] == 902

    asyncio.run(scenario())


def test_confirm_lock_prevents_two_concurrent_callbacks_from_inserting_twice(monkeypatch):
    async def scenario():
        access_started = asyncio.Event()
        release_access = asyncio.Event()

        async def delayed_access(*_args):
            access_started.set()
            await release_access.wait()

        monkeypatch.setattr(composer, "ensure_chat_access", delayed_access)
        monkeypatch.setattr(composer, "build_scheduled_post", lambda **kwargs: kwargs)
        insert = AsyncMock(return_value="post-123")
        monkeypatch.setattr(composer, "insert_post", insert)
        monkeypatch.setattr(composer.chat_cleanup, "cleanup", AsyncMock())

        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat_id=42, message_id=100),
            answer=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={"schedule_post": _valid_draft()},
            bot=SimpleNamespace(edit_message_text=AsyncMock()),
        )
        update = SimpleNamespace(callback_query=query)

        first = asyncio.create_task(composer.cb_confirm(update, context))
        await access_started.wait()

        second_state = await composer.cb_confirm(update, context)
        assert second_state == composer.REVIEW
        assert context.user_data["schedule_post"]["_saving"] is True
        assert insert.await_count == 0

        release_access.set()
        first_state = await first
        assert first_state == composer.HOME
        insert.assert_awaited_once()
        assert "schedule_post" not in context.user_data

    asyncio.run(scenario())


def test_uncertain_database_insert_is_locked_from_blind_retry(monkeypatch):
    async def scenario():
        monkeypatch.setattr(composer, "ensure_chat_access", AsyncMock())
        monkeypatch.setattr(composer, "build_scheduled_post", lambda **kwargs: kwargs)
        insert = AsyncMock(side_effect=RuntimeError("connection lost after insert"))
        monkeypatch.setattr(composer, "insert_post", insert)

        query = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            message=SimpleNamespace(chat_id=42, message_id=101),
            answer=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={"schedule_post": _valid_draft()},
            bot=SimpleNamespace(edit_message_text=AsyncMock()),
        )
        update = SimpleNamespace(callback_query=query)

        first_state = await composer.cb_confirm(update, context)
        assert first_state == composer.REVIEW
        assert context.user_data["schedule_post"]["_save_uncertain"] is True
        assert context.user_data["schedule_post"]["_saving"] is True

        second_state = await composer.cb_confirm(update, context)
        assert second_state == composer.REVIEW
        insert.assert_awaited_once()

    asyncio.run(scenario())
