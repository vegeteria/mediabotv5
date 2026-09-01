from pyrogram.enums import ParseMode
import asyncio
import os
import shutil
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.auth import require_auth
from bot.config import BASE_SONGS, logger
from bot.state import USER_STATES, USER_TASKS, check_concurrency_limit, register_user_task

@Client.on_message(filters.command("song"))
@require_auth
async def download_song(client: Client, message: Message):
    """Handle /song command."""
    user_id = message.from_user.id
    if not check_concurrency_limit(user_id):
        await message.reply_text("❌ You already have an active process. Please wait or use /cancel.")
        return

    register_user_task(user_id, asyncio.current_task())

    target_msg = None
    if message.reply_to_message and (message.reply_to_message.document or message.reply_to_message.audio):
        target_msg = message.reply_to_message
    elif message.document or message.audio:
        target_msg = message

    if target_msg:
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
        s_info = f"\n🔗 <b>Type:</b> <code>Song Download</code>"
        qtask.static_info = s_info
        GLOBAL_TASKS[task_id] = qtask
        
        from bot.config import get_base_url
        dashboard_link = f"{get_base_url()}/dashboard"
        status_msg = await message.reply_text(
            f"📥 Starting download...\n\n🌐 [Open Dashboard]({dashboard_link}) | Task ID: `{task_id}`",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
        
        from bot.state import task_manager
        await task_manager.acquire(qtask, client)
        
        unorganized_dir = BASE_SONGS / ".unorganized"
        target_dir = BASE_SONGS / task_id
        target_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            from bot.downloader import ProgressTracker
            tracker = ProgressTracker(status_msg, 0, user_id=user_id, task_id=task_id)
            filepath = await AsyncDownloader.download_telegram_media(target_msg, unorganized_dir, tracker, user_id=user_id)
            
            # Now we have the filepath, let's process it with beets.
            from bot.state import update_status_msg
            await update_status_msg(status_msg, "🎵 Processing song with beets...")
            qtask.message = f"🎵 <b>Processing with beets</b>\n⏳ Auto-tagging..."
            
            # Move file to target_dir so beets works on it there
            shutil.move(str(filepath), target_dir / filepath.name)
            process_filepath = target_dir / filepath.name
            
            # create beets config
            beets_config = target_dir / "config.yaml"
            beets_config_content = f"""
directory: {target_dir}/organized
library: {target_dir}/library.blb
import:
    move: yes
    write: yes
    autotag: yes
    quiet: yes
    singletons: yes
paths:
    default: $albumartist/$album/$track - $title
    singleton: $artist/Non-Album/$title
"""
            with open(beets_config, "w") as f:
                f.write(beets_config_content)
                
            # run beets
            cmd = ["beet", "-c", str(beets_config), "import", "-q", "-S", str(process_filepath)]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            stdout, _ = await process.communicate()
            
            # after beets, files are in target_dir/organized. Upload that.
            organized_dir = target_dir / "organized"
            if not organized_dir.exists() or not any(organized_dir.iterdir()):
                # Fallback: if beets failed to move it, just upload the original
                organized_dir = target_dir / "fallback"
                organized_dir.mkdir(parents=True, exist_ok=True)
                if process_filepath.exists():
                    shutil.move(str(process_filepath), organized_dir / process_filepath.name)
                
            from bot.uploader import perform_autorclone
            _, final_bot_msg = await perform_autorclone(organized_dir, "Songs", message, user_id=user_id, user_display=user_display)
            
            from bot.helpers import refresh_jellyfin
            for item in organized_dir.iterdir():
                if item.is_dir():
                    await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir=f"Songs/{item.name}")
            
            # If no dirs were found (e.g. fallback), just refresh Songs
            if not any(item.is_dir() for item in organized_dir.iterdir()):
                await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir="Songs")
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Song Download Failed: {e}")
        finally:
            GLOBAL_TASKS.pop(task_id, None)
            await task_manager.release(client)
            shutil.rmtree(target_dir, ignore_errors=True)
            
        return

    await message.reply_text("Usage: Reply to an audio/document with `/song`", parse_mode=ParseMode.MARKDOWN)
