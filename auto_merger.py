import json
import os
import re
import urllib.parse
import urllib.request
from collections import defaultdict
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v
JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://localhost:8096").rstrip('/')
API_KEY = os.getenv("JELLYFIN_API_KEY")

if not API_KEY:
    print("❌ ERROR: JELLYFIN_API_KEY is not set in your .env file!")
    exit(1)

HEADERS = {
    'Authorization': f'MediaBrowser Token="{API_KEY}", Client="AutoMerger", Device="Script", DeviceId="AutoMerger123", Version="2.0"',
    'Content-Type': 'application/json'
}

def _get_items(params):
    try:
        query = urllib.parse.urlencode(params)
        url = f"{JELLYFIN_URL}/Items?{query}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get('Items', [])
    except Exception as e:
        print(f"Error fetching items: {e}")
        return []

def get_all_series():
    return _get_items({
        'Recursive': 'true',
        'IncludeItemTypes': 'Series',
        'Fields': 'Id,Name'
    })

def get_episodes(series_id):
    return _get_items({
        'Recursive': 'true',
        'ParentId': series_id,
        'IncludeItemTypes': 'Episode',
        'Fields': 'ParentIndexNumber,IndexNumber,Path,Name,MediaSources'
    })

def get_all_movies():
    return _get_items({
        'Recursive': 'true',
        'IncludeItemTypes': 'Movie',
        'Fields': 'ProviderIds,ProductionYear,Path,Name,MediaSources'
    })

def extract_episode_info(item):
    s = item.get('ParentIndexNumber')
    e = item.get('IndexNumber')
    if s is None or e is None:
        path = item.get('Path', '')
        match = re.search(r'[sS](\d+)[eE](\d+)', path)
        if match:
            s = int(match.group(1))
            e = int(match.group(2))
    return s, e

def normalize_path(path):
    return path.replace('\\', '/').strip() if path else ""

def merge_ids(ids):
    query = urllib.parse.urlencode({'Ids': ",".join(ids)})
    endpoint = f"{JELLYFIN_URL}/Videos/MergeVersions?{query}"
    try:
        req = urllib.request.Request(endpoint, headers=HEADERS, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        print(f"Error merging {ids}: {e}")
        return False

def check_already_merged(items):
    detected_paths = {normalize_path(i.get('Path')) for i in items if i.get('Path')}
    for item in items:
        sources = item.get('MediaSources', [])
        if len(sources) > 1:
            known_paths = {normalize_path(src.get('Path')) for src in sources if src.get('Path')}
            if detected_paths.issubset(known_paths):
                return True
    return False

def run_auto_merger():
    total_merged = 0

    print("\n📺 Starting Auto-Merger scan for TV SHOWS...")
    series_list = get_all_series()

    for series in series_list:
        series_id = series['Id']
        series_name = series['Name']
        episodes = get_episodes(series_id)
        
        grouped = defaultdict(list)
        for ep in episodes:
            s, e = extract_episode_info(ep)
            if s is not None and e is not None:
                grouped[f"S{s:02d}E{e:02d}"].append(ep)

        for ep_key, items in grouped.items():
            if len(items) < 2 or check_already_merged(items):
                continue

            ids_to_merge = [i['Id'] for i in items]
            print(f"🔄 Merging {series_name} - {ep_key} ({len(ids_to_merge)} versions)...")
            
            if merge_ids(ids_to_merge):
                total_merged += 1
                print("   ✅ Success")
            else:
                print("   ❌ Failed")

    print(f"\n🎉 Auto-Merger complete! Merged {total_merged} total groups of media.")

if __name__ == "__main__":
    run_auto_merger()
