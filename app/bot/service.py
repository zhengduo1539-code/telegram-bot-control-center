"""Telegram bot for inspecting and rebuilding animated sticker assets."""
from __future__ import annotations

import asyncio
import html
import logging
import re
import secrets
from io import BytesIO
from typing import Any

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    MessageEntity,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.analysis.animation import AnimationError, analyze_animation, change_tgs_speed, recolor_tgs, replace_text_tgs
from app.config import Settings
from app.storage.mongo import MongoStore
from app.telegram.links import normalize_custom_emoji_id, parse_pack_link


logger = logging.getLogger(__name__)
SERVICE_NAME = "Animated Emoji Analyzer & Rebuilder"
MAX_PACK_STICKERS = 200
PACK_PAGE_SIZE = 10
MAX_CUSTOM_EMOJI_PER_MESSAGE = 10


def _sticker_record(sticker: Any) -> dict[str, Any]:
    return {
        "file_id": str(getattr(sticker, "file_id", "") or ""),
        "file_unique_id": str(getattr(sticker, "file_unique_id", "") or ""),
        "type": str(getattr(sticker, "type", "") or ""),
        "emoji": str(getattr(sticker, "emoji", "") or ""),
        "is_animated": bool(getattr(sticker, "is_animated", False)),
        "is_video": bool(getattr(sticker, "is_video", False)),
        "width": int(getattr(sticker, "width", 0) or 0),
        "height": int(getattr(sticker, "height", 0) or 0),
    }


def _format_hint(item: dict[str, Any]) -> str:
    if item.get("is_animated"):
        return "tgs"
    if item.get("is_video"):
        return "webm"
    return "webp"


def _report_text(report: dict[str, Any], asset_id: str) -> str:
    fmt = html.escape(str(report.get("format", "Unknown")))
    width, height = report.get("width"), report.get("height")
    lines = [
        "<b>Animation analysis complete</b>",
        f"Format: <code>{fmt}</code>",
        f"Dimensions: <code>{width or '—'} × {height or '—'}</code>",
    ]
    if report.get("frame_rate"):
        lines.append(f"Frame rate: <code>{report['frame_rate']} FPS</code>")
    if report.get("duration_seconds"):
        lines.append(f"Duration: <code>{report['duration_seconds']} s</code>")
    if report.get("frame_count") is not None:
        lines.append(f"Frames: <code>{report['frame_count']}</code>")
    if report.get("layer_count") is not None:
        lines.append(f"Layers: <code>{report['layer_count']}</code>")
    if report.get("shape_types"):
        shapes = ", ".join(f"{html.escape(str(k))}: {v}" for k, v in sorted(report["shape_types"].items()))
        lines.append(f"Shape types: {shapes}")
    if report.get("keyframe_count") is not None:
        lines.append(f"Keyframes: <code>{report['keyframe_count']}</code>")
    if report.get("effect_count") is not None:
        lines.append(f"Effect records: <code>{report['effect_count']}</code>")
    colors = report.get("colors") or []
    lines.append("Colors: " + (" ".join(f"<code>{html.escape(c)}</code>" for c in colors[:10]) if colors else "Not detected"))
    lines.append(f"Editable reconstruction: <b>{'available for TGS' if report.get('editable') else 'not available for this format'}</b>")
    if report.get("format") == "TGS":
        text_layer_count = int(report.get("editable_text_layer_count", 0) or 0)
        if text_layer_count == 1:
            source_text = ", ".join(html.escape(value) for value in report.get("text_contents", [])[:3]) or "available"
            lines.append(f"Editable text layer: <b>yes</b> (<code>{source_text}</code>)")
        elif text_layer_count > 1:
            lines.append(f"Editable text layers: <b>{text_layer_count}</b> — automatic replacement is disabled to avoid changing unrelated labels")
        else:
            lines.append("Editable text layer: <b>not found</b> — lettering may be vector outlines; exact text replacement needs vector reconstruction")
    if report.get("note"):
        lines.append(html.escape(str(report["note"])))
    if report.get("sampled_frames") is not None:
        lines.append(f"Sampled video frames: <code>{report['sampled_frames']}</code>")
    lines.append(f"Reference: <code>{asset_id}</code>")
    return "\n".join(lines)


class BotService:
    def __init__(self) -> None:
        self.settings = Settings.from_env()
        self.status = "not_started"
        self.mode = "disabled"
        self.reason = "Configure BOT_TOKEN and MONGODB_URI to enable the bot."
        self.ready = False
        self.webhook_secret = ""
        self.application: Application | None = None
        self.store: MongoStore | None = None
        self._processing_semaphore = asyncio.Semaphore(3)

    async def start(self) -> None:
        if not self.settings.bot_token:
            self.status, self.reason = "missing_bot_token", "BOT_TOKEN is not configured."
            logger.warning("Bot disabled: BOT_TOKEN is not configured.")
            return
        if not self.settings.mongodb_uri:
            self.status, self.reason = "missing_mongodb_uri", "MONGODB_URI is required before the bot can start."
            logger.warning("Bot disabled: MONGODB_URI is not configured.")
            return

        self.store = MongoStore(self.settings.mongodb_uri, self.settings.mongodb_database)
        try:
            await self.store.connect()
            application = Application.builder().token(self.settings.bot_token).build()
            self._register_handlers(application)
            await application.initialize()
            await application.start()
            self.application = application
            if self.settings.public_base_url:
                self.webhook_secret = self.settings.webhook_secret or secrets.token_urlsafe(32)
                await application.bot.set_webhook(
                    url=f"{self.settings.public_base_url}/telegram/webhook",
                    secret_token=self.webhook_secret,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                )
                self.mode = "webhook"
            else:
                if application.updater is None:
                    raise RuntimeError("Telegram updater is unavailable for local polling mode.")
                await application.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                )
                self.mode = "polling"
            await application.bot.set_my_commands(
                [
                    BotCommand("start", "Start the analyzer"),
                    BotCommand("help", "How to analyze emoji and packs"),
                    BotCommand("status", "Show bot readiness"),
                    BotCommand("recent", "Show recent analysis reports"),
                    BotCommand("id", "Analyze a custom animated emoji ID"),
                    BotCommand("cancel", "Cancel the current edit"),
                ]
            )
            self.status, self.reason, self.ready = "ready", "MongoDB and Telegram are connected.", True
            logger.info("Emoji analyzer started in %s mode; database=%s", self.mode, self.settings.mongodb_database)
        except Exception:
            self.status, self.reason, self.ready = "startup_failed", "Startup failed; inspect sanitized service logs.", False
            logger.exception("Emoji analyzer startup failed.")
            await self._cleanup_after_failed_start()
            raise

    def _register_handlers(self, app: Application) -> None:
        app.add_handler(CommandHandler("start", self._start_command))
        app.add_handler(CommandHandler("help", self._help_command))
        app.add_handler(CommandHandler("status", self._status_command))
        app.add_handler(CommandHandler("recent", self._recent_command))
        app.add_handler(CommandHandler("id", self._id_command))
        app.add_handler(CommandHandler("cancel", self._cancel_command))
        app.add_handler(CallbackQueryHandler(self._callback, pattern=r"^(page|item|original|export|color|speed|text):"))
        app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self._message))
        app.add_error_handler(self._error_handler)

    async def _cleanup_after_failed_start(self) -> None:
        if self.application is not None:
            try:
                if self.application.updater and self.application.updater.running:
                    await self.application.updater.stop()
                if self.application.running:
                    await self.application.stop()
                await self.application.shutdown()
            except Exception:
                logger.exception("Failed to clean up partially started Telegram application.")
            self.application = None
        if self.store is not None:
            try:
                await self.store.close()
            except Exception:
                logger.exception("Failed to close MongoDB after startup error.")
            self.store = None

    async def stop(self) -> None:
        self.ready = False
        if self.application is not None:
            try:
                if self.application.updater and self.application.updater.running:
                    await self.application.updater.stop()
                if self.application.running:
                    await self.application.stop()
                await self.application.shutdown()
            except Exception:
                logger.exception("Telegram application shutdown failed.")
            self.application = None
        if self.store is not None:
            await self.store.close()
            self.store = None
        if self.status == "ready":
            self.status, self.reason = "stopped", "Application stopped."

    async def process_webhook(self, payload: dict[str, Any]) -> None:
        if not self.ready or self.application is None:
            raise RuntimeError("Telegram bot is not ready.")
        async with self._processing_semaphore:
            update = Update.de_json(payload, self.application.bot)
            await self.application.process_update(update)

    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if not user or not message:
            return
        await self._touch_user(user.id, user.username)
        await message.reply_text(
            "👋 <b>Animated Emoji Analyzer & Rebuilder</b>\n\n"
            "Send a Telegram custom animated emoji, a sticker, a <code>t.me/addemoji/…</code> "
            "or <code>t.me/addstickers/…</code> link, or upload a .TGS/.WEBM file.\n\n"
            "I inspect animation metadata, colors, vector shape types, timing and keyframes where available. "
            "TGS files can be recolored or speed-adjusted when the result satisfies Telegram's constraints.",
            parse_mode="HTML",
        )

    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message:
            await message.reply_text(
                "<b>How to use</b>\n"
                "1. Send an animated custom emoji or animated sticker.\n"
                "2. Paste an <code>https://t.me/addemoji/PACK</code> or "
                "<code>https://t.me/addstickers/PACK</code> link to browse a pack.\n"
                "3. Select an item to inspect it.\n"
                "4. Use <b>Original</b> to send it unchanged or <b>Export</b> to download the source file.\n"
                "5. TGS items also offer conservative color and speed edits.\n\n"
                "Only Telegram pack links are accepted; arbitrary external URLs are not downloaded. "
                "The bot stores analysis metadata and Telegram file IDs, not binary source files.",
                parse_mode="HTML",
            )

    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return
        await message.reply_text(
            f"Service: <code>{html.escape(self.status)}</code>\n"
            f"Mode: <code>{html.escape(self.mode)}</code>\n"
            f"Ready: <code>{str(self.ready).lower()}</code>\n"
            f"Details: {html.escape(self.reason)}",
            parse_mode="HTML",
        )

    async def _id_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if not user or not message:
            return
        if not self.store:
            await message.reply_text("The analyzer is not ready yet. Please try again later.")
            return
        if len(context.args) != 1:
            await message.reply_text(
                "Send a custom emoji ID like this:\n<code>/id 5368324170671202286</code>",
                parse_mode="HTML",
            )
            return
        emoji_id = normalize_custom_emoji_id(context.args[0])
        if not emoji_id:
            await message.reply_text(
                "That ID format is invalid. Use the numeric custom emoji ID, or send the animated emoji itself."
            )
            return
        await self._touch_user(user.id, user.username)
        await self._lookup_custom_emoji_id(message.chat_id, user.id, emoji_id, context)

    async def _recent_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        message = update.effective_message
        if not user or not message or not self.store:
            return
        records = await self.store.recent_assets(user.id)
        if not records:
            await message.reply_text("No recent analyses yet. Send an animated emoji or pack link to begin.")
            return
        lines = ["<b>Your recent analyses</b>"]
        for record in records:
            short_id = html.escape(str(record.get("_id", "")))
            fmt = html.escape(str(record.get("format", "unknown")))
            emoji = html.escape(str(record.get("emoji", "") or "🎞"))
            lines.append(f"{emoji} <code>{short_id}</code> — <code>{fmt}</code>")
        await message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data.pop("pending_edit", None)
        message = update.effective_message
        if message:
            await message.reply_text("Edit cancelled.")

    async def _message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if not message or not user or not self.store:
            return
        await self._touch_user(user.id, user.username)

        pending = context.user_data.get("pending_edit")
        if pending:
            await self._consume_edit(message, user.id, context, pending)
            return

        sticker = message.sticker
        if sticker:
            await self._inspect_item(
                chat_id=message.chat_id,
                user_id=user.id,
                item=_sticker_record(sticker),
                context=context,
                source="sent_sticker",
            )
            return

        document = message.document
        if document and (document.file_name or "").lower().endswith((".tgs", ".webm", ".webp")):
            record = {
                "file_id": document.file_id,
                "file_unique_id": document.file_unique_id,
                "type": "uploaded_file",
                "emoji": "",
                "is_animated": (document.file_name or "").lower().endswith(".tgs"),
                "is_video": (document.file_name or "").lower().endswith(".webm"),
                "width": 0,
                "height": 0,
                "file_name": document.file_name,
            }
            await self._inspect_item(message.chat_id, user.id, record, context, source="uploaded_file")
            return

        text = message.text or message.caption or ""
        link = parse_pack_link(text)
        if link:
            await self._handle_pack_link(message.chat_id, user.id, link, context)
            return

        raw_emoji_id = normalize_custom_emoji_id(text)
        if raw_emoji_id:
            await self._lookup_custom_emoji_id(message.chat_id, user.id, raw_emoji_id, context)
            return

        entities = list(message.entities or []) + list(message.caption_entities or [])
        custom_ids = list(dict.fromkeys(
            entity.custom_emoji_id
            for entity in entities
            if entity.type == MessageEntity.CUSTOM_EMOJI and entity.custom_emoji_id
        ))[:MAX_CUSTOM_EMOJI_PER_MESSAGE]
        if custom_ids:
            try:
                stickers = await context.bot.get_custom_emoji_stickers(custom_emoji_ids=custom_ids)
            except TelegramError:
                logger.warning("Telegram could not resolve a custom emoji for user_id=%s", user.id)
                await message.reply_text("I couldn't retrieve that custom emoji. It may no longer be available.")
                return
            items = [_sticker_record(item) for item in stickers if getattr(item, "file_id", "")]
            if not items:
                await message.reply_text("Telegram returned no file for that custom emoji.")
            elif len(items) == 1:
                await self._inspect_item(message.chat_id, user.id, items[0], context, source="custom_emoji_id")
            else:
                await self._create_selection(message.chat_id, user.id, items, "Custom emojis from your message", "custom_emoji", context)
            return

        if text.strip():
            await message.reply_text(
                "Send a custom animated emoji, an animated sticker, a TGS/WEBM file, or a Telegram pack link. "
                "Use /help for details."
            )

    async def _lookup_custom_emoji_id(
        self, chat_id: int, user_id: int, emoji_id: str, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        try:
            stickers = await context.bot.get_custom_emoji_stickers(custom_emoji_ids=[emoji_id])
        except TelegramError:
            logger.warning("Telegram could not resolve custom emoji ID for user_id=%s", user_id)
            await context.bot.send_message(
                chat_id=chat_id,
                text="Telegram could not resolve that ID. Confirm it is a custom_emoji_id, not a file_id or file_unique_id.",
            )
            return
        items = [_sticker_record(sticker) for sticker in stickers if getattr(sticker, "file_id", "")]
        if not items:
            await context.bot.send_message(chat_id=chat_id, text="Telegram returned no item for that custom emoji ID.")
            return
        await self._inspect_item(chat_id, user_id, items[0], context, source="custom_emoji_id")

    async def _handle_pack_link(self, chat_id: int, user_id: int, link: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            sticker_set = await context.bot.get_sticker_set(name=link.short_name)
        except TelegramError:
            await context.bot.send_message(
                chat_id=chat_id,
                text="I couldn't load that pack. Check the link, visibility, and pack name, then try again.",
            )
            return
        sticker_objects = list(sticker_set.stickers or [])[:MAX_PACK_STICKERS]
        items = [_sticker_record(sticker) for sticker in sticker_objects if getattr(sticker, "file_id", "")]
        if not items:
            await context.bot.send_message(chat_id=chat_id, text="That pack contains no items I can inspect.")
            return
        kind = "custom_emoji" if link.kind == "custom_emoji" else "stickers"
        await self._create_selection(chat_id, user_id, items, sticker_set.title or link.short_name, kind, context, link.short_name)

    async def _create_selection(
        self,
        chat_id: int,
        user_id: int,
        items: list[dict[str, Any]],
        title: str,
        kind: str,
        context: ContextTypes.DEFAULT_TYPE,
        short_name: str = "custom-emoji",
    ) -> None:
        if not self.store:
            return
        session_id = secrets.token_hex(5)
        await self.store.save_pack_session(session_id, user_id, short_name, title, kind, items)
        page_count = (len(items) + PACK_PAGE_SIZE - 1) // PACK_PAGE_SIZE
        text = (
            f"<b>{html.escape(title[:128])}</b>\n"
            f"Items available: <code>{len(items)}</code>\n"
            f"Page <code>1/{page_count}</code>\n\n"
            "Select an item to inspect its format, colors, motion metadata and editable properties."
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=self._page_keyboard(session_id, items, 0),
        )

    def _page_keyboard(self, session_id: str, items: list[dict[str, Any]], page: int) -> InlineKeyboardMarkup:
        start = page * PACK_PAGE_SIZE
        end = min(start + PACK_PAGE_SIZE, len(items))
        rows: list[list[InlineKeyboardButton]] = []
        for index in range(start, end, 2):
            row: list[InlineKeyboardButton] = []
            for item_index in range(index, min(index + 2, end)):
                item = items[item_index]
                emoji = str(item.get("emoji") or "🎞")
                row.append(InlineKeyboardButton(f"{emoji[:4]} {item_index + 1}", callback_data=f"item:{session_id}:{item_index}"))
            rows.append(row)
        page_count = max(1, (len(items) + PACK_PAGE_SIZE - 1) // PACK_PAGE_SIZE)
        if page_count > 1:
            nav: list[InlineKeyboardButton] = []
            if page > 0:
                nav.append(InlineKeyboardButton("‹ Previous", callback_data=f"page:{session_id}:{page - 1}"))
            if page + 1 < page_count:
                nav.append(InlineKeyboardButton("Next ›", callback_data=f"page:{session_id}:{page + 1}"))
            if nav:
                rows.append(nav)
        return InlineKeyboardMarkup(rows)

    async def _callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user = update.effective_user
        if not query or not user or not query.data:
            return
        await query.answer()
        parts = query.data.split(":")
        action = parts[0]

        if action == "page" and len(parts) == 3 and self.store:
            session = await self.store.get_pack_session(parts[1], user.id)
            if not session:
                await query.answer("This selection expired. Send the pack link again.", show_alert=True)
                return
            try:
                page = int(parts[2])
                items = session.get("items", [])
                if page < 0 or page >= max(1, (len(items) + PACK_PAGE_SIZE - 1) // PACK_PAGE_SIZE):
                    raise ValueError
            except (ValueError, TypeError):
                await query.answer("Invalid page.", show_alert=True)
                return
            try:
                await query.edit_message_text(
                    f"<b>{html.escape(str(session.get('title', 'Sticker pack')))}</b>\n"
                    f"Items available: <code>{len(items)}</code>\n"
                    f"Page <code>{page + 1}/{max(1, (len(items) + PACK_PAGE_SIZE - 1) // PACK_PAGE_SIZE)}</code>\n\n"
                    "Select an item to inspect it.",
                    parse_mode="HTML",
                    reply_markup=self._page_keyboard(parts[1], items, page),
                )
            except TelegramError:
                logger.info("Could not update pack pagination for user_id=%s", user.id)
            return

        if action == "item" and len(parts) == 3 and self.store:
            session = await self.store.get_pack_session(parts[1], user.id)
            if not session:
                await query.answer("This selection expired. Send the pack link again.", show_alert=True)
                return
            try:
                index = int(parts[2])
                item = session["items"][index]
            except (ValueError, TypeError, IndexError, KeyError):
                await query.answer("That item is not available.", show_alert=True)
                return
            if query.message:
                await self._inspect_item(query.message.chat_id, user.id, item, context, source=str(session.get("kind", "pack")))
            return

        if action in {"original", "export", "color", "speed", "text"} and len(parts) == 2 and self.store:
            asset = await self.store.get_asset(parts[1], user.id)
            if not asset:
                await query.answer("The analysis expired or belongs to another user.", show_alert=True)
                return
            if not query.message:
                return
            chat_id = query.message.chat_id
            if action == "original":
                try:
                    await context.bot.send_sticker(chat_id=chat_id, sticker=asset["file_id"])
                except TelegramError:
                    await query.message.reply_text("Telegram could not resend this original file. Try Export to download it.")
                return
            if action == "export":
                await self._export_asset(chat_id, user.id, asset, context)
                return
            if asset.get("format") != "TGS":
                await query.answer("Editing is currently supported for TGS vector animations only.", show_alert=True)
                return
            if action == "text" and not asset.get("report", {}).get("text_replace_supported"):
                await query.message.reply_text(
                    "This animation has no single editable text layer. Vector-outlined letters need reconstruction instead."
                )
                return
            context.user_data["pending_edit"] = {"asset_id": parts[1], "mode": action}
            if action == "color":
                prompt = "Send the new hex color, for example <code>#FF8800</code>."
            elif action == "speed":
                prompt = "Send a speed multiplier from <code>0.5</code> to <code>2.0</code> (for example <code>1.5</code>)."
            else:
                prompt = "Send replacement text (1–20 characters). The original animation timing and style properties will be preserved when the TGS has an editable text layer. Use /cancel to stop."
            await query.message.reply_text(prompt, parse_mode="HTML")
            return

    async def _inspect_item(
        self,
        chat_id: int,
        user_id: int,
        item: dict[str, Any],
        context: ContextTypes.DEFAULT_TYPE,
        source: str,
    ) -> None:
        if not self.store or not item.get("file_id"):
            return
        try:
            data = await self._download_file(str(item["file_id"]), context)
            report = await asyncio.to_thread(analyze_animation, data, _format_hint(item))
            asset_id = secrets.token_hex(5)
            await self.store.save_asset(
                asset_id=asset_id,
                user_id=user_id,
                file_id=str(item["file_id"]),
                file_unique_id=str(item.get("file_unique_id") or ""),
                source=source,
                format_name=str(report.get("format", "unknown")),
                report=report,
                emoji=str(item.get("emoji") or ""),
            )
        except AnimationError as exc:
            await context.bot.send_message(chat_id=chat_id, text=f"Could not analyze this animation: {html.escape(str(exc))}", parse_mode="HTML")
            return
        except (TelegramError, OSError, ValueError):
            logger.warning("Could not download or inspect asset for user_id=%s", user_id, exc_info=True)
            await context.bot.send_message(chat_id=chat_id, text="I couldn't download that file. It may be unavailable, too large, or no longer accessible.")
            return

        buttons = [
            [
                InlineKeyboardButton("▶ Send original", callback_data=f"original:{asset_id}"),
                InlineKeyboardButton("⇩ Export file", callback_data=f"export:{asset_id}"),
            ]
        ]
        if report.get("format") == "TGS" and report.get("editable"):
            buttons.append(
                [
                    InlineKeyboardButton("🎨 Recolor TGS", callback_data=f"color:{asset_id}"),
                    InlineKeyboardButton("⏱ Adjust speed", callback_data=f"speed:{asset_id}"),
                ]
            )
            if report.get("text_replace_supported"):
                buttons.append([
                    InlineKeyboardButton("✍ Replace text", callback_data=f"text:{asset_id}")
                ])
        await context.bot.send_message(
            chat_id=chat_id,
            text=_report_text(report, asset_id),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _download_file(self, file_id: str, context: ContextTypes.DEFAULT_TYPE) -> bytes:
        file = await context.bot.get_file(file_id)
        reported_size = getattr(file, "file_size", None)
        if reported_size and reported_size > self.settings.max_download_bytes:
            raise AnimationError("The file exceeds the configured download limit.")
        buffer = BytesIO()
        await file.download_to_memory(out=buffer)
        data = buffer.getvalue()
        if not data or len(data) > self.settings.max_download_bytes:
            raise AnimationError("The downloaded file is empty or exceeds the configured download limit.")
        return data

    async def _export_asset(
        self,
        chat_id: int,
        user_id: int,
        asset: dict[str, Any],
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        try:
            data = await self._download_file(str(asset["file_id"]), context)
            ext = {"TGS": "tgs", "WEBM": "webm", "WEBP": "webp"}.get(str(asset.get("format")), "bin")
            filename = f"animation_{asset['_id']}.{ext}"
            await context.bot.send_document(
                chat_id=chat_id,
                document=InputFile(BytesIO(data), filename=filename),
                caption="Original source file. The binary file is delivered through Telegram and is not stored in MongoDB.",
            )
        except (TelegramError, AnimationError, OSError):
            logger.warning("Could not export asset for user_id=%s", user_id, exc_info=True)
            await context.bot.send_message(chat_id=chat_id, text="Could not export this original file. It may have expired or become unavailable.")

    async def _consume_edit(
        self,
        message: Any,
        user_id: int,
        context: ContextTypes.DEFAULT_TYPE,
        pending: dict[str, Any],
    ) -> None:
        if not self.store:
            return
        asset_id = str(pending.get("asset_id", ""))
        mode = str(pending.get("mode", ""))
        text = (message.text or "").strip()
        if not text:
            await message.reply_text("Please send the requested text value, or use /cancel to stop editing.")
            return
        if text.lower() == "/cancel":
            context.user_data.pop("pending_edit", None)
            await message.reply_text("Edit cancelled.")
            return
        asset = await self.store.get_asset(asset_id, user_id)
        if not asset or asset.get("format") != "TGS":
            context.user_data.pop("pending_edit", None)
            await message.reply_text("That source is no longer available. Analyze it again to create a fresh edit session.")
            return
        try:
            data = await self._download_file(str(asset["file_id"]), context)
            if mode == "color":
                output = await asyncio.to_thread(recolor_tgs, data, text)
                suffix = "recolored"
            elif mode == "text":
                output = await asyncio.to_thread(replace_text_tgs, data, text)
                suffix = "text-replaced"
            elif mode == "speed":
                try:
                    multiplier = float(text)
                except ValueError as exc:
                    raise AnimationError("Send a numeric multiplier from 0.5 to 2.0, such as 1.5.") from exc
                output = await asyncio.to_thread(change_tgs_speed, data, multiplier)
                suffix = "speed-adjusted"
            else:
                raise AnimationError("Unknown edit action.")
        except AnimationError as exc:
            await message.reply_text(f"Edit could not be applied: {html.escape(str(exc))}", parse_mode="HTML")
            return
        except (TelegramError, OSError):
            logger.warning("Could not load TGS source for edit; user_id=%s", user_id, exc_info=True)
            context.user_data.pop("pending_edit", None)
            await message.reply_text("The source file is no longer available. Analyze it again to retry.")
            return

        context.user_data.pop("pending_edit", None)
        output_file = InputFile(BytesIO(output), filename=f"emoji_{asset_id}_{suffix}.tgs")
        if mode == "text":
            try:
                await context.bot.send_sticker(chat_id=message.chat_id, sticker=output_file)
                await message.reply_text(
                    "Text replaced. The original timeline, animation properties and text styling were retained. "
                    "The source file was updated; the sticker pack itself was not modified."
                )
                return
            except TelegramError:
                logger.warning("Telegram rejected the edited TGS as a sticker; sending source as a document.")
        await message.reply_document(
            document=InputFile(BytesIO(output), filename=f"emoji_{asset_id}_{suffix}.tgs"),
            caption=(
                "Edited TGS source. The original pack is unchanged; use Telegram's sticker/emoji workflow to publish it."
            ),
        )

    async def _touch_user(self, user_id: int, username: str | None) -> None:
        if self.store:
            await self.store.touch_user(user_id, username)

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Unhandled Telegram update error: %s", context.error, exc_info=context.error)

