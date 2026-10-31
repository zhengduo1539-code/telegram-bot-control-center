"""Language picker command and persisted user language preferences."""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes, filters

from database import get_user_language, set_user_language
from localization import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    language_button_pattern,
    language_from_button,
    language_keyboard,
    normalize_language,
    translate,
)

logger = logging.getLogger(__name__)


async def resolve_user_language(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> str:
    """Load a user's saved locale once per update/session, defaulting safely to English."""
    cached = context.user_data.get("language")
    if cached in LANGUAGES:
        return cached
    if user_id is None:
        return DEFAULT_LANGUAGE
    try:
        language = normalize_language(await get_user_language(user_id))
    except Exception:
        logger.exception("Could not load language preference for user_id=%s", user_id)
        language = DEFAULT_LANGUAGE
    context.user_data["language"] = language
    return language


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display a temporary native Reply Keyboard with 20 language choices."""
    query = update.callback_query
    if query:
        await query.answer()

    message = update.effective_message
    if message is None:
        return

    chat = update.effective_chat
    user_id = update.effective_user.id if update.effective_user else None

    if chat is not None and chat.type != ChatType.PRIVATE:
        try:
            bot_user = await context.bot.get_me()
            private_url = f"https://t.me/{bot_user.username}" if bot_user.username else None
        except Exception:
            logger.exception("Could not create private language-settings link.")
            private_url = None

        rows = [[InlineKeyboardButton("📩 Open Bot in Private Chat", url=private_url)]] if private_url else []
        await message.reply_text(
            "🌐 Language settings are available in the bot's private chat. "
            "Open the bot privately and send /lang.",
            reply_markup=InlineKeyboardMarkup(rows) if rows else None,
        )
        return

    language = await resolve_user_language(context, user_id)
    context.user_data["awaiting_language_choice"] = True
    current_name = LANGUAGES[language]["name"]
    await message.reply_text(
        f"{translate(language, 'lang_title')}\n\n"
        f"{translate(language, 'lang_prompt')}\n\n"
        f"Current language: {current_name}",
        reply_markup=language_keyboard(),
    )


async def handle_language_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Persist a selected locale, then remove the one-time reply keyboard."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if not context.user_data.get("awaiting_language_choice"):
        return

    language = language_from_button(message.text)
    if language is None:
        return

    try:
        await set_user_language(user.id, language)
    except Exception:
        logger.exception("Could not save language preference for user_id=%s", user.id)
        current = await resolve_user_language(context, user.id)
        await message.reply_text(
            "⚠️ Could not save your language setting. Please try again.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.pop("awaiting_language_choice", None)
        return

    context.user_data["language"] = language
    context.user_data.pop("awaiting_language_choice", None)

    await message.reply_text(
        translate(language, "lang_saved"),
        reply_markup=ReplyKeyboardRemove(),
    )
    from handlers.base import main_menu_keyboard

    await message.reply_text(
        translate(language, "menu_title"),
        reply_markup=main_menu_keyboard(context.bot, user.id, language),
    )


def language_selection_filter():
    """Filter only the 20 exact language button labels, not normal wizard text."""
    return filters.Regex(language_button_pattern())
