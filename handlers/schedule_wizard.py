"""
handlers/schedule_wizard.py — Guided Schedule New Post conversation.

User journey:
  1. Destination — select a saved group/channel or enter its Chat ID.
  2. Content — send text or one supported media message.
  2a. Optional post buttons — add up to 10 buttons using a guided label/link flow.
  3. Recurrence — one-time, interval, or selected weekdays.
  4. Timezone — choose the timezone used for every posting time.
  5. First post — enter a future HH:MM time or exact DD/MM/YYYY HH:MM date.
  6. Delivery limit — one-time, preset/custom count, or unlimited.
  7. Advanced options — posting window, one delete mode, and optional pinning.
  8. Final review — revalidate the draft and destination before saving.

The wizard keeps the draft while navigating backwards. Nothing is saved until
the final confirmation has passed validation and the destination permission
check.
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
from schedule_flow_localization import translate_schedule_flow as flow_tr
from schedule_utils import parse_schedule_datetime

logger = logging.getLogger(__name__)


def _language(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("language", "en")


def _wizard_nav_keyboard(context: ContextTypes.DEFAULT_TYPE, back_data: str | None = None) -> InlineKeyboardMarkup:
    return nav_keyboard(back_data=back_data, language=_language(context))


def _wizard_confirm_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    return confirm_keyboard(CB_CONFIRM_POST, CB_BACK_LIFECYCLE, language=_language(context))


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

def _locale_weekdays(language: str) -> list[str]:
    labels = flow_tr(language, "weekdays").split(", ")
    return labels if len(labels) == 7 else ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _chat_list_keyboard(
    groups: list[dict],
    page: int,
    bot_username: str,
    language: str = "en",
) -> InlineKeyboardMarkup:
    """Build a localized group/channel picker with stable callback contracts."""
    rows: list[list[InlineKeyboardButton]] = []
    total = len(groups)
    start = page * _CHAT_PAGE_SIZE
    end = min(start + _CHAT_PAGE_SIZE, total)
    type_icon = {"channel": "📢", "supergroup": "👥", "group": "👥"}
    for group in groups[start:end]:
        icon = type_icon.get(group.get("chat_type", ""), "💬")
        title = (group.get("title") or f"Chat {group['chat_id']}")[:35]
        rows.append([InlineKeyboardButton(
            f"{icon} {title}",
            style="primary",
            callback_data=f"{CB_CHAT_PICK_PREFIX}{group['chat_id']}",
        )])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            f"⬅️ {tr(language, 'previous')}",
            style="primary",
            callback_data=f"{CB_CHAT_PAGE_PREFIX}{page - 1}",
        ))
    if end < total:
        nav.append(InlineKeyboardButton(
            f"{tr(language, 'next')} ➡️",
            style="primary",
            callback_data=f"{CB_CHAT_PAGE_PREFIX}{page + 1}",
        ))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(
        tr(language, "manual_chat_id"), style="success", callback_data=CB_MANUAL_CHAT_ID
    )])
    rows.append([InlineKeyboardButton(
        tr(language, "cancel"), style="danger", callback_data=CB_CANCEL
    )])
    return InlineKeyboardMarkup(rows)


def _no_groups_keyboard(bot_username: str, language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            flow_tr(language, "add_group"),
            style="primary",
            url=f"https://t.me/{bot_username}?startgroup=start",
        )],
        [InlineKeyboardButton(
            tr(language, "manual_chat_id"), style="success", callback_data=CB_MANUAL_CHAT_ID
        )],
        [InlineKeyboardButton(
            tr(language, "cancel"), style="danger", callback_data=CB_CANCEL
        )],
    ])


def _first_run_keyboard(back_data: str, language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            flow_tr(language, "suggest_time"), style="primary", callback_data=CB_SMART_SUGGEST
        )],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=back_data),
            InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
        ],
    ])


def _recurrence_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr(language, "recurrence_interval"), style="primary", callback_data=CB_REC_INTERVAL)],
        [InlineKeyboardButton(tr(language, "recurrence_days"), style="primary", callback_data=CB_REC_DOW)],
        [InlineKeyboardButton(tr(language, "recurrence_once"), style="primary", callback_data=CB_REC_ONCE)],
        [
            InlineKeyboardButton(flow_tr(language, "back_post_buttons"), callback_data=CB_BACK_INLINE_BTNS),
            InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
        ],
    ])


def _interval_unit_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(flow_tr(language, "unit_minutes"), style="primary", callback_data=CB_IU_MINUTES),
            InlineKeyboardButton(flow_tr(language, "unit_hours"), style="primary", callback_data=CB_IU_HOURS),
            InlineKeyboardButton(flow_tr(language, "unit_days"), style="primary", callback_data=CB_IU_DAYS),
        ],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_INTERVAL_V),
            InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
        ],
    ])


def _dow_keyboard(selected: set[int], language: str = "en") -> InlineKeyboardMarkup:
    """Build a localized seven-day toggle keyboard."""
    labels = _locale_weekdays(language)
    rows: list[list[InlineKeyboardButton]] = []
    for start, end in ((0, 4), (4, 7)):
        rows.append([
            InlineKeyboardButton(
                f"{'✅' if day in selected else '☐'} {labels[day]}",
                style="success" if day in selected else "primary",
                callback_data=_dow_cb(day),
            )
            for day in range(start, end)
        ])
    rows.append([
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_RECURRENCE),
        InlineKeyboardButton(tr(language, "next"), style="success", callback_data=CB_DOW_NEXT),
        InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _timezone_keyboard(page: int = 0, language: str = "en") -> InlineKeyboardMarkup:
    start = page * TZ_PAGE_SIZE
    end = start + TZ_PAGE_SIZE
    page_timezones = TIMEZONES[start:end]
    rows = [
        [InlineKeyboardButton(label, style="primary", callback_data=f"tz:{tz_name}")]
        for tz_name, label in page_timezones
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            tr(language, "previous"), style="primary", callback_data=f"{CB_TZ_PAGE}{page - 1}"
        ))
    if end < len(TIMEZONES):
        nav.append(InlineKeyboardButton(
            tr(language, "next"), style="primary", callback_data=f"{CB_TZ_PAGE}{page + 1}"
        ))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_TZ),
        InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _time_window_keyboard(enabled: bool, language: str = "en") -> InlineKeyboardMarkup:
    on_icon = f"🟢 {flow_tr(language, 'window_on')} ✅" if enabled else f"🔘 {flow_tr(language, 'window_on')}"
    off_icon = f"🔴 {flow_tr(language, 'window_off')} ✅" if not enabled else f"🔘 {flow_tr(language, 'window_off')}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(on_icon, style="success" if enabled else "primary", callback_data=CB_TW_ON),
            InlineKeyboardButton(off_icon, style="success" if not enabled else "primary", callback_data=CB_TW_OFF),
        ],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MAX_RUNS),
            InlineKeyboardButton(tr(language, "next"), style="success", callback_data=CB_LC_NEXT),
            InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
        ],
    ])


def _lifecycle_keyboard(ad: bool, sd: bool, ap: bool, language: str = "en") -> InlineKeyboardMarkup:
    flag = lambda enabled: "✅" if enabled else "☐"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"{flag(ad)} {flow_tr(language, 'auto_delete_label')}",
            style="success" if ad else "primary", callback_data=CB_LC_AD
        )],
        [InlineKeyboardButton(
            f"{flag(sd)} {flow_tr(language, 'self_destruct_label')}",
            style="success" if sd else "primary", callback_data=CB_LC_SD
        )],
        [InlineKeyboardButton(
            f"{flag(ap)} {flow_tr(language, 'auto_pin_label')}",
            style="success" if ap else "primary", callback_data=CB_LC_AP
        )],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_TW),
            InlineKeyboardButton(tr(language, "next"), style="success", callback_data=CB_LC_NEXT),
            InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
        ],
    ])


def _advanced_keyboard(
    tw: bool, ad: bool, sd: bool, ap: bool, language: str = "en"
) -> InlineKeyboardMarkup:
    """Localized optional controls for posting windows and post lifecycle actions."""
    toggle = lambda enabled, key: (
        f"{'🟢' if enabled else '🔘'} {flow_tr(language, key)}"
    )
    flag = lambda enabled: "✅" if enabled else "☐"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            toggle(tw, "window_on" if tw else "window_off"),
            style="success" if tw else "primary",
            callback_data=CB_TW_OFF if tw else CB_TW_ON,
        )],
        [InlineKeyboardButton(
            f"{flag(ad)} {flow_tr(language, 'auto_delete_label')}",
            style="success" if ad else "primary", callback_data=CB_LC_AD
        )],
        [InlineKeyboardButton(
            f"{flag(sd)} {flow_tr(language, 'self_destruct_label')}",
            style="success" if sd else "primary", callback_data=CB_LC_SD
        )],
        [InlineKeyboardButton(
            f"{flag(ap)} {flow_tr(language, 'auto_pin_label')}",
            style="success" if ap else "primary", callback_data=CB_LC_AP
        )],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MAX_RUNS),
            InlineKeyboardButton(tr(language, "next"), style="success", callback_data=CB_LC_NEXT),
            InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
        ],
    ])


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DT_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})$")


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
    """Continue from localized onboarding into destination selection or a linked group."""
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
            await query.answer(flow_tr(language, "linked_unavailable"), show_alert=True)
            answered = True
        else:
            draft = _w(context)
            draft["chat_id"] = launch_chat_id
            draft["chat_title"] = target_chat.title or str(launch_chat_id)
            await query.answer()
            await query.edit_message_text(
                _content_prompt_text(draft, language),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_content_step_keyboard(
                    bool(draft.get("content_text") or draft.get("media_file_id")), language
                ),
            )
            return WIZARD_CONTENT

    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id)
    if not answered:
        await query.answer()
    header = f"▰▱▱▱▱▱▱▱ 1/8\n*{flow_tr(language, 'destination_title')}*\n\n{tr(language, 'choose_group')}"
    if groups:
        text = (
            f"{header}\n\n"
            f"{flow_tr(language, 'destination_count', shown=min(_CHAT_PAGE_SIZE, len(groups)), total=len(groups))}"
        )
        keyboard = _chat_list_keyboard(groups, 0, bot_username, language)
    else:
        text = f"{header}\n\n{flow_tr(language, 'no_groups')}"
        keyboard = _no_groups_keyboard(bot_username, language)
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
    return WIZARD_CHAT_ID

async def cb_chat_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate the chosen destination and proceed to localized content instructions."""
    query = update.callback_query
    raw = (query.data or "").replace(CB_CHAT_PICK_PREFIX, "", 1)
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    try:
        chat_id = int(raw)
    except ValueError:
        await query.answer(flow_tr(language, "invalid_chat_id"), show_alert=True)
        return WIZARD_CHAT_ID
    groups = await list_groups_for_user(user_id) if user_id is not None else []
    titles = {group["chat_id"]: (group.get("title") or str(group["chat_id"])) for group in groups}
    try:
        target_chat = await ensure_chat_access(query.get_bot(), chat_id, user_id)
    except ChatAccessError:
        await query.answer(flow_tr(language, "linked_unavailable"), show_alert=True)
        return WIZARD_CHAT_ID
    draft = _w(context)
    draft["chat_id"] = chat_id
    draft["chat_title"] = target_chat.title or titles.get(chat_id, str(chat_id))
    await query.answer()
    await query.edit_message_text(
        _content_prompt_text(draft, language),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_content_step_keyboard(
            bool(draft.get("content_text") or draft.get("media_file_id")), language
        ),
    )
    return WIZARD_CONTENT

def _content_step_keyboard(
    has_content: bool = False, language: str = "en"
) -> InlineKeyboardMarkup:
    """Content actions with localized labels and an explicit route to the destination."""
    rows: list[list[InlineKeyboardButton]] = []
    if has_content:
        rows.append([InlineKeyboardButton(
            flow_tr(language, "keep_content"), style="success", callback_data=CB_CONTENT_KEEP
        )])
    rows.append([
        InlineKeyboardButton(flow_tr(language, "back_content"), callback_data=CB_BACK_CHAT),
        InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)

def _content_prompt_text(w: dict[str, Any], language: str = "en") -> str:
    """Build the localized content step without changing the message-storage contract."""
    title = escape_markdown(str(w.get("chat_title") or w.get("chat_id") or "Selected chat"), version=1)
    return (
        "▰▱▱▱▱▱▱▱ 2/8\n"
        f"{tr(language, 'schedule_content_title')}\n\n"
        f"📍 *{flow_tr(language, 'destination_title').split('—')[-1].strip()}:* {title}\n\n"
        f"{tr(language, 'schedule_content_prompt')}\n\n"
        f"{flow_tr(language, 'content_rules')}"
    )

def _inline_buttons_prompt(w: dict[str, Any], language: str = "en") -> str:
    """Explain the optional button flow in the user's selected language."""
    count = len(w.get("inline_buttons", []))
    return (
        f"{flow_tr(language, 'buttons_intro')}\n\n"
        f"{flow_tr(language, 'button_guide')}\n\n"
        f"*{count}/10*"
    )

async def cb_back_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to destination selection without discarding saved draft data."""
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id) if user_id is not None else []
    header = f"▰▱▱▱▱▱▱▱ 1/8\n*{flow_tr(language, 'destination_title')}*\n\n{tr(language, 'choose_group')}"
    if groups:
        text = header
        keyboard = _chat_list_keyboard(groups, 0, bot_username, language)
    else:
        text = f"{header}\n\n{flow_tr(language, 'no_groups')}"
        keyboard = _no_groups_keyboard(bot_username, language)
    await query.answer()
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
    return WIZARD_CHAT_ID

async def cb_back_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to saved content; users can keep it or replace it."""
    query = update.callback_query
    draft = _w(context)
    draft.pop("pending_inline_button_label", None)
    has_content = bool(draft.get("content_text") or draft.get("media_file_id"))
    language = await resolve_user_language(
        context, query.from_user.id if query.from_user else None
    )
    prompt = _content_prompt_text(draft, language)
    if has_content:
        prompt += f"\n\n✅ {flow_tr(language, 'keep_content')}"
    await query.answer()
    await query.edit_message_text(
        prompt,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_content_step_keyboard(has_content, language),
    )
    return WIZARD_CONTENT

async def cb_content_keep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Continue with saved content without asking the user to send it again."""
    query = update.callback_query
    draft = _w(context)
    language = await resolve_user_language(
        context, query.from_user.id if query.from_user else None
    )
    if not (draft.get("content_text") or draft.get("media_file_id")):
        await query.answer(tr(language, "schedule_content_prompt"), show_alert=True)
        return WIZARD_CONTENT
    draft.setdefault("inline_buttons", [])
    await query.answer()
    await query.edit_message_text(
        _inline_buttons_prompt(draft, language),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_inline_btns_keyboard(len(draft["inline_buttons"]), language),
    )
    return WIZARD_INLINE_BTNS

async def cb_chat_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paginate the localized group list."""
    query = update.callback_query
    raw = (query.data or "").replace(CB_CHAT_PAGE_PREFIX, "", 1)
    page = int(raw) if raw.isdigit() else 0
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id)
    total = len(groups)
    shown = min(_CHAT_PAGE_SIZE, max(0, total - page * _CHAT_PAGE_SIZE))
    text = (
        f"▰▱▱▱▱▱▱▱ 1/8\n*{flow_tr(language, 'destination_title')}*\n\n"
        f"{tr(language, 'choose_group')}\n\n"
        f"{flow_tr(language, 'destination_count', shown=shown, total=total)}"
    )
    await query.answer()
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_chat_list_keyboard(groups, page, bot_username, language),
    )
    return WIZARD_CHAT_ID

async def cb_manual_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Offer translated manual destination entry."""
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    await query.answer()
    await query.edit_message_text(
        f"▰▱▱▱▱▱▱▱ 1/8\n*{flow_tr(language, 'destination_title')}*\n\n"
        f"{flow_tr(language, 'manual_guide')}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_wizard_nav_keyboard(context, CB_BACK_CHAT),
    )
    return WIZARD_CHAT_ID

async def recv_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    language = _language(context)
    try:
        chat_id = int(raw)
    except ValueError:
        await update.message.reply_text(
            flow_tr(language, "invalid_chat_id"),
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_CHAT),
        )
        return WIZARD_CHAT_ID
    if chat_id >= 0:
        await update.message.reply_text(
            flow_tr(language, "negative_chat_id"),
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_CHAT),
        )
        return WIZARD_CHAT_ID
    try:
        target_chat = await ensure_chat_access(context.bot, chat_id, update.effective_user.id)
    except ChatAccessError:
        await update.message.reply_text(
            flow_tr(language, "linked_unavailable"),
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_CHAT),
        )
        return WIZARD_CHAT_ID
    draft = _w(context)
    draft["chat_id"] = chat_id
    draft["chat_title"] = target_chat.title or str(chat_id)
    await update.message.reply_text(
        _content_prompt_text(draft, language),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_content_step_keyboard(
            bool(draft.get("content_text") or draft.get("media_file_id")), language
        ),
    )
    return WIZARD_CONTENT

async def recv_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save one supported Telegram message as scheduled post content."""
    message: Message = update.message
    draft = _w(context)
    language = _language(context)
    media_file_id = None
    media_type = None
    content_text = None
    if message.photo:
        media_file_id, media_type, content_text = message.photo[-1].file_id, "photo", message.caption
    elif message.video:
        media_file_id, media_type, content_text = message.video.file_id, "video", message.caption
    elif message.document:
        media_file_id, media_type, content_text = message.document.file_id, "document", message.caption
    elif message.audio:
        media_file_id, media_type, content_text = message.audio.file_id, "audio", message.caption
    elif message.animation:
        media_file_id, media_type, content_text = message.animation.file_id, "animation", message.caption
    elif message.voice:
        media_file_id, media_type, content_text = message.voice.file_id, "voice", message.caption
    elif message.text and message.text.strip():
        content_text = message.text
    else:
        await message.reply_text(
            f"⚠️ {tr(language, 'schedule_content_prompt')}",
            reply_markup=_content_step_keyboard(
                bool(draft.get("content_text") or draft.get("media_file_id")), language
            ),
        )
        return WIZARD_CONTENT

    draft["media_file_id"] = media_file_id
    draft["media_type"] = media_type
    draft["content_text"] = content_text
    draft.setdefault("inline_buttons", [])
    await message.reply_text(
        _inline_buttons_prompt(draft, language),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_inline_btns_keyboard(len(draft["inline_buttons"]), language),
    )
    return WIZARD_INLINE_BTNS

def _inline_btns_keyboard(count: int, language: str = "en") -> InlineKeyboardMarkup:
    """Localized optional post-button controls."""
    rows: list[list[InlineKeyboardButton]] = []
    if count == 0:
        rows.append([
            InlineKeyboardButton(flow_tr(language, "add_button"), style="primary", callback_data=CB_IB_ADD),
            InlineKeyboardButton(flow_tr(language, "skip"), style="primary", callback_data=CB_IB_SKIP),
        ])
    elif count >= 10:
        rows.append([InlineKeyboardButton(
            flow_tr(language, "done"), style="success", callback_data=CB_IB_DONE
        )])
    else:
        rows.append([
            InlineKeyboardButton(flow_tr(language, "add_another"), style="primary", callback_data=CB_IB_ADD),
            InlineKeyboardButton(flow_tr(language, "done"), style="success", callback_data=CB_IB_DONE),
        ])
    rows.append([
        InlineKeyboardButton(flow_tr(language, "back_content"), callback_data=CB_BACK_CONTENT),
        InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)

async def cb_ib_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Skip optional post buttons and continue to localized recurrence choices."""
    query = update.callback_query
    language = _language(context)
    _w(context).pop("pending_inline_button_label", None)
    _w(context).setdefault("inline_buttons", [])
    await _edit(query, tr(language, "recurrence_prompt"), _recurrence_keyboard(language))
    return WIZARD_RECURRENCE

async def cb_ib_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the guided two-step post-button entry flow."""
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    if len(draft.get("inline_buttons", [])) >= 10:
        await query.answer(flow_tr(language, "button_limit"), show_alert=True)
        return WIZARD_INLINE_BTNS
    draft.pop("pending_inline_button_label", None)
    await _edit(
        query,
        flow_tr(language, "button_guide"),
        _wizard_nav_keyboard(context, CB_BACK_CONTENT),
    )
    return WIZARD_INLINE_BTNS

async def recv_ib_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Accept a guided label/link pair or the legacy Label | URL shortcut."""
    text = (update.message.text or "").strip()
    draft = _w(context)
    language = _language(context)
    draft.setdefault("inline_buttons", [])
    pending_label = draft.get("pending_inline_button_label")

    if "|" not in text:
        if pending_label is None:
            if not text or "\n" in text or "\r" in text:
                await update.message.reply_text(
                    flow_tr(language, "button_label_invalid"),
                    reply_markup=_wizard_nav_keyboard(context, CB_BACK_CONTENT),
                )
                return WIZARD_INLINE_BTNS
            if len(text) > 64:
                await update.message.reply_text(
                    flow_tr(language, "button_label_long"),
                    reply_markup=_wizard_nav_keyboard(context, CB_BACK_CONTENT),
                )
                return WIZARD_INLINE_BTNS
            draft["pending_inline_button_label"] = text
            await update.message.reply_text(
                f"✅ {escape_markdown(text, version=1)}\n\n{flow_tr(language, 'button_link_prompt')}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_wizard_nav_keyboard(context, CB_BACK_CONTENT),
            )
            return WIZARD_INLINE_BTNS

        url = text
        if url.startswith("@"):
            username = url[1:]
            url = "https://t.me/" + username if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) else ""
        elif not url.startswith(("http://", "https://", "tg://")) and "." in url and " " not in url:
            url = "https://" + url
        if not url or " " in url or not url.startswith(("http://", "https://", "tg://")):
            await update.message.reply_text(
                flow_tr(language, "button_link_invalid"),
                reply_markup=_wizard_nav_keyboard(context, CB_BACK_CONTENT),
            )
            return WIZARD_INLINE_BTNS
        if len(draft["inline_buttons"]) >= 10:
            draft.pop("pending_inline_button_label", None)
            await update.message.reply_text(
                flow_tr(language, "button_limit"),
                reply_markup=_inline_btns_keyboard(10, language),
            )
            return WIZARD_INLINE_BTNS

        draft["inline_buttons"].append([{"text": pending_label, "url": url}])
        draft.pop("pending_inline_button_label", None)
        count = len(draft["inline_buttons"])
        next_step = (
            flow_tr(language, "button_limit") if count == 10
            else f"{flow_tr(language, 'add_another')} / {flow_tr(language, 'done')}"
        )
        await update.message.reply_text(
            f"{flow_tr(language, 'button_added', count=count)}\n\n"
            f"*{flow_tr(language, 'done')}:*\n{_inline_btns_list_text(draft['inline_buttons'], language)}\n\n"
            f"{next_step}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_inline_btns_keyboard(count, language),
        )
        return WIZARD_INLINE_BTNS

    added = 0
    errors: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            errors.append(flow_tr(language, "button_label_invalid"))
            continue
        label, _, url = line.partition("|")
        label, url = label.strip(), url.strip()
        if not label or len(label) > 64:
            errors.append(flow_tr(language, "button_label_long"))
            continue
        if url.startswith("@"):
            username = url[1:]
            url = "https://t.me/" + username if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) else ""
        elif not url.startswith(("http://", "https://", "tg://")) and "." in url and " " not in url:
            url = "https://" + url
        if not url or " " in url or not url.startswith(("http://", "https://", "tg://")):
            errors.append(flow_tr(language, "button_link_invalid"))
            continue
        if len(draft["inline_buttons"]) >= 10:
            errors.append(flow_tr(language, "button_limit"))
            break
        draft["inline_buttons"].append([{"text": label, "url": url}])
        added += 1

    result = [flow_tr(language, "button_added", count=added)]
    if errors:
        result.append("⚠️\n" + "\n".join(errors))
    result.append(f"*{len(draft['inline_buttons'])}/10*")
    result.append(_inline_btns_list_text(draft["inline_buttons"], language))
    result.append(f"{flow_tr(language, 'add_another')} / {flow_tr(language, 'done')}")
    await update.message.reply_text(
        "\n\n".join(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_inline_btns_keyboard(len(draft["inline_buttons"]), language),
    )
    return WIZARD_INLINE_BTNS

async def cb_back_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return from recurrence selection to the optional post-button step."""
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    draft.pop("pending_inline_button_label", None)
    await query.answer()
    await query.edit_message_text(
        _inline_buttons_prompt(draft, language),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_inline_btns_keyboard(len(draft.get("inline_buttons", [])), language),
    )
    return WIZARD_INLINE_BTNS

async def cb_ib_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Finish optional post buttons and move to recurrence selection."""
    query = update.callback_query
    language = _language(context)
    _w(context).pop("pending_inline_button_label", None)
    await _edit(query, tr(language, "recurrence_prompt"), _recurrence_keyboard(language))
    return WIZARD_RECURRENCE

def _inline_btns_list_text(buttons: list, language: str = "en") -> str:
    """Render button labels and URLs safely in a Markdown message."""
    lines: list[str] = []
    for row in buttons:
        for button in row:
            label = escape_markdown(str(button.get("text", "")), version=1)
            url = escape_markdown(str(button.get("url", "")), version=1)
            lines.append(f"• {label} → {url}")
    return "\n".join(lines) if lines else flow_tr(language, "review_not_set")

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
    """Start interval recurrence and clear values belonging to other modes."""
    draft = _w(context)
    draft["recurrence_type"] = "interval"
    draft.pop("days_of_week", None)
    draft.pop("interval_value", None)
    draft.pop("interval_unit", None)
    query = update.callback_query
    language = _language(context)
    await _edit(
        query,
        flow_tr(language, "interval_value_prompt"),
        _wizard_nav_keyboard(context, CB_BACK_RECURRENCE),
    )
    return WIZARD_INTERVAL_VALUE

async def cb_rec_dow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start weekly recurrence and clear interval-only values."""
    draft = _w(context)
    draft["recurrence_type"] = "days_of_week"
    draft.pop("interval_value", None)
    draft.pop("interval_unit", None)
    draft["days_of_week"] = set()
    query = update.callback_query
    language = _language(context)
    await _edit(
        query,
        flow_tr(language, "days_prompt"),
        _dow_keyboard(draft["days_of_week"], language),
    )
    return WIZARD_DOW_SELECT

async def recv_interval_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    language = _language(context)
    if not raw.isdecimal() or not (1 <= int(raw) <= 525600):
        await update.message.reply_text(
            flow_tr(language, "interval_invalid"),
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_RECURRENCE),
        )
        return WIZARD_INTERVAL_VALUE
    draft = _w(context)
    draft["interval_value"] = int(raw)
    draft.pop("interval_unit", None)
    await update.message.reply_text(
        flow_tr(language, "interval_unit_prompt"),
        reply_markup=_interval_unit_keyboard(language),
    )
    return WIZARD_INTERVAL_UNIT

async def cb_back_interval_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = _language(context)
    await _edit(
        query,
        flow_tr(language, "interval_value_prompt"),
        _wizard_nav_keyboard(context, CB_BACK_RECURRENCE),
    )
    return WIZARD_INTERVAL_VALUE

async def cb_interval_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = _language(context)
    unit_map = {
        CB_IU_MINUTES: "minutes",
        CB_IU_HOURS: "hours",
        CB_IU_DAYS: "days",
    }
    unit = unit_map.get(query.data)
    if unit is None:
        await query.answer(flow_tr(language, "unit_invalid"), show_alert=True)
        return WIZARD_INTERVAL_UNIT
    value = _w(context).get("interval_value")
    limits = {"minutes": 525600, "hours": 8760, "days": 365}
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= limits[unit]:
        await query.answer(flow_tr(language, "interval_invalid"), show_alert=True)
        return WIZARD_INTERVAL_VALUE
    _w(context)["interval_unit"] = unit
    unit_label = flow_tr(language, f"unit_{unit}")
    await _edit(
        query,
        f"{flow_tr(language, 'interval_confirm', value=value, unit=unit_label)}\n\n"
        f"{tr(language, 'timezone_prompt')}",
        _timezone_keyboard(0, language),
    )
    return WIZARD_TIMEZONE

async def cb_dow_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = _language(context)
    day = int(query.data.split(":")[1])
    draft = _w(context)
    selected: set[int] = draft.setdefault("days_of_week", set())
    if day in selected:
        selected.discard(day)
    else:
        selected.add(day)
    labels = _locale_weekdays(language)
    await query.answer(f"{'✅' if day in selected else '☐'} {labels[day]}")
    await query.edit_message_reply_markup(_dow_keyboard(selected, language))
    return WIZARD_DOW_SELECT

async def cb_dow_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    draft = _w(context)
    selected: set[int] = draft.get("days_of_week", set())
    language = _language(context)
    if not selected:
        await query.answer(flow_tr(language, "day_required"), show_alert=True)
        return WIZARD_DOW_SELECT
    labels = _locale_weekdays(language)
    days_label = ", ".join(labels[day] for day in sorted(selected))
    await _edit(
        query,
        f"✅ {days_label}\n\n{tr(language, 'timezone_prompt')}",
        _timezone_keyboard(0, language),
    )
    return WIZARD_TIMEZONE

async def recv_first_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse a local future date/time and show only relevant delivery options."""
    raw = (update.message.text or "").strip()
    draft = _w(context)
    language = _language(context)
    timezone_name = draft.get("timezone", "UTC")
    dt = _parse_run_time(raw, timezone_name)
    if dt is None:
        await update.message.reply_text(
            flow_tr(language, "invalid_first_time"),
            reply_markup=_first_run_keyboard(CB_BACK_TIMEZONE, language),
        )
        return WIZARD_FIRST_RUN

    draft["first_run"] = dt
    draft["first_run_raw"] = raw
    draft.setdefault("time_window_enabled", False)
    local_display = dt.astimezone(pytz.timezone(timezone_name)).strftime("%d/%m/%Y %H:%M")
    one_time = draft.get("recurrence_type") == "once"
    if one_time:
        draft["max_runs"] = 1
        prompt = (
            f"{tr(language, 'first_run_prompt')}\n\n"
            f"✅ {local_display} ({timezone_name})\n\n{flow_tr(language, 'one_time_delivery')}"
        )
    else:
        prompt = (
            f"{tr(language, 'first_run_prompt')}\n\n"
            f"✅ {local_display} ({timezone_name})\n\n{tr(language, 'max_runs_prompt')}"
        )
    await update.message.reply_text(
        prompt,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_max_runs_keyboard(one_time=one_time, language=language),
    )
    return WIZARD_MAX_RUNS

async def cb_back_first_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to the localized First Run screen."""
    query = update.callback_query
    language = _language(context)
    await _edit(
        query,
        tr(language, "first_run_prompt"),
        _first_run_keyboard(CB_BACK_TIMEZONE, language),
    )
    return WIZARD_FIRST_RUN

async def cb_smart_suggest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show top active hours for the chosen chat as localized tappable suggestions."""
    query = update.callback_query
    language = _language(context)
    await query.answer()
    chat_id = _w(context).get("chat_id")
    results = await get_best_hours(chat_id, top_n=3) if chat_id else []

    if not results:
        await query.edit_message_text(
            flow_tr(language, "smart_no_data"),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_FIRST_RUN)
            ]]),
        )
        return WIZARD_FIRST_RUN

    total = sum(result["count"] for result in results)

    def activity_label(count: int) -> str:
        percentage = count / total * 100 if total else 0
        if percentage >= 40:
            return flow_tr(language, "activity_high")
        if percentage >= 20:
            return flow_tr(language, "activity_good")
        return flow_tr(language, "activity_moderate")

    lines = []
    button_rows: list[list[InlineKeyboardButton]] = []
    for index, result in enumerate(results):
        time_label = f"{result['hour']:02d}:00"
        medal = _MEDAL[index] if index < 3 else "•"
        lines.append(f"{medal} *{time_label}* — {activity_label(result['count'])} ({result['count']})")
        button_rows.append([InlineKeyboardButton(
            f"{medal} {time_label}",
            style="primary",
            callback_data=f"{CB_SMART_PICK_PREFIX}{time_label}",
        )])
    button_rows.append([InlineKeyboardButton(
        flow_tr(language, "smart_custom"), style="primary", callback_data=CB_BACK_FIRST_RUN
    )])
    await query.edit_message_text(
        f"{flow_tr(language, 'smart_heading')}\n\n"
        f"{flow_tr(language, 'smart_context')}\n\n"
        + "\n".join(lines)
        + f"\n\n{flow_tr(language, 'smart_custom')}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(button_rows),
    )
    return WIZARD_FIRST_RUN

async def cb_smart_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Apply a suggested local time and route to the matching delivery-limit step."""
    query = update.callback_query
    language = _language(context)
    time_str = (query.data or "").replace(CB_SMART_PICK_PREFIX, "", 1)
    draft = _w(context)
    timezone_name = draft.get("timezone", "UTC")
    dt = _parse_run_time(time_str, timezone_name)
    if dt is None:
        await query.answer(flow_tr(language, "smart_unavailable"), show_alert=True)
        return WIZARD_FIRST_RUN

    draft["first_run"] = dt
    draft["first_run_raw"] = time_str
    draft.setdefault("time_window_enabled", False)
    one_time = draft.get("recurrence_type") == "once"
    local_display = dt.astimezone(pytz.timezone(timezone_name)).strftime("%d/%m/%Y %H:%M")
    if one_time:
        draft["max_runs"] = 1
        prompt = (
            f"{tr(language, 'first_run_prompt')}\n\n"
            f"✅ {local_display} ({timezone_name})\n\n{flow_tr(language, 'one_time_delivery')}"
        )
    else:
        prompt = (
            f"{tr(language, 'first_run_prompt')}\n\n"
            f"✅ {local_display} ({timezone_name})\n\n{tr(language, 'max_runs_prompt')}"
        )
    await query.answer()
    await query.edit_message_text(
        prompt,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_max_runs_keyboard(one_time=one_time, language=language),
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
    timezone_name = (query.data or "").replace("tz:", "", 1)
    language = _language(context)
    try:
        pytz.timezone(timezone_name)
    except (pytz.exceptions.UnknownTimeZoneError, TypeError):
        await query.answer(flow_tr(language, "timezone_invalid"), show_alert=True)
        return WIZARD_TIMEZONE

    _w(context)["timezone"] = timezone_name
    await _edit(
        query,
        f"✅ {escape_markdown(timezone_name, version=1)}\n\n{tr(language, 'first_run_prompt')}",
        _first_run_keyboard(CB_BACK_TIMEZONE, language),
    )
    return WIZARD_FIRST_RUN

async def cb_back_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Called from First Run back button — return to Timezone picker."""
    query = update.callback_query
    await _edit(query,
        tr(context.user_data.get('language', 'en'), 'timezone_prompt'),
        _timezone_keyboard(0, context.user_data.get("language", "en")),
    )
    return WIZARD_TIMEZONE


async def cb_back_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return from Timezone to the correct localized recurrence sub-step."""
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    recurrence = draft.get("recurrence_type", "once")
    if recurrence == "days_of_week":
        await _edit(query, flow_tr(language, "days_prompt"), _dow_keyboard(draft.get("days_of_week", set()), language))
        return WIZARD_DOW_SELECT
    if recurrence == "interval":
        await _edit(
            query,
            f"{flow_tr(language, 'interval_unit_prompt')}\n\n"
            f"{draft.get('interval_value', '')}",
            _interval_unit_keyboard(language),
        )
        return WIZARD_INTERVAL_UNIT
    await _edit(query, tr(language, "recurrence_prompt"), _recurrence_keyboard(language))
    return WIZARD_RECURRENCE

def _max_runs_keyboard(one_time: bool = False, language: str = "en") -> InlineKeyboardMarkup:
    """Only show delivery-limit choices that apply to this recurrence."""
    if one_time:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                flow_tr(language, "one_time_button"), style="success",
                callback_data=f"{CB_MR_PRESET}1"
            )],
            [
                InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_FIRST_RUN),
                InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
            ],
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"1× {flow_tr(language, 'repeat_once')}", style="primary", callback_data=f"{CB_MR_PRESET}1"),
            InlineKeyboardButton("5×", style="primary", callback_data=f"{CB_MR_PRESET}5"),
            InlineKeyboardButton("10×", style="primary", callback_data=f"{CB_MR_PRESET}10"),
        ],
        [
            InlineKeyboardButton("30×", style="primary", callback_data=f"{CB_MR_PRESET}30"),
            InlineKeyboardButton(f"∞ {flow_tr(language, 'unlimited')}", style="primary", callback_data=f"{CB_MR_PRESET}0"),
        ],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_FIRST_RUN),
            InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
        ],
    ])

async def recv_max_runs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Accept bounded positive custom counts for recurring schedules only."""
    text = (update.message.text or "").strip()
    draft = _w(context)
    language = _language(context)
    one_time = draft.get("recurrence_type") == "once"
    if one_time:
        draft["max_runs"] = 1
        await update.message.reply_text(
            flow_tr(language, "one_time_already"),
            reply_markup=_max_runs_keyboard(one_time=True, language=language),
        )
        return WIZARD_MAX_RUNS
    if not text.isdecimal() or not (1 <= int(text) <= 100000):
        await update.message.reply_text(
            flow_tr(language, "count_invalid"),
            reply_markup=_max_runs_keyboard(language=language),
        )
        return WIZARD_MAX_RUNS

    value = int(text)
    draft["max_runs"] = value
    draft.setdefault("time_window_enabled", False)
    draft.setdefault("auto_delete", False)
    draft.setdefault("self_destruct", False)
    draft.setdefault("auto_pin", False)
    await update.message.reply_text(
        flow_tr(language, "delivery_confirm", value=value),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_advanced_keyboard(
            draft["time_window_enabled"], draft["auto_delete"], draft["self_destruct"],
            draft["auto_pin"], language
        ),
    )
    return WIZARD_TIME_WINDOW

async def cb_mr_preset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate the selected preset against the active recurrence."""
    query = update.callback_query
    language = _language(context)
    raw = (query.data or "").replace(CB_MR_PRESET, "", 1)
    try:
        value = int(raw)
    except ValueError:
        await query.answer(flow_tr(language, "count_invalid"), show_alert=True)
        return WIZARD_MAX_RUNS

    draft = _w(context)
    one_time = draft.get("recurrence_type") == "once"
    allowed = {1} if one_time else {0, 1, 5, 10, 30}
    if value not in allowed:
        await query.answer(flow_tr(language, "count_invalid"), show_alert=True)
        return WIZARD_MAX_RUNS
    draft["max_runs"] = 1 if one_time else (None if value == 0 else value)
    draft.setdefault("time_window_enabled", False)
    draft.setdefault("auto_delete", False)
    draft.setdefault("self_destruct", False)
    draft.setdefault("auto_pin", False)
    if one_time:
        label = flow_tr(language, "one_send")
    elif value == 0:
        label = flow_tr(language, "unlimited")
    else:
        label = flow_tr(language, "sends", value=value)
    await _edit(
        query,
        f"✅ *{flow_tr(language, 'delivery_confirm', value=label)}*\n\n{flow_tr(language, 'advanced_intro')}",
        _advanced_keyboard(
            draft["time_window_enabled"], draft["auto_delete"], draft["self_destruct"],
            draft["auto_pin"], language
        ),
    )
    return WIZARD_TIME_WINDOW

async def cb_tw_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggle a local-time posting window and refresh the localized settings."""
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    enabled = query.data == CB_TW_ON
    draft["time_window_enabled"] = enabled
    if not enabled:
        draft.pop("tw_start", None)
        draft.pop("tw_end", None)
    await query.answer(flow_tr(language, "window_on" if enabled else "window_off"))
    await query.edit_message_reply_markup(
        _advanced_keyboard(
            enabled, draft.get("auto_delete", False), draft.get("self_destruct", False),
            draft.get("auto_pin", False), language
        )
    )
    return WIZARD_TIME_WINDOW

async def cb_adv_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Route enabled optional settings through their localized detail screens."""
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    if draft.get("time_window_enabled"):
        await _edit(query, flow_tr(language, "window_start"), _wizard_nav_keyboard(context, CB_BACK_TW))
        return WIZARD_TW_START

    draft.pop("tw_start", None)
    draft.pop("tw_end", None)
    if draft.get("auto_delete"):
        await _edit(query, flow_tr(language, "delete_prompt"), _wizard_nav_keyboard(context, CB_BACK_TW))
        return WIZARD_AD_HOURS
    if draft.get("self_destruct"):
        await _edit(query, flow_tr(language, "destruct_prompt"), _wizard_nav_keyboard(context, CB_BACK_TW))
        return WIZARD_SD_SECS
    if draft.get("auto_pin"):
        await _edit(query, flow_tr(language, "pin_prompt"), _auto_pin_keyboard(language))
        return WIZARD_AP_HOURS
    return await _show_summary(update, context)

async def recv_tw_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    language = _language(context)
    parsed = _parse_hhmm(raw)
    if parsed is None:
        await update.message.reply_text(
            flow_tr(language, "window_invalid"),
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_TW),
        )
        return WIZARD_TW_START
    _w(context)["tw_start"] = parsed
    await update.message.reply_text(
        f"✅ {parsed}\n\n{flow_tr(language, 'window_end')}",
        reply_markup=_wizard_nav_keyboard(context, CB_BACK_TW_START),
    )
    return WIZARD_TW_END

async def recv_tw_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    parsed = _parse_hhmm(raw)
    draft = _w(context)
    language = _language(context)
    if parsed is None:
        await update.message.reply_text(
            flow_tr(language, "window_invalid"),
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_TW_START),
        )
        return WIZARD_TW_END

    start = draft.get("tw_start")
    if not start or parsed == start:
        await update.message.reply_text(
            flow_tr(language, "window_same"),
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_TW_START),
        )
        return WIZARD_TW_END

    draft["tw_end"] = parsed
    prefix = flow_tr(language, "window_saved", start=start, end=parsed)
    if draft.get("auto_delete"):
        await update.message.reply_text(
            f"{prefix}\n\n{flow_tr(language, 'delete_prompt')}",
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_TW),
        )
        return WIZARD_AD_HOURS
    if draft.get("self_destruct"):
        await update.message.reply_text(
            f"{prefix}\n\n{flow_tr(language, 'destruct_prompt')}",
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_TW),
        )
        return WIZARD_SD_SECS
    if draft.get("auto_pin"):
        await update.message.reply_text(
            f"{prefix}\n\n{flow_tr(language, 'pin_prompt')}",
            reply_markup=_auto_pin_keyboard(language),
        )
        return WIZARD_AP_HOURS
    return await _show_summary_from_message(update, context)

async def cb_lc_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Toggle optional post actions; the two deletion modes remain mutually exclusive."""
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    key_map = {CB_LC_AD: "auto_delete", CB_LC_SD: "self_destruct", CB_LC_AP: "auto_pin"}
    key = key_map.get(query.data)
    if key is None:
        await query.answer(flow_tr(language, "validation_generic"), show_alert=True)
        return WIZARD_TIME_WINDOW

    enabled = not bool(draft.get(key, False))
    draft[key] = enabled
    if key == "auto_delete":
        if enabled:
            draft["self_destruct"] = False
            draft.pop("sd_secs", None)
        else:
            draft.pop("ad_hours", None)
            draft.pop("ad_duration_label", None)
    elif key == "self_destruct":
        if enabled:
            draft["auto_delete"] = False
            draft.pop("ad_hours", None)
            draft.pop("ad_duration_label", None)
        else:
            draft.pop("sd_secs", None)
    elif key == "auto_pin" and not enabled:
        draft.pop("ap_hours", None)

    label_key = {
        "auto_delete": "auto_delete_label",
        "self_destruct": "self_destruct_label",
        "auto_pin": "auto_pin_label",
    }[key]
    await query.answer(flow_tr(language, label_key))
    await query.edit_message_reply_markup(
        _advanced_keyboard(
            draft.get("time_window_enabled", False), draft.get("auto_delete", False),
            draft.get("self_destruct", False), draft.get("auto_pin", False), language
        )
    )
    return WIZARD_TIME_WINDOW

async def cb_lc_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Compatibility route through optional actions using localized prompts."""
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    if draft.get("auto_delete"):
        await _edit(query, flow_tr(language, "delete_prompt"), _wizard_nav_keyboard(context, CB_BACK_TW))
        return WIZARD_AD_HOURS
    if draft.get("self_destruct"):
        await _edit(query, flow_tr(language, "destruct_prompt"), _wizard_nav_keyboard(context, CB_BACK_TW))
        return WIZARD_SD_SECS
    if draft.get("auto_pin"):
        await _edit(query, flow_tr(language, "pin_prompt"), _auto_pin_keyboard(language))
        return WIZARD_AP_HOURS
    return await _show_summary(update, context)

async def recv_ad_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    language = _language(context)
    value = _parse_auto_delete_duration(raw)
    if value is None:
        await update.message.reply_text(
            flow_tr(language, "delete_invalid"),
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_TW),
        )
        return WIZARD_AD_HOURS
    draft = _w(context)
    draft["ad_hours"] = value
    draft["ad_duration_label"] = raw
    if draft.get("auto_pin"):
        await update.message.reply_text(
            f"{flow_tr(language, 'delete_saved', value=raw)}\n\n{flow_tr(language, 'pin_prompt')}",
            reply_markup=_auto_pin_keyboard(language),
        )
        return WIZARD_AP_HOURS
    return await _show_summary_from_message(update, context)

async def recv_sd_secs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    language = _language(context)
    if not raw.isdecimal() or not (1 <= int(raw) <= 31536000):
        await update.message.reply_text(
            flow_tr(language, "destruct_invalid"),
            reply_markup=_wizard_nav_keyboard(context, CB_BACK_TW),
        )
        return WIZARD_SD_SECS
    draft = _w(context)
    draft["sd_secs"] = int(raw)
    if draft.get("auto_pin"):
        await update.message.reply_text(
            f"{flow_tr(language, 'destruct_saved', value=raw)}\n\n{flow_tr(language, 'pin_prompt')}",
            reply_markup=_auto_pin_keyboard(language),
        )
        return WIZARD_AP_HOURS
    return await _show_summary_from_message(update, context)

def _auto_pin_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    """Choose a timed unpin or leave published posts pinned."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            flow_tr(language, "pin_forever"), style="primary", callback_data=CB_AP_FOREVER
        )],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_LIFECYCLE),
            InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
        ],
    ])

async def cb_ap_forever(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save permanent pinning and display the full review screen."""
    query = update.callback_query
    _w(context)["ap_hours"] = None
    return await _show_summary(update, context)
async def recv_ap_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    language = _language(context)
    try:
        value = float(raw)
        if not math.isfinite(value) or value <= 0 or value > 87600:
            raise ValueError
    except (TypeError, ValueError):
        await update.message.reply_text(
            flow_tr(language, "pin_invalid"),
            reply_markup=_auto_pin_keyboard(language),
        )
        return WIZARD_AP_HOURS
    _w(context)["ap_hours"] = value
    return await _show_summary_from_message(update, context)

def _build_summary(w: dict[str, Any], language: str = "en") -> str:
    """Build a localized, safe final review from the validated draft."""
    recurrence = w.get("recurrence_type", "once")
    if recurrence == "interval":
        raw_unit = str(w.get("interval_unit") or "minutes")
        unit_key = f"unit_{raw_unit}"
        unit_label = flow_tr(language, unit_key)
        unit_label = re.sub(r"^[^A-Za-z\u0080-\uFFFF]+", "", unit_label).strip()
        recurrence_line = flow_tr(
            language, "repeat_interval",
            value=w.get("interval_value"), unit=unit_label,
        )
    elif recurrence == "days_of_week":
        weekdays = _locale_weekdays(language)
        days = sorted(w.get("days_of_week", set()))
        recurrence_line = flow_tr(
            language, "repeat_weekly",
            days=", ".join(weekdays[day] for day in days if day in range(7)),
        )
    else:
        recurrence_line = flow_tr(language, "repeat_once")

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
        first_run_line = flow_tr(language, "review_not_set")

    if w.get("time_window_enabled"):
        window_line = f"{w.get('tw_start')} to {w.get('tw_end')} ({timezone_name})"
    else:
        window_line = flow_tr(language, "review_off")

    auto_delete_line = (
        flow_tr(language, "delete_after", value=w.get("ad_duration_label") or w.get("ad_hours"))
        if w.get("auto_delete") else flow_tr(language, "review_off")
    )
    self_destruct_line = (
        flow_tr(language, "destruct_after", value=w.get("sd_secs"))
        if w.get("self_destruct") else flow_tr(language, "review_off")
    )
    if not w.get("auto_pin"):
        auto_pin_line = flow_tr(language, "review_off")
    elif "ap_hours" not in w:
        auto_pin_line = flow_tr(language, "review_not_set")
    elif w.get("ap_hours") is None:
        auto_pin_line = flow_tr(language, "keep_pinned")
    else:
        auto_pin_line = flow_tr(language, "unpin_after", value=w.get("ap_hours"))

    if recurrence == "once":
        delivery_line = flow_tr(language, "one_send")
    elif w.get("max_runs") is None:
        delivery_line = flow_tr(language, "unlimited")
    else:
        delivery_line = flow_tr(language, "sends", value=w.get("max_runs"))

    raw_text = " ".join(str(w.get("content_text") or "").split())
    preview = escape_markdown(raw_text[:100], version=1) if raw_text else ""
    if w.get("media_type"):
        media_kind = str(w["media_type"]).replace("_", " ").title()
        preview = f"[{media_kind}] {preview}".strip()
    if len(raw_text) > 100:
        preview += "…"
    if not preview:
        preview = flow_tr(language, "review_media") if w.get("media_file_id") else flow_tr(language, "review_not_set")

    title = escape_markdown(str(w.get("chat_title") or "Selected destination"), version=1)
    chat_id = escape_markdown(str(w.get("chat_id") or "—"), version=1)
    timezone_display = escape_markdown(timezone_name, version=1)
    values = {
        "destination": title,
        "chat_id": chat_id,
        "content": preview,
        "repeat": escape_markdown(recurrence_line, version=1),
        "first_post": first_run_line,
        "timezone": timezone_display,
        "limit": escape_markdown(delivery_line, version=1),
        "window": escape_markdown(window_line, version=1),
        "auto_delete": escape_markdown(auto_delete_line, version=1),
        "self_destruct": escape_markdown(self_destruct_line, version=1),
        "auto_pin": escape_markdown(auto_pin_line, version=1),
        "buttons": _inline_btns_list_text(w.get("inline_buttons", []), language),
    }
    return flow_tr(language, "review_template", **values)

async def _show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = _language(context)
    await query.answer()
    await query.edit_message_text(
        _build_summary(_w(context), language),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_wizard_confirm_keyboard(context),
    )
    return WIZARD_SUMMARY

async def _show_summary_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    language = _language(context)
    await update.message.reply_text(
        _build_summary(_w(context), language),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_wizard_confirm_keyboard(context),
    )
    return WIZARD_SUMMARY

def _validate_schedule_draft(w: dict[str, Any]) -> str | None:
    """Validate the whole draft before performing the database write."""
    chat_id = w.get("chat_id")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id >= 0:
        return "Choose a valid group or channel destination."
    if not (str(w.get("content_text") or "").strip() or w.get("media_file_id")):
        return "Add the post text or media before continuing."
    if w.get("media_type") and not w.get("media_file_id"):
        return "The selected media is incomplete. Please send it again."
    if w.get("media_file_id") and w.get("media_type") not in {
        "photo", "video", "document", "audio", "animation", "voice"
    }:
        return "The selected media type is not supported."

    recurrence = w.get("recurrence_type")
    if recurrence not in {"once", "interval", "days_of_week"}:
        return "Choose a valid repeat schedule."
    if recurrence == "interval":
        value, unit = w.get("interval_value"), w.get("interval_unit")
        limits = {"minutes": 525600, "hours": 8760, "days": 365}
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            return "Enter a valid repeat interval."
        if unit not in limits or value > limits[unit]:
            return "The repeat interval is outside the supported range."
    elif recurrence == "days_of_week":
        days = w.get("days_of_week")
        if not isinstance(days, (set, list, tuple)) or not days or any(
            not isinstance(day, int) or isinstance(day, bool) or day not in range(7)
            for day in days
        ):
            return "Select at least one valid weekday."

    if w.get("time_window_enabled"):
        start = _parse_hhmm(w.get("tw_start") or "")
        end = _parse_hhmm(w.get("tw_end") or "")
        if not start or not end or start == end:
            return "Complete both Posting Window times. Start and end must be different."
    if w.get("auto_delete") and w.get("self_destruct"):
        return "Choose either Auto-Delete or Self-Destruct, not both."
    if w.get("auto_delete"):
        value = w.get("ad_hours")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            return "Complete the Auto-Delete duration before confirming."
    if w.get("self_destruct"):
        value = w.get("sd_secs")
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 31536000:
            return "Complete the Self-Destruct duration before confirming."
    if w.get("auto_pin"):
        if "ap_hours" not in w:
            return "Choose an Auto-Pin duration or Keep Pinned before confirming."
        value = w.get("ap_hours")
        if value is not None and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or not 0 < value <= 87600
        ):
            return "Choose a valid Auto-Pin duration."

    try:
        pytz.timezone(w.get("timezone", "UTC"))
    except (pytz.exceptions.UnknownTimeZoneError, TypeError):
        return "Choose a valid timezone."
    first_run = w.get("first_run")
    if (
        not isinstance(first_run, datetime)
        or first_run.tzinfo is None
        or first_run.utcoffset() is None
        or first_run.astimezone(timezone.utc) <= datetime.now(tz=timezone.utc)
    ):
        return "The first posting time is no longer in the future. Choose a new time."

    if recurrence == "once":
        w["max_runs"] = 1
    else:
        value = w.get("max_runs")
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100000
        ):
            return "Choose a valid delivery limit."

    buttons = w.get("inline_buttons") or []
    if len(buttons) > 10:
        return "A post can have no more than 10 buttons."
    for row in buttons:
        if not isinstance(row, list):
            return "One of the post-button rows is invalid."
        for button in row:
            if not isinstance(button, dict):
                return "One of the post buttons is invalid."
            label = str(button.get("text") or "").strip()
            url = str(button.get("url") or "").strip()
            if not label or len(label) > 64 or not url.startswith(("http://", "https://", "tg://")) or " " in url:
                return "One of the post buttons has an invalid label or destination."
    return None



async def _edit_confirm_result(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    keyboard: InlineKeyboardMarkup,
    *,
    parse_mode: str | None = None,
) -> bool:
    """Edit the wizard message, then fall back to a fresh chat message."""
    try:
        await query.edit_message_text(
            text,
            parse_mode=parse_mode,
            reply_markup=keyboard,
        )
        return True
    except Exception as edit_error:
        logger.warning(
            "Could not edit Schedule New Post confirmation message (%s); trying a new message.",
            edit_error,
        )

    message = getattr(query, "message", None)
    if message is not None:
        try:
            await message.reply_text(
                text,
                parse_mode=parse_mode,
                reply_markup=keyboard,
            )
            return True
        except Exception as reply_error:
            logger.warning(
                "Reply fallback for Schedule New Post confirmation failed (%s); trying direct send.",
                reply_error,
            )

    chat_id = getattr(message, "chat_id", None)
    bot = getattr(context, "bot", None)
    if chat_id is not None and bot is not None:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=keyboard,
            )
            return True
        except Exception:
            logger.exception("Could not send a fallback Schedule New Post confirmation message.")
    return False


async def cb_confirm_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Revalidate, save once, and confirm without relying on an editable old message."""
    query = update.callback_query
    message = getattr(query, "message", None)
    message_id = getattr(message, "message_id", None)
    completed = context.user_data.get("_completed_schedule_confirmations", {})
    message_key = str(message_id) if message_id is not None else None

    # A completed wizard can receive delayed/replayed callback updates. Never
    # recreate its draft or attempt a second database insert for that message.
    if message_key is not None and isinstance(completed, dict) and message_key in completed:
        await query.answer("This schedule has already been saved.", show_alert=True)
        return ConversationHandler.END

    w = context.user_data.get("wizard")
    if not isinstance(w, dict):
        await query.answer(
            "This setup is no longer active. Start a new schedule to continue.",
            show_alert=True,
        )
        return ConversationHandler.END

    if w.get("_saving"):
        await query.answer("Your schedule is already being saved.")
        return WIZARD_SUMMARY

    validation_error = _validate_schedule_draft(w)
    if validation_error:
        await query.answer()
        if "destination" in validation_error.lower():
            user_id = query.from_user.id if query.from_user else None
            language = await resolve_user_language(context, user_id)
            bot_username = (await query.get_bot().get_me()).username
            groups = await list_groups_for_user(user_id) if user_id is not None else []
            await _edit_confirm_result(
                query,
                context,
                f"⚠️ {validation_error}\\n\\n{tr(language, 'choose_group')}",
                _chat_list_keyboard(groups, 0, bot_username, language)
                if groups else _no_groups_keyboard(bot_username, language),
            )
            return WIZARD_CHAT_ID
        if "first posting time" in validation_error:
            await _edit_confirm_result(
                query,
                context,
                "⚠️ The selected first-post time is no longer in the future.\\n\\n"
                "Enter a new 24-hour HH:MM time or an exact DD/MM/YYYY HH:MM date.",
                _first_run_keyboard(CB_BACK_TIMEZONE),
            )
            return WIZARD_FIRST_RUN
        await _edit_confirm_result(
            query,
            context,
            "⚠️ *This schedule is incomplete.*\\n\\n"
            + escape_markdown(validation_error, version=1)
            + "\\n\\nReview the settings and try again.",
            confirm_keyboard(CB_CONFIRM_POST, CB_BACK_LIFECYCLE),
            parse_mode=ParseMode.MARKDOWN,
        )
        return WIZARD_SUMMARY

    user_id = query.from_user.id if query.from_user else None
    if user_id is None:
        await query.answer("Unable to identify your Telegram account.", show_alert=True)
        return WIZARD_SUMMARY

    # Acquire the per-user wizard lock before the first awaited operation.
    # This closes the race where two quick callbacks both pass permission
    # checks and insert the same schedule.
    w["_saving"] = True
    try:
        await query.answer("Saving schedule…")
    except Exception:
        logger.debug("Could not acknowledge Schedule New Post confirmation callback.", exc_info=True)

    try:
        await ensure_chat_access(context.bot, w["chat_id"], user_id)
    except ChatAccessError:
        w.pop("_saving", None)
        bot_username = (await query.get_bot().get_me()).username
        groups = await list_groups_for_user(user_id)
        await _edit_confirm_result(
            query,
            context,
            "⚠️ Posting access to the selected destination has changed. "
            "Restore the bot's administrator/posting permissions, then select the destination again.",
            _chat_list_keyboard(groups, 0, bot_username)
            if groups else _no_groups_keyboard(bot_username),
        )
        return WIZARD_CHAT_ID
    except Exception:
        w.pop("_saving", None)
        logger.exception("Could not revalidate destination access for user_id=%s", user_id)
        await _edit_confirm_result(
            query,
            context,
            "⚠️ We couldn't verify posting access right now. Your setup is still available. "
            "Please try confirming again.",
            confirm_keyboard(CB_CONFIRM_POST, CB_BACK_LIFECYCLE),
            parse_mode=ParseMode.MARKDOWN,
        )
        return WIZARD_SUMMARY

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
    except Exception:
        logger.exception("Failed to save scheduled post for user_id=%s", user_id)
        w.pop("_saving", None)
        await _edit_confirm_result(
            query,
            context,
            "⚠️ *We couldn't confirm that your schedule was saved.*\\n\\n"
            "Your setup is still available. Before retrying, check *My Posts* in case the database save completed but the response was interrupted.",
            confirm_keyboard(CB_CONFIRM_POST, CB_BACK_LIFECYCLE),
            parse_mode=ParseMode.MARKDOWN,
        )
        return WIZARD_SUMMARY

    # Remember the completed callback message before any more awaits. Delayed
    # duplicate clicks are acknowledged without touching the database again.
    if message_key is not None:
        completed = context.user_data.setdefault("_completed_schedule_confirmations", {})
        if not isinstance(completed, dict):
            completed = {}
            context.user_data["_completed_schedule_confirmations"] = completed
        completed[message_key] = str(post_id)
        while len(completed) > 20:
            completed.pop(next(iter(completed)))

    _clear(context)
    chat_id = getattr(message, "chat_id", None)
    try:
        if chat_id is not None:
            await chat_cleanup.cleanup(
                context, chat_id, keep_message_id=message_id
            )
    except Exception:
        # The database commit is already complete; cleanup must never suppress
        # the success acknowledgement or make users retry and create duplicates.
        logger.exception("Schedule %s was saved, but wizard message cleanup failed.", post_id)

    await _edit_confirm_result(
        query,
        context,
        f"✅ *Post scheduled successfully!*\\n\\n"
        f"🆔 Post ID: {post_id}\\n\\n"
        "Your schedule is saved and ready. Use *My Posts* to manage it.",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Main Menu", style="primary", callback_data=CB_MAIN_MENU)],
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def cb_back_recurrence(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = _language(context)
    await _edit(query, tr(language, "recurrence_prompt"), _recurrence_keyboard(language))
    return WIZARD_RECURRENCE

async def cb_back_dow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    await _edit(
        query,
        flow_tr(language, "days_prompt"),
        _dow_keyboard(draft.get("days_of_week", set()), language),
    )
    return WIZARD_DOW_SELECT

async def cb_back_interval_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    await _edit(
        query,
        f"{flow_tr(language, 'interval_unit_prompt')}\n\n"
        f"{draft.get('interval_value', '')}",
        _interval_unit_keyboard(language),
    )
    return WIZARD_INTERVAL_UNIT

async def cb_back_max_runs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    one_time = draft.get("recurrence_type") == "once"
    prompt = (
        flow_tr(language, "one_time_delivery")
        if one_time else tr(language, "max_runs_prompt")
    )
    await _edit(query, prompt, _max_runs_keyboard(one_time=one_time, language=language))
    return WIZARD_MAX_RUNS

async def cb_back_tw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to the localized Advanced Options screen."""
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    await _edit(
        query,
        flow_tr(language, "advanced_intro"),
        _advanced_keyboard(
            draft.get("time_window_enabled", False),
            draft.get("auto_delete", False),
            draft.get("self_destruct", False),
            draft.get("auto_pin", False),
            language,
        ),
    )
    return WIZARD_TIME_WINDOW

async def cb_back_tw_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = _language(context)
    await _edit(
        query,
        flow_tr(language, "window_start"),
        _wizard_nav_keyboard(context, CB_BACK_TW),
    )
    return WIZARD_TW_START

async def cb_back_lifecycle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to Advanced Options from the final review or a lifecycle detail."""
    query = update.callback_query
    draft = _w(context)
    language = _language(context)
    await _edit(
        query,
        flow_tr(language, "advanced_intro"),
        _advanced_keyboard(
            draft.get("time_window_enabled", False),
            draft.get("auto_delete", False),
            draft.get("self_destruct", False),
            draft.get("auto_pin", False),
            language,
        ),
    )
    return WIZARD_TIME_WINDOW

async def cb_cancel_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    cancelled = flow_tr(language, "cancelled")
    await query.answer(cancelled.replace("*", ""))
    _clear(context)
    chat_id = query.message.chat_id
    await chat_cleanup.cleanup(context, chat_id, keep_message_id=query.message.message_id)
    await replace_with_main_menu_message(
        query, context, cancelled, user_id, language,
    )
    return ConversationHandler.END

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
