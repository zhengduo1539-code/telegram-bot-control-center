"""Independent non-linear composer for scheduled Telegram posts.

Users configure Content, Destination, Schedule, Delivery Rules and Buttons
from one workspace in any order. Nothing is stored until Review is confirmed.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ConversationHandler, ContextTypes,
    MessageHandler, filters,
)
from telegram.helpers import escape_markdown

from database import build_scheduled_post, insert_post, list_groups_for_user
from handlers import chat_cleanup
from handlers.base import (
    CB_CANCEL, CB_MAIN_MENU, CB_SCHEDULE_NEW, main_menu_keyboard,
    replace_with_main_menu_message,
)
from handlers.language import resolve_user_language
from handlers.permissions import ChatAccessError, ensure_chat_access
from localization import translate as tr
from post_composer_localization import translate_post_composer as pct
from schedule_flow_localization import translate_schedule_flow as flow_tr
from schedule_utils import parse_schedule_datetime, parse_hhmm

logger = logging.getLogger(__name__)

HOME, CONTENT, DESTINATION, MANUAL_DESTINATION, SCHEDULE, TIMEZONE, INTERVAL_VALUE, INTERVAL_UNIT, WEEKDAYS, FIRST_RUN, RUN_LIMIT, OPTIONS, POSTING_WINDOW, AUTO_DELETE, SELF_DESTRUCT, AUTO_PIN, BUTTONS, BUTTON_ENTRY, REVIEW = range(1000, 1019)

P = "post:"
CB_HOME = P + "home"
CB_CONTENT = P + "content"
CB_DESTINATION = P + "destination"
CB_MANUAL = P + "manual"
CB_SCHEDULE = P + "schedule"
CB_MODE_ONCE = P + "mode:once"
CB_MODE_INTERVAL = P + "mode:interval"
CB_MODE_WEEKLY = P + "mode:weekly"
CB_TIMEZONE = P + "timezone"
CB_FIRST_RUN = P + "first"
CB_RUN_LIMIT = P + "limit"
CB_LIMIT_UNLIMITED = P + "unlimited"
CB_OPTIONS = P + "options"
CB_WINDOW = P + "window"
CB_DELETE = P + "delete"
CB_DESTRUCT = P + "destruct"
CB_PIN = P + "pin"
CB_PIN_DURATION = P + "pin:duration"
CB_PIN_FOREVER = P + "pin:forever"
CB_BUTTONS = P + "buttons"
CB_BUTTON_ADD = P + "button:add"
CB_BUTTON_REMOVE = P + "button:remove"
CB_BUTTON_CLEAR = P + "button:clear"
CB_REVIEW = P + "review"
CB_CONFIRM = P + "confirm"
CB_MENU = P + "menu"
CB_CHAT = P + "chat:"
CB_CHAT_PAGE = P + "chats:"
CB_TZ = P + "tz:"
CB_TZ_PAGE = P + "tzpage:"
CB_DAY = P + "day:"
CB_DAYS_DONE = P + "days:done"
CB_UNIT = P + "unit:"
CB_LIMIT = P + "limit:"
CHAT_PAGE_SIZE = 6
TZ_PAGE_SIZE = 6
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
TIMEZONES = (
    ("UTC", "UTC"), ("Asia/Kolkata", "India · GMT+5:30"),
    ("Asia/Dhaka", "Bangladesh · GMT+6"), ("Asia/Rangoon", "Myanmar · GMT+6:30"),
    ("Asia/Bangkok", "Thailand · GMT+7"), ("Asia/Singapore", "Singapore · GMT+8"),
    ("Asia/Tokyo", "Japan · GMT+9"), ("Asia/Seoul", "Korea · GMT+9"),
    ("Europe/London", "London · GMT+0/+1"), ("Europe/Berlin", "Berlin · GMT+1/+2"),
    ("America/New_York", "New York · GMT-5/-4"), ("America/Chicago", "Chicago · GMT-6/-5"),
    ("America/Denver", "Denver · GMT-7/-6"), ("America/Los_Angeles", "Los Angeles · GMT-8/-7"),
    ("Australia/Sydney", "Sydney · GMT+10/+11"),
)


def _w(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    return context.user_data.setdefault("schedule_post", {})


async def _answer(query, text: str | None = None, *, alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=alert)
    except Exception:
        logger.debug("Could not acknowledge composer callback.", exc_info=True)


async def _show(context, w: dict, text: str, keyboard: InlineKeyboardMarkup, parse_mode=ParseMode.MARKDOWN) -> None:
    """Keep the composer on one editable message; fall back to a replacement."""
    chat_id, message_id = w.get("ui_chat_id"), w.get("ui_message_id")
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text,
            parse_mode=parse_mode, reply_markup=keyboard,
        )
        return
    except Exception as exc:
        logger.info("Composer screen could not be edited; creating a replacement: %s", exc)
    message = await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=keyboard,
    )
    w["ui_message_id"] = message.message_id


def _schedule_is_ready(w: dict) -> bool:
    first = w.get("first_run")
    if (
        not isinstance(first, datetime)
        or first.tzinfo is None
        or first.utcoffset() is None
        or first.astimezone(timezone.utc) <= datetime.now(timezone.utc)
    ):
        return False
    mode = w.get("recurrence_type")
    if mode == "once":
        return True
    if mode == "interval":
        value, unit = w.get("interval_value"), w.get("interval_unit")
        limits = {"minutes": 525600, "hours": 8760, "days": 365}
        return isinstance(value, int) and not isinstance(value, bool) and unit in limits and 1 <= value <= limits[unit]
    if mode == "days_of_week":
        days = w.get("days_of_week")
        return bool(days) and all(
            isinstance(day, int) and not isinstance(day, bool) and day in range(7)
            for day in days
        )
    return False


def _lang(w: dict) -> str:
    return str(w.get("language") or "en")


def _t(w: dict, key: str, **values: object) -> str:
    return pct(_lang(w), key, **values)


def _flow(w: dict, key: str, **values: object) -> str:
    return flow_tr(_lang(w), key, **values)


def _weekdays(w: dict) -> list[str]:
    labels = re.split(r"\s*[,،、]\s*|၊\s*", _flow(w, "weekdays"))
    return labels if len(labels) == 7 else list(DAYS)


def _home_keyboard(w: dict) -> InlineKeyboardMarkup:
    content = bool(w.get("content_text") or w.get("media_file_id"))
    destination = isinstance(w.get("chat_id"), int) and w.get("chat_id", 0) < 0
    scheduled = _schedule_is_ready(w)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(("✅ " if content else "📝 ") + _t(w, "section_content"), style="success" if content else "primary", callback_data=CB_CONTENT)],
        [InlineKeyboardButton(("✅ " if destination else "📍 ") + _t(w, "section_destination"), style="success" if destination else "primary", callback_data=CB_DESTINATION)],
        [InlineKeyboardButton(("✅ " if scheduled else "🕒 ") + _t(w, "section_schedule"), style="success" if scheduled else "primary", callback_data=CB_SCHEDULE)],
        [
            InlineKeyboardButton("⚙️ " + _t(w, "section_rules"), style="primary", callback_data=CB_OPTIONS),
            InlineKeyboardButton("🔘 " + _t(w, "section_buttons"), style="primary", callback_data=CB_BUTTONS),
        ],
        [InlineKeyboardButton(_t(w, "preview"), style="success", callback_data=CB_REVIEW)],
        [InlineKeyboardButton(tr(_lang(w), "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])


def _home_text(w: dict, notice: str | None = None) -> str:
    content = bool(w.get("content_text") or w.get("media_file_id"))
    destination = isinstance(w.get("chat_id"), int) and w.get("chat_id", 0) < 0
    scheduled = _schedule_is_ready(w)
    language = _lang(w)
    mode = w.get("recurrence_type", "once")
    if mode == "interval":
        unit_key = f"unit_{w.get('interval_unit')}"
        repeat = _flow(w, "repeat_interval", value=w.get("interval_value", "?"), unit=_flow(w, unit_key))
    elif mode == "days_of_week":
        days = ", ".join(_weekdays(w)[d] for d in sorted(w.get("days_of_week") or set()) if d in range(7))
        repeat = _flow(w, "repeat_weekly", days=days)
    else:
        repeat = _flow(w, "repeat_once")
    timezone_name = str(w.get("timezone") or "UTC")
    first = w.get("first_run")
    try:
        first_text = first.astimezone(pytz.timezone(timezone_name)).strftime("%d %b %Y, %H:%M") if scheduled else _flow(w, "review_not_set")
    except Exception:
        first_text = _flow(w, "review_not_set")
    destination_text = escape_markdown(str(w.get("chat_title") or _t(w, "not_selected")), version=1)
    content_type = (
        _t(w, "media_caption") if w.get("media_file_id") and w.get("content_text")
        else (_t(w, "media_only") if w.get("media_file_id") else _t(w, "text_only"))
    )
    rules = _t(w, "customized") if any((
        w.get("time_window_enabled"), w.get("auto_delete"),
        w.get("self_destruct"), w.get("auto_pin")
    )) else _t(w, "standard")
    ready = _t(w, "ready")
    needs = _t(w, "needs_setup")
    lines = [
        f"*{_t(w, 'workspace_title')}*",
        _t(w, "workspace_intro"),
        "",
    ]
    if notice:
        lines.extend([f"ℹ️ {notice}", ""])
    lines.extend([
        f"📝 *{_t(w, 'section_content')}* — {ready + ' · ' + content_type if content else needs}",
        f"📍 *{_t(w, 'section_destination')}* — {ready + ' · ' + destination_text if destination else needs}",
        f"🕒 *{_t(w, 'section_schedule')}* — {ready if scheduled else needs}",
        f"   {escape_markdown(repeat, version=1)} · {escape_markdown(first_text, version=1)} · {escape_markdown(timezone_name, version=1)}",
        f"⚙️ *{_t(w, 'section_rules')}* — {rules}",
        f"🔘 *{_t(w, 'section_buttons')}* — {len(w.get('inline_buttons') or [])}/10",
        "",
        f"{_t(w, 'required')}: {_t(w, 'section_content')}, {_t(w, 'section_destination')} {'·'} {_t(w, 'section_schedule')}.",
        f"{_t(w, 'optional')}: {_t(w, 'section_rules')} {'·'} {_t(w, 'section_buttons')}.",
    ])
    return "\n".join(lines)


async def _home(context, w: dict, notice: str | None = None) -> int:
    await _show(context, w, _home_text(w, notice), _home_keyboard(w))
    return HOME


def _nav(back: str = CB_HOME, language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ " + tr(language, "back"), callback_data=back),
        InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL),
    ]])


def _destination_keyboard(groups: list[dict], page: int, username: str, language: str = "en") -> InlineKeyboardMarkup:
    w = {"language": language}
    start = page * CHAT_PAGE_SIZE
    selected = groups[start:start + CHAT_PAGE_SIZE]
    icons = {"channel": "📢", "group": "👥", "supergroup": "👥"}
    rows = [[InlineKeyboardButton(
        f"{icons.get(g.get('chat_type'), '💬')} {str(g.get('title') or g['chat_id'])[:42]}",
        style="primary", callback_data=f"{CB_CHAT}{g['chat_id']}",
    )] for g in selected]
    nav = []
    if page:
        nav.append(InlineKeyboardButton("⬅️ " + tr(language, "previous"), callback_data=f"{CB_CHAT_PAGE}{page - 1}"))
    if start + len(selected) < len(groups):
        nav.append(InlineKeyboardButton(tr(language, "next") + " ➡️", callback_data=f"{CB_CHAT_PAGE}{page + 1}"))
    if nav:
        rows.append(nav)
    if not groups:
        rows.insert(0, [InlineKeyboardButton(_flow(w, "add_group"), style="primary", url=f"https://t.me/{username}?startgroup=true")])
    rows.extend([
        [InlineKeyboardButton(tr(language, "manual_chat_id"), style="primary", callback_data=CB_MANUAL)],
        [InlineKeyboardButton(_t(w, "review_open_workspace"), callback_data=CB_HOME)],
        [InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])
    return InlineKeyboardMarkup(rows)


def _schedule_text(w: dict) -> str:
    mode = w.get("recurrence_type", "once")
    language = _lang(w)
    if mode == "interval":
        repeat = _flow(w, "repeat_interval", value=w.get("interval_value", "?"), unit=_flow(w, f"unit_{w.get('interval_unit')}"))
    elif mode == "days_of_week":
        days = ", ".join(_weekdays(w)[d] for d in sorted(w.get("days_of_week") or set()) if d in range(7))
        repeat = _flow(w, "repeat_weekly", days=days)
    else:
        repeat = _flow(w, "repeat_once")
    zone_name = str(w.get("timezone") or "UTC")
    first = w.get("first_run")
    try:
        first_text = first.astimezone(pytz.timezone(zone_name)).strftime("%a, %d %b %Y · %H:%M") if isinstance(first, datetime) else _flow(w, "review_not_set")
    except Exception:
        first_text = _flow(w, "review_not_set")
    limit = _flow(w, "one_send") if mode == "once" else (
        _flow(w, "unlimited") if w.get("max_runs") is None else _flow(w, "sends", value=w.get("max_runs"))
    )
    return (
        f"🕒 *{pct(language, 'schedule_title')}*\n\n"
        f"*{tr(language, 'recurrence_prompt').splitlines()[0].replace('🔁 ', '')}:* {escape_markdown(repeat, version=1)}\n"
        f"*{pct(language, 'first_publish')}:* {escape_markdown(first_text, version=1)}\n"
        f"*{pct(language, 'timezone')}:* {escape_markdown(zone_name, version=1)}\n"
        f"*{pct(language, 'delivery_limit')}:* {limit}\n\n"
        f"{tr(language, 'recurrence_prompt').splitlines()[-1] if len(tr(language, 'recurrence_prompt').splitlines()) > 1 else ''}"
    )


def _schedule_keyboard(w: dict) -> InlineKeyboardMarkup:
    language = _lang(w)
    mode = w.get("recurrence_type", "once")
    rows = [
        [
            InlineKeyboardButton(_t(w, "mode_once"), style="success" if mode == "once" else "primary", callback_data=CB_MODE_ONCE),
            InlineKeyboardButton(_t(w, "mode_interval"), style="success" if mode == "interval" else "primary", callback_data=CB_MODE_INTERVAL),
            InlineKeyboardButton(_t(w, "mode_weekly"), style="success" if mode == "days_of_week" else "primary", callback_data=CB_MODE_WEEKLY),
        ],
        [InlineKeyboardButton(_t(w, "first_publish"), style="primary", callback_data=CB_FIRST_RUN)],
        [InlineKeyboardButton(_t(w, "timezone"), style="primary", callback_data=CB_TIMEZONE)],
    ]
    if mode != "once":
        rows.append([InlineKeyboardButton(_t(w, "delivery_limit"), style="primary", callback_data=CB_RUN_LIMIT)])
    rows.extend([
        [InlineKeyboardButton(_t(w, "done"), style="success", callback_data=CB_HOME)],
        [InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])
    return InlineKeyboardMarkup(rows)


def _timezone_keyboard(page: int, language: str = "en") -> InlineKeyboardMarkup:
    start = page * TZ_PAGE_SIZE
    subset = TIMEZONES[start:start + TZ_PAGE_SIZE]
    rows = [[InlineKeyboardButton(name.replace("_", " "), style="primary", callback_data=f"{CB_TZ}{name}")] for name, _label in subset]
    nav = []
    if page:
        nav.append(InlineKeyboardButton("⬅️ " + tr(language, "previous"), callback_data=f"{CB_TZ_PAGE}{page - 1}"))
    if start + len(subset) < len(TIMEZONES):
        nav.append(InlineKeyboardButton(tr(language, "next") + " ➡️", callback_data=f"{CB_TZ_PAGE}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.extend([
        [InlineKeyboardButton("⬅️ " + tr(language, "back"), callback_data=CB_SCHEDULE)],
        [InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])
    return InlineKeyboardMarkup(rows)


def _days_keyboard(selected: set[int], language: str = "en") -> InlineKeyboardMarkup:
    labels = _weekdays({"language": language})
    rows = [
        [InlineKeyboardButton(f"{'✅' if d in selected else '○'} {labels[d]}", style="success" if d in selected else "primary", callback_data=f"{CB_DAY}{d}") for d in range(4)],
        [InlineKeyboardButton(f"{'✅' if d in selected else '○'} {labels[d]}", style="success" if d in selected else "primary", callback_data=f"{CB_DAY}{d}") for d in range(4, 7)],
        [InlineKeyboardButton(_flow({"language": language}, "wizard_next"), style="success", callback_data=CB_DAYS_DONE)],
        [InlineKeyboardButton("⬅️ " + _t({"language": language}, "schedule_title"), callback_data=CB_SCHEDULE)],
        [InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL)],
    ]
    return InlineKeyboardMarkup(rows)


def _pin_label(w: dict) -> str:
    if not w.get("auto_pin"):
        return _flow(w, "review_off")
    if w.get("ap_hours") is None:
        return _t(w, "keep_pinned")
    duration = w.get("ap_duration_label") or _flow(w, "unpin_after", value=w.get("ap_hours"))
    return _flow(w, "unpin_after", value=duration)


def _options_text(w: dict) -> str:
    window = f"{w.get('tw_start', '?')}–{w.get('tw_end', '?')}" if w.get("time_window_enabled") else _flow(w, "review_off")
    auto_delete = str(w.get("ad_duration_label") or _flow(w, "delete_after", value=w.get("ad_hours"))) if w.get("auto_delete") else _flow(w, "review_off")
    self_destruct = _flow(w, "destruct_after", value=w.get("sd_secs")) if w.get("self_destruct") else _flow(w, "review_off")
    pin = _pin_label(w)
    return (
        f"⚙️ *{_t(w, 'options_title')}*\n\n"
        f"*{_t(w, 'posting_window')}:* {escape_markdown(window, version=1)}\n"
        f"*{_t(w, 'auto_delete')}:* {escape_markdown(auto_delete, version=1)}\n"
        f"*{_t(w, 'self_destruct')}:* {escape_markdown(self_destruct, version=1)}\n"
        f"*{_t(w, 'auto_pin')}:* {escape_markdown(pin, version=1)}\n\n"
        f"{_t(w, 'options_intro')}"
    )


def _options_keyboard(w: dict) -> InlineKeyboardMarkup:
    def label(active: bool, title: str) -> str:
        return f"{'✅' if active else '○'} {title}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label(bool(w.get("time_window_enabled")), _t(w, "posting_window")), style="success" if w.get("time_window_enabled") else "primary", callback_data=CB_WINDOW)],
        [InlineKeyboardButton(label(bool(w.get("auto_delete")), _t(w, "auto_delete")), style="success" if w.get("auto_delete") else "primary", callback_data=CB_DELETE)],
        [InlineKeyboardButton(label(bool(w.get("self_destruct")), _t(w, "self_destruct")), style="success" if w.get("self_destruct") else "primary", callback_data=CB_DESTRUCT)],
        [InlineKeyboardButton(label(bool(w.get("auto_pin")), _t(w, "auto_pin")), style="success" if w.get("auto_pin") else "primary", callback_data=CB_PIN)],
        [InlineKeyboardButton(_t(w, "done"), style="success", callback_data=CB_HOME)],
        [InlineKeyboardButton(tr(_lang(w), "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])


def _buttons_text(w: dict) -> str:
    buttons = w.get("inline_buttons") or []
    language = _lang(w)
    lines = [f"🔘 *{_t(w, 'buttons_title')}*", _t(w, "buttons_count", count=len(buttons)), ""]
    if buttons:
        for i, row in enumerate(buttons, 1):
            for item in row:
                lines.append(
                    f"{i}. {escape_markdown(str(item.get('text') or ''), version=1)} → "
                    f"{escape_markdown(str(item.get('url') or ''), version=1)}"
                )
    else:
        lines.append(_t(w, "buttons_empty"))
    lines.extend(["", _t(w, "buttons_instructions")])
    return "\n".join(lines)


def _buttons_keyboard(count: int, language: str = "en") -> InlineKeyboardMarkup:
    w = {"language": language}
    rows = []
    if count < 10:
        rows.append([InlineKeyboardButton(_t(w, "add_buttons"), style="primary", callback_data=CB_BUTTON_ADD)])
    if count:
        rows.append([
            InlineKeyboardButton(_t(w, "remove_last"), style="primary", callback_data=CB_BUTTON_REMOVE),
            InlineKeyboardButton(_t(w, "clear_buttons"), style="danger", callback_data=CB_BUTTON_CLEAR),
        ])
    rows.extend([
        [InlineKeyboardButton(_t(w, "done"), style="success", callback_data=CB_HOME)],
        [InlineKeyboardButton(tr(language, "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])
    return InlineKeyboardMarkup(rows)


def _preview_text(w: dict) -> str:
    mode = w.get("recurrence_type", "once")
    if mode == "interval":
        repeat = _flow(w, "repeat_interval", value=w.get("interval_value"), unit=_flow(w, f"unit_{w.get('interval_unit')}"))
    elif mode == "days_of_week":
        days = ", ".join(_weekdays(w)[d] for d in sorted(w.get("days_of_week") or set()) if d in range(7))
        repeat = _flow(w, "repeat_weekly", days=days)
    else:
        repeat = _flow(w, "repeat_once")
    zone_name = str(w.get("timezone") or "UTC")
    first = w.get("first_run")
    first_text = first.astimezone(pytz.timezone(zone_name)).strftime("%A, %d %b %Y at %H:%M") if isinstance(first, datetime) else _flow(w, "review_not_set")
    limit = _flow(w, "one_send") if mode == "once" else (
        _flow(w, "unlimited") if w.get("max_runs") is None else _flow(w, "sends", value=w.get("max_runs"))
    )
    raw = str(w.get("content_text") or "").strip()
    preview = escape_markdown(" ".join(raw.split())[:180], version=1)
    if w.get("media_type"):
        media_label = _flow(w, "review_media")
        preview = f"[{media_label}] {preview}".strip()
    if raw and len(raw) > 180:
        preview += "…"
    if not preview:
        preview = _flow(w, "review_media") if w.get("media_file_id") else _flow(w, "review_not_set")
    destination = escape_markdown(str(w.get("chat_title") or _t(w, "not_selected")), version=1)
    chat_id = escape_markdown(str(w.get("chat_id") or "—"), version=1)
    window = f"{w.get('tw_start')}–{w.get('tw_end')}" if w.get("time_window_enabled") else _flow(w, "review_off")
    delete = str(w.get("ad_duration_label") or _flow(w, "delete_after", value=w.get("ad_hours"))) if w.get("auto_delete") else _flow(w, "review_off")
    destruct = _flow(w, "destruct_after", value=w.get("sd_secs")) if w.get("self_destruct") else _flow(w, "review_off")
    pin = _pin_label(w)
    button_lines = [
        f"• {escape_markdown(str(item.get('text') or ''), version=1)} → {escape_markdown(str(item.get('url') or ''), version=1)}"
        for row in w.get("inline_buttons") or [] for item in row
    ]
    values = {
        "destination": destination, "chat_id": chat_id, "content": preview,
        "repeat": escape_markdown(repeat, version=1), "first_post": escape_markdown(first_text, version=1),
        "timezone": escape_markdown(zone_name, version=1), "limit": limit,
        "window": escape_markdown(window, version=1), "auto_delete": escape_markdown(delete, version=1),
        "self_destruct": escape_markdown(destruct, version=1), "auto_pin": escape_markdown(pin, version=1),
        "buttons": "\n".join(button_lines) if button_lines else _flow(w, "review_not_set"),
    }
    return _flow(w, "review_template", **values)


def _validate(w: dict) -> list[str]:
    language = _lang(w)
    errors = []
    chat_id = w.get("chat_id")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id >= 0:
        errors.append(tr(language, "choose_group"))
    if not (str(w.get("content_text") or "").strip() or w.get("media_file_id")):
        errors.append(tr(language, "schedule_content_prompt"))
    if w.get("media_type") and not w.get("media_file_id"):
        errors.append(_flow(w, "content_rules"))
    if w.get("media_file_id") and w.get("media_type") not in {"photo", "video", "document", "audio", "animation", "voice"}:
        errors.append(_t(w, "content_unsupported"))
    try:
        pytz.timezone(w.get("timezone") or "UTC")
    except (pytz.exceptions.UnknownTimeZoneError, TypeError):
        errors.append(_flow(w, "timezone_invalid"))
    first = w.get("first_run")
    if not isinstance(first, datetime) or first.tzinfo is None or first.utcoffset() is None or first.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        errors.append(_flow(w, "invalid_first_time"))
    mode = w.get("recurrence_type")
    if mode not in {"once", "interval", "days_of_week"}:
        errors.append(tr(language, "recurrence_prompt"))
    elif mode == "interval":
        value, unit = w.get("interval_value"), w.get("interval_unit")
        bounds = {"minutes": 525600, "hours": 8760, "days": 365}
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or unit not in bounds or value > bounds.get(unit, 0):
            errors.append(_flow(w, "interval_invalid"))
    elif mode == "days_of_week":
        days = w.get("days_of_week")
        if not isinstance(days, (set, list, tuple)) or not days or any(not isinstance(d, int) or isinstance(d, bool) or d not in range(7) for d in days):
            errors.append(_flow(w, "day_required"))
    if mode == "once":
        w["max_runs"] = 1
    else:
        limit = w.get("max_runs")
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100000):
            errors.append(_flow(w, "count_invalid"))
    if w.get("time_window_enabled"):
        start, end = parse_hhmm(w.get("tw_start") or ""), parse_hhmm(w.get("tw_end") or "")
        if not start or not end or start == end:
            errors.append(_flow(w, "window_invalid"))
    if w.get("auto_delete") and w.get("self_destruct"):
        errors.append(_flow(w, "validation_generic"))
    if w.get("auto_delete"):
        value = w.get("ad_hours")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            errors.append(_flow(w, "delete_invalid"))
    if w.get("self_destruct"):
        value = w.get("sd_secs")
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 31536000:
            errors.append(_flow(w, "destruct_invalid"))
    if w.get("auto_pin"):
        value = w.get("ap_hours", "missing")
        if value == "missing" or (value is not None and (
            not isinstance(value, (int, float)) or isinstance(value, bool) or
            not math.isfinite(value) or not 0 < value <= 87600
        )):
            errors.append(_flow(w, "pin_invalid"))
    buttons = w.get("inline_buttons") or []
    if len(buttons) > 10:
        errors.append(_flow(w, "button_limit"))
    for row in buttons:
        if not isinstance(row, list):
            errors.append(_flow(w, "button_label_invalid"))
            break
        for item in row:
            if not isinstance(item, dict):
                errors.append(_flow(w, "button_label_invalid"))
                break
            label, url = str(item.get("text") or "").strip(), str(item.get("url") or "").strip()
            if not label or len(label) > 64 or not url.startswith(("http://", "https://", "tg://")) or " " in url:
                errors.append(_flow(w, "button_link_invalid"))
                break
    return errors


def _validate(w: dict) -> list[str]:
    errors = []
    chat_id = w.get("chat_id")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id >= 0:
        errors.append("Choose a destination.")
    if not (str(w.get("content_text") or "").strip() or w.get("media_file_id")):
        errors.append("Add post text or media.")
    if w.get("media_type") and not w.get("media_file_id"):
        errors.append("The selected media is incomplete.")
    if w.get("media_file_id") and w.get("media_type") not in {"photo", "video", "document", "audio", "animation", "voice"}:
        errors.append("The selected media type is not supported.")
    try:
        pytz.timezone(w.get("timezone") or "UTC")
    except (pytz.exceptions.UnknownTimeZoneError, TypeError):
        errors.append("Choose a valid timezone.")
    first = w.get("first_run")
    if not isinstance(first, datetime) or first.tzinfo is None or first.utcoffset() is None or first.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        errors.append("Set a future publish time.")
    mode = w.get("recurrence_type")
    if mode not in {"once", "interval", "days_of_week"}:
        errors.append("Choose a valid repeat pattern.")
    elif mode == "interval":
        value, unit = w.get("interval_value"), w.get("interval_unit")
        bounds = {"minutes": 525600, "hours": 8760, "days": 365}
        if not isinstance(value, int) or isinstance(value, bool) or value < 1 or unit not in bounds or value > bounds.get(unit, 0):
            errors.append("Complete the repeat interval.")
    elif mode == "days_of_week":
        days = w.get("days_of_week")
        if not isinstance(days, (set, list, tuple)) or not days or any(not isinstance(d, int) or isinstance(d, bool) or d not in range(7) for d in days):
            errors.append("Choose at least one weekday.")
    if mode == "once":
        w["max_runs"] = 1
    else:
        limit = w.get("max_runs")
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100000):
            errors.append("Set a delivery limit from 1 to 100,000 or choose Unlimited.")
    if w.get("time_window_enabled"):
        start, end = parse_hhmm(w.get("tw_start") or ""), parse_hhmm(w.get("tw_end") or "")
        if not start or not end or start == end:
            errors.append("Complete the posting window.")
    if w.get("auto_delete") and w.get("self_destruct"):
        errors.append("Choose either Auto-Delete or Self-Destruct, not both.")
    if w.get("auto_delete"):
        value = w.get("ad_hours")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            errors.append("Set a valid Auto-Delete duration.")
    if w.get("self_destruct"):
        value = w.get("sd_secs")
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 31536000:
            errors.append("Set a valid Self-Destruct duration.")
    if w.get("auto_pin"):
        value = w.get("ap_hours", "missing")
        if value == "missing" or (value is not None and (
            not isinstance(value, (int, float)) or isinstance(value, bool) or
            not math.isfinite(value) or not 0 < value <= 87600
        )):
            errors.append("Choose an Auto-Pin duration or Keep pinned.")
    buttons = w.get("inline_buttons") or []
    if len(buttons) > 10:
        errors.append("A post can have no more than 10 buttons.")
    for row in buttons:
        if not isinstance(row, list):
            errors.append("A post-button row is invalid.")
            break
        for item in row:
            if not isinstance(item, dict):
                errors.append("A post button is invalid.")
                break
            label, url = str(item.get("text") or "").strip(), str(item.get("url") or "").strip()
            if not label or len(label) > 64 or not url.startswith(("http://", "https://", "tg://")) or " " in url:
                errors.append("A post button has an invalid label or link.")
                break
    return errors


def _normalize_url(raw: str) -> str | None:
    value = raw.strip()
    if value.startswith("@"):
        username = value[1:]
        return "https://t.me/" + username if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) else None
    if not value.startswith(("http://", "https://", "tg://")) and "." in value and " " not in value:
        value = "https://" + value
    return value if value.startswith(("http://", "https://", "tg://")) and " " not in value else None


def _parse_button_batch(raw: str, existing: int) -> list[list[dict[str, str]]] | None:
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    if not lines or existing + len(lines) > 10:
        return None
    rows = []
    for line in lines:
        if "|" not in line:
            return None
        label, _, raw_url = line.partition("|")
        label, url = label.strip(), _normalize_url(raw_url)
        if not label or len(label) > 64 or not url:
            return None
        rows.append([{"text": label, "url": url}])
    return rows


async def enter_schedule_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    context.user_data.pop("wizard", None)
    context.user_data.pop("schedule_post", None)
    chat_id = query.message.chat_id
    chat_cleanup.reset(context.application.bot_data, chat_id)
    chat_cleanup.track(context.application.bot_data, chat_id, query.message.message_id)
    w = {
        "language": language, "ui_chat_id": chat_id,
        "ui_message_id": query.message.message_id, "timezone": "UTC",
        "recurrence_type": "once", "max_runs": 1, "days_of_week": set(),
        "inline_buttons": [], "time_window_enabled": False,
        "auto_delete": False, "self_destruct": False, "auto_pin": False,
    }
    context.user_data["schedule_post"] = w
    linked = context.user_data.pop("launch_chat", None)
    notice = None
    if isinstance(linked, dict):
        try:
            target_id = int(linked.get("chat_id"))
            target = await ensure_chat_access(context.bot, target_id, user_id)
            w["chat_id"], w["chat_title"] = target_id, target.title or str(target_id)
        except (TypeError, ValueError, ChatAccessError):
            notice = "The linked group could not be verified. Choose a destination."
    await _answer(query)
    return await _home(context, w, notice)


async def cb_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _answer(query)
    w = context.user_data.get("schedule_post")
    if not isinstance(w, dict):
        await _answer(query, _flow(_w(context), "setup_inactive"), alert=True)
        return ConversationHandler.END
    return await _home(context, w)


async def cb_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    text = f"📝 *{_t(w, 'content_title')}*\n\n"
    if w.get("content_text") or w.get("media_file_id"):
        text += f"{_t(w, 'content_existing')}\n\n"
    text += f"{_t(w, 'content_prompt')}\n\n{_t(w, 'content_supported')}"
    await _answer(query)
    await _show(context, w, text, _nav(language=_lang(w)))
    return CONTENT

async def recv_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg: Message = update.message
    w = _w(context)
    file_id = media_type = content_text = None
    if msg.photo:
        file_id, media_type, content_text = msg.photo[-1].file_id, "photo", msg.caption
    elif msg.video:
        file_id, media_type, content_text = msg.video.file_id, "video", msg.caption
    elif msg.document:
        file_id, media_type, content_text = msg.document.file_id, "document", msg.caption
    elif msg.audio:
        file_id, media_type, content_text = msg.audio.file_id, "audio", msg.caption
    elif msg.animation:
        file_id, media_type, content_text = msg.animation.file_id, "animation", msg.caption
    elif msg.voice:
        file_id, media_type, content_text = msg.voice.file_id, "voice", msg.caption
    elif msg.text and msg.text.strip():
        content_text = msg.text
    else:
        await msg.reply_text(_t(w, "content_unsupported"))
        return CONTENT
    w["content_text"], w["media_file_id"], w["media_type"] = content_text, file_id, media_type
    return await _home(context, w, _t(w, "content_updated"))

async def cb_destination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    groups = await list_groups_for_user(query.from_user.id if query.from_user else None)
    username = (await query.get_bot().get_me()).username
    await _answer(query)
    await _show(context, w, f"📍 *{_t(w, 'destination_title')}*\n\n{_t(w, 'destination_prompt')}", _destination_keyboard(groups, 0, username, _lang(w)))
    return DESTINATION

async def cb_destination_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    raw = (query.data or "").replace(CB_CHAT_PAGE, "", 1)
    page = int(raw) if raw.isdigit() else 0
    groups = await list_groups_for_user(query.from_user.id if query.from_user else None)
    username = (await query.get_bot().get_me()).username
    await _answer(query)
    await _show(context, w, f"📍 *{_t(w, 'destination_title')}*", _destination_keyboard(groups, page, username, _lang(w)))
    return DESTINATION

async def cb_choose_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    try:
        chat_id = int((query.data or "").replace(CB_CHAT, "", 1))
    except ValueError:
        await _answer(query, _flow(w, "invalid_chat_id"), alert=True)
        return DESTINATION
    user_id = query.from_user.id if query.from_user else None
    try:
        target = await ensure_chat_access(query.get_bot(), chat_id, user_id)
    except ChatAccessError:
        await _answer(query, _flow(w, "access_failed"), alert=True)
        return DESTINATION
    w["chat_id"], w["chat_title"] = chat_id, target.title or str(chat_id)
    await _answer(query, _t(w, "destination_verified"))
    return await _home(context, w, _t(w, "destination_updated"))

async def cb_manual_destination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    await _answer(query)
    await _show(context, w, f"✏️ *{_t(w, 'enter_chat_id')}*\n\n{_t(w, 'enter_chat_id_prompt')}", _nav(CB_DESTINATION, _lang(w)))
    return MANUAL_DESTINATION

async def recv_manual_destination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    w = _w(context)
    try:
        chat_id = int((msg.text or "").strip())
    except ValueError:
        await msg.reply_text(_flow(w, "invalid_chat_id"))
        return MANUAL_DESTINATION
    if chat_id >= 0:
        await msg.reply_text(_flow(w, "negative_chat_id"))
        return MANUAL_DESTINATION
    try:
        target = await ensure_chat_access(context.bot, chat_id, update.effective_user.id)
    except ChatAccessError:
        await msg.reply_text(_flow(w, "access_failed"))
        return MANUAL_DESTINATION
    w["chat_id"], w["chat_title"] = chat_id, target.title or str(chat_id)
    return await _home(context, w, _t(w, "destination_updated"))

async def cb_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    await _answer(query)
    await _show(context, w, _schedule_text(w), _schedule_keyboard(w))
    return SCHEDULE


async def cb_mode_once(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    w["recurrence_type"], w["max_runs"] = "once", 1
    for key in ("interval_value", "interval_unit", "days_of_week"):
        w.pop(key, None)
    await _answer(query, _flow(w, "repeat_once"))
    await _show(context, w, _schedule_text(w), _schedule_keyboard(w))
    return SCHEDULE

async def cb_mode_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    w["recurrence_type"] = "interval"
    if w.get("max_runs") == 1:
        w["max_runs"] = None
    w.pop("days_of_week", None)
    w.pop("interval_value", None)
    w.pop("interval_unit", None)
    await _answer(query)
    await _show(context, w, _flow(w, "interval_value_prompt"), _nav(CB_SCHEDULE, _lang(w)))
    return INTERVAL_VALUE

async def recv_interval_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    w = _w(context)
    if not raw.isdecimal() or not 1 <= int(raw) <= 525600:
        await update.message.reply_text(_flow(w, "interval_invalid"))
        return INTERVAL_VALUE
    w["interval_value"] = int(raw)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_flow(w, "unit_minutes"), style="primary", callback_data=CB_UNIT + "minutes"),
            InlineKeyboardButton(_flow(w, "unit_hours"), style="primary", callback_data=CB_UNIT + "hours"),
            InlineKeyboardButton(_flow(w, "unit_days"), style="primary", callback_data=CB_UNIT + "days"),
        ],
        [InlineKeyboardButton("⬅️ " + tr(_lang(w), "back"), callback_data=CB_SCHEDULE)],
        [InlineKeyboardButton("❌ " + tr(_lang(w), "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])
    await _show(context, w, f"{_flow(w, 'interval_unit_prompt')}\n\n{_flow(w, 'interval_confirm', value=raw, unit='…')}", keyboard)
    return INTERVAL_UNIT

async def cb_interval_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    unit = (query.data or "").replace(CB_UNIT, "", 1)
    value = w.get("interval_value")
    limits = {"minutes": 525600, "hours": 8760, "days": 365}
    if unit not in limits or not isinstance(value, int) or not 1 <= value <= limits[unit]:
        await _answer(query, _flow(w, "unit_invalid"), alert=True)
        return INTERVAL_UNIT
    w["interval_unit"] = unit
    await _answer(query, _flow(w, "interval_confirm", value=value, unit=_flow(w, f"unit_{unit}")))
    await _show(context, w, _schedule_text(w), _schedule_keyboard(w))
    return SCHEDULE

async def cb_mode_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    w["recurrence_type"] = "days_of_week"
    if w.get("max_runs") == 1:
        w["max_runs"] = None
    w.pop("interval_value", None)
    w.pop("interval_unit", None)
    selected = w.setdefault("days_of_week", set())
    await _answer(query)
    await _show(context, w, _flow(w, "days_prompt"), _days_keyboard(selected, _lang(w)))
    return WEEKDAYS

async def cb_day_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    raw = (query.data or "").replace(CB_DAY, "", 1)
    if not raw.isdigit() or int(raw) not in range(7):
        await _answer(query, _flow(w, "day_required"), alert=True)
        return WEEKDAYS
    day = int(raw)
    selected = w.setdefault("days_of_week", set())
    selected.symmetric_difference_update({day})
    await _answer(query, _flow(w, "done"))
    await _show(context, w, _flow(w, "days_prompt"), _days_keyboard(selected, _lang(w)))
    return WEEKDAYS

async def cb_days_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    if not w.get("days_of_week"):
        await _answer(query, _flow(w, "day_required"), alert=True)
        return WEEKDAYS
    await _answer(query, _flow(w, "done"))
    await _show(context, w, _schedule_text(w), _schedule_keyboard(w))
    return SCHEDULE

async def cb_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    await _answer(query)
    await _show(context, w, tr(_lang(w), "timezone_prompt"), _timezone_keyboard(0, _lang(w)))
    return TIMEZONE

async def cb_timezone_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    raw = (query.data or "").replace(CB_TZ_PAGE, "", 1)
    page = int(raw) if raw.isdigit() else 0
    await _answer(query)
    await _show(context, w, tr(_lang(w), "timezone_prompt"), _timezone_keyboard(page, _lang(w)))
    return TIMEZONE

async def cb_timezone_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    w = _w(context)
    zone = (query.data or "").replace(CB_TZ, "", 1)
    try:
        pytz.timezone(zone)
    except pytz.exceptions.UnknownTimeZoneError:
        await _answer(query, _flow(w, "timezone_invalid"), alert=True)
        return TIMEZONE
    w["timezone"] = zone
    await _answer(query, f"{_t(w, 'timezone')}: {zone}")
    await _show(context, w, _schedule_text(w), _schedule_keyboard(w))
    return SCHEDULE

async def cb_first_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    zone = w.get("timezone", "UTC")
    await _answer(query)
    await _show(context, w, f"{tr(_lang(w), 'first_run_prompt')}\n\n🌍 {escape_markdown(zone, version=1)}", _nav(CB_SCHEDULE, _lang(w)))
    return FIRST_RUN

async def recv_first_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    w = _w(context)
    raw = (update.message.text or "").strip()
    parsed = parse_schedule_datetime(raw, w.get("timezone", "UTC"))
    if parsed is None:
        await update.message.reply_text(_flow(w, "invalid_first_time"))
        return FIRST_RUN
    w["first_run"], w["first_run_raw"] = parsed, raw
    return await _home(context, w, _t(w, "time_updated"))

async def cb_run_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    await _answer(query)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_flow(w, "unlimited"), style="success" if w.get("max_runs") is None else "primary", callback_data=CB_LIMIT_UNLIMITED),
            InlineKeyboardButton(_flow(w, "sends", value=10), style="success" if w.get("max_runs") == 10 else "primary", callback_data=CB_LIMIT + "10"),
            InlineKeyboardButton(_flow(w, "sends", value=30), style="success" if w.get("max_runs") == 30 else "primary", callback_data=CB_LIMIT + "30"),
        ],
        [InlineKeyboardButton(_t(w, "custom_amount"), style="primary", callback_data=CB_LIMIT + "custom")],
        [InlineKeyboardButton("⬅️ " + tr(_lang(w), "back"), callback_data=CB_SCHEDULE)],
        [InlineKeyboardButton("❌ " + tr(_lang(w), "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])
    await _show(context, w, tr(_lang(w), "max_runs_prompt"), keyboard)
    return RUN_LIMIT

async def cb_limit_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    value = (query.data or "").replace(CB_LIMIT, "", 1)
    if value == "custom":
        await _answer(query)
        await _show(context, w, _flow(w, "count_invalid"), _nav(CB_SCHEDULE, _lang(w)))
        return RUN_LIMIT
    if not value.isdecimal() or not 1 <= int(value) <= 100000:
        await _answer(query, _flow(w, "count_invalid"), alert=True)
        return RUN_LIMIT
    w["max_runs"] = int(value)
    await _answer(query, _flow(w, "delivery_confirm", value=value))
    await _show(context, w, _schedule_text(w), _schedule_keyboard(w))
    return SCHEDULE

async def cb_limit_unlimited(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    w["max_runs"] = None
    await _answer(query, _flow(w, "unlimited"))
    await _show(context, w, _schedule_text(w), _schedule_keyboard(w))
    return SCHEDULE

async def recv_run_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    w = _w(context)
    raw = (update.message.text or "").strip()
    if not raw.isdecimal() or not 1 <= int(raw) <= 100000:
        await update.message.reply_text(_flow(w, "count_invalid"))
        return RUN_LIMIT
    w["max_runs"] = int(raw)
    return await _home(context, w, _flow(w, "delivery_confirm", value=w.get("max_runs")))

async def cb_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    await _answer(query)
    await _show(context, w, _options_text(w), _options_keyboard(w))
    return OPTIONS


async def cb_window(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    if w.get("time_window_enabled"):
        w["time_window_enabled"] = False
        w.pop("tw_start", None)
        w.pop("tw_end", None)
        await _answer(query, _flow(w, "window_off"))
        await _show(context, w, _options_text(w), _options_keyboard(w))
        return OPTIONS
    await _answer(query)
    await _show(context, w, f"{_t(w, 'window_title')}\n\n{_t(w, 'window_prompt')}", _nav(CB_OPTIONS, _lang(w)))
    return POSTING_WINDOW

async def recv_posting_window(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    match = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*", raw)
    start = parse_hhmm(match.group(1)) if match else None
    end = parse_hhmm(match.group(2)) if match else None
    if not start or not end or start == end:
        await update.message.reply_text(_flow(_w(context), "window_invalid"))
        return POSTING_WINDOW
    w = _w(context)
    w.update(time_window_enabled=True, tw_start=start, tw_end=end)
    return await _home(context, w, _t(w, "window_updated"))

async def cb_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    if w.get("auto_delete"):
        w["auto_delete"] = False
        w.pop("ad_hours", None)
        w.pop("ad_duration_label", None)
        await _answer(query, _flow(w, "review_off"))
        await _show(context, w, _options_text(w), _options_keyboard(w))
        return OPTIONS
    w["self_destruct"] = False
    w.pop("sd_secs", None)
    await _answer(query)
    await _show(context, w, _flow(w, "delete_prompt"), _nav(CB_OPTIONS, _lang(w)))
    return AUTO_DELETE

async def recv_auto_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    match = re.fullmatch(r"\s*(\d+)\s*(minutes?|mins?|m|hours?|hrs?|h)\s*", raw, re.I)
    if not match:
        await update.message.reply_text(_flow(_w(context), "delete_invalid"))
        return AUTO_DELETE
    amount, unit = int(match.group(1)), match.group(2).lower()
    minutes = amount if unit in {"m", "min", "mins", "minute", "minutes"} else amount * 60
    if not 5 <= minutes <= 525600:
        await update.message.reply_text(_flow(_w(context), "delete_invalid"))
        return AUTO_DELETE
    w = _w(context)
    w.update(auto_delete=True, self_destruct=False, ad_hours=minutes / 60)
    w.pop("sd_secs", None)
    w["ad_duration_label"] = _flow(w, "delete_after", value=minutes / 60)
    return await _home(context, w, _flow(w, "delete_saved", value=w.get("ad_duration_label")))


async def cb_destruct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    if w.get("self_destruct"):
        w["self_destruct"] = False
        w.pop("sd_secs", None)
        await _answer(query, _flow(w, "review_off"))
        await _show(context, w, _options_text(w), _options_keyboard(w))
        return OPTIONS
    w["auto_delete"] = False
    w.pop("ad_hours", None)
    w.pop("ad_duration_label", None)
    await _answer(query)
    await _show(context, w, _flow(w, "destruct_prompt"), _nav(CB_OPTIONS, _lang(w)))
    return SELF_DESTRUCT

async def recv_self_destruct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    if not raw.isdecimal() or not 1 <= int(raw) <= 31536000:
        await update.message.reply_text(_flow(_w(context), "destruct_invalid"))
        return SELF_DESTRUCT
    w = _w(context)
    w.update(self_destruct=True, auto_delete=False, sd_secs=int(raw))
    w.pop("ad_hours", None)
    w.pop("ad_duration_label", None)
    return await _home(context, w, _flow(w, "destruct_saved", value=w.get("sd_secs")))


async def cb_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    if w.get("auto_pin"):
        w["auto_pin"] = False
        w.pop("ap_hours", None)
        w.pop("ap_duration_label", None)
        await _answer(query, _flow(w, "review_off"))
        await _show(context, w, _options_text(w), _options_keyboard(w))
        return OPTIONS
    await _answer(query)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(_t(w, "keep_pinned"), style="success", callback_data=CB_PIN_FOREVER)],
        [InlineKeyboardButton(_t(w, "first_publish"), style="primary", callback_data=CB_PIN_DURATION)],
        [InlineKeyboardButton("⬅️ " + _t(w, "options_title"), callback_data=CB_OPTIONS)],
        [InlineKeyboardButton("❌ " + tr(_lang(w), "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])
    await _show(context, w, _flow(w, "pin_prompt"), keyboard)
    return AUTO_PIN

async def cb_pin_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    await _answer(query)
    await _show(context, w, _flow(w, "pin_prompt"), _nav(CB_OPTIONS, _lang(w)))
    return AUTO_PIN

def _duration_to_hours(raw: str, *, max_minutes: int) -> float | None:
    match = re.fullmatch(r"\s*(\d+)\s*(minutes?|mins?|m|hours?|hrs?|h)\s*", raw or "", re.I)
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2).lower()
    minutes = amount if unit in {"m", "min", "mins", "minute", "minutes"} else amount * 60
    if not 5 <= minutes <= max_minutes:
        return None
    return minutes / 60


def _duration_label(hours: float) -> str:
    minutes = round(hours * 60)
    if minutes < 60:
        return f"{minutes} minutes"
    if minutes % 60 == 0:
        count = minutes // 60
        return f"{count} hour" if count == 1 else f"{count} hours"
    return f"{minutes} minutes"


async def recv_auto_pin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    value = _duration_to_hours(raw, max_minutes=87600 * 60)
    if value is None:
        await update.message.reply_text(
            "Enter a duration such as 5 minutes, 30m, 2 hours or 12h. "
            "Minimum: 5 minutes; maximum: 87,600 hours."
        )
        return AUTO_PIN
    w = _w(context)
    w.update(auto_pin=True, ap_hours=value, ap_duration_label=_duration_label(value))
    return await _home(context, w, _t(w, "pin_updated"))


async def cb_pin_forever(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    w.update(auto_pin=True, ap_hours=None, ap_duration_label=_t(w, "keep_pinned"))
    await _answer(query, _t(w, "keep_pinned"))
    return await _home(context, w, _t(w, "pin_updated"))


async def cb_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    await _answer(query)
    await _show(context, w, _buttons_text(w), _buttons_keyboard(len(w.get("inline_buttons") or []), _lang(w)))
    return BUTTONS


async def cb_button_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    await _answer(query)
    await _show(context, w, f"🔘 *{_t(w, 'buttons_title')}*\n\n{_t(w, 'buttons_instructions')}", _nav(CB_BUTTONS, _lang(w)))
    return BUTTON_ENTRY

async def recv_button_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    w = _w(context)
    buttons = w.setdefault("inline_buttons", [])
    parsed = _parse_button_batch(update.message.text or "", len(buttons))
    if parsed is None:
        await update.message.reply_text(_flow(w, "button_link_invalid"))
        return BUTTON_ENTRY
    buttons.extend(parsed)
    return await _home(context, w, _flow(w, "button_added", count=len(w.get("inline_buttons") or [])))


async def cb_button_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    buttons = w.get("inline_buttons") or []
    if buttons:
        buttons.pop()
    w["inline_buttons"] = buttons
    await _answer(query, _t(w, "buttons_removed"))
    await _show(context, w, _buttons_text(w), _buttons_keyboard(len(buttons), _lang(w)))
    return BUTTONS


async def cb_button_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    w["inline_buttons"] = []
    await _answer(query, _t(w, "buttons_cleared"))
    await _show(context, w, _buttons_text(w), _buttons_keyboard(0, _lang(w)))
    return BUTTONS


async def cb_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query, w = update.callback_query, _w(context)
    errors = _validate(w)
    if errors:
        await _answer(query, _flow(w, "validation_generic"), alert=True)
        detail = "\n".join("• " + escape_markdown(error, version=1) for error in errors)
        await _show(context, w, f"⚠️ *{_flow(w, 'validation_generic')}*\n\n{detail}\n\n{_t(w, 'review_open_workspace')}", InlineKeyboardMarkup([
            [InlineKeyboardButton(_t(w, "review_open_workspace"), style="primary", callback_data=CB_HOME)],
            [InlineKeyboardButton("❌ " + tr(_lang(w), "cancel"), style="danger", callback_data=CB_CANCEL)],
        ]))
        return HOME
    await _answer(query)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(_t(w, "review_confirm"), style="success", callback_data=CB_CONFIRM)],
        [InlineKeyboardButton("✏️ " + _t(w, "section_content"), callback_data=CB_CONTENT), InlineKeyboardButton("📍 " + _t(w, "section_destination"), callback_data=CB_DESTINATION)],
        [InlineKeyboardButton("🕒 " + _t(w, "section_schedule"), callback_data=CB_SCHEDULE), InlineKeyboardButton("⚙️ " + _t(w, "section_rules"), callback_data=CB_OPTIONS)],
        [InlineKeyboardButton("🔘 " + _t(w, "section_buttons"), callback_data=CB_BUTTONS)],
        [InlineKeyboardButton("⬅️ " + _t(w, "review_open_workspace"), callback_data=CB_HOME)],
        [InlineKeyboardButton("❌ " + tr(_lang(w), "cancel"), style="danger", callback_data=CB_CANCEL)],
    ])
    await _show(context, w, _preview_text(w), keyboard)
    return REVIEW

async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    message_id = getattr(getattr(query, "message", None), "message_id", None)
    completed = context.user_data.get("_completed_post_composers", {})
    message_key = str(message_id) if message_id is not None else None
    if message_key and isinstance(completed, dict) and message_key in completed:
        await _answer(query, _flow(_w(context), "already_saved"), alert=True)
        return ConversationHandler.END
    w = context.user_data.get("schedule_post")
    if not isinstance(w, dict):
        await _answer(query, _flow(_w(context), "setup_inactive"), alert=True)
        return ConversationHandler.END
    if w.get("_save_uncertain"):
        await _answer(query, _flow(_w(context), "save_failed"), alert=True)
        return REVIEW
    if w.get("_saving"):
        await _answer(query, _flow(_w(context), "already_saving"))
        return REVIEW
    errors = _validate(w)
    if errors:
        await _answer(query, _flow(_w(context), "validation_generic"), alert=True)
        return await _home(context, w, "; ".join(errors))
    user_id = query.from_user.id if query.from_user else None
    if user_id is None:
        await _answer(query, _flow(_w(context), "user_missing"), alert=True)
        return REVIEW

    # Set the lock before any awaited permission or database operation.
    w["_saving"] = True
    await _answer(query, _flow(_w(context), "saving"))
    try:
        await ensure_chat_access(context.bot, w["chat_id"], user_id)
    except ChatAccessError as exc:
        w.pop("_saving", None)
        await _show(context, w, f"⚠️ {escape_markdown(str(exc), version=1)}\n\nChoose another destination or restore the required permissions.", InlineKeyboardMarkup([
            [InlineKeyboardButton(_t(w, "section_destination"), style="primary", callback_data=CB_DESTINATION)],
            [InlineKeyboardButton("⬅️ " + _t(w, "review_open_workspace"), callback_data=CB_HOME)],
            [InlineKeyboardButton("❌ " + tr(_lang(w), "cancel"), style="danger", callback_data=CB_CANCEL)],
        ]))
        return DESTINATION
    except Exception:
        w.pop("_saving", None)
        logger.exception("Destination revalidation failed for user_id=%s", user_id)
        await _show(context, w, f"⚠️ {_flow(w, 'access_failed')}", _nav(language=_lang(w)))
        return HOME

    try:
        document = build_scheduled_post(
            user_id=user_id, chat_id=w["chat_id"],
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
            time_window_start=w.get("tw_start"), time_window_end=w.get("tw_end"),
            auto_delete_enabled=w.get("auto_delete", False),
            auto_delete_after_hours=w.get("ad_hours"),
            self_destruct_enabled=w.get("self_destruct", False),
            self_destruct_after_seconds=w.get("sd_secs"),
            auto_pin_enabled=w.get("auto_pin", False),
            auto_pin_unpin_after_hours=w.get("ap_hours"),
            inline_keyboard=w.get("inline_buttons") or None,
        )
        post_id = await insert_post(document)
    except Exception:
        # The database may have committed before the connection failed.
        # Never expose a blind retry that could create a duplicate record.
        w["_save_uncertain"] = True
        logger.exception("Could not verify scheduled-post save for user_id=%s", user_id)
        await _show(context, w, "⚠️ *The save result could not be verified.*\n\nTo avoid a duplicate, this setup is locked from retrying. Open *My Posts* and check whether the post appears before creating another one.", InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 " + tr(_lang(w), "menu_title"), style="primary", callback_data=CB_MENU)],
        ]))
        return REVIEW

    if message_key:
        completed = context.user_data.setdefault("_completed_post_composers", {})
        if not isinstance(completed, dict):
            completed = {}
            context.user_data["_completed_post_composers"] = completed
        completed[message_key] = str(post_id)
        while len(completed) > 20:
            completed.pop(next(iter(completed)))

    chat_id, ui_message_id = w.get("ui_chat_id"), w.get("ui_message_id")
    context.user_data.pop("schedule_post", None)
    if chat_id is not None:
        try:
            await chat_cleanup.cleanup(context, chat_id, keep_message_id=ui_message_id)
        except Exception:
            logger.exception("Post %s saved, but composer cleanup failed.", post_id)

    success = (
        "✅ *Post scheduled successfully*\n\n"
        f"Post ID: {escape_markdown(str(post_id), version=1)}\n\n"
        "Your post is now available in *My Posts*, where you can pause, resume or delete it."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📋 " + tr(_lang(w), "menu_title"), style="primary", callback_data=CB_MENU)]])
    screen = {"ui_chat_id": chat_id, "ui_message_id": ui_message_id}
    try:
        await _show(context, screen, success, keyboard)
    except Exception:
        logger.exception("Post %s saved, but success screen could not be displayed.", post_id)
        if chat_id is not None:
            try:
                await context.bot.send_message(chat_id=chat_id, text=success, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
            except Exception:
                logger.exception("Could not send success notice for scheduled post %s.", post_id)
    return HOME


async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    await _answer(query)
    context.user_data.pop("schedule_post", None)
    if query.message:
        await chat_cleanup.cleanup(context, query.message.chat_id, keep_message_id=query.message.message_id)
    await replace_with_main_menu_message(query, context, tr(language, "menu_title"), user_id, language)
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("schedule_post", None)
    if not update.message:
        return ConversationHandler.END
    user_id = update.effective_user.id if update.effective_user else None
    language = await resolve_user_language(context, user_id)
    chat_id = update.message.chat_id
    final = await context.bot.send_message(chat_id=chat_id, text=tr(language, "menu_title"), reply_markup=main_menu_keyboard(context.bot, user_id, language))
    await chat_cleanup.cleanup(context, chat_id, keep_message_id=final.message_id)
    return ConversationHandler.END


def build_schedule_wizard() -> ConversationHandler:
    """Expose the non-linear composer from the existing Schedule New Post action."""
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(enter_schedule_post, pattern=f"^{CB_SCHEDULE_NEW}$")],
        states={
            HOME: [
                CallbackQueryHandler(cb_home, pattern=f"^{CB_HOME}$"),
                CallbackQueryHandler(cb_content, pattern=f"^{CB_CONTENT}$"),
                CallbackQueryHandler(cb_destination, pattern=f"^{CB_DESTINATION}$"),
                CallbackQueryHandler(cb_schedule, pattern=f"^{CB_SCHEDULE}$"),
                CallbackQueryHandler(cb_options, pattern=f"^{CB_OPTIONS}$"),
                CallbackQueryHandler(cb_buttons, pattern=f"^{CB_BUTTONS}$"),
                CallbackQueryHandler(cb_review, pattern=f"^{CB_REVIEW}$"),
            ],
            CONTENT: [
                MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.ANIMATION | filters.VOICE) & ~filters.COMMAND, recv_content),
                CallbackQueryHandler(cb_home, pattern=f"^{CB_HOME}$"),
            ],
            DESTINATION: [
                CallbackQueryHandler(cb_choose_chat, pattern=rf"^{re.escape(CB_CHAT)}-?\d+$"),
                CallbackQueryHandler(cb_destination_page, pattern=rf"^{re.escape(CB_CHAT_PAGE)}\d+$"),
                CallbackQueryHandler(cb_manual_destination, pattern=f"^{CB_MANUAL}$"),
                CallbackQueryHandler(cb_home, pattern=f"^{CB_HOME}$"),
            ],
            MANUAL_DESTINATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_manual_destination),
                CallbackQueryHandler(cb_home, pattern=f"^{CB_HOME}$"),
            ],
            SCHEDULE: [
                CallbackQueryHandler(cb_mode_once, pattern=f"^{CB_MODE_ONCE}$"),
                CallbackQueryHandler(cb_mode_interval, pattern=f"^{CB_MODE_INTERVAL}$"),
                CallbackQueryHandler(cb_mode_weekly, pattern=f"^{CB_MODE_WEEKLY}$"),
                CallbackQueryHandler(cb_first_run, pattern=f"^{CB_FIRST_RUN}$"),
                CallbackQueryHandler(cb_timezone, pattern=f"^{CB_TIMEZONE}$"),
                CallbackQueryHandler(cb_run_limit, pattern=f"^{CB_RUN_LIMIT}$"),
                CallbackQueryHandler(cb_home, pattern=f"^{CB_HOME}$"),
            ],
            TIMEZONE: [
                CallbackQueryHandler(cb_timezone_pick, pattern=rf"^{re.escape(CB_TZ)}.+$"),
                CallbackQueryHandler(cb_timezone_page, pattern=rf"^{re.escape(CB_TZ_PAGE)}\d+$"),
                CallbackQueryHandler(cb_schedule, pattern=f"^{CB_SCHEDULE}$"),
            ],
            INTERVAL_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_interval_value),
                CallbackQueryHandler(cb_schedule, pattern=f"^{CB_SCHEDULE}$"),
            ],
            INTERVAL_UNIT: [
                CallbackQueryHandler(cb_interval_unit, pattern=rf"^{re.escape(CB_UNIT)}(?:minutes|hours|days)$"),
                CallbackQueryHandler(cb_schedule, pattern=f"^{CB_SCHEDULE}$"),
            ],
            WEEKDAYS: [
                CallbackQueryHandler(cb_day_toggle, pattern=rf"^{re.escape(CB_DAY)}[0-6]$"),
                CallbackQueryHandler(cb_days_done, pattern=f"^{CB_DAYS_DONE}$"),
                CallbackQueryHandler(cb_schedule, pattern=f"^{CB_SCHEDULE}$"),
            ],
            FIRST_RUN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_first_run),
                CallbackQueryHandler(cb_schedule, pattern=f"^{CB_SCHEDULE}$"),
            ],
            RUN_LIMIT: [
                CallbackQueryHandler(cb_limit_choice, pattern=rf"^{re.escape(CB_LIMIT)}(?:custom|\d+)$"),
                CallbackQueryHandler(cb_limit_unlimited, pattern=f"^{CB_LIMIT_UNLIMITED}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_run_limit),
                CallbackQueryHandler(cb_schedule, pattern=f"^{CB_SCHEDULE}$"),
            ],
            OPTIONS: [
                CallbackQueryHandler(cb_window, pattern=f"^{CB_WINDOW}$"),
                CallbackQueryHandler(cb_delete, pattern=f"^{CB_DELETE}$"),
                CallbackQueryHandler(cb_destruct, pattern=f"^{CB_DESTRUCT}$"),
                CallbackQueryHandler(cb_pin, pattern=f"^{CB_PIN}$"),
                CallbackQueryHandler(cb_home, pattern=f"^{CB_HOME}$"),
            ],
            POSTING_WINDOW: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_posting_window),
                CallbackQueryHandler(cb_home, pattern=f"^{CB_HOME}$"),
            ],
            AUTO_DELETE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_auto_delete),
                CallbackQueryHandler(cb_options, pattern=f"^{CB_OPTIONS}$"),
            ],
            SELF_DESTRUCT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_self_destruct),
                CallbackQueryHandler(cb_options, pattern=f"^{CB_OPTIONS}$"),
            ],
            AUTO_PIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_auto_pin),
                CallbackQueryHandler(cb_pin_forever, pattern=f"^{CB_PIN_FOREVER}$"),
                CallbackQueryHandler(cb_pin_duration, pattern=f"^{CB_PIN_DURATION}$"),
                CallbackQueryHandler(cb_options, pattern=f"^{CB_OPTIONS}$"),
            ],
            BUTTONS: [
                CallbackQueryHandler(cb_button_add, pattern=f"^{CB_BUTTON_ADD}$"),
                CallbackQueryHandler(cb_button_remove, pattern=f"^{CB_BUTTON_REMOVE}$"),
                CallbackQueryHandler(cb_button_clear, pattern=f"^{CB_BUTTON_CLEAR}$"),
                CallbackQueryHandler(cb_home, pattern=f"^{CB_HOME}$"),
            ],
            BUTTON_ENTRY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_button_batch),
                CallbackQueryHandler(cb_buttons, pattern=f"^{CB_BUTTONS}$"),
            ],
            REVIEW: [
                CallbackQueryHandler(cb_confirm, pattern=f"^{CB_CONFIRM}$"),
                CallbackQueryHandler(cb_content, pattern=f"^{CB_CONTENT}$"),
                CallbackQueryHandler(cb_destination, pattern=f"^{CB_DESTINATION}$"),
                CallbackQueryHandler(cb_schedule, pattern=f"^{CB_SCHEDULE}$"),
                CallbackQueryHandler(cb_options, pattern=f"^{CB_OPTIONS}$"),
                CallbackQueryHandler(cb_buttons, pattern=f"^{CB_BUTTONS}$"),
                CallbackQueryHandler(cb_home, pattern=f"^{CB_HOME}$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel, pattern=f"^{CB_CANCEL}$"),
            CallbackQueryHandler(cb_menu, pattern=f"^{CB_MENU}$"),
            CallbackQueryHandler(cb_menu, pattern=f"^{CB_MAIN_MENU}$"),
        ],
        allow_reentry=True,
        name="schedule_post_composer",
        persistent=False,
    )


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await cb_menu(update, context)
