import pyrogram
from pyrogram.enums import ParseMode
import asyncio
bot_data = {}
"""
Moviebox interactive commands: /mbmovie, /mbseries, callback queries, inline queries
"""

import os
import re
import shutil
import time
import uuid
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from bot.auth import require_auth
from bot.config import BASE_MOVIES, BASE_SERIES, logger
from bot.helpers import refresh_jellyfin
from bot.state import USER_STATES, USER_TASKS, check_concurrency_limit, register_user_task


# ── helpers ──────────────────────────────────────────────────────────────────

async def run_moviebox_subprocess(cmd: list, status_msg, user_id=None, user_display="Unknown", title="Media", task_id=None) -> str:
    """Runs moviebox CLI in background, sends progress updates to Telegram."""
    cmd_str = " ".join(cmd)

    # moviebox-api has a bug where it ignores the --dir argument.
    # Work around this by running the subprocess directly in the target directory.
    target_cwd = None
    if "--dir" in cmd:
        target_cwd = cmd[cmd.index("--dir") + 1]

    from moviebox_api.v3.constants import USER_AGENT, CLIENT_INFO

    import os
    env = os.environ.copy()
    env["MOVIEBOX_USER_AGENT"] = USER_AGENT
    env["MOVIEBOX_CLIENT_INFO"] = CLIENT_INFO
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=target_cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    from bot.state import GLOBAL_TASKS, GlobalTask
    
    curr_task = asyncio.current_task()
    gtask = None
    for k, v in list(GLOBAL_TASKS.items()):
        if getattr(v, "asyncio_task", None) == curr_task:
            gtask = v
            task_id = task_id or k
            break
            
    if not gtask:
        import uuid
        task_id = task_id or str(uuid.uuid4())[:8]
        gtask = GlobalTask()
        gtask.id = task_id
        gtask.asyncio_task = curr_task
        gtask.chat_id = status_msg.chat.id
        gtask.user_id = user_id
        gtask.user_display = user_display
        GLOBAL_TASKS[task_id] = gtask

    gtask.message = f"📥 <b>Downloading via Moviebox</b>\n⏳ Starting download..." 


    last_update = 0
    last_line = ""
    last_percent = ""
    current_phase = None
    last_pct = 0.0
    last_text = None

    try:
        buffer = b""
        while True:
            chunk = await process.stdout.read(1024)
            if not chunk:
                break

            buffer += chunk
            while b"\r" in buffer or b"\n" in buffer:
                if b"\r" in buffer and b"\n" in buffer:
                    r_idx = buffer.find(b"\r")
                    n_idx = buffer.find(b"\n")
                    if r_idx < n_idx:
                        line_bytes, buffer = buffer.split(b"\r", 1)
                    else:
                        line_bytes, buffer = buffer.split(b"\n", 1)
                elif b"\r" in buffer:
                    line_bytes, buffer = buffer.split(b"\r", 1)
                else:
                    line_bytes, buffer = buffer.split(b"\n", 1)

                if not line_bytes.strip():
                    continue

                line_str = line_bytes.decode("utf-8", errors="replace").strip()
                clean_str = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line_str).strip()
                
                if not clean_str:
                    continue

                last_line = clean_str

                force_update = False
                
                # Check phase change
                phase_match = re.search(r"(Downloading|Merging)", clean_str)
                if phase_match:
                    phase = phase_match.group(1)
                    if current_phase != phase:
                        current_phase = phase
                        force_update = True

                pct_match = re.search(r"(\d+\.?\d*)%", clean_str)
                if pct_match:
                    pct = float(pct_match.group(1))
                    filled = int(pct / 10)
                    bar = "█" * filled + "░" * (10 - filled)
                    
                    if pct == 100.0 and last_pct != 100.0:
                        force_update = True
                        last_pct = 100.0
                        
                    last_percent = f"<code>[{bar}] {pct:.1f}%</code>"

                from bot.config import PROGRESS_UPDATE_DELAY
                if force_update or (time.time() - last_update > PROGRESS_UPDATE_DELAY):
                    last_update = time.time()
                    try:
                        import html
                        info_str = html.escape(clean_str[-120:])
                        
                        size_match = re.search(r"(\d+(?:\.\d+)?[a-zA-Z]*)\s*/\s*(\d+(?:\.\d+)?[a-zA-Z]*)", clean_str)
                        speed_match = re.search(r",\s*([^,\s\[\]]+(?:/s)?)", clean_str)
                        eta_match = re.search(r"<\s*([^,\]\s]+)", clean_str)

                        if size_match and speed_match and eta_match:
                            done_str = size_match.group(1)
                            total_str = size_match.group(2)
                            speed_str = speed_match.group(1)
                            if not speed_str.endswith("/s"):
                                speed_str += "/s"
                            eta_str = eta_match.group(1)
                            phase_display = current_phase if current_phase else "Downloading"
                            
                            ep_match = re.search(r"(S\d{1,2}E\d{1,3})", clean_str, re.IGNORECASE)
                            display_title = f"{title} ({ep_match.group(1).upper()})" if ep_match else title
                            
                            msg = (
                                f"📥 <b>{phase_display}:</b> <code>{display_title}</code>\n"
                                f"{last_percent}\n"
                                f"<b>Size:</b> <code>{total_str}</code> | <b>Done:</b> <code>{done_str}</code>\n"
                                f"<b>Speed:</b> <code>{speed_str}</code> | <b>ETA:</b> <code>{eta_str}</code>"
                            )
                        else:
                            msg = f"📥 <b>Downloading via Moviebox</b>\n"
                            if last_percent:
                                msg += f"{last_percent}\n"
                            msg += f"<code>{info_str}</code>"
                        
                        if last_text != msg:
                            last_text = msg
                            gtask.message = msg
                    except Exception:
                        pass

        await process.wait()
    except asyncio.CancelledError:
        try:
            process.kill()
            await process.wait()
        except OSError:
            pass
        raise
    if process.returncode == 0:
        return "SUCCESS"
    else:
        return f"ERROR: {last_line}"



async def _start_download(query, user_id, state):
    register_user_task(user_id, asyncio.current_task())
    

    title = state["title"]
    quality = state.get("quality", "best")
    dub = state.get("dub_lang")
    user_display = state.get("user_display", "Unknown")

    resolved_quality = quality
    details = state.get("details")
    if quality == "best" and details:
        try:
            if state["type"] == "movie":
                if getattr(details, "resource_detectors", None) and details.resource_detectors[0].resolution_list:
                    max_res = max([r.resolution.value for r in details.resource_detectors[0].resolution_list])
                    resolved_quality = f"{max_res}p"
            else:
                if getattr(details, "seasons", None):
                    s = int(state.get("season", 1))
                    s_model = next((szn for szn in details.seasons.seasons if szn.season_number == s), None)
                    if s_model and s_model.resolutions:
                        max_res = max([r.resolution.value for r in s_model.resolutions])
                        resolved_quality = f"{max_res}p"
        except Exception:
            pass

    from bot.config import IS_DUPLICATE_ALLOWED
    if not IS_DUPLICATE_ALLOWED:
        from bot.helpers import find_fuzzy_movie_folder, check_season_exists_in_cloud
        try:
            if state["type"] == "movie":
                exists, existing_qs = find_fuzzy_movie_folder(title, dub)
                # if we didn't resolve quality, best defaults to 1080p in moviebox
                check_q = "1080p" if resolved_quality == "best" else resolved_quality
                if exists and check_q in existing_qs:
                    await query.message.reply_text(f"❌ Aborted: **{title}** already exists in **{check_q}** on your server!")
                    return
            else:
                if state.get("scope") != "auto":
                    s = state.get("season", 1)
                    check_q = "1080p" if resolved_quality == "best" else resolved_quality
                    if check_season_exists_in_cloud(title, s, check_q, dub):
                        await query.message.reply_text(f"❌ Aborted: **{title} Season {s}** already exists in **{check_q}** on your server!")
                        return
        except OSError as e:
            import logging
            logging.error(f"Duplicate check failed due to mount error: {e}")
            await query.message.reply_text("❌ **CRITICAL ERROR:** The Cloud Mount is disconnected inside the Docker container!\n\nPlease fix this by running `docker compose restart` on your server.")
            return

    state["resolved_quality"] = resolved_quality


    venv_moviebox = str(Path(__file__).parent.parent.parent / ".venv" / "bin" / "moviebox")
    if not os.path.exists(venv_moviebox):
        venv_moviebox = shutil.which("moviebox") or "moviebox"
    cmd = [venv_moviebox, "v3"]
    
    unique_id = str(uuid.uuid4())[:8]
    target_dir = BASE_MOVIES / unique_id if state["type"] == "movie" else BASE_SERIES / unique_id
    target_dir.mkdir(parents=True, exist_ok=True)

    cmds = []
    
    if state["type"] == "movie":
        cmd.extend([
            "download-movie", title,
            "--quality", quality,
            "--dir", str(target_dir),
            "--caption-dir", str(target_dir),
            "--part-dir", str(target_dir),
            "--yes", "--tasks", "5",
            "--ignore-missing-caption",
        ])
        if state.get("year"):
            cmd.extend(["--year", str(state["year"])])
        if dub and dub.lower() != "original audio":
            cmd.extend(["--dub", dub])
        cmds.append(cmd)
    else:
        base_series_cmd = cmd + [
            "download-series", title,
            "--quality", quality,
            "--dir", str(target_dir),
            "--caption-dir", str(target_dir),
            "--part-dir", str(target_dir),
            "--yes", "--tasks", "5",
            "--format", "struct",
            "--ignore-missing-caption",
        ]
        if dub and dub.lower() != "original audio":
            base_series_cmd.extend(["--dub", dub])
            
        s = state.get("season", 1)
            
        if state.get("scope") == "auto":
            cmds.append(base_series_cmd + ["-s", "1", "-e", "1", "--auto-mode"])
        elif state.get("scope") == "season":
            total_eps = state.get("total_episodes", 100)
            cmds.append(base_series_cmd + ["-s", str(s), "-e", "1", "--limit", str(total_eps)])
        elif state.get("scope") == "range":
            e = state.get("episode", 1)
            limit = state.get("episode_limit", 1)
            cmds.append(base_series_cmd + ["-s", str(s), "-e", str(e), "--limit", str(limit)])
        elif state.get("scope") == "selected":
            # Group into contiguous blocks to minimize CLI calls
            selected = sorted(list(state.get("selected_episodes", set())))
            if not selected:
                await query.message.edit_text("❌ No episodes were selected.")
                return
                
            blocks = []
            current_start = selected[0]
            current_count = 1
            
            for i in range(1, len(selected)):
                if selected[i] == selected[i-1] + 1:
                    current_count += 1
                else:
                    blocks.append((current_start, current_count))
                    current_start = selected[i]
                    current_count = 1
            blocks.append((current_start, current_count))
            
            for block_start, block_limit in blocks:
                cmds.append(base_series_cmd + ["-s", str(s), "-e", str(block_start), "--limit", str(block_limit)])
        else:
            e = state.get("episode", 1)
            cmds.append(base_series_cmd + ["-s", str(s), "-e", str(e), "--limit", "1"])

    from bot.state import task_manager, GlobalTask
    
    # Create the placeholder task for the queue
    qtask = GlobalTask()
    qtask.id = state.get("task_id") or __import__("uuid").uuid4().hex[:8]
    qtask.asyncio_task = asyncio.current_task()
    qtask.chat_id = query.message.chat.id
    qtask.user_id = user_id
    qtask.user_display = user_display
    qtask.title = title
    
    # Build static info
    s_info = f"🎬 <b>Title:</b> <code>{title}</code>"
    if state["type"] == "series" and "season" in state:
        s_info += f"\n📺 <b>Season:</b> <code>S{int(state['season']):02d}</code>"
        
        if state.get("episodes") == "ALL":
            s_info += "\n🎞️ <b>Episodes:</b> <code>Full Season</code>"
        elif "selected_episodes" in state:
            selected = list(state["selected_episodes"])
            eps_str = ", ".join(str(e) for e in sorted(selected))
            if len(eps_str) > 30: eps_str = eps_str[:27] + "..."
            s_info += f"\n🎞️ <b>Episodes:</b> <code>{eps_str}</code>"
        elif "episode" in state:
            s_info += f"\n🎞️ <b>Episode:</b> <code>{state['episode']}</code>"
            
    if "quality" in state:
        s_info += f"\n⚙️ <b>Action:</b> Download (<code>{state['quality']}</code>)"
    else:
        s_info += "\n⚙️ <b>Action:</b> Download Selected"
        
    if dub:
        import html as html_lib
        s_info += f"\n🔊 <b>Audio:</b> <code>{html_lib.escape(dub)}</code>"
    qtask.static_info = s_info

    try:
        from bot.config import WEB_SERVER_URL, WEB_SERVER_PORT, get_base_url
        dash_url = f"{get_base_url()}/dashboard"
            
        import html as html_lib
        safe_title = html_lib.escape(title)
        
        dm_text = (
            f"✅ <b>Task Queued / Started!</b> \n"
            f"🪪 <b>Task ID:</b> <code>{qtask.id}</code>\n"
            f"🎬 <b>Title:</b> <code>{safe_title}</code>\n"
        )
        if dub:
            dm_text += f"🔊 <b>Audio:</b> <code>{html_lib.escape(dub)}</code>\n"
            
        if state["type"] == "series" and "season" in state:
            dm_text += f"📺 <b>Season:</b> <code>S{int(state['season']):02d}</code>\n"
            
            if state.get("episodes") == "ALL":
                dm_text += "🎞️ <b>Episodes:</b> <code>Full Season</code>\n"
            elif "selected_episodes" in state:
                selected = list(state["selected_episodes"])
                eps_str = ", ".join(str(e) for e in sorted(selected))
                if len(eps_str) > 30: eps_str = eps_str[:27] + "..."
                dm_text += f"🎞️ <b>Episodes:</b> <code>{eps_str}</code>\n"
            elif "episode" in state:
                dm_text += f"🎞️ <b>Episode:</b> <code>{state['episode']}</code>\n"
                
        dm_text += (
            f"\nTrack progress in real-time on the Web Dashboard:\n"
            f"🌐 <a href='{dash_url}'>Open Dashboard</a>"
        )
        
        try:
            bot = query._client
            await bot.send_message(
                chat_id=user_id,
                text=dm_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            await query.edit_message_text("✅ <b>Task Started!</b> Check your DM for the dashboard link.", parse_mode=ParseMode.HTML)
        except Exception as e:
            import logging
            logging.error(f"DM FAILED: {e}")
            try:
                await query.edit_message_text(
                    dm_text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
            except Exception as e2:
                logging.error(f"EDIT FAILED: {e2}")
    except Exception as e3:
        import logging
        logging.error(f"OUTER FAILED: {e3}")
        pass
    


    
    # Wait in Queue
    await task_manager.acquire(qtask, query._client)
    
    try:
        result = "SUCCESS"
        for c in cmds:
            # run_moviebox_subprocess handles creating its own GlobalTask while running
            # We can remove qtask while running, or just let it get overridden.
            res = await run_moviebox_subprocess(c, query.message, user_id=user_id, user_display=user_display, title=title, task_id=state.get("task_id"))
            if res != "SUCCESS":
                result = res
                break

        if result == "SUCCESS":
            try:
                from bot.state import update_status_msg
                await update_status_msg(query.message, "✅ Download complete! Uploading to cloud...")
            except Exception:
                pass
            
            from bot.uploader import perform_autorclone
            
            resolved_quality = state.get("resolved_quality", quality)
            check_q = "1080p" if resolved_quality == "best" else resolved_quality
            
            tags = ["[MB]"]
            if dub:
                tags.append(f"[{dub}]")
            tags.append(check_q)
            suffix = " ".join(tags[:-1]) + " - " + tags[-1]
            
            if state["type"] == "movie":
                year = state.get("year", "")
                base_movie_name = f"{title} ({year})" if year else title
                base_movie_name = re.sub(r'[<>:"/\\|?*]', "_", base_movie_name)
                
                from bot.helpers import get_existing_movie_folder
                existing_folder = get_existing_movie_folder(title)
                
                if existing_folder:
                    movie_folder = existing_folder
                else:
                    movie_folder = base_movie_name
                
                # rename files inside target_dir to include suffix and match parent folder
                for item in target_dir.iterdir():
                    if item.is_file():
                        if existing_folder:
                            new_name = f"{existing_folder} {suffix}{item.suffix}"
                        else:
                            new_name = f"{base_movie_name} {suffix}{item.suffix}"
                        item.rename(target_dir / new_name)
                        
                _, final_bot_msg = await perform_autorclone(target_dir, f"Movies/{movie_folder}", query.message, user_id=user_id, user_display=user_display)
            else:
                from bot.helpers import find_fuzzy_series_folder, find_fuzzy_season_folder
                from bot.config import RCLONE_MOUNT_DIR, RCLONE_BASE_DIR
                series_folder = find_fuzzy_series_folder(title)
                
                # Rename the structural series folders created by moviebox inside target_dir
                for item in list(target_dir.iterdir()):
                    if item.is_dir():
                        new_series_dir = target_dir / series_folder
                        if item != new_series_dir:
                            item.rename(new_series_dir)
                        
                        cloud_series_dir = Path(RCLONE_MOUNT_DIR)
                        if RCLONE_BASE_DIR:
                            cloud_series_dir = cloud_series_dir / RCLONE_BASE_DIR
                        cloud_series_dir = cloud_series_dir / "Series" / series_folder

                        # Append suffix to episode files inside season folders
                        for season_dir in list(new_series_dir.iterdir()):
                            if season_dir.is_dir():
                                m = re.search(r'\d+', season_dir.name)
                                if m:
                                    s_num = int(m.group())
                                    correct_season_name = find_fuzzy_season_folder(cloud_series_dir, s_num)
                                    new_season_dir = new_series_dir / correct_season_name
                                    if season_dir != new_season_dir:
                                        season_dir.rename(new_season_dir)
                                        season_dir = new_season_dir

                                for ep_file in season_dir.iterdir():
                                    if ep_file.is_file():
                                        new_ep_name = f"{ep_file.stem} {suffix}{ep_file.suffix}"
                                        ep_file.rename(season_dir / new_ep_name)
                        break
                
                _, final_bot_msg = await perform_autorclone(target_dir, "Series", query.message, user_id=user_id, user_display=user_display)
                
            # Aggressively clean up local empty breadcrumb folders
            try:
                if target_dir.exists() and not any(target_dir.iterdir()):
                    target_dir.rmdir()
            except Exception:
                pass
                
            from bot.helpers import refresh_jellyfin
            await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir="Series")

            if "task_id" in locals() and task_id:

                from bot.state import GLOBAL_TASKS

                GLOBAL_TASKS.pop(task_id, None)
        else:
            error_msg = result.replace("ERROR: ", "")
            try:
                await query.message.edit_text(
                    f"❌ Download failed.\n\n`{error_msg[:500]}`", parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass
    except asyncio.CancelledError:
        try:
            shutil.rmtree(target_dir, ignore_errors=True)
        except Exception:
            pass
        try:
            await query.message.edit_text("🚫 Process cancelled. Junk files cleaned up.")
        except Exception:
            pass
        raise
    finally:
        await task_manager.release(query._client)


def _get_year(item) -> int:
    """Extract year from a search result item, preferring release_date over season."""
    try:
        if hasattr(item, "release_date") and item.release_date:
            return item.release_date.year
    except Exception:
        pass
    return item.season if hasattr(item, "season") and item.season else 0


def _build_search_keyboard(search_id: str, page: int) -> list:
    """Builds inline keyboard for a specific page of search results."""
    search_data = bot_data["mb_search"][search_id]
    items = search_data["items"]
    item_type = search_data["type"]

    PER_PAGE = 5
    start_idx = page * PER_PAGE
    end_idx = start_idx + PER_PAGE
    page_items = items[start_idx:end_idx]

    keyboard = []
    if "mb_cache" not in bot_data:
        bot_data["mb_cache"] = {}

    for item in page_items:
        uid = str(uuid.uuid4())[:8]
        type_char = "m" if item_type == "movie" else "s"
        cb_data = f"mbs_{type_char}_{uid}"
        year = _get_year(item)
        prefix = "🎬" if item_type == "movie" else "📺"
        title_str = f"{prefix} {item.title} ({year})"
        keyboard.append([InlineKeyboardButton(title_str, callback_data=cb_data, style=pyrogram.enums.ButtonStyle.PRIMARY)])

        bot_data["mb_cache"][cb_data] = {
            "type": item_type,
            "title": item.title,
            "year": year,
            "item": item,
            "search_id": search_id,
            "cb_data": cb_data,
        }

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton("⬅️ Previous", callback_data=f"mbp_{search_id}_{page-1}", style=pyrogram.enums.ButtonStyle.PRIMARY)
        )
    if end_idx < len(items):
        nav_row.append(
            InlineKeyboardButton("Next ➡️", callback_data=f"mbp_{search_id}_{page+1}", style=pyrogram.enums.ButtonStyle.PRIMARY)
        )

    if nav_row:
        keyboard.append(nav_row)

    return keyboard


# ── /mbmovie ─────────────────────────────────────────────────────────────────

@Client.on_message(filters.command('mbmovie'))
@require_auth
async def cmd_mbmovie(client: Client, message: Message):
    user_id = message.from_user.id
    if not check_concurrency_limit(user_id):
        await message.reply_text("❌ You already have an active process. Please wait or use /cancel.")
        return

    register_user_task(user_id, asyncio.current_task())
    

    if not message.command[1:]:
        await message.reply_text("Usage: `/mbmovie <search term>`", parse_mode=ParseMode.MARKDOWN)
        return

    query = " ".join(message.command[1:])
    status_msg = await message.reply_text(
        f"🔍 Searching Movies for `{query}`...", parse_mode=ParseMode.MARKDOWN
    )

    try:
        from moviebox_api.v3.core import Search
        from moviebox_api.v3.http_client import MovieBoxHttpClient

        async with MovieBoxHttpClient() as session:
            searcher = Search(session, query)
            results = await searcher.get_content_model()

        movies = [i for i in results.items if i.subject_type.name == "MOVIES"]
        if not movies:
            await status_msg.edit_text("❌ No movies found.")
            return

        search_id = str(uuid.uuid4())[:8]
        if "mb_search" not in bot_data:
            bot_data["mb_search"] = {}

        bot_data["mb_search"][search_id] = {
            "type": "movie",
            "items": movies,
            "query": query,
        }

        keyboard = _build_search_keyboard(search_id, 0)
        msg_text = (
            f"Found movies for `{query}`. Select one:\n\n"
            f"ℹ️ _If you can't find your desired media, please look for it on_ "
            f"[hub.mhspace.store](https://hub.mhspace.store)"
        )
        await status_msg.edit_text(
            msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.exception("Moviebox Search Error")
        await status_msg.edit_text(f"❌ Search Error: {str(e)}")


# ── /mbseries ────────────────────────────────────────────────────────────────

@Client.on_message(filters.command('mbseries'))
@require_auth
async def cmd_mbseries(client: Client, message: Message):
    user_id = message.from_user.id
    if not check_concurrency_limit(user_id):
        await message.reply_text("❌ You already have an active process. Please wait or use /cancel.")
        return

    register_user_task(user_id, asyncio.current_task())
    

    if not message.command[1:]:
        await message.reply_text("Usage: `/mbseries <search term>`", parse_mode=ParseMode.MARKDOWN)
        return

    query = " ".join(message.command[1:])
    status_msg = await message.reply_text(
        f"🔍 Searching Series for `{query}`...", parse_mode=ParseMode.MARKDOWN
    )

    try:
        from moviebox_api.v3.core import Search
        from moviebox_api.v3.http_client import MovieBoxHttpClient

        async with MovieBoxHttpClient() as session:
            searcher = Search(session, query)
            results = await searcher.get_content_model()

        series = [i for i in results.items if i.subject_type.name == "TV_SERIES"]
        if not series:
            await status_msg.edit_text("❌ No series found.")
            return

        search_id = str(uuid.uuid4())[:8]
        if "mb_search" not in bot_data:
            bot_data["mb_search"] = {}

        bot_data["mb_search"][search_id] = {
            "type": "series",
            "items": series,
            "query": query,
        }

        keyboard = _build_search_keyboard(search_id, 0)
        msg_text = (
            f"Found series for `{query}`. Select one:\n\n"
            f"ℹ️ _If you can't find your desired media, please look for it on_ "
            f"[hub.mhspace.store](https://hub.mhspace.store)"
        )
        await status_msg.edit_text(
            msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.exception("Moviebox Search Error")
        await status_msg.edit_text(f"❌ Search Error: {str(e)}")


LANGUAGE_MAP = {
    "hi": "Hindi", "hin": "Hindi", "hindi": "Hindi",
    "ta": "Tamil", "tam": "Tamil", "tamil": "Tamil",
    "te": "Telugu", "tel": "Telugu", "telugu": "Telugu",
    "ml": "Malayalam", "mal": "Malayalam", "malayalam": "Malayalam",
    "kn": "Kannada", "kan": "Kannada", "kannada": "Kannada",
    "bn": "Bengali", "ben": "Bengali", "bengali": "Bengali",
    "mr": "Marathi", "mar": "Marathi", "marathi": "Marathi",
    "gu": "Gujarati", "guj": "Gujarati", "gujarati": "Gujarati",
    "pa": "Punjabi", "pan": "Punjabi", "punjabi": "Punjabi",
    "ur": "Urdu", "urd": "Urdu", "urdu": "Urdu",
    "en": "English", "eng": "English", "english": "English",
    "es": "Spanish", "spa": "Spanish", "spanish": "Spanish",
    "fr": "French", "fre": "French", "fra": "French", "french": "French",
    "de": "German", "ger": "German", "deu": "German", "german": "German",
    "it": "Italian", "ita": "Italian", "italian": "Italian",
    "pt": "Portuguese", "por": "Portuguese", "portuguese": "Portuguese",
    "ptbr": "Portuguese (Brazil)", "pt-br": "Portuguese (Brazil)",
    "esla": "Spanish (LATAM)", "es-la": "Spanish (LATAM)", "es-mx": "Spanish (LATAM)",
    "ru": "Russian", "rus": "Russian", "russian": "Russian",
    "zh": "Chinese", "chi": "Chinese", "zho": "Chinese", "chinese": "Chinese",
    "ja": "Japanese", "jpn": "Japanese", "japanese": "Japanese",
    "ko": "Korean", "kor": "Korean", "korean": "Korean",
    "ar": "Arabic", "ara": "Arabic", "arabic": "Arabic",
    "tr": "Turkish", "tur": "Turkish", "turkish": "Turkish",
}

def get_language_name(code: str) -> str:
    if not code:
        return "Unknown"
    code_lower = code.lower().strip()
    if code_lower.endswith(" dub"):
        code_lower = code_lower[:-4].strip()
    
    display_name = LANGUAGE_MAP.get(code_lower)
    if display_name:
        return display_name
        
    # Fallback: Capitalize the original string if not found
    return code.capitalize()

async def check_dubs_and_download(query, user_id, state):
    title = state.get("title", "Unknown")
    item = state.get("item")
    details = state.get("details")

    if not item:
        await query.edit_message_text("❌ Session data lost. Please search again.")
        return
        
    dubs = details.dubs if details and hasattr(details, "dubs") else []

    if len(dubs) > 1:
        state["step"] = "wait_dub_selection"
        kb = []
        row = []
        for d in dubs[:8]:
            if getattr(d, 'original', False) or d.lan_name.lower().strip() in ("original audio", "original", "original dub"):
                continue
            d_name = d.lan_name[:20] 
            display_name = get_language_name(d_name)
            row.append(InlineKeyboardButton(f"{display_name}", callback_data=f"mbd_{d_name}", style=pyrogram.enums.ButtonStyle.PRIMARY))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([InlineKeyboardButton("Original Audio", callback_data="mbd_Original Audio", style=pyrogram.enums.ButtonStyle.PRIMARY)])
        kb.append([InlineKeyboardButton("❌ Cancel", callback_data="mb_cancel", style=pyrogram.enums.ButtonStyle.DANGER)])
        
        import html
        poster_url = str(details.cover.url) if details and details.cover and details.cover.url else ""
        year = details.release_date.year if details and details.release_date else ""
        title_escaped = html.escape(title)
        
        caption = (
            f"🎬 <b>{title_escaped} ({year})</b>\n\n"
            f"🔊 <b>Multiple dubs found.</b>\nSelect Dub Language:"
        ).strip()
        
                
        await query.edit_message_text(
            caption,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML,
            
        )
        return

    # No multiple dubs, just pop and download directly
    USER_STATES.pop(user_id, None)
    asyncio.create_task(_start_download(query, user_id, state))


# ── Callback query handler ───────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r'^(mb|epq_|srq_|mvq_)'))
async def handle_callback_query(client: Client, query: CallbackQuery):
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id

    # ── pagination ───────────────────────────────────────────────────────
    if query.data.startswith("mbp_"):
        parts = query.data.split("_")
        
        if len(parts) == 3 and parts[1] == "back":
            search_id = parts[2]
            page = 0
            is_back = True
        elif len(parts) == 3:
            search_id = parts[1]
            page = int(parts[2])
            is_back = False
        else:
            return

        if "mb_search" not in bot_data or search_id not in bot_data["mb_search"]:
            await query.edit_message_text("❌ Search session expired. Please search again.")
            return
            
        search_data = bot_data["mb_search"][search_id]
        keyboard = _build_search_keyboard(search_id, page)
        
        if is_back:
            msg_text = (
                f"Found results for `{search_data['query']}`. Select one:\n\n"
                f"ℹ️ _If you can't find your desired media, please look for it on_ "
                f"[hub.mhspace.store](https://hub.mhspace.store)"
            )
            await query.edit_message_text(
                text=msg_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # ── item / scope selection ───────────────────────────────────────────
    if query.data.startswith("mbs_"):
        cache = bot_data.get("mb_cache", {}).get(query.data)
        if not cache:
            await query.edit_message_text("❌ Session expired. Please search again.")
            return


        USER_STATES[user_id] = cache.copy()
        USER_STATES[user_id]["task_id"] = str(uuid.uuid4())[:8]
        
        ud = query.from_user.username
        USER_STATES[user_id]["user_display"] = f"@{ud}" if ud else (query.from_user.first_name or str(user_id))
        
        # 1. Fetch detailed metadata
        try:
            await query.edit_message_text("⏳ Fetching rich metadata...")
        except Exception:
            pass
        
        try:
            from moviebox_api.v3.core import ItemDetails
            from moviebox_api.v3.http_client import MovieBoxHttpClient
            from bot.telegraph_utils import generate_movie_telegraph
            import urllib.parse
            import html
            
            item = cache.get("item")
            item_id = getattr(item, "id", getattr(item, "subject_id", getattr(item, "subjectId", None)))
            
            async with MovieBoxHttpClient() as session:
                details = await ItemDetails(session, include_seasons=(cache["type"] == "series")).get_content_model(item_id)
                
            # Update cache with rich details
            USER_STATES[user_id]["details"] = details
            
            # Generate Web Server URL
    
            import time
            from bot.state import WEB_CACHE
            page_uuid = str(uuid.uuid4())
            WEB_CACHE[page_uuid] = {"details": details, "timestamp": time.time()}
            
            from bot.config import get_base_url
            telegraph_url = f"{get_base_url()}/info/{page_uuid}"
            
            # Extract poster and year
            poster_url = str(details.cover.url) if details.cover and details.cover.url else ""
            year = details.release_date.year if details.release_date else ""
            rating = details.imdb_rating_value or "N/A"
            genres = ", ".join(details.genre) if details.genre else "N/A"
            title_escaped = html.escape(details.title)
            
            # Generate Trailer URL
            trailer_query = urllib.parse.quote_plus(f"{details.title} {year} Trailer")
            youtube_url = f"https://www.youtube.com/results?search_query={trailer_query}"
            
            # Pre-Download Tags
            audio_langs = list(set([d.lan_name for d in details.dubs])) if details.dubs else []
            subs = details.subtitles or []
            audio_tag = f"🗣 Audio: {', '.join(audio_langs[:3])}" if audio_langs else ""
            sub_tag = f"📝 Subs: {', '.join(subs[:3])}" if subs else ""
            cam_warning = "⚠️ <b>CAMRIP</b>\n" if getattr(details, "is_cam", False) else ""
            
            # Construct beautiful HTML caption
            caption = (
                f"{cam_warning}🎬 <b>{title_escaped} ({year})</b>\n\n"
                f"⭐ IMDb: {rating}  |  🎭 {html.escape(genres)}\n"
                f"{html.escape(audio_tag)}\n{html.escape(sub_tag)}\n\n"
                f"Select Option:"
            ).strip()

            kb = []
            
            if cache["type"] == "movie":
                kb.append([InlineKeyboardButton("📥 Download Movie", callback_data="mbq_best", style=pyrogram.enums.ButtonStyle.SUCCESS)])
            else:
                # Series: We will generate season buttons
                if details.seasons and details.seasons.total_seasons > 0:
                    season_row = []
                    for season in details.seasons.seasons:
                        s_num = season.season_number
                        season_row.append(InlineKeyboardButton(f"S{s_num:02d}", callback_data=f"mb_szn_{s_num}", style=pyrogram.enums.ButtonStyle.PRIMARY))
                        if len(season_row) == 4:
                            kb.append(season_row)
                            season_row = []
                    if season_row:
                        kb.append(season_row)
                else:
                    # Fallback to manual entry if API fails to provide seasons
                    USER_STATES[user_id]["scope"] = "range"
                    USER_STATES[user_id]["step"] = "wait_range_input"
                    caption += "\n\n<i>Please reply with the Season and Episode range.\nFormat: 3 5-10 or 1:1-20</i>"
            
            # Add action buttons
            actions_row = []
            if telegraph_url:
                actions_row.append(InlineKeyboardButton("📖 Full Info", url=telegraph_url))
            actions_row.append(InlineKeyboardButton("🎥 Trailer", url=youtube_url))
            kb.append(actions_row)
            
            kb.append([
                InlineKeyboardButton("⬅️ Back", callback_data=f"mbp_back_{cache.get('search_id', 'expired')}", style=pyrogram.enums.ButtonStyle.PRIMARY),
                InlineKeyboardButton("❌ Cancel", callback_data="mb_cancel", style=pyrogram.enums.ButtonStyle.DANGER)
            ])

            
            await query.edit_message_text(
                caption,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML,
                
            )
        except Exception as e:
            logger.exception("Rich metadata fetch failed")
            await query.edit_message_text(f"❌ Failed to fetch details: {str(e)}")

    # ── episode quality (manual fallback) ──────────────────────────────────
    elif query.data.startswith("epq_"):
        if user_id not in USER_STATES or USER_STATES[user_id].get("step") != "wait_ep_quality":
            await query.edit_message_text("❌ Session expired or invalid state.")
            return

        quality = query.data.replace("epq_", "")
        if quality == "skip":
            quality = None

        state = USER_STATES.pop(user_id)
        task_id = state.get("task_id")
        if task_id:
            from bot.state import GLOBAL_TASKS

            if task_id in GLOBAL_TASKS:
                GLOBAL_TASKS[task_id].asyncio_task = asyncio.current_task()
        filepath = Path(state["filepath"])
        series_name = state["series_name"]
        season = state["season"]
        episode = state["episode"]

        if not filepath.exists():
            await query.edit_message_text("❌ The downloaded file was lost or deleted.")
            return
            
        from bot.config import IS_DUPLICATE_ALLOWED
        if not IS_DUPLICATE_ALLOWED and quality:
            from bot.helpers import check_episode_exists_in_cloud
            if check_episode_exists_in_cloud(series_name, season, episode, quality):
                await query.edit_message_text(f"❌ Aborted: **{series_name} S{season}E{episode}** already exists in **{quality}** on your server!")
                filepath.unlink(missing_ok=True)
                return
            
        try:
            await query.edit_message_text("🔄 Organizing episode...", parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

        from bot.helpers import find_fuzzy_season_folder, get_file_extension
        dest_series_dir = BASE_SERIES / series_name
        season_folder = find_fuzzy_season_folder(dest_series_dir, season)
        dest_folder = dest_series_dir / season_folder
        dest_folder.mkdir(parents=True, exist_ok=True)

        ext = get_file_extension(filepath)
        if quality:
            new_filename = f"S{season:02d}E{episode:02d} - {quality}{ext}"
        else:
            new_filename = f"S{season:02d}E{episode:02d}{ext}"
            
        dest_path = dest_folder / new_filename

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, shutil.move, str(filepath), str(dest_path))

        from bot.state import update_status_msg
        await update_status_msg(query.message, "📤 Uploading episode to cloud...")
        from bot.uploader import perform_autorclone
        _, final_bot_msg = await perform_autorclone(dest_path, f"Series/{series_name}/{season_folder}", query.message, user_id=user_id)
        
        # Aggressively clean up local empty breadcrumb folders
        try:
            if dest_folder.exists() and not any(dest_folder.iterdir()):
                dest_folder.rmdir()
            if dest_series_dir.exists() and not any(dest_series_dir.iterdir()):
                dest_series_dir.rmdir()
        except Exception:
            pass
            
        await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir=f"Series/{series_name}/{season_folder}")

            
        if "task_id" in locals() and task_id:

            
            from bot.state import GLOBAL_TASKS

            
            GLOBAL_TASKS.pop(task_id, None)

    # ── series quality (manual fallback) ───────────────────────────────────
    elif query.data.startswith("srq_"):
        if user_id not in USER_STATES or USER_STATES[user_id].get("step") != "wait_sr_quality":
            await query.edit_message_text("❌ Session expired or invalid state.")
            return

        quality = query.data.replace("srq_", "")
        if quality == "skip":
            quality = None

        state = USER_STATES.pop(user_id)
        task_id = state.get("task_id")
        if task_id:
            from bot.state import GLOBAL_TASKS

            if task_id in GLOBAL_TASKS:
                GLOBAL_TASKS[task_id].asyncio_task = asyncio.current_task()
        filepath = Path(state["filepath"])
        series_name = state["series_name"]
        season = state["season"]
        password = state.get("password")

        multipart_urls = state.get("multipart_urls")

        if not filepath.exists():
            await query.edit_message_text("❌ The downloaded archive was lost or deleted.")
            return

        from bot.config import IS_DUPLICATE_ALLOWED
        if not IS_DUPLICATE_ALLOWED and quality and season is not None:
            from bot.helpers import check_season_exists_in_cloud
            if check_season_exists_in_cloud(series_name, season, quality):
                await query.edit_message_text(f"❌ Aborted: Season {season} of **{series_name}** already exists in **{quality}** on your server!")
                filepath.unlink(missing_ok=True)
                return

        from bot.organizer import prompt_series_download_options
        status_msg = await query.message.edit_text(
            f"📁 Initializing processing for `{series_name}`...",
            parse_mode=ParseMode.MARKDOWN,
        )

        register_user_task(user_id, asyncio.current_task())
        

        await prompt_series_download_options(
            filepath, series_name, season, quality, password, status_msg, user_id, multipart_urls=multipart_urls
        )

    # ── movie quality (manual fallback) ────────────────────────────────────
    elif query.data.startswith("mvq_"):
        if user_id not in USER_STATES or USER_STATES[user_id].get("step") != "wait_mv_quality":
            await query.edit_message_text("❌ Session expired or invalid state.")
            return

        quality = query.data.replace("mvq_", "")
        if quality == "skip":
            quality = None

        state = USER_STATES.pop(user_id)
        task_id = state.get("task_id")
        if task_id:
            from bot.state import GLOBAL_TASKS

            if task_id in GLOBAL_TASKS:
                GLOBAL_TASKS[task_id].asyncio_task = asyncio.current_task()
        filepath = Path(state["filepath"])
        movie_name = state["movie_name"]

        if not filepath.exists():
            await query.edit_message_text("❌ The downloaded file was lost or deleted.")
            return

        from bot.config import IS_DUPLICATE_ALLOWED
        if not IS_DUPLICATE_ALLOWED and quality:
            from bot.helpers import find_fuzzy_movie_folder
            exists, existing_qs = find_fuzzy_movie_folder(movie_name)
            if exists and quality in existing_qs:
                await query.edit_message_text(f"❌ Aborted: Movie **{movie_name}** already exists in **{quality}** on your server!")
                filepath.unlink(missing_ok=True)
                return

        from bot.helpers import get_file_extension
        ext = get_file_extension(filepath)
        from bot.helpers import get_clean_movie_name, get_existing_movie_folder
        existing_folder = get_existing_movie_folder(movie_name)
        folder_name = existing_folder if existing_folder else get_clean_movie_name(movie_name)
        
        if quality and f" - {quality}" not in folder_name:
            new_filename = f"{folder_name} - {quality}{ext}"
        else:
            new_filename = f"{folder_name}{ext}"
            
        dest_folder = BASE_MOVIES / folder_name
        dest_folder.mkdir(parents=True, exist_ok=True)
        dest_path = dest_folder / new_filename

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, shutil.move, str(filepath), str(dest_path))

        unorg_dir = BASE_MOVIES / ".unorganized"
        if unorg_dir.exists() and not any(unorg_dir.iterdir()):
            unorg_dir.rmdir()

        from bot.state import update_status_msg
        await update_status_msg(query.message, "📤 Uploading movie to cloud...")
        from bot.uploader import perform_autorclone
        _, final_bot_msg = await perform_autorclone(dest_folder, f"Movies/{folder_name}", query.message, user_id=user_id)
        
        # Aggressively clean up local empty breadcrumb folders
        try:
            if dest_folder.exists() and not any(dest_folder.iterdir()):
                dest_folder.rmdir()
        except Exception:
            pass
            
        await refresh_jellyfin(telegram_msg=final_bot_msg, target_dir=f"Movies/{folder_name}")

            
        if "task_id" in locals() and task_id:

            
            from bot.state import GLOBAL_TASKS

            
            GLOBAL_TASKS.pop(task_id, None)

    # ── series season selection ─────────────────────────────────────────
    elif query.data.startswith("mb_szn_"):
        if user_id not in USER_STATES:
            await query.edit_message_text("❌ Session expired.")
            return
            
        szn = int(query.data.replace("mb_szn_", ""))
        USER_STATES[user_id]["season"] = szn
        USER_STATES[user_id]["selected_episodes"] = set()
        USER_STATES[user_id]["step"] = "wait_ep_selection" # for text fallback
        
        details = USER_STATES[user_id].get("details")
        if not details or not details.seasons:
            await query.edit_message_text("❌ Session data lost.")
            return
            
        season_model = details.seasons.get_season_by_number(szn)
        USER_STATES[user_id]["total_episodes"] = season_model.total_episodes
        
        kb = []
        ep_row = []
        for ep in range(1, season_model.total_episodes + 1):
            ep_row.append(InlineKeyboardButton(f"E{ep:02d}", callback_data=f"mb_eptoggle_{ep}", style=pyrogram.enums.ButtonStyle.PRIMARY))
            if len(ep_row) == 4:
                kb.append(ep_row)
                ep_row = []
        if ep_row:
            kb.append(ep_row)
            
        kb.append([InlineKeyboardButton("📥 Download Full Season", callback_data="mb_ep_all", style=pyrogram.enums.ButtonStyle.SUCCESS)])
        kb.append([
            InlineKeyboardButton("⬅️ Back to Seasons", callback_data=USER_STATES[user_id].get('cb_data', 'expired'), style=pyrogram.enums.ButtonStyle.PRIMARY),
            InlineKeyboardButton("❌ Cancel", callback_data="mb_cancel", style=pyrogram.enums.ButtonStyle.DANGER)
        ])

        import html
        poster_url = str(details.cover.url) if details.cover and details.cover.url else ""
        year = details.release_date.year if details.release_date else ""
        title_escaped = html.escape(details.title)
        
        caption = (
            f"🎬 <b>{title_escaped} ({year})</b>\n\n"
            f"📺 <b>Season {szn}</b>\n"
            f"<i>Select episodes, OR reply with a range (e.g. 1-15 or 1,3,5)</i>"
        ).strip()
        
        
        await query.edit_message_text(
            caption,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML,
            
        )
        USER_STATES[user_id]["grid_msg_id"] = query.message.message_id
        USER_STATES[user_id]["grid_chat_id"] = query.message.chat.id
        
    # ── series episode toggle ───────────────────────────────────────────
    elif query.data.startswith("mb_eptoggle_"):
        if user_id not in USER_STATES:
            await query.edit_message_text("❌ Session expired.")
            return
            
        ep = int(query.data.replace("mb_eptoggle_", ""))
        selected = USER_STATES[user_id].setdefault("selected_episodes", set())
        if ep in selected:
            selected.remove(ep)
        else:
            selected.add(ep)
            
        total_episodes = USER_STATES[user_id].get("total_episodes", ep)
        
        kb = []
        ep_row = []
        for e in range(1, total_episodes + 1):
            btn_text = f"✅ E{e:02d}" if e in selected else f"E{e:02d}"
            ep_row.append(InlineKeyboardButton(btn_text, callback_data=f"mb_eptoggle_{e}", style=pyrogram.enums.ButtonStyle.PRIMARY))
            if len(ep_row) == 4:
                kb.append(ep_row)
                ep_row = []
        if ep_row:
            kb.append(ep_row)
            
        if selected:
            kb.append([InlineKeyboardButton(f"📥 Download Selected ({len(selected)})", callback_data="mb_ep_selected", style=pyrogram.enums.ButtonStyle.SUCCESS)])
        kb.append([InlineKeyboardButton("📥 Download Full Season", callback_data="mb_ep_all", style=pyrogram.enums.ButtonStyle.SUCCESS)])
        kb.append([
            InlineKeyboardButton("⬅️ Back to Seasons", callback_data=USER_STATES[user_id].get('cb_data', 'expired'), style=pyrogram.enums.ButtonStyle.PRIMARY),
            InlineKeyboardButton("❌ Cancel", callback_data="mb_cancel", style=pyrogram.enums.ButtonStyle.DANGER)
        ])
        
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

    # ── series episode download action ──────────────────────────────────
    elif query.data.startswith("mb_ep_"):
        if user_id not in USER_STATES:
            await query.edit_message_text("❌ Session expired.")
            return
            
        ep_val = query.data.replace("mb_ep_", "")
        if ep_val == "all":
            USER_STATES[user_id]["scope"] = "season"
            USER_STATES[user_id]["episode"] = 1
            ep_str = "Full Season"
        elif ep_val == "selected":
            USER_STATES[user_id]["scope"] = "selected"
            selected = sorted(list(USER_STATES[user_id].get("selected_episodes", [])))
            if not selected:
                return
            ep_str = f"{len(selected)} Selected Episodes"
        else:
            return # Should not happen unless corrupted
            
        USER_STATES[user_id]["quality"] = "best"
        await check_dubs_and_download(query, user_id, USER_STATES[user_id])



    # ── quality selection → dub selection ───────────────────────────────
    elif query.data.startswith("mbq_"):
        if not check_concurrency_limit(user_id):
            await query.edit_message_text("❌ You already have an active process. Please wait or use /cancel.")
            return

        if user_id not in USER_STATES:
            await query.edit_message_text("❌ Session expired.")
            return

        quality = query.data.replace("mbq_", "")
        state = USER_STATES[user_id]
        state["quality"] = quality
        
        await check_dubs_and_download(query, user_id, state)

    # ── dub selection → start download ───────────────────────────────
    elif query.data.startswith("mbd_"):
        if not check_concurrency_limit(user_id):
            await query.edit_message_text("❌ You already have an active process. Please wait or use /cancel.")
            return

        if user_id not in USER_STATES or USER_STATES[user_id].get("step") != "wait_dub_selection":
            await query.edit_message_text("❌ Session expired or invalid state.")
            return

        dub_lang = query.data.replace("mbd_", "")
        state = USER_STATES.pop(user_id)
        state["dub_lang"] = dub_lang
        asyncio.create_task(_start_download(query, user_id, state))

    # ── cancel session ──────────────────────────────────────────────────────────
    elif query.data == "mb_cancel":
        if user_id in USER_STATES:
            del USER_STATES[user_id]
        if user_id in USER_TASKS:
            for t in USER_TASKS[user_id]:
                if not t.done():
                    t.cancel()
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text("❌ Session cancelled.")
        return

    # ── Inline query handler ────────────────────────────────────────────────────

async def inline_query(client, message):
    query = inline_query.query
    if not query or len(query) < 3:
        return
    try:
        from moviebox_api.v3.core import Search
        from moviebox_api.v3.http_client import MovieBoxHttpClient

        async with MovieBoxHttpClient() as session:
            searcher = Search(session, query)
            results = await searcher.get_content_model()

        inline_results = []
        for r in results.items[:10]:
            prefix = "🎬" if r.subject_type.name == "MOVIES" else "📺"
            cmd = f"/mbmovie {r.title}" if r.subject_type.name == "MOVIES" else f"/mbseries {r.title}"
            year = _get_year(r)
            inline_results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title=f"{prefix} {r.title} ({year})",
                    input_message_content=InputTextMessageContent(
                        f"Use `{cmd}` in chat to download this!"
                    ),
                )
            )
        await inline_query.answer(inline_results)
    except Exception:
        pass
