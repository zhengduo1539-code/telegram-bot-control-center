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
from media_pool_localization import translate_media_pool as mp_tr
from queue_manager_localization import translate_queue_manager as qm_tr

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
MP_INTRO = 211
QM_CONFIRM_SLOTS = 110

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
CB_QM_CONTINUE     = "qm:intro:continue"
CB_QM_CONFIRM_SLOTS = "qm:slots:confirm"
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
CB_MP_CONTINUE = "mp:intro:continue"
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


def _mp_t(context: ContextTypes.DEFAULT_TYPE, key: str, **values: Any) -> str:
    """Translate Media Pool copy using the user's persisted locale."""
    return mp_tr(context.user_data.get("language", "en"), key, **values)


def _mp_language(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get("language", "en")


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


def _mp_interval_unit_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"⏱ {mp_tr(language, 'minutes')}", callback_data=CB_MP_IU_MINUTES),
            InlineKeyboardButton(f"🕐 {mp_tr(language, 'hours')}", callback_data=CB_MP_IU_HOURS),
            InlineKeyboardButton(f"📅 {mp_tr(language, 'days')}", callback_data=CB_MP_IU_DAYS),
        ],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MP_IU),
            InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
        ],
    ])


def _qm_tz_keyboard(page: int = 0, language: str = "en") -> InlineKeyboardMarkup:
    """Localized timezone picker for the Queue Manager daily-slot wizard."""
    start = page * TZ_PAGE_SIZE
    end = start + TZ_PAGE_SIZE
    rows = [
        [InlineKeyboardButton(label, callback_data=f"qmtz:{tz}")]
        for tz, label in TIMEZONES[start:end]
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            qm_tr(language, "prev"), callback_data=f"{CB_QM_TZ_PAGE}{page - 1}"
        ))
    if end < len(TIMEZONES):
        nav.append(InlineKeyboardButton(
            qm_tr(language, "next"), callback_data=f"{CB_QM_TZ_PAGE}{page + 1}"
        ))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_QM_SLOT_TZ),
        InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)
def _qm_ac_tz_keyboard(page: int = 0, language: str = "en") -> InlineKeyboardMarkup:
    """Localized timezone picker for the legacy date/time add-content path."""
    start = page * TZ_PAGE_SIZE
    end = start + TZ_PAGE_SIZE
    rows = [
        [InlineKeyboardButton(label, callback_data=f"qmactz:{tz}")]
        for tz, label in TIMEZONES[start:end]
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            qm_tr(language, "prev"), callback_data=f"{CB_QM_AC_TZ_PAGE}{page - 1}"
        ))
    if end < len(TIMEZONES):
        nav.append(InlineKeyboardButton(
            qm_tr(language, "next"), callback_data=f"{CB_QM_AC_TZ_PAGE}{page + 1}"
        ))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_QM_AC_DATE),
        InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)
def _mp_tz_keyboard(page: int = 0, language: str = "en") -> InlineKeyboardMarkup:
    start = page * TZ_PAGE_SIZE
    end = start + TZ_PAGE_SIZE
    rows = [
        [InlineKeyboardButton(tz, callback_data=f"mptz:{tz}")]
        for tz, _label in TIMEZONES[start:end]
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            mp_tr(language, "prev"), callback_data=f"{CB_MP_TZ_PAGE}{page - 1}"
        ))
    if end < len(TIMEZONES):
        nav.append(InlineKeyboardButton(
            mp_tr(language, "next"), callback_data=f"{CB_MP_TZ_PAGE}{page + 1}"
        ))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MP_FR),
        InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _qm_group_picker_keyboard(
    groups: list[dict],
    page: int,
    pick_prefix: str,
    page_prefix: str,
    manual_cb: str,
    back_cb: str,
    language: str = "en",
) -> InlineKeyboardMarkup:
    """Localized group picker shared by Queue Manager setup flows."""
    rows: list[list[InlineKeyboardButton]] = []
    total = len(groups)
    start = page * _QM_CHAT_PAGE_SIZE
    end = min(start + _QM_CHAT_PAGE_SIZE, total)
    type_icon = {"channel": "📢", "supergroup": "👥", "group": "👥"}
    for group in groups[start:end]:
        icon = type_icon.get(group.get("chat_type", ""), "💬")
        title = (group.get("title") or f"Chat {group['chat_id']}")[:35]
        rows.append([InlineKeyboardButton(
            f"{icon} {title}", callback_data=f"{pick_prefix}{group['chat_id']}"
        )])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            qm_tr(language, "prev"), callback_data=f"{page_prefix}{page - 1}"
        ))
    if end < total:
        nav.append(InlineKeyboardButton(
            qm_tr(language, "next"), callback_data=f"{page_prefix}{page + 1}"
        ))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(
        qm_tr(language, "button_manual_id"), callback_data=manual_cb
    )])
    rows.append([
        InlineKeyboardButton(tr(language, "back"), callback_data=back_cb),
        InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)
def _qm_no_groups_keyboard(
    manual_cb: str,
    back_cb: str,
    bot_username: str,
    language: str = "en",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            qm_tr(language, "button_add_bot"),
            url=f"https://t.me/{bot_username}?startgroup=start",
        )],
        [InlineKeyboardButton(
            qm_tr(language, "button_manual_id"), callback_data=manual_cb
        )],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=back_cb),
            InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
        ],
    ])
def _qm_intro_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    """Back returns to Main Menu; Continue opens the Queue Manager actions."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_MAIN_MENU),
        InlineKeyboardButton(qm_tr(language, "continue"), callback_data=CB_QM_CONTINUE),
    ]])


def _qm_menu_text(language: str = "en") -> str:
    return qm_tr(language, "menu")


def _qm_nav_keyboard(language: str, back_data: str | None = None) -> InlineKeyboardMarkup:
    row: list[InlineKeyboardButton] = []
    if back_data:
        row.append(InlineKeyboardButton(tr(language, "back"), callback_data=back_data))
    row.append(InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL))
    return InlineKeyboardMarkup([row])


def _qm_confirm_keyboard(
    language: str,
    confirm_data: str,
    back_data: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(tr(language, "confirm"), callback_data=confirm_data),
            InlineKeyboardButton(tr(language, "back"), callback_data=back_data),
        ],
        [InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL)],
    ])


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
    """Show the Queue Manager introduction before exposing its actions."""
    query = update.callback_query
    _clear(context)
    language = await resolve_user_language(
        context, query.from_user.id if query.from_user else None
    )
    context.user_data["active_queue_manager"] = True
    chat_cleanup.reset(context.application.bot_data, query.message.chat_id)
    chat_cleanup.track(
        context.application.bot_data, query.message.chat_id, query.message.message_id
    )
    await _edit(query, qm_tr(language, "intro"), _qm_intro_keyboard(language))
    return QM_INTRO


async def cb_qm_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Open the existing Queue Manager menu after the user reads the guide."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    await _edit(query, _qm_menu_text(language), _qm_menu_keyboard(language))
    return QM_MENU


async def cb_qm_back_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return from Queue Manager onboarding/menu to the localized main menu."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    context.user_data.pop("active_queue_manager", None)
    _clear(context)
    await query.answer()
    await query.edit_message_text(
        tr(language, "menu_title"),
        reply_markup=main_menu_keyboard(
            bot=context.bot,
            user_id=query.from_user.id,
            language=language,
        ),
    )
    return ConversationHandler.END
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
    """Start Daily Slots setup with a localized destination picker."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    wizard = _w(context)
    for key in ("slots_chat_id", "slots_chat_title", "pending_slots", "pending_timezone"):
        wizard.pop(key, None)

    user_id = query.from_user.id
    bot_username = (await query.get_bot().get_me()).username or "YourSchedulerBot"
    groups = await list_groups_for_user(user_id)
    range_text = (
        f"({min(_QM_CHAT_PAGE_SIZE, len(groups))} / {len(groups)} available)"
        if groups else ""
    )
    keyboard = (
        _qm_group_picker_keyboard(
            groups, 0, CB_QM_SS_PICK, CB_QM_SS_PAGE, CB_QM_SS_MANUAL,
            CB_BACK_QM_MENU, language,
        )
        if groups else
        _qm_no_groups_keyboard(
            CB_QM_SS_MANUAL, CB_BACK_QM_MENU, bot_username, language
        )
    )
    await _edit(query, qm_tr(language, "choose_group", range_text=range_text), keyboard)
    return QM_SET_SLOTS_CHAT_ID
async def cb_qm_ss_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate the destination and explain daily-time input."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    raw = (query.data or "").replace(CB_QM_SS_PICK, "", 1)
    try:
        chat_id = int(raw)
    except ValueError:
        await query.answer(qm_tr(language, "invalid_chat_id"), show_alert=True)
        return QM_SET_SLOTS_CHAT_ID

    user_id = query.from_user.id
    try:
        target_chat = await ensure_chat_access(query.get_bot(), chat_id, user_id)
    except ChatAccessError as exc:
        await query.answer(str(exc), show_alert=True)
        return QM_SET_SLOTS_CHAT_ID

    groups = await list_groups_for_user(user_id)
    title_map = {
        group["chat_id"]: (group.get("title") or str(group["chat_id"]))
        for group in groups
    }
    title = target_chat.title or title_map.get(chat_id, str(chat_id))
    _w(context)["slots_chat_id"] = chat_id
    _w(context)["slots_chat_title"] = title

    await _edit(
        query,
        qm_tr(
            language, "slots_times_prompt",
            group=escape_markdown(str(title), version=1),
        ),
        _qm_nav_keyboard(language, back_data=CB_BACK_QM_SLOT_CHAT),
    )
    return QM_SET_SLOTS
async def cb_qm_ss_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paginate the localized Daily Slots destination picker."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    raw = (query.data or "").replace(CB_QM_SS_PAGE, "", 1)
    page = int(raw) if raw.isdigit() else 0
    groups = await list_groups_for_user(query.from_user.id)
    total = len(groups)
    page = max(0, min(page, max(0, (total - 1) // _QM_CHAT_PAGE_SIZE)))
    start = page * _QM_CHAT_PAGE_SIZE
    end = min(start + _QM_CHAT_PAGE_SIZE, total)
    range_text = f"({start + 1}–{end} / {total})"
    await _edit(
        query,
        qm_tr(language, "choose_group", range_text=range_text),
        _qm_group_picker_keyboard(
            groups, page, CB_QM_SS_PICK, CB_QM_SS_PAGE, CB_QM_SS_MANUAL,
            CB_BACK_QM_MENU, language,
        ),
    )
    return QM_SET_SLOTS_CHAT_ID
async def cb_qm_ss_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask for a manually entered destination Chat ID."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    await _edit(
        query,
        qm_tr(language, "manual_id_prompt"),
        _qm_nav_keyboard(language, back_data=CB_BACK_QM_MENU),
    )
    return QM_SET_SLOTS_CHAT_ID
async def recv_qm_slots_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate a manual Chat ID and continue to daily-time selection."""
    language = await resolve_user_language(context, update.effective_user.id)
    raw_chat_id = (update.message.text or "").strip()
    try:
        chat_id = int(raw_chat_id)
    except ValueError:
        await update.message.reply_text(
            qm_tr(language, "invalid_chat_id"),
            parse_mode=ParseMode.MARKDOWN,
        )
        return QM_SET_SLOTS_CHAT_ID

    try:
        target_chat = await ensure_chat_access(
            context.bot, chat_id, update.effective_user.id
        )
    except ChatAccessError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return QM_SET_SLOTS_CHAT_ID

    title = target_chat.title or str(chat_id)
    _w(context)["slots_chat_id"] = chat_id
    _w(context)["slots_chat_title"] = title
    await update.message.reply_text(
        qm_tr(
            language, "slots_times_prompt",
            group=escape_markdown(str(title), version=1),
        ),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_nav_keyboard(language, back_data=CB_BACK_QM_SLOT_CHAT),
    )
    return QM_SET_SLOTS
async def recv_qm_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate daily slots and advance to timezone selection."""
    language = await resolve_user_language(context, update.effective_user.id)
    slots = _parse_slots((update.message.text or "").strip())
    if not slots:
        await update.message.reply_text(
            qm_tr(language, "invalid_slots"),
            parse_mode=ParseMode.MARKDOWN,
        )
        return QM_SET_SLOTS

    _w(context)["pending_slots"] = slots
    _w(context).setdefault("qm_tz_page", 0)
    slots_display = "  •  ".join(slots)
    await update.message.reply_text(
        f"✅ Slots: `{slots_display}`\n\n{qm_tr(language, 'timezone_prompt')}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_tz_keyboard(0, language),
    )
    return QM_SET_SLOTS_TZ
async def cb_qm_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Select timezone and review settings before any database write."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    tz_key = (query.data or "").replace("qmtz:", "", 1)
    try:
        pytz.timezone(tz_key)
    except pytz.exceptions.UnknownTimeZoneError:
        await query.answer(qm_tr(language, "timezone_prompt"), show_alert=True)
        return QM_SET_SLOTS_TZ

    wizard = _w(context)
    chat_id = wizard.get("slots_chat_id")
    slots = wizard.get("pending_slots")
    if not chat_id or not slots:
        await query.answer(qm_tr(language, "invalid_slots"), show_alert=True)
        return QM_SET_SLOTS_TZ

    wizard["pending_timezone"] = tz_key
    title = escape_markdown(
        str(wizard.get("slots_chat_title") or chat_id), version=1
    )
    slots_display = escape_markdown(" • ".join(slots), version=1)
    timezone_label = next(
        (label for key, label in TIMEZONES if key == tz_key), tz_key
    )
    await _edit(
        query,
        qm_tr(
            language, "slots_review",
            group=title, slots=slots_display,
            timezone=escape_markdown(timezone_label, version=1),
        ),
        _qm_confirm_keyboard(
            language, CB_QM_CONFIRM_SLOTS, CB_BACK_QM_SLOT_TZ
        ),
    )
    return QM_CONFIRM_SLOTS


async def cb_qm_confirm_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Revalidate permissions and save daily slots only after confirmation."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    wizard = _w(context)
    user_id = query.from_user.id
    chat_id = wizard.get("slots_chat_id")
    slots = wizard.get("pending_slots")
    timezone_key = wizard.get("pending_timezone")

    if not chat_id or not slots or not timezone_key:
        _clear(context)
        await _edit(query, qm_tr(language, "invalid_slots"), _qm_menu_keyboard(language))
        return QM_MENU

    try:
        target_chat = await ensure_chat_access(query.get_bot(), int(chat_id), user_id)
        await upsert_queue_slots(user_id, int(chat_id), slots, timezone_key)
    except ChatAccessError as exc:
        await query.answer(str(exc), show_alert=True)
        return QM_CONFIRM_SLOTS
    except Exception:
        logger.exception(
            "Failed to save daily queue slots for user_id=%s chat_id=%s",
            user_id, chat_id,
        )
        await query.answer(qm_tr(language, "item_save_error"), show_alert=True)
        return QM_CONFIRM_SLOTS

    title = target_chat.title or wizard.get("slots_chat_title") or str(chat_id)
    timezone_label = next(
        (label for key, label in TIMEZONES if key == timezone_key), timezone_key
    )
    message_text = qm_tr(
        language, "slots_saved",
        group=escape_markdown(str(title), version=1),
        slots=escape_markdown(" • ".join(slots), version=1),
        timezone=escape_markdown(timezone_label, version=1),
    )
    _clear(context)
    await query.answer()
    await chat_cleanup.cleanup(
        context, query.message.chat_id,
        keep_message_id=query.message.message_id,
    )
    await query.edit_message_text(
        message_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_qm_menu_keyboard(language),
    )
    return QM_MENU
async def cb_qm_tz_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paginate the localized Queue Manager timezone picker."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    page_str = (query.data or "").replace(CB_QM_TZ_PAGE, "")
    page = int(page_str) if page_str.isdigit() else 0
    _w(context)["qm_tz_page"] = page
    await _edit(
        query,
        qm_tr(language, "timezone_prompt"),
        _qm_tz_keyboard(page, language),
    )
    return QM_SET_SLOTS_TZ
async def cb_back_qm_slot_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to the localized destination picker from daily-time entry."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    wizard = _w(context)
    for key in ("slots_chat_id", "slots_chat_title", "pending_slots", "pending_timezone"):
        wizard.pop(key, None)

    user_id = query.from_user.id
    bot_username = (await query.get_bot().get_me()).username or "YourSchedulerBot"
    groups = await list_groups_for_user(user_id)
    range_text = (
        f"({min(_QM_CHAT_PAGE_SIZE, len(groups))} / {len(groups)} available)"
        if groups else ""
    )
    keyboard = (
        _qm_group_picker_keyboard(
            groups, 0, CB_QM_SS_PICK, CB_QM_SS_PAGE, CB_QM_SS_MANUAL,
            CB_BACK_QM_MENU, language,
        )
        if groups else
        _qm_no_groups_keyboard(
            CB_QM_SS_MANUAL, CB_BACK_QM_MENU, bot_username, language
        )
    )
    await _edit(query, qm_tr(language, "choose_group", range_text=range_text), keyboard)
    return QM_SET_SLOTS_CHAT_ID
async def cb_back_qm_slot_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return from timezone/review to daily-time entry."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    wizard = _w(context)
    chat_id = wizard.get("slots_chat_id", "?")
    title = escape_markdown(
        str(wizard.get("slots_chat_title", chat_id)), version=1
    )
    wizard.pop("pending_slots", None)
    wizard.pop("pending_timezone", None)
    await _edit(
        query,
        qm_tr(language, "slots_times_prompt", group=title),
        _qm_nav_keyboard(language, back_data=CB_BACK_QM_SLOT_CHAT),
    )
    return QM_SET_SLOTS
async def _has_slots(user_id: int, chat_id: int) -> bool:
    """Return True only when a slot document with ≥1 slot already exists."""
    doc = await get_queue(user_id, chat_id)
    return bool(doc and doc.get("slots"))


def _no_slots_warning_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    """Localized action buttons for destinations without daily slots."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            qm_tr(language, "button_set_slots_now"),
            callback_data=CB_QM_SET_SLOTS,
        )],
        [InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_QM_MENU)],
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


def _mp_menu_text(language: str = "en") -> str:
    return mp_tr(language, "menu_intro")


def _mp_intro_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    """Navigation for the first-time Media Pool introduction."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_MAIN_MENU),
        InlineKeyboardButton(mp_tr(language, "continue"), callback_data=CB_MP_CONTINUE),
    ]])


def _mp_menu_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(mp_tr(language, "add_content"), callback_data=CB_MP_ADD_ITEM)],
        [InlineKeyboardButton(mp_tr(language, "set_schedule"), callback_data=CB_MP_SET_INTERVAL)],
        [
            InlineKeyboardButton(mp_tr(language, "pool_overview"), callback_data=CB_MP_VIEW),
            InlineKeyboardButton(mp_tr(language, "manage_pool"), callback_data=CB_MP_MANAGE),
        ],
        [
            InlineKeyboardButton(tr(language, "menu_title"), callback_data=CB_MAIN_MENU),
            InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
        ],
    ])


def _mp_add_item_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(mp_tr(language, "schedule_from_add"), callback_data=CB_MP_ADD_SCHEDULE)],
        [InlineKeyboardButton(mp_tr(language, "finish_add"), callback_data=CB_MP_ADD_DONE)],
        [InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL)],
    ])


def _mp_quick_interval_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    minutes = mp_tr(language, "minutes")
    hours = mp_tr(language, "hours")
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"15 {minutes}", callback_data=f"{CB_MP_QUICK_PREFIX}15m"),
            InlineKeyboardButton(f"30 {minutes}", callback_data=f"{CB_MP_QUICK_PREFIX}30m"),
            InlineKeyboardButton(f"1 {hours}", callback_data=f"{CB_MP_QUICK_PREFIX}1h"),
        ],
        [
            InlineKeyboardButton(f"3 {hours}", callback_data=f"{CB_MP_QUICK_PREFIX}3h"),
            InlineKeyboardButton(f"6 {hours}", callback_data=f"{CB_MP_QUICK_PREFIX}6h"),
            InlineKeyboardButton(f"12 {hours}", callback_data=f"{CB_MP_QUICK_PREFIX}12h"),
        ],
        [
            InlineKeyboardButton(f"24 {hours}", callback_data=f"{CB_MP_QUICK_PREFIX}24h"),
            InlineKeyboardButton(mp_tr(language, "custom"), callback_data=CB_MP_CUSTOM_INTERVAL),
        ],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MP_MENU),
            InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
        ],
    ])


def _mp_first_run_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(mp_tr(language, "start_now"), callback_data=CB_MP_FIRST_NOW)],
        [InlineKeyboardButton(mp_tr(language, "choose_time"), callback_data=CB_MP_FIRST_CUSTOM)],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MP_IV),
            InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
        ],
    ])


def _mp_group_picker_keyboard(
    groups: list[dict], page: int, language: str = "en"
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    total = len(groups)
    start = page * _QM_CHAT_PAGE_SIZE
    end = min(start + _QM_CHAT_PAGE_SIZE, total)
    type_icon = {"channel": "📢", "supergroup": "👥", "group": "👥"}
    for group in groups[start:end]:
        icon = type_icon.get(group.get("chat_type", ""), "💬")
        title = (group.get("title") or f"Chat {group['chat_id']}")[:35]
        rows.append([
            InlineKeyboardButton(
                f"{icon} {title}",
                callback_data=f"{CB_MP_PICK}{group['chat_id']}",
            )
        ])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            mp_tr(language, "prev"), callback_data=f"{CB_MP_PAGE}{page - 1}"
        ))
    if end < total:
        nav.append(InlineKeyboardButton(
            mp_tr(language, "next"), callback_data=f"{CB_MP_PAGE}{page + 1}"
        ))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(mp_tr(language, "manual_id"), callback_data=CB_MP_MANUAL)
    ])
    rows.append([
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MP_MENU),
        InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _mp_manage_picker_keyboard(
    pools: list[dict],
    group_titles: dict[int, str],
    page: int,
    language: str = "en",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    start = page * _MP_POOL_PAGE_SIZE
    end = min(start + _MP_POOL_PAGE_SIZE, len(pools))
    for pool in pools[start:end]:
        chat_id = int(pool["chat_id"])
        count = len(pool.get("items", []))
        title = (group_titles.get(chat_id) or f"Chat {chat_id}")[:28]
        rows.append([
            InlineKeyboardButton(
                mp_tr(language, "pool_button", group=title, count=count),
                callback_data=f"{CB_MP_MANAGE_PICK}{chat_id}",
            )
        ])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            mp_tr(language, "prev"), callback_data=f"{CB_MP_MANAGE_PAGE}{page - 1}"
        ))
    if end < len(pools):
        nav.append(InlineKeyboardButton(
            mp_tr(language, "next"), callback_data=f"{CB_MP_MANAGE_PAGE}{page + 1}"
        ))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MP_MENU),
        InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
    ])
    return InlineKeyboardMarkup(rows)


def _mp_manage_actions_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(mp_tr(language, "reset_cycle"), callback_data=CB_MP_RESET)],
        [InlineKeyboardButton(mp_tr(language, "clear_pool"), callback_data=CB_MP_CLEAR)],
        [
            InlineKeyboardButton(mp_tr(language, "all_pools"), callback_data=CB_BACK_MP_MANAGE),
            InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
        ],
    ])


def _mp_manage_confirm_keyboard(
    confirm_callback: str,
    language: str = "en",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(tr(language, "confirm"), callback_data=confirm_callback),
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MP_DETAILS),
        ],
        [InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL)],
    ])


def _mp_nav_keyboard(
    language: str,
    back_data: str | None = None,
) -> InlineKeyboardMarkup:
    """Navigation keyboard localized to the user's selected language."""
    row: list[InlineKeyboardButton] = []
    if back_data:
        row.append(InlineKeyboardButton(tr(language, "back"), callback_data=back_data))
    row.append(InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL))
    return InlineKeyboardMarkup([row])


def _mp_no_groups_keyboard(language: str, bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            mp_tr(language, "add_bot"),
            url=f"https://t.me/{bot_username}?startgroup=start",
        )],
        [InlineKeyboardButton(
            mp_tr(language, "manual_id"),
            callback_data=CB_MP_MANUAL,
        )],
        [
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MP_MENU),
            InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
        ],
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
    language = await resolve_user_language(context, query.from_user.id)
    context.user_data["active_media_pool"] = True
    _clear_mp(context)
    chat_cleanup.reset(context.application.bot_data, query.message.chat_id)
    chat_cleanup.track(
        context.application.bot_data,
        query.message.chat_id,
        query.message.message_id,
    )
    await _edit(query, mp_tr(language, "onboarding"), _mp_intro_keyboard(language))
    return MP_INTRO


async def cb_mp_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Open the existing Media Pool actions after the user reads the guide."""
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    await _edit(query, _mp_menu_text(language), _mp_menu_keyboard(language))
    return MP_MENU


async def _mp_show_group_picker(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
) -> int:
    user_id = query.from_user.id
    language = await resolve_user_language(context, user_id)
    try:
        groups = await list_groups_for_user(user_id)
    except Exception:
        logger.exception("Media Pool group picker failed for user_id=%s", user_id)
        await query.answer(mp_tr(language, "pool_check_error"), show_alert=True)
        return MP_CHAT_ID

    if not groups:
        bot_username = (await query.get_bot().get_me()).username or "YourSchedulerBot"
        await _edit(
            query,
            mp_tr(language, "no_groups"),
            _mp_no_groups_keyboard(language, bot_username),
        )
        return MP_CHAT_ID

    total = len(groups)
    page = max(0, min(page, (total - 1) // _QM_CHAT_PAGE_SIZE))
    range_text = (
        f"({page * _QM_CHAT_PAGE_SIZE + 1}–"
        f"{min((page + 1) * _QM_CHAT_PAGE_SIZE, total)} / {total})"
    )
    await _edit(
        query,
        mp_tr(language, "select_group", range_text=range_text),
        _mp_group_picker_keyboard(groups, page, language),
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
    language = await resolve_user_language(context, query.from_user.id)
    try:
        chat_id = int(raw)
        target_chat = await ensure_chat_access(
            query.get_bot(),
            chat_id,
            query.from_user.id,
        )
    except ValueError:
        await query.answer(mp_tr(language, "invalid_chat_id"), show_alert=True)
        return MP_CHAT_ID
    except ChatAccessError:
        await query.answer(mp_tr(language, "access_error"), show_alert=True)
        return MP_CHAT_ID
    return await _mp_continue_after_chat_selection(update, context, target_chat)


async def cb_mp_manual_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    await _edit(
        query,
        mp_tr(language, "manual_id_prompt"),
        _mp_nav_keyboard(language, back_data=CB_BACK_MP_MENU),
    )
    return MP_CHAT_ID


async def _mp_continue_after_chat_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    target_chat: Any,
) -> int:
    pm = _pm(context)
    language = await resolve_user_language(context, update.effective_user.id)
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
                mp_tr(language, "pool_check_error"),
                _mp_nav_keyboard(language, back_data=CB_BACK_MP_MENU),
            )
            return MP_CHAT_ID

        if item_count == 0:
            pm["mode"] = "add"
            await _mp_reply_or_edit(
                update,
                f"{title}\n\n{mp_tr(language, 'empty_pool')}",
                InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        mp_tr(language, "add_content"),
                        callback_data=CB_MP_ADD_SELECTED,
                    )],
                    [InlineKeyboardButton(
                        tr(language, "back"),
                        callback_data=CB_BACK_MP_MENU,
                    )],
                    [InlineKeyboardButton(
                        tr(language, "cancel"),
                        callback_data=CB_CANCEL,
                    )],
                ]),
            )
            return MP_CHAT_ID

        return await _mp_prompt_interval(update, context)

    await _mp_prompt_add_item(update, context)
    return MP_ADD_ITEM


async def _mp_prompt_add_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    pm = _pm(context)
    language = await resolve_user_language(context, update.effective_user.id)
    title = escape_markdown(pm.get("mp_chat_title", "Selected group"), version=1)
    await _mp_reply_or_edit(
        update,
        mp_tr(language, "add_prompt", group=title),
        _mp_add_item_keyboard(language),
    )


async def cb_mp_add_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    language = await resolve_user_language(context, query.from_user.id)
    if not pm.get("mp_chat_id"):
        await query.answer(mp_tr(language, "pool_missing"), show_alert=True)
        _clear_mp(context)
        await _edit(query, _mp_menu_text(language), _mp_menu_keyboard(language))
        return MP_MENU
    pm["mode"] = "add"
    await _mp_prompt_add_item(update, context)
    return MP_ADD_ITEM


async def recv_mp_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    language = await resolve_user_language(context, update.effective_user.id)
    try:
        chat_id = int(raw)
    except ValueError:
        await update.message.reply_text(mp_tr(language, "invalid_chat_id"))
        return MP_CHAT_ID

    try:
        target_chat = await ensure_chat_access(
            context.bot,
            chat_id,
            update.effective_user.id,
        )
    except ChatAccessError:
        await update.message.reply_text(mp_tr(language, "access_error"))
        return MP_CHAT_ID

    return await _mp_continue_after_chat_selection(update, context, target_chat)


async def recv_mp_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    pm = _pm(context)
    language = await resolve_user_language(context, msg.from_user.id)
    if not pm.get("mp_chat_id"):
        await msg.reply_text(mp_tr(language, "pool_missing"))
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
        await msg.reply_text(mp_tr(language, "unsupported"))
        return MP_ADD_ITEM

    user_id = msg.from_user.id
    chat_id = int(pm["mp_chat_id"])
    try:
        await add_to_media_pool(user_id, chat_id, item)
    except Exception:
        logger.exception("Could not save Media Pool item for user_id=%s chat_id=%s", user_id, chat_id)
        await msg.reply_text(mp_tr(language, "save_error"))
        return MP_ADD_ITEM

    count: int | None = None
    try:
        db = await get_db()
        pool = await db[COL_MEDIA_POOLS].find_one(
            {"user_id": user_id, "chat_id": chat_id}
        )
        count = len(pool.get("items", [])) if pool else None
    except Exception:
        logger.exception("Item saved, but Media Pool count lookup failed for chat_id=%s", chat_id)

    title = pm.get("mp_chat_title", str(chat_id))
    count_display = str(count) if count is not None else "—"
    await msg.reply_text(
        mp_tr(language, "item_saved", group=title, count=count_display),
        reply_markup=_mp_add_item_keyboard(language),
    )
    return MP_ADD_ITEM


async def cb_mp_add_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    _clear_mp(context)
    await _edit(
        query,
        mp_tr(language, "finish_add"),
        _mp_menu_keyboard(language),
    )
    return MP_MENU


async def cb_mp_add_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    language = await resolve_user_language(context, query.from_user.id)
    chat_id = pm.get("mp_chat_id")
    if not chat_id:
        _clear_mp(context)
        await _edit(query, _mp_menu_text(language), _mp_menu_keyboard(language))
        return MP_MENU
    try:
        db = await get_db()
        pool = await db[COL_MEDIA_POOLS].find_one(
            {"user_id": query.from_user.id, "chat_id": int(chat_id)}
        )
        if not pool or not pool.get("items"):
            await query.answer(mp_tr(language, "empty_pool"), show_alert=True)
            return MP_ADD_ITEM
    except Exception:
        logger.exception("Could not verify Media Pool items before scheduling")
        await query.answer(mp_tr(language, "pool_check_error"), show_alert=True)
        return MP_ADD_ITEM
    pm["mode"] = "interval"
    return await _mp_prompt_interval(update, context)


async def _mp_prompt_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pm = _pm(context)
    language = await resolve_user_language(context, update.effective_user.id)
    title = escape_markdown(pm.get("mp_chat_title", "Selected group"), version=1)
    await _mp_reply_or_edit(
        update,
        mp_tr(language, "interval_prompt", group=title),
        _mp_quick_interval_keyboard(language),
    )
    return MP_SET_INTERVAL_VALUE


async def cb_mp_custom_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    await _edit(
        query,
        mp_tr(language, "custom_prompt"),
        _mp_nav_keyboard(language, back_data=CB_BACK_MP_MENU),
    )
    return MP_SET_INTERVAL_VALUE


async def cb_mp_quick_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    choice = (query.data or "").replace(CB_MP_QUICK_PREFIX, "", 1)
    selected = _MP_QUICK_INTERVALS.get(choice)
    if selected is None:
        await query.answer(mp_tr(language, "invalid_interval"), show_alert=True)
        return MP_SET_INTERVAL_VALUE
    value, unit, _label = selected
    pm = _pm(context)
    pm["interval_value"] = value
    pm["interval_unit"] = unit
    pm["interval_label"] = f"{value} {mp_tr(language, unit)}"
    await query.answer()
    await _mp_prompt_first_run(query, context)
    return MP_SET_FIRST_RUN


async def recv_mp_interval_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    language = await resolve_user_language(context, update.effective_user.id)
    if not raw.isdigit() or int(raw) <= 0 or int(raw) > 100000:
        await update.message.reply_text(mp_tr(language, "invalid_interval"))
        return MP_SET_INTERVAL_VALUE
    pm = _pm(context)
    pm["interval_value"] = int(raw)
    pm.pop("interval_label", None)
    await update.message.reply_text(
        f"{raw} — {mp_tr(language, 'custom')}",
        reply_markup=_mp_interval_unit_keyboard(language),
    )
    return MP_SET_INTERVAL_UNIT


async def cb_mp_interval_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    unit_map = {
        CB_MP_IU_MINUTES: "minutes",
        CB_MP_IU_HOURS: "hours",
        CB_MP_IU_DAYS: "days",
    }
    unit = unit_map.get(query.data)
    if unit is None:
        await query.answer(mp_tr(language, "invalid_interval"), show_alert=True)
        return MP_SET_INTERVAL_UNIT
    pm = _pm(context)
    pm["interval_unit"] = unit
    pm["interval_label"] = f"{pm.get('interval_value', 1)} {mp_tr(language, unit)}"
    await query.answer()
    await _mp_prompt_first_run(query, context)
    return MP_SET_FIRST_RUN


async def _mp_prompt_first_run(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    pm = _pm(context)
    language = _mp_language(context)
    label = pm.get("interval_label", mp_tr(language, "custom"))
    await query.edit_message_text(
        mp_tr(language, "first_prompt", interval=label),
        reply_markup=_mp_first_run_keyboard(language),
    )


async def cb_mp_first_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    language = await resolve_user_language(context, query.from_user.id)
    pm["first_run"] = "now"
    pm.setdefault("tz_page", 0)
    await query.answer()
    await query.edit_message_text(
        mp_tr(language, "timezone"),
        reply_markup=_mp_tz_keyboard(pm["tz_page"], language),
    )
    return MP_SET_TIMEZONE


async def cb_mp_first_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    await _edit(
        query,
        mp_tr(language, "time_prompt"),
        _mp_nav_keyboard(language, back_data=CB_BACK_MP_FR),
    )
    return MP_SET_FIRST_RUN


async def recv_mp_first_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip().lower()
    pm = _pm(context)
    language = await resolve_user_language(context, update.effective_user.id)
    if raw == "now":
        pm["first_run"] = "now"
    else:
        parsed = _parse_hhmm(raw)
        if parsed is None:
            await update.message.reply_text(mp_tr(language, "invalid_time"))
            return MP_SET_FIRST_RUN
        pm["first_run"] = parsed
    pm.setdefault("tz_page", 0)
    await update.message.reply_text(
        mp_tr(language, "timezone"),
        reply_markup=_mp_tz_keyboard(pm["tz_page"], language),
    )
    return MP_SET_TIMEZONE


async def cb_mp_tz_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    raw = (query.data or "").replace(CB_MP_TZ_PAGE, "", 1)
    page = int(raw) if raw.isdigit() else 0
    _pm(context)["tz_page"] = page
    await query.answer()
    await query.edit_message_text(
        mp_tr(language, "timezone"),
        reply_markup=_mp_tz_keyboard(page, language),
    )
    return MP_SET_TIMEZONE


async def cb_mp_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    tz_key = (query.data or "").replace("mptz:", "", 1)
    try:
        pytz.timezone(tz_key)
    except pytz.exceptions.UnknownTimeZoneError:
        await query.answer(mp_tr(language, "invalid_timezone"), show_alert=True)
        return MP_SET_TIMEZONE
    pm = _pm(context)
    pm["timezone"] = tz_key
    await query.answer()
    await query.edit_message_text(
        mp_tr(language, "auto_delete"),
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(mp_tr(language, "yes"), callback_data=CB_MP_AD_YES),
                InlineKeyboardButton(mp_tr(language, "keep_posts"), callback_data=CB_MP_AD_NO),
            ],
            [InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MP_TZ)],
            [InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL)],
        ]),
    )
    return MP_AUTO_DELETE


def _mp_summary_text(pm: dict, language: str = "en") -> str:
    title = pm.get("mp_chat_title", str(pm.get("mp_chat_id", "?")))
    value = pm.get("interval_value", "?")
    unit = pm.get("interval_unit", "?")
    label = pm.get("interval_label") or f"{value} {mp_tr(language, unit)}"
    first_run = pm.get("first_run", "?")
    tz_key = pm.get("timezone", "UTC")
    tz_label = next((key for key, _label in TIMEZONES if key == tz_key), tz_key)
    first_run_display = mp_tr(language, "start_now") if first_run == "now" else first_run
    ad_hours = pm.get("mp_ad_hours")
    ad_line = (
        f"{ad_hours} {mp_tr(language, 'hours')}"
        if ad_hours
        else mp_tr(language, "keep_posts")
    )
    return mp_tr(
        language,
        "schedule_summary",
        group=title,
        interval=label,
        first_post=first_run_display,
        timezone=tz_label,
        auto_delete=ad_line,
    )


def _mp_schedule_confirm_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(tr(language, "confirm"), callback_data=CB_MP_CONFIRM),
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_BACK_MP_TZ),
        ],
        [InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL)],
    ])


async def cb_mp_ad_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    await _edit(
        query,
        mp_tr(language, "delete_delay"),
        _mp_nav_keyboard(language, back_data=CB_BACK_MP_TZ),
    )
    return MP_AUTO_DELETE


async def recv_mp_ad_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip().replace(",", ".")
    language = await resolve_user_language(context, update.effective_user.id)
    try:
        value = float(raw)
        if value <= 0 or value > 8760:
            raise ValueError
    except ValueError:
        await update.message.reply_text(mp_tr(language, "invalid_delete_delay"))
        return MP_AUTO_DELETE
    pm = _pm(context)
    pm["mp_ad_hours"] = value
    await update.message.reply_text(
        _mp_summary_text(pm, language),
        reply_markup=_mp_schedule_confirm_keyboard(language),
    )
    return MP_CONFIRM


async def cb_mp_ad_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    pm = _pm(context)
    pm.pop("mp_ad_hours", None)
    await _edit(
        query,
        _mp_summary_text(pm, language),
        _mp_schedule_confirm_keyboard(language),
    )
    return MP_CONFIRM


async def cb_back_mp_tz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    pm = _pm(context)
    pm.pop("mp_ad_hours", None)
    page = pm.get("tz_page", 0)
    await query.answer()
    await query.edit_message_text(
        mp_tr(language, "timezone"),
        reply_markup=_mp_tz_keyboard(page, language),
    )
    return MP_SET_TIMEZONE




async def cb_mp_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from database import build_scheduled_post, insert_post

    query = update.callback_query
    pm = _pm(context)
    language = await resolve_user_language(context, query.from_user.id)
    user_id = query.from_user.id
    chat_id = pm.get("mp_chat_id")
    value = pm.get("interval_value")
    unit = pm.get("interval_unit")
    first_run = pm.get("first_run")
    tz_str = pm.get("timezone", "UTC")

    if not all([chat_id, value, unit, first_run]):
        await query.answer(mp_tr(language, "schedule_save_error"), show_alert=True)
        return MP_MENU

    try:
        await ensure_chat_access(query.get_bot(), int(chat_id), user_id)
    except ChatAccessError:
        await query.answer(mp_tr(language, "access_error"), show_alert=True)
        return MP_CONFIRM

    await query.answer()
    try:
        db = await get_db()
        pool = await db[COL_MEDIA_POOLS].find_one(
            {"user_id": user_id, "chat_id": chat_id}
        )
        if not pool or not pool.get("items"):
            _clear_mp(context)
            await query.edit_message_text(
                mp_tr(language, "empty_pool"),
                reply_markup=_mp_menu_keyboard(language),
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
                mp_tr(language, "duplicate_schedule"),
                reply_markup=_mp_menu_keyboard(language),
            )
            return MP_MENU
    except Exception:
        logger.exception("Could not validate Media Pool before schedule creation")
        await query.edit_message_text(
            mp_tr(language, "pool_check_error"),
            reply_markup=_mp_menu_keyboard(language),
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
        await insert_post(doc)
    except Exception:
        logger.exception("Media Pool schedule save failed for chat_id=%s", chat_id)
        await query.edit_message_text(
            mp_tr(language, "schedule_save_error"),
            reply_markup=_mp_menu_keyboard(language),
        )
        _clear_mp(context)
        return MP_MENU

    title = pm.get("mp_chat_title", str(chat_id))
    first_display = mp_tr(language, "start_now") if first_run == "now" else first_run
    interval_label = pm.get("interval_label") or f"{value} {mp_tr(language, unit)}"
    auto_delete_label = (
        f"{auto_delete_hours} {mp_tr(language, 'hours')}"
        if auto_delete_hours
        else mp_tr(language, "keep_posts")
    )
    success_text = mp_tr(
        language,
        "schedule_active",
        group=title,
        interval=interval_label,
        first_post=first_display,
        timezone=tz_str,
        auto_delete=auto_delete_label,
    )
    _clear_mp(context)
    await chat_cleanup.cleanup(
        context,
        query.message.chat_id,
        keep_message_id=query.message.message_id,
    )
    await query.edit_message_text(
        success_text,
        reply_markup=_mp_menu_keyboard(language),
    )
    return MP_MENU


async def cb_back_mp_interval_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    _clear_mp(context)
    await _edit(query, _mp_menu_text(language), _mp_menu_keyboard(language))
    return MP_MENU


async def cb_back_mp_interval_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    await _edit(
        query,
        mp_tr(language, "interval_prompt", group=_pm(context).get("mp_chat_title", "")),
        _mp_quick_interval_keyboard(language),
    )
    return MP_SET_INTERVAL_VALUE


async def cb_back_mp_first_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    label = _pm(context).get("interval_label", mp_tr(language, "custom"))
    await _edit(
        query,
        mp_tr(language, "first_prompt", interval=label),
        _mp_first_run_keyboard(language),
    )
    return MP_SET_FIRST_RUN


async def cb_mp_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    context.user_data.pop("active_media_pool", None)
    _clear_mp(context)
    await query.answer()
    await query.edit_message_text(
        tr(language, "menu_title"),
        reply_markup=main_menu_keyboard(
            bot=context.bot,
            user_id=query.from_user.id,
            language=language,
        ),
    )
    return ConversationHandler.END


async def cb_mp_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
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
            mp_tr(language, "pool_check_error"),
            reply_markup=_mp_menu_keyboard(language),
        )
        return MP_MENU

    if not pools:
        await query.edit_message_text(
            mp_tr(language, "overview_empty"),
            reply_markup=_mp_menu_keyboard(language),
        )
        return MP_MENU

    title_map = {
        int(group["chat_id"]): group.get("title") or str(group["chat_id"])
        for group in groups
    }
    lines = [mp_tr(language, "pool_overview"), ""]
    for pool in pools:
        items = pool.get("items", [])
        total = len(items)
        remaining = sum(1 for item in items if not item.get("posted", False))
        title = title_map.get(int(pool["chat_id"])) or f"Chat {pool['chat_id']}"
        lines.append(mp_tr(
            language,
            "overview_summary",
            group=title,
            total=total,
            remaining=remaining,
            published=total - remaining,
        ))
    lines.append(mp_tr(language, "cycle_note"))
    await query.edit_message_text(
        "\n\n".join(lines),
        reply_markup=_mp_menu_keyboard(language),
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


async def _mp_show_manage_picker(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
) -> int:
    user_id = query.from_user.id
    language = await resolve_user_language(context, user_id)
    try:
        pools, title_map = await _mp_load_manage_data(user_id)
    except Exception:
        logger.exception("Could not load Media Pool management list")
        await query.answer(mp_tr(language, "pool_check_error"), show_alert=True)
        return MP_MANAGE_CHAT

    if not pools:
        await _edit(
            query,
            mp_tr(language, "manage_empty"),
            _mp_menu_keyboard(language),
        )
        return MP_MENU

    pools.sort(
        key=lambda pool: (
            title_map.get(int(pool["chat_id"])) or str(pool["chat_id"])
        ).casefold()
    )
    total = len(pools)
    page = max(0, min(page, (total - 1) // _MP_POOL_PAGE_SIZE))
    await _edit(
        query,
        mp_tr(language, "manage_select"),
        _mp_manage_picker_keyboard(pools, title_map, page, language),
    )
    return MP_MANAGE_CHAT


async def cb_mp_manage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _mp_show_manage_picker(update.callback_query, context)


async def cb_mp_manage_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    raw = (query.data or "").replace(CB_MP_MANAGE_PAGE, "", 1)
    page = int(raw) if raw.isdigit() else 0
    return await _mp_show_manage_picker(query, context, page)


async def _mp_manage_summary(
    user_id: int,
    chat_id: int,
    language: str = "en",
) -> tuple[str, InlineKeyboardMarkup]:
    db = await get_db()
    pool = await db[COL_MEDIA_POOLS].find_one({"user_id": user_id, "chat_id": chat_id})
    if not pool:
        raise LookupError("This Media Pool no longer exists.")
    groups = await list_groups_for_user(user_id)
    title_map = {
        int(group["chat_id"]): group.get("title") or str(group["chat_id"])
        for group in groups
    }
    title = escape_markdown(title_map.get(chat_id) or f"Chat {chat_id}", version=1)
    items = pool.get("items", [])
    total = len(items)
    remaining = sum(1 for item in items if not item.get("posted", False))
    text = mp_tr(
        language,
        "manage_summary",
        group=title,
        total=total,
        remaining=remaining,
        published=total - remaining,
    )
    return text, _mp_manage_actions_keyboard(language)


async def cb_mp_manage_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    raw = (query.data or "").replace(CB_MP_MANAGE_PICK, "", 1)
    language = await resolve_user_language(context, query.from_user.id)
    try:
        chat_id = int(raw)
        target_chat = await ensure_chat_access(query.get_bot(), chat_id, query.from_user.id)
        text, keyboard = await _mp_manage_summary(query.from_user.id, chat_id, language)
    except (ValueError, ChatAccessError, LookupError):
        await query.answer(mp_tr(language, "access_error"), show_alert=True)
        return MP_MANAGE_CHAT
    except Exception:
        logger.exception("Could not open Media Pool details")
        await query.answer(mp_tr(language, "pool_check_error"), show_alert=True)
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
    language = await resolve_user_language(context, query.from_user.id)
    pm = _pm(context)
    chat_id = pm.get("mp_chat_id")
    if not chat_id:
        return await _mp_show_manage_picker(query, context)
    try:
        text, keyboard = await _mp_manage_summary(query.from_user.id, int(chat_id), language)
    except Exception:
        logger.exception("Could not reload Media Pool details")
        return await _mp_show_manage_picker(query, context)
    await _edit(query, text, keyboard)
    return MP_MANAGE_ACTION


async def cb_mp_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    title = escape_markdown(_pm(context).get("mp_chat_title", "selected group"), version=1)
    await _edit(
        query,
        mp_tr(language, "reset_confirm", group=title),
        _mp_manage_confirm_keyboard(CB_MP_RESET_YES, language),
    )
    return MP_MANAGE_ACTION


async def cb_mp_reset_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    pm = _pm(context)
    language = await resolve_user_language(context, query.from_user.id)
    chat_id = pm.get("mp_chat_id")
    if not chat_id:
        await query.answer(mp_tr(language, "pool_missing"), show_alert=True)
        return MP_MANAGE_CHAT
    try:
        await ensure_chat_access(query.get_bot(), int(chat_id), query.from_user.id)
    except ChatAccessError:
        await query.answer(mp_tr(language, "access_error"), show_alert=True)
        return MP_MANAGE_ACTION
    try:
        await reset_media_pool(query.from_user.id, int(chat_id))
        text, keyboard = await _mp_manage_summary(
            query.from_user.id, int(chat_id), language
        )
    except Exception:
        logger.exception("Could not reset Media Pool cycle for chat_id=%s", chat_id)
        await query.answer(mp_tr(language, "pool_check_error"), show_alert=True)
        return MP_MANAGE_ACTION
    await _edit(query, mp_tr(language, "reset_done") + "\n\n" + text, keyboard)
    return MP_MANAGE_ACTION


async def cb_mp_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    title = escape_markdown(_pm(context).get("mp_chat_title", "selected group"), version=1)
    await _edit(
        query,
        mp_tr(language, "clear_confirm", group=title),
        _mp_manage_confirm_keyboard(CB_MP_CLEAR_YES, language),
    )
    return MP_MANAGE_ACTION


async def cb_mp_clear_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    user_id = query.from_user.id
    chat_id = _pm(context).get("mp_chat_id")
    if not chat_id:
        await query.answer(mp_tr(language, "pool_missing"), show_alert=True)
        return MP_MANAGE_CHAT
    try:
        await ensure_chat_access(query.get_bot(), int(chat_id), user_id)
    except ChatAccessError:
        await query.answer(mp_tr(language, "access_error"), show_alert=True)
        return MP_MANAGE_ACTION

    try:
        db = await get_db()
        result = await db[COL_MEDIA_POOLS].delete_one({
            "user_id": user_id,
            "chat_id": int(chat_id),
        })
    except Exception:
        logger.exception("Could not delete Media Pool for user_id=%s chat_id=%s", user_id, chat_id)
        await query.answer(mp_tr(language, "pool_check_error"), show_alert=True)
        return MP_MANAGE_ACTION

    schedule_pause_ok = True
    try:
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
        schedule_pause_ok = False
        logger.exception("Pool content was deleted but schedule pause failed for chat_id=%s", chat_id)

    _clear_mp(context)
    if result.deleted_count and schedule_pause_ok:
        message = mp_tr(language, "clear_done")
    elif result.deleted_count:
        message = mp_tr(language, "clear_partial")
    else:
        message = mp_tr(language, "pool_missing")
    await _edit(query, message, _mp_menu_keyboard(language))
    return MP_MENU


async def cb_back_mp_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    language = await resolve_user_language(context, query.from_user.id)
    _clear_mp(context)
    await _edit(query, _mp_menu_text(language), _mp_menu_keyboard(language))
    return MP_MENU


# ── Cancel (with full message cleanup) ──

async def cmd_mp_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Handle /cancel inside Media Pool using the user's selected language."""
    message = update.effective_message
    user = update.effective_user
    language = (
        await resolve_user_language(context, user.id)
        if user is not None
        else "en"
    )
    context.user_data.pop("active_media_pool", None)
    _clear_mp(context)
    if message is None:
        return ConversationHandler.END
    chat_id = update.effective_chat.id if update.effective_chat else message.chat_id
    await chat_cleanup.cleanup(
        context,
        chat_id,
        keep_message_id=message.message_id,
    )
    await message.reply_text(
        mp_tr(language, "cancelled"),
        reply_markup=main_menu_keyboard(
            bot=context.bot,
            user_id=user.id if user is not None else None,
            language=language,
        ),
    )
    return ConversationHandler.END


async def cb_cancel_local(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    language = (
        await resolve_user_language(context, user_id)
        if user_id is not None
        else "en"
    )
    is_media_pool = (
        context.user_data.pop("active_media_pool", False)
        or "mpwiz" in context.user_data
    )
    await query.answer()
    _clear(context)
    _clear_mp(context)
    chat_id = query.message.chat_id
    await chat_cleanup.cleanup(
        context,
        chat_id,
        keep_message_id=query.message.message_id,
    )
    cancel_text = (
        mp_tr(language, "cancelled")
        if is_media_pool
        else "❌ *Cancelled.* Back to Main Menu."
    )
    await query.edit_message_text(
        cancel_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(
            bot=context.bot,
            user_id=user_id,
            language=language if is_media_pool else "en",
        ),
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
            MP_INTRO: [
                CallbackQueryHandler(cb_mp_continue, pattern=f"^{CB_MP_CONTINUE}$"),
                CallbackQueryHandler(cb_mp_main_menu, pattern=f"^{CB_MAIN_MENU}$"),
            ],
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
            CommandHandler("cancel", cmd_mp_cancel),
            CallbackQueryHandler(cb_cancel_local, pattern=f"^{CB_CANCEL}$"),
        ],
        allow_reentry=True,
        name="media_pool",
        persistent=False,
    )

