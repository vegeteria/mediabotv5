# MovieBox API Usage Guide

This guide demonstrates how to use the `moviebox_api` package. The package primarily uses `asyncio` and requires asynchronous contexts. The latest stable implementation is `v3`, which utilizes a unified HTTP client (`MovieBoxHttpClient`) that automatically manages API tokens and cryptographic signatures.

## Initialization

Always use the `MovieBoxHttpClient` context manager. This ensures that the essential authentication tokens (like `X-Client-Token` and `x-user`) are automatically fetched and correctly injected into headers behind the scenes.

```python
import asyncio
from moviebox_api.v3.http_client import MovieBoxHttpClient

async def main():
    async with MovieBoxHttpClient() as client:
        # Your API requests go here
        pass

if __name__ == "__main__":
    asyncio.run(main())
```

## Searching for Content

You can use the `SearchV2` class to query the API for movies, TV series, anime, etc. 

```python
import asyncio
from moviebox_api.v3.http_client import MovieBoxHttpClient
from moviebox_api.v3.core import SearchV2
from moviebox_api.v3.constants import SubjectType

async def search_movie():
    async with MovieBoxHttpClient() as client:
        # Initialize search for "Titanic" in Movies
        search = SearchV2(client, "Titanic", subject_type=SubjectType.MOVIES)
        
        # Get the parsed Pydantic model response
        results = await search.get_content_model()
        
        for item in results.list:
            print(f"Title: {item.title}")
            print(f"Subject ID: {item.subject_id}")
            print(f"Rating: {item.imdb_rate}")
            print("-" * 20)

asyncio.run(search_movie())
```

## Fetching the Homepage

You can fetch the main homepage content which includes carousels, categories, and trending lists:

```python
import asyncio
from moviebox_api.v3.http_client import MovieBoxHttpClient
from moviebox_api.v3.core import Homepage

async def get_homepage():
    async with MovieBoxHttpClient() as client:
        homepage = Homepage(client)
        contents = await homepage.get_content_model()
        
        for item in contents.items:
            print(f"Section: {item.title}")
            if item.subjects:
                for subject in item.subjects:
                    print(f" - {subject.title}")

asyncio.run(get_homepage())
```

## Extracting Downloadable Media URLs

To extract the direct video URLs (`.mp4`, `.m3u8`) and captions for a specific movie or episode, use the `DownloadableVideoFilesDetail` class along with the target resolution.

```python
import asyncio
from moviebox_api.v3.http_client import MovieBoxHttpClient
from moviebox_api.v3.core import DownloadableVideoFilesDetail
from moviebox_api.v3.constants import CustomResolutionType
from moviebox_api.v3.download import MediaFileDownloader

async def download_media():
    subject_id = "8906247916759695608" # Example ID for Avatar
    
    async with MovieBoxHttpClient() as client:
        # Request 1080P resolution details
        details = DownloadableVideoFilesDetail(
            client, 
            resolution=CustomResolutionType._1080P
        )
        
        # Fetch available files
        files_detail = await details.get_content_model(subject_id)
        
        if not files_detail.list:
            print("No media files found.")
            return

        # Target the first available media file
        target_media_file = files_detail.list[0]
        print(f"Found media: {target_media_file.quality} - {target_media_file.size}")
        
        # Initialize downloader
        downloader = MediaFileDownloader()
        
        # To just get the direct URL without downloading:
        print(f"Direct stream URL: {target_media_file.path}")

asyncio.run(download_media())
```

## Notes

- **Region Restrictions:** The API enforces geographic blocks. If you use this script outside of the supported regions, you may encounter `403` or `429` errors. The `v3` constants have been updated to spoof Indian IPs/Timezones (`Asia/Kolkata`), which satisfies the basic application-level geographic checks.
- **Tokens and Signatures:** All token mechanisms (`X-Client-Token`, `x-tr-signature`, and `x-user`) are now completely abstracted from the developer and happen instantly during the first request handled by the `MovieBoxHttpClient` or `v1.requests.Session`.

## Command-Line Interface (CLI)
You can absolutely use simple commands like before without writing any Python scripts. 

Ensure your virtual environment is active (or the package is installed globally), and use the `moviebox_api` CLI tool.

Here are some examples of what you can do directly from your terminal:

**1. Fetch the Homepage Content:**
```bash
python -m moviebox_api v3 homepage-content
```

**2. Search for a Movie or Series:**
```bash
# Search for 'Avatar' specifically in Movies
python -m moviebox_api v3 search-content Avatar -s MOVIES

# Search for 'Merlin' in TV Series
python -m moviebox_api v3 search-content Merlin -s TV
```

**3. Get Details for a Specific Item:**
```bash
# Shows detailed info, available episodes, and download options
python -m moviebox_api v3 item-details Avatar -s MOVIES --yes
```

**4. Download a Movie or Series:**
```bash
# Download a movie interactively
python -m moviebox_api v3 download-movie Avatar

# Download Season 1, Episode 1 of a Series
python -m moviebox_api v3 download-series "Game of Thrones" -s 1 -e 1
```

For a full list of commands and options, you can always run:
```bash
python -m moviebox_api v3 --help
```
