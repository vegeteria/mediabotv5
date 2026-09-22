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

MB_SERVER = os.environ.get("MB_SERVER", "http://localhost:8000")

async def fetch_cinemeta(type_, id_):
    async with httpx.AsyncClient() as client:
        res = await client.get(f"https://v3-cinemeta.strem.io/meta/{type_}/{id_}.json")
        if res.status_code == 200:
            return res.json().get("meta")
    return None

def find_moviebox_item(data, target_title, target_year):
    found = []
    def search_obj(obj):
        if isinstance(obj, dict):
            if "title" in obj and "subjectId" in obj:
                title = obj.get("title", "")
                year = obj.get("releaseDate", "").split("-")[0]
                if title.lower() == target_title.lower() and str(year) == str(target_year):
                    found.append(obj)
            for v in obj.values():
                search_obj(v)
        elif isinstance(obj, list):
            for v in obj:
                search_obj(v)
    search_obj(data)
    return found[0] if found else None

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
        year = str(year).split("-")[0]
        
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{MB_SERVER}/search?q={title}")
        if res.status_code != 200:
            return web.json_response({"streams": []})
            
        data = res.json().get("data", {})
        mb_item = find_moviebox_item(data, title, year)
        
        if not mb_item:
            return web.json_response({"streams": []})
            
        mb_id = mb_item["subjectId"]
        
        s_res = await client.get(f"{MB_SERVER}/stream?id={mb_id}&season={season}&episode={episode}")
        if s_res.status_code != 200:
            return web.json_response({"streams": []})
            
        s_data = s_res.json().get("data", [])
        streams = []
        
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        host = request.headers.get("X-Forwarded-Host", request.host)
        proxy_base_url = f"{scheme}://{host}"
        
        for s in s_data:
            url = s["mirrors"][0]["resolver_url"]
            headers = s["mirrors"][0]["headers"]
            
            # Encode target info
            proxy_data = {
                "u": url,
                "h": {k: v for k, v in headers}
            }
            encoded_data = base64.urlsafe_b64encode(json.dumps(proxy_data).encode()).decode()
            
            ext = ".mpd" if "dash" in url or ".mpd" in url else ".mp4"
            proxy_url = f"{proxy_base_url}/proxy/{encoded_data}/stream{ext}"
            
            streams.append({
                "name": "MovieBox",
                "title": f"{s.get('resolution', 'Unknown')} - {round(s.get('size_bytes', 0)/1024/1024, 1)} MB",
                "url": proxy_url
            })
            
        return web.json_response({"streams": streams})

async def proxy(request):
    encoded_data = request.match_info['data']
    try:
        proxy_data = json.loads(base64.urlsafe_b64decode(encoded_data).decode())
    except:
        return web.Response(status=400, text="Invalid proxy data")
        
    target_url = proxy_data["u"]
    target_headers = proxy_data.get("h", {})
    
    if "Range" in request.headers:
        target_headers["Range"] = request.headers["Range"]

    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    proxy_base_url = f"{scheme}://{host}"
    
    import aiohttp
    async with aiohttp.ClientSession() as session:
        resp = await session.get(target_url, headers=target_headers)
        
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
            
            # Make sure we route chunks back to the proxy!
            # Since DASH chunks don't have our encoded headers natively, we need to pass the same encoded data block in the path!
            proxy_https = f"{proxy_base_url}/chunk/{encoded_data}/https/{target_host}/"
            proxy_http = f"{proxy_base_url}/chunk/{encoded_data}/http/{target_host}/"
            
            rewritten = body.replace(https_prefix, proxy_https).replace(http_prefix, proxy_http)
            
            headers["Content-Length"] = str(len(rewritten))
            return web.Response(body=rewritten, headers=headers, status=resp.status)
            
        # For MP4s or raw chunks, stream it directly
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
        
    import aiohttp
    async with aiohttp.ClientSession() as session:
        resp = await session.get(target_url, headers=target_headers)
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
cors = aiohttp_cors.setup(app, defaults={
    "*": aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
    )
})

cors.add(app.router.add_get('/manifest.json', manifest))
cors.add(app.router.add_get('/stream/{type}/{id}.json', stream))
cors.add(app.router.add_get('/proxy/{data}/{filename}', proxy))
# Catch-all route for chunk requests inside the MPD
cors.add(app.router.add_get('/chunk/{data}/{scheme}/{host}/{path:.*}', chunk_proxy))

if __name__ == '__main__':
    port = int(os.environ.get("STREMIO_PORT", 8080))
    web.run_app(app, host='0.0.0.0', port=port)
