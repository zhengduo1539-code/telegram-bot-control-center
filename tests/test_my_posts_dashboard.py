"""Regression tests for the My Posts management dashboard."""
import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from handlers import base


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _post(status="pending", post_id="a" * 24, **overrides):
    post = {
        "_id": post_id,
        "status": status,
        "chat_id": -1001234567890,
        "chat_title": "Announcements",
        "timezone": "Asia/Bangkok",
        "recurrence": {"type": "daily"},
        "content": {"text": "Hello"},
    }
    post.update(overrides)
    return post


def test_status_filter_returns_only_matching_posts_and_preserves_order():
    posts = [_post("pending"), _post("paused", "b" * 24), _post("posted", "c" * 24)]
    assert base._filter_posts(posts, "pending") == [posts[0]]
    assert base._filter_posts(posts, "all") == posts
    assert base._filter_posts(posts, "unknown") == posts


def test_posts_dashboard_card_is_readable_and_escapes_group_title():
    post = _post(chat_title="Sales_[Official]")
    rendered = base._posts_list_text([post], 0, "en")
    assert "Active" in rendered
    assert "Sales\\_\\[Official]" in rendered
    assert "Daily" in rendered
    assert "Asia/Bangkok" not in rendered
    assert "Next run" in rendered


def test_posts_keyboard_exposes_filters_and_preserves_existing_actions():
    post = _post()
    markup = base._posts_keyboard([post], 0, "en", "pending")
    buttons = _buttons(markup)
    callbacks = [button.callback_data for button in buttons]
    assert f"{base.CB_MPP_FILTER}all" in callbacks
    assert f"{base.CB_MPP_FILTER}pending" in callbacks
    assert f"{base.CB_MPP_FILTER}failed" in callbacks
    assert f"{base.CB_MPP_PAUSE}{post['_id']}" in callbacks
    assert f"{base.CB_MPP_DEL}{post['_id']}" in callbacks
    assert f"{base.CB_MPP_CLONE}{post['_id']}" in callbacks
    assert f"{base.CB_MPP_SAVE_TMPL}{post['_id']}" in callbacks



def test_posts_rich_message_builds_native_compact_table():
    posts = [_post("pending"), _post("paused", "b" * 24)]
    rich = base._posts_rich_message(posts, 0, "en", all_posts=posts, filter_key="all")
    table = next(block for block in rich["blocks"] if block["type"] == "table")
    assert table["is_bordered"] is True
    assert table["is_striped"] is True
    assert table["is_compact"] is True
    assert len(table["cells"]) == 3
    assert table["cells"][1][0]["text"].startswith("#1\nAnnouncements")
    assert table["cells"][1][2]["text"] == "⏳ Active"
    assert table["cells"][2][2]["text"] == "⏸ Paused"


def test_empty_state_offers_create_first_post_action(monkeypatch):
    async def scenario():
        fake_db = ModuleType("database")
        fake_db.get_user_posts = AsyncMock(return_value=[])
        monkeypatch.setitem(sys.modules, "database", fake_db)
        monkeypatch.setattr(base, "resolve_user_language", AsyncMock(return_value="en"))
        query = SimpleNamespace(edit_message_text=AsyncMock(), inline_message_id=None, message=SimpleNamespace(chat_id=123, message_id=456))
        context = SimpleNamespace(user_data={})
        state = await base._render_my_posts(query, 123, 0, context)
        assert state == base.MY_POSTS
        kwargs = query.edit_message_text.await_args.kwargs
        callbacks = [button.callback_data for button in _buttons(kwargs["reply_markup"])]
        assert base.CB_SCHEDULE_NEW in callbacks
        assert base.CB_MAIN_MENU in callbacks
        assert "first scheduled post" in query.edit_message_text.await_args.args[0].lower()

    asyncio.run(scenario())


def test_dashboard_shows_summary_and_filter_empty_state(monkeypatch):
    async def scenario():
        posts = [_post("pending"), _post("paused", "b" * 24), _post("posted", "c" * 24)]
        fake_db = ModuleType("database")
        fake_db.get_user_posts = AsyncMock(return_value=posts)
        monkeypatch.setitem(sys.modules, "database", fake_db)
        monkeypatch.setattr(base, "resolve_user_language", AsyncMock(return_value="en"))
        query = SimpleNamespace(edit_message_text=AsyncMock(), inline_message_id=None, message=SimpleNamespace(chat_id=123, message_id=456))
        context = SimpleNamespace(user_data={"_mp_filter": "failed"})
        state = await base._render_my_posts(query, 123, 0, context)
        assert state == base.MY_POSTS
        text = query.edit_message_text.await_args.args[0]
        assert "No Failed posts found" in text
        assert f"{base.CB_MPP_FILTER}all" in [
            button.callback_data for button in _buttons(query.edit_message_text.await_args.kwargs["reply_markup"])
        ]

        context.user_data["_mp_filter"] = "all"
        query.edit_message_text.reset_mock()
        context.bot = SimpleNamespace(do_api_request=AsyncMock(return_value={}))
        await base._render_my_posts(query, 123, 0, context)
        context.bot.do_api_request.assert_awaited_once()
        method = context.bot.do_api_request.await_args.args[0]
        payload = context.bot.do_api_request.await_args.kwargs["api_kwargs"]
        assert method == "editMessageText"
        assert payload["rich_message"]["blocks"][0]["text"] == "📋 My Posts"
        table = next(block for block in payload["rich_message"]["blocks"] if block["type"] == "table")
        assert table["is_bordered"] is True
        assert table["is_striped"] is True
        assert table["is_compact"] is True
        assert len(table["cells"]) == 4
        assert [cell["text"] for cell in table["cells"][0]] == ["Post", "Content", "Status", "Next run"]
        assert query.edit_message_text.await_count == 0

    asyncio.run(scenario())


def test_filter_callback_resets_page_and_renders_filtered_results(monkeypatch):
    async def scenario():
        monkeypatch.setattr(base, "_render_my_posts", AsyncMock(return_value=base.MY_POSTS))
        query = SimpleNamespace(data=f"{base.CB_MPP_FILTER}paused", from_user=SimpleNamespace(id=123), answer=AsyncMock())
        context = SimpleNamespace(user_data={"_mp_page": 3, "_mp_filter": "all"})
        state = await base.cb_mpp_filter(SimpleNamespace(callback_query=query), context)
        assert state == base.MY_POSTS
        assert context.user_data == {"_mp_page": 0, "_mp_filter": "paused"}
        base._render_my_posts.assert_awaited_once_with(query, 123, 0, context)

    asyncio.run(scenario())
