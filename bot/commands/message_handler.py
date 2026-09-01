from pyrogram.enums import ParseMode
import asyncio
"""
Text-reply state machine – handles conversational follow-ups
(movie rename, series password/name/season, episode manual input).
"""

import re
import shutil
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.config import BASE_MOVIES, BASE_SERIES
from bot.helpers import (
    find_fuzzy_season_folder,
    find_fuzzy_series_folder,
    get_file_extension,
    refresh_jellyfin,
)
from bot.organizer import continue_series_processing, process_series_archive
from bot.state import USER_STATES, USER_TASKS, check_concurrency_limit, register_user_task


@Client.on_message(filters.text & ~filters.regex(r"^/"))
async def handle_message_input(client: Client, message: Message):
    user_id = message.from_user.id

    # ── movie rename ─────────────────────────────────────────────────────
    if user_id in USER_STATES and USER_STATES[user_id].get("step") == "wait_movie_name":
        movie_name = message.text.strip()
        filepath = Path(USER_STATES[user_id]["filepath"])
        task_id = USER_STATES[user_id].get("task_id")
        if task_id:
            from bot.state import GLOBAL_TASKS
            if task_id in GLOBAL_TASKS:
                GLOBAL_TASKS[task_id].asyncio_task = asyncio.current_task()
        del USER_STATES[user_id]

        if not filepath.exists():
            await message.reply_text("❌ The downloaded file was lost or deleted.")
            return

        try:
            ext = get_file_extension(filepath)

            if movie_name.lower().endswith(ext.lower()):
                movie_name = movie_name[: -len(ext)]

            from bot.helpers import extract_quality
            quality = extract_quality(filepath.name)
            if not quality:
                from bot.helpers import detect_quality_with_ffprobe
                quality = await detect_quality_with_ffprobe(str(filepath))

            if not quality:
                USER_STATES[user_id] = {
                    "step": "wait_mv_quality",
                    "filepath": str(filepath),
                    "movie_name": movie_name
                }
                kb = [
                    [InlineKeyboardButton("480p", callback_data="mvq_480p"),
                     InlineKeyboardButton("720p", callback_data="mvq_720p"),
                     InlineKeyboardButton("1080p", callback_data="mvq_1080p")],
                    [InlineKeyboardButton("4k", callback_data="mvq_4k"),
                     InlineKeyboardButton("Skip / None", callback_data="mvq_skip")]
                ]
                await message.reply_text(
                    f"✅ Name saved: `{movie_name}`\n\n"
                    f"ℹ️ **Quality tag missing.** Please select the movie quality:",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN
                )
                return

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

            
            from bot.config import WEB_SERVER_URL, WEB_SERVER_PORT, get_base_url
            dashboard_link = f"{get_base_url()}/dashboard"
            msg = f"✅ **Task Started!**\n\nTrack progress in real-time on the Web Dashboard:\n🌐 [Open Dashboard]({dashboard_link})"

            status_msg = await message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            from bot.uploader import perform_autorclone
            _, final_bot_msg = await perform_autorclone(dest_folder, f"Movies/{folder_name}", status_msg, user_id=user_id)
            
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
        except Exception as e:
            await message.reply_text(f"❌ Error moving file: {e}")
        return

    # ── series password ──────────────────────────────────────────────────
    if user_id in USER_STATES and USER_STATES[user_id].get("step") == "wait_series_password":
        password = message.text.strip()
        state = USER_STATES.pop(user_id)
        filepath = Path(state["filepath"])
        explicit_series_name = state.get("explicit_series_name")
        task_id = state.get("task_id")
        if task_id:
            from bot.state import GLOBAL_TASKS
            if task_id in GLOBAL_TASKS:
                GLOBAL_TASKS[task_id].asyncio_task = asyncio.current_task()

        if not filepath.exists():
            await message.reply_text("❌ The downloaded archive was lost or deleted.")
            return

        status_msg = await message.reply_text(
            "✅ Password saved. Checking archive details...", parse_mode=ParseMode.MARKDOWN
        )

        register_user_task(user_id, asyncio.current_task())
        
        multipart_urls = state.get("multipart_urls")

        await continue_series_processing(
            filepath, explicit_series_name, status_msg, user_id, password=password, multipart_urls=multipart_urls
        )
        return

    # ── series name ──────────────────────────────────────────────────────
    if user_id in USER_STATES and USER_STATES[user_id].get("step") == "wait_series_name":
        from bot.helpers import find_fuzzy_series_folder
        series_name = message.text.strip()
        series_name = find_fuzzy_series_folder(series_name)
        USER_STATES[user_id]["series_name"] = series_name
        USER_STATES[user_id]["step"] = "wait_series_season"
        await message.reply_text(
            "✅ Series Name saved.\n\nNow enter the **Season number** (e.g. `1` or `S01`):",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── series season ────────────────────────────────────────────────────
    if user_id in USER_STATES and USER_STATES[user_id].get("step") == "wait_series_season":
        text = message.text.strip()
        m = re.search(r"(?:S|Season\s*)?(\d{1,2})", text, re.IGNORECASE)
        if m:
            season = int(m.group(1))
            state = USER_STATES[user_id]
            filepath = Path(state["filepath"])
            series_name = state["series_name"]
            password = state.get("password")
            task_id = state.get("task_id")
            multipart_urls = state.get("multipart_urls")
            if task_id:
                from bot.state import GLOBAL_TASKS
                if task_id in GLOBAL_TASKS:
                    GLOBAL_TASKS[task_id].asyncio_task = asyncio.current_task()
            del USER_STATES[user_id]

            if not filepath.exists():
                await message.reply_text("❌ The downloaded archive was lost or deleted.")
                return

            series_name = find_fuzzy_series_folder(series_name)
            
            from bot.helpers import extract_quality
            quality = extract_quality(filepath.name)
            if not quality:
                from bot.helpers import detect_quality_with_ffprobe
                quality = await detect_quality_with_ffprobe(str(filepath))

            if not quality:
                USER_STATES[user_id] = {
                    "step": "wait_sr_quality",
                    "filepath": str(filepath),
                    "series_name": series_name,
                    "season": season,
                    "password": password,
                    "task_id": task_id,
                    "multipart_urls": multipart_urls
                }
                from bot.state import preserve_task_for_user_input
                preserve_task_for_user_input(USER_STATES[user_id], "⏸️ **Waiting for User Input**\nPlease select series quality in Telegram.")
                kb = [
                    [InlineKeyboardButton("480p", callback_data="srq_480p"),
                     InlineKeyboardButton("720p", callback_data="srq_720p"),
                     InlineKeyboardButton("1080p", callback_data="srq_1080p")],
                    [InlineKeyboardButton("4k", callback_data="srq_4k"),
                     InlineKeyboardButton("Skip / None", callback_data="srq_skip")]
                ]
                await message.reply_text(
                    f"✅ Downloaded: `{filepath.name}`\n\n"
                    f"ℹ️ **Quality tag missing.** Apply quality to all episodes in this archive:",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            status_msg = await message.reply_text(
                f"📁 Initializing processing for `{series_name}`...",
                parse_mode=ParseMode.MARKDOWN,
            )
            from bot.state import update_status_msg
            await update_status_msg(status_msg, f"📁 Initializing processing for `{series_name}`...")

            register_user_task(user_id, asyncio.current_task())
            

            from bot.organizer import prompt_series_download_options
            await prompt_series_download_options(
                filepath, series_name, season, quality, password, status_msg, user_id, multipart_urls=multipart_urls
            )
        else:
            await message.reply_text(
                "❌ Could not parse season. Please try again (e.g. `1` or `S01`)",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # ── moviebox season input ────────────────────────────────────────────
    if user_id in USER_STATES and USER_STATES[user_id].get("step") == "wait_season_input":
        text = message.text.strip()
        m = re.search(r"(?:S|Season\s*)?(\d{1,2})", text, re.IGNORECASE)
        if m:
            season = int(m.group(1))
            USER_STATES[user_id]["season"] = season
            USER_STATES[user_id]["scope"] = "season"
            
            details = USER_STATES[user_id].get("details")
            if details and details.seasons:
                try:
                    season_model = details.seasons.get_season_by_number(season)
                    if season_model:
                        USER_STATES[user_id]["total_episodes"] = season_model.total_episodes
                except Exception:
                    pass
                    
            kb = [
                [InlineKeyboardButton("📥 Confirm Download", callback_data="mbq_best")],
                [InlineKeyboardButton("❌ Cancel", callback_data="mb_cancel")]
            ]
            await message.reply_text(
                f"Parsed Season {season}.",
                reply_markup=InlineKeyboardMarkup(kb),
            )
        else:
            await message.reply_text(
                "❌ Could not parse season. Please use format: `1`, `S01` or `Season 1`",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # ── moviebox interactive episode toggles (hybrid UI) ─────────────────
    if user_id in USER_STATES and USER_STATES[user_id].get("step") == "wait_ep_selection":
        text = message.text.strip()
        
        # Parse text like "1-15", "1,3,5-10", "S2 1-15"
        season_m = re.match(r"(?:S(?:eason)?\s*)?(\d{1,2})\s+(.+)", text, re.I)
        if season_m:
            text = season_m.group(2)
            
        parts = text.split(',')
        new_eps = set()
        for p in parts:
            p = p.strip()
            if '-' in p or '~' in p or 'to' in p.lower():
                rm = re.match(r"(?:E(?:P)?)?\s*(\d{1,3})\s*[-~to]+\s*(?:E(?:P)?)?\s*(\d{1,3})", p, re.I)
                if rm:
                    s, e = int(rm.group(1)), int(rm.group(2))
                    if s > e: s, e = e, s
                    new_eps.update(range(s, e+1))
            else:
                rm = re.match(r"(?:E(?:P)?)?\s*(\d{1,3})", p, re.I)
                if rm:
                    new_eps.add(int(rm.group(1)))
                    
        if new_eps:
            USER_STATES[user_id]["selected_episodes"] = set(new_eps)
            selected = USER_STATES[user_id]["selected_episodes"]
            
            # Re-render keyboard
            total_episodes = USER_STATES[user_id].get("total_episodes", max(new_eps))
            kb = []
            ep_row = []
            for e in range(1, total_episodes + 1):
                btn_text = f"✅ E{e:02d}" if e in selected else f"E{e:02d}"
                ep_row.append(InlineKeyboardButton(btn_text, callback_data=f"mb_eptoggle_{e}"))
                if len(ep_row) == 4:
                    kb.append(ep_row)
                    ep_row = []
            if ep_row:
                kb.append(ep_row)
                
            kb.append([InlineKeyboardButton(f"📥 Download Selected ({len(selected)})", callback_data="mb_ep_selected")])
            kb.append([InlineKeyboardButton("📥 Download Full Season", callback_data="mb_ep_all")])
            kb.append([
                InlineKeyboardButton("⬅️ Back to Seasons", callback_data=USER_STATES[user_id].get('cb_data', 'expired')),
                InlineKeyboardButton("❌ Cancel", callback_data="mb_cancel")
            ])
            
            grid_msg_id = USER_STATES[user_id].get("grid_msg_id")
            grid_chat_id = USER_STATES[user_id].get("grid_chat_id")
            if grid_msg_id and grid_chat_id:
                try:
                    await client.edit_message_reply_markup(
                        chat_id=grid_chat_id,
                        message_id=grid_msg_id,
                        reply_markup=InlineKeyboardMarkup(kb)
                    )
                except Exception as e:
                    import logging
                    logging.getLogger("mediabot").error(f"Failed to edit message reply markup: {e}")
            
            # Delete user's message to keep chat clean
            try:
                await message.delete()
            except Exception:
                pass
            return
            
    # ── moviebox episode range input ─────────────────────────────────────
    if user_id in USER_STATES and USER_STATES[user_id].get("step") == "wait_range_input":
        text = message.text.strip()
        m = re.search(
            r"(?:S(?:eason)?\s*)?(\d{1,2})\s*[:EP.\s]*\s*(?:EP?\s*)?(\d{1,3})\s*[-~to]+\s*(?:EP?\s*)?(\d{1,3})",
            text, re.IGNORECASE,
        )
        if m:
            season = int(m.group(1))
            ep_start = int(m.group(2))
            ep_end = int(m.group(3))
            if ep_start > ep_end:
                ep_start, ep_end = ep_end, ep_start
            USER_STATES[user_id]["season"] = season
            USER_STATES[user_id]["episode"] = ep_start
            USER_STATES[user_id]["episode_limit"] = ep_end - ep_start + 1
            USER_STATES[user_id]["scope"] = "range"
            kb = [
                [InlineKeyboardButton("📥 Confirm Download", callback_data="mbq_best")],
                [InlineKeyboardButton("❌ Cancel", callback_data="mb_cancel")]
            ]
            await message.reply_text(
                f"Parsed Season {season}, Episodes {ep_start}-{ep_end}.",
                reply_markup=InlineKeyboardMarkup(kb),
            )
        else:
            await message.reply_text(
                "❌ Could not parse. Please use format: `3 5-10`, `S01E01-E20`, or `1:1-20`",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # ── moviebox episode input ───────────────────────────────────────────
    if user_id in USER_STATES and USER_STATES[user_id].get("step") == "wait_ep_input":
        text = message.text.strip()
        m = re.search(r"[Ss]?(\d{1,2})[Ee\s]*(\d{1,3})", text, re.I)
        if m:
            s, e = int(m.group(1)), int(m.group(2))
            USER_STATES[user_id]["season"] = s
            USER_STATES[user_id]["episode"] = e
            USER_STATES[user_id]["step"] = "quality"

            kb = [
                [InlineKeyboardButton("📥 Confirm Download", callback_data="mbq_best")],
                [InlineKeyboardButton("❌ Cancel", callback_data="mb_cancel")]
            ]
            await message.reply_text(
                f"Parsed Season {s}, Episode {e}.",
                reply_markup=InlineKeyboardMarkup(kb),
            )
        else:
            await message.reply_text(
                "❌ Could not parse. Please use format: `S01E01` or `1 1`",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # ── episode manual series name ───────────────────────────────────────
    elif user_id in USER_STATES and USER_STATES[user_id].get("step") == "wait_ep_manual_series":
        from bot.helpers import find_fuzzy_series_folder
        text = message.text.strip()
        text = find_fuzzy_series_folder(text)
        USER_STATES[user_id]["series_name"] = text
        USER_STATES[user_id]["step"] = "wait_ep_manual_season_ep"
        await message.reply_text(
            "✅ Series Name saved.\n\nNow enter the **Season and Episode number**:\n"
            "(e.g., `S01E02`, `1 2`, or `1x02`)",
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── episode manual season+episode ────────────────────────────────────
    elif user_id in USER_STATES and USER_STATES[user_id].get("step") == "wait_ep_manual_season_ep":
        text = message.text.strip()
        m = re.search(
            r"(?:S|Season\s*)?(\d{1,2})(?:[\s._xX:-]+)(?:E|Ep|Episode\s*)?(\d{1,3})",
            text,
            re.IGNORECASE,
        )
        if m:
            season = int(m.group(1))
            episode = int(m.group(2))
            series_name = USER_STATES[user_id]["series_name"]

            try:
                filepath = Path(USER_STATES[user_id]["filepath"])

                if not filepath.exists():
                    await message.reply_text("❌ The downloaded file was lost or deleted.")
                    del USER_STATES[user_id]
                    return

                series_name = find_fuzzy_series_folder(series_name)
                
                from bot.helpers import extract_quality
                quality = extract_quality(filepath.name)

                if not quality:
                    USER_STATES[user_id] = {
                        "step": "wait_ep_quality",
                        "filepath": str(filepath),
                        "series_name": series_name,
                        "season": season,
                        "episode": episode,
                    }
                    kb = [
                        [InlineKeyboardButton("480p", callback_data="epq_480p"),
                         InlineKeyboardButton("720p", callback_data="epq_720p"),
                         InlineKeyboardButton("1080p", callback_data="epq_1080p")],
                        [InlineKeyboardButton("4k", callback_data="epq_4k"),
                         InlineKeyboardButton("Skip / None", callback_data="epq_skip")]
                    ]
                    await message.reply_text(
                        f"✅ Downloaded: `{filepath.name}`\n\n"
                        f"ℹ️ **Quality tag missing.** Please select the video quality:",
                        reply_markup=InlineKeyboardMarkup(kb),
                        parse_mode=ParseMode.MARKDOWN
                    )
                    return

                dest_series_dir = BASE_SERIES / series_name
                season_folder = find_fuzzy_season_folder(dest_series_dir, season)
                dest_folder = dest_series_dir / season_folder
                dest_folder.mkdir(parents=True, exist_ok=True)

                ext = get_file_extension(filepath)
                new_filename = f"S{season:02d}E{episode:02d} - {quality}{ext}"
                dest_path = dest_folder / new_filename

                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, shutil.move, str(filepath), str(dest_path))

                unorg_dir = BASE_SERIES / ".unorganized"
                if unorg_dir.exists() and not any(unorg_dir.iterdir()):
                    unorg_dir.rmdir()

                del USER_STATES[user_id]
                
                from bot.config import WEB_SERVER_URL, WEB_SERVER_PORT, get_base_url
                dashboard_link = f"{get_base_url()}/dashboard"
                msg = f"✅ **Task Started!**\n\nTrack progress in real-time on the Web Dashboard:\n🌐 [Open Dashboard]({dashboard_link})"

                status_msg = await message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
                from bot.uploader import perform_autorclone
                _, final_bot_msg = await perform_autorclone(dest_path, f"Series/{series_name}/{season_folder}", status_msg, user_id=user_id)
                
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
            except Exception as e:
                await message.reply_text(
                    f"❌ Error moving file: {e}\nPlease reply again with Season and Episode:"
                )
        else:
            await message.reply_text(
                "❌ Could not parse. Please try again (e.g. `S01E02` or `1 2`)",
                parse_mode=ParseMode.MARKDOWN,
            )
