from pyrogram.enums import ParseMode
import asyncio
import re
from pathlib import Path

import aiohttp

from bot.config import BASE_SERIES, logger
from bot.downloader import AsyncDownloader, ProgressTracker
from bot.state import task_manager, GlobalTask, USER_STATES
from bot.organizer import continue_series_processing

async def handle_multipart_series(client, message, urls: list[str], explicit_series_name: str, user_id: int):
    status_msg = await message.reply_text("🔍 **Probing Links...**\nFetching headers to determine archive types...", parse_mode=ParseMode.MARKDOWN)
    
    qtask = GlobalTask()
    from bot.config import get_base_url
    dashboard_link = f"{get_base_url()}/dashboard"
    qtask.asyncio_task = asyncio.current_task()
    qtask.chat_id = status_msg.chat.id
    qtask.user_id = user_id
    user_display = message.from_user.username or message.from_user.first_name
    qtask.user_display = f"@{user_display}" if message.from_user.username else str(user_display)
    await task_manager.acquire(qtask, client)

    try:
        from bot.state import update_status_msg
        
        # Probing
        split_volumes = []
        independent = []
        for i, url in enumerate(urls, 1):
            await update_status_msg(status_msg, f"🔍 **Probing Links ({i}/{len(urls)})...**")
            filename = await AsyncDownloader.probe_filename(url)
            if re.search(r'\.part[0-9]+\.rar$|\.r[0-9]{2,3}$|\.7z\.[0-9]{3}$|\.z[0-9]{2,3}$', filename, re.IGNORECASE):
                split_volumes.append((url, filename))
            else:
                independent.append((url, filename))
                
        is_split = len(split_volumes) > 0
        unorganized_dir = BASE_SERIES / ".unorganized"
        
        from bot.config import IS_DUPLICATE_ALLOWED
        if not IS_DUPLICATE_ALLOWED:
            from bot.helpers import extract_quality, check_season_exists_in_cloud, parse_series_archive_filename
            first_filename = split_volumes[0][1] if is_split else independent[0][1]
            q = extract_quality(first_filename)
            parsed = parse_series_archive_filename(first_filename)
            if parsed:
                s_name, s_num, _ = parsed
                s_name = explicit_series_name or s_name
                if q and s_num is not None:
                    if check_season_exists_in_cloud(s_name, s_num, q):
                        await update_status_msg(status_msg, f"❌ **Aborted:** Season {s_num} of **{s_name}** already exists in **{q}** on your server!")
                        return
        
        if is_split:
            if len(urls) == 1:
                await update_status_msg(status_msg, f"❌ **Missing Volumes!**\nYou provided a split volume (e.g. `.part1.rar`), but no other parts.\n\nTrue split archives require **all parts** to be downloaded together. Please send all links in a single message separated by newlines.")
                return
            await update_status_msg(status_msg, f"⚠️ **Split Volumes Detected!**\nDownloading {len(urls)} parts simultaneously...")
            # Download all at once
            tasks = []
            tracker = ProgressTracker(status_msg, 0, user_id=user_id, user_display=qtask.user_display) # Note: progress might be glitchy with multiple concurrent, but we can just use 1 tracker or wait
            
            # Better: download sequentially to unorganized_dir to avoid UI spam, but all must be downloaded before extraction
            downloaded_paths = []
            for i, (url, filename) in enumerate(split_volumes + independent, 1):
                await update_status_msg(status_msg, f"⬇️ **Downloading Part {i}/{len(urls)}**:\n`{filename}`")
                tracker = ProgressTracker(status_msg, 0, user_id=user_id, user_display=qtask.user_display, title_prefix=f"(Part {i}/{len(urls)})")
                path = await AsyncDownloader.download(url, unorganized_dir, tracker, user_id=user_id)
                downloaded_paths.append(path)
                
            # Now we have all parts downloaded. We just pass the FIRST split volume to continue_series_processing!
            first_vol = downloaded_paths[0]
            
            # Check password on the first volume
            process_check = await asyncio.create_subprocess_exec(
                "7z", "l", "-slt", "-pDUMMY_PW", str(first_vol),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout_check, _ = await process_check.communicate()
            output_check = stdout_check.decode(errors="replace")

            if "Encrypted = +" in output_check or "Wrong password" in output_check:
                from bot.state import GLOBAL_TASKS
                curr_task = asyncio.current_task()
                task_id = next((k for k, v in GLOBAL_TASKS.items() if getattr(v, "asyncio_task", None) == curr_task), None)
                
                USER_STATES[user_id] = {
                    "step": "wait_series_password",
                    "filepath": str(first_vol),
                    "explicit_series_name": explicit_series_name,
                    "task_id": task_id,
                }
                await update_status_msg(status_msg, f"🔒 The archive `{first_vol.name}` is password protected.\n\nPlease enter the **password**:")
                return

            await continue_series_processing(first_vol, explicit_series_name, status_msg, user_id, password=None)
            
        else:
            if len(urls) > 1:
                await update_status_msg(status_msg, f"📦 **Independent Archives Detected!**\nWill download & extract sequentially to save disk space.")
            else:
                await update_status_msg(status_msg, f"📦 **Archive Detected!**\nStarting download...")
            
            # Determine series name from first archive
            first_url, first_filename = independent[0]
            
            # Download ONLY the first one to check password and name
            tracker = ProgressTracker(status_msg, 0, user_id=user_id, user_display=qtask.user_display)
            first_path = await AsyncDownloader.download(first_url, unorganized_dir, tracker, user_id=user_id)
            
            process_check = await asyncio.create_subprocess_exec(
                "7z", "l", "-slt", "-pDUMMY_PW", str(first_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout_check, _ = await process_check.communicate()
            output_check = stdout_check.decode(errors="replace")
            
            if "Encrypted = +" in output_check or "Wrong password" in output_check:
                from bot.state import GLOBAL_TASKS
                curr_task = asyncio.current_task()
                task_id = next((k for k, v in GLOBAL_TASKS.items() if getattr(v, "asyncio_task", None) == curr_task), None)
                
                USER_STATES[user_id] = {
                    "step": "wait_series_password",
                    "filepath": str(first_path),
                    "explicit_series_name": explicit_series_name,
                    "task_id": task_id,
                    "multipart_urls": [u for u, _ in independent[1:]] # Save remaining urls!
                }
                await update_status_msg(status_msg, f"🔒 The archive `{first_path.name}` is password protected.\n\nPlease enter the **password**:")
                return
                
            # If no password, we store remaining urls in a special state or we just hijack continue_series_processing?
            # Actually, the easiest way is to modify continue_series_processing to accept multipart_urls!
            await continue_series_processing(first_path, explicit_series_name, status_msg, user_id, password=None, multipart_urls=[u for u, _ in independent[1:]])

    except asyncio.CancelledError:
        await status_msg.edit_text("🚫 Series batch download cancelled.")
        raise
    except Exception as e:
        logger.exception("Series batch download error")
        await status_msg.edit_text(f"❌ Error: {str(e)}")
    finally:
        await task_manager.release(client)
