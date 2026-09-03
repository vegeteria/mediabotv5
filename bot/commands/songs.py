from pyrogram.enums import ParseMode
import asyncio
import os
import shutil
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message


import urllib.parse
import aiohttp
import base64
from bot.config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

SONG_EVENTS = {}
SONG_CHOICES = {}
SONG_SEARCH_CACHE = {}

@Client.on_callback_query(filters.regex(r"^songmatch_"))
async def song_match_callback(client: Client, query):
    data = query.data.split("_")
    task_id = data[1]
    choice = data[2]
    
    if task_id in SONG_EVENTS:
        SONG_CHOICES[task_id] = choice
        SONG_EVENTS[task_id].set()
        await query.message.edit_text("⏳ Processing your choice...")
    else:
        await query.answer("Task expired", show_alert=True)


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
plugins: fromfilename chroma
directory: {target_dir}/organized
library: {target_dir}/library.blb
import:
    move: yes
    write: yes
    autotag: yes
    quiet: yes
    quiet_fallback: asis
    singletons: yes
paths:
    default: %if{{$albumartist,$albumartist,%if{{$artist,$artist,Unknown Artist}}}}/%if{{$album,$album,Unknown Album}}/$track - %if{{$title,$title,Unknown Title}}
    singleton: %if{{$albumartist,$albumartist,%if{{$artist,$artist,Unknown Artist}}}}/%if{{$album,$album,Unknown Album}}/$track - %if{{$title,$title,Unknown Title}}
"""
            with open(beets_config, "w") as f:
                f.write(beets_config_content)
                
            # run beets
            cmd = ["beet", "-c", str(beets_config), "import", "-q", "-s", str(process_filepath)]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            stdout, _ = await process.communicate()
            
            stdout_str = stdout.decode('utf-8')
            
            if "Importing as-is" in stdout_str:
                # Try Spotify fallback
                search_query = urllib.parse.quote(filepath.stem)
                results = []
                if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
                    async with aiohttp.ClientSession() as session:
                        auth_string = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
                        auth_base64 = str(base64.b64encode(auth_string.encode("utf-8")), "utf-8")
                        async with session.post("https://accounts.spotify.com/api/token",
                                                headers={"Authorization": f"Basic {auth_base64}", "Content-Type": "application/x-www-form-urlencoded"},
                                                data="grant_type=client_credentials") as resp:
                            if resp.status == 200:
                                token_data = await resp.json()
                                access_token = token_data.get('access_token')
                                
                                async with session.get(f"https://api.spotify.com/v1/search?q={search_query}&type=track&limit=5",
                                                       headers={"Authorization": f"Bearer {access_token}"}) as s_resp:
                                    if s_resp.status == 200:
                                        spotify_data = await s_resp.json()
                                        results = spotify_data.get('tracks', {}).get('items', [])
                
                if not results:
                    # Fallback to iTunes if Spotify fails or not configured
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f"https://itunes.apple.com/search?term={search_query}&entity=song&limit=5") as resp:
                            itunes_data = await resp.json(content_type=None)
                            results = itunes_data.get('results', [])
                
                if results:
                    buttons = []
                    SONG_SEARCH_CACHE[task_id] = results
                    for idx, res in enumerate(results):
                        # Handle both Spotify and iTunes formats
                        title = res.get('name') or res.get('trackName', 'Unknown')
                        
                        if 'artists' in res and res['artists']:
                            artist = res['artists'][0].get('name', 'Unknown')
                        else:
                            artist = res.get('artistName', 'Unknown')
                        
                        if 'album' in res:
                            res['collectionName'] = res['album'].get('name', 'Unknown') # standardize for tagging later
                        btn_text = f"{title} - {artist}"
                        if len(btn_text) > 40:
                            btn_text = btn_text[:37] + "..."
                        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"songmatch_{task_id}_{idx}")])
                    buttons.append([InlineKeyboardButton("Skip (Use As-Is)", callback_data=f"songmatch_{task_id}_skip")])
                    
                    await status_msg.edit_text(
                        "⚠️ **No automatic match found.**\nDid you mean one of these songs?",
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
                    
                    SONG_EVENTS[task_id] = asyncio.Event()
                    await SONG_EVENTS[task_id].wait()
                    
                    choice = SONG_CHOICES.get(task_id, "skip")
                    if choice != "skip":
                        idx = int(choice)
                        track_info = SONG_SEARCH_CACHE[task_id][idx]
                        
                        imported_files = list(organized_dir.rglob("*.*"))
                        if imported_files:
                            moved_file = imported_files[0]
                            tmp_tagged = target_dir / f"tagged_temp{moved_file.suffix}"
                            tag_cmd = [
                                "ffmpeg", "-y", "-i", str(moved_file),
                                "-metadata", f"title={track_info.get('name', track_info.get('trackName', ''))}",
                                "-metadata", f"artist={track_info.get('artists', [{'name': track_info.get('artistName', '')}])[0].get('name', '')}",
                                "-metadata", f"album={track_info.get('album', {}).get('name', track_info.get('collectionName', ''))}",
                                "-c", "copy", str(tmp_tagged)
                            ]
                            proc = await asyncio.create_subprocess_exec(*tag_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                            await proc.communicate()
                            
                            # Wipe and re-run beets
                            shutil.rmtree(organized_dir, ignore_errors=True)
                            (target_dir / "library.blb").unlink(missing_ok=True)
                            
                            cmd = ["beet", "-c", str(beets_config), "import", "-q", "-s", str(tmp_tagged)]
                            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                            await proc.communicate()
                            
                            await status_msg.edit_text("🎵 **Song tagged successfully!**\n⏳ Uploading...")

            # after beets, files are in target_dir/organized. Upload that.
            organized_dir = target_dir / "organized"
            if not organized_dir.exists() or not any(organized_dir.iterdir()):
                # Fallback: if beets failed to move it, just upload the original
                organized_dir = target_dir / "fallback"
                unknown_artist_dir = organized_dir / "Unknown Artist"
                unknown_artist_dir.mkdir(parents=True, exist_ok=True)
                if process_filepath.exists():
                    shutil.move(str(process_filepath), unknown_artist_dir / process_filepath.name)
                
            directories_to_refresh = []
            if organized_dir.exists():
                for item in organized_dir.iterdir():
                    if item.is_dir():
                        directories_to_refresh.append(item.name)
                
            from bot.uploader import perform_autorclone
            _, final_bot_msg = await perform_autorclone(organized_dir, "Songs", status_msg, user_id=user_id, user_display=user_display)
            
            from bot.helpers import refresh_jellyfin
            if directories_to_refresh:
                # Refresh parent non-recursively so Rclone discovers the new Artist folders
                await refresh_jellyfin(telegram_msg=None, target_dir="Songs", recursive="false")
                for dir_name in directories_to_refresh:
                    await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir=f"Songs/{dir_name}")
            else:
                await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir="Songs", recursive="false")
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Song Download Failed: {e}")
        finally:
            GLOBAL_TASKS.pop(task_id, None)
            await task_manager.release(client)
            shutil.rmtree(target_dir, ignore_errors=True)
            
        return

    await message.reply_text("Usage: Reply to an audio/document with `/song`", parse_mode=ParseMode.MARKDOWN)
