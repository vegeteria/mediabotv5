import sys
import aiohttp
import asyncio
import subprocess
import argparse
from pathlib import Path
import re

async def download_item(session, item_id, title, target_dir, season=0, episode=0):
    url = f"http://localhost:8000/stream?id={item_id}&season={season}&episode={episode}"
    print(f"Fetching stream for {title} S{season}E{episode}...")
    sys.stdout.flush()
    async with session.get(url) as resp:
        data = await resp.json()
        if not data.get("success") or not data.get("data"):
            print(f"Failed to get stream for {title} S{season}E{episode}")
            return False
        
        best_stream = data["data"][0]
        mirror = best_stream["mirrors"][0]
        download_url = mirror["resolver_url"]
        headers = mirror["headers"]
        
        import os
        concurrent_fragments = os.environ.get("CONCURRENT_FRAGMENTS", "16")
        cmd = [sys.executable, "-m", "yt_dlp", "-N", concurrent_fragments, download_url]
        for k, v in headers:
            cmd.extend(["--add-header", f"{k}: {v}"])
        
        if season > 0:
            out_name = f"{title} S{season:02d}E{episode:02d}.mp4"
            print(f"Downloading {title} S{season:02d}E{episode:02d}")
        else:
            out_name = f"{title}.mp4"
            print(f"Downloading {title}")
            
        out_path = Path(target_dir) / out_name
        cmd.extend(["-o", str(out_path)])
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            m = re.search(r'\[download\]\s+(\d+\.\d+)%\s+of\s+~?([^ ]+)\s+at\s+([^ ]+)\s+ETA\s+([^ ]+)', line)
            if m:
                pct = m.group(1)
                total = m.group(2)
                speed = m.group(3)
                eta = m.group(4)
                done = "Unknown"
                print(f"Downloading {pct}% {done}/{total}, {speed} <{eta}")
            else:
                if "%" in line:
                    print(line.strip())
            sys.stdout.flush()
        proc.wait()
        return proc.returncode == 0

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--season", type=int, default=0)
    parser.add_argument("--episode", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--dir", required=True)
    args, unknown = parser.parse_known_args()

    async with aiohttp.ClientSession() as session:
        for i in range(args.limit):
            ep = args.episode + i if args.season > 0 else 0
            success = await download_item(session, args.id, args.title, args.dir, args.season, ep)
            if not success:
                print("Failed!")
                sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
