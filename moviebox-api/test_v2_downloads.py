import asyncio
from moviebox_api.v2.requests import Session
from moviebox_api.v2.core import Search
from moviebox_api.v2.download import DownloadableSingleFilesDetail

async def main():
    session = Session()
    try:
        search = Search(session, query="Avatar")
        res = await search.get_content_model()
        item = [i for i in res.items if "Avatar" in i.title][0]
        print(f"Detail Path: {item.detailPath}")
        
        dl = DownloadableSingleFilesDetail(session, item)
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
