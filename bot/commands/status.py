from pyrogram.enums import ParseMode
"""
Status command module for displaying global active tasks link.
"""


from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot.auth import require_auth
from bot.config import WEB_SERVER_URL, WEB_SERVER_PORT, get_base_url
from bot.state import GLOBAL_TASKS, task_manager

@Client.on_message(filters.command("status"))
@require_auth
async def cmd_status(client, message):
    """Handle /status command."""
    
    dashboard_link = f"{get_base_url()}/dashboard"
        
    keyboard = [[InlineKeyboardButton("🌐 Open Live Web Dashboard", url=dashboard_link)]]
    
    tasks_count = len(GLOBAL_TASKS)
    queue_count = len(task_manager.queue)
    
    msg = (
        f"📊 <b>Bot Status</b>\n\n"
        f"<b>Active Tasks:</b> {tasks_count}\n"
        f"<b>In Queue:</b> {queue_count}\n\n"
        f"Track progress in real-time below:"
    )
    
    await message.reply_text(
        msg,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )
