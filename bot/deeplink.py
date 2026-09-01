"""
Telegram deep-link support with a pending-link store.

Since Telegram deep-link payloads are limited to 64 characters and only allow
[A-Za-z0-9_-], we can't embed full download URLs directly.  Instead we use
a short random key that maps to the real URL stored on disk.

Flow:
  1.  Web page calls ``generate_deep_link(bot_username, "movie", url)``
      → returns ``https://t.me/bot?start=movie_<8-char-key>``
  2.  User clicks the link → Telegram sends ``/start movie_<key>`` to the bot.
  3.  ``resolve_deep_link("movie_<key>")`` looks up the URL from the JSON file.

The store auto-expires entries older than LINK_TTL_HOURS.
"""

import json
import secrets
import time
from pathlib import Path
from typing import Optional, Tuple

from bot.config import logger

# ── Configuration ────────────────────────────────────────────────────────────

_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "pending_links.json"
LINK_TTL_HOURS = 24          # links expire after this many hours
KEY_LENGTH = 8               # 8 chars → 2^48 possibilities, more than enough

# Valid deep-link command prefixes
VALID_PREFIXES = ("movie", "series", "episode")


# ── Store helpers ────────────────────────────────────────────────────────────

def _load_store() -> dict:
    """Load the pending links store from disk."""
    if not _STORE_PATH.exists():
        return {}
    try:
        with open(_STORE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_store(store: dict):
    """Save the pending links store to disk."""
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STORE_PATH, "w") as f:
        json.dump(store, f, indent=2)


def _purge_expired(store: dict) -> dict:
    """Remove entries older than LINK_TTL_HOURS."""
    cutoff = time.time() - (LINK_TTL_HOURS * 3600)
    return {k: v for k, v in store.items() if v.get("ts", 0) > cutoff}


# ── Public API ───────────────────────────────────────────────────────────────

def create_pending_link(command: str, url: str) -> str:
    """
    Store a URL and return a short key for use in a deep-link payload.

    Args:
        command: One of "movie", "series", "episode".
        url: The raw download URL.

    Returns:
        The short key (e.g. ``"a3Bf9x2K"``).
    """
    if command not in VALID_PREFIXES:
        raise ValueError(f"Invalid command: {command!r}. Must be one of {VALID_PREFIXES}")

    store = _load_store()
    store = _purge_expired(store)

    key = secrets.token_urlsafe(KEY_LENGTH)[:KEY_LENGTH]  # 8 URL-safe chars
    store[key] = {
        "cmd": command,
        "url": url,
        "ts": time.time(),
    }

    _save_store(store)
    logger.info("Created pending deep-link %s → %s %s", key, command, url)
    return key


def generate_deep_link(bot_username: str, command: str, url: str) -> str:
    """
    Generate a full Telegram deep-link URL.

    Args:
        bot_username: The bot's username (without @).
        command: One of "movie", "series", "episode".
        url: The raw download URL.

    Returns:
        ``https://t.me/<bot>?start=<command>_<key>``

    Example::

        >>> generate_deep_link("mybot", "movie", "https://example.com/file.mkv")
        'https://t.me/mybot?start=movie_a3Bf9x2K'
    """
    key = create_pending_link(command, url)
    return f"https://t.me/{bot_username}?start={command}_{key}"


def resolve_deep_link(payload: str) -> Optional[Tuple[str, str]]:
    """
    Resolve a deep-link payload into (command, url).

    Args:
        payload: The raw payload from ``/start``, e.g. ``"movie_a3Bf9x2K"``.

    Returns:
        A tuple of (command, url), or None if invalid/expired.
    """
    for prefix in VALID_PREFIXES:
        full_prefix = f"{prefix}_"
        if payload.startswith(full_prefix):
            key = payload[len(full_prefix):]
            if not key:
                return None

            store = _load_store()
            entry = store.get(key)

            if not entry:
                return None

            # Check expiry
            if time.time() - entry.get("ts", 0) > LINK_TTL_HOURS * 3600:
                # Clean up expired entry
                del store[key]
                _save_store(store)
                return None

            # Remove after use (one-time link)
            del store[key]
            _save_store(store)

            logger.info("Resolved deep-link %s → %s %s", key, entry["cmd"], entry["url"])
            return entry["cmd"], entry["url"]

    return None
