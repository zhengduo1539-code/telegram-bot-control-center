"""Telegram Animated Text Creator. FastAPI health endpoint + Telegram long polling."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputMediaPhoto, InputSticker, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from database import DAILY_LIMIT, UsageStore
from sticker_engine import ANIMATION_OPTIONS, FONT_OPTIONS, PALETTES, SHAPE_OPTIONS, Style, render_pack_webm, render_preview_png

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("animated_text_creator")
SERVICE_NAME = "Telegram Animated Text Creator"


def parse_admin_ids(value: str | None) -> set[int]:
    ids: set[int] = set()
    for token in re.split(r"[\s,;]+", value or ""):
        token = token.strip()
        if token.isdigit():
            ids.add(int(token))
    return ids


class BotService:
    def __init__(self) -> None:
        self.token = os.getenv("BOT_TOKEN", "").strip()
        self.mongo_uri = os.getenv("MONGODB_URI", "").strip()
        self.admin_ids = parse_admin_ids(os.getenv("ADMIN_IDS"))
        self.status = "not_configured"
        self.status_reason = "Set BOT_TOKEN, ADMIN_IDS and MONGODB_URI in Render Environment to enable the bot."
        self.store: UsageStore | None = None
        self.application: Application | None = None
        self.bot_username = ""
        self.sessions: dict[int, Style] = {}
        self.session_message_ids: dict[int, int] = {}
        self.locks: dict[int, asyncio.Lock] = {}

    async def start(self) -> None:
        missing = [key for key, value in (("BOT_TOKEN", self.token), ("MONGODB_URI", self.mongo_uri)) if not value]
        if not self.admin_ids:
            missing.append("ADMIN_IDS (at least one numeric Telegram user ID)")
        if missing:
            self.status = "not_configured"
            self.status_reason = "Missing required environment variable(s): " + ", ".join(missing)
            logger.warning("Bot polling not started; missing environment variables: %s", ", ".join(missing))
            return
        try:
            store = UsageStore(self.mongo_uri)
            self.store = store
            await asyncio.to_thread(store.initialize)
            application = ApplicationBuilder().token(self.token).build()
            self.application = application
            application.bot_data["service"] = self
            application.add_handler(CommandHandler("start", self.cmd_start))
            application.add_handler(CommandHandler("help", self.cmd_help))
            application.add_handler(CommandHandler("cancel", self.cmd_cancel))
            application.add_handler(CommandHandler("admin", self.cmd_admin))
            application.add_handler(CallbackQueryHandler(self.on_callback))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
            await application.initialize()
            me = await application.bot.get_me()
            self.bot_username = me.username or ""
            await application.bot.delete_webhook(drop_pending_updates=False)
            await application.start()
            if application.updater is None:
                raise RuntimeError("Telegram polling updater is unavailable.")
            await application.updater.start_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)
            self.status = "running"
            self.status_reason = ""
            logger.info("Telegram bot started successfully as @%s", self.bot_username)
        except Exception as exc:
            self.status = "error"
            self.status_reason = f"Bot startup failed ({type(exc).__name__}). Check Render application logs."
            logger.exception("Bot startup failed; health server will remain available")
            if self.store:
                await asyncio.to_thread(self.store.close)
                self.store = None

    async def stop(self) -> None:
        application = self.application
        if application is not None:
            try:
                if application.updater is not None and application.updater.running:
                    await application.updater.stop()
                if application.running:
                    await application.stop()
                await application.shutdown()
            except Exception:
                logger.exception("Error while shutting down the Telegram bot")
            self.application = None
        if self.store is not None:
            await asyncio.to_thread(self.store.close)
            self.store = None

    @staticmethod
    def _private_chat(update: Update) -> bool:
        message = update.effective_message
        return bool(message and message.chat and message.chat.type == ChatType.PRIVATE)

    def _style_caption(self, style: Style, count: int | None = None) -> str:
        import html
        safe_text = html.escape(style.text)
        return (
            f"✨ <b>Animated Text Creator</b>\n\n"
            f"Text: <code>{safe_text}</code>\n"
            f"Font: {html.escape(style.font)}\n"
            f"Color: {html.escape(style.color)}\n"
            f"Shape: {html.escape(style.shape)}\n"
            f"Animation: {html.escape(style.animation)}\n\n"
            f"Daily free packs used: {count or 0}/{DAILY_LIMIT}\n"
            "Choose options below, then create your 3-emoji pack."
        )

    @staticmethod
    def _keyboard(style: Style) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("‹", callback_data="font:-1"), InlineKeyboardButton(f"🔤 {style.font}", callback_data="noop"), InlineKeyboardButton("›", callback_data="font:1")],
            [InlineKeyboardButton("‹", callback_data="color:-1"), InlineKeyboardButton(f"🎨 {style.color}", callback_data="noop"), InlineKeyboardButton("›", callback_data="color:1")],
            [InlineKeyboardButton("‹", callback_data="shape:-1"), InlineKeyboardButton(f"◈ {style.shape}", callback_data="noop"), InlineKeyboardButton("›", callback_data="shape:1")],
            [InlineKeyboardButton("‹", callback_data="anim:-1"), InlineKeyboardButton(f"✨ {style.animation}", callback_data="noop"), InlineKeyboardButton("›", callback_data="anim:1")],
            [InlineKeyboardButton("✅ Create Emoji Pack", callback_data="create"), InlineKeyboardButton("✏️ Change Text", callback_data="change")],
            [InlineKeyboardButton("✖ Cancel", callback_data="cancel")],
        ])

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if not message:
            return
        await message.reply_text(
            "✨ *Animated Text Creator*\n\n"
            "Turn short text into animated Telegram custom emoji.\n\n"
            "1. Send text (up to 20 characters).\n"
            "2. Choose font, color, shape and animation.\n"
            "3. Create a coordinated pack of 3 animated emoji.\n\n"
            "🎁 Free limit: *3 packs per user per UTC day*. No bot PRO plan or payment is included.\n"
            "Note: Telegram Premium may be required to add and use custom emoji packs.",
            parse_mode="Markdown",
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message:
            await message.reply_text(
                "Send a short line of text to open the editor. Use the arrows to change each option, then tap *Create Emoji Pack*.\n\n"
                "Commands: /start, /help, /cancel" + ("\n/admin — administrator usage stats" if update.effective_user and update.effective_user.id in self.admin_ids else ""),
                parse_mode="Markdown",
            )

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user:
            self.sessions.pop(user.id, None)
            self.session_message_ids.pop(user.id, None)
        if update.effective_message:
            await update.effective_message.reply_text("Creation cancelled. Send another text whenever you're ready.")

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, user = update.effective_message, update.effective_user
        if not message or not user:
            return
        if user.id not in self.admin_ids:
            await message.reply_text("This command is available to administrators only.")
            return
        if not self.store:
            await message.reply_text("Database is not connected. Check MONGODB_URI in Render.")
            return
        try:
            stats = await asyncio.to_thread(self.store.today_stats)
            await message.reply_text(
                "🛠 *Admin Status*\n"
                f"Bot: {self.status}\n"
                f"Packs created today (UTC): {stats['packs_today']}\n"
                f"Users with usage today: {stats['users_today']}\n"
                f"Daily free limit: {DAILY_LIMIT} packs/user",
                parse_mode="Markdown",
            )
        except Exception:
            logger.exception("Unable to load admin stats")
            await message.reply_text("Could not load statistics right now. Check application logs.")

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, user = update.effective_message, update.effective_user
        if not message or not user or message.text is None:
            return
        if not self._private_chat(update):
            await message.reply_text("Please open me in a private chat to create your custom emoji pack.")
            return
        text = " ".join(message.text.strip().split())
        if not text:
            await message.reply_text("Please send some text to design.")
            return
        if len(text) > 20:
            await message.reply_text("Please keep text to 20 characters or fewer so it remains readable as a custom emoji.")
            return
        style = Style(text=text).normalized()
        self.sessions[user.id] = style
        count = 0
        if self.store:
            try:
                count = await asyncio.to_thread(self.store.get_count, user.id)
            except Exception:
                logger.exception("Could not read daily count for user %s", user.id)
        try:
            png = await asyncio.to_thread(render_preview_png, style)
            previous_message_id = self.session_message_ids.get(user.id)
            if previous_message_id:
                try:
                    await context.bot.edit_message_reply_markup(chat_id=user.id, message_id=previous_message_id, reply_markup=None)
                except TelegramError:
                    pass
            preview = await message.reply_photo(
                photo=InputFile(png, filename="animated-text-preview.png"),
                caption=self._style_caption(style, count),
                parse_mode="HTML",
                reply_markup=self._keyboard(style),
            )
            self.session_message_ids[user.id] = preview.message_id
        except Exception:
            logger.exception("Preview render failed for user %s", user.id)
            await message.reply_text("I couldn't render that preview. Try a shorter text using basic letters and numbers.")

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user = update.effective_user
        if not query or not user or not query.data:
            return
        if query.data == "noop":
            await query.answer()
            return
        current_message_id = self.session_message_ids.get(user.id)
        if current_message_id and query.message and query.message.message_id != current_message_id:
            await query.answer("This editor has been replaced. Use the latest preview.", show_alert=True)
            return
        if query.data == "create":
            await self._create_pack(update, context)
            return
        style = self.sessions.get(user.id)
        if not style:
            await query.answer("Send text first to start a new design.", show_alert=True)
            return
        if query.data == "cancel":
            self.sessions.pop(user.id, None)
            self.session_message_ids.pop(user.id, None)
            await query.answer("Cancelled")
            try:
                await query.edit_message_caption(caption="Creation cancelled. Send another text whenever you're ready.", reply_markup=None)
            except TelegramError:
                pass
            return
        if query.data == "change":
            await query.answer("Send your new text in the chat.")
            if query.message:
                await query.message.reply_text("Send the new text (up to 20 characters).")
            return
        try:
            key, direction = query.data.split(":", 1)
            delta = int(direction)
            if key == "font":
                options = FONT_OPTIONS
                style = replace(style, font=options[(options.index(style.font) + delta) % len(options)])
            elif key == "color":
                options = tuple(PALETTES)
                style = replace(style, color=options[(options.index(style.color) + delta) % len(options)])
            elif key == "shape":
                options = SHAPE_OPTIONS
                style = replace(style, shape=options[(options.index(style.shape) + delta) % len(options)])
            elif key == "anim":
                options = ANIMATION_OPTIONS
                style = replace(style, animation=options[(options.index(style.animation) + delta) % len(options)])
            else:
                await query.answer("Unknown option", show_alert=True)
                return
            self.sessions[user.id] = style
            await query.answer()
            png = await asyncio.to_thread(render_preview_png, style)
            count = await asyncio.to_thread(self.store.get_count, user.id) if self.store else 0
            await query.edit_message_media(
                media=InputMediaPhoto(media=InputFile(png, filename="animated-text-preview.png"), caption=self._style_caption(style, count), parse_mode="HTML"),
                reply_markup=self._keyboard(style),
            )
        except Exception:
            logger.exception("Could not update preview for user %s", user.id)
            try:
                await query.answer("Could not update this preview. Please try again.", show_alert=True)
            except TelegramError:
                pass

    async def _create_pack(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user = update.effective_user
        if not query or not user:
            return
        style = self.sessions.get(user.id)
        if style is None:
            await query.answer("Send text first to start a new design.", show_alert=True)
            return
        if not self.store or self.status != "running":
            await query.answer("The bot is not fully configured. Please try again later.", show_alert=True)
            return
        lock = self.locks.setdefault(user.id, asyncio.Lock())
        if lock.locked():
            await query.answer("Your pack is already being created.", show_alert=False)
            return
        async with lock:
            # Use the same timestamp for reservation and rollback. If generation
            # crosses UTC midnight and fails, the previous day's quota is restored.
            quota_time = datetime.now(timezone.utc)
            count = await asyncio.to_thread(self.store.reserve, user.id, quota_time)
            if count is None:
                await query.answer("Daily free limit reached: 3 packs per UTC day. Try again tomorrow.", show_alert=True)
                return
            pack_created = False
            try:
                await query.answer("Creating your animated emoji pack…", show_alert=False)
                bot_username = self.bot_username or (context.bot.username or "bot")
                bot_username = re.sub(r"[^A-Za-z0-9_]", "", bot_username).lower() or "bot"
                pack_name = f"tc_{user.id % 100000}_{int(time.time()) % 1000000}_{secrets.token_hex(2)}_by_{bot_username}"
                title = (style.text.strip()[:45] + " · Animated Emoji")[:64]
                webm_files = await asyncio.to_thread(render_pack_webm, style)
                native_emojis = ("✨", "💫", "🌟")
                stickers = [
                    InputSticker(
                        sticker=InputFile(data, filename=f"animated_emoji_{index + 1}.webm"),
                        emoji_list=[native_emojis[index]],
                        format="video",
                    )
                    for index, data in enumerate(webm_files)
                ]
                await context.bot.create_new_sticker_set(
                    user_id=user.id,
                    name=pack_name,
                    title=title,
                    stickers=stickers,
                    sticker_type="custom_emoji",
                )
                pack_created = True
                pack_url = f"https://t.me/addemoji/{pack_name}"
                self.sessions.pop(user.id, None)
                self.session_message_ids.pop(user.id, None)
                try:
                    import html
                    await query.message.reply_text(
                        "🎉 <b>Your animated emoji pack is ready!</b>\n\n"
                        f"Text: <code>{html.escape(style.text)}</code>\n"
                        "Pack contents: 3 animated custom emoji\n"
                        f"Free packs used today: {count}/{DAILY_LIMIT} (UTC)\n\n"
                        "Open the pack to add it to Telegram. Telegram Premium may be required to add and use custom emoji sets.",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✨ Open Emoji Pack", url=pack_url)]]),
                    )
                except TelegramError:
                    logger.exception("Pack was created, but the confirmation message failed for user_id=%s", user.id)
                logger.info("Created custom emoji pack for user_id=%s; daily_count=%s", user.id, count)
            except Exception as exc:
                if not pack_created:
                    await asyncio.to_thread(self.store.release, user.id, quota_time)
                logger.exception("Pack creation failed for user_id=%s", user.id)
                error_hint = "Telegram rejected the generated pack or the animation could not be encoded."
                if isinstance(exc, TelegramError):
                    error_hint = "Telegram could not accept this pack. Please try another text or design."
                try:
                    await query.message.reply_text(f"⚠️ {error_hint}\nYour daily free credit has been restored if the pack was not created. Please try again.")
                except TelegramError:
                    logger.warning("Could not send pack failure notice to user_id=%s", user.id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = BotService()
    app.state.bot_service = service
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if render_url:
        logger.info("Health endpoint: %s/health", render_url)
    else:
        logger.info("Health endpoint path: /health")
    await service.start()
    yield
    await service.stop()


app = FastAPI(title=SERVICE_NAME, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    service: BotService = request.app.state.bot_service
    status_text = "Running" if service.status == "running" else "Setup required"
    description = "Send a short text to the Telegram bot to create an animated custom-emoji pack." if service.status == "running" else "Add BOT_TOKEN, ADMIN_IDS and MONGODB_URI in Render Environment, then restart the service."
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{SERVICE_NAME}</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a1020;color:#edf2ff;font:16px/1.6 system-ui,sans-serif;padding:24px}}main{{max-width:620px;background:#111a2d;border:1px solid #293653;border-radius:22px;padding:36px;box-shadow:0 24px 70px #0005}}.tag{{display:inline-block;border:1px solid #67508d;border-radius:999px;padding:4px 10px;color:#d9baff;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em}}h1{{font-size:clamp(28px,5vw,40px);line-height:1.15;margin:20px 0 12px}}p{{color:#b9c5de}}code{{color:#e4c9ff}}</style></head><body><main><span class="tag">{status_text}</span><h1>✨ Animated Text Creator</h1><p>{description}</p><p>Daily allowance: <strong>3 packs per user, UTC day</strong>. This service does not include a bot PRO plan or payments.</p><p>Health endpoint: <code>/health</code></p></main></body></html>"""
    return HTMLResponse(html)


@app.get("/healthz")
async def healthz(request: Request) -> dict[str, str]:
    service: BotService = request.app.state.bot_service
    return {"status": "ok", "service": "animated-text-creator", "bot": service.status}


@app.get("/health")
async def health(request: Request) -> JSONResponse:
    service: BotService = request.app.state.bot_service
    if service.status != "running" or service.store is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "service": "animated-text-creator", "bot": service.status, "detail": service.status_reason},
        )
    try:
        await asyncio.to_thread(service.store.ping)
    except Exception:
        logger.exception("Readiness check could not reach MongoDB")
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "service": "animated-text-creator", "bot": "running", "database": "disconnected"},
        )
    return JSONResponse(status_code=200, content={"status": "ok", "service": "animated-text-creator", "bot": "running", "database": "connected"})
