"""EmojiTitleBot-style Telegram Custom Animated Emoji generator.

Free, quota-free implementation: no MongoDB, subscriptions, invoices, Stars,
PRO branches, or payment handlers. FastAPI is used only for Render health probes;
Telegram updates are received through long polling.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, replace
from io import BytesIO
from urllib.parse import quote

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaAnimation,
    InputSticker,
    Sticker,
    Update,
)
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from naming import make_pack_name
from emoji_engine import (
    ANIMATION_ICONS,
    ANIMATION_OPTIONS,
    COLOR_ICONS,
    COLOR_OPTIONS,
    FONT_OPTIONS,
    PALETTES,
    SHAPE_ICONS,
    SHAPE_OPTIONS,
    Style,
    generate_pack,
    render_preview_gif,
)
from app.config import Settings
from app.storage.pack_registry import PackRegistry
from app.telegram.sticker_api import TelegramStickerPublisher

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
# httpx request URLs contain the bot token in the path; never log them at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("emoji_title_bot")
SERVICE_NAME = "Animated Custom Emoji & Sticker Generator"
CALLBACK_PREFIX = "e"
MAX_TEXT_LENGTH = 20


@dataclass(slots=True)
class EditorSession:
    token: str
    owner_id: int
    chat_id: int
    message_id: int | None
    style: Style
    created_at: float
    lock: asyncio.Lock
    pack_type: str = "custom_emoji"
    creating: bool = False


class BotService:
    def __init__(self) -> None:
        self.settings = Settings.from_env()
        self.token = self.settings.bot_token
        self.status = "not_configured"
        self.status_reason = "Set BOT_TOKEN in the Render environment to enable Telegram polling."
        self.application: Application | None = None
        self.bot_username = ""
        self.sessions: dict[str, EditorSession] = {}
        self.last_styles: dict[int, Style] = {}
        self.ready = False
        self.publisher = TelegramStickerPublisher()
        self.pack_registry = PackRegistry(self.settings.database_path)
        self.render_semaphore = asyncio.Semaphore(self.settings.max_concurrent_renders)

    async def start(self) -> None:
        if not self.token:
            self.status = "not_configured"
            logger.warning("Telegram polling disabled: BOT_TOKEN is not configured.")
            return
        try:
            application = ApplicationBuilder().token(self.token).build()
            self.application = application
            application.bot_data["service"] = self
            application.add_handler(CommandHandler("start", self.cmd_start))
            application.add_handler(CommandHandler("help", self.cmd_help))
            application.add_handler(CommandHandler("cancel", self.cmd_cancel))
            application.add_handler(CallbackQueryHandler(self.on_callback, pattern=r"^e:"))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
            await application.initialize()
            me = await application.bot.get_me()
            self.bot_username = me.username or ""
            await application.bot.set_my_commands([
                BotCommand("start", "Start the Custom Emoji generator"),
                BotCommand("help", "How to create animated custom emojis"),
                BotCommand("cancel", "Cancel the current editor"),
            ])
            await application.start()
            if application.updater is None:
                raise RuntimeError("Telegram Updater is unavailable; polling cannot start.")
            await application.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
            self.ready = True
            self.status = "ok"
            self.status_reason = "Telegram long polling is running."
            logger.info("Telegram polling started for @%s", self.bot_username)
        except Exception as exc:
            self.ready = False
            self.status = "error"
            self.status_reason = f"Telegram startup failed: {type(exc).__name__}"
            logger.exception("Failed to start Telegram polling")
            await self.stop()

    async def stop(self) -> None:
        app = self.application
        self.ready = False
        if not app:
            return
        try:
            if app.updater and app.updater.running:
                await app.updater.stop()
        except Exception:
            logger.exception("Error stopping Telegram updater")
        try:
            if app.running:
                await app.stop()
        except Exception:
            logger.exception("Error stopping Telegram application")
        try:
            await app.shutdown()
        except Exception:
            logger.exception("Error shutting down Telegram application")
        self.application = None

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message:
            return
        text = (
            "👋 Hello! I turn short text into animated Telegram Custom Emojis.\n\n"
            "Send me a word, name, or short phrase to open the editor.\n"
            "Choose a font, color, shape, and animation, preview the result, then create your pack.\n\n"
            "No bot subscription, quota, or payment is required. Telegram's own Premium rules for using custom emojis still apply."
        )
        await update.effective_message.reply_text(text)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(
                "How it works:\n"
                "1. Send text (up to 20 characters).\n"
                "2. Use the inline arrows to change the font, color, shape, and animation.\n"
                "3. Watch the updated GIF preview.\n"
                "4. Tap ✅ Create Custom Emoji Pack.\n\n"
                "The bot creates a Telegram custom-emoji set using animated .tgs vector files. "
                "It has no PRO plan, payment handlers, or daily creation limit."
            )

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not update.effective_message:
            return
        for token, session in list(self.sessions.items()):
            if session.owner_id == user.id:
                self.sessions.pop(token, None)
        await update.effective_message.reply_text("Current editor sessions cleared. Send new text to start again.")

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if not message or not user or not message.text:
            return
        raw_text = " ".join(message.text.split())
        if not raw_text:
            await message.reply_text("Please send some text or a short phrase to animate.")
            return
        if len(raw_text) > MAX_TEXT_LENGTH:
            await message.reply_text(
                f"Please keep the text to {MAX_TEXT_LENGTH} characters or fewer so it stays readable in the emoji."
            )
            return

        previous = self.last_styles.get(user.id)
        style = replace(previous, text=raw_text).normalized() if previous else Style(text=raw_text).normalized()
        token = secrets.token_hex(4)
        session = EditorSession(
            token=token, owner_id=user.id, chat_id=message.chat_id,
            message_id=None, style=style, created_at=time.monotonic(), lock=asyncio.Lock(),
        )
        self.sessions[token] = session
        try:
            async with self.render_semaphore:
                gif = await asyncio.to_thread(render_preview_gif, style)
            preview = await message.reply_animation(
                animation=InputFile(BytesIO(gif), filename="custom-emoji-preview.gif"),
                caption=self._preview_caption(style, session.pack_type),
                reply_markup=self._keyboard(session),
                write_timeout=30,
            )
            session.message_id = preview.message_id
            self.last_styles[user.id] = style
            self._prune_sessions()
        except Exception:
            logger.exception("Could not create or send preview for user %s", user.id)
            await message.reply_text("I couldn't render the preview. Please try a shorter text or another font.")

    @staticmethod
    def _preview_caption(style: Style, pack_type: str = "custom_emoji") -> str:
        output_type = "Custom Emoji Pack" if pack_type == "custom_emoji" else "Animated Sticker Pack"
        return f"✨ {style.text}\nOutput: {output_type}\nChoose the font, color, shape, and animation for your text."

    def _keyboard(self, session: EditorSession, *, creating: bool = False) -> InlineKeyboardMarkup:
        style = session.style
        tok = session.token

        def button(label: str, field: str, action: str) -> InlineKeyboardButton:
            return InlineKeyboardButton(label, callback_data=f"{CALLBACK_PREFIX}:{tok}:{field}:{action}")

        rows = [
            [button("👉🏻", "f", "-"), button(style.font, "f", "+"), button("👉🏻", "f", "+")],
            [button("👉🏻", "c", "-"), button(COLOR_ICONS[style.color], "c", "+"), button("👉🏻", "c", "+")],
            [button("👉🏻", "s", "-"), button(SHAPE_ICONS[style.shape], "s", "+"), button("👉🏻", "s", "+")],
            [button("👉🏻", "a", "-"), button(ANIMATION_ICONS[style.animation], "a", "+"), button("👉🏻", "a", "+")],
        ]
        mode_label = "🪄 Output: Custom Emoji" if session.pack_type == "custom_emoji" else "🎞️ Output: Animated Sticker"
        rows.append([button(mode_label, "m", "toggle")])
        create_label = "✅ Create Custom Emoji Pack" if session.pack_type == "custom_emoji" else "✅ Create Animated Sticker Pack"
        rows.append([
            InlineKeyboardButton(
                "⏳ Creating Pack…" if creating else create_label,
                callback_data=f"{CALLBACK_PREFIX}:{tok}:create:0",
            )
        ])
        return InlineKeyboardMarkup(rows)

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user = update.effective_user
        if not query or not user or not query.data:
            return
        try:
            _, token, field, action = query.data.split(":", 3)
        except ValueError:
            await query.answer("This button is invalid. Send your text again.", show_alert=True)
            return
        session = self.sessions.get(token)
        if not session:
            await query.answer("This editor has expired. Send your text again.", show_alert=True)
            return
        if session.owner_id != user.id:
            await query.answer("Only the person who opened this editor can use it.", show_alert=True)
            return

        if field == "create":
            if session.creating:
                await query.answer("Your Custom Animated Emoji Pack is already being created.")
                return
            session.creating = True
            await query.answer("Preparing your Custom Animated Emoji Pack…")
            await self._create_pack(query, session)
            return

        if field == "m":
            session.pack_type = "regular" if session.pack_type == "custom_emoji" else "custom_emoji"
            await query.answer("Output mode changed.")
            try:
                await query.edit_message_caption(
                    caption=self._preview_caption(session.style, session.pack_type),
                    reply_markup=self._keyboard(session),
                )
            except TelegramError:
                logger.exception("Could not update output mode")
            return

        options_by_field: dict[str, tuple[str, ...]] = {
            "f": FONT_OPTIONS, "c": COLOR_OPTIONS, "s": SHAPE_OPTIONS, "a": ANIMATION_OPTIONS,
        }
        style_field = {"f": "font", "c": "color", "s": "shape", "a": "animation"}.get(field)
        options = options_by_field.get(field)
        if not style_field or not options:
            await query.answer("Unknown setting.", show_alert=True)
            return
        async with session.lock:
            current = getattr(session.style, style_field)
            current_index = options.index(current) if current in options else 0
            delta = -1 if action == "-" else 1
            selected = options[(current_index + delta) % len(options)]
            session.style = replace(session.style, **{style_field: selected}).normalized()
            self.last_styles[user.id] = session.style
            await query.answer("Updating preview…")
            try:
                async with self.render_semaphore:
                    gif = await asyncio.to_thread(render_preview_gif, session.style)
                await query.edit_message_media(
                    media=InputMediaAnimation(
                        media=InputFile(BytesIO(gif), filename="custom-emoji-preview.gif"),
                        caption=self._preview_caption(session.style, session.pack_type),
                    ),
                    reply_markup=self._keyboard(session),
                    read_timeout=30,
                    write_timeout=30,
                )
                session.message_id = query.message.message_id if query.message else session.message_id
            except BadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    logger.warning("Preview update was rejected: %s", exc)
                    if query.message:
                        await query.message.reply_text("Couldn't update the preview. Please try another setting.")
            except TelegramError:
                logger.exception("Telegram failed to update preview")
                if query.message:
                    await query.message.reply_text("Telegram couldn't update the preview. Please try again.")
            except Exception:
                logger.exception("Failed to render preview update")
                if query.message:
                    await query.message.reply_text("Preview render failed. Try another setting.")

    async def _generate_assets(self, style: Style, progress) -> list[bytes]:
        async with self.render_semaphore:
            return await asyncio.to_thread(generate_pack, style, progress)

    async def _create_pack(self, query, session: EditorSession) -> None:
        if not self.application or not self.bot_username:
            session.creating = False
            await query.message.reply_text("The Telegram bot is not ready yet. Please try again shortly.")
            return
        async with session.lock:
            status = await query.message.reply_text("⏳ Preparing your Custom Animated Emoji Pack…")
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[str] = asyncio.Queue()

            def progress(text: str) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, text)

            task = asyncio.create_task(self._generate_assets(session.style, progress))
            last_text = ""
            last_edit = 0.0
            try:
                while not task.done():
                    try:
                        progress_text = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    now = time.monotonic()
                    if progress_text != last_text and now - last_edit >= 0.8:
                        try:
                            await status.edit_text("⏳ " + progress_text)
                            last_text, last_edit = progress_text, now
                        except TelegramError:
                            pass
                assets = await task
                await status.edit_text("📦 Uploading your animated pack to Telegram…")
                published = await self.publisher.create_pack(
                    bot=self.application.bot,
                    owner_id=session.owner_id,
                    bot_username=self.bot_username,
                    text=session.style.text,
                    assets=assets,
                    pack_type=session.pack_type,
                )
                pack_name = published.name
                pack_link = published.link
                if self.pack_registry.enabled:
                    try:
                        await asyncio.to_thread(
                            self.pack_registry.record,
                            user_id=session.owner_id,
                            pack_name=pack_name,
                            pack_type=session.pack_type,
                            display_text=session.style.text,
                        )
                    except Exception:
                        # Telegram publication has succeeded; optional registry failures
                        # must not report a false failure to the user.
                        logger.exception("Could not save optional pack registry record")
                pack_kind = "Custom Animated Emoji Pack" if session.pack_type == "custom_emoji" else "Animated Sticker Pack"
                add_label = "➕ Add Custom Emojis" if session.pack_type == "custom_emoji" else "➕ Add Animated Stickers"
                share_url = "https://t.me/share/url?url=" + quote(pack_link, safe="") + "&text=" + quote(
                    f"Check out my {pack_kind}: {session.style.text}", safe=""
                )
                result_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(add_label, url=pack_link)],
                    [InlineKeyboardButton("↗️ Share Pack", url=share_url)],
                ])
                await status.edit_text(f"✅ {pack_kind} created successfully!")
                await query.message.reply_text(
                    f"🎉 Your {pack_kind} has been created!\n\n"
                    "Open the link to add the pack to Telegram. Account-level availability rules for custom emojis are controlled by Telegram.\n\n"
                    f"🔗 Pack link: {pack_link}\n\n"
                    "This bot has no PRO subscription, payment requirement, or daily creation quota.",
                    reply_markup=result_keyboard,
                    disable_web_page_preview=True,
                )
                self.last_styles[session.owner_id] = session.style
                self.sessions.pop(session.token, None)
            except Exception as exc:
                session.creating = False
                logger.exception("Failed to create custom emoji pack for user %s", session.owner_id)
                try:
                    await status.edit_text(
                        "⚠️ I couldn't publish the Custom Animated Emoji Pack.\n\n"
                        f"Reason: {self._safe_error(exc)}\n\n"
                        "Your text and selected settings are preserved. Please tap Create again or try a shorter text."
                    )
                except TelegramError:
                    pass

    def _pack_name(self, text: str) -> str:
        return make_pack_name(text, self.bot_username)

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc).replace("\n", " ").strip()
        # Never echo token-bearing Telegram API URLs to users or logs in error payloads.
        message = re.sub(r"https?://api\.telegram\.org/bot[^\s]+", "Telegram API request", message)
        return (message or type(exc).__name__)[:260]

    def _prune_sessions(self) -> None:
        cutoff = time.monotonic() - 2 * 60 * 60
        for token, session in list(self.sessions.items()):
            if session.created_at < cutoff:
                self.sessions.pop(token, None)
        # Keep in-memory preferences bounded during high-volume use.
        if len(self.last_styles) > 5000:
            for user_id in list(self.last_styles)[:1000]:
                self.last_styles.pop(user_id, None)

