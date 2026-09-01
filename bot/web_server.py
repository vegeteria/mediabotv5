import logging
from aiohttp import web
import html
from bot.state import WEB_CACHE

logger = logging.getLogger("mediabot")

import urllib.parse

def proxy_image(url: str, width=None, height=None, blur=False) -> str:
    if not url or "placeholder.com" in url:
        return url
    encoded = urllib.parse.quote_plus(url)
    proxy_url = f"https://wsrv.nl/?url={encoded}&output=webp"
    if width:
        proxy_url += f"&w={width}"
    if height:
        proxy_url += f"&h={height}"
    if blur:
        proxy_url += "&blur=50"
    return proxy_url

def render_html(details) -> str:
    # Safely escape text fields
    title = html.escape(details.title or "Unknown")
    year = details.release_date.year if details.release_date else ""
    full_title = f"{title} ({year})" if year else title
    
    # Safely extract URLs and route through global CDN for instant loading
    cover = getattr(details, "cover", None)
    raw_poster = str(cover.url) if (cover and getattr(cover, "url", None)) else "https://via.placeholder.com/300x450?text=No+Poster"
    poster_url = proxy_image(raw_poster, width=300)
    
    banner = getattr(details, "banner", getattr(details, "backdrop", None))
    raw_banner = str(banner.url) if (banner and getattr(banner, "url", None)) else raw_poster
    # Aggressively downscale background image since it is blurred anyway
    banner_url = proxy_image(raw_banner, width=600, blur=True)
    
    synopsis = html.escape(details.description or "No synopsis available.")
    rating = details.imdb_rating_value or "N/A"
    runtime = details.duration or "N/A"
    genres = ", ".join(details.genre) if details.genre else "N/A"
    content_rating = details.content_rating or "N/A"
    
    # Process Audio & Subs
    from bot.commands.moviebox import get_language_name
    dubs = getattr(details, "dubs", []) or []
    audio_langs = []
    for d in dubs:
        if getattr(d, 'original', False) or d.lan_name.lower().strip() in ("original audio", "original", "original dub"):
            continue
        audio_langs.append(get_language_name(d.lan_name))
        
    # Always include the original track explicitly
    final_audio_langs = ["Original"] + sorted(list(set(audio_langs)))
    audio_str = ", ".join(final_audio_langs)
    
    subs = getattr(details, "subtitles", []) or []
    sub_str = "English" if subs else "None"
    
    # Cast Grid HTML
    cast_html = ""
    if hasattr(details, "staff_list") and details.staff_list:
        for staff in details.staff_list[:15]:
            actor_name = html.escape(staff.name)
            character = html.escape(staff.character or "Unknown")
            raw_avatar = str(staff.avatar_url) if getattr(staff, "avatar_url", None) else "https://via.placeholder.com/140x210?text=No+Photo"
            avatar = proxy_image(raw_avatar, width=140, height=210)
            cast_html += f"""
            <div class="cast-card">
                <img src="{avatar}" alt="{actor_name}">
                <div class="cast-info">
                    <div class="actor">{actor_name}</div>
                    <div class="character">{character}</div>
                </div>
            </div>
            """
    
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{full_title}</title>
    <style>
        :root {{
            --bg-dark: #0f172a;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #3b82f6;
            --glass-bg: rgba(30, 41, 59, 0.7);
        }}
        body {{
            margin: 0;
            padding: 0;
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background-color: var(--bg-dark);
            color: var(--text-main);
            overflow-x: hidden;
        }}
        .hero-bg {{
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background-image: url('{banner_url}');
            background-size: cover;
            background-position: center;
            filter: blur(40px) brightness(0.3);
            z-index: -1;
            transform: scale(1.1);
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            padding: 40px 20px;
        }}
        .header-section {{
            display: flex;
            gap: 40px;
            background: var(--glass-bg);
            padding: 40px;
            border-radius: 24px;
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
            margin-bottom: 40px;
        }}
        .poster {{
            flex-shrink: 0;
            width: 300px;
            border-radius: 16px;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);
        }}
        .info h1 {{
            margin: 0 0 10px 0;
            font-size: 2.5rem;
            line-height: 1.2;
        }}
        .tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 24px;
        }}
        .tag {{
            background: rgba(255, 255, 255, 0.1);
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 0.9rem;
            font-weight: 500;
        }}
        .tag.accent {{
            background: var(--accent);
            color: white;
        }}
        .synopsis h3 {{
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 1px;
            font-size: 0.9rem;
            margin-bottom: 8px;
        }}
        .synopsis p {{
            font-size: 1.1rem;
            line-height: 1.6;
            margin: 0;
        }}
        .cast-section h2 {{
            font-size: 2rem;
            margin-bottom: 24px;
            padding-left: 10px;
            border-left: 4px solid var(--accent);
        }}
        .cast-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
            gap: 20px;
        }}
        .cast-card {{
            background: rgba(255, 255, 255, 0.05);
            border-radius: 12px;
            overflow: hidden;
            transition: transform 0.2s;
        }}
        .cast-card:hover {{
            transform: translateY(-5px);
            background: rgba(255, 255, 255, 0.1);
        }}
        .cast-card img {{
            width: 100%;
            aspect-ratio: 2/3;
            object-fit: cover;
        }}
        .cast-info {{
            padding: 12px;
            text-align: center;
        }}
        .actor {{
            font-weight: 600;
            font-size: 0.9rem;
            margin-bottom: 4px;
        }}
        .character {{
            font-size: 0.8rem;
            color: var(--text-muted);
        }}
        @media (max-width: 768px) {{
            .header-section {{
                flex-direction: column;
                align-items: center;
                padding: 24px;
                text-align: center;
            }}
            .tags {{
                justify-content: center;
            }}
            .cast-section h2 {{
                text-align: center;
                border-left: none;
                border-bottom: 4px solid var(--accent);
                display: inline-block;
                padding: 0 0 8px 0;
            }}
            .cast-section {{
                text-align: center;
            }}
        }}
    </style>
</head>
<body>
    <div class="hero-bg"></div>
    <div class="container">
        <div class="header-section">
            <img src="{poster_url}" alt="Poster" class="poster">
            <div class="info">
                <h1>{full_title}</h1>
                <div class="tags">
                    <div class="tag accent" style="display: flex; align-items: center; gap: 6px; padding: 4px 10px;">
                        <img src="https://upload.wikimedia.org/wikipedia/commons/6/69/IMDB_Logo_2016.svg" alt="IMDb" height="16">
                        <span style="font-weight: 700;">{rating}</span>
                    </div>
                    <div class="tag">⏱ {runtime}</div>
                    <div class="tag">🔞 {content_rating}</div>
                    <div class="tag">🎭 {genres}</div>
                    <div class="tag" style="background: rgba(59, 130, 246, 0.2); border: 1px solid rgba(59, 130, 246, 0.5);">🗣 Audio: {audio_str}</div>
                    <div class="tag" style="background: rgba(255, 255, 255, 0.1);">📝 Subs: {sub_str}</div>
                </div>
                <div class="synopsis">
                    <h3>Synopsis</h3>
                    <p>{synopsis}</p>
                </div>
            </div>
        </div>
        
        <div class="cast-section">
            <h2>Cast & Crew</h2>
            <div class="cast-grid">
                {cast_html}
            </div>
        </div>
    </div>
</body>
</html>
    """

async def handle_info(request):
    import time
    
    # 1. Garbage Collection Sweep (Self-cleaning)
    # Deletes any cached sessions older than 24 hours (86400 seconds)
    current_time = time.time()
    expired_keys = [k for k, v in WEB_CACHE.items() if current_time - v.get("timestamp", 0) > 86400]
    for k in expired_keys:
        del WEB_CACHE[k]
        
    uid = request.match_info.get('uuid', '')
    cache_entry = WEB_CACHE.get(uid)
    
    if not cache_entry:
        return web.Response(text="Session expired or movie not found. Please search again in Telegram.", status=404)
        
    html_content = render_html(cache_entry["details"])
    return web.Response(text=html_content, content_type='text/html')

async def api_tasks(request):
    from bot.state import GLOBAL_TASKS, task_manager
    import re
    
    tasks_data = []
    # Sort tasks by creation order (ordered dict by default in python 3.7+)
    for key, task in GLOBAL_TASKS.items():
        msg = getattr(task, "message", "Waiting in queue...")
        # Parse common Telegram HTML formatting into safe HTML or strip it
        # We'll just let the dashboard render the safe HTML tags since we control them
        tasks_data.append({
            "id": getattr(task, "id", key),
            "user": getattr(task, "user_display", "Unknown"),
            "status": msg,
            "static_info": getattr(task, "static_info", "")
        })
        
    return web.json_response({
        "tasks": tasks_data,
        "queue_len": len(task_manager.queue),
        "active_count": task_manager.active,
        "max_concurrent": task_manager.max_concurrent
    }, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0"
    })

async def handle_dashboard(request):
    html_content = r"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>MediaBot Advanced</title>
        <link rel="icon" href="/logo.svg" type="image/svg+xml">
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            body {
                font-family: 'Inter', sans-serif;
                background-color: #000000;
                color: #e3e3e3;
                margin: 0;
                overflow-x: hidden;
            }

            .aurora-bg {
                position: fixed;
                top: 0; left: 0; width: 100vw; height: 100vh;
                z-index: -1;
                overflow: hidden;
                background: #000;
            }
            .aurora-blob {
                position: absolute;
                filter: blur(90px);
                opacity: 0.5;
                animation: float 20s infinite ease-in-out alternate;
            }
            .blob-1 {
                background: radial-gradient(circle, rgba(66,133,244,0.8) 0%, rgba(0,0,0,0) 70%);
                width: 60vw; height: 60vw;
                top: -20vh; left: -10vw;
            }
            .blob-2 {
                background: radial-gradient(circle, rgba(161,90,227,0.6) 0%, rgba(0,0,0,0) 70%);
                width: 50vw; height: 50vw;
                bottom: -10vh; right: -5vw;
                animation-delay: -5s;
            }
            .blob-3 {
                background: radial-gradient(circle, rgba(234,67,53,0.4) 0%, rgba(0,0,0,0) 70%);
                width: 45vw; height: 45vw;
                top: 30vh; left: 30vw;
                animation-delay: -10s;
            }
            @keyframes float {
                0% { transform: translate(0, 0) scale(1); }
                50% { transform: translate(5%, 10%) scale(1.1); }
                100% { transform: translate(-5%, -5%) scale(0.9); }
            }

            .glass-card {
                background: rgba(20, 20, 22, 0.5);
                backdrop-filter: blur(24px);
                -webkit-backdrop-filter: blur(24px);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 24px;
                box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
                transition: all 0.3s ease;
            }
            .glass-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 12px 40px 0 rgba(0, 0, 0, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.15);
            }
            
            .glass-header {
                background: rgba(10, 10, 12, 0.7);
                backdrop-filter: blur(20px);
                -webkit-backdrop-filter: blur(20px);
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                position: sticky;
                top: 0;
                z-index: 50;
            }

            .task-msg b { font-weight: 600; color: #fff; }
            .task-msg code { 
                font-family: 'SFMono-Regular', Consolas, monospace; 
                background: rgba(255, 255, 255, 0.1); 
                padding: 2px 6px; 
                border-radius: 6px; 
                color: #a8c7fa; 
                font-size: 0.9em;
            }

            .text-gradient {
                background: linear-gradient(135deg, #4285f4, #a15ae3, #ea4335);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
            }
            
            /* Custom Scrollbar */
            ::-webkit-scrollbar { width: 8px; }
            ::-webkit-scrollbar-track { background: transparent; }
            ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.2); border-radius: 4px; }
            ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.3); }
        </style>
    </head>
    <body class="min-h-screen">
        <div class="aurora-bg">
            <div class="aurora-blob blob-1"></div>
            <div class="aurora-blob blob-2"></div>
            <div class="aurora-blob blob-3"></div>
        </div>

        <header class="glass-header py-4 px-6 md:px-12 mb-8 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <img src="/logo.svg" alt="Logo" class="w-11 h-11 shadow-lg shadow-purple-500/30 rounded-xl hover:scale-105 transition-transform" />
                <div>
                    <h1 class="text-xl md:text-2xl font-bold tracking-tight">MediaBot <span class="text-gradient">Advanced</span></h1>
                </div>
            </div>
            <div class="flex gap-6">
                <div class="text-center">
                    <div class="text-2xl font-bold text-white tracking-tighter" id="active-count">0/3</div>
                    <div class="text-[10px] text-slate-400 uppercase tracking-widest font-semibold mt-1">Active</div>
                </div>
                <div class="text-center pl-6 border-l border-white/10">
                    <div class="text-2xl font-bold text-white tracking-tighter" id="queue-count">0</div>
                    <div class="text-[10px] text-slate-400 uppercase tracking-widest font-semibold mt-1">Queued</div>
                </div>
            </div>
        </header>

        <main class="max-w-4xl mx-auto px-4 md:px-8 pb-20">
            <div id="tasks-container" class="space-y-5">
                <div class="text-center py-20" id="empty-state">
                    <div class="w-20 h-20 mx-auto rounded-full bg-white/5 flex items-center justify-center mb-6 border border-white/10">
                        <i class="fa-solid fa-check text-3xl text-white/40"></i>
                    </div>
                    <h2 class="text-xl font-semibold text-white mb-2">All caught up</h2>
                    <p class="text-slate-400">The server is idle and ready for new tasks.</p>
                </div>
            </div>
        </main>

        <script>
            function escapeHtml(unsafe) {
                return unsafe
                    .replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;")
                    .replace(/"/g, "&quot;")
                    .replace(/'/g, "&#039;");
            }
            
            function copyId(id, btnElement) {
                navigator.clipboard.writeText(id).then(() => {
                    const originalHTML = btnElement.innerHTML;
                    btnElement.innerHTML = '<i class="fa-solid fa-check text-green-400"></i> Copied';
                    setTimeout(() => { btnElement.innerHTML = originalHTML; }, 2000);
                });
            }
            
            function parseTelegramHtml(msg) {
                return msg.replace(/\\n/g, '\n');
            }

            async function fetchTasks() {
                try {
                    const response = await fetch(`/api/tasks?_t=${new Date().getTime()}`);
                    const data = await response.json();
                    
                    document.getElementById('active-count').innerText = `${data.active_count}/${data.max_concurrent}`;
                    document.getElementById('queue-count').innerText = data.queue_len;
                    
                    const container = document.getElementById('tasks-container');
                    
                    if (data.tasks.length === 0) {
                        container.innerHTML = `
                        <div class="text-center py-20" id="empty-state">
                            <div class="w-20 h-20 mx-auto rounded-full bg-white/5 flex items-center justify-center mb-6 border border-white/10 transition-all hover:bg-white/10 hover:scale-105">
                                <i class="fa-solid fa-check text-3xl text-white/50"></i>
                            </div>
                            <h2 class="text-xl font-semibold text-white mb-2">All caught up</h2>
                            <p class="text-slate-400">The server is idle and ready for new tasks.</p>
                        </div>`;
                        return;
                    }
                    
                    let html = '';
                    data.tasks.forEach(task => {
                        html += `
                        <div class="glass-card p-6 md:p-8 relative overflow-hidden group">
                            <div class="absolute inset-0 bg-gradient-to-br from-white/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500"></div>
                            
                            <div class="flex justify-between items-center mb-6 relative z-10">
                                <div class="flex items-center gap-3 bg-white/10 px-4 py-2 rounded-full border border-white/5">
                                    <div class="w-6 h-6 rounded-full bg-gradient-to-br from-blue-400 to-purple-400 flex items-center justify-center">
                                        <i class="fa-solid fa-user text-[10px] text-white"></i>
                                    </div>
                                    <span class="font-medium text-sm text-white">${escapeHtml(task.user)}</span>
                                </div>
                                
                                <div class="text-xs text-slate-400 font-mono flex items-center gap-2 cursor-pointer hover:text-white transition-colors bg-white/5 px-3 py-1.5 rounded-full border border-white/5" onclick="copyId('${escapeHtml(task.id)}', this)" title="Copy ID">
                                    <span>ID: ${escapeHtml(task.id)}</span>
                                    <i class="fa-regular fa-copy"></i>
                                </div>
                            </div>
                            
                            <div class="relative z-10">
                                ${task.static_info ? `<div class="mb-5 pb-5 border-b border-white/10 text-sm text-slate-300 font-medium whitespace-pre-wrap leading-relaxed">${parseTelegramHtml(task.static_info)}</div>` : ''}
                                <div class="task-msg text-sm md:text-base leading-relaxed whitespace-pre-wrap text-slate-200">${parseTelegramHtml(task.status)}</div>
                            </div>
                        </div>`;
                    });
                    
                    container.innerHTML = html;
                } catch (error) {
                    console.error('Failed to fetch tasks:', error);
                }
            }

            setInterval(fetchTasks, 1500);
            fetchTasks();
        </script>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')


async def handle_logo(request):
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="brand-grad" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#4285f4" />
      <stop offset="50%" stop-color="#a15ae3" />
      <stop offset="100%" stop-color="#ea4335" />
    </linearGradient>
    <linearGradient id="glow-grad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="1" />
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0.8" />
    </linearGradient>
  </defs>
  <rect width="512" height="512" rx="128" fill="url(#brand-grad)" />
  <path d="M 180,140 C 180,130 190,123 200,129 L 380,240 C 390,246 390,265 380,271 L 200,382 C 190,388 180,381 180,371 Z" fill="url(#glow-grad)" />
  <path d="M 340,120 Q 350,150 380,160 Q 350,170 340,200 Q 330,170 300,160 Q 330,150 340,120 Z" fill="#ffffff" opacity="0.9"/>
  <path d="M 140,280 Q 145,295 160,300 Q 145,305 140,320 Q 135,305 120,300 Q 135,295 140,280 Z" fill="#ffffff" opacity="0.7"/>
</svg>'''
    return web.Response(text=svg, content_type='image/svg+xml')

async def start_web_server():
    app = web.Application()
    app.add_routes([
        web.get('/info/{uuid}', handle_info),
        web.get('/api/tasks', api_tasks),
        web.get('/dashboard', handle_dashboard),
        web.get('/logo.svg', handle_logo),
        web.get('/favicon.ico', handle_logo)
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    
    import os
    port = int(os.environ.get("WEB_SERVER_PORT", 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Internal Web Server started on port {port}")
