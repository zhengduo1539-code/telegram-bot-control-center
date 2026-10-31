"""
handlers/base.py — /start, /help, /cancel, My Posts, and universal navigation.

Navigation pattern used throughout the bot:
  - Every wizard step includes ⬅️ Back and ❌ Cancel inline buttons.
  - Before committing to the database a full summary is shown with ✅ Confirm.
  - ❌ Cancel always resets ConversationHandler state and returns to the main menu.
"""

from __future__ import annotations

import logging
from datetime import timezone as _dt_timezone
from typing import Any

import pytz

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
from handlers import chat_cleanup
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_IDS
from handlers.owner_panel import CB_OWNER_PANEL
from handlers.permissions import ChatAccessError, ensure_chat_access
from handlers.language import resolve_user_language
from localization import translate as tr

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Callback-data constants  (all ≤ 64 bytes — well within Telegram's 64-byte limit)
# ---------------------------------------------------------------------------
CB_HELP         = "nav:help"
CB_CANCEL       = "nav:cancel"
CB_MAIN_MENU    = "nav:main_menu"
CB_SCHEDULE_NEW = "action:schedule_new"
CB_MY_POSTS     = "action:my_posts"
CB_MANAGE_QUEUE = "action:manage_queue"
CB_MEDIA_POOL   = "action:media_pool"
CB_LANGUAGE    = "nav:language"

# My Posts action prefixes
CB_MPP_FILTER  = "mpp:filter:"  # + all|pending|paused|posted|failed (prefix + 24-char ObjectId ≤ 64 bytes total)
CB_MPP_PAGE    = "mpp:page:"     # + page number
CB_MPP_PAUSE   = "mpp:pause:"   # + post_id
CB_MPP_RESUME  = "mpp:resume:"  # + post_id
CB_MPP_DEL       = "mpp:del:"       # + post_id  (ask confirm)
CB_MPP_DEL_YES   = "mpp:delyes:"   # + post_id  (execute)
CB_MPP_CLONE     = "mpp:clone:"    # + post_id  (ask confirm)
CB_MPP_CLONE_YES = "mpp:cloneyes:" # + post_id  (execute)
CB_MPP_SAVE_TMPL = "mpp:savetmpl:" # + post_id  (save as template)

CB_MY_STATS  = "action:my_stats"   # post statistics
CB_TEMPLATES = "action:templates"  # templates menu

# ---------------------------------------------------------------------------
# Conversation states (used by the base ConversationHandler)
# ---------------------------------------------------------------------------
MAIN_MENU               = 0
MY_POSTS                = 1
MY_POSTS_CONFIRM_DELETE = 2
MY_POSTS_CONFIRM_CLONE  = 3
MY_POSTS_SAVE_TEMPLATE  = 4

POSTS_PER_PAGE = 5


# Telegram deep-link payload used to carry the originating group into private chat.
START_GROUP_PREFIX = "setup_"


def build_group_start_payload(chat_id: int) -> str:
    """Build a Telegram-compatible /start payload for a group or supergroup."""
    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id >= 0:
        raise ValueError("A negative group or supergroup chat ID is required.")
    payload = f"{START_GROUP_PREFIX}{chat_id}"
    if len(payload.encode("utf-8")) > 64:
        raise ValueError("The group deep-link payload exceeds Telegram's 64-byte limit.")
    return payload


def parse_group_start_payload(args: list[str]) -> int | None:
    """Parse a trusted-format group ID from /start arguments; reject other payloads."""
    if len(args) != 1 or not args[0].startswith(START_GROUP_PREFIX):
        return None
    raw_chat_id = args[0][len(START_GROUP_PREFIX):]
    if not raw_chat_id.startswith("-") or not raw_chat_id[1:].isdecimal():
        return None
    if len(raw_chat_id) > 21:
        return None
    try:
        return int(raw_chat_id)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def _bot_username(bot: Bot) -> str:
    """Return the bot's username, falling back gracefully if not yet available."""
    return bot.username or "YourSchedulerBot"


def start_keyboard(bot: Bot) -> InlineKeyboardMarkup:
    username = _bot_username(bot)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❓ Help", callback_data=CB_HELP),
        ],
        [
            InlineKeyboardButton(
                "➕ Add me to Group/Channel",
                url=f"https://t.me/{username}?startgroup=true",
            ),
        ],
        [
            InlineKeyboardButton(
                "📢 Share Bot",
                url=f"https://t.me/share/url?url=https://t.me/{username}&text=Check out this Advanced Scheduler Bot!",
            ),
        ],
    ])


def main_menu_keyboard(
    bot: Bot | None = None,
    user_id: int | None = None,
    language: str = "en",
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(tr(language, "schedule_new"), callback_data=CB_SCHEDULE_NEW),
            InlineKeyboardButton(tr(language, "my_posts"), callback_data=CB_MY_POSTS),
        ],
        [
            InlineKeyboardButton(tr(language, "manage_queue"), callback_data=CB_MANAGE_QUEUE),
            InlineKeyboardButton(tr(language, "media_pool"), callback_data=CB_MEDIA_POOL),
        ],
        [
            InlineKeyboardButton(tr(language, "templates"), callback_data=CB_TEMPLATES),
            InlineKeyboardButton(tr(language, "my_stats"), callback_data=CB_MY_STATS),
        ],
        [
            InlineKeyboardButton(tr(language, "language"), callback_data=CB_LANGUAGE),
            InlineKeyboardButton(tr(language, "cancel"), callback_data=CB_CANCEL),
        ],
    ]
    if bot is not None:
        username = _bot_username(bot)
        rows.append([
            InlineKeyboardButton(
                tr(language, "add_group"),
                url=f"https://t.me/{username}?startgroup=true",
            ),
            InlineKeyboardButton(
                tr(language, "share_bot"),
                url=f"https://t.me/share/url?url=https://t.me/{username}&text=Check out this Advanced Scheduler Bot!",
            ),
        ])
    if user_id is not None and user_id in ADMIN_IDS:
        rows.append([
            InlineKeyboardButton(tr(language, "owner_panel"), callback_data=CB_OWNER_PANEL),
        ])
    return InlineKeyboardMarkup(rows)


def nav_keyboard(back_data: str | None = None) -> InlineKeyboardMarkup:
    """
    Universal navigation row.  Pass `back_data` to enable the Back button;
    omit it (or pass None) to hide Back (e.g. on the first wizard step).
    """
    row: list[InlineKeyboardButton] = []
    if back_data:
        row.append(InlineKeyboardButton("⬅️ Back", callback_data=back_data))
    row.append(InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL))
    return InlineKeyboardMarkup([row])


def confirm_keyboard(confirm_data: str, back_data: str) -> InlineKeyboardMarkup:
    """
    Final summary confirmation keyboard shown before any database write.
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data=confirm_data),
            InlineKeyboardButton("⬅️ Back",   callback_data=back_data),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data=CB_CANCEL),
        ],
    ])


# ---------------------------------------------------------------------------
# Welcome / help text
# ---------------------------------------------------------------------------

WELCOME_TEXT = (
    "👋 *Welcome to Schedule Bot*\n\n"
    "Plan your posts once. The bot publishes them to your group or channel at the time you choose.\n\n"
    "Choose a tool below to get started. Each setup guides you one step at a time.\n\n"
    "🔁 *Recurring posts* — repeat by interval or selected weekdays\n"
    "🕐 *Time windows* — choose allowed posting hours, including overnight windows\n"
    "🗂 *Daily Queue* — add posts and let the bot publish one at each daily slot\n"
    "🎲 *Random shuffler* — post randomly from a media pool\n"
    "📌 *Auto-pin / unpin* — pin on send, unpin after a duration\n"
    "🗑 *Auto-delete / self-destruct* — remove posts after X hours or seconds\n\n"
    "Start with *📅 Schedule New Post* for a single or recurring post, or *🗂 Manage Queue* for daily slots."
)

HELP_TEXT = (
    "📖 *Feature Guide*\n\n"
    "*📅 Schedule New Post*\n"
    "  Set up a one-off or recurring post with full lifecycle control.\n\n"
    "*📋 My Posts*\n"
    "  View, pause, resume, or delete your scheduled posts.\n\n"
    "*🗂 Manage Queue*\n"
    "  Define daily posting slots. Content auto-fills the next free slot.\n\n"
    "*🎲 Media Pool*\n"
    "  Upload multiple pieces of content. The bot picks one randomly at each interval.\n\n"
    "*🕐 Time Windows*\n"
    "  Set a daily active window (e.g. 08:00–22:00). Posts outside it are skipped.\n\n"
    "*📌 Lifecycle Options*\n"
    "  Auto-delete after X hours, self-destruct after X seconds, auto-pin+unpin.\n\n"
    "*🔁 Recurrence*\n"
    "  Every X minutes/hours/days · Specific weekdays · Max runs counter."
)


# ---------------------------------------------------------------------------
# My Posts — helpers
# ---------------------------------------------------------------------------

_STATUS_ICON: dict[str, str] = {
    "pending": "⏳", "paused": "⏸", "posted": "✅", "failed": "❌",
}
_STATUS_LABEL = {"pending": "Active", "paused": "Paused", "posted": "Completed", "failed": "Failed"}
_MP_FILTERS = ("all", "pending", "paused", "posted", "failed")
_MP_FILTER_LABELS = {
    "en": {"all": "All", "pending": "Active", "paused": "Paused", "posted": "Completed", "failed": "Failed"},
    "my": {"all": "အားလုံး", "pending": "လုပ်ဆောင်နေ", "paused": "ခေတ္တရပ်", "posted": "ပြီးဆုံး", "failed": "မအောင်မြင်"},
}


def _mp_label(language: str, key: str) -> str:
    return _MP_FILTER_LABELS.get(language, _MP_FILTER_LABELS["en"]).get(key, key.title())


def _format_next_run(next_run, tz_str: str) -> str:
    """Format a UTC next_run_at datetime in the post's saved timezone."""
    if next_run is None:
        return "—"
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=_dt_timezone.utc)
    try:
        zone = pytz.timezone(tz_str) if tz_str != "UTC" else _dt_timezone.utc
    except pytz.exceptions.UnknownTimeZoneError:
        zone = _dt_timezone.utc
        tz_str = "UTC"
    local_dt = next_run.astimezone(zone)
    label = "UTC" if tz_str == "UTC" else tz_str.split("/")[-1].replace("_", " ")
    return local_dt.strftime(f"%d/%m/%Y %H:%M {label}")


def _filter_posts(posts: list[dict], filter_key: str) -> list[dict]:
    """Return a status-filtered view without mutating database results."""
    if filter_key not in _MP_FILTERS or filter_key == "all":
        return list(posts)
    return [post for post in posts if post.get("status", "pending") == filter_key]



def _posts_rich_message(
    posts: list[dict],
    page: int,
    language: str = "en",
    all_posts: list[dict] | None = None,
    filter_key: str = "all",
) -> dict[str, Any]:
    """Build a Telegram Bot API Rich Message containing a native compact table."""
    source_posts = all_posts if all_posts is not None else posts
    counts = {
        status: sum(1 for post in source_posts if post.get("status", "pending") == status)
        for status in _MP_FILTERS[1:]
    }
    total = len(posts)
    start = page * POSTS_PER_PAGE
    end = min(start + POSTS_PER_PAGE, total)
    n_pages = max(1, (total + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE)

    cells: list[list[dict[str, Any]]] = [[
        {"text": "Post", "is_header": True},
        {"text": "Content", "is_header": True},
        {"text": "Status", "is_header": True},
        {"text": "Next run", "is_header": True},
    ]]
    for idx, post in enumerate(posts[start:end], start=start + 1):
        status = post.get("status", "pending")
        icon = _STATUS_ICON.get(status, "❓")
        status_label = _mp_label(language, status) if status in _MP_FILTERS else status.replace("_", " ").title()
        chat_id = post.get("chat_id", "?")
        chat_title = post.get("chat_title") or post.get("group_title") or post.get("chat_name") or chat_id
        group = " ".join(str(chat_title).split())
        if len(group) > 30:
            group = group[:27].rstrip() + "…"

        recurrence = post.get("recurrence") or {}
        rec_type = str(recurrence.get("type", "once")).replace("_", " ").title()
        content = post.get("content") or {}
        media_type = content.get("media_type")
        content_type = (
            "Text" if content.get("text") and not media_type
            else str(media_type or "Text").replace("_", " ").title()
        )
        next_run = recurrence.get("next_run_at") or post.get("next_run_at")
        next_label = _format_next_run(next_run, post.get("timezone", "UTC"))
        if status == "posted":
            next_label = "Published"
        elif status == "failed":
            next_label = "Needs attention"

        cells.append([
            {"text": f"#{idx}\n{group}"},
            {"text": f"{content_type}\n{rec_type}"},
            {"text": f"{icon} {status_label}"},
            {"text": next_label},
        ])

    summary = (
        f"{len(source_posts)} total · ⏳ {counts['pending']} active · "
        f"⏸ {counts['paused']} paused · ✅ {counts['posted']} completed · "
        f"❌ {counts['failed']} failed"
    )
    return {
        "blocks": [
            {"type": "heading", "size": 2, "text": "📋 My Posts"},
            {
                "type": "paragraph",
                "text": f"{_mp_label(language, filter_key)} · {total} posts · Page {page + 1}/{n_pages}",
            },
            {"type": "paragraph", "text": summary},
            {
                "type": "table",
                "cells": cells,
                "is_bordered": True,
                "is_striped": True,
                "is_compact": True,
                "caption": "Scheduled post overview",
            },
            {
                "type": "footer",
                "text": "Use the buttons below each post to pause, resume, duplicate, save as a template, or delete it.",
            },
        ]
    }

def _posts_list_text(posts: list[dict], page: int, language: str = "en") -> str:
    total = len(posts)
    n_pages = max(1, (total + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE)
    start = page * POSTS_PER_PAGE
    end = min(start + POSTS_PER_PAGE, total)
    lines = [
        f"📋 *{tr(language, 'posts_title')}*",
        f"{_mp_label(language, 'all')} · {total} posts · Page {page + 1}/{n_pages}",
        "",
    ]
    for idx, post in enumerate(posts[start:end], start=start + 1):
        status = post.get("status", "pending")
        icon = _STATUS_ICON.get(status, "❓")
        status_label = _mp_label(language, status) if status in _MP_FILTERS else status.title()
        chat_id = post.get("chat_id", "?")
        chat_title = post.get("chat_title") or post.get("group_title") or post.get("chat_name")
        group = escape_markdown(str(chat_title or chat_id), version=1)
        recurrence = post.get("recurrence") or {}
        rec_type = recurrence.get("type", "once")
        content = post.get("content") or {}
        media_type = content.get("media_type")
        content_type = "Text" if content.get("text") and not media_type else str(media_type or "Text").replace("_", " ").title()
        next_run = recurrence.get("next_run_at") or post.get("next_run_at")
        next_str = _format_next_run(next_run, post.get("timezone", "UTC"))
        next_label = "Next run"
        if status == "posted":
            next_label, next_str = "Status", "Published"
        elif status == "failed":
            next_label, next_str = "Status", "Needs attention"
        lines.extend([
            f"*{idx}. {icon} {status_label}*",
            f"🗂 {group}",
            f"🧩 {content_type} · 🔁 {str(rec_type).replace('_', ' ').title()}",
            f"🕒 {next_label}: {next_str}",
            "",
        ])
    lines.append("Choose an action below to manage a post. Use the status filters to narrow the list.")
    return "\n".join(lines)


def _posts_keyboard(
    posts: list[dict], page: int, language: str = "en", filter_key: str = "all"
) -> InlineKeyboardMarkup:
    total = len(posts)
    start = page * POSTS_PER_PAGE
    end = min(start + POSTS_PER_PAGE, total)
    buttons: list[list[InlineKeyboardButton]] = []
    buttons.append([
        InlineKeyboardButton(f"{'• ' if filter_key == key else ''}{_mp_label(language, key)}", callback_data=f"{CB_MPP_FILTER}{key}")
        for key in ("all", "pending", "paused")
    ])
    buttons.append([
        InlineKeyboardButton(f"{'• ' if filter_key == key else ''}{_mp_label(language, key)}", callback_data=f"{CB_MPP_FILTER}{key}")
        for key in ("posted", "failed")
    ])
    for idx, post in enumerate(posts[start:end], start=start + 1):
        pid = str(post["_id"])
        status = post.get("status", "pending")
        controls: list[InlineKeyboardButton] = []
        if status == "pending":
            controls.append(InlineKeyboardButton(f"{tr(language, 'pause')} #{idx}", callback_data=f"{CB_MPP_PAUSE}{pid}"))
        elif status == "paused":
            controls.append(InlineKeyboardButton(f"{tr(language, 'resume')} #{idx}", callback_data=f"{CB_MPP_RESUME}{pid}"))
        controls.append(InlineKeyboardButton(f"{tr(language, 'delete')} #{idx}", callback_data=f"{CB_MPP_DEL}{pid}"))
        buttons.append(controls)
        buttons.append([
            InlineKeyboardButton(f"{tr(language, 'clone')} #{idx}", callback_data=f"{CB_MPP_CLONE}{pid}"),
            InlineKeyboardButton(f"{tr(language, 'save_template')} #{idx}", callback_data=f"{CB_MPP_SAVE_TMPL}{pid}"),
        ])
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(tr(language, "previous"), callback_data=f"{CB_MPP_PAGE}{page - 1}"))
    if end < total:
        nav_row.append(InlineKeyboardButton(tr(language, "next"), callback_data=f"{CB_MPP_PAGE}{page + 1}"))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([
        InlineKeyboardButton(tr(language, "statistics"), callback_data=CB_MY_STATS),
        InlineKeyboardButton(tr(language, "menu_title"), callback_data=CB_MAIN_MENU),
    ])
    return InlineKeyboardMarkup(buttons)


async def _render_my_posts(query: Any, user_id: int, page: int, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Fetch, filter, and render the user's post-management dashboard."""
    from database import get_user_posts
    language = await resolve_user_language(context, user_id)
    try:
        posts = await get_user_posts(user_id)
    except Exception as exc:
        logger.exception("get_user_posts failed: %s", exc)
        await query.edit_message_text(
            f"❌ Error loading posts: `{str(exc).replace(chr(96), chr(39))}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(tr(language, "menu_title"), callback_data=CB_MAIN_MENU)]]),
        )
        return MY_POSTS

    filter_key = context.user_data.get("_mp_filter", "all")
    if filter_key not in _MP_FILTERS:
        filter_key = "all"
        context.user_data["_mp_filter"] = filter_key
    filtered_posts = _filter_posts(posts, filter_key)

    if not posts:
        await query.edit_message_text(
            f"{tr(language, 'posts_empty')}\n\nUse *Schedule New Post* to create your first scheduled post. "
            "Return here anytime to pause, resume, duplicate, or remove posts.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(tr(language, "schedule_new"), callback_data=CB_SCHEDULE_NEW)],
                [InlineKeyboardButton(tr(language, "menu_title"), callback_data=CB_MAIN_MENU)],
            ]),
        )
        return MY_POSTS

    if not filtered_posts:
        await query.edit_message_text(
            f"📋 *{tr(language, 'posts_title')}*\n\nNo {_mp_label(language, filter_key)} posts found. Choose another filter below.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_posts_keyboard([], 0, language, filter_key),
        )
        context.user_data["_mp_page"] = 0
        return MY_POSTS

    max_page = max(0, (len(filtered_posts) + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE - 1)
    page = max(0, min(page, max_page))
    context.user_data["_mp_page"] = page
    keyboard = _posts_keyboard(filtered_posts, page, language, filter_key)
    rich_message = _posts_rich_message(
        filtered_posts, page, language, all_posts=posts, filter_key=filter_key
    )
    try:
        # PTB 22.8 exposes do_api_request for Bot API methods added after the
        # library release. Edit in place to preserve the dashboard message.
        api_kwargs: dict[str, Any] = {
            "rich_message": rich_message,
            "reply_markup": keyboard,
        }
        if query.inline_message_id:
            api_kwargs["inline_message_id"] = query.inline_message_id
        elif query.message:
            api_kwargs["chat_id"] = query.message.chat_id
            api_kwargs["message_id"] = query.message.message_id
        else:
            raise ValueError("Cannot edit My Posts: callback message is unavailable.")

        await context.bot.do_api_request("editMessageText", api_kwargs=api_kwargs)
    except Exception as exc:
        logger.warning("Native My Posts Rich Message unavailable; using text fallback: %s", exc)
        rendered = _posts_list_text(filtered_posts, page, language)
        rendered = rendered.replace(
            "\n\n",
            f"\n\n{len(posts)} total · "
            f"⏳ {sum(1 for post in posts if post.get('status', 'pending') == 'pending')} active · "
            f"⏸ {sum(1 for post in posts if post.get('status') == 'paused')} paused · "
            f"✅ {sum(1 for post in posts if post.get('status') == 'posted')} completed · "
            f"❌ {sum(1 for post in posts if post.get('status') == 'failed')} failed\n",
            1,
        )
        rendered = rendered.replace(
            f"{_mp_label(language, 'all')} ·", f"{_mp_label(language, filter_key)} ·", 1
        )
        await query.edit_message_text(
            rendered, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard
        )
    return MY_POSTS


# ---------------------------------------------------------------------------
# Handlers — navigation & main menu
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Redirect group /start requests to private chat; show the localized menu in PM."""
    message = update.message
    if message is None:
        return MAIN_MENU

    chat = update.effective_chat
    user_id = update.effective_user.id if update.effective_user else None
    language = await resolve_user_language(context, user_id)

    if chat is not None and chat.type in ("group", "supergroup"):
        try:
            bot_user = await context.bot.get_me()
            if not bot_user.username:
                raise RuntimeError("The bot does not have a public username.")
            payload = build_group_start_payload(chat.id)
            private_url = f"https://t.me/{bot_user.username}?start={payload}"
        except Exception:
            logger.exception("Could not create a private-chat deep link for group %s.", chat.id)
            await message.reply_text(
                "⚠️ " + tr(language, "group_start_body", group="this group"),
            )
            return ConversationHandler.END

        group_title = escape_markdown(chat.title or "this group", version=1)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(tr(language, "open_private"), url=private_url)]
        ])
        await message.reply_text(
            f"{tr(language, 'group_start_title')}\n\n"
            f"{tr(language, 'group_start_body', group=group_title)}\n\n"
            f"🔒 {tr(language, 'private_setup_action')}\n"
            f"{tr(language, 'group_admin_note')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return ConversationHandler.END

    target_chat_id = parse_group_start_payload(context.args)
    context.user_data.pop("launch_chat", None)
    launch_notice = None

    if target_chat_id is not None and user_id is not None:
        try:
            target_chat = await ensure_chat_access(context.bot, target_chat_id, user_id)
            context.user_data["launch_chat"] = {
                "chat_id": target_chat.id,
                "title": target_chat.title or str(target_chat.id),
            }
        except ChatAccessError as exc:
            launch_notice = (
                "⚠️ *Group not linked:* "
                f"{escape_markdown(str(exc), version=1)}\n\n"
                "You can still choose a group manually, or ask a group administrator "
                "to open this setup link."
            )
        except Exception:
            logger.exception("Failed to validate deep-linked group %s.", target_chat_id)
            launch_notice = (
                "⚠️ I couldn't verify the selected group. "
                "You can choose a group manually from the menu."
            )

    if "launch_chat" in context.user_data:
        linked = context.user_data["launch_chat"]
        safe_title = escape_markdown(str(linked["title"]), version=1)
        menu_text = (
            f"{tr(language, 'private_setup_title')}\n\n"
            f"{tr(language, 'private_setup_body', group=safe_title)}\n\n"
            f"{tr(language, 'private_setup_action')}"
        )
    else:
        menu_text = tr(language, "menu_title")
        if launch_notice:
            menu_text = f"{launch_notice}\n\n{menu_text}"

    await message.reply_text(
        menu_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(context.bot, user_id, language),
    )
    return MAIN_MENU

async def cb_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show the help/feature guide."""
    query = update.callback_query
    if not query:
        return MAIN_MENU
    await query.answer()
    language = await resolve_user_language(context, query.from_user.id if query.from_user else None)
    await query.edit_message_text(
        tr(language, "help_text"),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(tr(language, "menu_title"), callback_data=CB_MAIN_MENU)],
        ]),
    )
    return MAIN_MENU


async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to the main feature menu."""
    query = update.callback_query
    if not query:
        return MAIN_MENU
    await query.answer()
    user_id = query.from_user.id if query.from_user else None
    language = await resolve_user_language(context, user_id)
    await query.edit_message_text(
        tr(language, "menu_title"),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(context.bot, user_id, language),
    )
    return MAIN_MENU


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Universal cancel handler.
    Clears all user_data wizard state and returns the user to the welcome screen.
    """
    query = update.callback_query
    if not query:
        return ConversationHandler.END
    await query.answer("Cancelled.")

    language = await resolve_user_language(
        context, query.from_user.id if query.from_user else None
    )
    # Wipe any in-progress wizard state so the next session starts clean
    context.user_data.clear()

    chat_id = query.message.chat_id if query.message else None
    keep_id = query.message.message_id if query.message else None
    if chat_id is not None:
        await chat_cleanup.cleanup(context, chat_id, keep_message_id=keep_id)

    await query.edit_message_text(
        "❌ *Cancelled.*\n\nUse /start to begin again.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Text-command version of cancel (/cancel)."""
    context.user_data.clear()
    if update.message:
        chat_id = update.message.chat_id
        # Send the confirmation FIRST and keep *that* message — sweeping
        # everything else including the user's own "/cancel" text — so only
        # one message (the confirmation) remains, matching cb_cancel's
        # behaviour.
        final = await update.message.reply_text(
            "❌ *Cancelled.* Use /start to begin again.",
            parse_mode=ParseMode.MARKDOWN,
        )
        await chat_cleanup.cleanup(context, chat_id, keep_message_id=final.message_id)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Handlers — My Posts
# ---------------------------------------------------------------------------

async def cb_my_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry: show the first page of the user's scheduled posts."""
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS
    await query.answer()
    user_id: int = query.from_user.id
    page = context.user_data.get("_mp_page", 0)
    context.user_data["_mp_filter"] = "all"
    return await _render_my_posts(query, user_id, page, context)


async def cb_mpp_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Switch the status filter and reset pagination."""
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS
    filter_key = (query.data or "").replace(CB_MPP_FILTER, "", 1)
    if filter_key not in _MP_FILTERS:
        await query.answer("Unknown filter.", show_alert=True)
        return MY_POSTS
    context.user_data["_mp_filter"] = filter_key
    context.user_data["_mp_page"] = 0
    await query.answer()
    return await _render_my_posts(query, query.from_user.id, 0, context)


async def cb_mpp_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Paginate through the posts list."""
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS
    page_str = (query.data or "").replace(CB_MPP_PAGE, "")
    page = int(page_str) if page_str.isdigit() else 0
    await query.answer()
    user_id: int = query.from_user.id
    return await _render_my_posts(query, user_id, page, context)


async def cb_mpp_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Pause a pending post."""
    from database import update_post_status
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS
    post_id = (query.data or "").replace(CB_MPP_PAUSE, "")
    try:
        await update_post_status(post_id, "paused")
        await query.answer("⏸ Post paused.")
    except Exception as exc:
        logger.exception("Pause post %s failed: %s", post_id, exc)
        await query.answer(f"Error: {exc}", show_alert=True)
    user_id: int = query.from_user.id
    page = context.user_data.get("_mp_page", 0)
    return await _render_my_posts(query, user_id, page, context)


async def cb_mpp_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Resume a paused post (sets status back to pending)."""
    from database import update_post_status
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS
    post_id = (query.data or "").replace(CB_MPP_RESUME, "")
    try:
        await update_post_status(post_id, "pending")
        await query.answer("▶️ Post resumed.")
    except Exception as exc:
        logger.exception("Resume post %s failed: %s", post_id, exc)
        await query.answer(f"Error: {exc}", show_alert=True)
    user_id: int = query.from_user.id
    page = context.user_data.get("_mp_page", 0)
    return await _render_my_posts(query, user_id, page, context)


async def cb_mpp_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask for delete confirmation."""
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS_CONFIRM_DELETE
    post_id = (query.data or "").replace(CB_MPP_DEL, "")
    await query.answer()
    await query.edit_message_text(
        "⚠️ *Confirm Delete*\n\nThis post will be permanently deleted.\n\nProceed?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, delete", callback_data=f"{CB_MPP_DEL_YES}{post_id}"),
                InlineKeyboardButton("⬅️ Back",        callback_data=CB_MY_POSTS),
            ],
        ]),
    )
    return MY_POSTS_CONFIRM_DELETE


async def cb_mpp_del_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute the delete and refresh the list."""
    from database import delete_post
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS
    post_id = (query.data or "").replace(CB_MPP_DEL_YES, "")
    try:
        deleted = await delete_post(post_id)
        await query.answer("Deleted." if deleted else "Already deleted.")
    except Exception as exc:
        logger.exception("Delete post %s failed: %s", post_id, exc)
        await query.answer(f"Error: {exc}", show_alert=True)
    user_id: int = query.from_user.id
    page = context.user_data.get("_mp_page", 0)
    return await _render_my_posts(query, user_id, page, context)


# ---------------------------------------------------------------------------
# Handlers — My Stats
# ---------------------------------------------------------------------------

async def cb_my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show per-channel post statistics for the current user."""
    from database import get_post_stats
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS
    await query.answer()
    user_id = query.from_user.id

    try:
        rows = await get_post_stats(user_id)
    except Exception as exc:
        logger.exception("get_post_stats failed: %s", exc)
        await query.edit_message_text(
            f"❌ Error loading statistics: `{str(exc).replace(chr(96), chr(39))}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data=CB_MAIN_MENU)]]),
        )
        return MY_POSTS

    if not rows:
        await query.edit_message_text(
            "📊 *My Statistics*\n\nNo posts found. Schedule something first!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 My Posts", callback_data=CB_MY_POSTS)],
                [InlineKeyboardButton("🏠 Main Menu", callback_data=CB_MAIN_MENU)],
            ]),
        )
        return MY_POSTS

    lines = ["📊 *Post Statistics*\n"]
    for row in rows:
        chat_id  = row["_id"]
        total    = row["total"]
        sm       = {s["status"]: s["count"] for s in row.get("stats", [])}
        lines.append(
            f"🗂 `{chat_id}` — *{total}* total\n"
            f"  ⏳`{sm.get('pending', 0)}`  ✅`{sm.get('posted', 0)}`"
            f"  ⏸`{sm.get('paused', 0)}`  ❌`{sm.get('failed', 0)}`"
        )

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 My Posts",  callback_data=CB_MY_POSTS)],
            [InlineKeyboardButton("🏠 Main Menu", callback_data=CB_MAIN_MENU)],
        ]),
    )
    return MY_POSTS


# ---------------------------------------------------------------------------
# Handlers — Clone Post
# ---------------------------------------------------------------------------

async def cb_mpp_clone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask for clone confirmation."""
    from database import get_post
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS
    post_id = (query.data or "").replace(CB_MPP_CLONE, "")
    try:
        post = await get_post(post_id)
    except Exception as exc:
        await query.answer(f"Error: {exc}", show_alert=True)
        return MY_POSTS
    if not post:
        await query.answer("Post not found.", show_alert=True)
        return MY_POSTS
    await query.answer()
    chat_id  = post.get("chat_id", "?")
    rec_type = (post.get("recurrence") or {}).get("type", "once")
    await query.edit_message_text(
        f"🔁 *Clone Post*\n\n"
        f"🗂 Chat: `{chat_id}`\n"
        f"🔄 Recurrence: `{rec_type}`\n\n"
        "A copy will be created with the same content and recurrence settings.\n"
        "It will be scheduled to run in *5 minutes* from now.\n\n"
        "Confirm?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Clone",  callback_data=f"{CB_MPP_CLONE_YES}{post_id}"),
                InlineKeyboardButton("⬅️ Back",  callback_data=CB_MY_POSTS),
            ],
        ]),
    )
    return MY_POSTS_CONFIRM_CLONE


async def cb_mpp_clone_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute the clone and refresh the My Posts list."""
    from database import clone_post
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS
    post_id = (query.data or "").replace(CB_MPP_CLONE_YES, "")
    try:
        new_id = await clone_post(post_id)
    except Exception as exc:
        logger.exception("Clone post %s failed: %s", post_id, exc)
        await query.answer(f"Error: {exc}", show_alert=True)
        return MY_POSTS
    await query.answer(f"✅ Cloned! ID ends …{new_id[-6:]}")
    user_id: int = query.from_user.id
    page = context.user_data.get("_mp_page", 0)
    return await _render_my_posts(query, user_id, page, context)


# ---------------------------------------------------------------------------
# Handlers — Save as Template
# ---------------------------------------------------------------------------

async def cb_mpp_save_tmpl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt user for a template name after tapping 💾 Template."""
    from database import get_post
    query = update.callback_query
    if not query or not query.from_user:
        return MY_POSTS
    post_id = (query.data or "").replace(CB_MPP_SAVE_TMPL, "")
    try:
        post = await get_post(post_id)
    except Exception as exc:
        await query.answer(f"Error: {exc}", show_alert=True)
        return MY_POSTS
    if not post:
        await query.answer("Post not found.", show_alert=True)
        return MY_POSTS
    await query.answer()
    context.user_data["_save_tmpl_post_id"] = post_id
    content = post.get("content") or {}
    preview = content.get("text") or f"[{content.get('media_type', 'media')}]"
    await query.edit_message_text(
        f"💾 *Save as Template*\n\n"
        f"Content preview: _{str(preview)[:80]}_\n\n"
        "📝 Type a short name for this template (e.g. *Weekly Promo*):",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data=CB_MY_POSTS)],
        ]),
    )
    return MY_POSTS_SAVE_TEMPLATE


async def recv_template_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the template name from the user and save the template."""
    from database import get_post, save_template
    if not update.message or not update.effective_user:
        return MY_POSTS_SAVE_TEMPLATE
    name    = (update.message.text or "").strip()[:50]
    if not name:
        await update.message.reply_text("⚠️ Please enter a valid name.")
        return MY_POSTS_SAVE_TEMPLATE
    post_id = context.user_data.get("_save_tmpl_post_id")
    user_id = update.effective_user.id
    try:
        post = await get_post(post_id)
        if not post:
            raise ValueError("Post not found")
        content = post.get("content") or {}
        tmpl_id = await save_template(user_id, name, content)
    except Exception as exc:
        logger.exception("save_template failed: %s", exc)
        await update.message.reply_text(
            f"❌ Error saving template: `{str(exc).replace(chr(96), chr(39))}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return MY_POSTS_SAVE_TEMPLATE
    context.user_data.pop("_save_tmpl_post_id", None)
    await update.message.reply_text(
        f"✅ *Template saved!*\n\n"
        f"📝 Name: *{name}*\n"
        f"🆔 ID: `{tmpl_id}`\n\n"
        "Access from *📝 Templates* in the Main Menu.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data=CB_MAIN_MENU)]]),
    )
    return MY_POSTS


# ---------------------------------------------------------------------------
# ConversationHandler factory
# ---------------------------------------------------------------------------

def build_base_conversation() -> ConversationHandler:
    """
    Root ConversationHandler — handles /start, navigation, help, cancel,
    and the full My Posts management flow (list / pause / resume / delete).

    Action entry-points for Schedule New Post, Manage Queue, and Media Pool are
    intentionally NOT registered here — they live as entry_points in their own
    higher-priority ConversationHandlers so those wizards always intercept first.
    """
    nav_pattern = f"^({CB_HELP}|{CB_CANCEL}|{CB_MAIN_MENU})$"

    # Patterns that contain a 24-char MongoDB ObjectId suffix
    oid = r"[0-9a-f]{24}"

    return ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(cb_help,      pattern=f"^{CB_HELP}$"),
                CallbackQueryHandler(cb_main_menu, pattern=f"^{CB_MAIN_MENU}$"),
                CallbackQueryHandler(cb_cancel,    pattern=f"^{CB_CANCEL}$"),
                CallbackQueryHandler(cb_my_posts,  pattern=f"^{CB_MY_POSTS}$"),
                CallbackQueryHandler(cb_my_stats,  pattern=f"^{CB_MY_STATS}$"),
            ],
            MY_POSTS: [
                CallbackQueryHandler(cb_my_posts,       pattern=f"^{CB_MY_POSTS}$"),
                CallbackQueryHandler(cb_mpp_filter,     pattern=rf"^{CB_MPP_FILTER}(?:all|pending|paused|posted|failed)$"),
                CallbackQueryHandler(cb_mpp_page,       pattern=rf"^{CB_MPP_PAGE}\d+$"),
                CallbackQueryHandler(cb_mpp_pause,      pattern=rf"^{CB_MPP_PAUSE}{oid}$"),
                CallbackQueryHandler(cb_mpp_resume,     pattern=rf"^{CB_MPP_RESUME}{oid}$"),
                CallbackQueryHandler(cb_mpp_del,        pattern=rf"^{CB_MPP_DEL}{oid}$"),
                CallbackQueryHandler(cb_mpp_clone,      pattern=rf"^{CB_MPP_CLONE}{oid}$"),
                CallbackQueryHandler(cb_mpp_save_tmpl,  pattern=rf"^{CB_MPP_SAVE_TMPL}{oid}$"),
                CallbackQueryHandler(cb_my_stats,       pattern=f"^{CB_MY_STATS}$"),
                CallbackQueryHandler(cb_main_menu,      pattern=f"^{CB_MAIN_MENU}$"),
                CallbackQueryHandler(cb_cancel,         pattern=f"^{CB_CANCEL}$"),
            ],
            MY_POSTS_CONFIRM_DELETE: [
                CallbackQueryHandler(cb_mpp_del_yes, pattern=rf"^{CB_MPP_DEL_YES}{oid}$"),
                CallbackQueryHandler(cb_my_posts,    pattern=f"^{CB_MY_POSTS}$"),
                CallbackQueryHandler(cb_main_menu,   pattern=f"^{CB_MAIN_MENU}$"),
                CallbackQueryHandler(cb_cancel,      pattern=f"^{CB_CANCEL}$"),
            ],
            MY_POSTS_CONFIRM_CLONE: [
                CallbackQueryHandler(cb_mpp_clone_yes, pattern=rf"^{CB_MPP_CLONE_YES}{oid}$"),
                CallbackQueryHandler(cb_my_posts,      pattern=f"^{CB_MY_POSTS}$"),
                CallbackQueryHandler(cb_main_menu,     pattern=f"^{CB_MAIN_MENU}$"),
                CallbackQueryHandler(cb_cancel,        pattern=f"^{CB_CANCEL}$"),
            ],
            MY_POSTS_SAVE_TEMPLATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recv_template_name),
                CallbackQueryHandler(cb_my_posts,  pattern=f"^{CB_MY_POSTS}$"),
                CallbackQueryHandler(cb_main_menu, pattern=f"^{CB_MAIN_MENU}$"),
                CallbackQueryHandler(cb_cancel,    pattern=f"^{CB_CANCEL}$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_main_menu, pattern=nav_pattern),
        ],
        allow_reentry=True,
        name="base_conversation",
        persistent=False,
    )
