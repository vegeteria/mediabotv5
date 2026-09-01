import pyrogram
from pyrogram.enums import ParseMode
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import BASE_MOVIES, BASE_SERIES, logger
from bot.downloader import AsyncDownloader, ProgressTracker
from bot.helpers import get_file_extension, is_meaningful_movie_name, refresh_jellyfin, parse_episode_filename, find_fuzzy_series_folder, find_fuzzy_season_folder
from bot.state import USER_STATES, USER_TASKS, register_user_task

def get_track_keyboard(state):
    task_id = state.get("task_id", "")
    if task_id:
        from bot.state import CALLBACK_STATES
        CALLBACK_STATES[task_id] = state
        
    tracks = state.get("audio_tracks", [])
    selected = state.get("selected_tracks", [])
    keep = state.get("keep_original_audio", True)
    kb = []
    
    keep_text = "🔀 Mode: Add Stereo (Keep Original)" if keep else "🔀 Mode: Replace with Stereo"
    kb.append([InlineKeyboardButton(keep_text, callback_data=f"dd_track_mode_{task_id}", style=pyrogram.enums.ButtonStyle.PRIMARY if keep else pyrogram.enums.ButtonStyle.DANGER)])
    
    is_opt = state.get('opt_mkvmerge')
    kb.append([InlineKeyboardButton(f"{'✅' if is_opt else '❌'} Video: Web Optimize MKV (Faststart)", callback_data=f"ddopt_mkvmerge_{task_id}", style=pyrogram.enums.ButtonStyle.SUCCESS if is_opt else pyrogram.enums.ButtonStyle.DEFAULT)])
    
    for t in tracks:
        idx = t["audio_index"]
        
        # Determine codec and channels
        codec = t.get("codec_name", "unknown")
        channels = t.get("channels", 2)
        if channels == 6:
            chan_str = "5.1"
        elif channels == 8:
            chan_str = "7.1"
        elif channels == 2:
            chan_str = "2.0"
        elif channels == 1:
            chan_str = "Mono"
        else:
            chan_str = f"{channels}ch"
            
        lang = t.get('tags', {}).get('language', 'und').upper()
        name = f"Track {idx} - {lang} ({codec} {chan_str})"
        
        is_aac_stereo = codec.lower() == "aac" and channels <= 2
        
        if is_aac_stereo:
            mark = "✅ (Already AAC 2.0)"
            callback = f"dd_track_ignore_{task_id}"
            kb.append([InlineKeyboardButton(f"{mark} {name}", callback_data=callback, style=pyrogram.enums.ButtonStyle.SUCCESS)])
        else:
            is_sel = idx in selected
            mark = "✅" if is_sel else "❌"
            kb.append([InlineKeyboardButton(f"{mark} {name}", callback_data=f"dd_track_{idx}_{task_id}", style=pyrogram.enums.ButtonStyle.SUCCESS if is_sel else pyrogram.enums.ButtonStyle.DEFAULT)])
    
    btn_text = "🚀 Confirm & Download" if not state.get("filepath") else "🚀 Start Conversion"
    kb.append([InlineKeyboardButton(btn_text, callback_data=f"dd_track_start_{task_id}", style=pyrogram.enums.ButtonStyle.PRIMARY)])
    return InlineKeyboardMarkup(kb)


async def probe_and_show_options(client, status_msg, state):
    """Probes the URL or local file, populates audio_tracks, and shows get_track_keyboard."""
    from pyrogram.enums import ParseMode
    import json, asyncio
    
    url = state.get("url")
    filepath = state.get("filepath")
    
    # Do not probe for series_archive/series_extracted
    if state.get("type") in ("series_archive", "series_extracted"):
        state["step"] = "wait_audio_tracks"
        await status_msg.edit_text(
            "✅ **Archive/Extracted Process Ready.**\n\nSelect processing options before proceeding:",
            reply_markup=get_track_keyboard(state)
        )
        return
        
    await status_msg.edit_text("🔍 Analyzing media file...")
    
    # We always probe the local file if it exists, otherwise the URL
    target = str(filepath) if filepath else str(url)
    
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", target]
    
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        probe_data = json.loads(stdout.decode('utf-8', errors='replace'))
        
        try:
            state["duration_secs"] = float(probe_data.get("format", {}).get("duration", 0))
        except ValueError:
            state["duration_secs"] = 0
            
        audio_tracks = []
        a_idx = 0
        for s in probe_data.get("streams", []):
            if s.get("codec_type") == "video":
                h = s.get("height", 0)
                if h >= 2160: state["detected_quality"] = "4k"
                elif h >= 1080: state["detected_quality"] = "1080p"
                elif h >= 720: state["detected_quality"] = "720p"
                else: state["detected_quality"] = "480p"
            elif s.get("codec_type") == "audio":
                s["audio_index"] = a_idx
                audio_tracks.append(s)
                a_idx += 1
                
        state["audio_tracks"] = audio_tracks
        # Default selection: only select AAC 2.0 tracks
        state["selected_tracks"] = []
        
    except Exception as e:
        import logging
        logging.getLogger("mediabot").warning(f"ffprobe failed: {e}")
        state["audio_tracks"] = []
        state["selected_tracks"] = []
        
    state["step"] = "wait_audio_tracks"
    from bot.config import get_base_url
    dashboard_link = f"{get_base_url()}/dashboard"
    
    text = f"✅ **Media Analyzed**\n\n"
    if state["audio_tracks"]:
        text += "🎧 **Audio Tracks Detected**\nSelect the tracks to convert to Stereo (AAC 2.0). Unselected tracks will be removed.\n\n"
    else:
        text += "ℹ️ No audio tracks detected or probe failed.\n\n"
        
    text += f"🌐 [Open Dashboard]({dashboard_link}) | Task ID: `{state.get('task_id')}`"
    
    from bot.state import preserve_task_for_user_input
    preserve_task_for_user_input(state, "⏸️ **Waiting for User Input**\nPlease select audio tracks in Telegram.")
    
    try:
        await status_msg.delete()
    except Exception:
        pass
        
    await client.send_message(
        chat_id=status_msg.chat.id,
        text=text,
        reply_markup=get_track_keyboard(state),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )

async def run_process_with_progress(cmd, status_msg, process_type, filename, duration_secs=0, title=None, user_id=None):
    import time, re
    from bot.state import GLOBAL_TASKS, GlobalTask
    import uuid
    current_asyncio_task = asyncio.current_task()
    gtask = None
    task_id = None
    for k, v in list(GLOBAL_TASKS.items()):
        if getattr(v, "asyncio_task", None) == current_asyncio_task:
            gtask = v
            task_id = k
            break
            
    if not gtask:
        task_id = str(uuid.uuid4())
        gtask = GlobalTask()
        gtask.chat_id = status_msg.chat.id
        GLOBAL_TASKS[task_id] = gtask

    # Default titles
    if not title:
        if process_type == "ffmpeg":
            title = "Converting Audio"
        elif process_type == "7z":
            title = "Extracting Archive"
        else:
            title = "Remuxing Video"

    gtask.message = f"🔄 <b>{title}:</b> <code>{filename}</code>\n⏳ Processing started..."

    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    
    last_update = 0
    buffer = ""
    
    try:
        while True:
            chunk = await process.stdout.read(1024)
            if not chunk:
                break
            
            buffer += chunk.decode('utf-8', errors='replace')
            while True:
                r_idx = buffer.find('\r')
                n_idx = buffer.find('\n')
                b_idx = buffer.find('\x08')
                
                indices = [i for i in (r_idx, n_idx, b_idx) if i != -1]
                if not indices:
                    break
                    
                split_idx = min(indices)
                    
                line_str = buffer[:split_idx].strip()
                buffer = buffer[split_idx+1:]
                
                if not line_str:
                    continue
                    
                now = time.time()
                from bot.config import PROGRESS_UPDATE_DELAY
                if now - last_update > PROGRESS_UPDATE_DELAY:
                    msg = None
                    if process_type == "mkvmerge" and "Progress:" in line_str:
                        match = re.search(r"(\d+)%", line_str)
                        if match:
                            percent = int(match.group(1))
                            filled = int(percent / 10)
                            bar = "█" * filled + "░" * (10 - filled)
                            msg = f"🔄 <b>{title}:</b> <code>{filename}</code>\n<code>[{bar}] {percent}%</code>\n<b>Engine:</b> <code>mkvmerge</code>"
                    elif process_type == "7z" and "%" in line_str:
                        match = re.search(r"(\d+)%", line_str)
                        if match:
                            percent = int(match.group(1))
                            filled = int(percent / 10)
                            bar = "█" * filled + "░" * (10 - filled)
                            msg = f"📦 <b>{title}:</b> <code>{filename}</code>\n<code>[{bar}] {percent}%</code>\n<b>Engine:</b> <code>7z</code>"
                    elif process_type == "ffmpeg" and "time=" in line_str:
                        t_match = re.search(r"time=(\d{2}:\d{2}:\d{2})", line_str)
                        s_match = re.search(r"speed=\s*([0-9.]+e?\+?[0-9]*x)", line_str)
                        if t_match:
                            t_str = t_match.group(1)
                            s_str = s_match.group(1) if s_match else "N/A"
                            
                            if duration_secs > 0:
                                h, m, s = t_str.split(':')
                                current_secs = int(h) * 3600 + int(m) * 60 + float(s)
                                percent = min(100, int((current_secs / duration_secs) * 100))
                                filled = int(percent / 10)
                                bar = "█" * filled + "░" * (10 - filled)
                                msg = f"🔄 <b>{title}:</b> <code>{filename}</code>\n<code>[{bar}] {percent}%</code>\n<b>Speed:</b> <code>{s_str}</code> | <b>Engine:</b> <code>ffmpeg</code>"
                            else:
                                msg = f"🔄 <b>{title}:</b> <code>{filename}</code>\n<b>Time Processed:</b> <code>{t_str}</code> | <b>Speed:</b> <code>{s_str}</code>\n<b>Engine:</b> <code>ffmpeg</code>"
                                
                    if msg:
                        if getattr(gtask, "last_msg_text", None) != msg:
                            gtask.last_msg_text = msg
                            gtask.message = msg
                        last_update = now

        await process.wait()
        return process.returncode
    except asyncio.CancelledError:
        try:
            process.kill()
            await process.wait()
        except OSError:
            pass
        raise
    finally:
        pass

@Client.on_callback_query(filters.regex(r"^(ddopt_|dd_track_)"))
async def handle_dd_callback(client: Client, query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    
    parts = query.data.split('_')
    if len(parts) > 1 and len(parts[-1]) <= 8:  # Assuming task_id is 4-8 chars
        task_id = parts[-1]
        data = "_".join(parts[:-1])
    else:
        task_id = ""
        data = query.data

    from bot.state import CALLBACK_STATES
    if not task_id or task_id not in CALLBACK_STATES:
        await query.edit_message_text("❌ Session expired.")
        return

    if user_id in USER_TASKS:
        register_user_task(user_id, asyncio.current_task())

    state = CALLBACK_STATES[task_id]

    if data in ("ddopt_audio", "ddopt_mkvmerge"):
        opt = data.replace("ddopt_", "opt_")
        state[opt] = not state.get(opt, False)
        await query.edit_message_reply_markup(reply_markup=get_track_keyboard(state))
        return

    if data.startswith("dd_track_"):
        if data == "dd_track_ignore":
            await query.answer("This track is already AAC 2.0 and does not need conversion.", show_alert=True)
            return

        if data == "dd_track_mode":
            state["keep_original_audio"] = not state.get("keep_original_audio", True)
            await query.edit_message_reply_markup(reply_markup=get_track_keyboard(state))
            return
            
        if data == "dd_track_start":
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
                
            if state.get("type") == "series_sequential":
                if "core_archive" in state and not Path(state["core_archive"]).exists():
                    await query.message.reply_text("❌ Archive already processed or deleted.", parse_mode=ParseMode.MARKDOWN)
                    return
            else:
                if "filepath" in state and not Path(state["filepath"]).exists():
                    await query.message.reply_text("❌ File already processed or deleted.", parse_mode=ParseMode.MARKDOWN)
                    return
            
            if state.get("type") in ("series_archive", "series_extracted", "series_sequential"):
                from bot.state import update_status_msg
                from bot.state import task_manager, GlobalTask
                qtask = GlobalTask()
                qtask.id = state.get("task_id") or __import__("uuid").uuid4().hex[:8]
                qtask.asyncio_task = asyncio.current_task()
                qtask.chat_id = query.message.chat.id
                qtask.user_id = user_id
                user_display = query.from_user.username or query.from_user.first_name
                qtask.user_display = f"@{user_display}" if query.from_user.username else str(user_display)
                s_info = (
                    f"\n🔗 <b>URL:</b> <code>{state.get('url', 'Unknown')[:30]}...</code>"
                    f"\n⚙️ <b>Type:</b> <code>{state.get('type', 'direct').capitalize()}</code>"
                )
                qtask.static_info = s_info
                await task_manager.acquire(qtask, client)
                try:
                    if state.get("type") == "series_sequential":
                        await update_status_msg(query.message, "🔄 Processing archive sequentially...")
                        from bot.organizer import process_archive_sequentially_loop
                        core_archive = Path(state["core_archive"])
                        await process_archive_sequentially_loop(core_archive, state["series_name"], query.message, state, user_id=user_id)
                    else:
                        await update_status_msg(query.message, "🔄 Processing all episodes...")
                        from bot.organizer import process_extracted_videos, organize_and_upload_extracted
                        extract_dir = Path(state["filepath"])
                        await process_extracted_videos(extract_dir, query.message, state, user_id=user_id)
                        await organize_and_upload_extracted(extract_dir, state["series_name"], query.message, user_id=user_id, fallback_season=state["season"], quality=state.get("quality"))
                finally:
                    await task_manager.release(client)
                return

            from bot.state import task_manager, GlobalTask
            qtask = GlobalTask()
            qtask.id = state.get("task_id") or __import__("uuid").uuid4().hex[:8]
            qtask.asyncio_task = asyncio.current_task()
            qtask.chat_id = query.message.chat.id
            qtask.user_id = user_id
            user_display = query.from_user.username or query.from_user.first_name
            qtask.user_display = f"@{user_display}" if query.from_user.username else str(user_display)
            s_info = (
                f"\n🔗 <b>URL:</b> <code>{state.get('url', 'Unknown')[:30]}...</code>"
                f"\n⚙️ <b>Type:</b> <code>{state.get('type', 'direct').capitalize()}</code>"
            )
            qtask.static_info = s_info
            await task_manager.acquire(qtask, client)
            
            try:
                # If filepath is not set, we need to download it first
                if "filepath" not in state:
                    from bot.config import get_base_url
                    dashboard_link = f"{get_base_url()}/dashboard"
                    await query.edit_message_text(f"📥 Starting download...\n\n🌐 [Open Dashboard]({dashboard_link}) | Task ID: `{state.get('task_id')}`", parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
                    url = state["url"]
                    dl_type = state["type"]
                    unorganized_dir = BASE_MOVIES / ".unorganized" if dl_type == "movie" else BASE_SERIES / ".unorganized"
                    tracker = ProgressTracker(query.message, 0)
                    try:
                        filepath = await AsyncDownloader.download(url, unorganized_dir, tracker, user_id=user_id)
                        state["filepath"] = str(filepath)
                    except asyncio.CancelledError:
                        await query.edit_message_text("🚫 Download cancelled.")
                        raise
                    except Exception as e:
                        logger.exception("Download error")
                        await query.edit_message_text(f"❌ Error: {str(e)}")
                        return

                audio_tracks = state.get("audio_tracks", [])
                if not audio_tracks:
                    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(state["filepath"])]
                    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    stdout, _ = await process.communicate()
                    try:
                        probe_data = json.loads(stdout.decode('utf-8', errors='replace'))
                        state["duration_secs"] = float(probe_data.get("format", {}).get("duration", 0))
                        
                        a_idx = 0
                        for s in probe_data.get("streams", []):

                            if s.get("codec_type") == "video":

                                h = s.get("height", 0)

                                if h >= 2160: state["detected_quality"] = "4k"

                                elif h >= 1080: state["detected_quality"] = "1080p"

                                elif h >= 720: state["detected_quality"] = "720p"

                                else: state["detected_quality"] = "480p"

                            elif s.get("codec_type") == "audio":

                                s["audio_index"] = a_idx

                                audio_tracks.append(s)

                                a_idx += 1
                        
                        if audio_tracks:
                            state["audio_tracks"] = audio_tracks
                            state["selected_tracks"] = []
                            
                            from bot.state import preserve_task_for_user_input
                            preserve_task_for_user_input(state, "⏸️ **Waiting for User Input**\nPlease select audio tracks in Telegram.")
                            
                            from bot.config import get_base_url
                            dashboard_link = f"{get_base_url()}/dashboard"
                            await client.send_message(
                                chat_id=query.message.chat.id,
                                text=f"🎧 Download complete! Select audio tracks to convert to Stereo:\n\n🌐 [Open Dashboard]({dashboard_link}) | Task ID: `{state.get('task_id')}`",
                                reply_markup=get_track_keyboard(state),
                                parse_mode=ParseMode.MARKDOWN,
                                disable_web_page_preview=True
                            )
                            return
                    except Exception:
                        pass

                selected = state.get("selected_tracks", [])
                audio_tracks = state.get("audio_tracks", [])
                keep = state.get("keep_original_audio", True)
                
                filtered_selected = []
                for idx in selected:
                    t = next((x for x in audio_tracks if x.get("audio_index") == idx), None)
                    already_aac20 = t and t.get("codec_name") == "aac" and t.get("channels") == 2
                    if not already_aac20:
                        filtered_selected.append(idx)
                selected = filtered_selected
                
                keep = state.get("keep_original_audio", True)
                audio_tracks = state.get("audio_tracks", [])
                
                # Identify automatically kept AAC 2.0 tracks
                auto_aac = [t.get("audio_index") for t in audio_tracks if t.get("codec_name", "").lower() == "aac" and t.get("channels", 2) <= 2]
                
                # If nothing was explicitly selected AND we are keeping original audio, we don't need ffmpeg at all
                if not selected and keep:
                    await proceed_post_download(query.message, user_id, state)
                    return
                    
                # If not keeping original audio, but we also have no selected tracks, AND no auto_aac tracks, this is weird but we shouldn't strip all audio.
                if not selected and not keep and not auto_aac:
                    await proceed_post_download(query.message, user_id, state)
                    return
                    
                from bot.state import update_status_msg
                await update_status_msg(query.message, "🔄 Processing audio tracks...")
                filepath = Path(state["filepath"])
                temp_path = filepath.with_name(filepath.stem + "_temp" + filepath.suffix)
                
                cmd = ["ffmpeg", "-y", "-i", str(filepath)]
                
                if keep:
                    # Keep everything natively, then just append the converted ones
                    cmd.extend(["-map", "0", "-c", "copy"])
                    new_idx = len(audio_tracks)
                    for idx in selected:
                        cmd.extend(["-map", f"0:a:{idx}", f"-c:a:{new_idx}", "aac", "-ac", "2"])
                        new_idx += 1
                else:
                    # Strip unselected non-AAC tracks
                    cmd.extend(["-map", "0:v?", "-map", "0:s?", "-c:v", "copy", "-c:s", "copy"])
                    new_idx = 0
                    
                    # 1. Map and copy all auto AAC 2.0 tracks
                    for idx in auto_aac:
                        cmd.extend(["-map", f"0:a:{idx}", f"-c:a:{new_idx}", "copy"])
                        new_idx += 1
                        
                    # 2. Map and convert all user selected tracks
                    for idx in selected:
                        cmd.extend(["-map", f"0:a:{idx}", f"-c:a:{new_idx}", "aac", "-ac", "2"])
                        new_idx += 1
                        
                cmd.extend(["-reserve_index_space", "50M", str(temp_path)])
                
                retcode = await run_process_with_progress(cmd, query.message, "ffmpeg", filepath.name, duration_secs=state.get("duration_secs", 0))
                if retcode == 0:
                    os.replace(str(temp_path), str(filepath))
                
                await proceed_post_download(query.message, user_id, state)
            finally:
                await task_manager.release(client)
        else:
            idx = int(data.replace("dd_track_", ""))
            sel = state.get("selected_tracks", [])
            if idx in sel:
                sel.remove(idx)
            else:
                sel.append(idx)
            state["selected_tracks"] = sel
            await query.edit_message_reply_markup(reply_markup=get_track_keyboard(state))
        return

async def proceed_post_download(status_msg, user_id, state):
    filepath = Path(state["filepath"])
    
    if state.get("opt_mkvmerge"):
        from bot.state import update_status_msg
        await update_status_msg(status_msg, "🔄 Applying Web Optimization (Faststart)...")
        temp_path = filepath.with_name(filepath.stem + "_optimized.mkv")
        
        # Use ffmpeg with -reserve_index_space to push the Cues to the front of the MKV file for instant seeking
        cmd = ["ffmpeg", "-y", "-i", str(filepath), "-map", "0", "-c", "copy", "-reserve_index_space", "50M", str(temp_path)]
        retcode = await run_process_with_progress(cmd, status_msg, "ffmpeg", filepath.name, duration_secs=state.get("duration_secs", 0), title="Web Optimizing MKV")
        
        if retcode == 0:
            new_filepath = filepath.with_name(filepath.stem + ".mkv")
            os.replace(str(temp_path), str(new_filepath))
            if new_filepath != filepath:
                try:
                    os.remove(str(filepath))
                except Exception:
                    pass
            filepath = new_filepath

    if state["type"] == "movie":
        await process_movie_post(status_msg, user_id, filepath, state)
    elif state["type"] == "episode":
        await process_episode_post(status_msg, user_id, filepath, state)

async def process_movie_post(status_msg, user_id, filepath, state=None):
    if not filepath.exists():
        try:
            await status_msg.edit_text("❌ Error: File not found (it may have been already moved).")
        except:
            pass
        return
        
    if is_meaningful_movie_name(filepath.name):
        from bot.helpers import extract_quality, get_file_extension
        quality = extract_quality(filepath.name)

        if not quality and USER_STATES.get(user_id, {}).get("detected_quality"):
            quality = USER_STATES[user_id]["detected_quality"]
        if not quality:
            from bot.helpers import detect_quality_with_ffprobe
            quality = await detect_quality_with_ffprobe(str(filepath))
        if not quality:
            USER_STATES[user_id] = {
                "step": "wait_mv_quality",
                "filepath": str(filepath),
                "movie_name": filepath.stem
            }
            from bot.state import preserve_task_for_user_input
            preserve_task_for_user_input(USER_STATES[user_id], "⏸️ **Waiting for User Input**\nPlease select movie quality in Telegram.")
            from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            kb = [
                [InlineKeyboardButton("480p", callback_data="mvq_480p", style=pyrogram.enums.ButtonStyle.PRIMARY),
                 InlineKeyboardButton("720p", callback_data="mvq_720p", style=pyrogram.enums.ButtonStyle.PRIMARY),
                 InlineKeyboardButton("1080p", callback_data="mvq_1080p", style=pyrogram.enums.ButtonStyle.PRIMARY)],
                [InlineKeyboardButton("4k", callback_data="mvq_4k", style=pyrogram.enums.ButtonStyle.PRIMARY),
                 InlineKeyboardButton("Skip / None", callback_data="mvq_skip", style=pyrogram.enums.ButtonStyle.DANGER)]
            ]
            try:
                await status_msg.delete()
            except Exception:
                pass
            await status_msg.reply_text(
                f"✅ Downloaded: `{filepath.name}`\n\n"
                     f"ℹ️ **Quality tag missing.** Please select the movie quality:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        ext = get_file_extension(filepath)
        from bot.helpers import get_clean_movie_name, get_existing_movie_folder
        existing_folder = get_existing_movie_folder(filepath.stem)
        folder_name = existing_folder if existing_folder else get_clean_movie_name(filepath.stem)

        if quality and f" - {quality}" not in folder_name:
            new_filename = f"{folder_name} - {quality}{ext}"
        else:
            new_filename = f"{folder_name}{ext}"

        dest_folder = BASE_MOVIES / folder_name
        dest_folder.mkdir(parents=True, exist_ok=True)
        dest_path = dest_folder / new_filename
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, shutil.move, str(filepath), str(dest_path))

        from bot.state import update_status_msg
        await update_status_msg(status_msg, "📤 Uploading movie to cloud...")
            
        from bot.uploader import perform_autorclone
        _, final_bot_msg = await perform_autorclone(dest_folder, f"Movies/{folder_name}", status_msg, user_id=user_id)
        
        # Aggressively clean up local empty breadcrumb folders
        try:
            if dest_folder.exists() and not any(dest_folder.iterdir()):
                dest_folder.rmdir()
        except Exception:
            pass
            
        await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir=f"Movies/{folder_name}")
    else:
        USER_STATES[user_id] = {"step": "wait_movie_name", "filepath": str(filepath), "task_id": state.get("task_id") if state else None}
        from bot.state import preserve_task_for_user_input
        preserve_task_for_user_input(USER_STATES[user_id], "⏸️ **Waiting for User Input**\nPlease enter movie name in Telegram.")
        try:
            await status_msg.delete()
        except Exception:
            pass
        await client.send_message(
            chat_id=status_msg.chat.id,
            text=f"✅ Downloaded to holding area: `{filepath.name}`\n\n"
                 f"⚠️ **The file name doesn't look like a proper movie name.**\n\n"
                 f"Please enter the **Movie Name** (it will be renamed):",
            parse_mode=ParseMode.MARKDOWN,
        )

async def process_episode_post(status_msg, user_id, filepath, state=None):
    if not filepath.exists():
        try:
            await status_msg.edit_text("❌ Error: File not found (it may have been already moved).")
        except:
            pass
        return
        
    parsed = parse_episode_filename(filepath.name)
    if parsed:
        series_name, season, episode, quality = parsed

        if not quality and USER_STATES.get(user_id, {}).get("detected_quality"):
            quality = USER_STATES[user_id]["detected_quality"]
        if not quality:
            from bot.helpers import detect_quality_with_ffprobe
            quality = await detect_quality_with_ffprobe(str(filepath))
        if not quality:
            USER_STATES[user_id] = {
                "step": "wait_ep_quality",
                "filepath": str(filepath),
                "series_name": series_name,
                "season": season,
                "episode": episode,
            }
            from bot.state import preserve_task_for_user_input
            preserve_task_for_user_input(USER_STATES[user_id], "⏸️ **Waiting for User Input**\nPlease select episode quality in Telegram.")
            from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            kb = [
                [InlineKeyboardButton("480p", callback_data="epq_480p", style=pyrogram.enums.ButtonStyle.PRIMARY),
                 InlineKeyboardButton("720p", callback_data="epq_720p", style=pyrogram.enums.ButtonStyle.PRIMARY),
                 InlineKeyboardButton("1080p", callback_data="epq_1080p", style=pyrogram.enums.ButtonStyle.PRIMARY)],
                [InlineKeyboardButton("4k", callback_data="epq_4k", style=pyrogram.enums.ButtonStyle.PRIMARY),
                 InlineKeyboardButton("Skip / None", callback_data="epq_skip", style=pyrogram.enums.ButtonStyle.DANGER)]
            ]
            try:
                await status_msg.delete()
            except Exception:
                pass
            await status_msg.reply_text(
                f"✅ Downloaded: `{filepath.name}`\n\n"
                     f"ℹ️ **Quality tag missing.** Please select the video quality:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        series_name = find_fuzzy_series_folder(series_name)
        dest_series_dir = BASE_SERIES / series_name
        season_folder = find_fuzzy_season_folder(dest_series_dir, season)
        dest_folder = dest_series_dir / season_folder
        dest_folder.mkdir(parents=True, exist_ok=True)

        ext = get_file_extension(filepath)
        new_filename = f"S{season:02d}E{episode:02d} - {quality}{ext}"
        dest_path = dest_folder / new_filename

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, shutil.move, str(filepath), str(dest_path))

        from bot.state import update_status_msg
        await update_status_msg(status_msg, "📤 Uploading episode to cloud...")
            
        from bot.uploader import perform_autorclone
        _, final_bot_msg = await perform_autorclone(dest_path, f"Series/{series_name}/{season_folder}", status_msg, user_id=user_id)
        
        # Aggressively clean up local empty breadcrumb folders
        try:
            if dest_folder.exists() and not any(dest_folder.iterdir()):
                dest_folder.rmdir()
            if dest_series_dir.exists() and not any(dest_series_dir.iterdir()):
                dest_series_dir.rmdir()
        except Exception:
            pass
            
        await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir=f"Series/{target_series}/{target_season}")
    else:
        USER_STATES[user_id] = {"step": "wait_ep_manual_series", "filepath": str(filepath)}
        from bot.state import preserve_task_for_user_input
        preserve_task_for_user_input(USER_STATES[user_id], "⏸️ **Waiting for User Input**\nPlease enter series name in Telegram.")
        try:
            await status_msg.delete()
        except Exception:
            pass
        await client.send_message(
            chat_id=status_msg.chat.id,
            text=f"✅ Downloaded to holding area: `{filepath.name}`\n\n"
                 f"⚠️ **Could not automatically parse the episode details.**\n\n"
                 f"Please enter the **Series Name** (Season and Episode will be asked next):",
            parse_mode=ParseMode.MARKDOWN,
        )

async def handle_direct_link_probe(message, user_id, url, dl_type):
    from bot.state import USER_STATES
    
    import uuid
    state = {
        "step": "wait_audio_tracks",
        "url": url,
        "type": dl_type,
        "opt_audio": True,
        "opt_mkvmerge": False,
        "keep_original_audio": True,
        "task_id": str(uuid.uuid4())[:8]
    }
    
    from bot.config import IS_DUPLICATE_ALLOWED
    if not IS_DUPLICATE_ALLOWED:
        from bot.downloader import AsyncDownloader
        from bot.helpers import extract_quality, find_fuzzy_movie_folder, check_episode_exists_in_cloud, parse_episode_filename, parse_movie_filename
        
        status_msg = await message.reply_text("🔍 Analyzing link...")
        aborted = False
        try:
            filename = await AsyncDownloader.probe_filename(url)
            quality = extract_quality(filename)
            
            if dl_type == "movie":
                clean_title = parse_movie_filename(filename)
                exists, existing_qs = find_fuzzy_movie_folder(clean_title)
                if exists:
                    if quality and quality in existing_qs:
                        await status_msg.edit_text(f"❌ Aborted: Movie **{filename}** already exists in **{quality}** on your server!")
                        aborted = True
                        return
                    elif not quality:
                        # Will be handled in post-download if name/quality still missing
                        pass
                        
            elif dl_type == "episode":
                parsed = parse_episode_filename(filename)
                if parsed:
                    s_name, s_num, e_num, e_qual = parsed
                    e_qual = e_qual or quality
                    if e_qual and check_episode_exists_in_cloud(s_name, s_num, e_num, e_qual):
                        await status_msg.edit_text(f"❌ Aborted: **{s_name} S{s_num}E{e_num}** already exists in **{e_qual}** on your server!")
                        aborted = True
                        return
                        
        except Exception:
            pass
        finally:
            if not aborted:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
    
    if dl_type in ("movie", "episode"):
        status_msg = await message.reply_text("🔍 Analyzing audio tracks from the link...")
        try:
            cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(url) if url else str(state.get("filepath"))]
            process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await process.communicate()
            probe_data = json.loads(stdout.decode('utf-8', errors='replace'))
            
            try:
                state["duration_secs"] = float(probe_data.get("format", {}).get("duration", 0))
            except ValueError:
                state["duration_secs"] = 0
            
            audio_tracks = []
            a_idx = 0
            for s in probe_data.get("streams", []):

                if s.get("codec_type") == "video":

                    h = s.get("height", 0)

                    if h >= 2160: state["detected_quality"] = "4k"

                    elif h >= 1080: state["detected_quality"] = "1080p"

                    elif h >= 720: state["detected_quality"] = "720p"

                    else: state["detected_quality"] = "480p"

                elif s.get("codec_type") == "audio":

                    s["audio_index"] = a_idx

                    audio_tracks.append(s)

                    a_idx += 1
            
            if audio_tracks:
                state["audio_tracks"] = audio_tracks
                state["selected_tracks"] = []
                USER_STATES[user_id] = state
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                
                from bot.state import GLOBAL_TASKS, GlobalTask
                qtask = GlobalTask()
                qtask.id = state["task_id"]
                qtask.user_id = user_id
                user_display = message.from_user.username or message.from_user.first_name
                qtask.user_display = f"@{user_display}" if message.from_user.username else str(user_display)
                qtask.message = "⏸️ **Waiting for User Input**\nPlease select audio tracks in Telegram."
                qtask.static_info = f"\n🔗 <b>URL:</b> <code>{url[:30]}...</code>\n⚙️ <b>Type:</b> <code>{dl_type.capitalize()}</code>"
                GLOBAL_TASKS[qtask.id] = qtask
                
                from bot.config import get_base_url
                dashboard_link = f"{get_base_url()}/dashboard"
                await message._client.send_message(
                    chat_id=message.chat.id,
                    text=f"🎧 Select audio tracks to convert to Stereo:\n\n🌐 [Open Dashboard]({dashboard_link}) | Task ID: `{state['task_id']}`",
                    reply_markup=get_track_keyboard(state),
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )
                return
        except Exception as e:
            # Fallback to downloading directly if probe fails
            pass
            
    # Fallback / Direct Download if probe failed
    USER_STATES[user_id] = state
    
    if "audio_tracks" not in state:
        state["audio_tracks"] = []
        state["selected_tracks"] = []
        state["probe_failed"] = True
        
        # Start download directly silently
        class FakeCallbackQuery:
            def __init__(self, msg, d):
                self.message = msg
                self.data = d
                self.from_user = msg.from_user
                
            async def answer(self, *args, **kwargs):
                pass
                
            async def edit_message_text(self, *args, **kwargs):
                if 'status_msg' in locals():
                    try:
                        return await status_msg.edit_text(*args, **kwargs)
                    except Exception:
                        return await self.message.reply_text(*args, **kwargs)
                else:
                    return await self.message.reply_text(*args, **kwargs)
                    
            async def edit_message_reply_markup(self, *args, **kwargs):
                pass
                
        fake_query = FakeCallbackQuery(message, f"dd_track_start_{state['task_id']}")
        # Call the callback handler directly to kick off the download
        from bot.state import CALLBACK_STATES
        CALLBACK_STATES[state["task_id"]] = state
        asyncio.create_task(handle_dd_callback(message._client, fake_query))
        return

async def prompt_audio_tracks_for_extracted(extract_dir, state, status_msg):
    from bot.config import VIDEO_EXTENSIONS
    first_video = None
    for ext in VIDEO_EXTENSIONS:
        found = list(extract_dir.rglob(f"*{ext}"))
        if found:
            first_video = found[0]
            break
            
    if first_video:
        from bot.state import update_status_msg
        await update_status_msg(status_msg, "🔍 Analyzing audio tracks from first episode...")
        cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(first_video)]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        probe_data = json.loads(stdout.decode('utf-8', errors='replace'))
        
        try:
            state["duration_secs"] = float(probe_data.get("format", {}).get("duration", 0))
        except ValueError:
            state["duration_secs"] = 0
        
        audio_tracks = []
        a_idx = 0
        for s in probe_data.get("streams", []):

            if s.get("codec_type") == "video":

                h = s.get("height", 0)

                if h >= 2160: state["detected_quality"] = "4k"

                elif h >= 1080: state["detected_quality"] = "1080p"

                elif h >= 720: state["detected_quality"] = "720p"

                else: state["detected_quality"] = "480p"

            elif s.get("codec_type") == "audio":

                s["audio_index"] = a_idx

                audio_tracks.append(s)

                a_idx += 1
        
        if audio_tracks:
            state["audio_tracks"] = audio_tracks
            state["selected_tracks"] = []
            state["step"] = "wait_audio_tracks"
            try:
                await status_msg.delete()
            except Exception:
                pass
            from bot.config import get_base_url
            dashboard_link = f"{get_base_url()}/dashboard"
            await status_msg.reply_text(
                f"🎧 Select audio tracks to convert to Stereo (based on `{first_video.name}`):\n\n🌐 [Open Dashboard]({dashboard_link}) | Task ID: `{state.get('task_id')}`",
                reply_markup=get_track_keyboard(state),
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            return True
        else:
            await status_msg.edit_text("❌ No audio tracks found in the first video.")
            return True
    else:
        await status_msg.edit_text("❌ No video files found in archive.")
        return True
