import asyncio
import os
import sys

# Load env manually
env_path = ".env"
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k] = v.strip("\"'")

from moviebox_api.v3.core import DownloadableVideoFilesDetail
from moviebox_api.v3.http_client import MovieBoxHttpClient

async def main():
    async with MovieBoxHttpClient() as session:
        subject_id = "6487986440675673128"
        details = DownloadableVideoFilesDetail(session)
        links = await details.get_content(subject_id)
        for item in links.get("list", []):
            print(f"[{item.get('resolution')}p] - {item.get('resourceLink')}")

asyncio.run(main())
