from pyrogram.enums import ParseMode
import asyncio
"""
Small, reusable utility functions.
"""

import mimetypes
import os
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import aiohttp

from bot.config import BASE_SERIES, JELLYFIN_API_KEY, logger


# ── URL validation ───────────────────────────────────────────────────────────

def validate_url(url: str) -> bool:
    """Validate URL format."""
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False


# ── Jellyfin integration ─────────────────────────────────────────────────────

_merger_task = None
_merger_target_time = 0
_merger_msgs = []

async def _debounced_merger_loop():
    global _merger_target_time, _merger_msgs
    import time
    
    logger.info("Auto-merger debouncer started.")
    
    last_reported_remaining = -1
    while True:
        now = time.time()
        if now >= _merger_target_time:
            break
            
        remaining = int(_merger_target_time - now)
        
        report_remaining = ((remaining + 9) // 10) * 10
        if report_remaining != last_reported_remaining:
            last_reported_remaining = report_remaining
            for msg in _merger_msgs:
                try:
                    await msg.edit_text(f"⏳ **Rclone is syncing...**\nPreparing media in **{report_remaining} seconds**.", parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
                    
        await asyncio.sleep(min(2, remaining))
        
    # --- ACTIVE POLLING PHASE ---
    logger.info("Debounce timer finished. Waiting 3 seconds for Jellyfin task scheduler to catch up...")
    
    await asyncio.sleep(3)
    
    logger.info("Starting Jellyfin API active polling...")
    for msg in _merger_msgs:
        try:
            await msg.edit_text("⏳ **Jellyfin is scanning...**\nWaiting for library scan to officially complete...", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    import os
    jf_url = os.getenv("JELLYFIN_URL", "http://localhost:8096").rstrip('/')
    headers = {'X-Emby-Token': JELLYFIN_API_KEY}
    
    poll_attempts = 0
    while poll_attempts < 60:
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{jf_url}/ScheduledTasks", headers=headers) as resp:
                    if resp.status == 200:
                        tasks = await resp.json()
                        scan_task = next((t for t in tasks if t.get("Key") == "RefreshLibrary"), None)
                        if scan_task and scan_task.get("State") == "Running":
                            poll_attempts += 1
                            await asyncio.sleep(5)
                            continue
        except Exception as e:
            logger.warning(f"Failed to poll Jellyfin tasks: {e}")
        break
        
    for msg in _merger_msgs:
        try:
            await msg.edit_text("✅ **Jellyfin Scan Complete!**\n🔄 **Executing TV Show Auto-Merger...**", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    try:
        logger.info("Spawning auto_merger.py...")
        process = await asyncio.create_subprocess_exec(
            "python3", "auto_merger.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            logger.info(f"Auto-merger finished successfully:\n{stdout.decode()}")
            for msg in _merger_msgs:
                try:
                    await msg.edit_text(f"✅ **TV Show Auto-Merger Finished Successfully!**\n\nAll duplicate TV episodes have been grouped.", parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
        else:
            logger.error(f"Auto-merger failed:\n{stderr.decode()}")
            for msg in _merger_msgs:
                try:
                    await msg.edit_text(f"❌ **Auto-Merger Failed!**\nCheck bot logs.", parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Could not run auto_merger.py: {e}")
        
    # Delete all status messages when completely done!
    await asyncio.sleep(2)
    for msg in _merger_msgs:
        try:
            await msg.delete()
        except Exception:
            pass
    _merger_msgs.clear()

async def refresh_jellyfin(telegram_msg=None, target_dir=None, recursive="true"):
    """Trigger rclone VFS refresh and Jellyfin library scan."""
    
    try:
        logger.info(f"Sending VFS refresh signal to rclone mount... target_dir={target_dir}, recursive={recursive}")
        timeout = aiohttp.ClientTimeout(total=120) # Increased timeout in case of synchronous refresh
        async with aiohttp.ClientSession(timeout=timeout) as session:
            from bot.config import RCLONE_BASE_DIR
            payload = {"recursive": recursive}
            if target_dir:
                if RCLONE_BASE_DIR:
                    payload["dir"] = f"{RCLONE_BASE_DIR}/{target_dir}".strip("/")
                else:
                    payload["dir"] = target_dir
                payload["_async"] = "false" # Synchronously wait for target dir
            else:
                if RCLONE_BASE_DIR:
                    payload["dir"] = RCLONE_BASE_DIR
                payload["_async"] = "true" # Fallback to async for global refresh
                
            async with session.post("http://localhost:5572/vfs/refresh", json=payload) as rc_resp:
                if rc_resp.status == 200:
                    logger.info("Rclone VFS cache refreshed successfully!")
                else:
                    error_text = await rc_resp.text()
                    logger.warning(f"Rclone RC returned status {rc_resp.status}: {error_text}")
    except Exception as e:
        logger.error(f"Could not reach Rclone RC (Is --rc enabled on your host mount?): {e}")

    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            from bot.config import JELLYFIN_URL
            url = f"{JELLYFIN_URL}/Library/Refresh?api_key={JELLYFIN_API_KEY}"
            logger.info("Triggering Jellyfin global library refresh...")
            async with session.post(url) as response:
                if response.status in (200, 204):
                    logger.info("Jellyfin library refresh invoked successfully.")
                else:
                    logger.warning(f"Jellyfin refresh returned status: {response.status}")
    except Exception as e:
        logger.error(f"Failed to invoke Jellyfin refresh: {e}")

    # --- INJECTED AUTO MERGER TRIGGER ---
    global _merger_task, _merger_target_time, _merger_msgs
    import time
    
    if telegram_msg:
        try:
            new_msg = await telegram_msg.reply_text("⏳ **Rclone is syncing...**", parse_mode=ParseMode.MARKDOWN)
            _merger_msgs.append(new_msg)
        except Exception:
            pass
    
    # We still keep a small 10s debouncer just in case multiple tasks are finishing back to back.
    # The real wait time is now handled by the Active Poller.
    _merger_target_time = time.time() + 10
    
    if _merger_task is None or _merger_task.done():
        _merger_task = asyncio.create_task(_debounced_merger_loop())
    # ------------------------------------

# ── File extension sniffing ──────────────────────────────────────────────────

def get_file_extension(filepath: Path) -> str:
    """Gets extension from file, or sniffs it with `file` if missing."""
    ext = filepath.suffix
    if not ext:
        try:
            mime_out = subprocess.check_output(
                ["file", "-b", "--mime-type", str(filepath)]
            ).decode().strip()
            if "mp4" in mime_out or "webm" in mime_out or "ogg" in mime_out:
                ext = mimetypes.guess_extension(mime_out) or ".mp4"
            elif "matroska" in mime_out:
                ext = ".mkv"
            elif "avi" in mime_out:
                ext = ".avi"
            elif "zip" in mime_out:
                ext = ".zip"
            else:
                ext = ".mkv"
        except Exception:
            ext = ".mkv"
    return ext


# ── Fuzzy folder matching ────────────────────────────────────────────────────

def clean_folder_name(name: str) -> str:
    """Strips tags like - 1080p, [MB], [Dub], (2020) and non-alphanumeric chars for matching."""
    base_name = re.sub(r"(?i)\s*-\s*(480p|720p|1080p|4k)$", "", name)
    base_name = re.sub(r"\[.*?\]", "", base_name)
    base_name = re.sub(r"\(\d{4}\)", "", base_name)
    return re.sub(r"[\W_]+", "", base_name).lower()


def find_fuzzy_series_folder(series_name: str) -> str:
    """Case-insensitive match of an existing folder in cloud mount then BASE_SERIES."""
    from bot.config import RCLONE_MOUNT_DIR, RCLONE_BASE_DIR
    clean_target = clean_folder_name(series_name)

    cloud_dir = Path(RCLONE_MOUNT_DIR)
    if RCLONE_BASE_DIR:
        cloud_dir = cloud_dir / RCLONE_BASE_DIR
    cloud_dir = cloud_dir / "Series"
    
    if cloud_dir.exists():
        for item in cloud_dir.iterdir():
            if item.is_dir():
                clean_item = clean_folder_name(item.name)
                if clean_item == clean_target:
                    return item.name

    if BASE_SERIES.exists():
        for item in BASE_SERIES.iterdir():
            if item.is_dir():
                clean_item = clean_folder_name(item.name)
                if clean_item == clean_target:
                    return item.name
    return series_name


def find_fuzzy_season_folder(series_dir: Path, season: int) -> str:
    """Find an existing season folder or return 'Season {season}'."""
    if not series_dir.exists():
        return f"Season {season}"
    for item in series_dir.iterdir():
        if item.is_dir():
            m = re.match(r"^(?:season|s)\s*0?(\d+)$", item.name, re.IGNORECASE)
            if m and int(m.group(1)) == season:
                return item.name
    return f"Season {season}"


def find_fuzzy_movie_folder(movie_name: str, dub: str = None) -> tuple[bool, list[str]]:
    """Returns (True, list_of_qualities) if movie exists in cloud mount."""
    from bot.config import RCLONE_MOUNT_DIR, RCLONE_BASE_DIR
    clean_target = clean_folder_name(movie_name)

    cloud_dir = Path(RCLONE_MOUNT_DIR)
    if RCLONE_BASE_DIR:
        cloud_dir = cloud_dir / RCLONE_BASE_DIR
    cloud_dir = cloud_dir / "Movies"
    
    if cloud_dir.exists():
        for item in cloud_dir.iterdir():
            if item.is_dir():
                clean_item = clean_folder_name(item.name)
                if clean_item == clean_target:
                    found_qualities = set()
                    dub_found = False
                    
                    for file_item in item.iterdir():
                        if file_item.is_file():
                            # Check dub
                            if dub and f"[{dub}]" in file_item.name:
                                dub_found = True
                            elif not dub:
                                dub_found = True
                                
                            q = extract_quality(file_item.name)
                            if q:
                                found_qualities.add(q)
                                
                    if dub and not dub_found:
                        continue
                        
                    # Fallback to checking the folder name itself (legacy support)
                    if not found_qualities:
                        q = extract_quality(item.name)
                        if q:
                            found_qualities.add(q)
                            
                    return True, list(found_qualities)
    return False, []


def check_episode_exists_in_cloud(series_name: str, season: int, episode: int, quality: str, dub: str = None) -> bool:
    """Check if specific episode and quality exist in cloud mount."""
    from bot.config import RCLONE_MOUNT_DIR, RCLONE_BASE_DIR
    cloud_dir = Path(RCLONE_MOUNT_DIR)
    if RCLONE_BASE_DIR:
        cloud_dir = cloud_dir / RCLONE_BASE_DIR
    cloud_dir = cloud_dir / "Series"
    
    clean_target = clean_folder_name(series_name)
    series_match = None
    if cloud_dir.exists():
        for item in cloud_dir.iterdir():
            if item.is_dir() and clean_folder_name(item.name) == clean_target:
                if dub and f"[{dub}]" not in item.name:
                    continue
                series_match = item
                break
                
    if not series_match:
        return False
        
    season_name = find_fuzzy_season_folder(series_match, season)
    season_dir = series_match / season_name
    
    if not season_dir.exists():
        return False
        
    for item in season_dir.iterdir():
        if item.is_file():
            # Parse episode
            ep_match = re.search(r"[Ee](\d{1,3})", item.name, re.IGNORECASE)
            if ep_match and int(ep_match.group(1)) == episode:
                # Check quality (inherit from series root folder if file lacks it)
                found_q = extract_quality(item.name) or extract_quality(series_match.name)
                if found_q == quality:
                    return True
    return False


def check_season_exists_in_cloud(series_name: str, season: int, quality: str, dub: str = None) -> bool:
    """Check if ANY episode of this season exists in requested quality in cloud mount."""
    from bot.config import RCLONE_MOUNT_DIR, RCLONE_BASE_DIR
    cloud_dir = Path(RCLONE_MOUNT_DIR)
    if RCLONE_BASE_DIR:
        cloud_dir = cloud_dir / RCLONE_BASE_DIR
    cloud_dir = cloud_dir / "Series"
    
    clean_target = clean_folder_name(series_name)
    series_match = None
    if cloud_dir.exists():
        for item in cloud_dir.iterdir():
            if item.is_dir() and clean_folder_name(item.name) == clean_target:
                if dub and f"[{dub}]" not in item.name:
                    continue
                series_match = item
                break
                
    if not series_match:
        return False
        
    season_name = find_fuzzy_season_folder(series_match, season)
    season_dir = series_match / season_name
    
    if not season_dir.exists():
        return False
        
    for item in season_dir.iterdir():
        if item.is_file():
            found_q = extract_quality(item.name) or extract_quality(series_match.name)
            if found_q == quality:
                return True
    return False


# ── Filename parsing ─────────────────────────────────────────────────────────

def extract_quality(filename: str) -> Optional[str]:
    """Smartly extract and normalize quality from messy filenames."""
    filename_lower = filename.lower()
    
    # Check for 4K / UHD
    if re.search(r"(?:^|[\s._-])(4k|2160p|2160|uhd)(?:$|[\s._-])", filename_lower):
        return "4k"
    
    # Check for 1080p / FHD
    if re.search(r"(?:^|[\s._-])(1080p|1080i|1080|fhd)(?:$|[\s._-])", filename_lower):
        return "1080p"
        
    # Check for 720p / HD
    if re.search(r"(?:^|[\s._-])(720p|720i|720|hd)(?:$|[\s._-])", filename_lower):
        return "720p"
        
    # Check for 480p / SD
    if re.search(r"(?:^|[\s._-])(480p|480|sd)(?:$|[\s._-])", filename_lower):
        return "480p"
        
    return None

async def detect_quality_with_ffprobe(filepath: str):
    import asyncio, json
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "json", str(filepath)]
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        probe_data = json.loads(stdout.decode('utf-8', errors='replace'))
        for s in probe_data.get("streams", []):
            h = s.get("height", 0)
            if h >= 2160: return "4k"
            elif h >= 1080: return "1080p"
            elif h >= 720: return "720p"
            else: return "480p"
    except Exception:
        pass
    return None

def parse_movie_filename(filename: str) -> str:
    """Extracts base movie title from messy scene release filenames."""
    name = re.sub(r"\.[a-zA-Z0-9]{2,4}$", "", filename)
    match = re.search(r"^(.*?)(?:[\s._-]+(19\d{2}|20\d{2}|480p|720p|1080p|1080i|2160p|4k|bluray|web-dl|hdrip|hdcam)(?:[\s._-]|$))", name, re.IGNORECASE)
    if match:
        name = match.group(1)
    name = re.sub(r"[\._-]", " ", name).strip()
    return name if name else filename

def parse_episode_filename(filename: str) -> Optional[tuple]:
    """
    Parse series name, season, and episode from a filename like
    ``The.Office.S04E05.mkv`` → ("The Office", 4, 5).
    Returns None on failure.
    """
    match = re.search(
        r"^(.*?)(?:[\s._-]+)(?:[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})|(\d{1,2})x(\d{1,3}))",
        filename,
        re.IGNORECASE,
    )
    if not match:
        return None
    series_name_raw = match.group(1)
    series_name = series_name_raw.replace(".", " ").replace("_", " ").strip()
    s1, e1, s2, e2 = match.groups()[1:]
    season = int(s1) if s1 is not None else int(s2)
    episode = int(e1) if e1 is not None else int(e2)
    quality = extract_quality(filename)
    return series_name, season, episode, quality


def parse_series_archive_filename(filename: str) -> Optional[tuple]:
    """
    Parse series name and season from an archive filename like
    ``Breaking.Bad.S01.zip`` → ("Breaking Bad", 1).
    Returns None on failure.
    """
    name_no_ext = re.sub(
        r"\.(zip|rar|7z|tar(\.[a-z0-9]+)?)$", "", filename, flags=re.IGNORECASE
    )
    match = re.search(
        r"^(.*?)(?:[\s._-]+)(?:[Ss](\d{1,2})|Season[\s._-]*(\d{1,2}))(?:[\s._-]+|$)",
        name_no_ext,
        re.IGNORECASE,
    )
    if match:
        series_name_raw = match.group(1)
        series_name = series_name_raw.replace(".", " ").replace("_", " ").strip()
        s1, s2 = match.groups()[1:]
        season = int(s1) if s1 is not None else int(s2)
        quality = extract_quality(filename)
        return series_name, season, quality
    return None


# ── Movie name heuristic ─────────────────────────────────────────────────────

def is_meaningful_movie_name(filename: str) -> bool:
    """Return True if the filename looks like a real movie title."""
    name = re.sub(r"\.[a-zA-Z0-9]+$", "", filename)
    name_lower = name.lower()

    meaningless_exact = {"download", "video", "movie", "media", "index", "file", "playlist"}
    if name_lower in meaningless_exact:
        return False

    # Random hex hashes
    if re.match(r"^[a-f0-9]{10,}$", name_lower):
        return False

    # Has a year → probably a movie name
    if re.search(r"\b(19\d{2}|20[0-2]\d)\b", name):
        return True

    # Multiple words
    words = [w for w in re.split(r"[\s._-]+", name) if len(w) > 1]
    if len(words) >= 2:
        return True

    return False

def get_clean_movie_name(raw_name: str) -> str:
    """Extracts 'Movie Name (Year)' from a messy release filename."""
    import re
    match = re.search(r"^(.+?)(?:[\. \(]+(19\d{2}|20\d{2})[\.\) ]+)", raw_name)
    if match:
        name = match.group(1).replace(".", " ").strip()
        year = match.group(2)
        return f"{name} ({year})"
    return raw_name

def get_existing_movie_folder(movie_name: str) -> Optional[str]:
    """Returns the exact name of an existing movie folder in the cloud mount using fuzzy matching."""
    from bot.config import RCLONE_MOUNT_DIR, RCLONE_BASE_DIR
    clean_target = clean_folder_name(movie_name)

    cloud_dir = Path(RCLONE_MOUNT_DIR)
    if RCLONE_BASE_DIR:
        cloud_dir = cloud_dir / RCLONE_BASE_DIR
    cloud_dir = cloud_dir / "Movies"
    
    if cloud_dir.exists():
        for item in cloud_dir.iterdir():
            if item.is_dir():
                clean_item = clean_folder_name(item.name)
                if clean_item == clean_target:
                    return item.name
    return None
