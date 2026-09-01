import asyncio
import httpx

url = "https://bcdn.hakunaymatata.com/resource/h265/437bb82ab88e30fc7bb3121383909f78.mp4?sign=7ccf075e1727cdd8c3ba4ba725b2bf1e&t=1784214284"

async def test():
    headers_list = [
        {"User-Agent": "Mozilla/5.0"},
        {"User-Agent": "Mozilla/5.0", "Referer": "https://h5.aoneroom.com/"},
        {"User-Agent": "Mozilla/5.0", "Referer": "https://moviebox.ng/"},
        {"User-Agent": "Mozilla/5.0", "Referer": "https://bcdn.hakunaymatata.com/"},
        {"User-Agent": "moviebox"},
        {}
    ]
    for headers in headers_list:
        async with httpx.AsyncClient() as client:
            resp = await client.head(url, headers=headers)
            print(f"Headers: {headers} -> Status: {resp.status_code}")

asyncio.run(test())
