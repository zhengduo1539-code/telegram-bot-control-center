"""Render all bot navigation actions as native Telegram Reply Keyboards.

Inline button definitions are retained internally as route definitions only.
Their actions are dispatched to existing conversation handlers through
synthetic callback updates. Private-chat button taps are deleted best-effort.
"""
from __future__ import annotations

import logging
from typing import Any

from telegram import CallbackQuery, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from handlers import chat_cleanup

logger = logging.getLogger(__name__)
ROUTE_REGISTRY_KEY = "_reply_keyboard_route_registry"


def is_private_chat(chat_id: Any) -> bool:
    """Private user chats use positive integer chat IDs."""
    try:
        return int(chat_id) > 0
    except (TypeError, ValueError):
        return False


def split_inline_keyboard(
    markup: InlineKeyboardMarkup | None,
) -> tuple[None, ReplyKeyboardMarkup | None, dict[str, dict[str, str]]]:
    """Convert every inline-button definition into a Reply Keyboard and route map.

    The first tuple item remains None for compatibility with existing callers.
    No Inline Keyboard is ever returned for display.
    """
    if not isinstance(markup, InlineKeyboardMarkup):
        return None, None, {}

    reply_rows: list[list[KeyboardButton]] = []
    inline_rows: list[list[Any]] = []
    routes: dict[str, dict[str, str]] = {}

    for row in markup.inline_keyboard:
        converted: list[KeyboardButton] = []
        retained_inline: list[Any] = []
        for button in row:
            label = str(getattr(button, "text", "") or "").strip() or "Select option"
            callback_data = getattr(button, "callback_data", None)
            url = getattr(button, "url", None)
            # Only the two explicit welcome actions remain native Inline Buttons.
            # Other URL actions (for example, group-to-private setup links) keep
            # the established Reply Keyboard routing behaviour.
            keep_inline = bool(
                url
                and (
                    "startgroup=true" in str(url)
                    or "/share/url?" in str(url)
                )
            )
            if keep_inline:
                retained_inline.append(button)
                continue

            if callback_data is not None:
                route = {"type": "callback", "data": str(callback_data)}
            elif url:
                route = {"type": "url", "url": str(url)}
            else:
                route = {"type": "unsupported"}

            display_label = label
            index = 2
            while display_label in routes and routes[display_label] != route:
                display_label = f"{label} ({index})"
                index += 1
            routes[display_label] = route
            # Preserve semantic button styling when callback actions are bridged
            # from the inline route definition into a native Reply Keyboard.
            button_style = getattr(button, "style", None)
            converted.append(KeyboardButton(display_label, style=button_style))

        if retained_inline:
            inline_rows.append(retained_inline)
        if converted:
            # Three buttons per row keep mobile layouts compact and scannable.
            reply_rows.extend(
                converted[index:index + 3]
                for index in range(0, len(converted), 3)
            )

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
    inline_markup = InlineKeyboardMarkup(inline_rows) if inline_rows else None
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
        logger.debug("Could not remove previous Reply Keyboard anchor: %s", exc)


async def publish_reply_keyboard(
    bot: Any,
    bot_data: dict[str, Any] | None,
    chat_id: Any,
    markup: ReplyKeyboardMarkup,
    routes: dict[str, dict[str, str]],
    target_message: Any,
) -> Any:
    """Display a Reply Keyboard without adding a visible cue message.

    Telegram allows only one keyboard type per message. A zero-width
    placeholder lets the keyboard stay separate from an existing Inline
    Keyboard message without adding an arrow or another visible text line.
    """
    await remove_previous_keyboard_anchor(bot, bot_data, chat_id)
    clear_reply_keyboard_routes(bot_data, chat_id)
    anchor = await bot.send_message(
        chat_id=chat_id,
        text="\u2063",
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


async def _delete_private_keyboard_tap(message: Any, context: Any, chat_id: Any) -> None:
    """Remove a private-chat button tap while preserving fallback cleanup."""
    message_id = getattr(message, "message_id", None)
    if message_id is None:
        return

    chat_cleanup.track(context.application.bot_data, chat_id, message_id)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as exc:
        # Deletion is best-effort; leave it tracked for a later wizard cleanup.
        logger.debug(
            "Could not remove Reply Keyboard tap %s in %s: %s",
            message_id,
            chat_id,
            exc,
        )
    else:
        chat_cleanup.untrack(context.application.bot_data, chat_id, message_id)


async def handle_reply_keyboard_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Dispatch exact Reply Keyboard choices through the existing callback flows."""
    message = update.message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or not message.text:
        return
    entry = get_reply_keyboard_entry(context.bot_data, chat.id)
    route = entry.get("routes", {}).get(message.text) if entry else None
    if not route:
        # Normal wizard text/media input continues to the existing handlers.
        return
    # Group /start exposes only the deep link as a Reply Keyboard URL choice.
    if not is_private_chat(chat.id) and route["type"] != "url":
        return

    # Keep group URL taps available for diagnostics. In private chats, a
    # Reply Keyboard selection is just a normal user message; remove callback
    # taps so the conversation stays focused on the active bot screen.
    if not is_private_chat(chat.id):
        chat_cleanup.track(context.application.bot_data, chat.id, message.message_id)

    if route["type"] == "url":
        if is_private_chat(chat.id):
            await _delete_private_keyboard_tap(message, context, chat.id)
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

    if is_private_chat(chat.id):
        await _delete_private_keyboard_tap(message, context, chat.id)

    query = _ReplyKeyboardCallbackQuery(
        id=f"reply-keyboard-{update.update_id}",
        from_user=user,
        chat_instance=str(chat.id),
        message=target_message,
        data=route["data"],
    )
    # A synthetic CallbackQuery is not deserialized from Telegram's API
    # payload, so PTB cannot attach the originating Bot automatically.
    # Existing callbacks rely on query.get_bot() / query.bot in wizard and
    # group-permission flows. Bind the real application bot before the update
    # is queued so those handlers behave exactly like native callback queries.
    query.set_bot(context.bot)
    synthetic_update = Update(update_id=update.update_id, callback_query=query)
    try:
        # Do not call process_update() from inside another update handler.
        # PTB processes updates sequentially by default; recursively entering
        # its update processor can block on the same concurrency semaphore.
        # Queue the synthetic callback so the normal dispatcher handles it
        # after this Reply Keyboard message handler has returned.
        await context.application.update_queue.put(synthetic_update)
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
