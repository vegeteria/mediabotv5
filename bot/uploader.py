from pyrogram.enums import ParseMode
import asyncio
import os
import re
from pathlib import Path

from bot.config import logger
from bot.downloader import ProgressTracker



class RcloneUploader:

    @staticmethod
    async def upload(local_path: Path, remote_dir: str, message, user_id=None, user_display=None) -> str:
        remote_name = os.getenv("RCLONE_REMOTE", "gdrive")
        remote_base = os.getenv("RCLONE_BASE_DIR", "").strip()
        
        if remote_base:
            remote_path = f"{remote_name}:{remote_base}/{remote_dir}"
        else:
            remote_path = f"{remote_name}:{remote_dir}"
            
        remote_path = remote_path.replace("//", "/")
        
        logger.info(f"Starting rclone upload: {local_path} to {remote_path}")
        
        from bot.state import GLOBAL_TASKS
        import asyncio
        global_task = None
        task_id = None
        current_asyncio_task = asyncio.current_task()
        for k, v in list(GLOBAL_TASKS.items()):
            if getattr(v, "asyncio_task", None) == current_asyncio_task:
                global_task = v
                task_id = k
                break
                
        created_fallback_task = False
        if not global_task:
            # Fallback if not found
            from bot.state import GlobalTask
            import uuid
            task_id = str(uuid.uuid4())
            global_task = GlobalTask()
            global_task.asyncio_task = current_asyncio_task
            global_task.chat_id = getattr(message, "chat_id", None) or (message.chat.id if hasattr(message, "chat") else None)
            global_task.user_id = user_id
            global_task.user_display = user_display
            GLOBAL_TASKS[task_id] = global_task
            created_fallback_task = True
        else:
            if not user_id and getattr(global_task, "user_id", None):
                user_id = global_task.user_id
            if (not user_display or user_display == "Unknown") and getattr(global_task, "user_display", None):
                user_display = global_task.user_display
            
        global_task.message = f"⬆️ <b>Uploading:</b> <code>{local_path.name}</code>\n⏳ Starting upload..."
        
        cmd = [
            "rclone", "copy", str(local_path), remote_path,
            "--drive-chunk-size", "16M",      # Rapid-fire chunks are REQUIRED to bypass Oracle TCP shaping
            "--transfers", "4",
            "--checkers", "8",
            "--multi-thread-streams", "4",
            "--use-mmap",
            "--bind", "0.0.0.0",              # Bypass buggy Oracle IPv6 peering
            "--disable-http2",                # Prevents HTTP/2 multiplexing from getting caught by single-connection QoS
            "--fast-list",
            "-P",
            "--stats", "2s"
        ]
        
        # Inject SOCKS5 Proxy if provided in .env to bypass Oracle DPI/QoS!
        proxy_url = os.getenv("SOCKS5_PROXY")
        custom_env = None
        if proxy_url:
            custom_env = os.environ.copy()
            custom_env["HTTP_PROXY"] = proxy_url
            custom_env["HTTPS_PROXY"] = proxy_url
            
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=custom_env
        )
        
        # Give the OS time to flush massive files (e.g. 18GB+ 4K movies) to disk
        # before rclone scans the directory. This prevents rclone from seeing an 
        # empty directory and exiting instantly with a false success code.
        await asyncio.sleep(10)
        
        regex = re.compile(r"Transferred:\s+([\d.]+\s+[KMG]?iB)\s+/\s+([\d.]+\s+[KMG]?iB),\s+(\d+)%,\s+([\d.]+\s+[KMG]?iB/s),\s+ETA\s+(.*)")
        
        last_update = 0
        last_msg_text = None
        
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                
                line = line.decode('utf-8', errors='ignore').strip()
                match = regex.search(line)
                if match:
                    transferred = match.group(1)
                    total = match.group(2)
                    percent = int(match.group(3))
                    speed = match.group(4)
                    eta = match.group(5)
                    
                    import time
                    from bot.config import PROGRESS_UPDATE_DELAY
                    current_time = time.time()
                    if current_time - last_update < PROGRESS_UPDATE_DELAY:
                        continue
                    last_update = current_time
                    
                    filled = int(percent / 10)
                    bar = "█" * filled + "░" * (10 - filled)
                    msg = (
                        f"⬆️ <b>Uploading:</b> <code>{local_path.name}</code>\n"
                        f"<code>[{bar}] {percent}%</code>\n"
                        f"<b>Size:</b> <code>{total}</code> | <b>Done:</b> <code>{transferred}</code>\n"
                        f"<b>Speed:</b> <code>{speed}</code> | <b>ETA:</b> <code>{eta}</code>"
                    )
                    
                    if last_msg_text != msg:
                        last_msg_text = msg
                        global_task.message = msg

            await process.wait()
            
            if process.returncode != 0:
                raise Exception(f"Rclone upload failed with return code {process.returncode}")
        except asyncio.CancelledError:
            try:
                process.kill()
                await process.wait()
            except OSError:
                pass
            raise
        finally:
            if created_fallback_task:
                from bot.state import GLOBAL_TASKS
                GLOBAL_TASKS.pop(task_id, None)
        return remote_path

async def perform_autorclone(local_path: Path, remote_folder: str, message, user_id=None, user_display="Unknown", silent=False) -> str:
    from bot.downloader import ProgressTracker
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from bot.config import GLOBAL_DASHBOARD_GROUPS
    import os
    import shutil
    from urllib.parse import quote
    
    try:
        remote_path = await RcloneUploader.upload(local_path, remote_folder, message, user_id=user_id, user_display=user_display)
    except asyncio.CancelledError:
        try:
            if local_path.is_file():
                local_path.unlink(missing_ok=True)
            elif local_path.is_dir():
                shutil.rmtree(local_path, ignore_errors=True)
        except Exception:
            pass
        if not silent:
            await message.edit_text("🚫 Upload cancelled.")
        raise
    except Exception as e:
        if not silent:
            await message.edit_text(f"❌ Rclone upload failed: {e}")
        return ""
        
    index_url = os.getenv("INDEX_URL", "")
    cloud_link_base = os.getenv("CLOUD_LINK_BASE", "")
    
    # Determine the exact remote path for the uploaded item
    is_uuid_wrapper = local_path.is_dir() and len(local_path.name) == 8 and local_path.name.isalnum()
    if is_uuid_wrapper:
        contents = list(local_path.iterdir())
        if len(contents) == 1 and contents[0].is_dir():
            # e.g. /mbseries creates a subfolder inside the uuid wrapper
            uploaded_name = contents[0].name
            final_remote_path = f"{remote_path}/{uploaded_name}"
        else:
            # e.g. /mbmovie dumps files directly, and remote_folder is already Movies/Folder
            final_remote_path = remote_path
    elif local_path.is_file():
        final_remote_path = f"{remote_path}/{local_path.name}"
    else:
        final_remote_path = remote_path

    try:
        if local_path.is_file():
            local_path.unlink(missing_ok=True)
        elif local_path.is_dir():
            shutil.rmtree(local_path, ignore_errors=True)
    except Exception as e:
        logger.error(f"Failed to delete local path {local_path}: {e}")
    
    if not silent:
        kb = []
        
        remote_path_no_drive = final_remote_path.split(":", 1)[-1].strip("/")
        
        if cloud_link_base:
            cloud_url = f"{cloud_link_base}/{quote(remote_path_no_drive)}"
            if not local_path.is_file() and not cloud_url.endswith("/"):
                cloud_url += "/"
            kb.append([InlineKeyboardButton("☁️ Cloud Link", url=cloud_url)])
            
        if index_url:
            index_url = index_url if index_url.endswith("/") else f"{index_url}/"
            path_query = quote(remote_path_no_drive)
            if not local_path.is_file() and not path_query.endswith("/"):
                path_query += "/"
            kb.append([InlineKeyboardButton("⚡ Index Link", url=f"{index_url}{path_query}")])
                
        final_msg = (
            f"✅ **Process Complete!**\n\n"
            f"📁 **Item:** `{final_remote_path.split('/')[-1]}`\n"
            f"📍 **Remote:** `{final_remote_path}`"
        )
        
        reply_markup = InlineKeyboardMarkup(kb) if kb else None
        
        final_bot_msg = None
        if user_id:
            try:
                final_bot_msg = await message._client.send_message(
                    chat_id=user_id,
                    text=final_msg,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            except Exception:
                pass
                
        group_msg = f"🎉 **Upload Complete for {user_display}!**\n\n" + final_msg.replace("✅ **Process Complete!**\n\n", "")

        for group_id in GLOBAL_DASHBOARD_GROUPS:
            try:
                await message._client.send_message(
                    chat_id=group_id,
                    text=group_msg,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            except Exception:
                pass
                
        try:
            await message.delete()
        except Exception:
            pass
    else:
        final_bot_msg = None
    
    return remote_path, final_bot_msg



