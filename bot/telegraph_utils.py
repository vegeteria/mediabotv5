import json
import httpx
from moviebox_api.v3.models.details import RootItemDetailsModel

_TELEGRAPH_TOKEN = None

async def _get_token() -> str:
    global _TELEGRAPH_TOKEN
    if _TELEGRAPH_TOKEN:
        return _TELEGRAPH_TOKEN
        
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get("https://api.telegra.ph/createAccount?short_name=MediaBot&author_name=Media+Bot")
            data = res.json()
            if data.get("ok"):
                _TELEGRAPH_TOKEN = data["result"]["access_token"]
                return _TELEGRAPH_TOKEN
    except Exception:
        pass
        
    return "d3b25feccb89e508a9114afb82aa421fe2a9712b963b387cc5ad71e58722" # fallback test token

async def generate_movie_telegraph(details: RootItemDetailsModel) -> str:
    """Generates a Telegraph page for the movie/series with cover, description, and cast."""
    token = await _get_token()
    
    content = []
    
    # 1. Cover image
    if details.cover and details.cover.url:
        content.append({"tag": "img", "attrs": {"src": str(details.cover.url)}})
        
    # 2. Rating & Basic Info
    info_text = f"⭐ IMDb Rating: {details.imdb_rating_value}\n"
    if details.duration:
        info_text += f"⏱ Duration: {details.duration}\n"
    if details.content_rating:
        info_text += f"🔞 Rating: {details.content_rating}\n"
    if details.genre:
        info_text += f"🎭 Genres: {', '.join(details.genre)}\n"
        
    content.append({"tag": "h4", "children": ["Information"]})
    content.append({"tag": "p", "children": [info_text.strip()]})
        
    # 3. Synopsis
    if details.description:
        content.append({"tag": "h4", "children": ["Synopsis"]})
        content.append({"tag": "p", "children": [details.description]})
        
    # 4. Cast & Crew
    if hasattr(details, "staff_list") and details.staff_list:
        content.append({"tag": "h3", "children": ["Cast & Crew"]})
        
        for staff in details.staff_list[:15]: # Limit to 15 cast members to avoid massive pages
            name = staff.name
            character = staff.character or "Unknown"
            
            content.append({"tag": "h4", "children": [f"{name} as {character}"]})
            if staff.avatar_url:
                content.append({"tag": "img", "attrs": {"src": str(staff.avatar_url)}})

    year = details.release_date.year if details.release_date else ""
    title_str = f"{details.title} ({year})" if year else details.title

    data = {
        "access_token": token,
        "title": title_str,
        "author_name": "Media Bot",
        "content": json.dumps(content),
        "return_content": "false"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post("https://api.telegra.ph/createPage", data=data)
            result = res.json()
            if result.get("ok"):
                return result["result"]["url"]
    except Exception:
        pass
        
    return None
