"""
handlers/queue_manager.py — Two fully independent sub-systems:

  A) QUEUE MANAGER (slot-based)
     • Choose a group/channel from a picker (or enter ID manually)
     • Set named daily time slots for a chat
     • Add content → specify exact date/time (DD/MM/YYYY HH:MM or HH:MM) + timezone
     • Confirm / Cancel with full message cleanup
     • View / clear the queue

  B) MEDIA POOL (random shuffler)
     • Add multiple content items to a pool
     • Bot picks one randomly at each scheduled interval
     • Pool resets automatically once all items are posted
"""

from __future__ import annotations

import logging
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
    upsert_queue_slots,
    add_to_queue,
    get_queue,
    add_to_media_pool,
    pick_random_pool_item,
    reset_media_pool,
    build_queue_slot_doc,
    build_media_pool_doc,
    get_db,
    list_groups_for_user,
    COL_QUEUE_SLOTS,
    COL_MEDIA_POOLS,
    COL_POSTS,
)
from handlers import chat_cleanup
from handlers.permissions import ChatAccessError, ensure_chat_access
from handlers.base import CB_CANCEL, CB_MAIN_MENU, cmd_cancel, nav_keyboard, confirm_keyboard, main_menu_keyboard
from handlers.language import resolve_user_language
from localization import translate as tr

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------
(
    QM_MENU,
    QM_CHAT_ID,
    QM_SET_SLOTS,
    QM_ADD_CONTENT,
    QM_ADD_CHAT_ID,
    QM_CONFIRM_SLOT,
    QM_SET_SLOTS_CHAT_ID,
    QM_SET_SLOTS_TZ,
) = range(100, 108)

# New states for date/time + timezone in Add Content flow
QM_ADD_DATE = 108
QM_ADD_TZ   = 109

(
    MP_MENU,
    MP_CHAT_ID,
    MP_ADD_ITEM,
    MP_SET_INTERVAL_VALUE,
    MP_SET_INTERVAL_UNIT,
    MP_SET_FIRST_RUN,
    MP_SET_TIMEZONE,
    MP_AUTO_DELETE,
    MP_CONFIRM,
) = range(200, 209)
MP_MANAGE_CHAT = 209
MP_MANAGE_ACTION = 210

# ---------------------------------------------------------------------------
# Callback-data constants  (all ≤ 64 bytes)
# ---------------------------------------------------------------------------
CB_MANAGE_QUEUE  = "action:manage_queue"
CB_MEDIA_POOL    = "action:media_pool"

# Queue manager
CB_QM_SET_SLOTS    = "qm:set_slots"
CB_QM_ADD_CONTENT  = "qm:add_content"
CB_QM_VIEW         = "qm:view"
CB_QM_CLEAR        = "qm:clear"
CB_QM_CLEAR_YES    = "qm:clear_yes"
CB_QM_CONFIRM_ADD  = "qm:confirm_add"

# Group picker — Set Slots flow
CB_QM_SS_PICK   = "qm:ss:pick:"    # + str(chat_id)
CB_QM_SS_PAGE   = "qm:ss:page:"    # + str(page)
CB_QM_SS_MANUAL = "qm:ss:manual"

# Group picker — Add Content flow
CB_QM_AC_PICK   = "qm:ac:pick:"    # + str(chat_id)
CB_QM_AC_PAGE   = "qm:ac:page:"    # + str(page)
CB_QM_AC_MANUAL = "qm:ac:manual"

# Add Content timezone picker
CB_QM_AC_TZ_PAGE = "qmactz:page:"  # + str(page)

# Media pool
CB_MP_ADD_ITEM     = "mp:add"
CB_MP_VIEW         = "mp:view"
CB_MP_RESET        = "mp:reset"
CB_MP_RESET_YES    = "mp:reset_yes"
CB_MP_CLEAR        = "mp:clear"
CB_MP_CLEAR_YES    = "mp:clear_yes"
CB_MP_SET_INTERVAL = "mp:set_interval"
CB_MP_CONFIRM      = "mp:confirm"
CB_MP_IU_MINUTES   = "mp:iu:minutes"
CB_MP_IU_HOURS     = "mp:iu:hours"
CB_MP_IU_DAYS      = "mp:iu:days"
CB_MP_MANAGE       = "mp:manage"
CB_MP_PICK         = "mp:pick:"
CB_MP_PAGE         = "mp:page:"
CB_MP_MANUAL       = "mp:manual"
CB_MP_ADD_SELECTED = "mp:add:selected"
CB_MP_ADD_DONE     = "mp:add:done"
CB_MP_ADD_SCHEDULE = "mp:add:schedule"
CB_MP_MANAGE_PICK  = "mp:manage:pick:"
CB_MP_MANAGE_PAGE  = "mp:manage:page:"
CB_MP_CUSTOM_INTERVAL = "mp:interval:custom"
CB_MP_QUICK_PREFIX = "mp:quick:"
CB_MP_FIRST_NOW    = "mp:first:now"
CB_MP_FIRST_CUSTOM = "mp:first:custom"
CB_BACK_MP_MANAGE = "mp:back:manage"
CB_BACK_MP_DETAILS = "mp:back:details"

# Back targets
CB_BACK_QM_MENU         = "back:qm_menu"
CB_BACK_QM_SLOT_CHAT    = "back:qm_slot_chat"    # back to Set Slots group picker
CB_BACK_QM_SLOT_TZ      = "back:qm_slot_tz"      # back to slots text entry
CB_BACK_QM_AC_CHAT      = "back:qm_ac_chat"      # back to Add Content group picker
CB_BACK_QM_AC_CONTENT   = "back:qm_ac_content"   # back to Add Content content send
CB_BACK_QM_AC_DATE      = "back:qm_ac_date"      # back to Add Content date step
CB_BACK_QM_AC_TZ        = "back:qm_ac_tz"        # back to Add Content timezone step

# QM timezone picker (Set Slots)
CB_QM_TZ_PAGE = "qmtz:page:"
CB_MP_AD_YES       = "mp:ad:yes"
CB_MP_AD_NO        = "mp:ad:no"

CB_BACK_MP_MENU    = "back:mp_menu"
CB_BACK_MP_IV      = "back:mp_iv"
CB_BACK_MP_IU      = "back:mp_iu"
CB_BACK_MP_FR      = "back:mp_fr"
CB_BACK_MP_TZ      = "back:mp_tz"

# Timezone constants
CB_MP_TZ_PAGE = "mptz:page:"
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_DT_RE   = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})$")

_QM_CHAT_PAGE_SIZE = 5

TIMEZONES: list[tuple[str, str]] = [
    ("UTC",              "UTC"),
    ("Asia/Kolkata",     "GMT+5:30 India"),
    ("Asia/Dhaka",       "GMT+6:00 Bangladesh"),
    ("Asia/Rangoon",     "GMT+6:30 Myanmar"),
    ("Asia/Bangkok",     "GMT+7:00 Thailand"),
    ("Asia/Singapore",   "GMT+8:00 Singapore"),
    ("Asia/Tokyo",       "GMT+9:00 Japan"),
    ("Asia/Seoul",       "GMT+9:00 Korea"),
    ("Europe/London",    "GMT+0/+1 London"),
    ("Europe/Berlin",    "GMT+1/+2 Berlin"),
    ("America/New_York", "GMT-5/-4 New York"),
    ("America/Chicago",  "GMT-6/-5 Chicago"),
    ("America/Denver",   "GMT-7/-6 Denver"),
    ("America/Los_Angeles", "GMT-8/-7 Los Angeles"),
    ("Australia/Sydney", "GMT+10/+11 Sydney"),
]
TZ_PAGE_SIZE = 5


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _w(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    return context.user_data.setdefault("qwiz", {})


def _clear(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("qwiz", None)


def _pm(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    return context.user_data.setdefault("mpwiz", {})


def _clear_mp(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("mpwiz", None)


async def _edit(query: Any, text: str, keyboard: InlineKeyboardMarkup) -> None:
    await query.answer()
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
    except Exception:
        try:
            await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        except Exception:
            logger.exception("_edit fallback reply_text also failed")


def _parse_hhmm(text: str) -> str | None:
    m = _TIME_RE.match(text.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def _parse_slots(raw: str) -> list[str] | None:
    """
    Parse a comma/newline/space separated list of HH:MM time strings.
    Returns sorted unique valid slots, or None if any token is invalid.
    """
    tokens = re.split(r"[\s,;]+", raw.strip())
    tokens = [t.strip() for t in tokens if t.strip()]
    if not tokens:
        return None
    parsed: list[str] = []
    for t in tokens:
        p = _parse_hhmm(t)
        if p is None:
            return None
        parsed.append(p)
    return sorted(set(parsed))


def _parse_run_time(text: str, tz: str) -> datetime | None:
    """
    Parse user-supplied date/time string into an aware UTC datetime.
    Accepts:
      • "HH:MM"               — today in the given tz (tomorrow if already past)
      • "DD/MM/YYYY HH:MM"    — specific date in the given tz
    Returns None on parse failure or invalid date.
    """
    text = text.strip()
    try:
        zone: Any = pytz.timezone(tz) if tz != "UTC" else timezone.utc
    except pytz.exceptions.UnknownTimeZoneError:
        zone = timezone.utc

    # Try full date first
    m = _DT_RE.match(text)
    if m:
        d, mo, y, h, mi = (int(x) for x in m.groups())
        try:
            naive = datetime(y, mo, d, h, mi, 0)
        except ValueError:
            return None
        if isinstance(zone, pytz.BaseTzInfo):
            return zone.localize(naive).astimezone(timezone.utc)
        return naive.replace(tzinfo=zone).astimezone(timezone.utc)

    # Try HH:MM only
    m2 = _TIME_RE.match(text)
    if m2:
        h, mi = int(m2.group(1)), int(m2.group(2))
        if h > 23 or mi > 59:
            return None
        now = datetime.now(tz=zone)
        candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    return None


def _next_slot_datetime(slots: list[str], tz_str: str = "UTC") -> tuple[str, datetime] | None:
    if not slots:
        return None
    try:
        zone = pytz.timezone(tz_str) if tz_str != "UTC" else timezone.utc
    except pytz.exceptions.UnknownTimeZoneError:
        zone = timezone.utc

    now = datetime.now(tz=zone)
    candidates: list[tuple[datetime, str]] = []

    for slot in slots:
        h, mi = int(slot[:2]), int(slot[3:])
        candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        candidates.append((candidate, slot))

    candidates.sort(key=lambda x: x[0])
    dt, label = candidates[0]
    return label, dt


def _mp_interval_unit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏱ Minutes", callback_data=CB_MP_IU_MINUTES),
            InlineKeyboardButton("🕐 Hours",   callback_data=CB_MP_IU_HOURS),
            InlineKeyboardButton("📅 Days",    callback_data=CB_MP_IU_DAYS),
        ],
        [
            InlineKeyboardButton("⬅️ Back",   callback_data=CB_BACK_MP_IU),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ],
    ])


def _qm_tz_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    """Timezone picker keyboard for the Queue Manager Set Slots wizard."""
    start = page * TZ_PAGE_SIZE
    end   = start + TZ_PAGE_SIZE
    rows = [
        [InlineKeyboardButton(label, callback_data=f"qmtz:{tz}")]
        for tz, label in TIMEZONES[start:end]
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{CB_QM_TZ_PAGE}{page - 1}"))
    if end < len(TIMEZONES):
        nav.append(InlineKeyboardButton("▶️ Next", callback_data=f"{CB_QM_TZ_PAGE}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("⬅️ Back",   callback_data=CB_BACK_QM_SLOT_TZ),
        InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _qm_ac_tz_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    """Timezone picker keyboard for the Add Content wizard."""
    start = page * TZ_PAGE_SIZE
    end   = start + TZ_PAGE_SIZE
    rows = [
        [InlineKeyboardButton(label, callback_data=f"qmactz:{tz}")]
        for tz, label in TIMEZONES[start:end]
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{CB_QM_AC_TZ_PAGE}{page - 1}"))
    if end < len(TIMEZONES):
        nav.append(InlineKeyboardButton("▶️ Next", callback_data=f"{CB_QM_AC_TZ_PAGE}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("⬅️ Back",   callback_data=CB_BACK_QM_AC_DATE),
        InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _mp_tz_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    start = page * TZ_PAGE_SIZE
    end   = start + TZ_PAGE_SIZE
    rows = [
        [InlineKeyboardButton(label, callback_data=f"mptz:{tz}")]
        for tz, label in TIMEZONES[start:end]
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{CB_MP_TZ_PAGE}{page - 1}"))
    if end < len(TIMEZONES):
        nav.append(InlineKeyboardButton("▶️ Next", callback_data=f"{CB_MP_TZ_PAGE}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("⬅️ Back",   callback_data=CB_BACK_MP_FR),
        InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _qm_group_picker_keyboard(
    groups: list[dict],
    page: int,
    pick_prefix: str,
    page_prefix: str,
    manual_cb: str,
    back_cb: str,
) -> InlineKeyboardMarkup:
    """Generic group-picker keyboard shared by Set Slots and Add Content flows."""
    rows: list[list[InlineKeyboardButton]] = []
    total  = len(groups)
    start  = page * _QM_CHAT_PAGE_SIZE
    end    = min(start + _QM_CHAT_PAGE_SIZE, total)
    type_icon = {"channel": "📢", "supergroup": "👥", "group": "👥"}
    for g in groups[start:end]:
        icon  = type_icon.get(g.get("chat_type", ""), "💬")
        title = (g.get("title") or f"Chat {g['chat_id']}")[:35]
        rows.append([InlineKeyboardButton(
            f"{icon} {title}",
            callback_data=f"{pick_prefix}{g['chat_id']}",
        )])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{page_prefix}{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"{page_prefix}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("✏️ Enter ID manually", callback_data=manual_cb)])
    rows.append([
        InlineKeyboardButton("⬅️ Back",   callback_data=back_cb),
        InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _qm_no_groups_keyboard(manual_cb: str, back_cb: str, bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "➕ Add Bot to a Group",
            url=f"https://t.me/{bot_username}?startgroup=start",
        )],
        [InlineKeyboardButton("✏️ Enter ID manually", callback_data=manual_cb)],
        [
            InlineKeyboardButton("⬅️ Back",   callback_data=back_cb),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ],
    ])


# ============================================================================
#  A) QUEUE MANAGER
# ============================================================================

def _qm_menu_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr(language, "queue_set_slots"), callback_data=CB_QM_SET_SLOTS)],
        [InlineKeyboardButton(tr(language, "queue_add_content"), callback_data=CB_QM_ADD_CONTENT)],
        [InlineKeyboardButton(tr(language, "queue_view"), callback_data=CB_QM_VIEW)],
        [InlineKeyboardButton(tr(language, "queue_clear"), callback_data=CB_QM_CLEAR)],
        [
            InlineKeyboardButton(tr(language, "menu_title"), callback_data=CB_MAIN_MENU),
            InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
        ],
    ])


async def enter_queue_manager(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    _clear(context)
    language = await resolve_user_language(context, query.from_user.id if query.from_user else None)
    chat_cleanup.reset(context.application.bot_data, query.message.chat_id)
    chat_cleanup.track(context.application.bot_data, query.message.chat_id, query.message.message_id)
    await _edit(
        query,
        f"{tr(language, 'queue_title')}\n\n{tr(language, 'queue_intro')}",
        _qm_menu_keyboard(language),
    )
    return QM_MENU


async def cb_qm_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show a concise overview of this user's daily queues and item states."""
    query = update.callback_query
    if query is None or query.from_user is None:
        return QM_MENU

    user_id = query.from_user.id
    try:
        db = await get_db()
        cursor = (
            db[COL_QUEUE_SLOTS]
            .find({"user_id": user_id})
            .sort("updated_at", -1)
            .limit(5)
        )
        queues = await cursor.to_list(length=5)
        groups = await list_groups_for_user(user_id)
    except Exception:
        logger.exception("Failed to load daily queues for user_id=%s", user_id)
        await query.edit_message_text(
            "⚠️ Unable to load your queues right now. Please try again.",
            reply_markup=_qm_menu_keyboard(context.user_data.get("language", "en")),
        )
        return QM_MENU

    if not queues:
        await _edit(
            query,
            "📭 *No Daily Queues Yet*\n\n"
            "Set daily posting slots first, then add content to the queue.",
            _qm_menu_keyboard(context.user_data.get("language", "en")),
        )
        return QM_MENU

    group_names = {
        int(group.get("chat_id")): str(group.get("title") or group.get("chat_id"))
        for group in groups
        if group.get("chat_id") is not None
    }

    lines = ["👁 *Your Daily Queues*", ""]
    for index, queue in enumerate(queues, start=1):
        chat_id = queue.get("chat_id")
        title = group_names.get(int(chat_id), f"Chat {chat_id}") if chat_id is not None else "Unknown chat"
        slots = queue.get("slots") or []
        timezone_name = str(queue.get("timezone") or "UTC")
        items = queue.get("contents") or []

        counts = {"pending": 0, "sending": 0, "posted": 0, "failed": 0}
        for item in items:
            state = str(item.get("state") or ("posted" if item.get("posted") else "pending")).lower()
            if state in ("queued", "pending"):
                counts["pending"] += 1
            elif state in counts:
                counts[state] += 1

        lines.extend([
            f"{index}. *{escape_markdown(title, version=1)}*",
            f"🆔 Chat ID: `{chat_id}`",
            f"🕒 Slots: `{escape_markdown(', '.join(str(slot) for slot in slots) or 'Not set', version=1)}`",
            f"🌍 Timezone: *{escape_markdown(timezone_name, version=1)}*",
            f"📦 Pending: {counts['pending']}  ⏳ Sending: {counts['sending']}",
            f"✅ Posted: {counts['posted']}  ❌ Failed: {counts['failed']}",
        ])

        recent_items = items[-2:]
        for item in recent_items:
            state = str(item.get("state") or ("posted" if item.get("posted") else "pending")).lower()
            status_icon = {
                "pending": "🕓", "queued": "🕓", "sending": "⏳",
                "posted": "✅", "failed": "❌",
            }.get(state, "•")
            preview = str(item.get("text") or "").replace("\n", " ").strip()
            if not preview:
                media_type = str(item.get("media_type") or "media").replace("_", " ").title()
                preview = f"[{media_type}]"
            if len(preview) > 54:
                preview = preview[:51] + "..."
            lines.append(
                f"  {status_icon} {escape_markdown(preview, version=1)}"
            )
        if index < len(queues):
            lines.append("")

    if len(queues) == 5:
        lines.extend(["", "_Showing the 5 most recently updated queues._"])

    await _edit(query, "\n".join(lines), _qm_menu_keyboard(context.user_data.get("language", "en")))
    return QM_MENU


# ── Set Slots — Step 1: Group Picker ──

async def cb_qm_set_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry to Set Slots wizard — show group picker."""
    query = update.callback_query
    _w(context).pop("slots_chat_id", None)
    _w(context).pop("pending_slots", None)

    user_id      = query.from_user.id
    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id)

    if groups:
        await _edit(query,
            "⏰ *Set Daily Slots — Step 1 of 3*\n\n"
            f"🗂 Choose the group or channel to set slots for:\n"
            f"_(Showing {min(_QM_CHAT_PAGE_SIZE, len(groups))} of {len(groups)} known chats)_",
            _qm_group_picker_keyboard(
                groups, 0,
                CB_QM_SS_PICK, CB_QM_SS_PAGE, CB_QM_SS_MANUAL,
                CB_BACK_QM_MENU,
            ),
        )
    else:
        await _edit(query,
            "⏰ *Set Daily Slots — Step 1 of 3*\n\n"
            "⚠️ *No groups found.*\n\n"
            "Add the bot to your group first, then come back.\n"
            "_Or enter the Chat ID manually._",
            _qm_no_groups_keyboard(CB_QM_SS_MANUAL, CB_BACK_QM_MENU, bot_username),
        )
    return QM_SET_SLOTS_CHAT_ID


async def cb_qm_ss_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Set Slots — user tapped a group button."""
    query = update.callback_query
    raw = (query.data or "").replace(CB_QM_SS_PICK, "", 1)
    try:
        chat_id = int(raw)
    except ValueError:
        await query.answer("Invalid selection.", show_alert=True)
        return QM_SET_SLOTS_CHAT_ID

    user_id = query.from_user.id
    try:
        target_chat = await ensure_chat_access(query.get_bot(), chat_id, user_id)
    except ChatAccessError as exc:
        await query.answer(str(exc), show_alert=True)
        return QM_SET_SLOTS_CHAT_ID
    groups  = await list_groups_for_user(user_id)
    title_map = {g["chat_id"]: (g.get("title") or str(g["chat_id"])) for g in groups}
    title = target_chat.title or title_map.get(chat_id, str(chat_id))

    _w(context)["slots_chat_id"]    = chat_id
    _w(context)["slots_chat_title"] = title

    await _edit(query,
        f"✅ Selected: *{title}* (`{chat_id}`)\n\n"
        "⏰ *Set Daily Slots — Step 2 of 3*\n\n"
        "Enter the daily posting times separated by commas or spaces.\n\n"
        "Example: `09:00, 14:00, 20:00`",
        nav_keyboard(back_data=CB_BACK_QM_SLOT_CHAT),
    )
    return QM_SET_SLOTS


async def cb_qm_ss_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Set Slots — paginate group list."""
    query = update.callback_query
    raw   = (query.data or "").replace(CB_QM_SS_PAGE, "", 1)
    page  = int(raw) if raw.isdigit() else 0
    user_id      = query.from_user.id
    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id)
    total  = len(groups)
    start  = page * _QM_CHAT_PAGE_SIZE
    shown  = min(_QM_CHAT_PAGE_SIZE, total - start)
    await _edit(query,
        "⏰ *Set Daily Slots — Step 1 of 3*\n\n"
        f"🗂 Choose the group or channel:\n"
        f"_(Showing {shown} of {total} known chats)_",
        _qm_group_picker_keyboard(
            groups, page,
            CB_QM_SS_PICK, CB_QM_SS_PAGE, CB_QM_SS_MANUAL,
            CB_BACK_QM_MENU,
        ),
    )
    return QM_SET_SLOTS_CHAT_ID


async def cb_qm_ss_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Set Slots — user wants to type Chat ID manually."""
    query = update.callback_query
    await _edit(query,
        "⏰ *Set Daily Slots — Step 1 of 3*\n\n"
        "✏️ Type the *Chat ID* of the channel/group:\n"
        "_(Use /id in the group to get the ID)_\n\n"
        "Example: `-1001234567890`",
        nav_keyboard(back_data=CB_BACK_QM_MENU),
    )
    return QM_SET_SLOTS_CHAT_ID


async def recv_qm_slots_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Set Slots — receive manually-typed Chat ID."""
    text = (update.message.text or "").strip()
    try:
        chat_id = int(text)
    except ValueError:
        await update.message.reply_text(
            "⚠️ Enter a valid numeric Chat ID (e.g. `-1001234567890`).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return QM_SET_SLOTS_CHAT_ID

    try:
        target_chat = await ensure_chat_access(context.bot, chat_id, update.effective_user.id)
    except ChatAccessError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return QM_SET_SLOTS_CHAT_ID
    _w(context)["slots_chat_id"]    = chat_id
    _w(context)["slots_chat_title"] = target_chat.title or str(chat_id)
    await update.message.reply_text(
        f"✅ Chat: `{chat_id}`\n\n"
        "⏰ *Set Daily Slots — Step 2 of 3*\n\n"
        "Enter the daily posting times separated by commas or spaces.\n\n"
        "Example: `09:00, 14:00, 20:00`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=nav_keyboard(back_data=CB_BACK_QM_SLOT_CHAT),
    )
    return QM_SET_SLOTS


async def recv_qm_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Set Slots — receive HH:MM time list, then show timezone picker."""
    text = (update.message.text or "").strip()
    slots = _parse_slots(text)
    if not slots:
        await update.message.reply_text(
            "⚠️ Could not parse valid times. Use `HH:MM` format.\n"
            "Example: `09:00, 14:30, 21:00`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return QM_SET_SLOTS

    _w(context)["pending_slots"] = slots
    _w(context).setdefault("qm_tz_page", 0)
    slot_display = "  •  ".join(slots)
    await update.message.reply_text(
        f"✅ Slots: `{slot_display}`\n\n"
        "🌍 *Set Daily Slots — Step 3 of 3*\n\n"
        "Select your timezone:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_tz_keyboard(0),
    )
    return QM_SET_SLOTS_TZ


async def cb_qm_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Set Slots — user picked timezone; save slots + timezone to DB."""
    query  = update.callback_query
    tz_key = (query.data or "").replace("qmtz:", "", 1)
    try:
        pytz.timezone(tz_key)
    except pytz.exceptions.UnknownTimeZoneError:
        await query.answer("Unknown timezone — choose from the list.", show_alert=True)
        return QM_SET_SLOTS_TZ

    w = _w(context)
    user_id: int   = query.from_user.id  # type: ignore[union-attr]
    chat_id: int   = w["slots_chat_id"]
    chat_title: str = w.get("slots_chat_title", str(chat_id))
    slots: list[str] = w["pending_slots"]
    tz_label = next((lbl for k, lbl in TIMEZONES if k == tz_key), tz_key)

    await query.answer()
    try:
        await upsert_queue_slots(user_id, chat_id, slots, tz_key)
    except Exception as exc:
        logger.exception("upsert_queue_slots failed: %s", exc)
        _clear(context)
        chat_id_dm = query.message.chat_id
        await chat_cleanup.cleanup(context, chat_id_dm, keep_message_id=query.message.message_id)
        await query.edit_message_text(f"❌ Database error: `{exc}`", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    slot_display = "  •  ".join(slots)
    _clear(context)
    # ── Cleanup wizard messages, keep this success message ──
    await chat_cleanup.cleanup(context, query.message.chat_id, keep_message_id=query.message.message_id)
    await query.edit_message_text(
        f"✅ *Daily slots saved!*\n\n"
        f"🗂 Chat: *{chat_title}* (`{chat_id}`)\n"
        f"⏰ Slots: `{slot_display}`\n"
        f"🌍 Timezone: `{tz_label}`\n\n"
        "Now use *Add Content to Queue* to schedule posts!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_menu_keyboard(context.user_data.get("language", "en")),
    )
    return QM_MENU


async def cb_qm_tz_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Pagination for the QM timezone picker (Set Slots)."""
    query    = update.callback_query
    page_str = (query.data or "").replace(CB_QM_TZ_PAGE, "")
    page     = int(page_str) if page_str.isdigit() else 0
    _w(context)["qm_tz_page"] = page
    await query.answer()
    await query.edit_message_text(
        "🌍 *Select your timezone:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_tz_keyboard(page),
    )
    return QM_SET_SLOTS_TZ


async def cb_back_qm_slot_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Back from slots input → re-show group picker."""
    query = update.callback_query
    _w(context).pop("slots_chat_id", None)
    _w(context).pop("slots_chat_title", None)

    user_id      = query.from_user.id
    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id)

    if groups:
        await _edit(query,
            "⏰ *Set Daily Slots — Step 1 of 3*\n\n"
            f"🗂 Choose the group or channel:\n"
            f"_(Showing {min(_QM_CHAT_PAGE_SIZE, len(groups))} of {len(groups)} known chats)_",
            _qm_group_picker_keyboard(
                groups, 0,
                CB_QM_SS_PICK, CB_QM_SS_PAGE, CB_QM_SS_MANUAL,
                CB_BACK_QM_MENU,
            ),
        )
    else:
        await _edit(query,
            "⏰ *Set Daily Slots — Step 1 of 3*\n\n"
            "⚠️ No groups found. Enter Chat ID manually.",
            _qm_no_groups_keyboard(CB_QM_SS_MANUAL, CB_BACK_QM_MENU, bot_username),
        )
    return QM_SET_SLOTS_CHAT_ID


async def cb_back_qm_slot_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Back from timezone picker → re-ask for slots."""
    query   = update.callback_query
    chat_id = _w(context).get("slots_chat_id", "?")
    title   = _w(context).get("slots_chat_title", str(chat_id))
    _w(context).pop("pending_slots", None)
    await query.answer()
    await query.edit_message_text(
        f"✅ Chat: *{title}* (`{chat_id}`)\n\n"
        "⏰ *Set Daily Slots — Step 2 of 3*\n\n"
        "Enter the daily posting times separated by commas or spaces.\n\n"
        "Example: `09:00, 14:00, 20:00`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=nav_keyboard(back_data=CB_BACK_QM_SLOT_CHAT),
    )
    return QM_SET_SLOTS


# ── Add Content to Queue ──

async def _has_slots(user_id: int, chat_id: int) -> bool:
    """Return True only when a slot document with ≥1 slot already exists."""
    doc = await get_queue(user_id, chat_id)
    return bool(doc and doc.get("slots"))


def _no_slots_warning_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown when the chosen chat has no time slots configured."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏰ Set Daily Slots Now", callback_data=CB_QM_SET_SLOTS)],
        [InlineKeyboardButton("⬅️ Back to Menu",        callback_data=CB_BACK_QM_MENU)],
    ])


async def cb_qm_add_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry to Add Content wizard — show group picker."""
    query = update.callback_query
    # Clear only add-content wizard keys
    for k in ("add_chat_id", "add_chat_title", "media_file_id", "media_type",
              "content_text", "add_date_str", "add_tz", "add_tz_page"):
        _w(context).pop(k, None)

    user_id      = query.from_user.id
    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id)

    if groups:
        await _edit(query,
            "➕ *Add Content to Queue — Step 1 of 4*\n\n"
            f"🗂 Choose the group or channel:\n"
            f"_(Showing {min(_QM_CHAT_PAGE_SIZE, len(groups))} of {len(groups)} known chats)_",
            _qm_group_picker_keyboard(
                groups, 0,
                CB_QM_AC_PICK, CB_QM_AC_PAGE, CB_QM_AC_MANUAL,
                CB_BACK_QM_MENU,
            ),
        )
    else:
        await _edit(query,
            "➕ *Add Content to Queue — Step 1 of 4*\n\n"
            "⚠️ *No groups found.*\n\n"
            "Add the bot to your group first, or enter the Chat ID manually.",
            _qm_no_groups_keyboard(CB_QM_AC_MANUAL, CB_BACK_QM_MENU, bot_username),
        )
    return QM_ADD_CHAT_ID


async def cb_qm_ac_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — user tapped a group button."""
    query = update.callback_query
    raw   = (query.data or "").replace(CB_QM_AC_PICK, "", 1)
    try:
        chat_id = int(raw)
    except ValueError:
        await query.answer("Invalid selection.", show_alert=True)
        return QM_ADD_CHAT_ID

    user_id = query.from_user.id
    try:
        target_chat = await ensure_chat_access(query.get_bot(), chat_id, user_id)
    except ChatAccessError as exc:
        await query.answer(str(exc), show_alert=True)
        return QM_ADD_CHAT_ID
    groups  = await list_groups_for_user(user_id)
    title_map = {g["chat_id"]: (g.get("title") or str(g["chat_id"])) for g in groups}
    title = target_chat.title or title_map.get(chat_id, str(chat_id))

    _w(context)["add_chat_id"]    = chat_id
    _w(context)["add_chat_title"] = title

    # Guard: require slots to be set before allowing content to be queued
    if not await _has_slots(user_id, chat_id):
        await _edit(query,
            f"⚠️ *No Time Slots Set — {title}*\n\n"
            "Queue posting needs daily time slots configured first.\n\n"
            "Set them up in this order:\n"
            "① ⏰ *Set Daily Slots* ← do this first\n"
            "② ➕ *Add Content*     ← then add your posts",
            _no_slots_warning_keyboard(),
        )
        return QM_ADD_CHAT_ID

    await _edit(query,
        f"✅ Selected: *{title}* (`{chat_id}`)\n\n"
        "➕ *Add Content — Step 2 of 4*\n\n"
        "📝 Now send the content you want to add to the queue.\n"
        "_(Text, photo, video, document, audio, etc.)_",
        nav_keyboard(back_data=CB_BACK_QM_AC_CHAT),
    )
    return QM_ADD_CONTENT


async def cb_qm_ac_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — paginate group list."""
    query = update.callback_query
    raw   = (query.data or "").replace(CB_QM_AC_PAGE, "", 1)
    page  = int(raw) if raw.isdigit() else 0
    user_id      = query.from_user.id
    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id)
    total  = len(groups)
    start  = page * _QM_CHAT_PAGE_SIZE
    shown  = min(_QM_CHAT_PAGE_SIZE, total - start)
    await _edit(query,
        "➕ *Add Content to Queue — Step 1 of 4*\n\n"
        f"🗂 Choose the group or channel:\n"
        f"_(Showing {shown} of {total} known chats)_",
        _qm_group_picker_keyboard(
            groups, page,
            CB_QM_AC_PICK, CB_QM_AC_PAGE, CB_QM_AC_MANUAL,
            CB_BACK_QM_MENU,
        ),
    )
    return QM_ADD_CHAT_ID


async def cb_qm_ac_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — user wants to type Chat ID manually."""
    query = update.callback_query
    await _edit(query,
        "➕ *Add Content to Queue — Step 1 of 4*\n\n"
        "✏️ Type the *Chat ID* of the target chat:\n\n"
        "Example: `-1001234567890`",
        nav_keyboard(back_data=CB_BACK_QM_MENU),
    )
    return QM_ADD_CHAT_ID


async def recv_qm_add_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — receive manually-typed Chat ID."""
    text = (update.message.text or "").strip()
    try:
        chat_id = int(text)
    except ValueError:
        await update.message.reply_text("⚠️ Enter a valid numeric Chat ID.", parse_mode=ParseMode.MARKDOWN)
        return QM_ADD_CHAT_ID

    try:
        target_chat = await ensure_chat_access(context.bot, chat_id, update.effective_user.id)
    except ChatAccessError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return QM_ADD_CHAT_ID
    _w(context)["add_chat_id"]    = chat_id
    _w(context)["add_chat_title"] = target_chat.title or str(chat_id)

    # Guard: require slots to be set before allowing content to be queued
    user_id = update.message.from_user.id
    if not await _has_slots(user_id, chat_id):
        await update.message.reply_text(
            f"⚠️ *No Time Slots Set — Chat `{chat_id}`*\n\n"
            "Queue posting needs daily time slots configured first.\n\n"
            "Set them up in this order:\n"
            "① ⏰ *Set Daily Slots* ← do this first\n"
            "② ➕ *Add Content*     ← then add your posts",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_no_slots_warning_keyboard(),
        )
        return QM_ADD_CHAT_ID

    await update.message.reply_text(
        f"✅ Chat: `{chat_id}`\n\n"
        "➕ *Add Content — Step 2 of 4*\n\n"
        "📝 Send the content you want to add to the queue.\n"
        "_(Text, photo, video, document, audio, etc.)_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=nav_keyboard(back_data=CB_BACK_QM_AC_CHAT),
    )
    return QM_ADD_CONTENT


async def recv_qm_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive content and show the queue's daily slots before confirmation."""
    msg = update.message
    w = _w(context)
    if msg.photo:
        w["media_file_id"], w["media_type"], w["content_text"] = msg.photo[-1].file_id, "photo", msg.caption
    elif msg.video:
        w["media_file_id"], w["media_type"], w["content_text"] = msg.video.file_id, "video", msg.caption
    elif msg.document:
        w["media_file_id"], w["media_type"], w["content_text"] = msg.document.file_id, "document", msg.caption
    elif msg.audio:
        w["media_file_id"], w["media_type"], w["content_text"] = msg.audio.file_id, "audio", msg.caption
    elif msg.animation:
        w["media_file_id"], w["media_type"], w["content_text"] = msg.animation.file_id, "animation", msg.caption
    elif msg.voice:
        w["media_file_id"], w["media_type"], w["content_text"] = msg.voice.file_id, "voice", msg.caption
    elif msg.text:
        w["media_file_id"], w["media_type"], w["content_text"] = None, None, msg.text
    else:
        await msg.reply_text("⚠️ Unsupported content type. Send text, photo, video, document, audio, animation or voice.")
        return QM_ADD_CONTENT

    queue = await get_queue(msg.from_user.id, int(w["add_chat_id"]))
    if not queue or not queue.get("slots"):
        await msg.reply_text(
            "⚠️ Daily slots are not configured for this chat. Set Daily Slots first.",
            reply_markup=_no_slots_warning_keyboard(),
        )
        return QM_ADD_CHAT_ID

    tz_key = queue.get("timezone", "UTC")
    tz_label = next((label for key, label in TIMEZONES if key == tz_key), tz_key)
    slots_text = " • ".join(queue.get("slots", []))
    content_preview = (w.get("content_text") or "")[:120]
    if w.get("media_type"):
        content_preview = f"[{w['media_type'].upper()}] {content_preview}"
    w["add_tz"] = tz_key

    await msg.reply_text(
        "📋 *Queue Item Review*\n\n"
        f"🗂 Chat: *{w.get('add_chat_title', w['add_chat_id'])}*\n"
        f"📝 Content: `{content_preview or 'Media'}`\n"
        f"⏰ Daily slots: `{slots_text}`\n"
        f"🌍 Timezone: *{tz_label}*\n\n"
        "This item will be added to the end of the queue and published at the next available daily slot.\n"
        "Tap *Confirm* to add it.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=confirm_keyboard(CB_QM_CONFIRM_ADD, CB_BACK_QM_AC_CONTENT),
    )
    return QM_CONFIRM_SLOT


async def recv_qm_add_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — Step 3: receive date/time string, ask for timezone."""
    text = (update.message.text or "").strip()
    # Validate format (we need timezone before we can compute the actual dt)
    is_full_dt = bool(_DT_RE.match(text))
    is_hhmm    = bool(_TIME_RE.match(text))
    if not (is_full_dt or is_hhmm):
        await update.message.reply_text(
            "⚠️ *Invalid format.*\n\n"
            "Use one of:\n"
            "• `DD/MM/YYYY HH:MM` — e.g. `25/12/2025 09:00`\n"
            "• `HH:MM` — e.g. `09:00`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return QM_ADD_DATE

    # Additional validation for full date
    if is_full_dt:
        m = _DT_RE.match(text)
        d, mo, y, h, mi = (int(x) for x in m.groups())
        try:
            datetime(y, mo, d, h, mi)
        except ValueError:
            await update.message.reply_text(
                "⚠️ That date doesn't exist (e.g. 30/02/2025). Please enter a valid date.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return QM_ADD_DATE

    _w(context)["add_date_str"]  = text
    _w(context)["add_tz_page"]   = 0
    await update.message.reply_text(
        f"✅ Date/time: `{text}`\n\n"
        "🌍 *Add Content — Step 4 of 4 — Timezone*\n\n"
        "Select the timezone for this posting time:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_ac_tz_keyboard(0),
    )
    return QM_ADD_TZ


async def cb_qm_ac_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — user picked timezone; show confirmation summary."""
    query  = update.callback_query
    tz_key = (query.data or "").replace("qmactz:", "", 1)
    try:
        pytz.timezone(tz_key)
    except pytz.exceptions.UnknownTimeZoneError:
        await query.answer("Unknown timezone — choose from the list.", show_alert=True)
        return QM_ADD_TZ

    await query.answer()
    w = _w(context)
    w["add_tz"] = tz_key

    date_str  = w.get("add_date_str", "")
    tz_label  = next((lbl for k, lbl in TIMEZONES if k == tz_key), tz_key)
    chat_id   = w.get("add_chat_id", "?")
    chat_title = w.get("add_chat_title", str(chat_id))
    media_type = w.get("media_type")

    # Validate that date_str + timezone produces a valid future datetime
    dt_utc = _parse_run_time(date_str, tz_key)
    if dt_utc is None:
        await query.edit_message_text(
            "⚠️ Could not parse the date/time with the selected timezone.\n"
            "Please go back and try again.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_QM_AC_DATE),
            ]]),
        )
        return QM_ADD_TZ

    now_utc = datetime.now(tz=timezone.utc)
    if dt_utc <= now_utc:
        await query.edit_message_text(
            f"⚠️ That time (`{date_str}` in `{tz_label}`) is in the past.\n"
            "Please go back and enter a future date/time.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_QM_AC_DATE),
            ]]),
        )
        return QM_ADD_TZ

    w["add_dt_utc_iso"] = dt_utc.isoformat()

    content_preview = (w.get("content_text") or "")[:60]
    if media_type:
        content_preview = f"[{media_type.upper()}] {content_preview}"

    # Format display time in selected timezone
    try:
        zone_for_display = pytz.timezone(tz_key)
        dt_local_display = dt_utc.astimezone(zone_for_display).strftime("%d/%m/%Y %H:%M")
    except Exception:
        dt_local_display = date_str

    await query.edit_message_text(
        f"📋 *Queue — Confirmation*\n\n"
        f"🗂 Chat: *{chat_title}* (`{chat_id}`)\n"
        f"📝 Content: `{content_preview or '—'}`\n"
        f"🕐 Post at: `{dt_local_display}` ({tz_label})\n\n"
        "Tap *✅ Confirm* to schedule:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=confirm_keyboard(CB_QM_CONFIRM_ADD, CB_BACK_QM_AC_TZ),
    )
    return QM_CONFIRM_SLOT


async def cb_qm_ac_tz_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — paginate timezone picker."""
    query    = update.callback_query
    page_str = (query.data or "").replace(CB_QM_AC_TZ_PAGE, "")
    page     = int(page_str) if page_str.isdigit() else 0
    _w(context)["add_tz_page"] = page
    await query.answer()
    await query.edit_message_text(
        "🌍 *Select your timezone:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_ac_tz_keyboard(page),
    )
    return QM_ADD_TZ


async def cb_back_qm_ac_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — back to group picker from content step."""
    query = update.callback_query
    for k in ("add_chat_id", "add_chat_title", "media_file_id", "media_type",
              "content_text", "add_date_str", "add_tz", "add_tz_page", "add_dt_utc_iso"):
        _w(context).pop(k, None)

    user_id      = query.from_user.id
    bot_username = (await query.get_bot().get_me()).username
    groups = await list_groups_for_user(user_id)

    if groups:
        await _edit(query,
            "➕ *Add Content to Queue — Step 1 of 4*\n\n"
            f"🗂 Choose the group or channel:\n"
            f"_(Showing {min(_QM_CHAT_PAGE_SIZE, len(groups))} of {len(groups)} known chats)_",
            _qm_group_picker_keyboard(
                groups, 0,
                CB_QM_AC_PICK, CB_QM_AC_PAGE, CB_QM_AC_MANUAL,
                CB_BACK_QM_MENU,
            ),
        )
    else:
        await _edit(query,
            "➕ *Add Content to Queue — Step 1 of 4*\n\n"
            "⚠️ No groups found. Enter Chat ID manually.",
            _qm_no_groups_keyboard(CB_QM_AC_MANUAL, CB_BACK_QM_MENU, bot_username),
        )
    return QM_ADD_CHAT_ID


async def cb_back_qm_ac_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — back to content send step from date step."""
    query = update.callback_query
    w = _w(context)
    for k in ("media_file_id", "media_type", "content_text", "add_date_str",
              "add_tz", "add_tz_page", "add_dt_utc_iso"):
        w.pop(k, None)
    chat_id   = w.get("add_chat_id", "?")
    chat_title = w.get("add_chat_title", str(chat_id))
    await _edit(query,
        f"✅ Chat: *{chat_title}* (`{chat_id}`)\n\n"
        "➕ *Add Content — Step 2 of 4*\n\n"
        "📝 Send the content you want to add to the queue.\n"
        "_(Text, photo, video, document, audio, etc.)_",
        nav_keyboard(back_data=CB_BACK_QM_AC_CHAT),
    )
    return QM_ADD_CONTENT


async def cb_back_qm_ac_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — back to date/time entry from timezone step."""
    query = update.callback_query
    _w(context).pop("add_tz", None)
    _w(context).pop("add_dt_utc_iso", None)
    await _edit(query,
        "🕐 *Add Content — Step 3 of 4 — Posting Date/Time*\n\n"
        "When should this content be posted?\n\n"
        "• `DD/MM/YYYY HH:MM` — specific date (e.g. `25/12/2025 09:00`)\n"
        "• `HH:MM` — today at that time (tomorrow if already past)",
        nav_keyboard(back_data=CB_BACK_QM_AC_CONTENT),
    )
    return QM_ADD_DATE


async def cb_back_qm_ac_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Add Content — back to timezone picker from confirm step."""
    query = update.callback_query
    _w(context).pop("add_dt_utc_iso", None)
    page = _w(context).get("add_tz_page", 0)
    date_str = _w(context).get("add_date_str", "")
    await query.answer()
    await query.edit_message_text(
        f"✅ Date/time: `{date_str}`\n\n"
        "🌍 *Add Content — Step 4 of 4 — Timezone*\n\n"
        "Select the timezone for this posting time:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_ac_tz_keyboard(page),
    )
    return QM_ADD_TZ


async def cb_qm_confirm_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save an item to the daily queue; scheduler publishes it at a configured slot."""
    query = update.callback_query
    if not query or not query.from_user or not query.message:
        return QM_MENU
    await query.answer("Adding to queue…")
    w = _w(context)
    user_id = query.from_user.id
    chat_id = w.get("add_chat_id")
    if not chat_id:
        _clear(context)
        await query.edit_message_text("⚠️ This setup expired. Please start Add Content again.", reply_markup=_qm_menu_keyboard(context.user_data.get("language", "en")))
        return QM_MENU

    tz_key = w.get("add_tz", "UTC")
    tz_label = next((label for key, label in TIMEZONES if key == tz_key), tz_key)
    try:
        target_chat = await ensure_chat_access(context.bot, int(chat_id), user_id)
        payload = {"text": w.get("content_text"), "media_file_id": w.get("media_file_id"), "media_type": w.get("media_type")}
        if not payload["text"] and not payload["media_file_id"]:
            raise ValueError("Queue content is empty. Send a text message or supported media.")
        item_id = await add_to_queue(user_id, int(chat_id), payload)
        queue = await get_queue(user_id, int(chat_id))
    except (ChatAccessError, ValueError) as exc:
        _clear(context)
        await query.edit_message_text(f"⚠️ {exc}", reply_markup=_qm_menu_keyboard(context.user_data.get("language", "en")))
        return QM_MENU
    except Exception:
        logger.exception("Failed to save queue item.")
        _clear(context)
        await query.edit_message_text(
            "❌ I couldn't save this item. Your existing queue has not been cleared. Please try again.",
            reply_markup=_qm_menu_keyboard(context.user_data.get("language", "en")),
        )
        return QM_MENU

    next_run = queue.get("next_run_at") if queue else None
    next_text = next_run.astimezone(pytz.timezone(tz_key)).strftime("%d/%m/%Y %H:%M") if next_run else "the next available daily slot"
    _clear(context)
    await chat_cleanup.cleanup(context, query.message.chat_id, keep_message_id=query.message.message_id)
    await query.edit_message_text(
        "✅ *Added to your daily queue*\n\n"
        f"🗂 Chat: *{target_chat.title or chat_id}*\n"
        f"🌍 Timezone: *{tz_label}*\n"
        f"🕐 Next posting: *{next_text}*\n"
        f"🆔 Queue item: `{item_id[:10]}`\n\n"
        "The bot publishes one pending item at each configured daily slot. Use *View Queue* to check its status.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_menu_keyboard(context.user_data.get("language", "en")),
    )
    return QM_MENU


# ── Clear Queue ──

async def cb_qm_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _edit(query,
        "🗑 *Clear Queue*\n\n"
        "⚠️ This will delete *all* queued content for all your chats.\n"
        "Are you sure?",
        InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, clear all", callback_data=CB_QM_CLEAR_YES),
                InlineKeyboardButton("⬅️ Back",           callback_data=CB_BACK_QM_MENU),
            ],
        ]),
    )
    return QM_MENU


async def cb_qm_clear_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Clearing…")
    user_id: int = query.from_user.id  # type: ignore[union-attr]

    try:
        db = await get_db()
        result = await db[COL_QUEUE_SLOTS].delete_many({"user_id": user_id})
        count = result.deleted_count
    except Exception as exc:
        await query.edit_message_text(f"❌ Error: `{exc}`", parse_mode=ParseMode.MARKDOWN)
        return QM_MENU

    await query.edit_message_text(
        f"✅ Cleared `{count}` queue(s).",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_menu_keyboard(context.user_data.get("language", "en")),
    )
    return QM_MENU


# ── Back to QM menu ──

async def cb_back_qm_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _edit(query,
        "🗂 *Queue Manager*\n\nWhat would you like to do?",
        _qm_menu_keyboard(context.user_data.get("language", "en")),
    )
    return QM_MENU


# ============================================================================
#  B) MEDIA POOL — guided random-post workflow
# ============================================================================

_MP_POOL_PAGE_SIZE = 5
_MP_QUICK_INTERVALS: dict[str, tuple[int, str, str]] = {
    "15m": (15, "minutes", "15 minutes"),
    "30m": (30, "minutes", "30 minutes"),
    "1h": (1, "hours", "1 hour"),
    "3h": (3, "hours", "3 hours"),
    "6h": (6, "hours", "6 hours"),
    "12h": (12, "hours", "12 hours"),
    "24h": (24, "hours", "24 hours"),
}


def _mp_menu_text() -> str:
    return (
        "🎲 Media Pool\n\n"
        "Automatically publish one random item from a collection at a regular interval.\n\n"
        "How it works:\n"
        "1. Choose a group or channel.\n"
        "2. Add your content items. You can send them one after another.\n"
        "3. Choose when the bot should publish.\n\n"
        "Each item is used once in a cycle. After every item has been published, "
        "the next cycle starts automatically."
    )


def _mp_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Content", callback_data=CB_MP_ADD_ITEM)],
        [InlineKeyboardButton("⏱ Set Posting Schedule", callback_data=CB_MP_SET_INTERVAL)],
        [
            InlineKeyboardButton("📊 Pool Overview", callback_data=CB_MP_VIEW),
            InlineKeyboardButton("🛠 Manage Pool", callback_data=CB_MP_MANAGE),
        ],
        [
            InlineKeyboardButton("🏠 Main Menu", callback_data=CB_MAIN_MENU),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ],
    ])


def _mp_add_item_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ Configure Posting Schedule", callback_data=CB_MP_ADD_SCHEDULE)],
        [InlineKeyboardButton("✅ Finish Adding", callback_data=CB_MP_ADD_DONE)],
        [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
    ])


def _mp_quick_interval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("15 min", callback_data=f"{CB_MP_QUICK_PREFIX}15m"),
            InlineKeyboardButton("30 min", callback_data=f"{CB_MP_QUICK_PREFIX}30m"),
            InlineKeyboardButton("1 hour", callback_data=f"{CB_MP_QUICK_PREFIX}1h"),
        ],
        [
            InlineKeyboardButton("3 hours", callback_data=f"{CB_MP_QUICK_PREFIX}3h"),
            InlineKeyboardButton("6 hours", callback_data=f"{CB_MP_QUICK_PREFIX}6h"),
            InlineKeyboardButton("12 hours", callback_data=f"{CB_MP_QUICK_PREFIX}12h"),
        ],
        [
            InlineKeyboardButton("24 hours", callback_data=f"{CB_MP_QUICK_PREFIX}24h"),
            InlineKeyboardButton("✏️ Custom", callback_data=CB_MP_CUSTOM_INTERVAL),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_MP_MENU),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ],
    ])


def _mp_first_run_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Start as soon as possible", callback_data=CB_MP_FIRST_NOW)],
        [InlineKeyboardButton("🕒 Choose a specific time", callback_data=CB_MP_FIRST_CUSTOM)],
        [
            InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_MP_IV),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ],
    ])


def _mp_group_picker_keyboard(groups: list[dict], page: int) -> InlineKeyboardMarkup:
    return _qm_group_picker_keyboard(
        groups,
        page,
        CB_MP_PICK,
        CB_MP_PAGE,
        CB_MP_MANUAL,
        CB_BACK_MP_MENU,
    )


def _mp_manage_picker_keyboard(
    pools: list[dict], group_titles: dict[int, str], page: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    start = page * _MP_POOL_PAGE_SIZE
    end = min(start + _MP_POOL_PAGE_SIZE, len(pools))
    for pool in pools[start:end]:
        chat_id = int(pool["chat_id"])
        count = len(pool.get("items", []))
        title = escape_markdown(
            group_titles.get(chat_id) or f"Chat {chat_id}", version=1
        )
        rows.append([
            InlineKeyboardButton(
                f"🗂 {(group_titles.get(chat_id) or f'Chat {chat_id}')[:28]} · {count} item(s)",
                callback_data=f"{CB_MP_MANAGE_PICK}{chat_id}",
            )
        ])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "⬅️ Prev", callback_data=f"{CB_MP_MANAGE_PAGE}{page - 1}"
        ))
    if end < len(pools):
        nav.append(InlineKeyboardButton(
            "Next ➡️", callback_data=f"{CB_MP_MANAGE_PAGE}{page + 1}"
        ))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_MP_MENU),
        InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _mp_manage_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Reset Posting Cycle", callback_data=CB_MP_RESET)],
        [InlineKeyboardButton("🗑 Clear This Pool", callback_data=CB_MP_CLEAR)],
        [
            InlineKeyboardButton("⬅️ All Pools", callback_data=CB_BACK_MP_MANAGE),
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ],
    ])


def _mp_manage_confirm_keyboard(confirm_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data=confirm_callback),
            InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_MP_DETAILS),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
    ])


async def _mp_reply_or_edit(
    update: Update,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
    elif update.effective_message is not None:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )


async def enter_media_pool(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    _clear_mp(context)
    chat_cleanup.reset(context.application.bot_data, query.message.chat_id)
    chat_cleanup.track(
        context.application.bot_data,
        query.message.chat_id,
        query.message.message_id,
    )
    await _edit(query, _mp_menu_text(), _mp_menu_keyboard())
    return MP_MENU


async def _mp_show_group_picker(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
) -> int:
    user_id = query.from_user.id
    try:
        groups = await list_groups_for_user(user_id)
    except Exception:
        logger.exception("Media Pool group picker failed for user_id=%s", user_id)
        await query.answer("Unable to load your groups. Please try again.", show_alert=True)
        return MP_CHAT_ID

    if not groups:
        bot_username = (await query.get_bot().get_me()).username or "YourSchedulerBot"
        mode_label = (
            "add content to"
            if _pm(context).get("mode") == "add"
            else "set a posting schedule for"
        )
        await _edit(
            query,
            "No groups or channels are linked to your account yet.\n\n"
            "Add the bot to your group or channel first, then make sure you are an administrator. "
            "You can also enter a Chat ID manually.\n\n"
            f"Choose where you want to {mode_label} the Media Pool:",
            _qm_no_groups_keyboard(
                CB_MP_MANUAL,
                CB_BACK_MP_MENU,
                bot_username,
            ),
        )
        return MP_CHAT_ID

    total = len(groups)
    page = max(0, min(page, (total - 1) // _QM_CHAT_PAGE_SIZE))
    await _edit(
        query,
        "Step 1 of 3 — Select a Group or Channel\n\n"
        "Choose the destination where the Media Pool will save content and publish posts.\n"
        f"Showing {page * _QM_CHAT_PAGE_SIZE + 1}–"
        f"{min((page + 1) * _QM_CHAT_PAGE_SIZE, total)} of {total}.",
        _mp_group_picker_keyboard(groups, page),
    )
    return MP_CHAT_ID


async def cb_mp_add_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _pm(context).clear()
    _pm(context)["mode"] = "add"
    return await _mp_show_group_picker(update.callback_query, context)


async def cb_mp_set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _pm(context).clear()
    _pm(context)["mode"] = "interval"
    return await _mp_show_group_picker(update.callback_query, context)


async def cb_mp_group_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    raw = (query.data or "").replace(CB_MP_PAGE, "", 1)
    page = int(raw) if raw.isdigit() else 0
    return await _mp_show_group_picker(query, context, page)


async def cb_mp_group_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    raw = (query.data or "").replace(CB_MP_PICK, "", 1)
    try:
        chat_id = int(raw)
        target_chat = await ensure_chat_access(
            query.get_bot(),
            chat_id,
            query.from_user.id,
        )
    except ValueError:
        await query.answer("Invalid group selection. Please choose again.", show_alert=True)
        return MP_CHAT_ID
    except ChatAccessError as exc:
        await query.answer(str(exc), show_alert=True)
        return MP_CHAT_ID
    return await _mp_continue_after_chat_selection(update, context, target_chat)


async def cb_mp_manual_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _edit(
        query,
        "Enter the numeric Chat ID of your group or channel.\n\n"
        "Tip: Send /id in the target group to see its Chat ID. "
        "Example: -1001234567890",
        nav_keyboard(back_data=CB_BACK_MP_MENU),
    )
    return MP_CHAT_ID


async def _mp_continue_after_chat_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_chat: Any,
) -> int:
    pm = _pm(context)
    chat_id = int(target_chat.id)
    title = target_chat.title or str(chat_id)
    pm["mp_chat_id"] = chat_id
    pm["mp_chat_title"] = title

    if pm.get("mode") == "interval":
        try:
            db = await get_db()
            pool = await db[COL_MEDIA_POOLS].find_one(
                {"user_id": update.effective_user.id, "chat_id": chat_id}
            )
            item_count = len(pool.get("items", [])) if pool else 0
        except Exception:
            logger.exception("Could not inspect Media Pool for chat_id=%s", chat_id)
            await _mp_reply_or_edit(
                update,
                "I couldn't check this group's content right now. Please try again.",
                nav_keyboard(back_data=CB_BACK_MP_MENU),
            )
            return MP_CHAT_ID

        if item_count == 0:
            pm["mode"] = "add"
            await _mp_reply_or_edit(
                update,
                f"Selected: {escape_markdown(title, version=1)}\n\n"
                "This group does not have any Media Pool content yet. "
                "Add at least one item before creating a posting schedule.",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "➕ Add Content to This Group",
                        callback_data=CB_MP_ADD_SELECTED,
                    )],
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data=CB_BACK_MP_MENU)],
                    [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
                ]),
            )
            return MP_CHAT_ID

        return await _mp_prompt_interval(update, context)

    await _mp_prompt_add_item(update, context)
    return MP_ADD_ITEM


async def _mp_prompt_add_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pm = _pm(context)
    title = escape_markdown(pm.get("mp_chat_title", "Selected group"), version=1)
    await _mp_reply_or_edit(
        update,
        f"Selected destination: {title}\n\n"
        "Step 2 of 3 — Add Content\n\n"
        "Send a text message, photo, video, GIF/animation, document, audio, or voice message. "
        "The bot saves each message as one item in this group's pool.\n\n"
        "You can send several items one after another. When you are finished, tap "
        "Finish Adding. Captions are kept with their media.",
        _mp_add_item_keyboard(),
    )


async def cb_mp_add_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    if not pm.get("mp_chat_id"):
        await query.answer("Please select a group again.", show_alert=True)
        _clear_mp(context)
        await _edit(query, _mp_menu_text(), _mp_menu_keyboard())
        return MP_MENU
    pm["mode"] = "add"
    await _mp_prompt_add_item(update, context)
    return MP_ADD_ITEM


async def recv_mp_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    try:
        chat_id = int(raw)
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid numeric Chat ID, for example -1001234567890."
        )
        return MP_CHAT_ID

    try:
        target_chat = await ensure_chat_access(
            context.bot,
            chat_id,
            update.effective_user.id,
        )
    except ChatAccessError as exc:
        await update.message.reply_text(
            f"Cannot use this chat: {escape_markdown(str(exc), version=1)}"
        )
        return MP_CHAT_ID

    return await _mp_continue_after_chat_selection(update, context, target_chat)


async def recv_mp_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    pm = _pm(context)
    if not pm.get("mp_chat_id"):
        await msg.reply_text("Your group selection expired. Please start Add Content again.")
        _clear_mp(context)
        return MP_MENU

    item: dict[str, Any] = {"posted": False}
    if msg.photo:
        item.update(media_file_id=msg.photo[-1].file_id, media_type="photo", text=msg.caption)
    elif msg.video:
        item.update(media_file_id=msg.video.file_id, media_type="video", text=msg.caption)
    elif msg.document:
        item.update(media_file_id=msg.document.file_id, media_type="document", text=msg.caption)
    elif msg.audio:
        item.update(media_file_id=msg.audio.file_id, media_type="audio", text=msg.caption)
    elif msg.animation:
        item.update(media_file_id=msg.animation.file_id, media_type="animation", text=msg.caption)
    elif msg.voice:
        item.update(media_file_id=msg.voice.file_id, media_type="voice", text=msg.caption)
    elif msg.text:
        item.update(media_file_id=None, media_type=None, text=msg.text)
    else:
        await msg.reply_text(
            "Unsupported content type. Send text, photo, video, GIF/animation, document, audio, or voice."
        )
        return MP_ADD_ITEM

    user_id = msg.from_user.id
    chat_id = int(pm["mp_chat_id"])
    try:
        await add_to_media_pool(user_id, chat_id, item)
        db = await get_db()
        pool = await db[COL_MEDIA_POOLS].find_one(
            {"user_id": user_id, "chat_id": chat_id}
        )
        count = len(pool.get("items", [])) if pool else 1
    except Exception:
        logger.exception("Could not save Media Pool item for user_id=%s chat_id=%s", user_id, chat_id)
        await msg.reply_text(
            "The item could not be saved. Please try sending it again."
        )
        return MP_ADD_ITEM

    title = escape_markdown(pm.get("mp_chat_title", str(chat_id)), version=1)
    await msg.reply_text(
        f"✅ Item saved to {title}.\n"
        f"Items in this pool: {count}\n\n"
        "Send the next item to continue, or use the buttons below when you are finished.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_mp_add_item_keyboard(),
    )
    return MP_ADD_ITEM


async def cb_mp_add_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    _clear_mp(context)
    await _edit(
        query,
        "✅ Content collection saved.\n\n"
        "You can review your pools or configure a posting schedule from this menu.",
        _mp_menu_keyboard(),
    )
    return MP_MENU


async def cb_mp_add_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    chat_id = pm.get("mp_chat_id")
    if not chat_id:
        await query.answer("Your group selection expired. Please start again.", show_alert=True)
        _clear_mp(context)
        await _edit(query, _mp_menu_text(), _mp_menu_keyboard())
        return MP_MENU
    try:
        db = await get_db()
        pool = await db[COL_MEDIA_POOLS].find_one(
            {"user_id": query.from_user.id, "chat_id": int(chat_id)}
        )
        if not pool or not pool.get("items"):
            await query.answer("Add at least one content item before scheduling.", show_alert=True)
            return MP_ADD_ITEM
    except Exception:
        logger.exception("Could not verify Media Pool items before scheduling")
        await query.answer("Could not check this pool. Please try again.", show_alert=True)
        return MP_ADD_ITEM
    pm["mode"] = "interval"
    return await _mp_prompt_interval(update, context)


async def _mp_prompt_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pm = _pm(context)
    title = escape_markdown(pm.get("mp_chat_title", "Selected group"), version=1)
    await _mp_reply_or_edit(
        update,
        f"Selected destination: {title}\n\n"
        "Step 2 of 3 — Choose a Posting Interval\n\n"
        "How often should the bot publish one random item? Choose a common interval or select Custom.",
        _mp_quick_interval_keyboard(),
    )
    return MP_SET_INTERVAL_VALUE


async def cb_mp_custom_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _edit(
        query,
        "Enter a positive whole number for the interval.\n\n"
        "Example: enter 6, then choose Hours to post every 6 hours.",
        nav_keyboard(back_data=CB_BACK_MP_MENU),
    )
    return MP_SET_INTERVAL_VALUE


async def cb_mp_quick_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    choice = (query.data or "").replace(CB_MP_QUICK_PREFIX, "", 1)
    selected = _MP_QUICK_INTERVALS.get(choice)
    if selected is None:
        await query.answer("Invalid interval. Please choose again.", show_alert=True)
        return MP_SET_INTERVAL_VALUE
    value, unit, label = selected
    pm = _pm(context)
    pm["interval_value"] = value
    pm["interval_unit"] = unit
    pm["interval_label"] = label
    await query.answer()
    await _mp_prompt_first_run(query, context)
    return MP_SET_FIRST_RUN


async def recv_mp_interval_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0 or int(raw) > 100000:
        await update.message.reply_text(
            "Enter a whole number between 1 and 100000."
        )
        return MP_SET_INTERVAL_VALUE
    pm = _pm(context)
    pm["interval_value"] = int(raw)
    pm.pop("interval_label", None)
    await update.message.reply_text(
        f"Interval value: {raw}\n\nChoose the time unit:",
        reply_markup=_mp_interval_unit_keyboard(),
    )
    return MP_SET_INTERVAL_UNIT


async def cb_mp_interval_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    unit_map = {
        CB_MP_IU_MINUTES: "minutes",
        CB_MP_IU_HOURS: "hours",
        CB_MP_IU_DAYS: "days",
    }
    unit = unit_map.get(query.data)
    if unit is None:
        await query.answer("Please choose a time unit.", show_alert=True)
        return MP_SET_INTERVAL_UNIT
    pm = _pm(context)
    pm["interval_unit"] = unit
    pm["interval_label"] = f"{pm.get('interval_value', 1)} {unit}"
    await query.answer()
    await _mp_prompt_first_run(query, context)
    return MP_SET_FIRST_RUN


async def _mp_prompt_first_run(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    pm = _pm(context)
    label = escape_markdown(pm.get("interval_label", "custom interval"), version=1)
    await query.edit_message_text(
        f"Posting interval: {label}\n\n"
        "Step 3 of 3 — Choose the First Post Time\n\n"
        "Start as soon as possible, or choose a specific time in your selected timezone.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_mp_first_run_keyboard(),
    )


async def cb_mp_first_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    pm["first_run"] = "now"
    pm.setdefault("tz_page", 0)
    await query.answer()
    await query.edit_message_text(
        "🌍 Select the timezone that should be used for this posting schedule:",
        reply_markup=_mp_tz_keyboard(pm["tz_page"]),
    )
    return MP_SET_TIMEZONE


async def cb_mp_first_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _edit(
        query,
        "Enter the first post time in 24-hour HH:MM format.\n"
        "Example: 09:30. If that time has already passed today, the first post will be tomorrow.",
        nav_keyboard(back_data=CB_BACK_MP_FR),
    )
    return MP_SET_FIRST_RUN


async def recv_mp_first_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip().lower()
    pm = _pm(context)
    if raw == "now":
        pm["first_run"] = "now"
    else:
        parsed = _parse_hhmm(raw)
        if parsed is None:
            await update.message.reply_text(
                "Invalid time. Enter HH:MM in 24-hour format, for example 09:30."
            )
            return MP_SET_FIRST_RUN
        pm["first_run"] = parsed
    pm.setdefault("tz_page", 0)
    await update.message.reply_text(
        "🌍 Select the timezone for this schedule:",
        reply_markup=_mp_tz_keyboard(pm["tz_page"]),
    )
    return MP_SET_TIMEZONE


async def cb_mp_tz_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    raw = (query.data or "").replace(CB_MP_TZ_PAGE, "", 1)
    page = int(raw) if raw.isdigit() else 0
    _pm(context)["tz_page"] = page
    await query.answer()
    await query.edit_message_text(
        "🌍 Select the timezone for this schedule:",
        reply_markup=_mp_tz_keyboard(page),
    )
    return MP_SET_TIMEZONE


async def cb_mp_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    tz_key = (query.data or "").replace("mptz:", "", 1)
    try:
        pytz.timezone(tz_key)
    except pytz.exceptions.UnknownTimeZoneError:
        await query.answer("Unknown timezone. Please choose from the list.", show_alert=True)
        return MP_SET_TIMEZONE
    pm = _pm(context)
    pm["timezone"] = tz_key
    await query.answer()
    await query.edit_message_text(
        "🗑 Automatically delete each published post?\n\n"
        "Choose Yes to set a deletion delay, or No to keep posts permanently.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes", callback_data=CB_MP_AD_YES),
                InlineKeyboardButton("No, keep posts", callback_data=CB_MP_AD_NO),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data=CB_BACK_MP_TZ)],
            [InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL)],
        ]),
    )
    return MP_AUTO_DELETE


def _mp_summary_text(pm: dict) -> str:
    title = escape_markdown(pm.get("mp_chat_title", str(pm.get("mp_chat_id", "?"))), version=1)
    value = pm.get("interval_value", "?")
    unit = pm.get("interval_unit", "?")
    label = pm.get("interval_label") or f"{value} {unit}"
    first_run = pm.get("first_run", "?")
    tz_key = pm.get("timezone", "UTC")
    tz_label = next((lbl for key, lbl in TIMEZONES if key == tz_key), tz_key)
    first_run_display = "As soon as possible" if first_run == "now" else first_run
    ad_hours = pm.get("mp_ad_hours")
    ad_line = f"{ad_hours} hour(s) after publishing" if ad_hours else "Off"
    return (
        "📋 Schedule Review\n\n"
        f"Destination: {title}\n"
        f"Posting interval: {label}\n"
        f"First post: {first_run_display}\n"
        f"Timezone: {tz_label}\n"
        f"Auto-delete: {ad_line}\n\n"
        "Review these settings. Confirm to activate the schedule."
    )


async def cb_mp_ad_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _edit(
        query,
        "⏳ Auto-Delete Delay\n\n"
        "How many hours after publishing should each post be deleted?\n"
        "Enter a positive number. Decimals are supported: 1.5 means 90 minutes.",
        nav_keyboard(back_data=CB_BACK_MP_TZ),
    )
    return MP_AUTO_DELETE


async def recv_mp_ad_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip().replace(",", ".")
    try:
        value = float(raw)
        if value <= 0 or value > 8760:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Enter a number greater than 0 and no more than 8760 hours. Example: 24 or 1.5."
        )
        return MP_AUTO_DELETE
    pm = _pm(context)
    pm["mp_ad_hours"] = value
    await update.message.reply_text(
        _mp_summary_text(pm),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=confirm_keyboard(CB_MP_CONFIRM, CB_BACK_MP_TZ),
    )
    return MP_CONFIRM


async def cb_mp_ad_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    pm.pop("mp_ad_hours", None)
    await _edit(
        query,
        _mp_summary_text(pm),
        confirm_keyboard(CB_MP_CONFIRM, CB_BACK_MP_TZ),
    )
    return MP_CONFIRM


async def cb_back_mp_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    pm.pop("mp_ad_hours", None)
    page = pm.get("tz_page", 0)
    await query.answer()
    await query.edit_message_text(
        "🌍 Select the timezone for this schedule:",
        reply_markup=_mp_tz_keyboard(page),
    )
    return MP_SET_TIMEZONE


async def cb_mp_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from database import build_scheduled_post, insert_post

    query = update.callback_query
    pm = _pm(context)
    user_id = query.from_user.id
    chat_id = pm.get("mp_chat_id")
    value = pm.get("interval_value")
    unit = pm.get("interval_unit")
    first_run = pm.get("first_run")
    tz_str = pm.get("timezone", "UTC")

    if not all([chat_id, value, unit, first_run]):
        await query.answer("The setup is incomplete. Please start again.", show_alert=True)
        return MP_MENU

    try:
        await ensure_chat_access(query.get_bot(), int(chat_id), user_id)
    except ChatAccessError as exc:
        await query.answer(str(exc), show_alert=True)
        return MP_CONFIRM

    await query.answer("Checking and saving…")
    try:
        db = await get_db()
        pool = await db[COL_MEDIA_POOLS].find_one(
            {"user_id": user_id, "chat_id": chat_id}
        )
        if not pool or not pool.get("items"):
            _clear_mp(context)
            await query.edit_message_text(
                "This pool has no content, so no schedule was created. Add content first.",
                reply_markup=_mp_menu_keyboard(),
            )
            return MP_MENU

        existing = await db[COL_POSTS].find_one({
            "user_id": user_id,
            "chat_id": chat_id,
            "recurrence.type": "pool",
            "status": "pending",
        })
        if existing:
            _clear_mp(context)
            await query.edit_message_text(
                "An active Media Pool schedule already exists for this destination.\n\n"
                "To avoid duplicate posts, no second schedule was created. "
                "You can manage the existing schedule from My Posts.",
                reply_markup=_mp_menu_keyboard(),
            )
            return MP_MENU
    except Exception:
        logger.exception("Could not validate Media Pool before schedule creation")
        await query.edit_message_text(
            "The pool could not be checked right now. No schedule was created. Please try again.",
            reply_markup=_mp_menu_keyboard(),
        )
        _clear_mp(context)
        return MP_MENU

    try:
        zone = pytz.timezone(tz_str)
    except pytz.exceptions.UnknownTimeZoneError:
        zone = timezone.utc

    now_local = datetime.now(tz=zone)
    if first_run == "now":
        first_run_dt = datetime.now(tz=timezone.utc) + timedelta(minutes=1)
    else:
        hour, minute = int(first_run[:2]), int(first_run[3:])
        candidate = now_local.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now_local:
            candidate += timedelta(days=1)
        first_run_dt = candidate.astimezone(timezone.utc)

    auto_delete_hours = pm.get("mp_ad_hours")
    try:
        doc = build_scheduled_post(
            user_id=user_id,
            chat_id=chat_id,
            recurrence_type="pool",
            interval_value=value,
            interval_unit=unit,
            next_run_at=first_run_dt,
            timezone_str=tz_str,
            auto_delete_enabled=bool(auto_delete_hours),
            auto_delete_after_hours=auto_delete_hours,
        )
        post_id = await insert_post(doc)
    except Exception:
        logger.exception("Media Pool schedule save failed for chat_id=%s", chat_id)
        await query.edit_message_text(
            "The schedule could not be saved. No changes were made. Please try again.",
            reply_markup=_mp_menu_keyboard(),
        )
        _clear_mp(context)
        return MP_MENU

    title = escape_markdown(pm.get("mp_chat_title", str(chat_id)), version=1)
    tz_label = next((label for key, label in TIMEZONES if key == tz_str), tz_str)
    first_display = "As soon as possible (about 1 minute)" if first_run == "now" else first_run
    interval_label = pm.get("interval_label") or f"{value} {unit}"
    _clear_mp(context)
    await chat_cleanup.cleanup(
        context,
        query.message.chat_id,
        keep_message_id=query.message.message_id,
    )
    await query.edit_message_text(
        "✅ Media Pool schedule activated.\n\n"
        f"Destination: {title}\n"
        f"Interval: every {interval_label}\n"
        f"First post: {first_display}\n"
        f"Timezone: {tz_label}\n"
        f"Auto-delete: {'Off' if not auto_delete_hours else str(auto_delete_hours) + ' hour(s)'}\n\n"
        "The bot will choose a random item that has not yet been published in the current cycle.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_mp_menu_keyboard(),
    )
    return MP_MENU


async def cb_back_mp_interval_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    _clear_mp(context)
    await _edit(query, _mp_menu_text(), _mp_menu_keyboard())
    return MP_MENU


async def cb_back_mp_interval_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _edit(
        query,
        "Choose a common posting interval, or select Custom to enter your own interval.",
        _mp_quick_interval_keyboard(),
    )
    return MP_SET_INTERVAL_VALUE


async def cb_back_mp_first_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    label = escape_markdown(pm.get("interval_label", "custom interval"), version=1)
    await _edit(
        query,
        f"Posting interval: {label}\n\nChoose when the first post should be published.",
        _mp_first_run_keyboard(),
    )
    return MP_SET_FIRST_RUN


async def cb_mp_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    _clear_mp(context)
    await query.answer()
    await query.edit_message_text(
        "🏠 Main Menu — choose an action",
        reply_markup=main_menu_keyboard(
            bot=context.bot,
            user_id=query.from_user.id,
            language=context.user_data.get("language", "en"),
        ),
    )
    return ConversationHandler.END


async def cb_mp_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        db = await get_db()
        cursor = db[COL_MEDIA_POOLS].find({"user_id": user_id}).sort("updated_at", -1)
        pools = await cursor.to_list(length=50)
        groups = await list_groups_for_user(user_id)
    except Exception:
        logger.exception("Could not load Media Pool overview for user_id=%s", user_id)
        await query.edit_message_text(
            "The pool overview is unavailable right now. Please try again.",
            reply_markup=_mp_menu_keyboard(),
        )
        return MP_MENU

    if not pools:
        await query.edit_message_text(
            "📊 Pool Overview\n\nNo content pools yet. Choose Add Content to create your first pool.",
            reply_markup=_mp_menu_keyboard(),
        )
        return MP_MENU

    title_map = {
        int(group["chat_id"]): group.get("title") or str(group["chat_id"])
        for group in groups
    }
    lines = ["📊 Pool Overview", ""]
    for pool in pools:
        items = pool.get("items", [])
        total = len(items)
        remaining = sum(1 for item in items if not item.get("posted", False))
        title = escape_markdown(
            title_map.get(int(pool["chat_id"])) or f"Chat {pool['chat_id']}",
            version=1,
        )
        lines.extend([
            f"🗂 {title}",
            f"Total items: {total}",
            f"Remaining this cycle: {remaining}",
            f"Published this cycle: {total - remaining}",
            "",
        ])
    lines.append("When Remaining reaches zero, the next scheduled run starts a new cycle.")
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_mp_menu_keyboard(),
    )
    return MP_MENU


async def _mp_load_manage_data(user_id: int) -> tuple[list[dict], dict[int, str]]:
    db = await get_db()
    pools = await db[COL_MEDIA_POOLS].find({"user_id": user_id}).to_list(length=100)
    groups = await list_groups_for_user(user_id)
    title_map = {
        int(group["chat_id"]): group.get("title") or str(group["chat_id"])
        for group in groups
    }
    return pools, title_map


async def _mp_show_manage_picker(query: Any, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> int:
    user_id = query.from_user.id
    try:
        pools, title_map = await _mp_load_manage_data(user_id)
    except Exception:
        logger.exception("Could not load Media Pool management list")
        await query.answer("Unable to load your pools. Please try again.", show_alert=True)
        return MP_MANAGE_CHAT

    if not pools:
        await _edit(
            query,
            "🛠 Manage Pool\n\nYou do not have any content pools yet. Add content first.",
            _mp_menu_keyboard(),
        )
        return MP_MENU

    pools.sort(key=lambda pool: (title_map.get(int(pool["chat_id"])) or str(pool["chat_id"])).casefold())
    total = len(pools)
    page = max(0, min(page, (total - 1) // _MP_POOL_PAGE_SIZE))
    await _edit(
        query,
        "🛠 Manage Pool\n\nSelect the pool you want to manage. Changes will affect only the selected group or channel.",
        _mp_manage_picker_keyboard(pools, title_map, page),
    )
    return MP_MANAGE_CHAT


async def cb_mp_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _mp_show_manage_picker(update.callback_query, context)


async def cb_mp_manage_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    raw = (query.data or "").replace(CB_MP_MANAGE_PAGE, "", 1)
    page = int(raw) if raw.isdigit() else 0
    return await _mp_show_manage_picker(query, context, page)


async def _mp_manage_summary(user_id: int, chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    db = await get_db()
    pool = await db[COL_MEDIA_POOLS].find_one({"user_id": user_id, "chat_id": chat_id})
    if not pool:
        raise LookupError("This Media Pool no longer exists.")
    groups = await list_groups_for_user(user_id)
    title_map = {
        int(group["chat_id"]): group.get("title") or str(group["chat_id"])
        for group in groups
    }
    title = escape_markdown(
        title_map.get(chat_id) or f"Chat {chat_id}",
        version=1,
    )
    items = pool.get("items", [])
    total = len(items)
    remaining = sum(1 for item in items if not item.get("posted", False))
    text = (
        f"🗂 {title}\n\n"
        f"Total items: {total}\n"
        f"Remaining this cycle: {remaining}\n"
        f"Published this cycle: {total - remaining}\n\n"
        "Choose an action for this pool only."
    )
    return text, _mp_manage_actions_keyboard()


async def cb_mp_manage_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    raw = (query.data or "").replace(CB_MP_MANAGE_PICK, "", 1)
    try:
        chat_id = int(raw)
        target_chat = await ensure_chat_access(query.get_bot(), chat_id, query.from_user.id)
        text, keyboard = await _mp_manage_summary(query.from_user.id, chat_id)
    except (ValueError, ChatAccessError, LookupError) as exc:
        await query.answer(str(exc) or "Invalid pool selection.", show_alert=True)
        return MP_MANAGE_CHAT
    except Exception:
        logger.exception("Could not open Media Pool details")
        await query.answer("Could not load this pool. Please try again.", show_alert=True)
        return MP_MANAGE_CHAT

    pm = _pm(context)
    pm["mp_chat_id"] = chat_id
    pm["mp_chat_title"] = target_chat.title or str(chat_id)
    await _edit(query, text, keyboard)
    return MP_MANAGE_ACTION


async def cb_back_mp_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    _pm(context).pop("mp_chat_id", None)
    _pm(context).pop("mp_chat_title", None)
    return await _mp_show_manage_picker(query, context)


async def cb_back_mp_manage_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    chat_id = pm.get("mp_chat_id")
    if not chat_id:
        return await _mp_show_manage_picker(query, context)
    try:
        text, keyboard = await _mp_manage_summary(query.from_user.id, int(chat_id))
    except Exception:
        logger.exception("Could not reload Media Pool details")
        await query.answer("This pool is no longer available.", show_alert=True)
        return await _mp_show_manage_picker(query, context)
    await _edit(query, text, keyboard)
    return MP_MANAGE_ACTION


async def cb_mp_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    title = escape_markdown(pm.get("mp_chat_title", "selected group"), version=1)
    await _edit(
        query,
        f"🔄 Reset posting cycle for {title}?\n\n"
        "All items will become available for random selection again. "
        "No content will be deleted.",
        _mp_manage_confirm_keyboard(CB_MP_RESET_YES),
    )
    return MP_MANAGE_ACTION


async def cb_mp_reset_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    chat_id = pm.get("mp_chat_id")
    if not chat_id:
        await query.answer("Select a pool first.", show_alert=True)
        return MP_MANAGE_CHAT
    try:
        await ensure_chat_access(query.get_bot(), int(chat_id), query.from_user.id)
    except ChatAccessError as exc:
        await query.answer(str(exc), show_alert=True)
        return MP_MANAGE_ACTION
    try:
        await reset_media_pool(query.from_user.id, int(chat_id))
        text, keyboard = await _mp_manage_summary(query.from_user.id, int(chat_id))
    except Exception:
        logger.exception("Could not reset Media Pool cycle for chat_id=%s", chat_id)
        await query.answer("The pool could not be reset. Please try again.", show_alert=True)
        return MP_MANAGE_ACTION
    await _edit(query, "✅ Posting cycle reset.\n\n" + text, keyboard)
    return MP_MANAGE_ACTION


async def cb_mp_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    title = escape_markdown(pm.get("mp_chat_title", "selected group"), version=1)
    await _edit(
        query,
        f"🗑 Clear the Media Pool for {title}?\n\n"
        "This permanently deletes the content items in this one pool. "
        "Any active random-post schedule for this destination will be paused. "
        "Other groups and channels will not be affected.",
        _mp_manage_confirm_keyboard(CB_MP_CLEAR_YES),
    )
    return MP_MANAGE_ACTION


async def cb_mp_clear_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = _pm(context).get("mp_chat_id")
    if not chat_id:
        await query.answer("Select a pool first.", show_alert=True)
        return MP_MANAGE_CHAT
    try:
        await ensure_chat_access(query.get_bot(), int(chat_id), user_id)
    except ChatAccessError as exc:
        await query.answer(str(exc), show_alert=True)
        return MP_MANAGE_ACTION

    try:
        db = await get_db()
        result = await db[COL_MEDIA_POOLS].delete_one({
            "user_id": user_id,
            "chat_id": int(chat_id),
        })
        await db[COL_POSTS].update_many(
            {
                "user_id": user_id,
                "chat_id": int(chat_id),
                "recurrence.type": "pool",
                "status": "pending",
            },
            {
                "$set": {
                    "status": "paused",
                    "updated_at": datetime.now(tz=timezone.utc),
                }
            },
        )
    except Exception:
        logger.exception("Could not clear Media Pool for user_id=%s chat_id=%s", user_id, chat_id)
        await query.answer("The pool could not be cleared. Please try again.", show_alert=True)
        return MP_MANAGE_ACTION

    _clear_mp(context)
    message = (
        "✅ This Media Pool has been cleared. Its pending random-post schedule has been paused."
        if result.deleted_count
        else "This Media Pool was already empty or no longer exists."
    )
    await _edit(query, message, _mp_menu_keyboard())
    return MP_MENU


async def cb_back_mp_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    _clear_mp(context)
    await _edit(query, _mp_menu_text(), _mp_menu_keyboard())
    return MP_MENU


# ── Cancel (with full message cleanup) ──

async def cb_cancel_local(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Cancelled.")
    _clear(context)
    _clear_mp(context)
    chat_id = query.message.chat_id
    user_id = query.from_user.id if query.from_user else None
    await chat_cleanup.cleanup(context, chat_id, keep_message_id=query.message.message_id)
    await query.edit_message_text(
        "❌ *Cancelled.* Back to Main Menu.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(bot=context.bot, user_id=user_id),
    )
    return ConversationHandler.END


# ============================================================================
#  ConversationHandler factories
# ============================================================================

def build_queue_manager() -> ConversationHandler:
    qm_tz_pattern      = r"^qmtz:(?!page:).+"
    qm_tzp_pattern     = r"^qmtz:page:\d+$"
    qm_ac_tz_pattern   = r"^qmactz:(?!page:).+"
    qm_ac_tzp_pattern  = r"^qmactz:page:\d+$"
    qm_ss_pick_pattern = rf"^{re.escape(CB_QM_SS_PICK)}-?\d+$"
    qm_ac_pick_pattern = rf"^{re.escape(CB_QM_AC_PICK)}-?\d+$"
    qm_ss_page_pattern = rf"^{re.escape(CB_QM_SS_PAGE)}\d+$"
    qm_ac_page_pattern = rf"^{re.escape(CB_QM_AC_PAGE)}\d+$"

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(enter_queue_manager, pattern=f"^{CB_MANAGE_QUEUE}$")],
        states={
            QM_MENU: [
                CallbackQueryHandler(cb_qm_set_slots,   pattern=f"^{CB_QM_SET_SLOTS}$"),
                CallbackQueryHandler(cb_qm_add_content, pattern=f"^{CB_QM_ADD_CONTENT}$"),
                CallbackQueryHandler(cb_qm_view,        pattern=f"^{CB_QM_VIEW}$"),
                CallbackQueryHandler(cb_qm_clear,       pattern=f"^{CB_QM_CLEAR}$"),
                CallbackQueryHandler(cb_qm_clear_yes,   pattern=f"^{CB_QM_CLEAR_YES}$"),
                CallbackQueryHandler(cb_back_qm_menu,   pattern=f"^{CB_BACK_QM_MENU}$"),
            ],

            # ── Set Slots wizard ──────────────────────────────────────────────
            QM_SET_SLOTS_CHAT_ID: [
                # Group picker callbacks
                CallbackQueryHandler(cb_qm_ss_pick,       pattern=qm_ss_pick_pattern),
                CallbackQueryHandler(cb_qm_ss_page,       pattern=qm_ss_page_pattern),
                CallbackQueryHandler(cb_qm_ss_manual,     pattern=f"^{CB_QM_SS_MANUAL}$"),
                # Manual text entry
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_qm_slots_chat_id),
                CallbackQueryHandler(cb_back_qm_menu,     pattern=f"^{CB_BACK_QM_MENU}$"),
            ],
            QM_SET_SLOTS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_qm_slots),
                CallbackQueryHandler(cb_back_qm_slot_chat, pattern=f"^{CB_BACK_QM_SLOT_CHAT}$"),
                CallbackQueryHandler(cb_back_qm_menu,      pattern=f"^{CB_BACK_QM_MENU}$"),
            ],
            QM_SET_SLOTS_TZ: [
                CallbackQueryHandler(cb_qm_tz,             pattern=qm_tz_pattern),
                CallbackQueryHandler(cb_qm_tz_page,        pattern=qm_tzp_pattern),
                CallbackQueryHandler(cb_back_qm_slot_tz,   pattern=f"^{CB_BACK_QM_SLOT_TZ}$"),
                CallbackQueryHandler(cb_back_qm_menu,      pattern=f"^{CB_BACK_QM_MENU}$"),
            ],

            # ── Add Content wizard ────────────────────────────────────────────
            QM_ADD_CHAT_ID: [
                # Group picker callbacks
                CallbackQueryHandler(cb_qm_ac_pick,       pattern=qm_ac_pick_pattern),
                CallbackQueryHandler(cb_qm_ac_page,       pattern=qm_ac_page_pattern),
                CallbackQueryHandler(cb_qm_ac_manual,     pattern=f"^{CB_QM_AC_MANUAL}$"),
                # Manual text entry
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_qm_add_chat_id),
                # No-slots warning buttons: "Set Daily Slots Now" and "Back to Menu"
                CallbackQueryHandler(cb_qm_set_slots,     pattern=f"^{CB_QM_SET_SLOTS}$"),
                CallbackQueryHandler(cb_back_qm_menu,     pattern=f"^{CB_BACK_QM_MENU}$"),
            ],
            QM_ADD_CONTENT: [
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.VIDEO |
                     filters.Document.ALL | filters.AUDIO | filters.ANIMATION |
                     filters.VOICE) & ~filters.COMMAND,
                    recv_qm_content,
                ),
                CallbackQueryHandler(cb_back_qm_ac_chat,    pattern=f"^{CB_BACK_QM_AC_CHAT}$"),
                CallbackQueryHandler(cb_back_qm_menu,        pattern=f"^{CB_BACK_QM_MENU}$"),
            ],
            QM_ADD_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_qm_add_date),
                CallbackQueryHandler(cb_back_qm_ac_content,  pattern=f"^{CB_BACK_QM_AC_CONTENT}$"),
                CallbackQueryHandler(cb_back_qm_menu,        pattern=f"^{CB_BACK_QM_MENU}$"),
            ],
            QM_ADD_TZ: [
                CallbackQueryHandler(cb_qm_ac_tz,            pattern=qm_ac_tz_pattern),
                CallbackQueryHandler(cb_qm_ac_tz_page,       pattern=qm_ac_tzp_pattern),
                CallbackQueryHandler(cb_back_qm_ac_date,     pattern=f"^{CB_BACK_QM_AC_DATE}$"),
                CallbackQueryHandler(cb_back_qm_menu,        pattern=f"^{CB_BACK_QM_MENU}$"),
            ],
            QM_CONFIRM_SLOT: [
                CallbackQueryHandler(cb_qm_confirm_add,      pattern=f"^{CB_QM_CONFIRM_ADD}$"),
                CallbackQueryHandler(cb_back_qm_ac_content,  pattern=f"^{CB_BACK_QM_AC_CONTENT}$"),
                CallbackQueryHandler(cb_back_qm_menu,        pattern=f"^{CB_BACK_QM_MENU}$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel_local, pattern=f"^{CB_CANCEL}$"),
        ],
        allow_reentry=True,
        name="queue_manager",
        persistent=False,
    )


def build_media_pool() -> ConversationHandler:
    mp_tz_pattern = r"^mptz:(?!page:).+"
    mp_tzp_pattern = r"^mptz:page:\d+$"
    mp_pick_pattern = rf"^{re.escape(CB_MP_PICK)}-?\d+$"
    mp_page_pattern = rf"^{re.escape(CB_MP_PAGE)}\d+$"
    mp_manage_pick_pattern = rf"^{re.escape(CB_MP_MANAGE_PICK)}-?\d+$"
    mp_manage_page_pattern = rf"^{re.escape(CB_MP_MANAGE_PAGE)}\d+$"
    mp_quick_pattern = rf"^{re.escape(CB_MP_QUICK_PREFIX)}(15m|30m|1h|3h|6h|12h|24h)$"

    return ConversationHandler(
        entry_points=[CallbackQueryHandler(enter_media_pool, pattern=f"^{CB_MEDIA_POOL}$")],
        states={
            MP_MENU: [
                CallbackQueryHandler(cb_mp_add_item, pattern=f"^{CB_MP_ADD_ITEM}$"),
                CallbackQueryHandler(cb_mp_set_interval, pattern=f"^{CB_MP_SET_INTERVAL}$"),
                CallbackQueryHandler(cb_mp_view, pattern=f"^{CB_MP_VIEW}$"),
                CallbackQueryHandler(cb_mp_manage, pattern=f"^{CB_MP_MANAGE}$"),
                CallbackQueryHandler(cb_mp_main_menu, pattern=f"^{CB_MAIN_MENU}$"),
            ],
            MP_CHAT_ID: [
                CallbackQueryHandler(cb_mp_group_pick, pattern=mp_pick_pattern),
                CallbackQueryHandler(cb_mp_group_page, pattern=mp_page_pattern),
                CallbackQueryHandler(cb_mp_manual_chat_id, pattern=f"^{CB_MP_MANUAL}$"),
                CallbackQueryHandler(cb_mp_add_selected, pattern=f"^{CB_MP_ADD_SELECTED}$"),
                CallbackQueryHandler(cb_back_mp_menu, pattern=f"^{CB_BACK_MP_MENU}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_mp_chat_id),
            ],
            MP_ADD_ITEM: [
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.VIDEO |
                     filters.Document.ALL | filters.AUDIO | filters.ANIMATION |
                     filters.VOICE) & ~filters.COMMAND,
                    recv_mp_item,
                ),
                CallbackQueryHandler(cb_mp_add_done, pattern=f"^{CB_MP_ADD_DONE}$"),
                CallbackQueryHandler(cb_mp_add_schedule, pattern=f"^{CB_MP_ADD_SCHEDULE}$"),
            ],
            MP_SET_INTERVAL_VALUE: [
                CallbackQueryHandler(cb_mp_quick_interval, pattern=mp_quick_pattern),
                CallbackQueryHandler(cb_mp_custom_interval, pattern=f"^{CB_MP_CUSTOM_INTERVAL}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_mp_interval_value),
                CallbackQueryHandler(cb_back_mp_interval_value, pattern=f"^{CB_BACK_MP_IV}$"),
                CallbackQueryHandler(cb_back_mp_menu, pattern=f"^{CB_BACK_MP_MENU}$"),
            ],
            MP_SET_INTERVAL_UNIT: [
                CallbackQueryHandler(
                    cb_mp_interval_unit,
                    pattern=f"^({CB_MP_IU_MINUTES}|{CB_MP_IU_HOURS}|{CB_MP_IU_DAYS})$",
                ),
                CallbackQueryHandler(cb_back_mp_interval_unit, pattern=f"^{CB_BACK_MP_IU}$"),
                CallbackQueryHandler(cb_back_mp_menu, pattern=f"^{CB_BACK_MP_MENU}$"),
            ],
            MP_SET_FIRST_RUN: [
                CallbackQueryHandler(cb_mp_first_now, pattern=f"^{CB_MP_FIRST_NOW}$"),
                CallbackQueryHandler(cb_mp_first_custom, pattern=f"^{CB_MP_FIRST_CUSTOM}$"),
                CallbackQueryHandler(cb_back_mp_interval_unit, pattern=f"^{CB_BACK_MP_IV}$"),
                CallbackQueryHandler(cb_back_mp_first_run, pattern=f"^{CB_BACK_MP_FR}$"),
                CallbackQueryHandler(cb_back_mp_menu, pattern=f"^{CB_BACK_MP_MENU}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_mp_first_run),
            ],
            MP_SET_TIMEZONE: [
                CallbackQueryHandler(cb_mp_tz, pattern=mp_tz_pattern),
                CallbackQueryHandler(cb_mp_tz_page, pattern=mp_tzp_pattern),
                CallbackQueryHandler(cb_back_mp_first_run, pattern=f"^{CB_BACK_MP_FR}$"),
                CallbackQueryHandler(cb_back_mp_menu, pattern=f"^{CB_BACK_MP_MENU}$"),
            ],
            MP_AUTO_DELETE: [
                CallbackQueryHandler(cb_mp_ad_yes, pattern=f"^{CB_MP_AD_YES}$"),
                CallbackQueryHandler(cb_mp_ad_no, pattern=f"^{CB_MP_AD_NO}$"),
                CallbackQueryHandler(cb_back_mp_tz, pattern=f"^{CB_BACK_MP_TZ}$"),
                CallbackQueryHandler(cb_back_mp_menu, pattern=f"^{CB_BACK_MP_MENU}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_mp_ad_hours),
            ],
            MP_CONFIRM: [
                CallbackQueryHandler(cb_mp_confirm, pattern=f"^{CB_MP_CONFIRM}$"),
                CallbackQueryHandler(cb_back_mp_tz, pattern=f"^{CB_BACK_MP_TZ}$"),
                CallbackQueryHandler(cb_back_mp_menu, pattern=f"^{CB_BACK_MP_MENU}$"),
            ],
            MP_MANAGE_CHAT: [
                CallbackQueryHandler(cb_mp_manage_pick, pattern=mp_manage_pick_pattern),
                CallbackQueryHandler(cb_mp_manage_page, pattern=mp_manage_page_pattern),
                CallbackQueryHandler(cb_back_mp_menu, pattern=f"^{CB_BACK_MP_MENU}$"),
            ],
            MP_MANAGE_ACTION: [
                CallbackQueryHandler(cb_mp_reset, pattern=f"^{CB_MP_RESET}$"),
                CallbackQueryHandler(cb_mp_reset_yes, pattern=f"^{CB_MP_RESET_YES}$"),
                CallbackQueryHandler(cb_mp_clear, pattern=f"^{CB_MP_CLEAR}$"),
                CallbackQueryHandler(cb_mp_clear_yes, pattern=f"^{CB_MP_CLEAR_YES}$"),
                CallbackQueryHandler(cb_back_mp_manage, pattern=f"^{CB_BACK_MP_MANAGE}$"),
                CallbackQueryHandler(cb_back_mp_manage_details, pattern=f"^{CB_BACK_MP_DETAILS}$"),
                CallbackQueryHandler(cb_back_mp_menu, pattern=f"^{CB_BACK_MP_MENU}$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel_local, pattern=f"^{CB_CANCEL}$"),
        ],
        allow_reentry=True,
        name="media_pool",
        persistent=False,
    )

