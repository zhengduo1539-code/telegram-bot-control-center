"""Regression tests for the all-private-actions Reply Keyboard bridge."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from reply_keyboard import split_inline_keyboard


def _labels(markup):
    return [button.text for row in markup.keyboard for button in row]


def test_only_add_to_group_and_share_bot_remain_inline():
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add to My Group", url="https://t.me/Scheduler?startgroup=true"),
            InlineKeyboardButton("📤 Share Bot", url="https://t.me/share/url?url=https://t.me/Scheduler"),
        ],
        [
            InlineKeyboardButton("📅 Schedule New Post", callback_data="action:schedule_new"),
            InlineKeyboardButton("📋 My Posts", callback_data="action:my_posts"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="nav:help"),
            InlineKeyboardButton("⚙️ Language", callback_data="nav:language"),
        ],
    ])

    inline, reply, routes = split_inline_keyboard(markup)

    assert [button.text for row in inline.inline_keyboard for button in row] == [
        "➕ Add to My Group", "📤 Share Bot"
    ]
    assert isinstance(reply, ReplyKeyboardMarkup)
    assert set(_labels(reply)) == {
        "📅 Schedule New Post", "📋 My Posts", "❓ Help", "⚙️ Language"
    }
    assert routes["📅 Schedule New Post"] == {
        "type": "callback", "data": "action:schedule_new"
    }
    assert routes["📋 My Posts"] == {"type": "callback", "data": "action:my_posts"}


def test_non_welcome_url_button_becomes_a_reply_choice():
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📩 Open Bot in Private Chat",
            url="https://t.me/Scheduler?start=setup_-100123",
        )
    ]])
    inline, reply, routes = split_inline_keyboard(markup)

    assert inline is None
    assert isinstance(reply, ReplyKeyboardMarkup)
    assert _labels(reply) == ["📩 Open Bot in Private Chat"]
    assert routes["📩 Open Bot in Private Chat"] == {
        "type": "url", "url": "https://t.me/Scheduler?start=setup_-100123"
    }


def test_conflicting_duplicate_button_labels_are_disambiguated():
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Next", callback_data="page:1")],
        [InlineKeyboardButton("Next", callback_data="page:2")],
    ])
    inline, reply, routes = split_inline_keyboard(markup)

    assert inline is None
    assert isinstance(reply, ReplyKeyboardMarkup)
    assert _labels(reply) == ["Next", "Next (2)"]
    assert routes["Next"]["data"] == "page:1"
    assert routes["Next (2)"]["data"] == "page:2"


def test_empty_inline_keyboard_returns_no_markup():
    inline, reply, routes = split_inline_keyboard(InlineKeyboardMarkup([]))
    assert inline is None
    assert reply is None
    assert routes == {}
