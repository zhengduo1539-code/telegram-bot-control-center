"""Telegram chat permission checks used before creating scheduled content."""
from __future__ import annotations
from telegram import Bot
from telegram.error import Forbidden, TelegramError


class ChatAccessError(ValueError):
    """Raised when the requester or bot cannot publish to the selected chat."""


async def ensure_chat_access(bot: Bot, chat_id: int, user_id: int):
    """Verify the chat exists, requester is an admin, and the bot can publish."""
    try:
        chat = await bot.get_chat(chat_id)
        requester = await bot.get_chat_member(chat_id, user_id)
        bot_member = await bot.get_chat_member(chat_id, bot.id)
    except (Forbidden, TelegramError) as exc:
        raise ChatAccessError(
            "I couldn't verify this chat. Check its Chat ID and make sure the bot is a member."
        ) from exc
    if requester.status not in ("administrator", "creator"):
        raise ChatAccessError("Only a group administrator or channel owner can create schedules here.")
    if chat.type == "channel":
        if bot_member.status not in ("administrator", "creator"):
            raise ChatAccessError("The bot must be a channel administrator before it can publish posts.")
        if getattr(bot_member, "can_post_messages", True) is False:
            raise ChatAccessError("The bot is missing the 'Post Messages' permission in this channel.")
    else:
        if bot_member.status in ("left", "kicked"):
            raise ChatAccessError("The bot is not a member of this group. Add it and try again.")
        if bot_member.status == "restricted" and not getattr(bot_member, "can_send_messages", False):
            raise ChatAccessError("The bot is restricted and cannot send messages in this group.")
    return chat
