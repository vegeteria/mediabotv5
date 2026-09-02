# 🎬 Telegram Media Download Bot

**MediaBot Advanced:** A powerful Telegram bot to download, extract, and process movies & series. Features smart FFmpeg audio conversion (ensuring AAC Stereo compatibility), MKV web optimization, MovieBox integration, and a sleek real-time Web Dashboard to monitor tasks. Fully containerized with Docker.

## Features

| Feature | Description |
|---------|-------------|
| **Authorization** | Owner + authorized users system with `/adduser` and `/remuser` |
| **Mirror-Leech UI** | Detailed, real-time download and upload progress bars (`[████████░░] 80%`) with Speed and ETA |
| **Media Processing**| Interactive UI to convert Audio to Stereo AAC (with toggle to keep or replace 5.1 tracks) and Web Optimize MKV files (Faststart) for perfect HTTP streaming & seeking! |
| **Global Queue** | `/status` command to monitor all active tasks server-wide in real-time |
| **Speed Boost** | Accelerated multi-stream downloads using ThrottleBuster |
| **Auto-Rclone Upload**| Automatically uploads downloaded movies and series directly to your Google Drive |
| **Jellyfin Refresh** | Auto-refreshes your Rclone VFS mount instantly via RC API, and triggers Jellyfin library refresh exactly when uploads finish |
| **Jellyfin Auto-Merge**| A built-in background script automatically detects and groups duplicate movies and TV episodes (e.g. 1080p and 4K versions) 60 seconds after a refresh |
| **Index & Cloud Links**| Generates your personal direct Cloud Links and Index paths upon successful upload |
| **Movie Downloads** | Direct file download with smart name detection and auto-folder wrapping |
| **Series Downloads** | Archive extraction + batch audio/video processing + automatic Season folder organization |
| **Episode Downloads** | Auto-detect series/season/episode from filenames |
| **Moviebox Search** | Interactive movie & series search via `/mbmovie` and `/mbseries` |
| **Smart Detection** | Parses `S01E01`, `Season 1`, `1x01` patterns automatically |
| **Password Archives** | Handles password-protected series archives seamlessly |

| **Premium Web UI** | Built-in lightweight `aiohttp` web server rendering a beautiful, real-time `/dashboard` (with Gemini-style glassmorphism) to monitor active downloads globally, plus blazing-fast movie info pages via Cloudflare CDN |

## Setup (Docker)

1. **Configure environment variables:**
   ```bash
   cp .env.example .env
   nano .env
   ```
   Set your essential values:
   ```env
   BOT_TOKEN=123456:ABC-DEF...       # From @BotFather
   OWNER_ID=123456789                # Your Telegram user ID
   GLOBAL_DASHBOARD_GROUPS=-100123...# Comma-separated Group IDs where normal users can use the bot
   MAX_CONCURRENT_TASKS=3            # Limit the max number of heavy active downloads/uploads at a time
   MEDIA_DIR=/media/storage          # The container path to your local disk
   JELLYFIN_API_KEY=xyz123           # Optional
   PROGRESS_UPDATE_DELAY=10.0        # Optional (defaults to 10.0 seconds)
   RCLONE_REMOTE=gdrive              # Rclone Remote Name
   RCLONE_BASE_DIR=                  # Optional Base Directory on Remote
   RCLONE_MOUNT_DIR=/mnt/gdrive      # Path to the mounted rclone drive inside the container for fast fuzzy matching
   RCLONE_RC_URL=http://localhost:5572 # Point to your Remote Server IP if Rclone is hosted elsewhere
   IS_DUPLICATE_ALLOWED=False        # Set to True to disable duplicate download blocking
   INDEX_URL=https://index...        # Your Google Drive Index Worker URL
   CLOUD_LINK_BASE=https://...       # Your Direct Cloud Link Base URL
   WEB_SERVER_URL=https://movie.com  # (Optional) Your Caddy-proxied domain for Web UI
   WEB_SERVER_PORT=8080              # (Optional) Port for the internal Web UI (default 8080)
   ```

2. **Run Rclone Mount on Host:**
   To ensure the bot can instantly update your Jellyfin mount without polling delays, ensure your host machine is running the rclone mount with Remote Control enabled. It is recommended to use an optimized systemd service:

   ```bash
   sudo nano /etc/systemd/system/rclone-gdrive.service
   ```
   ```ini
   [Unit]
   Description=RClone Mount Service (Optimized for RAM)
   Wants=network-online.target
   After=network-online.target

   [Service]
   Type=notify
   Environment=RCLONE_CONFIG=/home/ubuntu/.config/rclone/rclone.conf
   KillMode=none
   RestartSec=5
   ExecStart=/usr/bin/rclone mount gdrive: /mnt/gdrive \
     --allow-other \
     --vfs-cache-mode full \
     --cache-dir /tmp/rclone-cache \
     --vfs-cache-max-size 8G \
     --vfs-cache-max-age 2h \
     --buffer-size 256M \
     --vfs-read-ahead 512M \
     --vfs-read-chunk-size 64M \
     --vfs-read-chunk-size-limit 2G \
     --async-read=true \
     --dir-cache-time 1000h \
     --poll-interval 15s \
     --log-level INFO \
     --log-file /var/log/rclone.log \
     --rc \
     --rc-addr 0.0.0.0:5572 \
     --rc-no-auth
   ExecStop=/bin/fusermount -uz /mnt/gdrive
   Restart=on-failure
   User=root
   Group=root

   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now rclone-gdrive
   ```

3. **(Optional) Configure Caddy for Web Server:**
   The bot hosts a beautiful internal web server on port `8080` for movie metadata. To make this public, add this to your host's `/etc/caddy/Caddyfile`:
   ```caddyfile
   movie.yourdomain.com {
       reverse_proxy 127.0.0.1:8080
   }
   ```
   Then run `sudo systemctl reload caddy`.

4. **Build and Start Bot:**
   ```bash
   docker compose up -d --build
   ```

> [!TIP]
> Get your Telegram user ID by messaging [@userinfobot](https://t.me/userinfobot).

## Commands

| Command | Access | Description |
|---------|--------|-------------|
| `/start` | Everyone | Show status and available commands |
| `/status` | Authorized | View a live-updating global dashboard of all active downloads/uploads |
| `/movie <url>` | Authorized | Download a movie (creates folder & uploads to Rclone) |
| `/episode <url>` | Authorized | Download & auto-route an episode to existing Series |
| `/series <url>` | Authorized | Download, extract, process, and upload a full series |
| `/song` | Authorized | Reply to an audio file to download, auto-tag (beets), and upload to Songs |
| `/mbmovie <search>` | Authorized | Search, download, & upload a movie via Moviebox |
| `/mbseries <search>` | Authorized | Search, download, & upload a series via Moviebox |
| `/throttle <1-20>` | Authorized | Set your personal download blocks (1 = Direct) |
| `/refresh` | Authorized | Force a Jellyfin & Rclone VFS mount refresh manually |
| `/cancel` | Authorized | Cancel your active process |
| `/adduser <id>` | Owner | Grant access to a user |
| `/remuser <id>` | Owner | Revoke user access |

### BotFather Command List

Copy-paste this into [@BotFather](https://t.me/BotFather) → `/setcommands`:

```text
start - Show status and available commands
status - View all active bot downloads/uploads
movie - Download a movie from a direct link
episode - Download and auto-route an episode
series - Download, extract, process, and organize a series
song - Download and auto-tag a song via beets
mbmovie - Search and download a movie via Moviebox
mbseries - Search and download a series via Moviebox
refresh - Manually update Jellyfin media library
throttle - Set personal download blocks (1-20)
cancel - Cancel the active download
adduser - Grant access to a user
remuser - Revoke user access
```

## Usage Examples

**Download a movie:**
```text
/movie https://example.com/Inception.2010.1080p.mkv
```

**Search via Moviebox:**
```text
/mbmovie Interstellar
/mbseries The Office
```

**Download Multi-Part Series Archives:**
```text
/series https://example.com/part1.rar
https://example.com/part2.rar
https://example.com/part3.rar
```

**View Live Queue Dashboard:**
```text
/status
```

**Set personal download speed:**
```text
/throttle 10
/throttle 1  (Direct download - no merging phase)
```

> [!NOTE]
> The `/throttle` setting applies to all direct-link downloads. Downloads via `mbmovie`/`mbseries` are hardcoded to **5 blocks** for server stability.

## Changelog

### v5.0 - Kurigram Migration & Refactor
#### 1. Core Framework Migration
* **Dependency Swap**: Removed `hydrogram` from `requirements.txt` and installed `kurigram`. 
* **Namespace Refactor**: Since `kurigram` is a direct, maintained fork of `pyrogram`, a global replacement was run across the entire `bot/` directory replacing `from hydrogram` with `from pyrogram`.
* **Asyncio Loop Fix**: Modified `media_bot.py`'s entry point. Using `asyncio.run(main())` in Python 3.10+ creates a brand new event loop, causing globally instantiated MTProto clients to silently drop updates. Rewrote to use `asyncio.get_event_loop().run_until_complete(main())`.
* **ParseMode Strictness**: Pyrogram v2 enforces strict enums for parse modes. Replaced all raw string occurrences of `parse_mode="Markdown"` or `"HTML"` with `from pyrogram.enums import ParseMode` and `ParseMode.MARKDOWN` / `ParseMode.HTML`.
* **Command Filter Fix**: Replaced `~filters.command` in `message_handler.py` (which throws a `TypeError` in Pyrogram forks) with `~filters.regex(r"^/")` to accurately ignore commands during file interception.

#### 2. Colored Buttons Implementation (Bot API 9.4+)
* **Style Injection**: Globally injected the `style` parameter into `InlineKeyboardButton` instantiations across `moviebox.py` and `dd_callbacks.py`.
  * Success/Confirm/Download buttons → `style=pyrogram.enums.ButtonStyle.SUCCESS` (Green)
  * Navigation/Dubs/Episodes buttons → `style=pyrogram.enums.ButtonStyle.PRIMARY` (Blue)
  * Cancel/Skip buttons → `style=pyrogram.enums.ButtonStyle.DANGER` (Red)
* **Syntax Error Fixes**: Corrected nested regex parsing bugs that accidentally damaged f-strings (e.g. `f"mbp_{cache.get('search_id')}"`) during the style injection.
* **Import Patches**: Injected `import pyrogram` at the top of `moviebox.py` and `dd_callbacks.py` to ensure `pyrogram.enums` could be resolved by the Python interpreter.

#### 3. Persistent Sessions (FloodWait Fix)
* **The Problem**: The bot was creating and storing its MTProto `.session` file directly in the `/app` root directory. Because Docker's `/app` wasn't mounted as a persistent volume, the session file was wiped every time `docker compose build` was run, causing the bot to request a brand new auth key from Telegram on every reboot. This rapidly triggered a 533-second `FloodWait` ban.
* **The Fix**: Modified `bot/clients.py` to instantiate `bot_app = Client("media_bot", workdir="data", ...)`. The session file is now safely stored inside the persistent `./data/` directory mounted on the host machine. You can now rebuild the container infinite times without getting rate-limited.

#### 4. Backend Logic & Silent Crash Fixes
* **PTB Legacy Cleanup**: Removed leftover `context` parameters from helper functions like `_build_search_keyboard` which were triggering `NameError` exceptions.
* **The `qtask.chat_id` Bug**: A global regex meant to convert Telegram's `message.chat_id` into Pyrogram's `message.chat.id` accidentally corrupted the backend queue's `GlobalTask` class. It converted `qtask.chat_id` into `qtask.chat.id`, causing silent `AttributeError` crashes in the background `_start_download` asyncio tasks. Manually reverted these specific instances across `moviebox.py`, `dd_callbacks.py`, `organizer.py`, and `downloader.py`.
* **The `get_bot()` Bug**: Pyrogram v2 drops support for `message.get_bot()`. Replaced all occurrences of `query.get_bot()` and `message.get_bot()` with the `query._client` and `message._client` instances so the bot can successfully send the Web Dashboard tracking link to the user's DMs.

#### 5. Deployment
* **Docker Flow**: Because `/app` is copied (not mounted) in `docker-compose.yml`, all Python codebase modifications require a hard rebuild via `docker compose up -d --build`. This process is now completely safe due to the persistent session fix.

### v4.x and below
* **Optimization**: **IPVanish SOCKS5 VPN (Oracle DPI Bypass)**: Shattered Oracle Cloud's aggressive Layer-7 traffic shaping on Google Drive connections. Configured a local IPVanish SOCKS5 proxy (Port `1080`) and dynamically injected `HTTP_PROXY` into the `uploader.py` Python subprocess. This encapsulates all `rclone` upload traffic through IPVanish's Datacenter backbone, completely bypassing Oracle's 3 MB/s QoS decay without breaking the host's Web Dashboard.
* **Fix**: **MovieBox Original Audio Crash**: Fixed a bug where selecting "Original Audio" during `/mbmovie` or `/mbseries` downloads would pass an invalid language flag to the underlying CLI, silently crashing the process and causing a "session expired" error. The bot now natively handles original audio fallback.
* **Documentation**: **Service Account Quota Optimization**: Outlined a deployment strategy to use Google Service Accounts for the host `rclone mount` (granting 10TB/day of download bandwidth for Jellyfin) while preserving personal Google Account OAuth for the bot's 750GB/day upload pipeline. Also optimized `--poll-interval 24h` to dramatically reduce API limits.
* **Fix**: **MovieBox Smart Routing & Deep Duplicate Detection**: Re-engineered `/mbmovie` and `/mbseries` to natively hook into the host's existing Rclone VFS mount. The bot now automatically detects existing clean media folders (e.g., `Movie (2026)`) via fuzzy-matching and smartly drops new qualities or dubs into the *same exact parent folder* instead of fragmenting your library. It also features a "deep scan" duplicate preventer that steps inside existing folders to check individual media files for matching qualities and dubs before downloading!
* **UX Overhaul**: **Fallback Interactive Menu**: If the bot is blocked by a CDN during the initial remote probe of a Direct Download link, it will completely skip the redundant "Web Optimize" pre-download menu. Instead, it will silently start downloading immediately and **pause** *after* the download finishes. It then performs a local probe and presents a beautiful **combined** menu asking for both Audio Track Selection and MKV Web Optimization simultaneously before proceeding!
* **Feature**: **Jellyfin Auto-Merger**: Replaced external merging tools with a custom-built, fully automated Python script (`auto_merger.py`) injected directly into the Jellyfin refresh workflow. It now silently detects and merges duplicate TV Shows AND Movies (via TMDB/IMDB matching) 60 seconds after every successful download!
* **Feature**: **Smart Cloud Duplicate Prevention**: The bot now directly reads the host's Rclone VFS mount to instantly detect duplicate movie, series, and episode downloads. It features a Smart Quality Extractor that reads `HTTP HEAD` filenames to block identical duplicates before the download even begins. Added `IS_DUPLICATE_ALLOWED` toggle to bypass.
* **Feature**: **Multi-Part Archive Engine**: Rebuilt the `/series` direct-download router to natively accept batches of URLs in a single message.
* **Feature**: **Sequential Archive Extraction**: Introduced `ARCHIVE_EXTRACTION_LIMIT_GB` (default 15GB). For massive series archives, the bot now scans the archive's internal table of contents (`7z l -slt`) and intelligently streams extraction one episode at a time—extracting, processing, uploading, and deleting the local file *before* moving to the next. This completely bypasses the need for massive VPS disk space.
* **Feature**: **HTTP Probing & Smart Routing**: The bot now secretly fires lightweight `HTTP HEAD` requests to extract the `Content-Disposition` filenames of generic Cloudflare/Drive links before downloading a single byte. If it detects a True Split Volume (e.g. `.part1.rar`), it routes to a parallel chunk-downloader to prevent `7z` header crashes. If it detects independent archives (e.g. `Season1.zip`), it routes to a sequential download-extract-delete pipeline to aggressively save VPS disk space.
* **Fix**: **Nested Archives ("Onion Peeling")**: Fixed nested archive extraction logic. If a downloaded archive contains another massive archive inside it instead of video files, the bot now recursively peels the inner archive out and immediately deletes the parent to prevent disk space doubling.
* **Feature**: **Ultra-Premium Web Dashboard**: Completely overhauled the internal web server to include a real-time `/dashboard` route. This dashboard features a stunning animated Aurora/Gemini gradient background, glassmorphism UI cards, real-time `fetch()` polling (busting Cloudflare cache), and dynamically displays "Waiting for User Input" states so tasks never silently disappear while awaiting Telegram interaction!
* **Fix**: **URL Generation & Routing**: Hardened the internal web server URL generation (`get_base_url()`) to dynamically handle trailing slashes and intelligent local port appending, ensuring `/info` and `/dashboard` links never break regardless of local IP or Nginx reverse proxy configurations.
* **Fix**: **Concurrency State Isolation**: Patched a critical bug in `prompt_series_download_options` where internal background tasks (like `7z` archive extraction) were accidentally acquiring duplicate concurrency slots under an "Unknown" ghost user, instantly exhausting the `MAX_CONCURRENT_TASKS` limit.
* **Feature**: **Custom SVG Branding**: Hand-coded a gorgeous 512x512 scalable vector graphic (SVG) logo directly into the Python backend, served dynamically as both the web UI header logo and the browser `favicon.ico`.
* **Feature**: **Global Concurrency Queue & Dual Dashboards**: Heavy tasks (downloads/uploads) are now globally queued based on `MAX_CONCURRENT_TASKS`. Queued tasks appear on live-updating, paginated dual dashboards that automatically spawn in both your Private DM and the Group Chat simultaneously. When your queued task starts, the bot sends you a direct message!
* **Feature**: **Tiered Access Control**: Implemented strict authorization walls. Group members can only use commands inside the designated `GLOBAL_DASHBOARD_GROUPS` and are blocked from DMing the bot commands directly. Users must first initialize the bot in DMs before interacting in groups to ensure direct-message delivery for Index/Cloud links!
* **Feature**: **Massive UX Overhaul** for Moviebox Search! Integrated interactive paginated episode grids, native LinkPreview posters, and a friction-less "Best Quality" fast-track.
* **Feature**: Built a fully responsive **Internal Web Server** using `aiohttp` to serve beautiful CSS-styled movie/series Info pages directly from the bot, bypassing Telegraph's strict design limits! Includes an intelligent **Time-To-Live (TTL)** Garbage Collector to prevent memory leaks over time.
* **Feature**: Supercharged Web Server load times by proxying all Moviebox images through **wsrv.nl (Cloudflare CDN)**, heavily compressing high-res posters into WebP format and scaling down cast thumbnails on the fly to save VPS bandwidth.
* **Feature**: Added **Hybrid Text-Sync Workflow**: Users can now view an interactive button grid, type `1-15` directly into the chat, and the bot will instantly parse the message and natively overwrite the `[✅]` toggle UI buttons in real time!
* **Feature**: Added automatic translation for dub language codes (e.g. `ptbr` -> `Portuguese (Brazil)`) and implemented deduplication to prevent the API's duplicate original audio tracks from appearing twice.
* **Feature**: Added session `[❌ Cancel]` buttons to every menu layer to instantly free up RAM and delete the inline menu from the chat.
* **Overhaul**: Completely removed inline progress updates across all phases (Downloading, FFmpeg, Uploading) and replaced them with a single, unified live-updating `/status` dashboard that instantly auto-spawns at the bottom of the chat to eliminate Telegram API bloat and chat clutter.
* **Fix**: Restructured the series download workflow to perform archive extraction *before* presenting the FFmpeg/MKV processing options, eliminating the long wait gap between selecting options and choosing audio tracks.
* **Feature**: Added a live, throttled progress bar to the global `/status` dashboard specifically for `7z` archive extractions (parsing `-bsp1` output) to visually track large series unzipping in real-time.
* **Fix**: Added asynchronous Rclone `vfs/refresh` signal (passing `{"async": True}`) to completely eliminate Python `500` timeout errors when scanning massive Google Drives.
* **Fix**: Added a 10-second OS disk flush delay before Rclone uploads start. This ensures massive 4K files (e.g., 20GB+) are fully written to the disk before Rclone scans them, preventing false-positive instant success exits.
* **Fix**: Ripped out redundant `ProgressTracker` instantiations during the upload phase that were leaving permanent "zombie" Download tasks hanging in the active dashboard queue.
* **Fix**: Implemented a global `CHAT_PENALTIES` state to instantly halt all progress bar updates for a chat if Telegram issues a temporary flood ban.
* **Fix**: Fixed `/cancel` command failing to stop direct-link downloads by correctly tracking background callback tasks in `dd_callbacks.py`.
* **Fix**: Fixed `/mbmovie` and `/mbseries` commands failing to trigger Jellyfin library refresh after successful cloud uploads by moving the refresh call to execute after the upload process completes.
* **Feature**: Optimized Rclone upload speeds by introducing performance flags (`--drive-chunk-size 128M`, `--transfers 4`, `--checkers 8`, `--fast-list`) to the copy command.
* **Fix**: Fixed `/mbmovie` and `/mbseries` dynamic progress bar freezing by parsing raw byte carriage returns (`\r`) from the Moviebox subprocess output instead of waiting for newlines.
* **Fix**: Reprogrammed the global `/status` dashboard to instantly wake from its 10-second sleep interval via an `asyncio.Event` trigger when vital download phase milestones (e.g. 0% or 100%) are reached, ensuring users never see a frozen "Starting download..." screen even on lightning-fast VPS connections.
* **Fix**: **MovieBox Season Folder Routing**: Fixed a bug where `/mbseries` would ignore existing fuzzy-matched season folder names (e.g., `Season 01`) and hardcode structural folders like `Season 1`, fragmenting the rclone mount. It now correctly fuzzy-matches internal season folders against the cloud mount before uploading.
* **Fix**: **Parallel Series Extraction Isolation**: Fixed a critical cross-contamination bug where running multiple `/series` tasks simultaneously for the same series would cause them to extract into the same `extracted/` folder. This caused FFmpeg tasks to steal each other's files, resulting in corrupted queue numbers (e.g., `6/17`) and `FileNotFoundError` crashes. Extractions are now sandboxed using unique UUIDs.
* **Fix**: **Concurrent UI Task State Collision**: Fixed a bug where interacting with the "Audio Selection" and "Web Optimize MKV" inline keyboards for concurrent direct downloads would apply the user's choice to the wrong files. Interactive bot states are now strictly isolated using unique Task IDs embedded directly into the inline callback strings, rather than a globally mutable user state.
* **Fix**: **Parallel Rclone Upload Collision**: Fixed a critical bug where concurrent `/series` tasks for the same show would organize and upload from the same global series directory. This caused `rclone` to scan the combined size of both seasons, and when the first task finished, it would inadvertently delete the entire series directory before the second task could finish uploading its files. Series files are now organized within their sandboxed extraction directory and uploaded directly to the cloud.
* **Fix**: **Auto-Merger Concurrency Debouncer**: Fixed a race condition where multiple concurrent series downloads would independently trigger the `auto_merger.py` script 60 seconds after finishing. This caused the merger to run prematurely before Jellyfin could finish scanning large batches of files from slower downloads. A global debouncer was implemented to ensure the auto-merger only runs exactly once, 60 seconds after the *very last* download finishes.
