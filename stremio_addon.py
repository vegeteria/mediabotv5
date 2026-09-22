import asyncio
import base64
import json
import logging
from aiohttp import web
import httpx
import aiohttp_cors
from urllib.parse import urlparse
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mb_port = os.environ.get("MB_PORT", "8000")
MB_SERVER = os.environ.get("MB_SERVER", f"http://localhost:{mb_port}")

async def fetch_cinemeta(type_, id_):
    async with httpx.AsyncClient() as client:
        res = await client.get(f"https://v3-cinemeta.strem.io/meta/{type_}/{id_}.json")
        if res.status_code == 200:
            return res.json().get("meta")
    return None

def find_moviebox_item(data, target_title, target_year, target_type):
    found = []
    def search_obj(obj):
        if isinstance(obj, dict):
            if "title" in obj and "subjectId" in obj:
                title = obj.get("title", "")
                year = str(obj.get("releaseDate", ""))[:4]
                
                if target_type == "series":
                    if title.lower().startswith(target_title.lower()) and str(year) == str(target_year):
                        found.append(obj)
                else:
                    if title.lower() == target_title.lower() and str(year) == str(target_year):
                        found.append(obj)
            for v in obj.values():
                search_obj(v)
        elif isinstance(obj, list):
            for v in obj:
                search_obj(v)
    search_obj(data)
    
    # Deduplicate by subjectId
    unique = []
    seen = set()
    for item in found:
        if item["subjectId"] not in seen:
            seen.add(item["subjectId"])
            unique.append(item)
    return unique

async def manifest(request):
    return web.json_response({
        "id": "com.moviebox.addon",
        "version": "1.0.0",
        "name": "MovieBox",
        "description": "Stream movies and series from MovieBox",
        "resources": ["stream"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": []
    })

async def stream(request):
    type_ = request.match_info['type']
    id_ = request.match_info['id']
    
    parts = id_.split(":")
    imdb_id = parts[0]
    season = int(parts[1]) if len(parts) > 1 else 0
    episode = int(parts[2]) if len(parts) > 2 else 0
    
    logger.info(f"Stremio requested {type_} {id_}")
    
    meta = await fetch_cinemeta(type_, imdb_id)
    if not meta:
        return web.json_response({"streams": []})
        
    title = meta.get("name")
    year = meta.get("year")
    if not title:
        return web.json_response({"streams": []})
        
    if type_ == "series":
        year = str(year)[:4]
        
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{MB_SERVER}/search?q={title}")
        if res.status_code != 200:
            return web.json_response({"streams": []})
            
        data = res.json().get("data", {})
        mb_items = find_moviebox_item(data, title, year, type_)
        
        if not mb_items:
            return web.json_response({"streams": []})
            
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        host = request.headers.get("X-Forwarded-Host", request.host)
        proxy_base_url = f"{scheme}://{host}"
        
        streams = []
        for mb_item in mb_items:
            mb_id = mb_item["subjectId"]
            item_title = mb_item.get("title", "MovieBox")
            
            s_res = await client.get(f"{MB_SERVER}/stream?id={mb_id}&season={season}&episode={episode}")
            if s_res.status_code != 200:
                continue
                
            s_data = s_res.json().get("data", [])
            for s in s_data:
                url = s["mirrors"][0]["resolver_url"]
                headers = s["mirrors"][0]["headers"]
                
                proxy_data = {
                    "u": url,
                    "h": {k: v for k, v in headers}
                }
                encoded_data = base64.urlsafe_b64encode(json.dumps(proxy_data).encode()).decode()
                
                ext = ".mpd" if "dash" in url or ".mpd" in url else ".mp4"
                proxy_url = f"{proxy_base_url}/proxy/{encoded_data}/stream{ext}"
                
                # If there are dub tags like [Hindi], include them in the stream name
                disp_name = "MovieBox"
                import re
                tags = re.findall(r'\[(.*?)\]', item_title)
                if tags:
                    disp_name = f"MovieBox ({', '.join(tags)})"
                
                streams.append({
                    "name": disp_name,
                    "title": f"{s.get('resolution', 'Unknown')} - {round(s.get('size_bytes', 0)/1024/1024, 1)} MB",
                    "url": proxy_url
                })
            
        return web.json_response({"streams": streams})

PREFETCH_CACHE = {}

async def prefetch_chunks(session, current_url, headers):
    import re
    match = re.search(r'(\d+)\.m4s$', current_url)
    if not match:
        return
        
    current_num_str = match.group(1)
    current_num = int(current_num_str)
    
    tasks = []
    # Prefetch next 8 chunks concurrently
    for i in range(current_num + 1, current_num + 9):
        next_num_str = str(i).zfill(len(current_num_str))
        next_url = current_url[:match.start(1)] + next_num_str + ".m4s"
        
        if next_url not in PREFETCH_CACHE:
            PREFETCH_CACHE[next_url] = asyncio.Future()
            tasks.append(fetch_chunk(session, next_url, headers))
            
    if tasks:
        asyncio.create_task(asyncio.gather(*tasks, return_exceptions=True))

async def fetch_chunk(session, url, headers):
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.read()
                future = PREFETCH_CACHE.get(url)
                if isinstance(future, asyncio.Future) and not future.done():
                    future.set_result((dict(resp.headers), data))
                else:
                    PREFETCH_CACHE[url] = (dict(resp.headers), data)
            else:
                PREFETCH_CACHE.pop(url, None)
    except Exception as e:
        future = PREFETCH_CACHE.get(url)
        if isinstance(future, asyncio.Future) and not future.done():
            future.set_exception(e)
        PREFETCH_CACHE.pop(url, None)

    # Clean up old cache to prevent memory leaks (keep ~50 items = ~100MB)
    if len(PREFETCH_CACHE) > 100:
        keys = list(PREFETCH_CACHE.keys())[:-50]
        for k in keys:
            PREFETCH_CACHE.pop(k, None)

async def proxy(request):
    encoded_data = request.match_info['data']
    path = request.match_info.get('path', '')
    
    try:
        proxy_data = json.loads(base64.urlsafe_b64decode(encoded_data).decode())
    except:
        return web.Response(status=400, text="Invalid proxy data")
        
    target_url = proxy_data["u"]
    target_headers = proxy_data.get("h", {})
    
    if path and path not in ("stream.mpd", "stream.mp4"):
        from urllib.parse import urljoin
        target_url = urljoin(target_url, path)
    
    if "Range" in request.headers:
        target_headers["Range"] = request.headers["Range"]

    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    proxy_base_url = f"{scheme}://{host}"
    
    session = request.app['client']
    
    # Handle Cache Hit
    if target_url in PREFETCH_CACHE:
        cached = PREFETCH_CACHE[target_url]
        try:
            if isinstance(cached, asyncio.Future):
                resp_headers, data = await cached
            else:
                resp_headers, data = cached
                
            resp_headers.pop("Transfer-Encoding", None)
            resp_headers.pop("Content-Encoding", None)
            resp_headers["Access-Control-Allow-Origin"] = "*"
            
            asyncio.create_task(prefetch_chunks(session, target_url, target_headers))
            return web.Response(status=200, headers=resp_headers, body=data)
        except Exception:
            pass # fallback to direct fetch
            
    # Trigger prefetch for upcoming chunks
    if target_url.endswith(".m4s"):
        asyncio.create_task(prefetch_chunks(session, target_url, target_headers))
        
    async with session.get(target_url, headers=target_headers) as resp:
        headers = dict(resp.headers)
        headers.pop("Transfer-Encoding", None)
        headers.pop("Content-Encoding", None)
        headers["Access-Control-Allow-Origin"] = "*"
        
        if target_url.endswith(".mpd") or "application/dash+xml" in headers.get("Content-Type", ""):
            body = await resp.text()
            parsed_url = urlparse(target_url)
            target_host = parsed_url.netloc
            
            https_prefix = f"https://{target_host}/"
            http_prefix = f"http://{target_host}/"
            
            proxy_https = f"{proxy_base_url}/chunk/{encoded_data}/https/{target_host}/"
            proxy_http = f"{proxy_base_url}/chunk/{encoded_data}/http/{target_host}/"
            
            rewritten = body.replace(https_prefix, proxy_https).replace(http_prefix, proxy_http)
            headers["Content-Length"] = str(len(rewritten))
            return web.Response(body=rewritten, headers=headers, status=resp.status)
            
        response = web.StreamResponse(status=resp.status, headers=headers)
        await response.prepare(request)
        async for chunk in resp.content.iter_chunked(64 * 1024):
            await response.write(chunk)
        await response.write_eof()
        return response

async def chunk_proxy(request):
    encoded_data = request.match_info['data']
    scheme = request.match_info['scheme']
    host = request.match_info['host']
    path = request.match_info['path']
    
    try:
        proxy_data = json.loads(base64.urlsafe_b64decode(encoded_data).decode())
    except:
        return web.Response(status=400, text="Invalid proxy data")
        
    target_url = f"{scheme}://{host}/{path}"
    target_headers = proxy_data.get("h", {})
    
    if "Range" in request.headers:
        target_headers["Range"] = request.headers["Range"]
        
    session = request.app['client']
    
    if target_url in PREFETCH_CACHE:
        cached = PREFETCH_CACHE[target_url]
        try:
            if isinstance(cached, asyncio.Future):
                resp_headers, data = await cached
            else:
                resp_headers, data = cached
                
            resp_headers.pop("Transfer-Encoding", None)
            resp_headers.pop("Content-Encoding", None)
            resp_headers["Access-Control-Allow-Origin"] = "*"
            
            asyncio.create_task(prefetch_chunks(session, target_url, target_headers))
            return web.Response(status=200, headers=resp_headers, body=data)
        except Exception:
            pass
            
    if target_url.endswith(".m4s"):
        asyncio.create_task(prefetch_chunks(session, target_url, target_headers))
        
    async with session.get(target_url, headers=target_headers) as resp:
        headers = dict(resp.headers)
        headers.pop("Transfer-Encoding", None)
        headers.pop("Content-Encoding", None)
        headers["Access-Control-Allow-Origin"] = "*"
        
        response = web.StreamResponse(status=resp.status, headers=headers)
        await response.prepare(request)
        async for chunk in resp.content.iter_chunked(64 * 1024):
            await response.write(chunk)
        await response.write_eof()
        return response

app = web.Application()

async def on_startup(app):
    import aiohttp
    app['client'] = aiohttp.ClientSession()

async def on_cleanup(app):
    await app['client'].close()

app.on_startup.append(on_startup)
app.on_cleanup.append(on_cleanup)

cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
    )
})

cors.add(app.router.add_get('/manifest.json', manifest))
cors.add(app.router.add_get('/stream/{type}/{id}.json', stream))
cors.add(app.router.add_get('/proxy/{data}/{path:.*}', proxy))
# Catch-all route for chunk requests inside the MPD
cors.add(app.router.add_get('/chunk/{data}/{scheme}/{host}/{path:.*}', chunk_proxy))

if __name__ == '__main__':
    port = int(os.environ.get("STREMIO_PORT", 8080))
    web.run_app(app, host='0.0.0.0', port=port)
