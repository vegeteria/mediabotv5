from bot.auth import require_auth
import re
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

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LinkPreviewOptions

SONG_EVENTS = {}
SONG_CHOICES = {}
SONG_SEARCH_CACHE = {}



def embed_artwork(file_path, image_path):
    try:
        from pathlib import Path
        file_path = Path(file_path)
        image_path = Path(image_path)
        with open(image_path, "rb") as f:
            img_data = f.read()
            
        ext = file_path.suffix.lower()
        if ext == '.m4a' or ext == '.mp4':
            from mutagen.mp4 import MP4, MP4Cover
            audio = MP4(file_path)
            audio["covr"] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
        elif ext == '.mp3':
            from mutagen.mp3 import MP3
            from mutagen.id3 import ID3, APIC
            try:
                audio = MP3(file_path, ID3=ID3)
            except:
                audio = MP3(file_path)
                if audio.tags is None:
                    audio.add_tags()
            audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img_data))
            audio.save()
        elif ext == '.flac':
            from mutagen.flac import Picture, FLAC
            audio = FLAC(file_path)
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Front Cover"
            pic.data = img_data
            audio.add_picture(pic)
            audio.save()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to embed artwork: {e}")

def get_track_info(res):

    title = res.get('name') or res.get('trackName', 'Unknown')
    if 'artists' in res and res['artists']:
        artist = res['artists'][0].get('name', 'Unknown')
    else:
        artist = res.get('artistName', 'Unknown')
        
    album = "Unknown"
    cover_url = ""
    if 'album' in res:
        album = res['album'].get('name', 'Unknown')
        if res['album'].get('images'):
            cover_url = res['album']['images'][0].get('url', '')
    elif 'collectionName' in res:
        album = res['collectionName']
        cover_url = res.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
    
    duration_ms = res.get('duration_ms', 0)
    duration_str = "Unknown"
    if duration_ms:
        mins = duration_ms // 60000
        secs = (duration_ms % 60000) // 1000
        duration_str = f"{mins}:{secs:02d}"
        
    url = res.get('external_urls', {}).get('spotify', '')
        
    return title, artist, album, cover_url, duration_str, url

async def render_song_page(message, task_id, results, page, clean_name=""):
    if not results:
        text = f"⚠️ <b>No matches found</b>\nI couldn't find any results for '<code>{clean_name}</code>'."
        buttons = [[InlineKeyboardButton("🔴 ❌ Keep Original Metadata (Skip)", callback_data=f"songmatch_{task_id}_skip")]]
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
        return

    items_per_page = 5
    total_pages = (len(results) - 1) // items_per_page + 1
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    page_results = results[start_idx:end_idx]
    
    buttons = []
    text = f"🎶 <b>Manual Match Required</b>\nPage {page+1}/{total_pages}\n\nPlease select a track to view details:\n"
    
    for i, res in enumerate(page_results):
        actual_idx = start_idx + i
        title, artist, album, _, _, _ = get_track_info(res)
        
        btn_text = f"🎵 {title} - {artist}"
        if len(btn_text) > 40:
            btn_text = btn_text[:37] + "..."
            
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"songmatch_{task_id}_view_{actual_idx}_{page}")])
        
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"songmatch_{task_id}_page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"songmatch_{task_id}_page_{page+1}"))
        
    if nav_buttons:
        buttons.append(nav_buttons)
        
    buttons.append([InlineKeyboardButton("🔴 ❌ Keep Original Metadata (Skip)", callback_data=f"songmatch_{task_id}_skip")])
    
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True))

async def render_song_detail(message, task_id, results, idx, page):
    res = results[idx]
    title, artist, album, cover_url, duration, url = get_track_info(res)
    
    text = f"🎧 <b>Track Details</b>\n\n"
    if cover_url:
        text = f"<a href='{cover_url}'>&#8203;</a>" + text
        
    text += f"🎵 <b>Title:</b> {title}\n"
    text += f"👤 <b>Artist:</b> {artist}\n"
    text += f"💿 <b>Album:</b> {album}\n"
    text += f"⏱ <b>Duration:</b> {duration}\n"
    if url:
        text += f"🔗 <a href='{url}'>Open in Spotify</a>\n"
        
    text += "\n<i>Is this the correct track?</i>"
    
    buttons = [
        [
            InlineKeyboardButton("🟢 ✅ YES! Tag this Track", callback_data=f"songmatch_{task_id}_confirm_{idx}")
        ],
        [
            InlineKeyboardButton("🔵 🔙 Back to Search Results", callback_data=f"songmatch_{task_id}_back_{page}")
        ],
        [InlineKeyboardButton("🔴 ❌ Keep Original Metadata (Skip)", callback_data=f"songmatch_{task_id}_skip")]
    ]
    
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=False))

@Client.on_callback_query(filters.regex(r"^songmatch_"))
async def song_match_callback(client: Client, query):
    data = query.data.split("_")
    task_id = data[1]
    action = data[2]
    
    if task_id not in SONG_EVENTS or task_id not in SONG_SEARCH_CACHE:
        await query.answer("Session expired or invalid.", show_alert=True)
        return
        
    results = SONG_SEARCH_CACHE[task_id]
    
    if action == "skip":
        SONG_CHOICES[task_id] = "skip"
        SONG_EVENTS[task_id].set()
        await query.message.edit_text("⏳ Processing as-is...")
        
    elif action == "confirm":
        idx = int(data[3])
        SONG_CHOICES[task_id] = str(idx)
        SONG_EVENTS[task_id].set()
        await query.message.edit_text("⏳ Tagging track...")
        
    elif action in ("page", "back"):
        page = int(data[3])
        await render_song_page(query.message, task_id, results, page)
        
    elif action == "view":
        idx = int(data[3])
        page = int(data[4])
        await render_song_detail(query.message, task_id, results, idx, page)


from bot.config import BASE_SONGS, logger
from bot.state import USER_STATES, USER_TASKS, check_concurrency_limit, register_user_task, task_manager

@Client.on_message(filters.command("song"))
@require_auth
async def download_song(client: Client, message: Message):
    """Handle /song command."""
    if not message.reply_to_message or not (message.reply_to_message.audio or message.reply_to_message.document or message.reply_to_message.video or message.reply_to_message.voice):
        await message.reply_text("Usage: Reply to an audio, video, or document with `/song`", parse_mode=ParseMode.MARKDOWN)
        return

    user_id = message.from_user.id
    if not check_concurrency_limit(user_id):
        await message.reply_text("❌ You already have an active process. Please wait or use /cancel.")
        return

    register_user_task(user_id, asyncio.current_task())
    
    from bot.downloader import AsyncDownloader
    from bot.state import GLOBAL_TASKS, GlobalTask
    import shutil
    
    task_id = f"song{message.id}"
    qtask = GlobalTask()
    qtask.id = task_id
    qtask.user_id = user_id
    user_display = message.from_user.username or message.from_user.first_name
    qtask.user_display = f"@" + user_display if message.from_user.username else str(user_display)
    qtask.asyncio_task = asyncio.current_task()
    qtask.type = "music"
    GLOBAL_TASKS[task_id] = qtask
    
    status_msg = None
    target_dir = BASE_SONGS / task_id
    target_dir.mkdir(parents=True, exist_ok=True)
    
    from bot.config import get_base_url
    dashboard_link = f"{get_base_url()}/dashboard"

    try:
        await task_manager.acquire(qtask, client)
        status_msg = await message.reply_text(
            f"📥 Starting download...\n\n🌐 <a href='{dashboard_link}'>Open Dashboard</a> | Task ID: <code>{task_id}</code>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        
        from bot.downloader import ProgressTracker
        tracker = ProgressTracker(status_msg, "Downloading Audio")
        
        from bot.state import update_status_msg
        filepath = await AsyncDownloader.download_telegram_media(
            message.reply_to_message,
            dest_dir=target_dir,
            progress_tracker=tracker,
            user_id=user_id
        )
        
        if not filepath:
            await status_msg.edit_text("❌ Failed to download file.")
            return
            
        process_filepath = Path(filepath)
        
        # Clean the filename for searching
        clean_name = process_filepath.stem
        clean_name = re.sub(r'(?i)\d{2,3}\s*(kbps|mbps|hz)', '', clean_name)
        clean_name = re.sub(r'(?i)(official|video|audio|lyric|lyrics)', '', clean_name)
        clean_name = re.sub(r'[\(\[\{].*?[\)\]\}]', '', clean_name)
        clean_name = clean_name.strip()
        
        await status_msg.edit_text(f"🔍 Searching Spotify for: <code>{clean_name}</code>...", parse_mode=ParseMode.HTML)
        
        search_query = urllib.parse.quote(clean_name)
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
        
        SONG_SEARCH_CACHE[task_id] = results
        GLOBAL_TASKS[task_id].message = "⏸️ Waiting for your selection in Telegram..."
        await render_song_page(status_msg, task_id, results, 0, clean_name)
        
        SONG_EVENTS[task_id] = asyncio.Event()
        await SONG_EVENTS[task_id].wait()
        
        GLOBAL_TASKS[task_id].message = "⏳ Applying metadata tags..."
        choice = SONG_CHOICES.get(task_id, "skip")
        
        title = process_filepath.stem
        artist = "Unknown Artist"
        album = "Unknown Album"
        cover_url = ""
        
        if choice != "skip":
            idx = int(choice)
            track_info = SONG_SEARCH_CACHE[task_id][idx]
            title, artist, album, cover_url, _, _ = get_track_info(track_info)
            
        await status_msg.edit_text("⏳ Applying tags and organizing...", parse_mode=ParseMode.HTML)
        
        # Sanitize folder names
        def sanitize_name(name):
            return re.sub(r'[\\/*?:"<>|]', "", name).strip()
            
        safe_artist = sanitize_name(artist) or "Unknown Artist"
        safe_album = sanitize_name(album) or "Unknown Album"
        safe_title = sanitize_name(title) or "Unknown Title"
        
        organized_dir = target_dir / "organized"
        album_dir = organized_dir / safe_artist / safe_album
        album_dir.mkdir(parents=True, exist_ok=True)
        
        final_filename = f"{safe_title}{process_filepath.suffix}"
        final_path = album_dir / final_filename
        
        # 1. Download cover art if available
        cover_path = album_dir / "cover.jpg"
        if cover_url:
            async with aiohttp.ClientSession() as session:
                async with session.get(cover_url) as resp:
                    if resp.status == 200:
                        with open(cover_path, "wb") as f:
                            f.write(await resp.read())
                        shutil.copy(cover_path, album_dir / "folder.jpg")
        
        # 2. Apply text tags with ffmpeg
        tag_cmd = [
            "ffmpeg", "-y", "-i", str(process_filepath),
            "-metadata", f"title={title}",
            "-metadata", f"artist={artist}",
            "-metadata", f"album={album}",
            "-c", "copy", str(final_path)
        ]
        proc = await asyncio.create_subprocess_exec(*tag_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        
        # 3. Embed artwork physically using mutagen if cover exists
        if cover_path.exists():
            try:
                embed_artwork(final_path, cover_path)
            except Exception as e:
                logger.error(f"Failed to embed artwork: {e}")
                
        await status_msg.edit_text("🎵 <b>Song tagged successfully!</b>\n⏳ Uploading...", parse_mode=ParseMode.HTML)
        
        from bot.uploader import perform_autorclone
        from bot.helpers import refresh_jellyfin
        _, final_bot_msg = await perform_autorclone(organized_dir, "Songs", status_msg, user_id=user_id, user_display=user_display)
        
        # Refresh parent non-recursively so Rclone discovers the new Artist folders
        await refresh_jellyfin(telegram_msg=None, target_dir="Songs", recursive="false")
        await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir=f"Songs/{safe_artist}")
        
    except Exception as e:
        if status_msg:
            try:
                await status_msg.edit_text(f"❌ Song Download Failed: {e}")
            except:
                pass
        logger.error(f"Song upload failed: {e}")
    finally:
        GLOBAL_TASKS.pop(task_id, None)
        await task_manager.release(client)
        if 'target_dir' in locals() and target_dir.exists():
            import shutil
            shutil.rmtree(target_dir, ignore_errors=True)
        
    return

