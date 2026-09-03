import asyncio
"""
Download engine – progress tracking and async HTTP downloading.
"""

import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import aiohttp
import aiofiles


class ProgressTracker:
    """Tracks download progress and updates a Telegram message."""

    def __init__(self, message, total_size: int, user_id=None, user_display="Unknown", title_prefix="", task_id=None):
        self.message = message
        self.total_size = total_size
        self.downloaded = 0
        self.start_time = time.time()
        self.last_update = 0
        self.title_prefix = title_prefix
        from bot.config import PROGRESS_UPDATE_DELAY
        self.update_interval = PROGRESS_UPDATE_DELAY
        self.blocks = 1
        
        from bot.state import GLOBAL_TASKS
        import asyncio
        self.global_task = None
        self.task_id = None
        current_asyncio_task = asyncio.current_task()
        for k, v in list(GLOBAL_TASKS.items()):
            if getattr(v, "asyncio_task", None) == current_asyncio_task:
                self.global_task = v
                self.task_id = k
                break
                
        self.created_fallback_task = False
        if not self.global_task:
            from bot.state import GlobalTask
            import uuid
            self.task_id = task_id if task_id else str(uuid.uuid4())
            self.global_task = GlobalTask()
            self.global_task.asyncio_task = current_asyncio_task
            self.global_task.chat_id = message.chat.id
            self.global_task.user_id = user_id
            self.global_task.user_display = user_display
            GLOBAL_TASKS[self.task_id] = self.global_task
            self.created_fallback_task = True
            
        self.global_task.message = "📥 <b>Downloading...</b>\n⏳ Starting download engine..." 
        

    def cleanup(self):
        if self.created_fallback_task:
            from bot.state import GLOBAL_TASKS
            GLOBAL_TASKS.pop(self.task_id, None)

    # ── formatting helpers ───────────────────────────────────────────────
    @staticmethod
    def _format_size(size: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    @staticmethod
    def _format_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    # ── update callback ──────────────────────────────────────────────────
    async def update(self, chunk_size: int):
        self.downloaded += chunk_size
        current_time = time.time()

        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time

        if self.total_size > 0:
            percent = min(100, (self.downloaded / self.total_size) * 100)
            filled = int(percent / 10)
            bar = "█" * filled + "░" * (10 - filled)
        else:
            percent = 0
            bar = "░" * 10

        elapsed = current_time - self.start_time
        speed = self.downloaded / elapsed if elapsed > 0 else 0
        speed_str = f"{self._format_size(speed)}/s" if elapsed > 0 else "0 B/s"
        
        if self.total_size > 0 and speed > 0:
            remaining = (self.total_size - self.downloaded) / speed
            eta_str = self._format_time(remaining)
        else:
            eta_str = "00:00:00"
            
        dl_str = self._format_size(self.downloaded)
        tot_str = self._format_size(self.total_size) if self.total_size > 0 else "?? B"
        title = getattr(self, "filename", "Unknown")
        if getattr(self, "title_prefix", ""):
            title = f"{self.title_prefix} {title}"

        progress_msg = (
            f"📥 <b>Downloading:</b> <code>{title}</code>\n"
            f"<code>[{bar}] {percent:.1f}%</code>\n"
            f"<b>Size:</b> <code>{tot_str}</code> | <b>Done:</b> <code>{dl_str}</code>\n"
            f"<b>Speed:</b> <code>{speed_str}</code> | <b>ETA:</b> <code>{eta_str}</code>"
        )
        if getattr(self, "blocks", 1) > 1:
            progress_msg += f"\n🚀 Speed Boost Active ({self.blocks} blocks)"
        elif getattr(self, "smart_fallback", False):
            progress_msg += f"\n⚠️ Multi-part unsupported by server (Forced 1 connection)"
            
        if getattr(self, "last_msg_text", None) != progress_msg:
            self.last_msg_text = progress_msg
            if hasattr(self, "global_task"):
                self.global_task.message = progress_msg
                



class AsyncDownloader:
    """Handles async file downloads with progress tracking."""

    @staticmethod
    def extract_filename(url: str, headers: dict) -> str:
        """Extract filename from URL or Content-Disposition header."""
        cd = headers.get("Content-Disposition", "")
        if "filename=" in cd:
            match = re.search(r'filename[*]?=["\']?([^"\';]+)', cd)
            if match:
                return unquote(match.group(1))

        parsed = urlparse(url)
        path = unquote(parsed.path)
        filename = os.path.basename(path)

        if not filename or filename.lower() == "download":
            ct = headers.get("Content-Type", "")
            ext = ""
            import mimetypes
            if ct:
                ext = mimetypes.guess_extension(ct.split(";")[0]) or ""
            if not ext and "video" in ct:
                if "mp4" in ct:
                    ext = ".mp4"
                elif "matroska" in ct:
                    ext = ".mkv"
                elif "webm" in ct:
                    ext = ".webm"
            filename = f"download{ext}"

        return filename if filename else "download"

    @staticmethod
    async def probe_filename(url: str) -> str:
        """Fetch headers to determine the real filename without downloading."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                # Try HEAD first, as it's the fastest
                async with session.head(url, allow_redirects=True, timeout=5) as resp:
                    if resp.status != 405: # 405 means Method Not Allowed
                        return AsyncDownloader.extract_filename(str(resp.url), resp.headers)
                
                # Fallback to GET if HEAD is rejected by dumb servers
                async with session.get(url, allow_redirects=True, timeout=5) as get_resp:
                    return AsyncDownloader.extract_filename(str(get_resp.url), get_resp.headers)
        except Exception as e:
            from bot.config import logger
            logger.warning(f"Failed to probe filename for {url}: {e}")
            return AsyncDownloader.extract_filename(url, {})

    @staticmethod
    async def download(
        url: str,
        dest_dir: Path,
        progress_tracker: Optional[ProgressTracker] = None,
        user_id: Optional[int] = None,
    ) -> Path:
        """Download file from URL to destination directory using ThrottleBuster."""
        from throttlebuster import ThrottleBuster, DownloadTracker

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = None

        try:
            # We first need to get the filename so we can set dest_path up correctly,
            # but ThrottleBuster will also grab it.
            # To preserve our extract_filename logic, let's do a quick HEAD/GET request
            # just to get headers.
            timeout = aiohttp.ClientTimeout(total=None, connect=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, allow_redirects=True, headers={"Range": "bytes=0-0"}) as response:
                    filename = AsyncDownloader.extract_filename(
                        url, dict(response.headers)
                    )
                    # Smart Detection: If the server returns 200 OK instead of 206 Partial Content,
                    # it means the server ignores Range requests and will corrupt multi-part downloads!
                    supports_range = response.status == 206
            
            dest_path = dest_dir / filename
            if progress_tracker:
                progress_tracker.filename = filename
            
            # Create a callback adapter for ThrottleBuster to ProgressTracker
            async def tb_progress_hook(data: DownloadTracker):
                if progress_tracker:
                    # ThrottleBuster's data.downloaded_size is just the size of that specific part/chunk
                    # We need to adapt it to our ProgressTracker which expects chunk_size updates
                    # Actually, our ProgressTracker is easier to bypass and set directly,
                    # or we can use the DownloadTracker's streaming_chunk_size
                    progress_tracker.total_size = data.expected_size
                    # But since download tracker is per part, total_size is per part!
                    pass

            # Since ProgressTracker expects simple chunks, let's use a simpler closure
            # Actually, ThrottleBuster has its own progress bar... but we want Telegram updates.
            # Let's adjust how we update the ProgressTracker. ThrottleBuster passes `DownloadTracker` which has `streaming_chunk_size`.
            
            overall_total_size = 0
            
            async def custom_progress_hook(data: DownloadTracker):
                if progress_tracker:
                    if progress_tracker.total_size == 0 and data.expected_size > 0:
                        pass
                    await progress_tracker.update(data.streaming_chunk_size)

            import httpx
            # Get real total size
            async with httpx.AsyncClient() as client:
                resp = await client.head(url, follow_redirects=True)
                overall_total_size = int(resp.headers.get("Content-Length", 0))
            
            if progress_tracker:
                progress_tracker.total_size = overall_total_size

            from bot.user_settings import user_settings
            
            if user_id:
                tasks = user_settings.get_user_throttle(user_id)
            else:
                try:
                    tasks = int(os.environ.get("THROTTLE_TASKS", 10))
                except ValueError:
                    tasks = 10
            
            if progress_tracker:
                progress_tracker.blocks = tasks

            # If the server does not support Range requests, does not report a size, or the user requested 1 task, fallback to single stream
            if not supports_range or overall_total_size == 0 or tasks <= 1:
                if progress_tracker:
                    progress_tracker.blocks = 1
                    if not supports_range:
                        progress_tracker.smart_fallback = True
                try:
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(url, allow_redirects=True) as response:
                            response.raise_for_status()
                            async with aiofiles.open(dest_path, "wb") as f:
                                async for chunk in response.content.iter_chunked(8192):
                                    await f.write(chunk)
                                    if progress_tracker:
                                        await progress_tracker.update(len(chunk))
                    return dest_path
                finally:
                    if progress_tracker:
                        progress_tracker.cleanup()

            from throttlebuster import ThrottleBuster

            # Instantiate ThrottleBuster. limit tasks to auto 5,
            # disable its own tqdm bar since we use Telegram progress.
            tb = ThrottleBuster(
                dir=dest_dir,
                tasks=tasks, 
                part_dir=dest_dir,
                suppress_incompatible_error=True,
                timeout=60.0
            )

            # ThrottleBuster's run method takes filename natively, but it might just return DownloadedFile.
            run_kwargs = {
                "filename": filename,
                "progress_hook": custom_progress_hook,
                "disable_progress_bar": True,
                "file_size": overall_total_size
            }

            async def merge_watcher():
                try:
                    while progress_tracker and getattr(progress_tracker, 'downloaded', 0) < overall_total_size:
                        await asyncio.sleep(1)
                        
                    last_size = 0
                    
                    while progress_tracker:
                        if dest_path.exists():
                            merged_size = os.path.getsize(dest_path)
                            percent = min(100.0, (merged_size / overall_total_size) * 100) if overall_total_size else 0
                            filled = int(percent / 10)
                            bar = "█" * filled + "░" * (10 - filled)
                            
                            speed = (merged_size - last_size) / 2
                            last_size = merged_size
                            speed_str = f"{ProgressTracker._format_size(speed)}/s" if speed > 0 else "Calculating..."
                            remaining = (overall_total_size - merged_size) / speed if speed > 0 else 0
                            eta_str = ProgressTracker._format_time(remaining) if speed > 0 else "00:00:00"
                            
                            msg = (
                                f"🔄 <b>Merging:</b> <code>{filename}</code>\n"
                                f"<code>[{bar}] {percent:.1f}%</code>\n"
                                f"<b>Size:</b> <code>{ProgressTracker._format_size(overall_total_size)}</code> | <b>Done:</b> <code>{ProgressTracker._format_size(merged_size)}</code>\n"
                                f"<b>Speed:</b> <code>{speed_str}</code> | <b>ETA:</b> <code>{eta_str}</code>"
                            )
                            if hasattr(progress_tracker, "global_task"):
                                progress_tracker.global_task.message = msg
                            if getattr(progress_tracker, "last_msg_text", None) != msg:
                                progress_tracker.last_msg_text = msg
                        from bot.config import PROGRESS_UPDATE_DELAY
                        await asyncio.sleep(PROGRESS_UPDATE_DELAY)
                except asyncio.CancelledError:
                    pass

            try:
                watcher = asyncio.create_task(merge_watcher()) if progress_tracker else None
                downloaded = await tb.run(
                    url, 
                    **run_kwargs
                )
                if watcher:
                    watcher.cancel()
                return Path(downloaded.saved_to)
            finally:
                import glob
                part_files = glob.glob(f"{dest_dir}/{filename}-*.part")
                for pf in part_files:
                    try:
                        os.remove(pf)
                    except OSError:
                        pass
                if progress_tracker:
                    progress_tracker.cleanup()
            
        except asyncio.CancelledError:
            if dest_path and dest_path.exists():
                dest_path.unlink()
            if progress_tracker:
                progress_tracker.cleanup()
            raise
        except Exception:
            if progress_tracker:
                progress_tracker.cleanup()
            raise


    @staticmethod
    async def download_telegram_media(
        message,
        dest_dir: Path,
        progress_tracker: Optional[ProgressTracker] = None,
        user_id: Optional[int] = None,
    ) -> Path:
        """Download MTProto media using the User Client (up to 2GB)."""
        from bot.clients import user_app
        
        if not user_app:
            raise Exception("User session is not configured! Cannot download large Telegram files.")
            
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        media = message.document or message.video or message.audio or getattr(message, "voice", None)
        filename = getattr(media, "file_name", "telegram_download.ext")
        if not hasattr(media, "file_name") and getattr(message, "voice", None):
            filename = "voice_message.ogg"
            
        dest_path = dest_dir / filename
        
        if progress_tracker:
            progress_tracker.filename = filename
            progress_tracker.total_size = getattr(media, "file_size", 0)
            
        async def progress_callback(current, total):
            if progress_tracker:
                progress_tracker.total_size = total
                # The callback provides total bytes downloaded so far. Our ProgressTracker expects chunk_size.
                # Let's adjust it by calculating the delta.
                chunk = current - progress_tracker.downloaded
                if chunk > 0:
                    await progress_tracker.update(chunk)
                    
        try:
            # We must use user_app.get_messages to fetch the exact message in the chat
            # since user_app needs its own context to download the media.
            bot_client = getattr(message, "_client", None)
            target_chat_id = message.chat.id
            target_message_id = message.id
            
            user_me = await user_app.get_me()
            forwarded = False
            
            from pyrogram.enums import ChatType
            user_msg = None
            if bot_client and message.chat.type == ChatType.PRIVATE:
                bot_me = await bot_client.get_me()
                # Forward the message to the User Client
                fwd_msg = await bot_client.forward_messages(
                    chat_id=user_me.id,
                    from_chat_id=message.chat.id,
                    message_ids=message.id
                )
                target_chat_id = bot_me.id
                forwarded = True
                
                # Fetch recent messages from the User Client's perspective to find the forwarded media
                # This avoids message ID desyncs between Bot and User Client
                async for msg in user_app.get_chat_history(bot_me.id, limit=10):
                    if msg.document or msg.video or msg.audio:
                        # Ensure it's roughly the same time or has a file
                        user_msg = msg
                        target_message_id = msg.id
                        break
            else:
                user_msg = await user_app.get_messages(target_chat_id, target_message_id)
                
            if getattr(user_msg, "empty", True) or not getattr(user_msg, "media", None):
                raise Exception(f"This message doesn't contain any downloadable media. Could not locate the media file.")
                
            await user_app.download_media(
                user_msg,
                file_name=str(dest_path),
                progress=progress_callback
            )
            
            if forwarded:
                try:
                    await user_app.delete_messages(chat_id=target_chat_id, message_ids=target_message_id)
                except Exception:
                    pass
                    
            return dest_path
        except asyncio.CancelledError:
            if dest_path.exists():
                dest_path.unlink()
            raise
        except Exception as e:
            if dest_path.exists():
                dest_path.unlink()
            raise Exception(f"MTProto Download Failed: {str(e)}")
        finally:
            if progress_tracker:
                progress_tracker.cleanup()
