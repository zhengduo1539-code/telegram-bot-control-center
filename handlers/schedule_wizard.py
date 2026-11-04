"""
handlers/schedule_wizard.py — Multi-step scheduling wizard.

Wizard flow:
  1.  WIZARD_CHAT_ID        — pick target chat from group picker (or enter ID manually)
  2.  WIZARD_CONTENT        — send post content (text / media)
  3.  WIZARD_RECURRENCE     — choose recurrence type
        ├─ interval  → WIZARD_INTERVAL_VALUE → WIZARD_INTERVAL_UNIT
        ├─ days_of_week → WIZARD_DOW_SELECT  (multi-toggle)
        └─ once      → (skip interval steps)
  4.  WIZARD_FIRST_RUN      — enter first-run datetime (HH:MM or DD/MM/YYYY HH:MM)
  5.  WIZARD_TIMEZONE       — pick timezone (inline keyboard)
  6.  WIZARD_MAX_RUNS       — max runs or "unlimited"
  7.  WIZARD_TIME_WINDOW    — silent-hours ON/OFF toggle
        └─ ON → WIZARD_TW_START → WIZARD_TW_END
  8.  WIZARD_LIFECYCLE      — toggle auto-delete / self-destruct / auto-pin
        ├─ auto-delete ON  → WIZARD_AD_HOURS
        ├─ self-destruct ON → WIZARD_SD_SECS
        └─ auto-pin ON     → WIZARD_AP_HOURS
  9.  WIZARD_SUMMARY        — full review, ✅ Confirm saves to DB
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import (
    build_scheduled_post, insert_post,
    list_groups, list_groups_for_user, get_best_hours,
)
from handlers import chat_cleanup
from handlers.permissions import ChatAccessError, ensure_chat_access
from handlers.base import (
    CB_CANCEL, CB_MAIN_MENU, cmd_cancel, nav_keyboard, confirm_keyboard,
    main_menu_keyboard, replace_with_main_menu_message,
)
from handlers.language import resolve_user_language
from localization import translate as tr
from schedule_wizard_localization import translate_schedule_wizard as sw_tr
from schedule_utils import parse_schedule_datetime

logger = logging.getLogger(__name__)


def _parse_auto_delete_duration(raw: str) -> float | None:
    """Parse an explicit integer duration such as '5 minutes' or '2 hours'. Return hours."""
    match = re.fullmatch(r"\s*(\d+)\s*(minutes?|mins?|m|hours?|hrs?|h)\s*", raw or "", re.IGNORECASE)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    minutes = amount if unit in ("m", "min", "mins", "minute", "minutes") else amount * 60
    if minutes < 5 or minutes > 525600:
        return None
    return minutes / 60

# ---------------------------------------------------------------------------
# Wizard state constants
# ---------------------------------------------------------------------------
(
    WIZARD_CHAT_ID,
    WIZARD_CONTENT,
    WIZARD_RECURRENCE,
    WIZARD_INTERVAL_VALUE,
    WIZARD_INTERVAL_UNIT,
    WIZARD_DOW_SELECT,
    WIZARD_FIRST_RUN,
    WIZARD_TIMEZONE,
    WIZARD_MAX_RUNS,
    WIZARD_TIME_WINDOW,
    WIZARD_TW_START,
    WIZARD_TW_END,
    WIZARD_LIFECYCLE,
    WIZARD_AD_HOURS,
    WIZARD_SD_SECS,
    WIZARD_AP_HOURS,
    WIZARD_SUMMARY,
    WIZARD_INLINE_BTNS,
) = range(10, 28)

WIZARD_INTRO = 29       # guided introduction before the setup wizard

# ---------------------------------------------------------------------------
# Callback-data constants (all ≤ 64 bytes)
# ---------------------------------------------------------------------------
CB_SCHEDULE_NEW  = "action:schedule_new"
CB_WIZARD_CONTINUE = "wizard:continue"
CB_REC_ONCE      = "rec:once"
CB_REC_INTERVAL  = "rec:interval"
CB_REC_DOW       = "rec:dow"
CB_IU_MINUTES    = "iu:minutes"
CB_IU_HOURS      = "iu:hours"
CB_IU_DAYS       = "iu:days"
CB_DOW_NEXT      = "dow:next"
CB_TW_ON         = "tw:on"
CB_TW_OFF        = "tw:off"
CB_LC_NEXT       = "lc:next"
CB_CONFIRM_POST  = "confirm:post"
CB_IB_SKIP       = "ib:skip"
CB_IB_ADD        = "ib:add"
CB_IB_DONE       = "ib:done"
CB_BACK_INLINE_BTNS = "back:inline_btns"
CB_BACK_CHAT = "back:chat"
CB_CONTENT_KEEP = "content:keep"
CB_AP_FOREVER = "ap:forever"

# Day-of-week toggle: "dow:0" … "dow:6"
def _dow_cb(day: int) -> str:
    return f"dow:{day}"

# Lifecycle toggle callbacks
CB_LC_AD   = "lc:ad"    # auto-delete toggle
CB_LC_SD   = "lc:sd"    # self-destruct toggle
CB_LC_AP   = "lc:ap"    # auto-pin toggle

# Timezone pagination
CB_TZ_PAGE = "tz:page:"     # "tz:page:0", "tz:page:1", …

# Back targets
CB_BACK_RECURRENCE  = "back:recurrence"
CB_BACK_INTERVAL_V  = "back:interval_v"
CB_BACK_INTERVAL_U  = "back:interval_u"
CB_BACK_DOW         = "back:dow"
CB_BACK_FIRST_RUN   = "back:first_run"
CB_BACK_TIMEZONE    = "back:timezone"
CB_BACK_MAX_RUNS    = "back:max_runs"
CB_BACK_TW          = "back:tw"
CB_BACK_TW_START    = "back:tw_start"
CB_BACK_LIFECYCLE   = "back:lifecycle"
CB_BACK_CONTENT     = "back:content"
CB_BACK_TZ          = "back:tz"         # from timezone screen → smart-routes to rec/interval/DOW
CB_MR_PRESET        = "mr:preset:"     # + str(value) — quick-tap max-runs preset (0 = unlimited)

# Smart scheduling
CB_SMART_SUGGEST    = "smart:suggest"
CB_SMART_PICK_PREFIX = "smart:pick:"   # + "HH:MM"

# Group / chat picker (step 1)
CB_CHAT_PICK_PREFIX = "chat:pick:"   # + str(chat_id)
CB_CHAT_PAGE_PREFIX = "chat:page:"  # + str(page_num)
CB_MANUAL_CHAT_ID   = "chat:manual"
_CHAT_PAGE_SIZE     = 5

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

TIMEZONES: list[tuple[str, str]] = [
    ("UTC",             "UTC"),
    ("Asia/Kolkata",    "GMT+5:30 India"),
    ("Asia/Dhaka",      "GMT+6:00 Bangladesh"),
    ("Asia/Rangoon",    "GMT+6:30 Myanmar"),
    ("Asia/Bangkok",    "GMT+7:00 Thailand"),
    ("Asia/Singapore",  "GMT+8:00 Singapore"),
    ("Asia/Tokyo",      "GMT+9:00 Japan"),
    ("Asia/Seoul",      "GMT+9:00 Korea"),
    ("Europe/London",   "GMT+0/+1 London"),
    ("Europe/Berlin",   "GMT+1/+2 Berlin"),
    ("America/New_York","GMT-5/-4 New York"),
    ("America/Chicago", "GMT-6/-5 Chicago"),
    ("America/Denver",  "GMT-7/-6 Denver"),
    ("America/Los_Angeles","GMT-8/-7 Los Angeles"),
    ("Australia/Sydney","GMT+10/+11 Sydney"),
]
TZ_PAGE_SIZE = 5   # timezones shown per page

# ---------------------------------------------------------------------------
# Wizard data helpers
# ---------------------------------------------------------------------------

def _w(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    """Get (or create) the wizard scratch-pad in user_data."""
    return context.user_data.setdefault("wizard", {})


def _clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("wizard", None)


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def _chat_list_keyboard(
    groups: list[dict],
    page: int,
    bot_username: str,
    language: str = "en",
) -> InlineKeyboardMarkup:
    """
    Build the inline keyboard for the group/channel picker.
    Each group gets its own button row.  Navigation + manual-entry rows follow.
    """
    rows: list[list[InlineKeyboardButton]] = []
    total   = len(groups)
    start   = page * _CHAT_PAGE_SIZE
    end     = min(start + _CHAT_PAGE_SIZE, total)
    page_groups = groups[start:end]

    type_icon = {"channel": "📢", "supergroup": "👥", "group": "👥"}
    for g in page_groups:
        icon  = type_icon.get(g.get("chat_type", ""), "💬")
        title = (g.get("title") or f"Chat {g['chat_id']}")[:35]
        rows.append([InlineKeyboardButton(
            f"{icon} {title}",
            style="primary",
            callback_data=f"{CB_CHAT_PICK_PREFIX}{g['chat_id']}",
        )])

    # Pagination
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", style="primary", callback_data=f"{CB_CHAT_PAGE_PREFIX}{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton("Next ➡️", style="primary", callback_data=f"{CB_CHAT_PAGE_PREFIX}{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(tr(language, "manual_chat_id"), style="success", callback_data=CB_MANUAL_CHAT_ID)])
    rows.append([InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL)])
    return InlineKeyboardMarkup(rows)


def _no_groups_keyboard(bot_username: str, language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "➕ Add Bot to a Group",
            style="primary",
            url=f"https://t.me/{bot_username}?startgroup=start",
        )],
        [InlineKeyboardButton(tr(language, "manual_chat_id"), style="success", callback_data=CB_MANUAL_CHAT_ID)],
        [InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])


def _first_run_keyboard(back_data: str) -> InlineKeyboardMarkup:
    """First-run step keyboard — includes a 💡 Smart Suggest button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 Suggest Best Time", style="primary", callback_data=CB_SMART_SUGGEST)],
        [
            InlineKeyboardButton("⬅️ Back",   callback_data=back_data),
            InlineKeyboardButton("❌ Cancel", style="danger", callback_data=CB_CANCEL),
        ],
    ])


def _recurrence_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr(language, "recurrence_interval"), style="primary", callback_data=CB_REC_INTERVAL)],
        [InlineKeyboardButton(tr(language, "recurrence_days"), style="primary", callback_data=CB_REC_DOW)],
        [InlineKeyboardButton(tr(language, "recurrence_once"), style="primary", callback_data=CB_REC_ONCE)],
        [
            InlineKeyboardButton("⬅️ Back to Post Buttons", callback_data=CB_BACK_INLINE_BTNS),
            InlineKeyboardButton("❌ Cancel", style="danger", callback_data=CB_CANCEL),
        ],
    ])
def _interval_unit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏱ Minutes", style="primary", callback_data=CB_IU_MINUTES),
            InlineKeyboardButton("🕐 Hours",   style="primary", callback_data=CB_IU_HOURS),
            InlineKeyboardButton("📅 Days",    style="primary", callback_data=CB_IU_DAYS),
        ],
        [
            InlineKeyboardButton("⬅️ Back",   callback_data=CB_BACK_INTERVAL_V),
            InlineKeyboardButton("❌ Cancel", style="danger", callback_data=CB_CANCEL),
        ],
    ])


def _dow_keyboard(selected: set[int]) -> InlineKeyboardMarkup:
    """Build a 7-button day-of-week toggle keyboard."""
    rows: list[list[InlineKeyboardButton]] = []
    # Two rows: Mon-Thu, Fri-Sun
    days_row1 = [
        InlineKeyboardButton(
            f"{'✅' if d in selected else '☐'} {DOW_LABELS[d]}",
            style="success" if d in selected else "primary",
            callback_data=_dow_cb(d),
        )
        for d in range(4)
    ]
    days_row2 = [
        InlineKeyboardButton(
            f"{'✅' if d in selected else '☐'} {DOW_LABELS[d]}",
            style="success" if d in selected else "primary",
            callback_data=_dow_cb(d),
        )
        for d in range(4, 7)
    ]
    rows.append(days_row1)
    rows.append(days_row2)
    rows.append([
        InlineKeyboardButton("⬅️ Back",    callback_data=CB_BACK_RECURRENCE),
        InlineKeyboardButton("➡️ Next",    style="success", callback_data=CB_DOW_NEXT),
        InlineKeyboardButton("❌ Cancel",  style="danger", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _timezone_keyboard(page: int = 0, language: str = "en") -> InlineKeyboardMarkup:
    start = page * TZ_PAGE_SIZE
    end   = start + TZ_PAGE_SIZE
    page_tzs = TIMEZONES[start:end]
    rows = [
        [InlineKeyboardButton(label, style="primary", callback_data=f"tz:{tz_name}")]
        for tz_name, label in page_tzs
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(tr(language, "previous"), style="primary", callback_data=f"{CB_TZ_PAGE}{page - 1}"))
    if end < len(TIMEZONES):
        nav.append(InlineKeyboardButton(tr(language, "next"), style="primary", callback_data=f"{CB_TZ_PAGE}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_TZ),
        InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _time_window_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    on_icon  = "🟢 ON ✅" if enabled else "🔘 ON"
    off_icon = "🔴 OFF ✅" if not enabled else "🔘 OFF"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(on_icon, style="success" if enabled else "primary", callback_data=CB_TW_ON),
            InlineKeyboardButton(off_icon, style="success" if not enabled else "primary", callback_data=CB_TW_OFF),
        ],
        [
            InlineKeyboardButton("⬅️ Back",    callback_data=CB_BACK_MAX_RUNS),
            InlineKeyboardButton("➡️ Next",    style="success", callback_data=CB_LC_NEXT),
            InlineKeyboardButton("❌ Cancel",  style="danger", callback_data=CB_CANCEL),
        ],
    ])


def _lifecycle_keyboard(ad: bool, sd: bool, ap: bool) -> InlineKeyboardMarkup:
    def _flag(v: bool) -> str:
        return "✅" if v else "☐"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{_flag(ad)} Auto-Delete after X hours", style="success" if ad else "primary", callback_data=CB_LC_AD)],
        [InlineKeyboardButton(f"{_flag(sd)} Self-Destruct after X seconds", style="success" if sd else "primary", callback_data=CB_LC_SD)],
        [InlineKeyboardButton(f"{_flag(ap)} Auto-Pin (+ unpin after X hrs)", style="success" if ap else "primary", callback_data=CB_LC_AP)],
        [
            InlineKeyboardButton("⬅️ Back",   callback_data=CB_BACK_TW),
            InlineKeyboardButton("➡️ Next",   style="success", callback_data=CB_LC_NEXT),
            InlineKeyboardButton("❌ Cancel", style="danger", callback_data=CB_CANCEL),
        ],
    ])


def _advanced_keyboard(tw: bool, ad: bool, sd: bool, ap: bool) -> InlineKeyboardMarkup:
    """Optional controls for when posts may publish and what happens afterward."""
    tw_btn = InlineKeyboardButton(
        "🟢 Posting Window: ON" if tw else "🔘 Posting Window: OFF",
        style="success" if tw else "primary",
        callback_data=CB_TW_OFF if tw else CB_TW_ON,
    )

    def _flag(enabled: bool) -> str:
        return "✅" if enabled else "☐"

    return InlineKeyboardMarkup([
        [tw_btn],
        [InlineKeyboardButton(f"{_flag(ad)} Auto-Delete after a duration", style="success" if ad else "primary", callback_data=CB_LC_AD)],
        [InlineKeyboardButton(f"{_flag(sd)} Self-Destruct after seconds", style="success" if sd else "primary", callback_data=CB_LC_SD)],
        [InlineKeyboardButton(f"{_flag(ap)} Auto-Pin after publishing", style="success" if ap else "primary", callback_data=CB_LC_AP)],
        [
            InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_MAX_RUNS),
            InlineKeyboardButton("➡️ Review Options", style="success", callback_data=CB_LC_NEXT),
            InlineKeyboardButton("❌ Cancel", style="danger", callback_data=CB_CANCEL),
        ],
    ])
def _parse_run_time(text: str, tz: str) -> datetime | None:
    """Parse the selected local time and return an aware UTC datetime."""
    return parse_schedule_datetime(text, tz)


def _parse_hhmm(text: str) -> str | None:
    """Validate and normalise 'HH:MM' string. Returns None if invalid."""
    m = _TIME_RE.match(text.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


# ---------------------------------------------------------------------------
# Shared "answer + edit" helper
# ---------------------------------------------------------------------------

async def _edit(query: Any, text: str, keyboard: InlineKeyboardMarkup) -> None:
    """
    Answer the callback and edit the message in-place.
    Falls back to reply_text if the edit fails (e.g. message too old, parse error).
    """
    await query.answer()
    try:
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )
    except Exception as exc:
        logger.warning("edit_message_text failed (%s) — sending new message instead.", exc)
        try:
            await query.message.reply_text(
                text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
            )
        except Exception as exc2:
            logger.error("reply_text fallback also failed: %s", exc2)


# ---------------------------------------------------------------------------
# Wizard entry
# ---------------------------------------------------------------------------

def _wizard_intro_keyboard(language: str) -> InlineKeyboardMarkup:
    """Make the safe next step visually primary; keep the existing callback contract."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            tr(language, "cancel"),
            style="danger",
            callback_data=CB_CANCEL,
        ),
        InlineKeyboardButton(
            sw_tr(language, "continue"),
            style="success",
            callback_data=CB_WIZARD_CONTINUE,
        ),
    ]])


async def enter_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show a short, localized introduction before starting Schedule New Post."""
    query = update.callback_query
    _clear(context)
    _w(context)
    chat_cleanup.reset(context.application.bot_data, query.message.chat_id)
    chat_cleanup.track(
        context.application.bot_data, query.message.chat_id, query.message.message_id
    )
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    await query.answer()
    await query.edit_message_text(
        sw_tr(language, "intro"),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_wizard_intro_keyboard(language),
    )
    return WIZARD_INTRO


async def cb_wizard_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Continue from onboarding into destination selection or a linked group."""
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    launch_chat = context.user_data.pop("launch_chat", None)
    answered = False

    if isinstance(launch_chat, dict):
        try:
            launch_chat_id = int(launch_chat.get("chat_id"))
            target_chat = await ensure_chat_access(context.bot, launch_chat_id, user_id)
        except (TypeError, ValueError, ChatAccessError) as exc:
            logger.info("Deep-linked group is unavailable for user %s: %s", user_id, exc)
            await query.answer(
                "The linked group is unavailable or your admin permissions changed. Please select a group manually.",
                show_alert=True,
            )
            answered = True
        else:
            w = _w(context)
            w["chat_id"] = launch_chat_id
            w["chat_title"] = target_chat.title or str(launch_chat_id)
            await query.answer()
            await query.edit_message_text(
                _content_prompt_text(w, language),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_content_step_keyboard(bool(w.get("content_text") or w.get("media_file_id"))),
            )
            return WIZARD_CONTENT

    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id)
    if not answered:
        await query.answer()
    if groups:
        await query.edit_message_text(
            f"▰▱▱▱▱▱▱▱ 1/8\n*Step 1/8 — Destination*\n\n{tr(language, 'choose_group')}\n\n"
            f"_(Showing {min(_CHAT_PAGE_SIZE, len(groups))} of {len(groups)} saved chats)_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_chat_list_keyboard(groups, 0, bot_username, language),
        )
    else:
        await query.edit_message_text(
            f"▰▱▱▱▱▱▱▱ 1/8\n*Step 1/8 — Destination*\n\n{tr(language, 'choose_group')}\n\n"
            "No saved groups were found. Add the bot to a group or enter a Chat ID manually.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_no_groups_keyboard(bot_username, language),
        )
    return WIZARD_CHAT_ID
async def cb_chat_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate the chosen destination and proceed to the content step."""
    query = update.callback_query
    raw = (query.data or "").replace(CB_CHAT_PICK_PREFIX, "", 1)
    try:
        chat_id = int(raw)
    except ValueError:
        await query.answer("Invalid destination selection.", show_alert=True)
        return WIZARD_CHAT_ID
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    groups = await list_groups_for_user(user_id) if user_id is not None else []
    titles = {g["chat_id"]: (g.get("title") or str(g["chat_id"])) for g in groups}
    try:
        target_chat = await ensure_chat_access(query.get_bot(), chat_id, user_id)
    except ChatAccessError as exc:
        await query.answer(str(exc), show_alert=True)
        return WIZARD_CHAT_ID
    w = _w(context)
    w["chat_id"] = chat_id
    w["chat_title"] = target_chat.title or titles.get(chat_id, str(chat_id))
    await query.answer()
    await query.edit_message_text(
        _content_prompt_text(w, language),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_content_step_keyboard(bool(w.get("content_text") or w.get("media_file_id"))),
    )
    return WIZARD_CONTENT
def _content_step_keyboard(has_content: bool = False) -> InlineKeyboardMarkup:
    """Content actions with an explicit route back to the destination."""
    rows: list[list[InlineKeyboardButton]] = []
    if has_content:
        rows.append([InlineKeyboardButton("✅ Keep Current Content", style="success", callback_data=CB_CONTENT_KEEP)])
    rows.append([
        InlineKeyboardButton("⬅️ Back to Destination", callback_data=CB_BACK_CHAT),
        InlineKeyboardButton("❌ Cancel", style="danger", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _content_prompt_text(w: dict[str, Any], language: str = "en") -> str:
    title = escape_markdown(str(w.get("chat_title") or w.get("chat_id") or "Selected chat"), version=1)
    return (
        "▰▱▱▱▱▱▱▱ 2/8\n"
        "📝 *Step 2/8 — Post Content*\n\n"
        f"📍 *Destination:* {title}\n\n"
        "Send one message that you want the bot to publish.\n\n"
        "Supported: text, photo, video, document, audio, animation, or voice message.\n"
        "To publish text with media, send the media with a caption.\n"
        "Each Telegram message becomes one scheduled post; albums and forwarded bundles are not combined."
    )


def _inline_buttons_prompt(w: dict[str, Any]) -> str:
    count = len(w.get("inline_buttons", []))
    media = w.get("media_type")
    kind = "Media with caption" if media and w.get("content_text") else ("Media" if media else "Text")
    return (
        f"✅ Content saved ({kind}).\n\n"
        "🔘 *Step 2.5 — Post Buttons (Optional)*\n\n"
        "Add clickable buttons below your post. Each button needs a label and a destination.\n\n"
        "1. Tap Add Button.\n"
        "2. Send a short label, such as Visit Website.\n"
        "3. Send a full URL, such as https://example.com, or a Telegram @username.\n\n"
        f"Added: *{count}/10*. Choose Skip or Done when you are ready."
    )
async def cb_back_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to destination selection without discarding saved draft data."""
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id) if user_id is not None else []
    await query.answer()
    if groups:
        await query.edit_message_text(
            f"▰▱▱▱▱▱▱▱ 1/8\n*Step 1/8 — Destination*\n\n{tr(language, 'choose_group')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_chat_list_keyboard(groups, 0, bot_username, language),
        )
    else:
        await query.edit_message_text(
            f"▰▱▱▱▱▱▱▱ 1/8\n*Step 1/8 — Destination*\n\n{tr(language, 'choose_group')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_no_groups_keyboard(bot_username, language),
        )
    return WIZARD_CHAT_ID

async def cb_back_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to saved content; user can keep it or replace it."""
    query = update.callback_query
    w = _w(context)
    w.pop("pending_inline_button_label", None)
    has_content = bool(w.get("content_text") or w.get("media_file_id"))
    language = await resolve_user_language(context, query.from_user.id if query.from_user else None)
    prompt = _content_prompt_text(w, language)
    if has_content:
        prompt += "\n\nYour content is saved. Choose Keep Current Content to continue, or send a new message to replace it."
    await query.answer()
    await query.edit_message_text(
        prompt,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_content_step_keyboard(has_content),
    )
    return WIZARD_CONTENT


async def cb_content_keep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Continue with saved content without sending it again."""
    query = update.callback_query
    w = _w(context)
    if not (w.get("content_text") or w.get("media_file_id")):
        await query.answer("Send the post content first.", show_alert=True)
        return WIZARD_CONTENT
    w.setdefault("inline_buttons", [])
    await query.answer()
    await query.edit_message_text(
        _inline_buttons_prompt(w),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_inline_btns_keyboard(len(w["inline_buttons"])),
    )
    return WIZARD_INLINE_BTNS

async def cb_chat_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paginate the group list."""
    query = update.callback_query
    raw = (query.data or "").replace(CB_CHAT_PAGE_PREFIX, "", 1)
    page = int(raw) if raw.isdigit() else 0
    user_id      = query.from_user.id  # type: ignore[union-attr]
    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id)
    total  = len(groups)
    start  = page * _CHAT_PAGE_SIZE
    shown  = min(_CHAT_PAGE_SIZE, total - start)
    await query.answer()
    await query.edit_message_text(
        f"▰▱▱▱▱▱▱▱ 1/8\n*Chat* › Content › Recurrence › Timezone › 1st Run › Max Runs › Advanced › Summary\n📅 *Step 1/8 — Target Chat*\n\n"
        f"🗂 Choose the group or channel to post to:\n"
        f"_(Showing {shown} of {total} known chats)_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_chat_list_keyboard(groups, page, bot_username),
    )
    return WIZARD_CHAT_ID


async def cb_manual_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User wants to type the chat ID manually."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "▰▱▱▱▱▱▱▱ 1/8\n*Chat* › Content › Recurrence › Timezone › 1st Run › Max Runs › Advanced › Summary\n📅 *Step 1/8 — Target Chat*\n\n"
        "✏️ Type the *Chat ID* of the target channel or group:\n\n"
        "💡 Forward any message from the chat to @userinfobot to find its ID.\n\n"
        "Example: `-1001234567890`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=nav_keyboard(back_data=CB_MAIN_MENU),
    )
    return WIZARD_CHAT_ID


# Step 1 — Chat ID (manual text entry fallback)
# ---------------------------------------------------------------------------

async def recv_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    try:
        chat_id = int(raw)
    except ValueError:
        await update.message.reply_text(
            "⚠️ Enter a valid Telegram group or channel ID, for example -1001234567890.",
            reply_markup=nav_keyboard(back_data=CB_BACK_CHAT),
        )
        return WIZARD_CHAT_ID
    if chat_id >= 0:
        await update.message.reply_text(
            "⚠️ Group and channel IDs must be negative. Check the ID and try again.",
            reply_markup=nav_keyboard(back_data=CB_BACK_CHAT),
        )
        return WIZARD_CHAT_ID
    try:
        target_chat = await ensure_chat_access(context.bot, chat_id, update.effective_user.id)
    except ChatAccessError as exc:
        await update.message.reply_text(
            f"⚠️ {exc}",
            reply_markup=nav_keyboard(back_data=CB_BACK_CHAT),
        )
        return WIZARD_CHAT_ID
    w = _w(context)
    w["chat_id"] = chat_id
    w["chat_title"] = target_chat.title or str(chat_id)
    await update.message.reply_text(
        _content_prompt_text(w, context.user_data.get("language", "en")),
        reply_markup=_content_step_keyboard(bool(w.get("content_text") or w.get("media_file_id"))),
    )
    return WIZARD_CONTENT
async def recv_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save one supported Telegram message as the scheduled post content."""
    msg: Message = update.message
    w = _w(context)
    media_file_id = None
    media_type = None
    content_text = None
    if msg.photo:
        media_file_id, media_type, content_text = msg.photo[-1].file_id, "photo", msg.caption
    elif msg.video:
        media_file_id, media_type, content_text = msg.video.file_id, "video", msg.caption
    elif msg.document:
        media_file_id, media_type, content_text = msg.document.file_id, "document", msg.caption
    elif msg.audio:
        media_file_id, media_type, content_text = msg.audio.file_id, "audio", msg.caption
    elif msg.animation:
        media_file_id, media_type, content_text = msg.animation.file_id, "animation", msg.caption
    elif msg.voice:
        media_file_id, media_type, content_text = msg.voice.file_id, "voice", msg.caption
    elif msg.text and msg.text.strip():
        content_text = msg.text
    else:
        await msg.reply_text(
            "⚠️ This message type cannot be scheduled. Send text, photo, video, document, audio, animation, or voice message.",
            reply_markup=_content_step_keyboard(bool(w.get("content_text") or w.get("media_file_id"))),
        )
        return WIZARD_CONTENT

    # Replace related fields only after the new message is known to be valid.
    w["media_file_id"] = media_file_id
    w["media_type"] = media_type
    w["content_text"] = content_text
    w.setdefault("inline_buttons", [])
    await msg.reply_text(
        _inline_buttons_prompt(w),
        reply_markup=_inline_btns_keyboard(len(w["inline_buttons"])),
    )
    return WIZARD_INLINE_BTNS
def _inline_btns_keyboard(count: int) -> InlineKeyboardMarkup:
    """Optional post-button controls with a direct route back to saved content."""
    rows: list[list[InlineKeyboardButton]] = []
    if count == 0:
        rows.append([
            InlineKeyboardButton("➕ Add Button", style="primary", callback_data=CB_IB_ADD),
            InlineKeyboardButton("⏭ Skip", style="primary", callback_data=CB_IB_SKIP),
        ])
    elif count >= 10:
        rows.append([InlineKeyboardButton("✅ Done", style="success", callback_data=CB_IB_DONE)])
    else:
        rows.append([
            InlineKeyboardButton("➕ Add Another", style="primary", callback_data=CB_IB_ADD),
            InlineKeyboardButton("✅ Done", style="success", callback_data=CB_IB_DONE),
        ])
    rows.append([
        InlineKeyboardButton("⬅️ Back to Content", callback_data=CB_BACK_CONTENT),
        InlineKeyboardButton("❌ Cancel", style="danger", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)
async def cb_ib_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User skips inline buttons — go straight to recurrence."""
    query = update.callback_query
    _w(context).pop("pending_inline_button_label", None)
    _w(context).setdefault("inline_buttons", [])
    await _edit(query,
        tr(context.user_data.get("language", "en"), "recurrence_prompt"),
        _recurrence_keyboard(context.user_data.get("language", "en")),
    )
    return WIZARD_RECURRENCE


async def cb_ib_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the guided two-step post-button entry flow."""
    query = update.callback_query
    w = _w(context)
    if len(w.get("inline_buttons", [])) >= 10:
        await query.answer("You can add up to 10 buttons.", show_alert=True)
        return WIZARD_INLINE_BTNS
    w.pop("pending_inline_button_label", None)
    await _edit(
        query,
        "➕ *Add a Post Button*\n\n"
        "📝 *Step 1 of 2 — Button label*\n"
        "Send the short text people will see on the button, such as Visit Website or Join Channel.\n\n"
        "Next, the bot will ask for its destination link.",
        nav_keyboard(back_data=CB_BACK_CONTENT),
    )
    return WIZARD_INLINE_BTNS
async def recv_ib_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive one or more 'Label | URL' lines from the user."""
    text = (update.message.text or "").strip()
    w = _w(context)
    w.setdefault("inline_buttons", [])

    # Guided input: first accept a plain button label, then ask for its URL.
    # The old "Label | URL" shortcut below remains supported.
    pending_label = w.get("pending_inline_button_label")
    if "|" not in text:
        if pending_label is None:
            if not text or "\n" in text or "\r" in text:
                await update.message.reply_text(
                    "Please send one short button label at a time, such as Join Channel.",
                    reply_markup=nav_keyboard(),
                )
                return WIZARD_INLINE_BTNS
            if len(text) > 64:
                await update.message.reply_text(
                    "Button text must be 64 characters or fewer. Please send a shorter label.",
                    reply_markup=nav_keyboard(),
                )
                return WIZARD_INLINE_BTNS
            w["pending_inline_button_label"] = text
            await update.message.reply_text(
                f"✅ Button text saved: *{escape_markdown(text, version=1)}*\n\n"
                "🔗 *Step 2 of 2 — Destination link*\n"
                "Now send the full website/channel link, or a Telegram @username.\n\n"
                "Examples:\n"
                "• https://example.com\n"
                "• https://t.me/mychannel\n"
                "• @mychannel",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=nav_keyboard(),
            )
            return WIZARD_INLINE_BTNS

        url = text.strip()
        if url.startswith("@"):
            username = url[1:]
            if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
                url = ""
            else:
                url = "https://t.me/" + username
        elif not url.startswith(("http://", "https://", "tg://")):
            if "." in url and " " not in url:
                url = "https://" + url
        if not url or " " in url or not url.startswith(("http://", "https://", "tg://")):
            await update.message.reply_text(
                "⚠️ I couldn't recognize that link. Send a full URL or a Telegram @username. Please try again.",
                reply_markup=nav_keyboard(),
            )
            return WIZARD_INLINE_BTNS
        if len(w["inline_buttons"]) >= 10:
            await update.message.reply_text(
                "You already have 10 buttons. Tap Done to continue.",
                reply_markup=_inline_btns_keyboard(10),
            )
            w.pop("pending_inline_button_label", None)
            return WIZARD_INLINE_BTNS

        w["inline_buttons"].append([{"text": pending_label, "url": url}])
        w.pop("pending_inline_button_label", None)
        existing = _inline_btns_list_text(w["inline_buttons"])
        count = len(w["inline_buttons"])
        next_step = (
            "You've reached the 10-button limit. Tap *✅ Done* to continue."
            if count >= 10 else
            "Tap *➕ Add Another* to add one more, or *✅ Done* to continue."
        )
        await update.message.reply_text(
            f"✅ *Button added! ({count}/10)*\n\n"
            f"*Buttons so far:*\n{existing}\n\n{next_step}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_inline_btns_keyboard(count),
        )
        return WIZARD_INLINE_BTNS

    added = 0
    errors: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            errors.append(f"• `{line[:40]}` — missing `|`")
            continue
        label, _, url = line.partition("|")
        label = label.strip()
        url   = url.strip()
        if not label:
            errors.append(f"• _(empty label)_ `{url[:40]}`")
            continue
        # Allow @username shorthand → convert to t.me link
        if url.startswith("@"):
            url = "https://t.me/" + url[1:]
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
            errors.append(f"• `{label}` — link must be a URL (http/https/tg://) or @username")
            continue
        if len(w["inline_buttons"]) >= 10:
            errors.append("Maximum 10 buttons reached.")
            break
        w["inline_buttons"].append([{"text": label, "url": url}])
        added += 1

    existing = _inline_btns_list_text(w["inline_buttons"])
    err_block = ("\n\n⚠️ *Skipped:*\n" + "\n".join(errors)) if errors else ""
    await update.message.reply_text(
        f"✅ *{added} button(s) added.*{err_block}\n\n"
        f"*Buttons so far ({len(w['inline_buttons'])}/10):*\n{existing}\n\n"
        "Add another or tap *✅ Done*:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_inline_btns_keyboard(len(w["inline_buttons"])),
    )
    return WIZARD_INLINE_BTNS


async def cb_back_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return from recurrence selection to the optional post-button step."""
    query = update.callback_query
    w = _w(context)
    w.pop("pending_inline_button_label", None)
    await query.answer()
    await query.edit_message_text(
        _inline_buttons_prompt(w),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_inline_btns_keyboard(len(w.get("inline_buttons", []))),
    )
    return WIZARD_INLINE_BTNS

async def cb_ib_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Finished adding buttons — move to recurrence."""
    query = update.callback_query
    _w(context).pop("pending_inline_button_label", None)
    await _edit(query,
        tr(context.user_data.get("language", "en"), "recurrence_prompt"),
        _recurrence_keyboard(context.user_data.get("language", "en")),
    )
    return WIZARD_RECURRENCE


def _inline_btns_list_text(buttons: list) -> str:
    """Render button labels and URLs safely in a Markdown message."""
    lines: list[str] = []
    for row in buttons:
        for button in row:
            label = escape_markdown(str(button.get("text", "")), version=1)
            url = escape_markdown(str(button.get("url", "")), version=1)
            lines.append(f"• {label} → {url}")
    return "\n".join(lines) if lines else "None"
async def cb_rec_once(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Set one-time recurrence and force the matching one-send limit."""
    w = _w(context)
    w["recurrence_type"] = "once"
    w["max_runs"] = 1
    w.pop("interval_value", None)
    w.pop("interval_unit", None)
    w.pop("days_of_week", None)
    query = update.callback_query
    language = context.user_data.get("language", "en")
    await _edit(query, tr(language, "timezone_prompt"), _timezone_keyboard(0, language))
    return WIZARD_TIMEZONE
async def cb_rec_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _w(context)["recurrence_type"] = "interval"
    query = update.callback_query
    await _edit(query,
        "⏱ *Step 3a — Interval Value*\n\n"
        "How many minutes/hours/days between each post?\n\n"
        "Type a positive whole number (e.g. `6`):",
        nav_keyboard(back_data=CB_BACK_RECURRENCE),
    )
    return WIZARD_INTERVAL_VALUE


async def cb_rec_dow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    w = _w(context)
    w["recurrence_type"] = "days_of_week"
    w.setdefault("days_of_week", set())
    query = update.callback_query
    await _edit(query,
        "📆 *Step 3a — Days of Week*\n\n"
        "Tap days to toggle them. Tap *➡️ Next* when done.",
        _dow_keyboard(w["days_of_week"]),
    )
    return WIZARD_DOW_SELECT


# ---------------------------------------------------------------------------
# Step 3a (interval) — value
# ---------------------------------------------------------------------------

async def recv_interval_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text(
            "⚠️ Please enter a *positive whole number* greater than 0 (e.g. `6`).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return WIZARD_INTERVAL_VALUE

    _w(context)["interval_value"] = int(text)
    await update.message.reply_text(
        f"✅ Value: `{text}`\n\n"
        "⏱ *Step 3b — Interval Unit*\n\nChoose the time unit:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_interval_unit_keyboard(),
    )
    return WIZARD_INTERVAL_UNIT


async def cb_back_interval_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _edit(query,
        "⏱ *Step 3a — Interval Value*\n\n"
        "Type a positive whole number (e.g. `6`):",
        nav_keyboard(back_data=CB_BACK_RECURRENCE),
    )
    return WIZARD_INTERVAL_VALUE


# ---------------------------------------------------------------------------
# Step 3b (interval) — unit
# ---------------------------------------------------------------------------

async def cb_interval_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    unit_map = {CB_IU_MINUTES: "minutes", CB_IU_HOURS: "hours", CB_IU_DAYS: "days"}
    unit = unit_map[query.data]
    _w(context)["interval_unit"] = unit
    language = context.user_data.get("language", "en")
    await _edit(
        query,
        f"✅ Interval: every \`{_w(context)['interval_value']} {unit}\`\n\n{tr(language, 'timezone_prompt')}",
        _timezone_keyboard(0, language),
    )
    return WIZARD_TIMEZONE

# ---------------------------------------------------------------------------
# Step 3a (DOW) — day toggles
# ---------------------------------------------------------------------------

async def cb_dow_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    day = int(query.data.split(":")[1])
    w = _w(context)
    selected: set[int] = w.setdefault("days_of_week", set())
    if day in selected:
        selected.discard(day)
    else:
        selected.add(day)
    await query.answer(f"{'✅' if day in selected else '☐'} {DOW_LABELS[day]}")
    await query.edit_message_reply_markup(_dow_keyboard(selected))
    return WIZARD_DOW_SELECT


async def cb_dow_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    selected: set[int] = w.get("days_of_week", set())
    if not selected:
        await query.answer("⚠️ Please select at least one day.", show_alert=True)
        return WIZARD_DOW_SELECT
    language = context.user_data.get("language", "en")
    await _edit(
        query,
        f"✅ Days: \`{', '.join(DOW_LABELS[d] for d in sorted(selected))}\`\n\n{tr(language, 'timezone_prompt')}",
        _timezone_keyboard(0, language),
    )
    return WIZARD_TIMEZONE

# ---------------------------------------------------------------------------
# Step 4 — First run time
# ---------------------------------------------------------------------------

async def recv_first_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse a local future date/time and show only relevant delivery options."""
    raw = (update.message.text or "").strip()
    w = _w(context)
    timezone_name = w.get("timezone", "UTC")
    dt = _parse_run_time(raw, timezone_name)
    if dt is None:
        await update.message.reply_text(
            "⚠️ That time could not be validated as a future date.\n\n"
            "Enter a 24-hour time such as 14:30 for the next occurrence, or "
            "DD/MM/YYYY HH:MM for an exact date, such as 25/12/2027 09:00.",
            reply_markup=_first_run_keyboard(CB_BACK_TIMEZONE),
        )
        return WIZARD_FIRST_RUN

    w["first_run"] = dt
    w["first_run_raw"] = raw
    w.setdefault("time_window_enabled", False)
    local_display = dt.astimezone(pytz.timezone(timezone_name)).strftime("%d/%m/%Y %H:%M")
    one_time = w.get("recurrence_type") == "once"
    if one_time:
        w["max_runs"] = 1
        prompt = (
            f"✅ *First post:* {local_display} ({timezone_name})\n\n"
            "🔢 *One-time delivery*\n"
            "This post will be sent once at the selected time. No repeat count is needed."
        )
    else:
        prompt = (
            f"✅ *First post:* {local_display} ({timezone_name})\n\n"
            f"{tr(context.user_data.get('language', 'en'), 'max_runs_prompt')}"
        )
    await update.message.reply_text(
        prompt,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_max_runs_keyboard(one_time=one_time),
    )
    return WIZARD_MAX_RUNS
async def cb_back_first_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to the First Run screen."""
    query = update.callback_query
    language = context.user_data.get("language", "en")
    await _edit(query, tr(language, "first_run_prompt"), _first_run_keyboard(CB_BACK_TIMEZONE))
    return WIZARD_FIRST_RUN

_MEDAL = ["🥇", "🥈", "🥉"]


async def cb_smart_suggest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show top active hours for the chosen chat as tappable time buttons."""
    query = update.callback_query
    await query.answer()

    chat_id = _w(context).get("chat_id")
    results = await get_best_hours(chat_id, top_n=3) if chat_id else []

    if not results:
        await query.edit_message_text(
            "💡 *Smart Scheduling*\n\n"
            "⚠️ *Not enough data yet.*\n\n"
            "The bot learns active hours by observing messages in the group. "
            "Come back after the bot has been active there for a while.\n\n"
            "For now, type the time manually:\n"
            "• `HH:MM` — e.g. `09:00`\n"
            "• `DD/MM/YYYY HH:MM` — e.g. `25/12/2027 09:00`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_FIRST_RUN)],
            ]),
        )
        return WIZARD_FIRST_RUN

    total = sum(r["count"] for r in results)

    def _label(count: int) -> str:
        pct = count / total * 100 if total else 0
        if pct >= 40:
            return "high activity"
        if pct >= 20:
            return "good activity"
        return "moderate activity"

    lines = []
    btn_rows: list[list[InlineKeyboardButton]] = []
    for i, r in enumerate(results):
        h = r["hour"]
        time_str = f"{h:02d}:00"
        medal = _MEDAL[i] if i < 3 else "•"
        lines.append(f"{medal} *{time_str}* — {_label(r['count'])} ({r['count']} msgs)")
        btn_rows.append([InlineKeyboardButton(
            f"{medal} {time_str}",
            style="primary",
            callback_data=f"{CB_SMART_PICK_PREFIX}{time_str}",
        )])

    btn_rows.append([InlineKeyboardButton("✏️ Enter custom time", style="primary", callback_data=CB_BACK_FIRST_RUN)])

    await query.edit_message_text(
        "💡 *Suggested Best Times*\n\n"
        "Based on message activity in this chat:\n\n" +
        "\n".join(lines) +
        "\n\nTap a time to use it, or enter a custom time:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(btn_rows),
    )
    return WIZARD_FIRST_RUN


async def cb_smart_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Apply a suggested local time and route to the matching delivery-limit step."""
    query = update.callback_query
    time_str = (query.data or "").replace(CB_SMART_PICK_PREFIX, "", 1)
    w = _w(context)
    timezone_name = w.get("timezone", "UTC")
    dt = _parse_run_time(time_str, timezone_name)
    if dt is None:
        await query.answer("That suggested time is no longer available. Enter a future time manually.", show_alert=True)
        return WIZARD_FIRST_RUN

    w["first_run"] = dt
    w["first_run_raw"] = time_str
    w.setdefault("time_window_enabled", False)
    one_time = w.get("recurrence_type") == "once"
    local_display = dt.astimezone(pytz.timezone(timezone_name)).strftime("%d/%m/%Y %H:%M")
    if one_time:
        w["max_runs"] = 1
        prompt = (
            f"✅ *First post:* {local_display} ({timezone_name})\n\n"
            "🔢 *One-time delivery*\nThis post will be sent once at the selected time."
        )
    else:
        prompt = (
            f"✅ *First post:* {local_display} ({timezone_name})\n\n"
            f"{tr(context.user_data.get('language', 'en'), 'max_runs_prompt')}"
        )
    await query.answer()
    await query.edit_message_text(
        prompt,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_max_runs_keyboard(one_time=one_time),
    )
    return WIZARD_MAX_RUNS
async def cb_timezone_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    page = int(query.data.replace(CB_TZ_PAGE, ""))
    await query.answer()
    await query.edit_message_reply_markup(_timezone_keyboard(page, context.user_data.get("language", "en")))
    return WIZARD_TIMEZONE


async def cb_timezone_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    tz_name = query.data.replace("tz:", "")
    try:
        pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        await query.answer("⚠️ Unknown timezone.", show_alert=True)
        return WIZARD_TIMEZONE

    w = _w(context)
    w["timezone"] = tz_name

    await _edit(query,
        f"✅ Timezone: `{tz_name}`\n\n"
        f"{tr(context.user_data.get('language', 'en'), 'first_run_prompt')}\\n\\n"
        "When should this post fire?\n\n"
        "Type the date/time:\n"
        "• `HH:MM` — the next occurrence (today if upcoming, otherwise tomorrow; e.g. `14:30`)\n"
        "• `DD/MM/YYYY HH:MM` — specific date (e.g. `25/12/2027 09:00`)\n\n"
        "Or tap *💡 Suggest Best Time* to see active hours for this chat.",
        _first_run_keyboard(CB_BACK_TIMEZONE),
    )
    return WIZARD_FIRST_RUN


# ---------------------------------------------------------------------------
# Back to timezone
# ---------------------------------------------------------------------------

async def cb_back_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Called from First Run back button — return to Timezone picker."""
    query = update.callback_query
    await _edit(query,
        tr(context.user_data.get('language', 'en'), 'timezone_prompt'),
        _timezone_keyboard(0, context.user_data.get("language", "en")),
    )
    return WIZARD_TIMEZONE


async def cb_back_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Called from Timezone back button — smart-routes to recurrence/interval-unit/DOW."""
    query = update.callback_query
    w = _w(context)
    rec_type = w.get("recurrence_type", "once")
    if rec_type == "days_of_week":
        await _edit(query,
            "📆 *Step 3a — Days of Week*\n\nTap days to toggle, then ➡️ Next.",
            _dow_keyboard(w.get("days_of_week", set())),
        )
        return WIZARD_DOW_SELECT
    elif rec_type == "interval":
        await _edit(query,
            f"✅ Value: `{w.get('interval_value')}`\n\n"
            "⏱ *Step 3b — Interval Unit*\n\nChoose the time unit:",
            _interval_unit_keyboard(),
        )
        return WIZARD_INTERVAL_UNIT
    else:
        await _edit(query,
            tr(context.user_data.get("language", "en"), "recurrence_prompt"),
            _recurrence_keyboard(context.user_data.get("language", "en")),
        )
        return WIZARD_RECURRENCE


# ---------------------------------------------------------------------------
# Step 6 — Max runs
# ---------------------------------------------------------------------------

def _max_runs_keyboard(one_time: bool = False) -> InlineKeyboardMarkup:
    """Only show delivery-limit choices that apply to this recurrence."""
    if one_time:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ One-Time Post — 1 Send", style="success", callback_data=f"{CB_MR_PRESET}1")],
            [
                InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_FIRST_RUN),
                InlineKeyboardButton("❌ Cancel", style="danger", callback_data=CB_CANCEL),
            ],
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1× One time", style="primary", callback_data=f"{CB_MR_PRESET}1"),
            InlineKeyboardButton("5×", style="primary", callback_data=f"{CB_MR_PRESET}5"),
            InlineKeyboardButton("10×", style="primary", callback_data=f"{CB_MR_PRESET}10"),
        ],
        [
            InlineKeyboardButton("30×", style="primary", callback_data=f"{CB_MR_PRESET}30"),
            InlineKeyboardButton("∞ Unlimited", style="primary", callback_data=f"{CB_MR_PRESET}0"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_FIRST_RUN),
            InlineKeyboardButton("❌ Cancel", style="danger", callback_data=CB_CANCEL),
        ],
    ])
async def recv_max_runs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Accept bounded positive custom counts for recurring schedules only."""
    text = (update.message.text or "").strip()
    w = _w(context)
    one_time = w.get("recurrence_type") == "once"
    if one_time:
        w["max_runs"] = 1
        await update.message.reply_text(
            "This is a one-time post, so its delivery limit is already set to one.",
            reply_markup=_max_runs_keyboard(one_time=True),
        )
        return WIZARD_MAX_RUNS
    if not text.isdecimal() or not (1 <= int(text) <= 100000):
        await update.message.reply_text(
            "⚠️ Enter a whole number from 1 to 100,000 (for example, 7). "
            "Choose Unlimited below for an unlimited schedule.",
            reply_markup=_max_runs_keyboard(),
        )
        return WIZARD_MAX_RUNS

    value = int(text)
    w["max_runs"] = value
    w.setdefault("time_window_enabled", False)
    w.setdefault("auto_delete", False)
    w.setdefault("self_destruct", False)
    w.setdefault("auto_pin", False)
    await update.message.reply_text(
        f"✅ *Delivery limit:* {value} sends\n\n"
        "⚙️ *Advanced Options*\n"
        "These settings are optional and OFF by default. Turn on only what you need.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_advanced_keyboard(
            w["time_window_enabled"], w["auto_delete"], w["self_destruct"], w["auto_pin"]
        ),
    )
    return WIZARD_TIME_WINDOW
async def cb_mr_preset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate the selected preset against the active recurrence."""
    query = update.callback_query
    raw = (query.data or "").replace(CB_MR_PRESET, "", 1)
    try:
        value = int(raw)
    except ValueError:
        await query.answer("Invalid delivery limit.", show_alert=True)
        return WIZARD_MAX_RUNS

    w = _w(context)
    one_time = w.get("recurrence_type") == "once"
    allowed = {1} if one_time else {0, 1, 5, 10, 30}
    if value not in allowed:
        await query.answer("That delivery limit is not available for this schedule.", show_alert=True)
        return WIZARD_MAX_RUNS
    w["max_runs"] = 1 if one_time else (None if value == 0 else value)
    w.setdefault("time_window_enabled", False)
    w.setdefault("auto_delete", False)
    w.setdefault("self_destruct", False)
    w.setdefault("auto_pin", False)
    label = "One send" if one_time else ("Unlimited" if value == 0 else f"{value} sends")
    await _edit(
        query,
        f"✅ *Delivery limit:* {label}\n\n"
        "⚙️ *Advanced Options*\n"
        "These settings are optional and OFF by default. Turn on only what you need.",
        _advanced_keyboard(w["time_window_enabled"], w["auto_delete"], w["self_destruct"], w["auto_pin"]),
    )
    return WIZARD_TIME_WINDOW
async def cb_tw_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggle a local-time posting window and refresh the settings."""
    query = update.callback_query
    w = _w(context)
    enabled = query.data == CB_TW_ON
    w["time_window_enabled"] = enabled
    if not enabled:
        w.pop("tw_start", None)
        w.pop("tw_end", None)
    await query.answer("Posting Window enabled" if enabled else "Posting Window disabled")
    await query.edit_message_reply_markup(
        _advanced_keyboard(
            enabled,
            w.get("auto_delete", False),
            w.get("self_destruct", False),
            w.get("auto_pin", False),
        )
    )
    return WIZARD_TIME_WINDOW
async def cb_adv_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Route enabled optional settings through their detail screens."""
    query = update.callback_query
    w = _w(context)
    if w.get("time_window_enabled"):
        await _edit(
            query,
            "🕐 *Posting Window — Start Time*\n\n"
            "Enter the earliest local time the bot may publish each day.\n"
            "Use 24-hour HH:MM format, for example 08:00.\n"
            "Posts scheduled outside this window will wait until it opens.",
            nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_TW_START

    w.pop("tw_start", None)
    w.pop("tw_end", None)
    if w.get("auto_delete"):
        await _edit(
            query,
            "🗑 *Auto-Delete*\n\n"
            "How long after each post is published should the bot delete it?\n"
            "Enter a duration such as 5 minutes or 2 hours. Minimum: 5 minutes.",
            nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_AD_HOURS
    if w.get("self_destruct"):
        await _edit(
            query,
            "💣 *Self-Destruct*\n\n"
            "How many seconds after publishing should the bot delete each post?\n"
            "Enter a positive whole number, such as 60.",
            nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_SD_SECS
    if w.get("auto_pin"):
        await _edit(
            query,
            "📌 *Auto-Pin*\n\n"
            "Choose how long each post should stay pinned, or keep it pinned until you remove it manually.",
            _auto_pin_keyboard(),
        )
        return WIZARD_AP_HOURS
    return await _show_summary(update, context)
async def recv_tw_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    parsed = _parse_hhmm(raw)
    if parsed is None:
        await update.message.reply_text(
            "⚠️ Enter a valid 24-hour time in HH:MM format, such as 08:00.",
            reply_markup=nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_TW_START
    _w(context)["tw_start"] = parsed
    await update.message.reply_text(
        f"✅ Posting window opens at {parsed}.\n\n"
        "🕐 *Posting Window — End Time*\n\n"
        "Enter the latest local time the bot may publish, using HH:MM (for example, 22:00).\n"
        "The window may cross midnight, such as 22:00 to 06:00.",
        reply_markup=nav_keyboard(back_data=CB_BACK_TW_START),
    )
    return WIZARD_TW_END
async def recv_tw_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    parsed = _parse_hhmm(raw)
    w = _w(context)
    if parsed is None:
        await update.message.reply_text(
            "⚠️ Enter a valid 24-hour time in HH:MM format, such as 22:00.",
            reply_markup=nav_keyboard(back_data=CB_BACK_TW_START),
        )
        return WIZARD_TW_END

    start = w.get("tw_start")
    if not start or parsed == start:
        await update.message.reply_text(
            "⚠️ Start and end times must be valid and different. For an overnight window, choose an earlier end time.",
            reply_markup=nav_keyboard(back_data=CB_BACK_TW_START),
        )
        return WIZARD_TW_END

    w["tw_end"] = parsed
    prefix = f"✅ Posting window: {start}–{parsed}.\n\n"
    if w.get("auto_delete"):
        await update.message.reply_text(
            prefix + "🗑 *Auto-Delete*\nEnter a duration such as 5 minutes or 2 hours. Minimum: 5 minutes.",
            reply_markup=nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_AD_HOURS
    if w.get("self_destruct"):
        await update.message.reply_text(
            prefix + "💣 *Self-Destruct*\nEnter the number of seconds after publishing before each post is deleted.",
            reply_markup=nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_SD_SECS
    if w.get("auto_pin"):
        await update.message.reply_text(
            prefix + "📌 *Auto-Pin*\nChoose how long each post stays pinned, or keep it pinned until you remove it manually.",
            reply_markup=_auto_pin_keyboard(),
        )
        return WIZARD_AP_HOURS
    return await _show_summary_from_message(update, context)
async def cb_lc_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggle optional post actions; deletion modes are mutually exclusive."""
    query = update.callback_query
    w = _w(context)
    key_map = {CB_LC_AD: "auto_delete", CB_LC_SD: "self_destruct", CB_LC_AP: "auto_pin"}
    key = key_map.get(query.data)
    if key is None:
        await query.answer("Unknown option.", show_alert=True)
        return WIZARD_TIME_WINDOW

    enabled = not bool(w.get(key, False))
    w[key] = enabled
    if key == "auto_delete":
        if enabled:
            w["self_destruct"] = False
            w.pop("sd_secs", None)
        else:
            w.pop("ad_hours", None)
            w.pop("ad_duration_label", None)
    elif key == "self_destruct":
        if enabled:
            w["auto_delete"] = False
            w.pop("ad_hours", None)
            w.pop("ad_duration_label", None)
        else:
            w.pop("sd_secs", None)
    elif key == "auto_pin" and not enabled:
        w.pop("ap_hours", None)

    await query.answer(
        f"{'Enabled' if enabled else 'Disabled'}: {key.replace('_', ' ').title()}"
    )
    await query.edit_message_reply_markup(
        _advanced_keyboard(
            w.get("time_window_enabled", False),
            w.get("auto_delete", False),
            w.get("self_destruct", False),
            w.get("auto_pin", False),
        )
    )
    return WIZARD_TIME_WINDOW
async def cb_lc_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Route to the first enabled lifecycle detail step, or directly to summary.
    (Kept for back-compat; primary entry is now cb_adv_next from Advanced Options.)"""
    query = update.callback_query
    w = _w(context)
    if w.get("auto_delete"):
        await _edit(query,
            "🗑 *Auto-Delete — After how many hours?*\n\n"
            "Type a positive number (decimals OK, e.g. `24` or `0.5`):",
            nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_AD_HOURS
    if w.get("self_destruct"):
        await _edit(query,
            "💣 *Self-Destruct — After how many seconds?*\n\n"
            "Type a positive integer (e.g. `60`):",
            nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_SD_SECS
    if w.get("auto_pin"):
        await _edit(query,
            "📌 *Auto-Pin — Unpin after how many hours?*\n\n"
            "Type a positive number (e.g. `12`):",
            nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_AP_HOURS
    return await _show_summary(update, context)


# ---------------------------------------------------------------------------
# Step 8a/b/c — Lifecycle detail inputs
# ---------------------------------------------------------------------------

async def recv_ad_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    val = _parse_auto_delete_duration(raw)
    if val is None:
        await update.message.reply_text(
            "⚠️ Enter a valid duration such as 5 minutes or 2 hours. Minimum: 5 minutes; maximum: 365 days.",
            reply_markup=nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_AD_HOURS
    w = _w(context)
    w["ad_hours"] = val
    w["ad_duration_label"] = raw
    if w.get("auto_pin"):
        await update.message.reply_text(
            f"✅ Auto-Delete: {raw}.\n\n"
            "📌 *Auto-Pin*\nChoose how long each post stays pinned, or keep it pinned until you remove it manually.",
            reply_markup=_auto_pin_keyboard(),
        )
        return WIZARD_AP_HOURS
    return await _show_summary_from_message(update, context)
async def recv_sd_secs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    if not raw.isdecimal() or not (1 <= int(raw) <= 31536000):
        await update.message.reply_text(
            "⚠️ Enter a whole number from 1 to 31,536,000 seconds (for example, 60).",
            reply_markup=nav_keyboard(back_data=CB_BACK_TW),
        )
        return WIZARD_SD_SECS
    w = _w(context)
    w["sd_secs"] = int(raw)
    if w.get("auto_pin"):
        await update.message.reply_text(
            f"✅ Self-Destruct: {raw} seconds.\n\n"
            "📌 *Auto-Pin*\nChoose how long each post stays pinned, or keep it pinned until you remove it manually.",
            reply_markup=_auto_pin_keyboard(),
        )
        return WIZARD_AP_HOURS
    return await _show_summary_from_message(update, context)
def _auto_pin_keyboard() -> InlineKeyboardMarkup:
    """Choose a timed unpin or leave published posts pinned."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📌 Keep Pinned (No Auto-Unpin)", style="primary", callback_data=CB_AP_FOREVER)],
        [
            InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_LIFECYCLE),
            InlineKeyboardButton("❌ Cancel", style="danger", callback_data=CB_CANCEL),
        ],
    ])


async def cb_ap_forever(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save permanent pinning and display the full review screen."""
    query = update.callback_query
    _w(context)["ap_hours"] = None
    return await _show_summary(update, context)
async def recv_ap_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    try:
        value = float(raw)
        if not math.isfinite(value) or value <= 0 or value > 87600:
            raise ValueError
    except (TypeError, ValueError):
        await update.message.reply_text(
            "⚠️ Enter a finite number greater than 0 and no more than 87,600 hours (for example, 12), or choose Keep Pinned.",
            reply_markup=_auto_pin_keyboard(),
        )
        return WIZARD_AP_HOURS
    _w(context)["ap_hours"] = value
    return await _show_summary_from_message(update, context)
def _build_summary(w: dict[str, Any]) -> str:
    """Build a safe final review from the validated draft."""
    recurrence = w.get("recurrence_type", "once")
    if recurrence == "interval":
        recurrence_line = f"Every {w.get('interval_value')} {w.get('interval_unit')}"
    elif recurrence == "days_of_week":
        days = sorted(w.get("days_of_week", set()))
        recurrence_line = "Weekly on " + ", ".join(DOW_LABELS[d] for d in days if d in range(7))
    else:
        recurrence_line = "One-time"

    timezone_name = str(w.get("timezone", "UTC"))
    try:
        zone = pytz.timezone(timezone_name)
    except (pytz.exceptions.UnknownTimeZoneError, TypeError):
        zone = pytz.UTC
        timezone_name = "UTC"

    first_run = w.get("first_run")
    if isinstance(first_run, datetime) and first_run.tzinfo is not None and first_run.utcoffset() is not None:
        first_run_line = first_run.astimezone(zone).strftime("%d/%m/%Y %H:%M")
    else:
        first_run_line = "Not set"

    if w.get("time_window_enabled"):
        window_line = f"{w.get('tw_start')} to {w.get('tw_end')} ({timezone_name})"
    else:
        window_line = "Off"

    auto_delete_line = (
        str(w.get("ad_duration_label") or f"{w.get('ad_hours')} hours")
        if w.get("auto_delete") else "Off"
    )
    self_destruct_line = (
        f"{w.get('sd_secs')} seconds after publishing"
        if w.get("self_destruct") else "Off"
    )
    if not w.get("auto_pin"):
        auto_pin_line = "Off"
    elif "ap_hours" not in w:
        auto_pin_line = "Not configured"
    elif w.get("ap_hours") is None:
        auto_pin_line = "Keep pinned until manually unpinned"
    else:
        auto_pin_line = f"Unpin after {w.get('ap_hours')} hours"

    if recurrence == "once":
        delivery_line = "1 send"
    elif w.get("max_runs") is None:
        delivery_line = "Unlimited"
    else:
        delivery_line = f"{w.get('max_runs')} sends"

    raw_text = " ".join(str(w.get("content_text") or "").split())
    preview = escape_markdown(raw_text[:100], version=1) if raw_text else ""
    if w.get("media_type"):
        preview = f"[{str(w['media_type']).replace('_', ' ').title()}] {preview}".strip()
    if len(raw_text) > 100:
        preview += "…"
    if not preview:
        preview = "Media post" if w.get("media_file_id") else "Not set"

    title = escape_markdown(str(w.get("chat_title") or "Selected destination"), version=1)
    chat_id = escape_markdown(str(w.get("chat_id") or "—"), version=1)
    tz_display = escape_markdown(timezone_name, version=1)

    return (
        "▰▰▰▰▰▰▰▰ 8/8\n"
        "*Final Review — Schedule New Post*\n\n"
        f"🗂 *Destination:* {title} ({chat_id})\n"
        f"📝 *Content:* {preview}\n"
        f"🔁 *Repeat:* {escape_markdown(recurrence_line, version=1)}\n"
        f"🕐 *First Post:* {first_run_line} ({tz_display})\n"
        f"🔢 *Delivery Limit:* {delivery_line}\n"
        f"🕑 *Posting Window:* {escape_markdown(window_line, version=1)}\n"
        f"🗑 *Auto-Delete:* {escape_markdown(auto_delete_line, version=1)}\n"
        f"💣 *Self-Destruct:* {escape_markdown(self_destruct_line, version=1)}\n"
        f"📌 *Auto-Pin:* {escape_markdown(auto_pin_line, version=1)}\n"
        f"🔘 *Post Buttons:*\n{_inline_btns_list_text(w.get('inline_buttons', []))}\n\n"
        "Review every detail. Nothing is scheduled until you tap Confirm."
    )
async def _show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        _build_summary(_w(context)),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=confirm_keyboard(CB_CONFIRM_POST, CB_BACK_LIFECYCLE),
    )
    return WIZARD_SUMMARY


async def _show_summary_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        _build_summary(_w(context)),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=confirm_keyboard(CB_CONFIRM_POST, CB_BACK_LIFECYCLE),
    )
    return WIZARD_SUMMARY


async def cb_confirm_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the post to MongoDB and end the wizard."""
    query = update.callback_query
    await query.answer("Saving…")

    w = _w(context)
    first_run = w.get("first_run")
    if (
        not isinstance(first_run, datetime)
        or first_run.tzinfo is None
        or first_run.utcoffset() is None
        or first_run.astimezone(timezone.utc) <= datetime.now(tz=timezone.utc)
    ):
        language = context.user_data.get("language", "en")
        await query.edit_message_text(
            "⚠️ The selected first-run time has passed. Please enter a new future time.\n\n"
            "Use `HH:MM` or `DD/MM/YYYY HH:MM`.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_first_run_keyboard(CB_BACK_TIMEZONE),
        )
        return WIZARD_FIRST_RUN
    user_id: int = query.from_user.id  # type: ignore[union-attr]

    try:
        doc = build_scheduled_post(
            user_id=user_id,
            chat_id=w["chat_id"],
            content_text=w.get("content_text"),
            content_media_file_id=w.get("media_file_id"),
            content_media_type=w.get("media_type"),
            timezone_str=w.get("timezone", "UTC"),
            recurrence_type=w.get("recurrence_type", "once"),
            interval_value=w.get("interval_value"),
            interval_unit=w.get("interval_unit"),
            days_of_week=sorted(w["days_of_week"]) if w.get("days_of_week") else None,
            next_run_at=w.get("first_run"),
            max_runs=w.get("max_runs"),
            time_window_enabled=w.get("time_window_enabled", False),
            time_window_start=w.get("tw_start"),
            time_window_end=w.get("tw_end"),
            auto_delete_enabled=w.get("auto_delete", False),
            auto_delete_after_hours=w.get("ad_hours"),
            self_destruct_enabled=w.get("self_destruct", False),
            self_destruct_after_seconds=w.get("sd_secs"),
            auto_pin_enabled=w.get("auto_pin", False),
            auto_pin_unpin_after_hours=w.get("ap_hours"),
            inline_keyboard=w.get("inline_buttons") or None,
        )
        post_id = await insert_post(doc)
    except Exception as exc:
        logger.exception("Failed to save post: %s", exc)
        _clear(context)
        chat_id = query.message.chat_id
        await chat_cleanup.cleanup(context, chat_id, keep_message_id=query.message.message_id)
        await query.edit_message_text(
            f"❌ Failed to save post: `{str(exc).replace(chr(96), chr(39))}`\n\nTry again or contact support.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    _clear(context)
    chat_id = query.message.chat_id
    await chat_cleanup.cleanup(context, chat_id, keep_message_id=query.message.message_id)
    await query.edit_message_text(
        f"✅ *Post scheduled successfully!*\n\n"
        f"🆔 Post ID: `{post_id}`\n\n"
        "Use *My Posts* to manage it, or start another task below.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Main Menu", style="primary", callback_data=CB_MAIN_MENU)],
        ]),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Back-navigation helpers
# ---------------------------------------------------------------------------

async def cb_back_recurrence(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _edit(query,
        tr(context.user_data.get("language", "en"), "recurrence_prompt"),
        _recurrence_keyboard(context.user_data.get("language", "en")),
    )
    return WIZARD_RECURRENCE


async def cb_back_dow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    await _edit(query,
        "📆 *Step 3a — Days of Week*\n\nTap days to toggle, then ➡️ Next.",
        _dow_keyboard(w.get("days_of_week", set())),
    )
    return WIZARD_DOW_SELECT


async def cb_back_interval_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    await _edit(query,
        f"✅ Value: `{w.get('interval_value')}`\n\n"
        "⏱ *Step 3b — Interval Unit*\n\nChoose the time unit:",
        _interval_unit_keyboard(),
    )
    return WIZARD_INTERVAL_UNIT


async def cb_back_max_runs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    one_time = w.get("recurrence_type") == "once"
    if one_time:
        prompt = (
            "🔢 *One-time delivery*\n"
            "This post will be sent once. Confirm the one-send setting to continue."
        )
    else:
        prompt = tr(context.user_data.get("language", "en"), "max_runs_prompt")
    await _edit(query, prompt, _max_runs_keyboard(one_time=one_time))
    return WIZARD_MAX_RUNS
async def cb_back_tw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Go back to the Advanced Options screen (from TW_START or lifecycle detail)."""
    query = update.callback_query
    w = _w(context)
    await _edit(query,
        "▰▰▰▰▰▰▰▱ 7/8\nChat › Content › Recurrence › Timezone › 1st Run › Max Runs › *Advanced* › Summary\n⚙️ *Step 7/8 — Advanced Options*\n\n"
        "Toggle optional features on/off, then tap *➡️ Next*:",
        _advanced_keyboard(w.get("time_window_enabled", False),
                           w.get("auto_delete", False), w.get("self_destruct", False),
                           w.get("auto_pin", False)),
    )
    return WIZARD_TIME_WINDOW


async def cb_back_tw_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _edit(query,
        "🕐 *Step 7a — Window Start Time*\n\n"
        "Type the earliest time posts should fire (e.g. `08:00`):",
        nav_keyboard(back_data=CB_BACK_TW),
    )
    return WIZARD_TW_START


async def cb_back_lifecycle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Back from Summary or lifecycle detail → return to Advanced Options screen."""
    query = update.callback_query
    w = _w(context)
    await _edit(query,
        "▰▰▰▰▰▰▰▱ 7/8\nChat › Content › Recurrence › Timezone › 1st Run › Max Runs › *Advanced* › Summary\n⚙️ *Step 7/8 — Advanced Options*\n\n"
        "Toggle optional features on/off, then tap *➡️ Next*:",
        _advanced_keyboard(w.get("time_window_enabled", False),
                           w.get("auto_delete", False), w.get("self_destruct", False),
                           w.get("auto_pin", False)),
    )
    return WIZARD_TIME_WINDOW


# ---------------------------------------------------------------------------
# Cancel (duplicated locally for convenience)
# ---------------------------------------------------------------------------

async def cb_cancel_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Cancelled.")
    _clear(context)
    chat_id = query.message.chat_id
    user_id = query.from_user.id if query.from_user else None
    await chat_cleanup.cleanup(context, chat_id, keep_message_id=query.message.message_id)
    await replace_with_main_menu_message(
        query,
        context,
        "❌ *Cancelled.* Back to Main Menu.",
        user_id,
        "en",
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# ConversationHandler factory
# ---------------------------------------------------------------------------

def build_schedule_wizard() -> ConversationHandler:
    dow_pattern  = r"^dow:[0-6]$"
    tz_pattern   = r"^tz:(?!page:).+"
    tzp_pattern  = r"^tz:page:\d+$"

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(enter_wizard, pattern=f"^{CB_SCHEDULE_NEW}$")],
        states={
            WIZARD_INTRO: [
                CallbackQueryHandler(cb_wizard_continue, pattern=f"^{CB_WIZARD_CONTINUE}$"),
            ],
            WIZARD_CHAT_ID: [
                CallbackQueryHandler(cb_back_chat, pattern=f"^{CB_BACK_CHAT}$"),
                CallbackQueryHandler(cb_chat_pick,       pattern=r"^chat:pick:-?\d+$"),
                CallbackQueryHandler(cb_chat_page,       pattern=r"^chat:page:\d+$"),
                CallbackQueryHandler(cb_manual_chat_id,  pattern=f"^{CB_MANUAL_CHAT_ID}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_chat_id),
            ],
            WIZARD_CONTENT: [
                CallbackQueryHandler(cb_content_keep, pattern=f"^{CB_CONTENT_KEEP}$"),
                CallbackQueryHandler(cb_back_chat, pattern=f"^{CB_BACK_CHAT}$"),
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.VIDEO |
                     filters.Document.ALL | filters.AUDIO | filters.ANIMATION |
                     filters.VOICE) & ~filters.COMMAND,
                    recv_content,
                ),
            ],
            WIZARD_INLINE_BTNS: [
                CallbackQueryHandler(cb_ib_skip, pattern=f"^{CB_IB_SKIP}$"),
                CallbackQueryHandler(cb_ib_add,  pattern=f"^{CB_IB_ADD}$"),
                CallbackQueryHandler(cb_ib_done, pattern=f"^{CB_IB_DONE}$"),
                CallbackQueryHandler(cb_back_content, pattern=f"^{CB_BACK_CONTENT}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_ib_entry),
            ],
            WIZARD_RECURRENCE: [
                CallbackQueryHandler(cb_back_inline_buttons, pattern=f"^{CB_BACK_INLINE_BTNS}$"),
                CallbackQueryHandler(cb_rec_once,     pattern=f"^{CB_REC_ONCE}$"),
                CallbackQueryHandler(cb_rec_interval, pattern=f"^{CB_REC_INTERVAL}$"),
                CallbackQueryHandler(cb_rec_dow,      pattern=f"^{CB_REC_DOW}$"),
                CallbackQueryHandler(cb_back_recurrence, pattern=f"^{CB_BACK_RECURRENCE}$"),
            ],
            WIZARD_INTERVAL_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_interval_value),
                CallbackQueryHandler(cb_back_recurrence, pattern=f"^{CB_BACK_RECURRENCE}$"),
            ],
            WIZARD_INTERVAL_UNIT: [
                CallbackQueryHandler(cb_interval_unit,    pattern=f"^({CB_IU_MINUTES}|{CB_IU_HOURS}|{CB_IU_DAYS})$"),
                CallbackQueryHandler(cb_back_interval_value, pattern=f"^{CB_BACK_INTERVAL_V}$"),
            ],
            WIZARD_DOW_SELECT: [
                CallbackQueryHandler(cb_dow_toggle,      pattern=dow_pattern),
                CallbackQueryHandler(cb_dow_next,        pattern=f"^{CB_DOW_NEXT}$"),
                CallbackQueryHandler(cb_back_recurrence, pattern=f"^{CB_BACK_RECURRENCE}$"),
            ],
            WIZARD_FIRST_RUN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_first_run),
                CallbackQueryHandler(cb_smart_suggest,  pattern=f"^{CB_SMART_SUGGEST}$"),
                CallbackQueryHandler(cb_smart_pick,     pattern=r"^smart:pick:\d{2}:\d{2}$"),
                # Smart-suggest sub-screen "Enter custom time" / "Back" → re-show first run
                CallbackQueryHandler(cb_back_first_run, pattern=f"^{CB_BACK_FIRST_RUN}$"),
                # Back from First Run → Timezone (timezone is now step 4, first run is step 5)
                CallbackQueryHandler(cb_back_timezone,  pattern=f"^{CB_BACK_TIMEZONE}$"),
            ],
            WIZARD_TIMEZONE: [
                CallbackQueryHandler(cb_timezone_pick, pattern=tz_pattern),
                CallbackQueryHandler(cb_timezone_page, pattern=tzp_pattern),
                # Back from Timezone → smart-routes to recurrence/interval-unit/DOW
                CallbackQueryHandler(cb_back_tz,       pattern=f"^{CB_BACK_TZ}$"),
            ],
            WIZARD_MAX_RUNS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_max_runs),
                # Quick-tap preset buttons (5, 10, 30, unlimited)
                CallbackQueryHandler(cb_mr_preset,      pattern=rf"^{re.escape(CB_MR_PRESET)}\d+$"),
                # Back from Max Runs → First Run (first run is now step 5)
                CallbackQueryHandler(cb_back_first_run, pattern=f"^{CB_BACK_FIRST_RUN}$"),
            ],
            WIZARD_TIME_WINDOW: [
                # Silent-hours toggle buttons
                CallbackQueryHandler(cb_tw_toggle,  pattern=f"^({CB_TW_ON}|{CB_TW_OFF})$"),
                # Lifecycle toggle buttons (now on the same Advanced Options screen)
                CallbackQueryHandler(cb_lc_toggle,  pattern=f"^({CB_LC_AD}|{CB_LC_SD}|{CB_LC_AP})$"),
                # Next → route to detail steps or summary
                CallbackQueryHandler(cb_adv_next,   pattern=f"^{CB_LC_NEXT}$"),
                # Back to Max Runs
                CallbackQueryHandler(cb_back_max_runs, pattern=f"^{CB_BACK_MAX_RUNS}$"),
            ],
            WIZARD_TW_START: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_tw_start),
                CallbackQueryHandler(cb_back_tw, pattern=f"^{CB_BACK_TW}$"),
            ],
            WIZARD_TW_END: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_tw_end),
                CallbackQueryHandler(cb_back_tw_start, pattern=f"^{CB_BACK_TW_START}$"),
            ],
            WIZARD_AD_HOURS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_ad_hours),
                # Back → Advanced Options screen
                CallbackQueryHandler(cb_back_tw, pattern=f"^{CB_BACK_TW}$"),
                CallbackQueryHandler(cb_back_lifecycle, pattern=f"^{CB_BACK_LIFECYCLE}$"),
            ],
            WIZARD_SD_SECS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_sd_secs),
                CallbackQueryHandler(cb_back_tw, pattern=f"^{CB_BACK_TW}$"),
                CallbackQueryHandler(cb_back_lifecycle, pattern=f"^{CB_BACK_LIFECYCLE}$"),
            ],
            WIZARD_AP_HOURS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_ap_hours),
                CallbackQueryHandler(cb_back_tw, pattern=f"^{CB_BACK_TW}$"),
                CallbackQueryHandler(cb_back_lifecycle, pattern=f"^{CB_BACK_LIFECYCLE}$"),
                CallbackQueryHandler(cb_ap_forever, pattern=f"^{CB_AP_FOREVER}$"),
            ],
            WIZARD_SUMMARY: [
                CallbackQueryHandler(cb_confirm_post,   pattern=f"^{CB_CONFIRM_POST}$"),
                CallbackQueryHandler(cb_back_lifecycle, pattern=f"^{CB_BACK_LIFECYCLE}$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel_wizard, pattern=f"^{CB_CANCEL}$"),
        ],
        allow_reentry=True,
        name="schedule_wizard",
        persistent=False,
    )
