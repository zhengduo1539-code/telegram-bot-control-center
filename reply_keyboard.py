"""Bridge all private-chat feature buttons to native Reply Keyboards.

The welcome screen keeps only Add to My Group and Share Bot as inline URL
buttons. Telegram permits one keyboard type per message, so function buttons
are shown using a small follow-up message. Reply-keyboard selections are
translated back into the existing callback handlers.
"""
from __future__ import annotations

import logging
from typing import Any

from telegram import CallbackQuery, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

logger = logging.getLogger(__name__)
ROUTE_REGISTRY_KEY = "_reply_keyboard_route_registry"


def is_private_chat(chat_id: Any) -> bool:
    """Private user chats use positive integer chat IDs."""
    try:
        return int(chat_id) > 0
    except (TypeError, ValueError):
        return False


def _preserve_inline_button(button: Any) -> bool:
    url = str(getattr(button, "url", None) or "").lower()
    return "startgroup=true" in url or "t.me/share/url" in url


def split_inline_keyboard(
    markup: InlineKeyboardMarkup | None,
) -> tuple[InlineKeyboardMarkup | None, ReplyKeyboardMarkup | None, dict[str, dict[str, str]]]:
    """Keep only the two approved URL links inline and convert other buttons."""
    if not isinstance(markup, InlineKeyboardMarkup):
        return None, None, {}

    inline_rows: list[list[Any]] = []
    reply_rows: list[list[KeyboardButton]] = []
    routes: dict[str, dict[str, str]] = {}

    for row in markup.inline_keyboard:
        kept_inline: list[Any] = []
        converted: list[KeyboardButton] = []
        for button in row:
            if _preserve_inline_button(button):
                kept_inline.append(button)
                continue

            label = str(getattr(button, "text", "") or "").strip() or "Select option"
            callback_data = getattr(button, "callback_data", None)
            url = getattr(button, "url", None)
            if callback_data is not None:
                route = {"type": "callback", "data": str(callback_data)}
            elif url:
                route = {"type": "url", "url": str(url)}
            else:
                # Some future inline-only button types do not have a direct
                # ReplyKeyboardButton equivalent. Keep a visible menu choice
                # and explain its limitation rather than silently dropping it.
                route = {"type": "unsupported"}

            display_label = label
            index = 2
            while display_label in routes and routes[display_label] != route:
                display_label = f"{label} ({index})"
                index += 1
            routes[display_label] = route
            converted.append(KeyboardButton(display_label))

        if kept_inline:
            inline_rows.append(kept_inline)
        if converted:
            # Three buttons per row is a manageable mobile layout.
            reply_rows.extend(
                converted[index:index + 3]
                for index in range(0, len(converted), 3)
            )

    inline_markup = InlineKeyboardMarkup(inline_rows) if inline_rows else None
    reply_markup = (
        ReplyKeyboardMarkup(
            reply_rows,
            resize_keyboard=True,
            one_time_keyboard=False,
            is_persistent=True,
        )
        if reply_rows
        else None
    )
    return inline_markup, reply_markup, routes


def _chat_key(chat_id: Any) -> str:
    return str(chat_id)


def get_reply_keyboard_entry(bot_data: dict[str, Any], chat_id: Any) -> dict[str, Any] | None:
    return bot_data.get(ROUTE_REGISTRY_KEY, {}).get(_chat_key(chat_id))


def clear_reply_keyboard_routes(bot_data: dict[str, Any] | None, chat_id: Any) -> None:
    if bot_data is None:
        return
    registry = bot_data.get(ROUTE_REGISTRY_KEY)
    if isinstance(registry, dict):
        registry.pop(_chat_key(chat_id), None)


def register_reply_keyboard_routes(
    bot_data: dict[str, Any] | None,
    chat_id: Any,
    routes: dict[str, dict[str, str]],
    target_message: Any,
    anchor_message_id: int | None = None,
) -> None:
    if bot_data is None:
        return
    if not routes:
        clear_reply_keyboard_routes(bot_data, chat_id)
        return
    bot_data.setdefault(ROUTE_REGISTRY_KEY, {})[_chat_key(chat_id)] = {
        "routes": dict(routes),
        "target_message": target_message,
        "anchor_message_id": anchor_message_id,
    }


async def remove_previous_keyboard_anchor(bot: Any, bot_data: dict[str, Any] | None, chat_id: Any) -> None:
    entry = get_reply_keyboard_entry(bot_data or {}, chat_id)
    anchor_id = entry.get("anchor_message_id") if entry else None
    if anchor_id is None:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=anchor_id)
    except Exception as exc:
        logger.debug("Could not remove previous Reply Keyboard cue: %s", exc)


async def publish_reply_keyboard(
    bot: Any,
    bot_data: dict[str, Any] | None,
    chat_id: Any,
    markup: ReplyKeyboardMarkup,
    routes: dict[str, dict[str, str]],
    target_message: Any,
) -> Any:
    """Display a Reply Keyboard while keeping the actionable prompt intact."""
    await remove_previous_keyboard_anchor(bot, bot_data, chat_id)
    clear_reply_keyboard_routes(bot_data, chat_id)
    anchor = await bot.send_message(
        chat_id=chat_id,
        text="⬇️",
        reply_markup=markup,
        disable_notification=True,
    )
    register_reply_keyboard_routes(
        bot_data,
        chat_id,
        routes,
        target_message,
        getattr(anchor, "message_id", None),
    )
    return anchor


class _ReplyKeyboardCallbackQuery(CallbackQuery):
    """A callback-compatible object for existing handlers; no API callback exists."""

    async def answer(
        self,
        text: str | None = None,
        show_alert: bool | None = None,
        url: str | None = None,
        cache_time: int | None = None,
        **kwargs: Any,
    ) -> bool:
        if show_alert and text and self.message is not None:
            await self.message.reply_text(text)
        return True


async def handle_reply_keyboard_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Dispatch exact Reply Keyboard choices through the existing callback flows."""
    message = update.message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return
    if not is_private_chat(chat.id):
        return

    entry = get_reply_keyboard_entry(context.bot_data, chat.id)
    route = entry.get("routes", {}).get(message.text) if entry else None
    if not route:
        # Normal wizard text/media input continues to the existing handlers.
        return

    if route["type"] == "url":
        await message.reply_text(f"🔗 Open this link:\n{route['url']}")
        raise ApplicationHandlerStop
    if route["type"] != "callback":
        await message.reply_text(
            "⚠️ This action is not supported by a Reply Keyboard. "
            "Please send /start to refresh the menu."
        )
        raise ApplicationHandlerStop

    target_message = entry.get("target_message")
    if target_message is None:
        await message.reply_text("⚠️ This menu has expired. Please send /start.")
        raise ApplicationHandlerStop

    query = _ReplyKeyboardCallbackQuery(
        id=f"reply-keyboard-{update.update_id}",
        from_user=user,
        chat_instance=str(chat.id),
        message=target_message,
        data=route["data"],
    )
    synthetic_update = Update(update_id=update.update_id, callback_query=query)
    try:
        await context.application.process_update(synthetic_update)
    except Exception:
        logger.exception(
            "Reply Keyboard action dispatch failed for chat_id=%s data=%s",
            chat.id,
            route.get("data"),
        )
        await message.reply_text(
            "⚠️ I couldn't process that action. Please try again or send /start."
        )
    raise ApplicationHandlerStop
