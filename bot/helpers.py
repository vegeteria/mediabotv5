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


# ── Jellyfin & Rclone VFS integration ────────────────────────────────────────

_refresh_lock = asyncio.Lock()
_last_scan_end_time = 0.0

async def _safe_edit(msg, text: str):
    """Safely edit a telegram message with markdown, ignoring transient errors."""
    if not msg:
        return
    try:
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.debug(f"Could not edit status message: {e}")

async def _refresh_rclone_vfs(target_dir: Optional[str] = None, recursive: str = "true") -> tuple[bool, str]:
    """Synchronously refreshes Rclone VFS cache, with automatic parent fallback for newly added folders."""
    from bot.config import RCLONE_BASE_DIR, RCLONE_RC_URL
    import os
    
    rc_url = RCLONE_RC_URL or os.getenv("RCLONE_RC_URL", "http://localhost:5572").rstrip("/")
    if target_dir:
        if RCLONE_BASE_DIR:
            dir_path = f"{RCLONE_BASE_DIR}/{target_dir}".strip("/")
        else:
            dir_path = str(target_dir).strip("/")
    else:
        dir_path = RCLONE_BASE_DIR or ""

    timeout = aiohttp.ClientTimeout(total=60)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = {
                "dir": dir_path,
                "recursive": recursive,
                "_async": "false"
            }
            logger.info(f"Sending VFS refresh to Rclone RC ({rc_url})... dir='{dir_path}', recursive={recursive}")
            async with session.post(f"{rc_url}/vfs/refresh", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {})
                    # If target directory was brand new, Rclone VFS returns "file does not exist"
                    has_not_found = any("file does not exist" in str(v).lower() for v in result.values())
                    if has_not_found and dir_path:
                        top_category = dir_path.split("/")[0]
                        logger.warning(
                            f"Rclone VFS returned 'file does not exist' for '{dir_path}'. "
                            f"Fallback refreshing top-level category '{top_category}'..."
                        )
                        fallback_payload = {
                            "dir": top_category,
                            "recursive": "true",
                            "_async": "false"
                        }
                        async with session.post(f"{rc_url}/vfs/refresh", json=fallback_payload) as fb_resp:
                            if fb_resp.status == 200:
                                logger.info(f"Top-level category '{top_category}' refreshed successfully in Rclone VFS.")
                                return True, "Refreshed parent category"
                            else:
                                err = await fb_resp.text()
                                logger.warning(f"Fallback VFS refresh failed ({fb_resp.status}): {err}")
                                return False, f"Fallback failed: {err}"
                    logger.info(f"Rclone VFS cache refreshed successfully for '{dir_path}'.")
                    return True, "Cache updated"
                else:
                    err = await resp.text()
                    logger.warning(f"Rclone RC returned HTTP {resp.status}: {err}")
                    return False, f"HTTP {resp.status}"
    except Exception as e:
        logger.error(f"Could not reach Rclone RC at {rc_url}: {e}")
        return False, f"Unreachable ({e})"

async def _trigger_jellyfin_scan(status_msg=None, step_prefix: str = "[2/3]") -> tuple[bool, str]:
    """Triggers Jellyfin library scan and actively monitors ScheduledTasks until completed."""
    import time
    from bot.config import JELLYFIN_URL, JELLYFIN_API_KEY
    import os

    jf_url = JELLYFIN_URL or os.getenv("JELLYFIN_URL", "http://localhost:8096").rstrip("/")
    api_key = JELLYFIN_API_KEY or os.getenv("JELLYFIN_API_KEY", "")

    if not api_key:
        logger.warning("JELLYFIN_API_KEY is not configured; skipping Jellyfin library scan.")
        return False, "API key not set"

    headers = {
        'Authorization': f'MediaBrowser Token="{api_key}", Client="MediaBot", Device="Script", DeviceId="Bot123", Version="5.1.0"'
    }

    initial_start = ""
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Inspect current task execution time
            try:
                async with session.get(f"{jf_url}/ScheduledTasks", headers=headers) as resp:
                    if resp.status == 200:
                        tasks = await resp.json()
                        task = next((t for t in tasks if t.get("Key") == "RefreshLibrary"), None)
                        if task:
                            initial_start = task.get("LastExecutionResult", {}).get("StartTimeUtc", "")
            except Exception as e:
                logger.debug(f"Could not fetch initial Jellyfin task state: {e}")

            # 2. Trigger scan
            url = f"{jf_url}/Library/Refresh"
            logger.info("Invoking Jellyfin global library refresh...")
            async with session.post(url, headers=headers) as response:
                if response.status not in (200, 204):
                    err = await response.text()
                    logger.warning(f"Jellyfin /Library/Refresh returned status {response.status}: {err}")
                    return False, f"HTTP {response.status}"

            # 3. Poll ScheduledTasks until scan officially finishes
            poll_attempts = 0
            has_started = False
            last_edit_time = 0.0

            while poll_attempts < 45: # max ~90 seconds
                await asyncio.sleep(2)
                poll_attempts += 1
                try:
                    async with session.get(f"{jf_url}/ScheduledTasks", headers=headers) as resp:
                        if resp.status == 200:
                            tasks = await resp.json()
                            task = next((t for t in tasks if t.get("Key") == "RefreshLibrary"), None)
                            if not task:
                                break
                            state = task.get("State")
                            cur_start = task.get("LastExecutionResult", {}).get("StartTimeUtc", "")
                            progress = task.get("CurrentProgressPercentage")

                            if state == "Running":
                                has_started = True
                                now = time.time()
                                if status_msg and progress is not None and (now - last_edit_time > 3.0):
                                    last_edit_time = now
                                    pct = int(progress)
                                    await _safe_edit(status_msg, f"⏳ **{step_prefix} Scanning Jellyfin media library...** ({pct}%)")
                            elif state == "Idle":
                                if has_started or cur_start != initial_start or poll_attempts >= 3:
                                    logger.info("Jellyfin media library scan completed successfully.")
                                    return True, "Scan completed"
                except Exception as e:
                    logger.warning(f"Polling Jellyfin tasks encountered error: {e}")

            return True, "Scan finished"
    except Exception as e:
        logger.error(f"Failed to communicate with Jellyfin: {e}")
        return False, str(e)

async def _run_tv_auto_merger() -> tuple[bool, str]:
    """Runs auto_merger.py using current python executable and returns summary."""
    import sys
    try:
        logger.info("Spawning auto_merger.py for TV series...")
        process = await asyncio.create_subprocess_exec(
            sys.executable, "auto_merger.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            out_str = stdout.decode("utf-8", errors="ignore")
            logger.info(f"Auto-merger finished successfully:\n{out_str}")
            m = re.search(r"Merged (\d+) total groups", out_str)
            if m:
                count = int(m.group(1))
                if count > 0:
                    return True, f"Merged {count} duplicate episode group(s)"
                else:
                    return True, "All episodes up to date"
            return True, "Completed"
        else:
            err_str = stderr.decode("utf-8", errors="ignore")
            logger.error(f"Auto-merger failed with code {process.returncode}:\n{err_str}")
            return False, f"Failed (exit code {process.returncode})"
    except Exception as e:
        logger.error(f"Could not execute auto_merger.py: {e}")
        return False, str(e)

async def refresh_jellyfin(telegram_msg=None, target_dir=None, recursive="true"):
    """Trigger rclone VFS refresh, Jellyfin library scan, and TV Auto-Merger with in-place status tracking."""
    import time
    global _last_scan_end_time, _refresh_lock

    start_time = time.time()
    is_manual = (target_dir is None)
    target_str = str(target_dir).lower() if target_dir else ""
    is_tv_or_global = is_manual or target_str.startswith("series") or ("series" in target_str)
    total_steps = 3 if is_tv_or_global else 2

    # 1. Determine status message handling
    status_msg = None
    if telegram_msg:
        try:
            msg_text = getattr(telegram_msg, "text", "") or ""
            if "Triggering media library refresh" in msg_text:
                status_msg = telegram_msg
                await _safe_edit(status_msg, f"⏳ **[1/{total_steps}] Syncing filesystem cache (Rclone VFS)...**")
            else:
                status_msg = await telegram_msg.reply_text(
                    f"⏳ **[1/{total_steps}] Syncing filesystem cache (Rclone VFS)...**",
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            logger.debug(f"Could not initialize status message: {e}")

    # 2. Step 1: Synchronous Rclone VFS Cache Refresh
    rc_ok, rc_info = await _refresh_rclone_vfs(target_dir=target_dir, recursive=recursive)
    vfs_done_time = time.time()

    # 3. Step 2 & Step 3: Jellyfin Library Scan & TV Auto-Merger under concurrency lock
    if status_msg:
        await _safe_edit(status_msg, f"⏳ **[2/{total_steps}] Scanning Jellyfin media library...**")

    async with _refresh_lock:
        # Coalesce: if another scan finished after this VFS refresh, library is already up to date
        if not is_manual and _last_scan_end_time > vfs_done_time:
            logger.info("Jellyfin library was already scanned after this VFS sync. Skipping redundant scan.")
            jf_ok, jf_info = True, "Already up to date"
            merger_summary = "All episodes up to date" if is_tv_or_global else None
        else:
            jf_ok, jf_info = await _trigger_jellyfin_scan(status_msg=status_msg, step_prefix=f"[{2}/{total_steps}]")
            _last_scan_end_time = time.time()

            if is_tv_or_global:
                if status_msg:
                    await _safe_edit(status_msg, f"⏳ **[3/{total_steps}] Grouping duplicate TV episodes...**")
                merger_ok, merger_summary = await _run_tv_auto_merger()
            else:
                merger_summary = None

    # 4. Final permanent status message update
    elapsed = round(time.time() - start_time, 1)
    lines = [
        "✅ **Media library refreshed & synced!**",
        f"⏱️ Completed in **{elapsed}s**"
    ]
    if not rc_ok:
        lines.append(f"⚠️ **Rclone VFS:** {rc_info}")
    if not jf_ok:
        lines.append(f"⚠️ **Jellyfin:** {jf_info}")
    if merger_summary:
        lines.append(f"🔄 **TV Auto-Merger:** {merger_summary}")

    if status_msg:
        await _safe_edit(status_msg, "\n".join(lines))

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
