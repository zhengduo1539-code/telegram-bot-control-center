"""
handlers/templates.py — Localized, ownership-safe post template management.

Templates are saved from My Posts and can be previewed, deleted, or reused
inside the Schedule, Queue, and Media Pool workflows.
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
)

from database import list_templates, delete_template, get_template_for_user
from handlers.language import resolve_user_language
from localization import translate as tr
from template_ui_localization import translate_template_ui as tui

from handlers.base import (
    CB_CANCEL, CB_MAIN_MENU, CB_TEMPLATES,
    cmd_cancel, main_menu_keyboard,
)

logger = logging.getLogger(__name__)

TMPL_MENU = 200
TMPL_CONFIRM_DEL = 201
CB_TMPL_PAGE = "tmpl:page:"
CB_TMPL_VIEW = "tmpl:view:"
CB_TMPL_DEL = "tmpl:del:"
CB_TMPL_DEL_YES = "tmpl:delyes:"
_PAGE_SIZE = 5


def _tmpl_list_keyboard(
    templates: list[dict], page: int, language: str = "en"
) -> InlineKeyboardMarkup:
    total = len(templates)
    start = page * _PAGE_SIZE
    end = min(start + _PAGE_SIZE, total)
    rows: list[list[InlineKeyboardButton]] = []
    for tmpl in templates[start:end]:
        tid = str(tmpl["_id"])
        name = str(tmpl.get("name", "Unnamed"))[:40]
        rows.append([
            InlineKeyboardButton(f"📄 {name}", callback_data=f"{CB_TMPL_VIEW}{tid}"),
            InlineKeyboardButton(
                tr(language, "delete"),
                style="danger",
                callback_data=f"{CB_TMPL_DEL}{tid}",
            ),
        ])
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(
            tr(language, "previous"), callback_data=f"{CB_TMPL_PAGE}{page - 1}"
        ))
    if end < total:
        nav_row.append(InlineKeyboardButton(
            tr(language, "next"), callback_data=f"{CB_TMPL_PAGE}{page + 1}"
        ))
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(
        tr(language, "menu_title"), callback_data=CB_MAIN_MENU
    )])
    return InlineKeyboardMarkup(rows)


def _templates_back_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(tr(language, "back"), callback_data=CB_TEMPLATES)
    ]])


def _build_template_preview(tmpl: dict, language: str) -> str:
    """Build a Markdown-safe preview; all user-controlled text is escaped."""
    content = tmpl.get("content") or {}
    name = escape_markdown(str(tmpl.get("name") or "Unnamed"), version=1)
    text = str(content.get("text") or "")
    media_type = str(content.get("media_type") or "")
    buttons = content.get("inline_keyboard") or []
    lines = [tui(language, "preview_title", name=name)]
    if media_type:
        icons = {
            "photo": "🖼", "video": "🎬", "document": "📎",
            "audio": "🎵", "animation": "🎞", "voice": "🎤",
        }
        lines.append(tui(
            language,
            "media_type",
            media_type=f"{icons.get(media_type, '📁')} {escape_markdown(media_type, version=1)}",
        ))
    if text:
        snippet = text[:300] + ("…" if len(text) > 300 else "")
        lines.extend(["", tui(language, "text_label"), escape_markdown(snippet, version=1)])
    else:
        lines.extend(["", tui(language, "no_text")])
    button_labels = [
        escape_markdown(str(button.get("text") or ""), version=1)
        for row in buttons if isinstance(row, list)
        for button in row if isinstance(button, dict) and button.get("text")
    ]
    if button_labels:
        lines.extend(["", tui(language, "buttons_label", buttons=", ".join(button_labels))])
    lines.extend(["", tui(language, "usage_tip")])
    return "\n".join(lines)


async def _show_template_list(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    language: str,
    *,
    page: int = 0,
    answer_callback: bool = True,
) -> int:
    if answer_callback:
        await query.answer()
    try:
        templates = await list_templates(user_id)
    except Exception:
        logger.exception("list_templates failed for user_id=%s", user_id)
        await query.edit_message_text(
            tui(language, "load_error"),
            reply_markup=_templates_back_keyboard(language),
        )
        return TMPL_MENU
    if not templates:
        await query.edit_message_text(
            tr(language, "templates_empty"),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_templates_back_keyboard(language),
        )
        return TMPL_MENU
    last_page = max(0, (len(templates) - 1) // _PAGE_SIZE)
    page = max(0, min(page, last_page))
    context.user_data["_tmpl_page"] = page
    await query.edit_message_text(
        f"{tr(language, 'templates_title')} — {len(templates)}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_tmpl_list_keyboard(templates, page, language),
    )
    return TMPL_MENU


async def enter_templates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.from_user:
        return TMPL_MENU
    language = await resolve_user_language(context, query.from_user.id)
    return await _show_template_list(
        query, context, query.from_user.id, language, page=0
    )


async def cb_tmpl_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.from_user:
        return TMPL_MENU
    raw = (query.data or "").replace(CB_TMPL_PAGE, "", 1)
    page = int(raw) if raw.isdigit() else 0
    language = await resolve_user_language(context, query.from_user.id)
    return await _show_template_list(
        query, context, query.from_user.id, language, page=page
    )


async def cb_tmpl_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.from_user:
        return TMPL_MENU
    tid = (query.data or "").replace(CB_TMPL_VIEW, "", 1)
    user_id = query.from_user.id
    language = await resolve_user_language(context, user_id)
    await query.answer()
    try:
        tmpl = await get_template_for_user(tid, user_id)
    except Exception:
        logger.exception("get_template_for_user failed for user_id=%s", user_id)
        await query.edit_message_text(
            tui(language, "load_error"),
            reply_markup=_templates_back_keyboard(language),
        )
        return TMPL_MENU
    if not tmpl:
        await query.edit_message_text(
            tui(language, "not_found"),
            reply_markup=_templates_back_keyboard(language),
        )
        return TMPL_MENU
    await query.edit_message_text(
        _build_template_preview(tmpl, language),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(tr(language, "back"), callback_data=CB_TEMPLATES),
            InlineKeyboardButton(
                tr(language, "delete"),
                style="danger",
                callback_data=f"{CB_TMPL_DEL}{tid}",
            ),
        ]]),
    )
    return TMPL_MENU


async def cb_tmpl_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.from_user:
        return TMPL_MENU
    tid = (query.data or "").replace(CB_TMPL_DEL, "", 1)
    user_id = query.from_user.id
    language = await resolve_user_language(context, user_id)
    await query.answer()
    try:
        tmpl = await get_template_for_user(tid, user_id)
    except Exception:
        logger.exception("Template ownership check failed for user_id=%s", user_id)
        await query.edit_message_text(
            tui(language, "load_error"),
            reply_markup=_templates_back_keyboard(language),
        )
        return TMPL_MENU
    if not tmpl:
        await query.edit_message_text(
            tui(language, "not_found"),
            reply_markup=_templates_back_keyboard(language),
        )
        return TMPL_MENU
    await query.edit_message_text(
        f"{tui(language, 'delete_title')}\n\n{tui(language, 'delete_body')}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    tr(language, "delete"),
                    style="danger",
                    callback_data=f"{CB_TMPL_DEL_YES}{tid}",
                ),
                InlineKeyboardButton(tr(language, "back"), callback_data=CB_TEMPLATES),
            ],
            [InlineKeyboardButton(
                tr(language, "cancel"), style="danger", callback_data=CB_CANCEL
            )],
        ]),
    )
    return TMPL_CONFIRM_DEL


async def cb_tmpl_del_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.from_user:
        return TMPL_MENU
    tid = (query.data or "").replace(CB_TMPL_DEL_YES, "", 1)
    user_id = query.from_user.id
    language = await resolve_user_language(context, user_id)
    try:
        deleted = await delete_template(tid, user_id=user_id)
    except Exception:
        logger.exception("delete_template failed for user_id=%s", user_id)
        await query.answer(tui(language, "delete_error"), show_alert=True)
        return TMPL_CONFIRM_DEL
    await query.answer(tui(language, "delete_success" if deleted else "delete_missing"))
    return await _show_template_list(
        query, context, user_id, language,
        page=int(context.user_data.get("_tmpl_page", 0) or 0),
        answer_callback=False,
    )


async def cb_tmpl_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    await query.answer()
    await query.edit_message_text(
        tr(language, "menu_title"),
        reply_markup=main_menu_keyboard(
            bot=context.bot, user_id=user_id, language=language
        ),
    )
    return ConversationHandler.END


async def cb_tmpl_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    await query.answer(tr(language, "cancel"))
    await query.edit_message_text(
        tr(language, "menu_title"),
        reply_markup=main_menu_keyboard(
            bot=context.bot, user_id=user_id, language=language
        ),
    )
    return ConversationHandler.END


def build_templates_conversation() -> ConversationHandler:
    oid = r"[0-9a-f]{24}"
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(enter_templates, pattern=f"^{CB_TEMPLATES}$")],
        states={
            TMPL_MENU: [
                CallbackQueryHandler(enter_templates, pattern=f"^{CB_TEMPLATES}$"),
                CallbackQueryHandler(cb_tmpl_page, pattern=rf"^{CB_TMPL_PAGE}\d+$"),
                CallbackQueryHandler(cb_tmpl_view, pattern=rf"^{CB_TMPL_VIEW}{oid}$"),
                CallbackQueryHandler(cb_tmpl_del, pattern=rf"^{CB_TMPL_DEL}{oid}$"),
                CallbackQueryHandler(cb_tmpl_main_menu, pattern=f"^{CB_MAIN_MENU}$"),
            ],
            TMPL_CONFIRM_DEL: [
                CallbackQueryHandler(cb_tmpl_del_yes, pattern=rf"^{CB_TMPL_DEL_YES}{oid}$"),
                CallbackQueryHandler(enter_templates, pattern=f"^{CB_TEMPLATES}$"),
                CallbackQueryHandler(cb_tmpl_main_menu, pattern=f"^{CB_MAIN_MENU}$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_tmpl_cancel, pattern=f"^{CB_CANCEL}$"),
        ],
        allow_reentry=True,
        name="templates_conversation",
        persistent=False,
    )
