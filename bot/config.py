"""
Configuration, constants, and logging setup.
"""

import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables
env_path = os.getenv("ENV_FILE_PATH")
if env_path:
    load_dotenv(env_path, override=True)
else:
    load_dotenv(override=True)

# ── Bot credentials ──────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
USER_SESSION_STRING = os.getenv("USER_SESSION_STRING", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# New: Groups for global dashboard visibility
_groups_str = os.getenv("GLOBAL_DASHBOARD_GROUPS", "")
GLOBAL_DASHBOARD_GROUPS = {int(x.strip()) for x in _groups_str.split(",") if x.strip()}

# New: Max concurrent tasks
MAX_CONCURRENT_TASKS = int(os.getenv("MAX_CONCURRENT_TASKS", "3"))
SINGLE_USER_CONCURRENT_TASK_LIMIT = int(os.getenv("SINGLE_USER_CONCURRENT_TASK_LIMIT", "0"))

# Archive Extraction Limit in GB (Threshold for sequential vs full extraction)
ARCHIVE_EXTRACTION_LIMIT_GB = float(os.getenv("ARCHIVE_EXTRACTION_LIMIT_GB", "15"))

# ── Media directories ────────────────────────────────────────────────────────
data_dir = os.getenv("MEDIA_DIR", str(Path.home() / "server"))
BASE_MOVIES = Path(data_dir) / "movies"
BASE_SERIES = Path(data_dir) / "series"
BASE_SONGS = Path(data_dir) / "songs"

# ── Web Server Configuration ──────────────────────────────────────────────────
WEB_SERVER_URL = os.getenv("WEB_SERVER_URL", "http://localhost").rstrip("/")
WEB_SERVER_PORT = int(os.getenv("WEB_SERVER_PORT", "8000"))

# Delay between progress updates in seconds (1.0 for real-time web dashboard)
PROGRESS_UPDATE_DELAY = float(os.getenv("PROGRESS_UPDATE_DELAY", "1.0"))

# ── Rclone Mount (For Fuzzy Detection) ───────────────────────────────────────
RCLONE_MOUNT_DIR = os.getenv("RCLONE_MOUNT_DIR", "/mnt/gdrive")
RCLONE_BASE_DIR = os.getenv("RCLONE_BASE_DIR", "").strip()

# Toggle to allow or block duplicate downloads (same name + same quality)
IS_DUPLICATE_ALLOWED = os.getenv("IS_DUPLICATE_ALLOWED", "False").lower() in ("true", "1", "yes")

# ── Jellyfin integration ─────────────────────────────────────────────────────
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://localhost:8096").rstrip("/")

# ── Env file path (for persisting authorized users) ──────────────────────────
ENV_FILE = Path(os.getenv("ENV_FILE_PATH", Path(__file__).parent.parent / ".env"))

# ── Video file extensions ────────────────────────────────────────────────────
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"}

# ── Season detection patterns ────────────────────────────────────────────────
SEASON_PATTERNS = [
    re.compile(r"[Ss](\d{1,2})[Ee]\d{1,3}"),           # S01E01
    re.compile(r"[Ss]eason[\s._-]*(\d{1,2})", re.I),   # Season 1, Season.1
    re.compile(r"[\s._-](\d{1,2})x\d{1,3}[\s._-]"),    # 1x01
]

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("mediabot")

# Limits concurrent rclone uploads

def get_base_url():
    """Returns the base URL with port if it's a local IP/localhost and port is missing."""
    url = WEB_SERVER_URL
    if f":{WEB_SERVER_PORT}" not in url:
        if any(x in url for x in ("127.0.0.1", "localhost", "0.0.0.0", "192.168.")):
            url = f"{url}:{WEB_SERVER_PORT}"
    return url
