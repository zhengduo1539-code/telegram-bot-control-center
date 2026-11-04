"""
handlers/owner_panel.py — Bot-owner-only admin panel.

Only chat IDs listed in config.ADMIN_IDS ever see the "🛠 Owner Panel" button
on /start (added in handlers/base.py's main_menu_keyboard). Every handler in
this module double-checks the caller against ADMIN_IDS again before doing
anything, so even a crafted callback_data can't be replayed by a non-owner.

Features:
  - 📊 Status        — live counts: users, groups and scheduled posts by status
  - 👤 User List      — paginated list of every user_id who has ever messaged the bot
  - 👥 Group List     — paginated list of every group/channel the bot is active in
  - 📣 Broadcast All  — send a message to every tracked user
  - 🎯 Broadcast One  — send a message to one specific user_id ("broadcast signal")
  - 🧹 Clear Data     — wipe scheduled posts, retained legacy data and lifecycle tasks (double-confirm)
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.helpers import escape_markdown
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_IDS
from handlers.language import resolve_user_language
from localization import translate as tr
from owner_panel_localization import translate_owner_panel as ot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Callback-data constants
# ---------------------------------------------------------------------------
CB_OWNER_PANEL      = "owner:panel"
CB_OWNER_STATUS     = "owner:status"
CB_OWNER_USERS      = "owner:users"
CB_OWNER_GROUPS     = "owner:groups"
CB_OWNER_BCAST_ALL  = "owner:bcast_all"
CB_OWNER_BCAST_ONE  = "owner:bcast_one"
CB_OWNER_CLEARDATA  = "owner:cleardata"
CB_OWNER_CLEAR_YES  = "owner:cleardata_yes"
CB_OWNER_BACK       = "owner:back"

CB_OWNER_USERS_PAGE  = "owner:userspage:"   # + page number
CB_OWNER_GROUPS_PAGE = "owner:groupspage:"  # + page number

PAGE_SIZE = 20

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------
OWNER_MENU           = 100
OWNER_LIST           = 101
OWNER_BCAST_ALL_WAIT = 102
OWNER_BCAST_ONE_WAIT_ID  = 103
OWNER_BCAST_ONE_WAIT_MSG = 104
OWNER_CLEARDATA_CONFIRM  = 105


def _is_owner(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def _owner_menu_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(ot(language, "status"), style="primary", callback_data=CB_OWNER_STATUS)],
        [
            InlineKeyboardButton(ot(language, "users"), style="primary", callback_data=CB_OWNER_USERS),
            InlineKeyboardButton(ot(language, "groups"), style="primary", callback_data=CB_OWNER_GROUPS),
        ],
        [
            InlineKeyboardButton(ot(language, "broadcast_all"), style="primary", callback_data=CB_OWNER_BCAST_ALL),
            InlineKeyboardButton(ot(language, "broadcast_one"), style="primary", callback_data=CB_OWNER_BCAST_ONE),
        ],
        [InlineKeyboardButton(ot(language, "clear_data"), style="danger", callback_data=CB_OWNER_CLEARDATA)],
        [InlineKeyboardButton(tr(language, "menu_title"), style="primary", callback_data="nav:main_menu")],
    ])

def _back_only_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ " + tr(language, "back"), callback_data=CB_OWNER_BACK)]])

def _cancel_only_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ " + tr(language, "cancel"), style="danger", callback_data=CB_OWNER_BACK)]])

async def cb_owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.from_user:
        return ConversationHandler.END
    language = await resolve_user_language(context, query.from_user.id)
    if not _is_owner(query.from_user.id):
        await query.answer(ot(language, "owner_only"), show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.edit_message_text(
        f"{ot(language, 'title')}\n\n{ot(language, 'intro')}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_owner_menu_keyboard(language),
    )
    return OWNER_MENU

async def cb_owner_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.from_user:
        return ConversationHandler.END
    language = await resolve_user_language(context, query.from_user.id)
    if not _is_owner(query.from_user.id):
        await query.answer(ot(language, "owner_only"), show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data.pop("_owner_bcast_target", None)
    await query.edit_message_text(
        f"{ot(language, 'title')}\n\n{ot(language, 'intro')}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_owner_menu_keyboard(language),
    )
    return OWNER_MENU

async def cb_owner_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from database import get_bot_stats
    query = update.callback_query
    if not query or not query.from_user:
        return OWNER_MENU
    language = await resolve_user_language(context, query.from_user.id)
    if not _is_owner(query.from_user.id):
        await query.answer(ot(language, "owner_only"), show_alert=True)
        return ConversationHandler.END
    await query.answer()
    try:
        stats = await get_bot_stats()
    except Exception:
        logger.exception("get_bot_stats failed")
        await query.edit_message_text(
            ot(language, "status_error"),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_back_only_keyboard(language),
        )
        return OWNER_MENU
    text = (
        f"{ot(language, 'status')}\n\n"
        f"👤 {ot(language, 'users_label')}: `{stats['users']}`\n"
        f"👥 {ot(language, 'groups_label')}: `{stats['groups']}`\n\n"
        f"📝 {ot(language, 'posts_total')}: `{stats['posts_total']}`\n"
        f"  ⏳ {ot(language, 'pending')}: `{stats['posts_pending']}`\n"
        f"  ⏸ {ot(language, 'paused')}: `{stats['posts_paused']}`\n"
        f"  ✅ {ot(language, 'posted')}: `{stats['posts_posted']}`\n"
        f"  ❌ {ot(language, 'failed')}: `{stats['posts_failed']}`"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=_back_only_keyboard(language))
    return OWNER_MENU

def _escape_md(text: str) -> str:
    """Escape characters that break Telegram's legacy Markdown parser."""
    return escape_markdown(text, version=1)


def _safe_exc(exc: Exception) -> str:
    """Render an exception message for a Markdown code span without breaking it."""
    return str(exc).replace("`", "'")


async def _render_id_list(
    query, items: list[dict], page: int, label: str,
    page_cb_prefix: str, kind: str, language: str = "en",
) -> None:
    total = len(items)
    n_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, n_pages - 1))
    start, end = page * PAGE_SIZE, min((page + 1) * PAGE_SIZE, total)
    chunk = items[start:end]
    lines = [f"{label} · {page + 1}/{n_pages} · {total}"]
    for idx, item in enumerate(chunk, start=start + 1):
        if kind == "user":
            item_id = item["user_id"]
            username = item.get("username")
            name = item.get("first_name") or ""
            handle = f"@{_escape_md(username)}" if username else (_escape_md(name) if name else "—")
        else:
            item_id = item["chat_id"]
            title = item.get("title")
            handle = _escape_md(title) if title else "—"
        lines.append(f"{idx}. `{item_id}` — {handle}")
    if not chunk:
        lines.append("—")
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ " + tr(language, "previous"), style="primary", callback_data=f"{page_cb_prefix}{page - 1}"))
    if end < total:
        nav_row.append(InlineKeyboardButton(tr(language, "next") + " ▶️", style="primary", callback_data=f"{page_cb_prefix}{page + 1}"))
    buttons = [nav_row] if nav_row else []
    buttons.append([InlineKeyboardButton("⬅️ " + tr(language, "back"), callback_data=CB_OWNER_BACK)])
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))

async def cb_owner_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from database import list_users
    query = update.callback_query
    if not query or not query.from_user:
        return OWNER_MENU
    language = await resolve_user_language(context, query.from_user.id)
    if not _is_owner(query.from_user.id):
        await query.answer(ot(language, "owner_only"), show_alert=True)
        return ConversationHandler.END
    await query.answer()
    try:
        items = await list_users()
    except Exception:
        logger.exception("list_users failed")
        await query.edit_message_text(ot(language, "status_error"), parse_mode=ParseMode.MARKDOWN, reply_markup=_back_only_keyboard(language))
        return OWNER_MENU
    await _render_id_list(query, items, 0, ot(language, "user_list"), CB_OWNER_USERS_PAGE, kind="user", language=language)
    return OWNER_LIST

async def cb_owner_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from database import list_users
    query = update.callback_query
    if not query or not query.from_user:
        return OWNER_MENU
    language = await resolve_user_language(context, query.from_user.id)
    if not _is_owner(query.from_user.id):
        await query.answer(ot(language, "owner_only"), show_alert=True)
        return ConversationHandler.END
    page_str = (query.data or "").replace(CB_OWNER_USERS_PAGE, "")
    page = int(page_str) if page_str.isdigit() else 0
    await query.answer()
    items = await list_users()
    await _render_id_list(query, items, page, ot(language, "user_list"), CB_OWNER_USERS_PAGE, kind="user", language=language)
    return OWNER_LIST

async def cb_owner_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from database import list_groups
    query = update.callback_query
    if not query or not query.from_user:
        return OWNER_MENU
    language = await resolve_user_language(context, query.from_user.id)
    if not _is_owner(query.from_user.id):
        await query.answer(ot(language, "owner_only"), show_alert=True)
        return ConversationHandler.END
    await query.answer()
    try:
        items = await list_groups()
    except Exception:
        logger.exception("list_groups failed")
        await query.edit_message_text(ot(language, "status_error"), parse_mode=ParseMode.MARKDOWN, reply_markup=_back_only_keyboard(language))
        return OWNER_MENU
    await _render_id_list(query, items, 0, ot(language, "group_list"), CB_OWNER_GROUPS_PAGE, kind="group", language=language)
    return OWNER_LIST

async def cb_owner_groups_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from database import list_groups
    query = update.callback_query
    if not query or not query.from_user:
        return OWNER_MENU
    language = await resolve_user_language(context, query.from_user.id)
    if not _is_owner(query.from_user.id):
        await query.answer(ot(language, "owner_only"), show_alert=True)
        return ConversationHandler.END
    page_str = (query.data or "").replace(CB_OWNER_GROUPS_PAGE, "")
    page = int(page_str) if page_str.isdigit() else 0
    await query.answer()
    items = await list_groups()
    await _render_id_list(query, items, page, ot(language, "group_list"), CB_OWNER_GROUPS_PAGE, kind="group", language=language)
    return OWNER_LIST

async def cb_owner_bcast_all_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.from_user:
        return OWNER_MENU
    if not _is_owner(query.from_user.id):
        await query.answer("⛔ Owner only.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.edit_message_text(
        "📣 *Broadcast to All Users*\n\n"
        "Send the message you want delivered to every tracked user "
        "(text only). Send `/cancel_owner` to abort.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_cancel_only_keyboard(),
    )
    return OWNER_BCAST_ALL_WAIT


async def msg_owner_bcast_all_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from database import list_user_ids

    if not update.message or not update.effective_user:
        return OWNER_BCAST_ALL_WAIT
    if not _is_owner(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text
    if not text:
        await update.message.reply_text("Please send text only.")
        return OWNER_BCAST_ALL_WAIT

    ids = await list_user_ids()
    sent, failed = 0, 0
    status_msg = await update.message.reply_text(f"Sending to {len(ids)} users…")
    for uid in ids:
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Forbidden:
            failed += 1
        except TelegramError:
            failed += 1
    await status_msg.edit_text(
        f"📣 *Broadcast complete.*\n\n✅ Delivered: `{sent}`\n❌ Failed/blocked: `{failed}`",
        parse_mode=ParseMode.MARKDOWN,
    )
    await update.message.reply_text(
        "🛠 *Owner Control Center*\n\nMonitor activity, manage users and groups, and send broadcasts.\n\nChoose an operation:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_owner_menu_keyboard(),
    )
    return OWNER_MENU


# ---------------------------------------------------------------------------
# Broadcast — single user ("broadcast signal")
# ---------------------------------------------------------------------------

async def cb_owner_bcast_one_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.from_user:
        return OWNER_MENU
    if not _is_owner(query.from_user.id):
        await query.answer("⛔ Owner only.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.edit_message_text(
        "🎯 *Broadcast to One User*\n\n"
        "Send the numeric *user ID* to message. Send `/cancel_owner` to abort.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_cancel_only_keyboard(),
    )
    return OWNER_BCAST_ONE_WAIT_ID


async def msg_owner_bcast_one_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return OWNER_BCAST_ONE_WAIT_ID
    if not _is_owner(update.effective_user.id):
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    if not text.isdigit():
        await update.message.reply_text("That doesn't look like a numeric user ID. Try again, or /cancel_owner.")
        return OWNER_BCAST_ONE_WAIT_ID
    context.user_data["_owner_bcast_target"] = int(text)
    await update.message.reply_text(
        f"Target set to `{text}`.\n\nNow send the message text to deliver.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return OWNER_BCAST_ONE_WAIT_MSG


async def msg_owner_bcast_one_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return OWNER_BCAST_ONE_WAIT_MSG
    if not _is_owner(update.effective_user.id):
        return ConversationHandler.END
    target = context.user_data.get("_owner_bcast_target")
    text = update.message.text
    if target is None:
        await update.message.reply_text("No target set — starting over. Use the Owner Panel again.")
        return ConversationHandler.END
    if not text:
        await update.message.reply_text("Please send text only.")
        return OWNER_BCAST_ONE_WAIT_MSG

    try:
        await context.bot.send_message(chat_id=target, text=text)
        result = f"✅ Delivered to `{target}`."
    except Forbidden:
        result = f"❌ `{target}` has blocked the bot or never started it."
    except TelegramError as exc:
        result = f"❌ Failed to deliver to `{target}`: `{_safe_exc(exc)}`"

    context.user_data.pop("_owner_bcast_target", None)
    await update.message.reply_text(result, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text(
        "🛠 *Owner Control Center*\n\nMonitor activity, manage users and groups, and send broadcasts.\n\nChoose an operation:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_owner_menu_keyboard(),
    )
    return OWNER_MENU


async def cmd_cancel_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/cancel_owner — abort any in-progress owner-panel broadcast flow."""
    if not update.effective_user or not _is_owner(update.effective_user.id):
        return ConversationHandler.END
    context.user_data.pop("_owner_bcast_target", None)
    if update.message:
        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=_owner_menu_keyboard(),
        )
    return OWNER_MENU


# ---------------------------------------------------------------------------
# Clear Data (double-confirm — destructive)
# ---------------------------------------------------------------------------

async def cb_owner_cleardata_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.from_user:
        return OWNER_MENU
    if not _is_owner(query.from_user.id):
        await query.answer("⛔ Owner only.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.edit_message_text(
        "⚠️ *Clear All Scheduled Data*\n\n"
        "This permanently deletes:\n"
        "• All scheduled posts (pending, paused, posted, failed)\n"
        "• Any retained data from retired posting tools\n"
        "• Pending message lifecycle tasks\n\n"
        "Tracked user/group lists are kept so broadcasting keeps working.\n\n"
        "*This cannot be undone.* Proceed?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, wipe it all", style="danger", callback_data=CB_OWNER_CLEAR_YES),
                InlineKeyboardButton("⬅️ Back", callback_data=CB_OWNER_BACK),
            ],
        ]),
    )
    return OWNER_CLEARDATA_CONFIRM


async def cb_owner_cleardata_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from database import clear_all_bot_data

    query = update.callback_query
    if not query or not query.from_user:
        return OWNER_MENU
    if not _is_owner(query.from_user.id):
        await query.answer("⛔ Owner only.", show_alert=True)
        return ConversationHandler.END
    await query.answer("Wiping…")
    try:
        result = await clear_all_bot_data()
    except Exception as exc:
        logger.exception("clear_all_bot_data failed: %s", exc)
        await query.edit_message_text(
            f"❌ Clear failed: `{_safe_exc(exc)}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_back_only_keyboard(),
        )
        return OWNER_MENU

    await query.edit_message_text(
        "🧹 *Data cleared.*\n\n"
        f"Posts removed: `{result['posts']}`\n"
        f"Legacy records removed: `{result['queues'] + result['pools']}`\n"
        f"Lifecycle tasks removed: `{result.get('lifecycle_tasks', 0)}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_back_only_keyboard(),
    )
    return OWNER_MENU


# ---------------------------------------------------------------------------
# ConversationHandler factory
# ---------------------------------------------------------------------------

def build_owner_panel() -> ConversationHandler:
    """
    Owner-only admin panel. Entry point is the "🛠 Owner Panel" inline button
    shown on /start (only rendered for user IDs in config.ADMIN_IDS — see
    handlers/base.py main_menu_keyboard). Every handler additionally
    re-checks ADMIN_IDS so a copied callback_data can't be replayed by
    someone else.
    """
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_owner_panel, pattern=f"^{CB_OWNER_PANEL}$"),
        ],
        states={
            OWNER_MENU: [
                CallbackQueryHandler(cb_owner_status,          pattern=f"^{CB_OWNER_STATUS}$"),
                CallbackQueryHandler(cb_owner_users,           pattern=f"^{CB_OWNER_USERS}$"),
                CallbackQueryHandler(cb_owner_groups,          pattern=f"^{CB_OWNER_GROUPS}$"),
                CallbackQueryHandler(cb_owner_bcast_all_start, pattern=f"^{CB_OWNER_BCAST_ALL}$"),
                CallbackQueryHandler(cb_owner_bcast_one_start, pattern=f"^{CB_OWNER_BCAST_ONE}$"),
                CallbackQueryHandler(cb_owner_cleardata_confirm, pattern=f"^{CB_OWNER_CLEARDATA}$"),
                CallbackQueryHandler(cb_owner_back,            pattern=f"^{CB_OWNER_BACK}$"),
            ],
            OWNER_LIST: [
                CallbackQueryHandler(cb_owner_users_page,  pattern=rf"^{CB_OWNER_USERS_PAGE}\d+$"),
                CallbackQueryHandler(cb_owner_groups_page, pattern=rf"^{CB_OWNER_GROUPS_PAGE}\d+$"),
                CallbackQueryHandler(cb_owner_back,        pattern=f"^{CB_OWNER_BACK}$"),
            ],
            OWNER_BCAST_ALL_WAIT: [
                CallbackQueryHandler(cb_owner_back, pattern=f"^{CB_OWNER_BACK}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_owner_bcast_all_send),
            ],
            OWNER_BCAST_ONE_WAIT_ID: [
                CallbackQueryHandler(cb_owner_back, pattern=f"^{CB_OWNER_BACK}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_owner_bcast_one_id),
            ],
            OWNER_BCAST_ONE_WAIT_MSG: [
                CallbackQueryHandler(cb_owner_back, pattern=f"^{CB_OWNER_BACK}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_owner_bcast_one_send),
            ],
            OWNER_CLEARDATA_CONFIRM: [
                CallbackQueryHandler(cb_owner_cleardata_yes, pattern=f"^{CB_OWNER_CLEAR_YES}$"),
                CallbackQueryHandler(cb_owner_back,          pattern=f"^{CB_OWNER_BACK}$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cb_owner_back, pattern=f"^{CB_OWNER_BACK}$"),
            CommandHandler("cancel_owner", cmd_cancel_owner),
        ],
        allow_reentry=True,
        name="owner_panel_conversation",
        persistent=False,
    )
