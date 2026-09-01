import asyncio
"""
General bot commands: /start, /adduser, /remuser, /cancel

Deep-link support:
  https://t.me/botusername?start=movie_<key>   → /movie <url>
  https://t.me/botusername?start=series_<key>  → /series <url>
  https://t.me/botusername?start=episode_<key> → /episode <url>
"""

from pyrogram import Client, filters
from pyrogram.types import Message

from bot.auth import auth_manager, require_auth
from bot.config import OWNER_ID, logger
from bot.deeplink import resolve_deep_link, VALID_PREFIXES
from bot.state import USER_TASKS
from bot.user_settings import user_settings


@Client.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    """Handle /start command and deep-link payloads."""
    logger.info(f"Received /start from user {message.from_user.id if message.from_user else 'Unknown'}")
    user_id = message.from_user.id
    is_auth = auth_manager.is_authorized(user_id)

    # ── Check for deep-link payload ──────────────────────────────────────
    if len(message.command) > 1:
        payload = message.command[1]

        # Check if this looks like a deep-link command
        if any(payload.startswith(f"{p}_") for p in VALID_PREFIXES):
            # Auth check: only authorized users can use deep links
            if not is_auth:
                await message.reply_text("⛔ You are not authorized to use this bot.")
                return

            result = resolve_deep_link(payload)
            if result is None:
                await message.reply_text("❌ This deep link is invalid or has expired.")
                return

            command_type, url = result
            logger.info("Deep-link %s from user %s → %s", command_type, user_id, url)

            # Inject decoded URL into message.command so the handler sees it
            message.command = [command_type, url]

            # Import and delegate to the matching handler
            from bot.commands.direct_download import download_movie, download_episode, download_series

            handler_map = {
                "movie": download_movie,
                "series": download_series,
                "episode": download_episode,
            }

            handler = handler_map[command_type]
            # Run in a new task so each user gets concurrent execution
            task = asyncio.create_task(handler(client, message))
            return

    # ── Normal /start greeting ───────────────────────────────────────────
    msg = (
        "🎬 **Media Download Bot**\n\n"
        f"Your User ID: `{user_id}`\n"
        f"Status: {'✅ Authorized' if is_auth else '⛔ Not Authorized'}\n\n"
    )

    if is_auth:
        msg += (
            "• `/movie <url>` (or reply to a video) - Download movie\n"
            "• `/series <url>` - Download & extract series\n"
            "• `/episode <url>` - Smart download & auto-route episode\n"
            "• `/cancel` - Cancel active download tasks\n"
            "• `/status` - View all active bot downloads/uploads\n"
            "• `/adduser <user_id>` - Authorize a user\n"
            "• `/remuser <user_id>` - Remove user access\n"
            "• `/mbmovie <search>` - Interactive Movie Search\n"
            "• `/mbseries <search>` - Interactive Series Search\n"
            "• `/throttle <num>` - Set download speed blocks (1-20)\n"
            "• `/refresh` - Manually update Jellyfin media library"
        )
    else:
        msg += "Contact the bot owner to get authorized."

    await message.reply_text(msg)


@Client.on_message(filters.command("adduser"))
@require_auth
async def authorize_user(client: Client, message: Message):
    """Handle /adduser command."""
    user_id = message.from_user.id
    if user_id != OWNER_ID:
        await message.reply_text("⛔ Only the owner can authorize users.")
        return

    if len(message.command) < 2:
        await message.reply_text("Usage: `/adduser <user_id>`")
        return

    try:
        target_id = int(message.command[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID. Must be a number.")
        return

    if auth_manager.authorize(target_id):
        await message.reply_text(f"✅ User `{target_id}` has been authorized.")
        try:
            await client.send_message(
                chat_id=target_id,
                text="🎉 You have been authorized to use the Media Download Bot!",
            )
        except Exception:
            await message.reply_text(
                f"⚠️ Could not send a notification to user `{target_id}`. "
                "They might need to send `/start` to the bot first."
            )
    else:
        await message.reply_text(f"ℹ️ User `{target_id}` is already authorized.")


@Client.on_message(filters.command("remuser"))
@require_auth
async def deauthorize_user(client: Client, message: Message):
    """Handle /remuser command."""
    user_id = message.from_user.id
    if user_id != OWNER_ID:
        await message.reply_text("⛔ Only the owner can deauthorize users.")
        return

    if len(message.command) < 2:
        await message.reply_text("Usage: `/remuser <user_id>`")
        return

    try:
        target_id = int(message.command[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID. Must be a number.")
        return

    if target_id == OWNER_ID:
        await message.reply_text("❌ Cannot deauthorize the owner.")
        return

    if auth_manager.deauthorize(target_id):
        await message.reply_text(f"✅ User `{target_id}` has been deauthorized.")
        try:
            await client.send_message(
                chat_id=target_id,
                text="ℹ️ Your authorization to use the Media Download Bot has been revoked by the owner.",
            )
        except Exception:
            pass
    else:
        await message.reply_text(f"ℹ️ User `{target_id}` was not authorized.")


@Client.on_message(filters.command("cancel"))
@require_auth
async def cancel_process(client: Client, message: Message):
    """Handle /cancel command."""
    user_id = message.from_user.id
    
    if len(message.command) < 2:
        if user_id in USER_TASKS:
            canceled_any = False
            for t in USER_TASKS[user_id]:
                if not t.done():
                    t.cancel()
                    canceled_any = True
            if canceled_any:
                await message.reply_text("🚫 Cancelling your active process(es)...")
                return
        await message.reply_text("ℹ️ You have no active processes to cancel.")
        return

    arg = message.command[1].lower()
    
    if arg == "all":
        if user_id != OWNER_ID:
            await message.reply_text("⛔ Only the owner can use `/cancel all`.")
            return
            
        count = 0
        for uid, tasks in USER_TASKS.items():
            for t in tasks:
                if not t.done():
                    t.cancel()
                    count += 1
                
        await message.reply_text(f"🚫 Cancelled {count} active process(es).")
        return

    task_id = message.command[1]
    from bot.state import GLOBAL_TASKS
    
    target_task = None
    for key, task in GLOBAL_TASKS.items():
        if getattr(task, 'id', key) == task_id or key == task_id:
            target_task = task
            break
            
    if target_task:
        target_user_id = target_task.user_id
        
        # Check authorization (admins can cancel any, users only their own)
        if target_user_id != user_id and user_id != OWNER_ID:
            await message.reply_text("⛔ You can only cancel your own tasks.")
            return
            
        async_task = getattr(target_task, 'asyncio_task', None)
        if async_task and not async_task.done():
            async_task.cancel()
            await message.reply_text(f"🚫 Cancelled task `{task_id}`.")
        else:
            # If it's a waiting task (no asyncio_task), just remove it from the dashboard
            if key in GLOBAL_TASKS:
                del GLOBAL_TASKS[key]
            await message.reply_text(f"🧹 Cleared waiting task `{task_id}` from the dashboard.")
    else:
        await message.reply_text(f"❌ Task ID `{task_id}` not found.")


@Client.on_message(filters.command("throttle"))
@require_auth
async def set_throttle(client: Client, message: Message):
    """Handle /throttle command."""
    user_id = message.from_user.id
    
    if len(message.command) < 2:
        current = user_settings.get_user_throttle(user_id)
        await message.reply_text(f"Usage: `/throttle <1-20>`\nYour current active throttle is: **{current}** blocks")
        return
        
    try:
        tasks = int(message.command[1])
    except ValueError:
        await message.reply_text("❌ Invalid number. Usage: `/throttle <1-20>`")
        return
        
    if tasks > 20:
        tasks = 20
        await message.reply_text(f"Friendly message: You can't set it more than 20 blocks to avoid rate-limits, so it has been set to 20! 🚀")
    elif tasks < 1:
        tasks = 1
        
    user_settings.set_user_throttle(user_id, tasks)
    
    if tasks == 1:
        await message.reply_text(f"✅ Your downloads are now set to **Direct Download** (1 Single Stream). No blocks merging!")
    elif tasks <= 20: # Should be true because of above cap
        await message.reply_text(f"✅ Your concurrent download blocks limit is set to: **{tasks}**")

from bot.helpers import refresh_jellyfin

@Client.on_message(filters.command("refresh"))
@require_auth
async def cmd_refresh(client: Client, message: Message):
    """Handle /refresh command."""
    msg = await message.reply_text("🔄 **Triggering media library refresh...**")
    await refresh_jellyfin(telegram_msg=msg)
