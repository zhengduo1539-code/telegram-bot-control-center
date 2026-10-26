"""EmojiTitleBot: FastAPI health endpoint + Telegram long polling."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputMediaAnimation, InputSticker, LabeledPrice, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters

from database import DAILY_LIMIT, UsageStore
from sticker_engine import ANIMATION_OPTIONS, FONT_OPTIONS, PALETTES, SHAPE_OPTIONS, Style, render_pack_webm, render_preview_gif, render_single_webm, split_text_for_stickers

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# httpx logs full request URLs at INFO. Telegram Bot API URLs contain the bot
# token in the path, so keep HTTP client request logs above INFO to avoid
# writing credentials into Render logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("emojititlebot")
SERVICE_NAME = "EmojiTitleBot | Text from Emojis"
PRO_PRICE_STARS = 100
PRO_PERIOD_SECONDS = 30 * 24 * 60 * 60
STICKER_EMOJI = "✨"


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
        self.mongo_database = os.getenv("MONGODB_DATABASE", "").strip()
        self.mongo_auth_source = os.getenv("MONGODB_AUTH_SOURCE", "").strip()
        self.admin_ids = parse_admin_ids(os.getenv("ADMIN_IDS"))
        self.status = "not_configured"
        self.status_reason = "Set BOT_TOKEN, ADMIN_IDS and MONGODB_URI in Render Environment to enable the bot."
        self.store: UsageStore | None = None
        self.application: Application | None = None
        self.bot_username = ""
        self.sessions: dict[int, Style] = {}
        # Keep style preferences in memory so the next text starts with the last selected appearance.
        self.last_styles: dict[int, Style] = {}
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
            store = UsageStore(
                self.mongo_uri,
                database_name=self.mongo_database or None,
                auth_source=self.mongo_auth_source or None,
            )
            self.store = store
            await asyncio.to_thread(store.initialize)
            application = ApplicationBuilder().token(self.token).build()
            self.application = application
            application.bot_data["service"] = self
            application.add_handler(CommandHandler("start", self.cmd_start))
            application.add_handler(CommandHandler("help", self.cmd_help))
            application.add_handler(CommandHandler("cancel", self.cmd_cancel))
            application.add_handler(CommandHandler("admin", self.cmd_admin))
            application.add_handler(CommandHandler("pro", self.cmd_pro))
            application.add_handler(PreCheckoutQueryHandler(self.on_precheckout))
            application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, self.on_successful_payment))
            application.add_handler(CallbackQueryHandler(self.on_callback))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
            await application.initialize()
            me = await application.bot.get_me()
            self.bot_username = me.username or ""
            await application.bot.set_my_commands([
                BotCommand("start", "Start EmojiTitleBot"),
                BotCommand("help", "How to create emoji packs"),
                BotCommand("pro", "Get PRO with Telegram Stars"),
                BotCommand("cancel", "Cancel current creation"),
            ])
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
        return "Choose the font, color, shape, and animation for your text"

    @staticmethod
    def _keyboard(style: Style, *, creating: bool = False) -> InlineKeyboardMarkup:
        color_icons = {
            "Orange": "💊", "Pink": "💋", "Lime": "🟢🟡",
            "Violet": "💜", "Cyan": "🔵🟢", "Gold": "🟡🟠",
            "Rainbow": "🌈",
        }
        shape_icons = {
            "Pill": "🎟️", "Heart": "💟", "Rounded": "🏷️",
            "Circle": "⚪", "Star": "⭐", "None": "▫️",
        }
        animation_icons = {
            "Wave": "🚩", "Pulse": "💗", "Bounce": "🔴",
            "Float": "🪶", "Shake": "⚡", "Tilt": "🔄", "None": "🚫",
        }
        # The reference uses the same right-pointing hand on both sides of each setting.
        left_arrow = "👉🏻"
        right_arrow = "👉🏻"
        create_label = "⌛ Creating Sticker Set..." if creating else "✅ Create Sticker Set"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(left_arrow, callback_data="font:-1"),
             InlineKeyboardButton(style.font, callback_data="noop"),
             InlineKeyboardButton(right_arrow, callback_data="font:1")],
            [InlineKeyboardButton(left_arrow, callback_data="color:-1"),
             InlineKeyboardButton(color_icons.get(style.color, "🎨"), callback_data="noop"),
             InlineKeyboardButton(right_arrow, callback_data="color:1")],
            [InlineKeyboardButton(left_arrow, callback_data="shape:-1"),
             InlineKeyboardButton(shape_icons.get(style.shape, "◈"), callback_data="noop"),
             InlineKeyboardButton(right_arrow, callback_data="shape:1")],
            [InlineKeyboardButton(left_arrow, callback_data="anim:-1"),
             InlineKeyboardButton(animation_icons.get(style.animation, "✨"), callback_data="noop"),
             InlineKeyboardButton(right_arrow, callback_data="anim:1")],
            [InlineKeyboardButton(create_label, callback_data="create")],
        ])

    def _referral_url(self, user_id: int) -> str:
        username = re.sub(r"[^A-Za-z0-9_]", "", self.bot_username) or "EmojiTitleBot_bot"
        return f"https://t.me/{username}?start=ref_{user_id}"

    def _share_url(self, user_id: int) -> str:
        referral = self._referral_url(user_id)
        text = quote("Create your own animated emoji pack with EmojiTitleBot!")
        return f"https://t.me/share/url?url={quote(referral, safe='')}&text={text}"

    def _account_keyboard(self, user_id: int, *, include_invite: bool = False) -> InlineKeyboardMarkup:
        rows = [[InlineKeyboardButton("⭐ Get PRO — 100 Stars", callback_data="get_pro")]]
        rows.append([
            InlineKeyboardButton(
                "👥 Invite friend" if include_invite else "🔗 Share",
                url=self._share_url(user_id),
            )
        ])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _time_until_utc_reset(now: datetime | None = None) -> tuple[int, int]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        next_day = current.date() + timedelta(days=1)
        reset_at = datetime.combine(next_day, datetime.min.time(), tzinfo=timezone.utc)
        seconds = max(0, int((reset_at - current).total_seconds()))
        total_minutes = (seconds + 59) // 60
        return total_minutes // 60, total_minutes % 60

    def _limit_message(self, user_id: int, now: datetime | None = None) -> str:
        hours, minutes = self._time_until_utc_reset(now)
        return (
            "You have reached the limit of 3 packs per day.\n"
            f"You can create a new pack in {hours} hours in {minutes} minutes.\n\n"
            "With 💎 PRO subscription you get:\n"
            "- Unlimited pack creation\n"
            "- Your text as the pack title instead of bot signature\n\n"
            "Or invite a friend to get +1 free pack!\n"
            f"{self._referral_url(user_id)}"
        )

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, user = update.effective_message, update.effective_user
        if not message or not user:
            return

        referrer_id = None
        if context.args:
            match = re.fullmatch(r"ref_(\d{1,20})", context.args[0])
            if match:
                candidate = int(match.group(1))
                if candidate != user.id:
                    referrer_id = candidate

        referral_rewarded = False
        if self.store:
            try:
                referral_rewarded = await asyncio.to_thread(
                    self.store.register_user, user.id, referrer_id
                )
            except Exception:
                logger.exception("Could not register Telegram user_id=%s", user.id)

        if referral_rewarded and referrer_id is not None:
            try:
                account = await asyncio.to_thread(self.store.get_account, referrer_id)
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=(
                        "Your friend joined the bot! 🎉\n"
                        f"+1 free pack added. You have {account['bonus_count']} bonus packs."
                    ),
                    reply_markup=self._account_keyboard(referrer_id),
                )
            except TelegramError:
                logger.warning("Could not notify referrer user_id=%s", referrer_id)
            except Exception:
                logger.exception("Could not update referral notification for user_id=%s", referrer_id)

        username = self.bot_username or "EmojiTitleBot"
        await message.reply_text(
            f"👋 Hello! I'm @{username}. I can convert any text into beautiful animated emoji text. "
            "Just send me your text and I will create a custom emoji pack for you!"
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message:
            await message.reply_text(
                "Send any short text to open the animated preview. Use the arrows to change its font, color, shape, and animation, then tap *Create Sticker Set*.\n\n"
                "Commands: /start, /help, /pro, /cancel" + ("\n/admin — administrator usage stats" if update.effective_user and update.effective_user.id in self.admin_ids else ""),
                parse_mode="Markdown",
            )

    async def _send_pro_invoice(self, chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.store or self.status != "running":
            await context.bot.send_message(
                chat_id=chat_id,
                text="The payment service is temporarily unavailable. Please try again later.",
            )
            return
        payload = f"pro:{user_id}:{secrets.token_urlsafe(12)}"
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="PRO Subscription",
            description="Unlimited animated emoji pack creation and custom pack titles for 30 days.",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("PRO — 30 days", PRO_PRICE_STARS)],
            subscription_period=PRO_PERIOD_SECONDS,
            start_parameter="emoji-pro",
        )

    async def cmd_pro(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, user = update.effective_message, update.effective_user
        if not message or not user:
            return
        if not self._private_chat(update):
            await message.reply_text("Please open me in a private chat to subscribe to PRO.")
            return
        try:
            await self._send_pro_invoice(message.chat_id, user.id, context)
        except TelegramError:
            logger.exception("Could not send PRO invoice to user_id=%s", user.id)
            await message.reply_text("I couldn't open the Telegram Stars invoice. Please try again later.")

    async def on_precheckout(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.pre_checkout_query
        if not query:
            return
        match = re.fullmatch(r"pro:(\d{1,20}):([A-Za-z0-9_-]{8,64})", query.invoice_payload)
        approved = bool(
            match
            and int(match.group(1)) == query.from_user.id
            and query.currency == "XTR"
            and query.total_amount == PRO_PRICE_STARS
        )
        try:
            await query.answer(
                ok=approved,
                error_message=None if approved else "This PRO invoice is not valid for your account. Please open a new invoice from the bot.",
            )
        except TelegramError:
            logger.exception("Could not answer a Stars pre-checkout query")

    async def on_successful_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message, user = update.effective_message, update.effective_user
        payment = message.successful_payment if message else None
        if not message or not user or not payment or not self.store:
            return
        payload_match = re.fullmatch(r"pro:(\d{1,20}):([A-Za-z0-9_-]{8,64})", payment.invoice_payload)
        if (
            not payload_match
            or int(payload_match.group(1)) != user.id
            or payment.currency != "XTR"
            or payment.total_amount != PRO_PRICE_STARS
        ):
            logger.warning("Ignoring invalid PRO payment details for user_id=%s", user.id)
            await message.reply_text("We couldn't verify this PRO payment. Please contact support with your Telegram receipt.")
            return
        expires_at = payment.subscription_expiration_date or (
            datetime.now(timezone.utc) + timedelta(seconds=PRO_PERIOD_SECONDS)
        )
        try:
            applied = await asyncio.to_thread(
                self.store.record_pro_payment,
                user_id=user.id,
                charge_id=payment.telegram_payment_charge_id,
                amount=payment.total_amount,
                payload=payment.invoice_payload,
                expires_at=expires_at,
            )
            if not applied:
                await message.reply_text("This Telegram Stars payment has already been processed.")
                return
            await message.reply_text(
                "🎉 PRO subscription activated!\n"
                "You now have unlimited pack creation and custom pack titles while your subscription is active.",
                reply_markup=self._account_keyboard(user.id),
            )
        except Exception:
            logger.exception("Could not persist PRO payment for user_id=%s", user.id)
            await message.reply_text(
                "Telegram confirmed your payment, but activation needs attention. "
                "Please contact support and keep your Telegram receipt."
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
        if self.store:
            try:
                await asyncio.to_thread(self.store.register_user, user.id, None)
            except Exception:
                logger.exception("Could not register text creator user_id=%s", user.id)
        previous_style = self.sessions.get(user.id) or self.last_styles.get(user.id)
        style = replace(previous_style, text=text).normalized() if previous_style else Style(text=text).normalized()
        self.sessions[user.id] = style
        self.last_styles[user.id] = style
        count = 0
        if self.store:
            try:
                count = await asyncio.to_thread(self.store.get_count, user.id)
            except Exception:
                logger.exception("Could not read daily count for user %s", user.id)
        try:
            preview_gif = await asyncio.to_thread(render_preview_gif, style)
            previous_message_id = self.session_message_ids.get(user.id)
            if previous_message_id:
                try:
                    await context.bot.edit_message_reply_markup(chat_id=user.id, message_id=previous_message_id, reply_markup=None)
                except TelegramError:
                    pass
            preview = await message.reply_animation(
                animation=InputFile(preview_gif, filename="animated-text-preview.gif"),
                caption=self._style_caption(style, count),
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
        if query.data == "get_pro":
            await query.answer("Opening Telegram Stars payment…")
            try:
                await self._send_pro_invoice(user.id, user.id, context)
            except TelegramError:
                logger.exception("Could not send PRO invoice to user_id=%s", user.id)
                if query.message:
                    await query.message.reply_text("Unable to open the Telegram Stars invoice. Please try again later.")
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
            self.last_styles[user.id] = style
            await query.answer()
            preview_gif = await asyncio.to_thread(render_preview_gif, style)
            count = await asyncio.to_thread(self.store.get_count, user.id) if self.store else 0
            if not query.message:
                logger.warning("Editor message missing while refreshing preview for user_id=%s", user.id)
                return

            # Upload the GIF once to obtain a Telegram file_id, then use that
            # ID to edit the existing preview message. This keeps the original
            # editor message and keyboard in place without multipart edit-media
            # payloads that Telegram rejected as "media not found".
            uploaded_preview = await context.bot.send_animation(
                chat_id=user.id,
                animation=InputFile(preview_gif, filename="animated-text-preview.gif"),
            )
            animation = getattr(uploaded_preview, "animation", None)
            preview_file_id = getattr(animation, "file_id", None)
            try:
                await uploaded_preview.delete()
            except TelegramError:
                logger.debug("Temporary preview upload could not be deleted for user_id=%s", user.id)
            if not isinstance(preview_file_id, str) or not preview_file_id:
                raise RuntimeError("Telegram did not return a file_id for the preview animation")

            await query.edit_message_media(
                media=InputMediaAnimation(
                    media=preview_file_id,
                    caption=self._style_caption(style, count),
                ),
                reply_markup=self._keyboard(style),
            )
        except Exception:
            logger.exception("Could not update preview for user %s", user.id)
            try:
                await query.answer("Could not update this preview. Please try again.", show_alert=True)
            except TelegramError:
                pass

    async def _prepare_stickers(self, bot, user_id: int, webm_files: list[bytes]) -> list[InputSticker]:
        """Upload an adaptive number of regular video stickers and build the set payload."""
        if not webm_files or len(webm_files) > 50:
            raise RuntimeError(
                f"renderer returned {len(webm_files)} files; expected between 1 and 50"
            )

        stickers: list[InputSticker] = []
        for index, data in enumerate(webm_files, start=1):
            uploaded = await bot.upload_sticker_file(
                user_id=user_id,
                sticker=InputFile(data, filename=f"text_{index}.webm"),
                sticker_format="video",
            )
            file_id = getattr(uploaded, "file_id", None)
            if not isinstance(file_id, str) or not file_id:
                raise RuntimeError(f"Telegram did not return a file_id for sticker {index}")
            stickers.append(
                InputSticker(
                    sticker=file_id,
                    emoji_list=[STICKER_EMOJI],
                    format="video",
                )
            )
        return stickers

    async def _create_pack(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query, user = update.callback_query, update.effective_user
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
            await query.answer("Your pack is already being created.")
            return

        async with lock:
            quota_time = datetime.now(timezone.utc)
            account = await asyncio.to_thread(self.store.get_account, user.id, quota_time)
            is_pro = bool(account["is_pro"])
            bonus_used = False
            reserved_over_limit = False

            # Preserve existing free-quota, PRO and referral-credit behavior.
            count = await asyncio.to_thread(self.store.reserve, user.id, quota_time)
            if count is None and is_pro:
                count = await asyncio.to_thread(
                    self.store.reserve, user.id, quota_time, allow_over_limit=True
                )
                reserved_over_limit = count is not None

            if count is None and not is_pro:
                bonus_used = await asyncio.to_thread(self.store.consume_bonus, user.id)
                if bonus_used:
                    count = await asyncio.to_thread(
                        self.store.reserve, user.id, quota_time, allow_over_limit=True
                    )
                    reserved_over_limit = count is not None
                    if count is None:
                        await asyncio.to_thread(self.store.add_bonus, user.id)
                        bonus_used = False

            if count is None:
                await query.answer("Daily free limit reached.", show_alert=True)
                if query.message:
                    await query.message.reply_text(
                        self._limit_message(user.id, quota_time),
                        reply_markup=self._account_keyboard(user.id, include_invite=True),
                    )
                return

            pack_created = False
            try:
                await query.answer()
                if query.message:
                    try:
                        await query.edit_message_reply_markup(
                            reply_markup=self._keyboard(style, creating=True)
                        )
                    except TelegramError:
                        logger.debug("Could not show pack-creation state for user_id=%s", user.id)

                used_count = await asyncio.to_thread(self.store.get_count, user.id, quota_time)
                bot_username = re.sub(
                    r"[^A-Za-z0-9_]", "",
                    self.bot_username or context.bot.username or "EmojiTitleBot",
                ) or "EmojiTitleBot"

                # Make the pack recognizable by its text, with Telegram's required bot suffix.
                slug = re.sub(r"[^A-Za-z0-9]", "", style.text)
                if not slug or not slug[0].isalpha():
                    slug = f"Text{slug}"
                slug = slug[:20]
                suffix = secrets.token_hex(3)[:5]
                pack_name = f"{slug}_{suffix}_by_{bot_username}"
                pack_title = style.text.strip()[:64] if is_pro else f"Created in 👉 @{bot_username}"[:64]

                webm_files = await asyncio.to_thread(render_pack_webm, style)
                if not webm_files:
                    raise RuntimeError("No sticker segments were generated.")
                # The reference sends the complete text as a sample sticker, while
                # the pack itself contains the individually readable text segments.
                sample_webm = await asyncio.to_thread(render_single_webm, style)
                stickers = await self._prepare_stickers(context.bot, user.id, webm_files)
                await context.bot.create_new_sticker_set(
                    user_id=user.id,
                    name=pack_name,
                    title=pack_title,
                    stickers=stickers,
                )
                pack_created = True
                pack_url = f"https://t.me/addstickers/{pack_name}"
                self.sessions.pop(user.id, None)
                self.session_message_ids.pop(user.id, None)
                self.last_styles[user.id] = style

                if query.message:
                    try:
                        await query.edit_message_reply_markup(reply_markup=None)
                    except TelegramError:
                        logger.debug("Could not remove completed editor keyboard for user_id=%s", user.id)

                try:
                    await context.bot.send_sticker(
                        chat_id=user.id,
                        sticker=InputFile(sample_webm, filename="text-sample.webm"),
                    )
                except Exception:
                    # The pack already exists; a sample delivery failure must not
                    # undo the successful creation or consume the quota twice.
                    logger.exception("Pack created but sample sticker could not be sent to user_id=%s", user.id)

                if is_pro:
                    pro_note = "💎 PRO subscription active: unlimited pack creation and custom pack titles."
                else:
                    pro_note = (
                        "With 💎 PRO subscription:\n"
                        "• Unlimited pack creation\n"
                        "• Your text as the pack title instead of bot link"
                    )
                confirmation = (
                    "Your Emojipack has been created!\n"
                    "You can now use it in any chat. Don't forget to add it to your collection to avoid losing it.\n"
                    f"{pack_url}\n\n"
                    f"Used: {used_count} of {DAILY_LIMIT} free packs today\n"
                    f"{pro_note}\n\n"
                    f"Emojipack with {len(webm_files)} emoji for Telegram Premium subscribers."
                )
                if query.message:
                    try:
                        await query.message.reply_text(
                            confirmation,
                            reply_markup=self._account_keyboard(user.id),
                        )
                    except TelegramError:
                        logger.exception("Pack was created but confirmation could not be sent for user_id=%s", user.id)
                logger.info(
                    "Created text sticker pack for user_id=%s; stickers=%s; daily_count=%s; pro=%s",
                    user.id, len(webm_files), count, is_pro,
                )
            except Exception as exc:
                if not pack_created:
                    await asyncio.to_thread(
                        self.store.release,
                        user.id,
                        quota_time,
                        allow_over_limit=reserved_over_limit,
                    )
                    if bonus_used:
                        await asyncio.to_thread(self.store.add_bonus, user.id)
                    if query.message:
                        try:
                            await query.edit_message_reply_markup(
                                reply_markup=self._keyboard(style)
                            )
                        except TelegramError:
                            logger.debug("Could not restore editor keyboard after failure for user_id=%s", user.id)
                logger.exception("Pack creation failed for user_id=%s", user.id)
                error_hint = (
                    "Telegram could not accept this sticker pack. Please try another text or design."
                    if isinstance(exc, TelegramError)
                    else "The animation could not be encoded. Please try again with shorter text."
                )
                try:
                    if query.message:
                        await query.message.reply_text(
                            f"⚠️ {error_hint}\nYour free credit has been restored if the pack was not created. Please try again."
                        )
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
    description = "Send a short text to EmojiTitleBot in Telegram to create an animated custom-emoji pack." if service.status == "running" else "Add BOT_TOKEN, ADMIN_IDS and MONGODB_URI in Render Environment, then restart the service."
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>{SERVICE_NAME}</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a1020;color:#edf2ff;font:16px/1.6 system-ui,sans-serif;padding:24px}}main{{max-width:620px;background:#111a2d;border:1px solid #293653;border-radius:22px;padding:36px;box-shadow:0 24px 70px #0005}}.tag{{display:inline-block;border:1px solid #67508d;border-radius:999px;padding:4px 10px;color:#d9baff;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.08em}}h1{{font-size:clamp(28px,5vw,40px);line-height:1.15;margin:20px 0 12px}}p{{color:#b9c5de}}code{{color:#e4c9ff}}</style></head><body><main><span class="tag">{status_text}</span><h1>👋 EmojiTitleBot</h1><p>{description}</p><p>Free allowance: <strong>3 packs per user, UTC day</strong>. PRO unlocks unlimited pack creation and custom pack titles, with Telegram Stars billing.</p><p>Health endpoint: <code>/health</code></p></main></body></html>"""
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
