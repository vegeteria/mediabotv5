# 🚀 Entire Workflow Setup Guide

This guide will walk you through setting up the complete media server pipeline from scratch, including Google Drive integration, Rclone setup, Cloudflare Index workers, and Bot configuration.

---

## 1. Getting Telegram Bot Tokens

To communicate with the bot, you need a Bot Token and your personal User ID.

1. **Create the Bot:**
   * Open Telegram and search for [@BotFather](https://t.me/BotFather).
   * Send `/newbot`, choose a name, and choose a username (must end in `bot`).
   * BotFather will give you a **Bot Token** (e.g., `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`). Save this.

2. **Get your User ID (Owner ID):**
   * Search for [@userinfobot](https://t.me/userinfobot) on Telegram and send `/start`.
   * It will reply with your ID (e.g., `Id: 123456789`). Save this number.

---

## 2. Setting Up Rclone (Google Drive)

Rclone is used to automatically upload your downloaded media to Google Drive and mount the drive to your host machine for Jellyfin.

1. **Install Rclone on your VPS:**
   ```bash
   sudo -v ; curl https://rclone.org/install.sh | sudo bash
   ```

2. **Configure Google Drive:**
   ```bash
   rclone config
   ```
   * Press `n` for New remote.
   * Name it: `gdrive` (if you choose a different name, remember it).
   * Storage type: type `drive` for Google Drive.
   * `client_id` / `client_secret`: Leave blank (press enter) unless you have your own GCP credentials.
   * Scope: choose `1` (Full access).
   * `root_folder_id` / `service_account_file`: Leave blank.
   * Advanced config? `n`.
   * Use auto config? `n` (since you are on a headless VPS).
   * Rclone will give you a command to run on your local PC (e.g., `rclone authorize "drive"`). 
   * Run that command on your local Windows/Mac terminal, log in to Google, and it will output a long token code. Paste that code back into your VPS.
   * Configure this as a Shared Drive (Team Drive)? Answer `y` or `n` depending on your setup.
   * Save the config.

---

## 3. Setting Up Rclone Mount on Host

To allow Jellyfin to scan your Google Drive media instantly, you need to mount it to your VPS file system using Rclone's FUSE mount.

1. **Create the mount directory:**
   ```bash
   sudo mkdir -p /mnt/gdrive
   sudo chown ubuntu:ubuntu /mnt/gdrive
   ```

2. **Create a Systemd Service for Rclone:**
   We use a systemd service optimized for RAM (using `/tmp`) for faster disk speed. We also add `--rc --rc-no-auth` so the Telegram bot can instantly tell the mount to refresh its cache after a download finishes.

   Create the service file:
   ```bash
   sudo nano /etc/systemd/system/rclone-gdrive.service
   ```

   Paste the following configuration:
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

   **Split-Server Architecture Note:** If your Jellyfin/Rclone mount is running on a *different* server than the bot, exposing `--rc-addr 0.0.0.0:5572` is required so the bot can trigger immediate Jellyfin refreshes. You must also ensure your Cloud provider's firewall allows TCP traffic on port 5572. For Oracle Cloud, bypass default iptables blocks with:
   `sudo iptables -I INPUT 1 -p tcp --dport 5572 -j ACCEPT`

3. **Enable and Start the Service:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable rclone-gdrive
   sudo systemctl start rclone-gdrive
   ```

---

## 4. Setting up Google Drive Index (Cloudflare Worker)

To generate direct browser viewing links for your uploaded media, you need a Cloudflare worker connected to your Google Drive.

1. Fork or clone [Google-Drive-Index](https://gitlab.com/GoogleDriveIndex/Google-Drive-Index).
2. Follow their documentation to deploy the worker to your Cloudflare account.
3. Once deployed, Cloudflare will give you a worker URL (e.g., `https://index.yourname.workers.dev`).
4. This URL becomes your `INDEX_URL` and `CLOUD_LINK_BASE` for the bot!

---

## 5. Setting up `.env` Variables

Now that everything is ready, create your environment configuration for the bot.

1. Navigate to the bot folder:
   ```bash
   cd ~/mediabotv3
   cp .env.example .env
   nano .env
   ```

2. Fill in the variables using the data you collected:
   ```env
   # Telegram Config
   BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
   OWNER_ID=123456789
   
   # Queue & Auth Settings
   GLOBAL_DASHBOARD_GROUPS=-100123456789,-100987654321  # Comma-separated Group IDs where normal users can use the bot
   MAX_CONCURRENT_TASKS=3  # Limit the max number of heavy active downloads/uploads at a time
   
   # Bot Preferences
   PROGRESS_UPDATE_DELAY=10.0   # Wait 10 seconds between API dashboard updates to prevent rate limits
   ARCHIVE_EXTRACTION_LIMIT_GB=15 # Files above this size will extract sequentially episode-by-episode
   
   # Jellyfin Config (Generate an API key from Jellyfin Dashboard -> Advanced -> API Keys)
   JELLYFIN_API_KEY=your_jellyfin_api_key
   JELLYFIN_URL=http://localhost:8096  # Needed for Auto-Merger to group duplicates
   
   # Rclone Upload Config
   RCLONE_REMOTE=gdrive
   RCLONE_BASE_DIR=            # Leave blank to upload directly to root, or type a folder name
   RCLONE_MOUNT_DIR=/mnt/gdrive # Mount path for fuzzy duplicate detection
   RCLONE_RC_URL=http://localhost:5572 # Use remote server IP (http://remote_ip:5572) if Rclone is hosted elsewhere
   IS_DUPLICATE_ALLOWED=False   # Set to True to disable duplicate download blocking
   
   # Indexing URLs
   INDEX_URL=https://index.yourname.workers.dev
   CLOUD_LINK_BASE=https://index.yourname.workers.dev
   
   # Web Server (Optional)
   WEB_SERVER_URL=http://127.0.0.1
   WEB_SERVER_PORT=8000
   
   # MTProto API Credentials (for downloading 2GB+ files)
   API_ID=your_api_id
   API_HASH=your_api_hash
   USER_SESSION_STRING=your_pyrogram_session_string
   ```

## 6. (Optional) Setting up Caddy for the Web Server UI

The bot hosts a premium internal web server on port `8000` (configurable via `WEB_SERVER_PORT`) featuring a real-time live download `/dashboard` and beautiful movie/series info pages. To make this public using a clean domain and free SSL:

1. Point your domain (e.g. `movie.yourdomain.com`) to your VPS IP via DNS (A Record).
2. Install Caddy on your VPS.
3. Open `/etc/caddy/Caddyfile` and add:
   ```caddyfile
   movie.yourdomain.com {
       reverse_proxy 127.0.0.1:8000
   }
   ```
4. Reload Caddy: `sudo systemctl reload caddy`.
5. Add `WEB_SERVER_URL=https://movie.yourdomain.com` to your `.env` file!

---

## 7. Run the Bot via Docker

With the `.env` configured and your Rclone mount running on the host, you are ready to start the bot.

```bash
docker compose up -d --build
```

**Note on Series Processing Workflow:** When downloading a series archive, the bot will first **extract the archive** (showing a live 7z progress bar in the `/status` queue). Once extracted, it will prompt you with processing options (FFmpeg Audio/MKV Faststart), followed immediately by audio track selection if requested. This ensures no waiting gap between processing choices!

**Workflow Complete! 🎉**
You can now go to Telegram, send `/start` to your bot, and begin downloading!
