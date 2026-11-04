"""Language picker command and persisted user language preferences."""
from __future__ import annotations

import logging

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes, filters

from database import get_user_language, set_user_language
from reply_keyboard import register_reply_keyboard_routes
from localization import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    language_button_pattern,
    language_cancel_button_label,
    language_from_button,
    language_keyboard,
    normalize_language,
    translate,
)
from language_ui_localization import translate_language_ui as lut

logger = logging.getLogger(__name__)


async def resolve_user_language(context: ContextTypes.DEFAULT_TYPE, user_id: int | None) -> str:
    """Load a user's saved locale once per update/session, defaulting safely to English."""
    user_data = getattr(context, "user_data", None)
    cached = user_data.get("language") if isinstance(user_data, dict) else None
    if cached in LANGUAGES:
        return cached
    if user_id is None:
        return DEFAULT_LANGUAGE
    try:
        language = normalize_language(await get_user_language(user_id))
    except Exception:
        logger.exception("Could not load language preference for user_id=%s", user_id)
        language = DEFAULT_LANGUAGE
    if isinstance(user_data, dict):
        user_data["language"] = language
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
        language = await resolve_user_language(context, user_id)
        try:
            bot_user = await context.bot.get_me()
            private_url = f"https://t.me/{bot_user.username}" if bot_user.username else None
        except Exception:
            logger.exception("Could not create private language-settings link.")
            private_url = None
        if private_url:
            open_label = translate(language, "open_private")
            keyboard = ReplyKeyboardMarkup(
                [[KeyboardButton(open_label, style="primary")]],
                resize_keyboard=True,
                one_time_keyboard=False,
                is_persistent=True,
            )
            sent = await message.reply_text(
                f"{lut(language, 'private_only')}\n\n"
                f"{lut(language, 'open_private')}\n\n🔗 {private_url}",
                reply_markup=keyboard,
            )
            register_reply_keyboard_routes(
                context.bot_data,
                chat.id,
                {open_label: {"type": "url", "url": private_url}},
                sent,
            )
        else:
            await message.reply_text(lut(language, "private_only"))
        return

    language = await resolve_user_language(context, user_id)
    context.user_data["awaiting_language_choice"] = True
    current_name = LANGUAGES[language]["name"]
    await message.reply_text(
        f"{translate(language, 'lang_title')}\n\n"
        f"{translate(language, 'lang_prompt')}\n\n"
        f"{translate(language, 'language')}: {current_name}",
        reply_markup=language_keyboard(language),
    )


async def handle_language_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Persist a selected locale, then remove the one-time reply keyboard."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if update.effective_chat is None or update.effective_chat.type != ChatType.PRIVATE:
        return
    if not context.user_data.get("awaiting_language_choice"):
        return

    current_language = await resolve_user_language(context, user.id)
    if message.text == language_cancel_button_label(current_language):
        context.user_data.pop("awaiting_language_choice", None)
        try:
            await context.bot.delete_message(
                chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except Exception as exc:
            logger.debug("Could not remove language-picker exit message: %s", exc)

        from handlers.base import main_menu_keyboard

        await message.reply_text(
            translate(current_language, "menu_title"),
            reply_markup=main_menu_keyboard(context.bot, user.id, current_language),
        )
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
            lut(current, 'save_failed'),
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

    # ReplyKeyboardMarkup sends the selected label as a normal user message.
    # Remove that temporary selection bubble so it does not remain in chat history.
    try:
        await context.bot.delete_message(
            chat_id=message.chat_id,
            message_id=message.message_id,
        )
    except Exception as exc:
        # Removing the keyboard and saving the locale remain successful even
        # when Telegram does not permit deleting the user's selection message.
        logger.debug("Could not remove language selection message: %s", exc)

    from handlers.base import main_menu_keyboard

    await message.reply_text(
        translate(language, "menu_title"),
        reply_markup=main_menu_keyboard(context.bot, user.id, language),
    )


def language_selection_filter():
    """Filter only the 20 exact language button labels, not normal wizard text."""
    return filters.Regex(language_button_pattern())
