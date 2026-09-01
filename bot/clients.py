from pyrogram import Client
from bot.config import API_ID, API_HASH, BOT_TOKEN, USER_SESSION_STRING

bot_app = Client(
    "media_bot",
    workdir="data",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=dict(root="bot/commands")
)

user_app = None
if USER_SESSION_STRING:
    user_app = Client(
        "media_user",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=USER_SESSION_STRING
    )
