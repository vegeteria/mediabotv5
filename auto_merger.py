import os
import re
import requests
from collections import defaultdict
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()
JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://localhost:8096").rstrip('/')
API_KEY = os.getenv("JELLYFIN_API_KEY")

if not API_KEY:
    print("❌ ERROR: JELLYFIN_API_KEY is not set in your .env file!")
    exit(1)

HEADERS = {
    'X-Emby-Token': API_KEY,
    'Authorization': f'MediaBrowser Client="AutoMerger", Device="Script", Version="2.0", Token="{API_KEY}"',
    'Content-Type': 'application/json'
}

def _get_items(params):
    try:
        r = requests.get(f"{JELLYFIN_URL}/Items", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json().get('Items', [])
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
    endpoint = f"{JELLYFIN_URL}/Videos/MergeVersions"
    try:
        r = requests.post(endpoint, headers=HEADERS, params={'Ids': ",".join(ids)})
        r.raise_for_status()
        return True
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
