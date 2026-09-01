from pyrogram.enums import ParseMode
import asyncio
"""
File & archive organisation – season detection, extraction, and folder routing.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Optional

from bot.config import BASE_SERIES, SEASON_PATTERNS, VIDEO_EXTENSIONS, logger
from bot.helpers import find_fuzzy_series_folder, parse_series_archive_filename, refresh_jellyfin
from bot.state import USER_STATES, USER_TASKS


class SeasonOrganizer:
    """Organizes video files into season folders."""

    @staticmethod
    def detect_season(filename: str) -> Optional[int]:
        for pattern in SEASON_PATTERNS:
            match = pattern.search(filename)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    async def organize(series_dir: Path, fallback_season: Optional[int] = None, quality: Optional[str] = None) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, SeasonOrganizer._organize_sync, series_dir, fallback_season, quality
        )

    @staticmethod
    def _organize_sync(series_dir: Path, fallback_season: Optional[int] = None, quality: Optional[str] = None) -> dict:
        stats = {"moved": 0, "unknown": 0}

        video_files = []
        for ext in VIDEO_EXTENSIONS:
            video_files.extend(series_dir.rglob(f"*{ext}"))

        for video_path in video_files:
            season = SeasonOrganizer.detect_season(video_path.name)
            if season is None and fallback_season is not None:
                season = fallback_season

            if season is not None:
                season_dir = series_dir / f"Season {season}"
                season_dir.mkdir(exist_ok=True)
                
                # Check if we should append quality to filename
                if quality:
                    # e.g., video_path.name -> S01E01 - 1080p.mkv
                    ext = video_path.suffix
                    base = video_path.stem
                    # if already has quality, we skip? Usually it doesn't if we prompted.
                    # but if it does, it's fine. We will just append it. To avoid double, we can check.
                    if f" - {quality}" not in base:
                        new_name = f"{base} - {quality}{ext}"
                    else:
                        new_name = video_path.name
                else:
                    new_name = video_path.name

                dest = season_dir / new_name
                if dest != video_path:
                    shutil.move(str(video_path), str(dest))
                    stats["moved"] += 1
            else:
                dest = series_dir / video_path.name
                if dest != video_path:
                    shutil.move(str(video_path), str(dest))
                    stats["unknown"] += 1

        # Cleanup empty directories
        for dirpath, _dirnames, _filenames in os.walk(series_dir, topdown=False):
            dir_path = Path(dirpath)
            if dir_path != series_dir and not any(dir_path.iterdir()):
                dir_path.rmdir()

        return stats


# ── archive helpers ──────────────────────────────────────────────────────────

async def process_series_archive(
    archive_path: Path,
    series_name: str,
    status_msg,
    user_id: int,
    fallback_season: int = None,
    password: str = None,
    quality: str = None,
    state: dict = None,
):
    """Extract a series archive, organise into season folders."""
    try:
        extract_dir = await extract_series_archive_only(archive_path, series_name, status_msg, user_id, password)
        if state and (state.get("opt_audio") or state.get("opt_mkvmerge")):
            await process_extracted_videos(extract_dir, status_msg, state, user_id=user_id)
        await organize_and_upload_extracted(extract_dir, series_name, status_msg, user_id=user_id, fallback_season=fallback_season, quality=quality)
    except asyncio.CancelledError:
        try:
            if 'extract_dir' in locals():
                shutil.rmtree(extract_dir, ignore_errors=True)
        except Exception:
            pass
        await status_msg.edit_text("🚫 Series extraction cancelled. Junk files cleaned up.")
        raise
    except Exception as e:
        logger.exception("Series extraction error")
        await status_msg.edit_text(f"❌ Error: {str(e)}")


async def extract_series_archive_only(archive_path: Path, series_name: str, status_msg, user_id, password=None) -> Path:
    import uuid
    series_name = re.sub(r'[<>:"/\\|?*]', "_", series_name)
    series_dir = BASE_SERIES / series_name
    series_dir.mkdir(parents=True, exist_ok=True)
    extract_dir = series_dir / f"extracted_{uuid.uuid4().hex[:8]}"
    extract_dir.mkdir(exist_ok=True)

    from bot.state import update_status_msg
    await update_status_msg(status_msg, "📦 Extracting archive...")
    extract_cmd = ["7z", "x", "-bsp1", str(archive_path), f"-o{extract_dir}", "-y"]
    if password:
        extract_cmd.append(f"-p{password}")

    from bot.commands.dd_callbacks import run_process_with_progress
    retcode = await run_process_with_progress(
        extract_cmd, 
        status_msg, 
        "7z", 
        archive_path.name,
        title="Extracting Archive",
        user_id=user_id
    )

    if retcode != 0:
        raise Exception(f"Extraction failed with code {retcode}")

    # Delete the original archive immediately to save space!
    archive_path.unlink(missing_ok=True)

    # Recursive nested extraction
    while True:
        nested_archives = []
        for ext in [".zip", ".rar", ".7z", ".tar", ".gz"]:
            nested_archives.extend(list(extract_dir.rglob(f"*{ext}")))
            
        if not nested_archives:
            break
            
        for nested_archive in nested_archives:
            await update_status_msg(status_msg, f"📦 Extracting nested archive: `{nested_archive.name}`...")
            
            extract_cmd = ["7z", "x", "-bsp1", str(nested_archive), f"-o{nested_archive.parent}", "-y"]
            if password:
                extract_cmd.append(f"-p{password}")
                
            retcode = await run_process_with_progress(
                extract_cmd, 
                status_msg, 
                "7z", 
                nested_archive.name,
                title="Extracting Nested",
                user_id=user_id
            )
            
            # Delete nested archive to save space
            nested_archive.unlink(missing_ok=True)
            
            if retcode != 0:
                raise Exception(f"Nested extraction failed with code {retcode}")

    return extract_dir

async def process_extracted_videos(extract_dir: Path, status_msg, state, user_id=None):
    from bot.commands.dd_callbacks import run_process_with_progress
    import os
    import json
    
    video_files = []
    for ext in VIDEO_EXTENSIONS:
        video_files.extend(extract_dir.rglob(f"*{ext}"))
        
    for i, filepath in enumerate(video_files, 1):
        duration_secs = 0
        try:
            cmd_probe = ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(filepath)]
            process_probe = await asyncio.create_subprocess_exec(*cmd_probe, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await process_probe.communicate()
            probe_data = json.loads(stdout.decode('utf-8', errors='replace'))
            duration_secs = float(probe_data.get("format", {}).get("duration", 0))
        except Exception:
            pass
            
        if state.get("opt_audio") and "selected_tracks" in state:
            selected = state.get("selected_tracks", [])
            to_convert = []
            for idx in selected:
                track_info = next((t for t in state.get("audio_tracks", []) if t["audio_index"] == idx), None)
                if track_info and track_info.get("codec_name") == "aac" and track_info.get("channels") == 2:
                    continue
                to_convert.append(idx)
                
            if to_convert:
                from bot.state import update_status_msg
                await update_status_msg(status_msg, f"🔄 Converting audio for Episode {i}/{len(video_files)}\n`{filepath.name}`...")
                temp_path = filepath.with_name(filepath.stem + "_temp" + filepath.suffix)
                cmd = ["ffmpeg", "-y", "-i", str(filepath), "-map", "0", "-c", "copy"]
                keep = state.get("keep_original_audio", True)
                if keep:
                    total_audio = len(state.get("audio_tracks", []))
                    new_idx = total_audio
                    for idx in to_convert:
                        cmd.extend(["-map", f"0:a:{idx}"])
                        cmd.extend([f"-c:a:{new_idx}", "aac", "-ac", "2"])
                        new_idx += 1
                else:
                    for idx in to_convert:
                        cmd.extend([f"-c:a:{idx}", "aac", "-ac", "2"])
                cmd.extend(["-reserve_index_space", "50M", str(temp_path)])
                retcode = await run_process_with_progress(cmd, status_msg, "ffmpeg", filepath.name, duration_secs=duration_secs, title=f"Converting Audio ({i}/{len(video_files)})", user_id=user_id)
                if retcode == 0:
                    os.replace(str(temp_path), str(filepath))
                
        if state.get("opt_mkvmerge"):
            from bot.state import update_status_msg
            await update_status_msg(status_msg, f"🔄 Web Optimizing Episode {i}/{len(video_files)}\n`{filepath.name}`...")
            temp_path = filepath.with_name(filepath.stem + "_optimized.mkv")
            cmd = ["ffmpeg", "-y", "-i", str(filepath), "-map", "0", "-c", "copy", "-reserve_index_space", "50M", str(temp_path)]
            retcode = await run_process_with_progress(cmd, status_msg, "ffmpeg", filepath.name, duration_secs=duration_secs, title=f"Web Optimizing ({i}/{len(video_files)})", user_id=user_id)
            if retcode == 0:
                os.replace(str(temp_path), str(filepath))
                if filepath.suffix != ".mkv":
                    new_filepath = filepath.with_suffix(".mkv")
                    os.rename(str(filepath), str(new_filepath))
                    filepath = new_filepath

async def organize_and_upload_extracted(extract_dir: Path, series_name: str, status_msg, user_id=None, fallback_season: int = None, quality: str = None, silent: bool = False):
    series_name = re.sub(r'[<>:"/\\|?*]', "_", series_name)
    series_dir = BASE_SERIES / series_name

    from bot.state import update_status_msg
    await update_status_msg(status_msg, "🗂 Organizing files in sandbox...")

    # Organize directly inside the sandboxed extraction directory to avoid collisions
    stats = await SeasonOrganizer.organize(extract_dir, fallback_season=fallback_season, quality=quality)
    
    await update_status_msg(status_msg, "📤 Uploading series to cloud...")
        
    from bot.uploader import perform_autorclone
    # Upload the organized sandbox contents directly to the cloud Series/Name folder
    remote_path, final_bot_msg = await perform_autorclone(extract_dir, f"Series/{series_name}", status_msg, user_id=user_id, silent=silent)
    
    if not remote_path:
        raise Exception("Rclone upload failed")
    
    # Aggressively clean up local empty breadcrumb folders
    try:
        if series_dir.exists() and not any(series_dir.iterdir()):
            series_dir.rmdir()
    except Exception:
        pass
        
    await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir=f"Series/{series_name}")

async def continue_series_processing(
    archive_path: Path,
    explicit_series_name: str,
    status_msg,
    user_id: int,
    password: str = None,
    multipart_urls: list[str] = None,
):
    """Decide whether we can auto-detect the series name/quality or must ask the user."""
    from bot.helpers import extract_quality
    quality = extract_quality(archive_path.name)
    
    series_name = None
    season = None

    if explicit_series_name:
        series_name = find_fuzzy_series_folder(explicit_series_name)
    else:
        parsed = parse_series_archive_filename(archive_path.name)
        if parsed:
            series_name, season, quality_from_parse = parsed
            series_name = find_fuzzy_series_folder(series_name)
            if not quality:
                quality = quality_from_parse

    if not series_name:
        USER_STATES[user_id] = {
            "step": "wait_series_name",
            "filepath": str(archive_path),
            "password": password,
            "multipart_urls": multipart_urls,
        }
        from bot.state import preserve_task_for_user_input
        preserve_task_for_user_input(USER_STATES[user_id], "⏸️ **Waiting for User Input**\nPlease enter series name in Telegram.")
        try:
            await status_msg.delete()
        except Exception:
            pass
        await client.send_message(
            chat_id=status_msg.chat.id,
            text=f"✅ Downloaded to holding area: `{archive_path.name}`\n\n"
                 f"⚠️ **Could not automatically detect series name and season.**\n\n"
                 f"Please enter the **Series Name**:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not quality:
        USER_STATES[user_id] = {
            "step": "wait_sr_quality",
            "filepath": str(archive_path),
            "series_name": series_name,
            "season": season,
            "password": password,
            "multipart_urls": multipart_urls,
        }
        from bot.state import preserve_task_for_user_input
        preserve_task_for_user_input(USER_STATES[user_id], "⏸️ **Waiting for User Input**\nPlease select series quality in Telegram.")
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = [
            [InlineKeyboardButton("480p", callback_data="srq_480p"),
             InlineKeyboardButton("720p", callback_data="srq_720p"),
             InlineKeyboardButton("1080p", callback_data="srq_1080p")],
            [InlineKeyboardButton("4k", callback_data="srq_4k"),
             InlineKeyboardButton("Skip / None", callback_data="srq_skip")]
        ]
        try:
            await status_msg.delete()
        except Exception:
            pass
        await client.send_message(
            chat_id=status_msg.chat.id,
            text=f"✅ Downloaded: `{archive_path.name}`\n\n"
                 f"ℹ️ **Quality tag missing.** Apply quality to all episodes in this archive:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await prompt_series_download_options(archive_path, series_name, season, quality, password, status_msg, user_id, multipart_urls=multipart_urls)

async def prompt_series_download_options(
    archive_path: Path,
    series_name: str,
    season: int,
    quality: str,
    password: str,
    status_msg,
    user_id: int,
    multipart_urls: list[str] = None,
):
    from bot.state import task_manager, GlobalTask, GLOBAL_TASKS
    from bot.config import ARCHIVE_EXTRACTION_LIMIT_GB
    
    size_gb = archive_path.stat().st_size / (1024**3)
    is_sequential = size_gb > ARCHIVE_EXTRACTION_LIMIT_GB

    # Reuse existing task if we are already inside a tracked process
    current_asyncio_task = asyncio.current_task()
    existing_gtask = None
    for k, v in list(GLOBAL_TASKS.items()):
        if getattr(v, "asyncio_task", None) == current_asyncio_task:
            existing_gtask = v
            break
            
    acquired_new = False
    if existing_gtask:
        qtask = existing_gtask
    else:
        qtask = GlobalTask()
        qtask.asyncio_task = current_asyncio_task
        qtask.chat_id = status_msg.chat.id
        qtask.user_id = user_id
        
        user_display = "Unknown"
        if status_msg.chat and status_msg.chat.type == "private":
            user_display = status_msg.chat.username or status_msg.chat.first_name or "Unknown"
            if status_msg.chat.username:
                user_display = f"@{user_display}"
                
        qtask.user_display = user_display
        await task_manager.acquire(qtask, status_msg._client)
        acquired_new = True
    
    try:
        try:
            if not is_sequential:
                extract_dir = await extract_series_archive_only(archive_path, series_name, status_msg, user_id, password)
                
                if multipart_urls:
                    from bot.downloader import AsyncDownloader, ProgressTracker
                    from bot.config import BASE_SERIES
                    from bot.state import update_status_msg
                    unorganized_dir = BASE_SERIES / ".unorganized"
                    for i, m_url in enumerate(multipart_urls, 1):
                        await update_status_msg(status_msg, f"⬇️ **Downloading Independent Archive ({i}/{len(multipart_urls)})**...")
                        tracker = ProgressTracker(status_msg, 0, user_id=user_id, user_display=qtask.user_display, title_prefix=f"(Archive {i+1}/{len(multipart_urls)+1})")
                        part_path = await AsyncDownloader.download(m_url, unorganized_dir, tracker, user_id=user_id)
                        await extract_series_archive_only(part_path, series_name, status_msg, user_id, password)
            else:
                # Sequential logic: Peel the onion and extract just the first video
                from bot.state import update_status_msg
                await update_status_msg(status_msg, "🔍 **Analyzing Large Archive (Sequential Mode)**...")
                core_archive, first_video_path = await get_first_video_and_peel(archive_path, status_msg, password, user_id)
                extract_dir = first_video_path.parent
                
        except asyncio.CancelledError:
            await status_msg.edit_text("🚫 Series extraction cancelled.")
            raise
        except Exception as e:
            logger.exception("Series extraction error")
            await status_msg.edit_text(f"❌ Error: {str(e)}")
            return
    finally:
        if acquired_new:
            await task_manager.release(status_msg._client)

    USER_STATES[user_id] = {
        "step": "wait_audio_tracks",
        "type": "series_extracted" if not is_sequential else "series_sequential",
        "url": "",
        "filepath": str(extract_dir),
        "core_archive": str(core_archive) if is_sequential else None,
        "series_name": series_name,
        "season": season,
        "password": password,
        "quality": quality,
        "multipart_urls": multipart_urls if is_sequential else None, # For sequential, keep URLs to download later
        "opt_audio": True,
        "opt_mkvmerge": False,
        "task_id": qtask.id
    }
    
    # Put it back in GLOBAL_TASKS to display on the dashboard (without acquiring a queue slot)
    from bot.state import GLOBAL_TASKS
    qtask.message = "⏸️ **Waiting for User Input**\nPlease select audio tracks in Telegram."
    qtask.asyncio_task = None
    GLOBAL_TASKS[qtask.id] = qtask
    
    from bot.commands.dd_callbacks import prompt_audio_tracks_for_extracted
    ret = await prompt_audio_tracks_for_extracted(extract_dir, USER_STATES[user_id], status_msg)
    
    if is_sequential:
        # Clean up the probe_temp dir since we only needed it to get audio tracks
        try:
            import shutil
            shutil.rmtree(extract_dir, ignore_errors=True)
        except Exception:
            pass
            
    if not ret:
        try:
            await status_msg.delete()
        except Exception:
            pass
import asyncio
import os
import re
import shutil
from pathlib import Path
from bot.config import BASE_SERIES, VIDEO_EXTENSIONS, logger
from bot.state import update_status_msg

async def get_archive_toc(archive_path: Path, password: str = None) -> list[str]:
    cmd = ["7z", "l", "-slt", "-sccUTF-8"]
    if password:
        cmd.append(f"-p{password}")
    cmd.append(str(archive_path))
    
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await process.communicate()
    output = stdout.decode('utf-8', errors='replace')
    
    files = []
    current_file = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            if current_file.get("Path") and current_file.get("Folder", "").lower() != "+":
                files.append(current_file["Path"])
            current_file = {}
        elif "=" in line:
            key, val = line.split("=", 1)
            current_file[key.strip()] = val.strip()
            
    if current_file.get("Path") and current_file.get("Folder", "").lower() != "+":
        files.append(current_file["Path"])
        
    # filter out the archive itself if 7z l lists it at the top
    filtered = [f for f in files if f != archive_path.name]
    return filtered

async def extract_single_file(archive_path: Path, file_inside: str, output_dir: Path, status_msg, title="Extracting", password=None, user_id=None) -> Path:
    from bot.commands.dd_callbacks import run_process_with_progress
    extract_cmd = ["7z", "x", "-bsp1", str(archive_path), f"{file_inside}", f"-o{output_dir}", "-y"]
    if password:
        extract_cmd.append(f"-p{password}")

    # The actual extracted path might be nested inside output_dir based on file_inside's path
    # So we need to compute the expected path
    expected_path = output_dir / file_inside
    
    retcode = await run_process_with_progress(
        extract_cmd, 
        status_msg, 
        "7z", 
        Path(file_inside).name,
        title=title,
        user_id=user_id
    )

    if retcode != 0:
        raise Exception(f"Failed to extract {file_inside} with code {retcode}")

    if not expected_path.exists():
        raise Exception(f"Extracted file not found at {expected_path}")
        
    return expected_path

async def peel_onion_archive(archive_path: Path, status_msg, password=None, user_id=None) -> Path:
    """Recursively checks if archive contains just another archive. If so, extracts it and deletes parent."""
    current_archive = archive_path
    
    while True:
        toc = await get_archive_toc(current_archive, password)
        # Check if the TOC contains only other archives (e.g. .rar, .zip) and maybe some tiny NFO/TXT files
        # We define a "nested archive" if there are NO video files, but there is at least one archive file.
        video_files = [f for f in toc if any(f.lower().endswith(ext) for ext in VIDEO_EXTENSIONS)]
        archive_files = [f for f in toc if any(f.lower().endswith(ext) for ext in [".zip", ".rar", ".7z", ".tar", ".gz"])]
        
        if not video_files and archive_files:
            # We must peel! Extract the first archive file
            target = archive_files[0]
            await update_status_msg(status_msg, f"🧅 **Peeling nested archive:**\n`{Path(target).name}`")
            extracted_path = await extract_single_file(current_archive, target, current_archive.parent, status_msg, title="Peeling Archive", password=password, user_id=user_id)
            
            # Delete parent to save space!
            current_archive.unlink(missing_ok=True)
            current_archive = extracted_path
            # The next loop will check the TOC of this newly extracted archive
        else:
            # We reached the core!
            break
            
    return current_archive

async def get_first_video_and_peel(archive_path: Path, status_msg, password=None, user_id=None) -> tuple[Path, Path]:
    """Peels the onion, then extracts the FIRST video file for probing."""
    core_archive = await peel_onion_archive(archive_path, status_msg, password, user_id)
    
    toc = await get_archive_toc(core_archive, password)
    video_files = [f for f in toc if any(f.lower().endswith(ext) for ext in VIDEO_EXTENSIONS)]
    
    if not video_files:
        raise Exception("No video files found inside the archive!")
        
    first_video = video_files[0]
    # Extract just this one video to a temp .unorganized/probe_temp dir
    probe_dir = core_archive.parent / "probe_temp"
    probe_dir.mkdir(exist_ok=True)
    
    extracted_video_path = await extract_single_file(core_archive, first_video, probe_dir, status_msg, title="Extracting First Episode", password=password, user_id=user_id)
    
    return core_archive, extracted_video_path

async def process_archive_sequentially_loop(
    core_archive: Path,
    series_name: str,
    status_msg,
    state: dict,
    user_id: int
):
    from bot.organizer import process_extracted_videos, organize_and_upload_extracted
    
    password = state.get("password")
    fallback_season = state.get("season")
    quality = state.get("quality")
    multipart_urls = state.get("multipart_urls", [])
    
    # We will process the core_archive, and then sequentially download and process any multipart_urls.
    archives_to_process = [core_archive]
    # We shouldn't download multipart URLs yet. We will do it in the loop.
    
    try:
        from bot.downloader import AsyncDownloader, ProgressTracker
        unorganized_dir = BASE_SERIES / ".unorganized"
        
        for arch_idx in range(len(archives_to_process) + len(multipart_urls)):
            if arch_idx > 0 and arch_idx - 1 < len(multipart_urls):
                m_url = multipart_urls[arch_idx - 1]
                await update_status_msg(status_msg, f"⬇️ **Downloading Independent Archive ({arch_idx}/{len(multipart_urls)})**...")
                
                # Fetch user_display from state if possible, else "Unknown"
                user_display = "Unknown" 
                
                tracker = ProgressTracker(status_msg, 0, user_id=user_id, user_display=user_display, title_prefix=f"(Archive {arch_idx+1}/{len(multipart_urls)+1})")
                part_path = await AsyncDownloader.download(m_url, unorganized_dir, tracker, user_id=user_id)
                current_archive = await peel_onion_archive(part_path, status_msg, password, user_id)
            else:
                current_archive = archives_to_process[0]
                
            toc = await get_archive_toc(current_archive, password)
            video_files = [f for f in toc if any(f.lower().endswith(ext) for ext in VIDEO_EXTENSIONS)]
            
            for i, video_file in enumerate(video_files, 1):
                # Temporary extraction directory for single files (recreated every time since organize_and_upload_extracted deletes it)
                temp_extract_dir = current_archive.parent / f"seq_temp_{arch_idx}"
                temp_extract_dir.mkdir(exist_ok=True)
                
                # 1. Extract
                await update_status_msg(status_msg, f"📦 **Extracting ({i}/{len(video_files)}):**\n`{Path(video_file).name}`")
                extracted_path = await extract_single_file(current_archive, video_file, temp_extract_dir, status_msg, title=f"Extracting ({i}/{len(video_files)})", password=password, user_id=user_id)
                
                # 2. Process (Audio/MKVMerge)
                # process_extracted_videos operates on a directory, so we pass temp_extract_dir.
                # But wait, we need to ensure process_extracted_videos only processes the file we just extracted!
                # Since temp_extract_dir ONLY contains this one file (we delete it after), it's safe.
                if state.get("opt_audio") or state.get("opt_mkvmerge"):
                    await process_extracted_videos(temp_extract_dir, status_msg, state, user_id=user_id)
                    
                # 3. Organize & Upload
                await organize_and_upload_extracted(temp_extract_dir, series_name, status_msg, user_id=user_id, fallback_season=fallback_season, quality=quality, silent=True)
                
            # Clean up the current archive to save space for the next independent archive
            current_archive.unlink(missing_ok=True)
            
        # Manually trigger a single success notification at the very end
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        import os
        from urllib.parse import quote
        from bot.config import GLOBAL_DASHBOARD_GROUPS
        
        final_remote_path = f"gdrive:Series/{series_name}" # Adjust depending on RCLONE_REMOTE/BASE
        cloud_link_base = os.getenv("CLOUD_LINK_BASE", "")
        index_url = os.getenv("INDEX_URL", "")
        
        kb = []
        remote_path_no_drive = final_remote_path.split(":", 1)[-1].strip("/")
        
        if cloud_link_base:
            cloud_url = f"{cloud_link_base}/{quote(remote_path_no_drive)}/"
            kb.append([InlineKeyboardButton("☁️ Cloud Link", url=cloud_url)])
            
        if index_url:
            index_url = index_url if index_url.endswith("/") else f"{index_url}/"
            kb.append([InlineKeyboardButton("⚡ Index Link", url=f"{index_url}{quote(remote_path_no_drive)}/")])
                
        final_msg = (
            f"✅ **Process Complete (Sequential)!**\n\n"
            f"📁 **Item:** `{series_name}`\n"
            f"📍 **Remote:** `{final_remote_path}`"
        )
        reply_markup = InlineKeyboardMarkup(kb) if kb else None
        
        if user_id:
            try:
                await client.send_message(
                    chat_id=user_id,
                    text=final_msg,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            except Exception:
                pass
                
        user_display = "Unknown"
        if getattr(status_msg, "chat", None) and status_msg.chat.type == "private":
            user_display = status_msg.chat.username or status_msg.chat.first_name or "Unknown"
            if status_msg.chat.username:
                user_display = f"@{user_display}"
                
        group_msg = f"🎉 **Sequential Upload Complete for {user_display}!**\n\n" + final_msg.replace("✅ **Process Complete (Sequential)!**\n\n", "")

        for group_id in GLOBAL_DASHBOARD_GROUPS:
            try:
                await client.send_message(
                    chat_id=group_id,
                    text=group_msg,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup
                )
            except Exception:
                pass
                
        try:
            await status_msg.delete()
        except Exception:
            pass
            
    except asyncio.CancelledError:
        await status_msg.edit_text("🚫 Sequential series extraction cancelled.")
        raise
    except Exception as e:
        logger.exception("Sequential series extraction error")
        await status_msg.edit_text(f"❌ Error: {str(e)}")

