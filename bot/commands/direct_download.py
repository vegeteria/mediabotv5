from pyrogram.enums import ParseMode
import asyncio
"""
Direct-link download commands: /movie, /episode, /series
"""

import shutil
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.auth import require_auth
from bot.config import BASE_MOVIES, BASE_SERIES, logger
from bot.state import USER_STATES, USER_TASKS, check_concurrency_limit, register_user_task
from bot.helpers import validate_url

# ── /movie ───────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("movie"))
@require_auth
async def download_movie(client: Client, message: Message):
    """Handle /movie command."""
    user_id = message.from_user.id
    if not check_concurrency_limit(user_id):
        await message.reply_text("❌ You already have an active process. Please wait or use /cancel.")
        return

    register_user_task(user_id, asyncio.current_task())

    if message.reply_to_message and (message.reply_to_message.document or message.reply_to_message.video):
        from bot.downloader import AsyncDownloader
        task_id = __import__("uuid").uuid4().hex[:8]
        from bot.state import GLOBAL_TASKS, GlobalTask
        qtask = GlobalTask()
        qtask.id = task_id
        qtask.user_id = user_id
        user_display = message.from_user.username or message.from_user.first_name
        qtask.user_display = f"@" + user_display if message.from_user.username else str(user_display)
        qtask.asyncio_task = asyncio.current_task()
        qtask.chat_id = message.chat.id
        s_info = f"\n🔗 <b>Type:</b> <code>Direct Download</code>"
        qtask.static_info = s_info
        GLOBAL_TASKS[task_id] = qtask
        from bot.config import get_base_url
        dashboard_link = f"{get_base_url()}/dashboard"
        status_msg = await message.reply_text(
            f"📥 Starting download...\n\n🌐 [Open Dashboard]({dashboard_link}) | Task ID: `{task_id}`",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        try:
            unorganized_dir = BASE_MOVIES / ".unorganized"
            from bot.downloader import ProgressTracker
            tracker = ProgressTracker(status_msg, 0, user_id=user_id, task_id=task_id)
            filepath = await AsyncDownloader.download_telegram_media(message.reply_to_message, unorganized_dir, tracker, user_id=user_id)
            
            from bot.state import CALLBACK_STATES
            state = {
                "filepath": str(filepath),
                "type": "movie",
                "opt_audio": False,
                "opt_mkvmerge": False,
                "task_id": task_id
            }
            CALLBACK_STATES[task_id] = state
            from bot.commands.dd_callbacks import probe_and_show_options
            await probe_and_show_options(status_msg._client, status_msg, state)
        except Exception as e:
            await status_msg.edit_text(f"❌ MTProto Download Failed: {e}")
            from bot.state import GLOBAL_TASKS
            GLOBAL_TASKS.pop(task_id, None)
        return

    if len(message.command) < 2:
        await message.reply_text("Usage: `/movie <url>` or reply to a video/document.", parse_mode=ParseMode.MARKDOWN)
        return

    url = message.command[1]

    if not validate_url(url):
        await message.reply_text("❌ Invalid URL format.")
        return

    try:
        from bot.direct_link_generator import direct_link_generator
        bypass_url = direct_link_generator(url)
        if bypass_url:
            url = bypass_url[0] if isinstance(bypass_url, tuple) else bypass_url
    except Exception:
        pass

    unorganized_dir = BASE_MOVIES / ".unorganized"

    from bot.commands.dd_callbacks import handle_direct_link_probe
    await handle_direct_link_probe(message, user_id, url, "movie")


# ── /episode ─────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("episode"))
@require_auth
async def download_episode(client: Client, message: Message):
    """Handle /episode command."""
    user_id = message.from_user.id
    if not check_concurrency_limit(user_id):
        await message.reply_text("❌ You already have an active process. Please wait or use /cancel.")
        return

    register_user_task(user_id, asyncio.current_task())

    if message.reply_to_message and (message.reply_to_message.document or message.reply_to_message.video):
        from bot.downloader import AsyncDownloader
        task_id = __import__("uuid").uuid4().hex[:8]
        from bot.state import GLOBAL_TASKS, GlobalTask
        qtask = GlobalTask()
        qtask.id = task_id
        qtask.user_id = user_id
        user_display = message.from_user.username or message.from_user.first_name
        qtask.user_display = f"@" + user_display if message.from_user.username else str(user_display)
        qtask.asyncio_task = asyncio.current_task()
        qtask.chat_id = message.chat.id
        s_info = f"\n🔗 <b>Type:</b> <code>Direct Download</code>"
        qtask.static_info = s_info
        GLOBAL_TASKS[task_id] = qtask
        from bot.config import get_base_url
        dashboard_link = f"{get_base_url()}/dashboard"
        status_msg = await message.reply_text(
            f"📥 Starting download...\n\n🌐 [Open Dashboard]({dashboard_link}) | Task ID: `{task_id}`",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        try:
            unorganized_dir = BASE_SERIES / ".unorganized"
            from bot.downloader import ProgressTracker
            tracker = ProgressTracker(status_msg, 0, user_id=user_id, task_id=task_id)
            filepath = await AsyncDownloader.download_telegram_media(message.reply_to_message, unorganized_dir, tracker, user_id=user_id)
            
            from bot.state import CALLBACK_STATES
            state = {
                "filepath": str(filepath),
                "type": "episode",
                "opt_audio": False,
                "opt_mkvmerge": False,
                "task_id": task_id
            }
            CALLBACK_STATES[task_id] = state
            from bot.commands.dd_callbacks import probe_and_show_options
            await probe_and_show_options(status_msg._client, status_msg, state)
        except Exception as e:
            await status_msg.edit_text(f"❌ MTProto Download Failed: {e}")
            from bot.state import GLOBAL_TASKS
            GLOBAL_TASKS.pop(task_id, None)
        return

    if len(message.command) < 2:
        await message.reply_text(
            "Usage: `/episode <url>` or reply to a video/document.\n\n"
            "Example: `/episode https://example.com/The.Office.S04E05.mkv`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    url = message.command[1]

    if not validate_url(url):
        await message.reply_text("❌ Invalid URL format.")
        return

    try:
        from bot.direct_link_generator import direct_link_generator
        bypass_url = direct_link_generator(url)
        if bypass_url:
            url = bypass_url[0] if isinstance(bypass_url, tuple) else bypass_url
    except Exception:
        pass

    unorganized_dir = BASE_SERIES / ".unorganized"

    from bot.commands.dd_callbacks import handle_direct_link_probe
    await handle_direct_link_probe(message, user_id, url, "episode")


# ── /series ──────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("series"))
@require_auth
async def download_series(client: Client, message: Message):
    """Handle /series command."""
    user_id = message.from_user.id
    if not check_concurrency_limit(user_id):
        await message.reply_text("❌ You already have an active process. Please wait or use /cancel.")
        return

    register_user_task(user_id, asyncio.current_task())

    if message.reply_to_message and (message.reply_to_message.document or message.reply_to_message.video):
        from bot.downloader import AsyncDownloader
        task_id = __import__("uuid").uuid4().hex[:8]
        from bot.state import GLOBAL_TASKS, GlobalTask
        qtask = GlobalTask()
        qtask.id = task_id
        qtask.user_id = user_id
        user_display = message.from_user.username or message.from_user.first_name
        qtask.user_display = f"@" + user_display if message.from_user.username else str(user_display)
        qtask.asyncio_task = asyncio.current_task()
        qtask.chat_id = message.chat.id
        s_info = f"\n🔗 <b>Type:</b> <code>Direct Download</code>"
        qtask.static_info = s_info
        GLOBAL_TASKS[task_id] = qtask
        from bot.config import get_base_url
        dashboard_link = f"{get_base_url()}/dashboard"
        status_msg = await message.reply_text(
            f"📥 Starting download...\n\n🌐 [Open Dashboard]({dashboard_link}) | Task ID: `{task_id}`",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        try:
            unorganized_dir = BASE_SERIES / ".unorganized"
            from bot.downloader import ProgressTracker
            tracker = ProgressTracker(status_msg, 0, user_id=user_id, task_id=task_id)
            filepath = await AsyncDownloader.download_telegram_media(message.reply_to_message, unorganized_dir, tracker, user_id=user_id)
            
            from bot.state import CALLBACK_STATES
            state = {
                "filepath": str(filepath),
                "type": "episode",
                "opt_audio": False,
                "opt_mkvmerge": False,
                "task_id": task_id
            }
            CALLBACK_STATES[task_id] = state
            from bot.commands.dd_callbacks import probe_and_show_options
            await probe_and_show_options(status_msg._client, status_msg, state)
        except Exception as e:
            await status_msg.edit_text(f"❌ MTProto Download Failed: {e}")
            from bot.state import GLOBAL_TASKS
            GLOBAL_TASKS.pop(task_id, None)
        return

    if len(message.command) < 2:
        await message.reply_text(
            "Usage: `/series <url>` or reply to a video/document.\n\n"
            "Example: `/series https://example.com/Breaking.Bad.S01.zip`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    urls = []
    name_parts = []
    for arg in message.command[1:]:
        if validate_url(arg):
            urls.append(arg)
        else:
            name_parts.append(arg)
            
    explicit_series_name = " ".join(name_parts) if name_parts else None

    if not urls:
        await message.reply_text("❌ No valid URLs found.")
        return

    # Attempt direct link bypass for all URLs
    final_urls = []
    for u in urls:
        try:
            from bot.direct_link_generator import direct_link_generator
            bypass_url = direct_link_generator(u)
            if bypass_url:
                final_urls.append(bypass_url[0] if isinstance(bypass_url, tuple) else bypass_url)
            else:
                final_urls.append(u)
        except Exception:
            final_urls.append(u)
            
    from bot.commands.multipart_handler import handle_multipart_series
    # Note: handle_multipart_series will need to be rewritten for Hydrogram too!
    await handle_multipart_series(client, message, final_urls, explicit_series_name, user_id)
