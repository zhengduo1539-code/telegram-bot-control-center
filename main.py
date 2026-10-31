"""
Schedule Bot ASGI and Telegram application entry point.

Render runs this module with Uvicorn. The ASGI lifespan starts and stops the
Telegram application on Uvicorn's event loop; Flask health endpoints are
served through WsgiToAsgi on the same HTTP port.

For local development, python main.py remains supported and starts the health
server in a background thread before starting Telegram polling.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from asgiref.wsgi import WsgiToAsgi
from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import MONGO_URI, MONGODB_DATABASE, PORT, TELEGRAM_BOT_TOKEN
from database import (
    close_db,
    connect_db,
    record_chat_activity,
    remove_group,
    upsert_group,
    upsert_user,
)
from handlers import chat_cleanup
from handlers.base import build_base_conversation
from handlers.owner_panel import build_owner_panel
from handlers.queue_manager import build_media_pool, build_queue_manager
from handlers.schedule_wizard import build_schedule_wizard
from handlers.templates import build_templates_conversation
from keep_alive import app as health_app
from keep_alive import set_readiness, start_keep_alive
from scheduler import setup_scheduler

logger = logging.getLogger(__name__)

BOT_VERSION = "Schedule Bot | Daily Queue Engine | Permission Checks | 2026-10"
ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]

_scheduler = None
_bot_data_ref: dict | None = None


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with the running version for deployment verification."""
    if not update.message:
        return
    try:
        await update.message.reply_text("pong — " + BOT_VERSION)
    except Exception:
        logger.exception("cmd_ping failed")


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the current chat ID and the sender's Telegram user ID."""
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    from telegram.helpers import escape_markdown

    chat = update.effective_chat
    user = update.effective_user
    title = escape_markdown(chat.title or chat.first_name or "Private Chat", version=1)
    try:
        await update.message.reply_text(
            f"ℹ️ *Group Help*\n"
            f"{title}\n\n"
            f"Command: /id\n\n"
            f"*CHAT ID:* {chat.id}\n"
            f"*YOUR ID:* {user.id}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        logger.exception("cmd_id failed")


async def post_init(application: Application) -> None:
    """Connect MongoDB and start APScheduler on the bot event loop."""
    global _scheduler

    await connect_db(MONGO_URI, db_name=MONGODB_DATABASE)
    logger.info("MongoDB connected.")

    loop = asyncio.get_running_loop()
    _scheduler = setup_scheduler(application.bot, event_loop=loop)
    _scheduler.start()
    set_readiness(database=True, scheduler=True)
    logger.info("APScheduler started (loop id=%d).", id(loop))


async def _track_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record wizard messages and best-effort user/group activity."""
    chat = update.effective_chat
    message = update.effective_message
    if chat is not None and message is not None:
        chat_cleanup.track(context.application.bot_data, chat.id, message.message_id)

    user = update.effective_user
    if user is not None and not user.is_bot:
        try:
            await upsert_user(user.id, user.username, user.first_name)
        except Exception:
            logger.exception("User tracking failed for user_id=%s", user.id)

    if chat is not None and chat.type in ("group", "supergroup", "channel"):
        try:
            await upsert_group(chat.id, chat.title, chat.type)
        except Exception:
            logger.exception("Group tracking failed for chat_id=%s", chat.id)

        if message is not None and message.date is not None:
            try:
                await record_chat_activity(chat.id, message.date.hour)
            except Exception:
                logger.exception("Activity tracking failed for chat_id=%s", chat.id)


async def _on_bot_membership_change(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Track bot additions and remove a group's data after the bot leaves."""
    event = update.my_chat_member
    if event is None:
        return

    new_status = event.new_chat_member.status
    chat_id = event.chat.id

    if new_status in ("member", "administrator"):
        added_by = event.from_user.id if event.from_user else None
        try:
            await upsert_group(
                chat_id,
                event.chat.title or str(chat_id),
                event.chat.type,
                added_by_user_id=added_by,
            )
        except Exception:
            logger.exception("Could not record bot membership for chat %s", chat_id)

    elif new_status in ("kicked", "left"):
        try:
            counts = await remove_group(chat_id)
            logger.info(
                "Removed group data for chat %s: %s",
                chat_id,
                ", ".join(f"{key}={value}" for key, value in counts.items()),
            )
        except Exception:
            logger.exception("Could not clean removed group %s", chat_id)


class _TrackedBot(Bot):
    """Track outgoing messages so completed wizard conversations can clean them up."""

    async def send_message(self, chat_id=None, *args, **kwargs):
        message = await super().send_message(chat_id, *args, **kwargs)
        if chat_id is not None and message is not None and _bot_data_ref is not None:
            chat_cleanup.track(_bot_data_ref, chat_id, message.message_id)
        return message


async def post_shutdown(application: Application) -> None:
    """Stop APScheduler and release the MongoDB connection."""
    global _scheduler

    set_readiness(database=False, scheduler=False)
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped.")
    _scheduler = None
    await close_db()
    logger.info("MongoDB disconnected. Shutdown complete.")


def build_application() -> Application:
    """Build the Telegram application and register all handlers."""
    global _bot_data_ref

    application = (
        ApplicationBuilder()
        .bot(_TrackedBot(token=TELEGRAM_BOT_TOKEN))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    _bot_data_ref = application.bot_data

    application.add_handler(CommandHandler("ping", cmd_ping))
    application.add_handler(CommandHandler("id", cmd_id))
    application.add_handler(MessageHandler(filters.ALL, _track_incoming), group=-1)
    application.add_handler(CallbackQueryHandler(_track_incoming), group=-1)
    application.add_handler(
        ChatMemberHandler(
            _on_bot_membership_change,
            ChatMemberHandler.MY_CHAT_MEMBER,
        )
    )

    application.add_handler(build_schedule_wizard())
    application.add_handler(build_queue_manager())
    application.add_handler(build_media_pool())
    application.add_handler(build_owner_panel())
    application.add_handler(build_templates_conversation())
    application.add_handler(build_base_conversation())
    return application


class ScheduleBotASGI:
    """Serve health routes and manage Telegram polling via ASGI lifespan events."""

    def __init__(self) -> None:
        self._http_app = WsgiToAsgi(health_app)
        self._application: Application | None = None
        self._initialized = False
        self._post_init_attempted = False

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] == "http":
            await self._http_app(scope, receive, send)
        elif scope["type"] == "lifespan":
            await self._handle_lifespan(receive, send)
        else:
            raise RuntimeError(f"Unsupported ASGI scope type: {scope['type']}")

    async def _start_bot(self) -> None:
        application = build_application()
        self._application = application

        await application.initialize()
        self._initialized = True

        # Application.initialize() does not invoke post_init; do it explicitly
        # to preserve the setup sequence normally provided by run_polling().
        self._post_init_attempted = True
        await post_init(application)

        if application.updater is None:
            raise RuntimeError("Telegram polling updater is not configured.")

        await application.updater.start_polling(
            allowed_updates=ALLOWED_UPDATES,
            drop_pending_updates=True,
        )
        await application.start()
        logger.info("Schedule Bot started through the ASGI lifespan.")

    async def _stop_bot(self) -> None:
        application = self._application
        if application is None:
            return

        errors: list[Exception] = []

        if application.updater is not None and application.updater.running:
            try:
                await application.updater.stop()
            except Exception as exc:
                errors.append(exc)
                logger.exception("Failed to stop Telegram polling.")

        if application.running:
            try:
                await application.stop()
            except Exception as exc:
                errors.append(exc)
                logger.exception("Failed to stop Telegram application.")

        if self._post_init_attempted:
            try:
                await post_shutdown(application)
            except Exception as exc:
                errors.append(exc)
                logger.exception("Schedule Bot post-shutdown cleanup failed.")

        if self._initialized:
            try:
                await application.shutdown()
            except Exception as exc:
                errors.append(exc)
                logger.exception("Telegram application shutdown failed.")

        self._application = None
        self._initialized = False
        self._post_init_attempted = False

        if errors:
            raise RuntimeError(
                "One or more Schedule Bot shutdown steps failed."
            ) from errors[0]

    async def _handle_lifespan(self, receive, send) -> None:
        while True:
            message = await receive()

            if message["type"] == "lifespan.startup":
                try:
                    await self._start_bot()
                except Exception:
                    logger.exception("Schedule Bot failed to start.")
                    try:
                        await self._stop_bot()
                    except Exception:
                        logger.exception("Cleanup after startup failure was incomplete.")
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": "Schedule Bot startup failed; inspect Render logs.",
                        }
                    )
                    return

                await send({"type": "lifespan.startup.complete"})

            elif message["type"] == "lifespan.shutdown":
                try:
                    await self._stop_bot()
                except Exception:
                    logger.exception("Schedule Bot shutdown was incomplete.")
                    await send(
                        {
                            "type": "lifespan.shutdown.failed",
                            "message": "Schedule Bot shutdown was incomplete; inspect logs.",
                        }
                    )
                    return

                await send({"type": "lifespan.shutdown.complete"})
                return


# Uvicorn imports this ASGI object using the current Render service command.
app = ScheduleBotASGI()


def main() -> None:
    """Standalone local-development entry point."""
    logger.info("Starting %s", BOT_VERSION)
    start_keep_alive(PORT)
    build_application().run_polling(
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
