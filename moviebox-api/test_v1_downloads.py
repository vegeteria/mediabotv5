import asyncio
from moviebox_api.v1.requests import Session
from moviebox_api.v1.core import Search
from moviebox_api.v1.download import DownloadableMovieFilesDetail

async def main():
    session = Session()
    try:
        search = Search(session, query="Avatar")
        res = await search.get_content_model()
        if not res.items:
            print("No items found.")
            return
        item = res.items[0]
        print(f"Detail Path: {item.detailPath}")
        
        dl = DownloadableMovieFilesDetail(session, item)
        contents = await dl.get_content()
        print("API Response:", contents.keys())
        if 'downloads' in contents:
            print("Downloads length:", len(contents['downloads']))
        else:
            print("No downloads key!")
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
