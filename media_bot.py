#!/usr/bin/env python3
"""
Telegram Media Download Bot
Downloads movies and series with progress tracking and smart folder organization.

This file is the entry point – it wires up all handlers from the bot/ package
and starts polling.  All logic lives under bot/.
"""

import asyncio
import logging
from pyrogram import idle
from bot.config import BOT_TOKEN, API_ID, API_HASH, USER_SESSION_STRING, OWNER_ID, BASE_MOVIES, BASE_SERIES, BASE_SONGS, logger
from bot.web_server import start_web_server
from bot.clients import bot_app as app, user_app

async def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set in environment!")
        return
    if not API_ID or not API_HASH:
        logger.error("API_ID or API_HASH not set in environment!")
        return

    if OWNER_ID == 0:
        logger.error("OWNER_ID not set in environment!")
        return

    # Ensure base directories exist
    BASE_MOVIES.mkdir(parents=True, exist_ok=True)
    BASE_SERIES.mkdir(parents=True, exist_ok=True)
    BASE_SONGS.mkdir(parents=True, exist_ok=True)

    # Start the web server
    logger.info("Starting web server...")
    await start_web_server()

    # Start the MTProto clients
    logger.info("Starting bot client...")
    await app.start()
    
    if user_app:
        logger.info("Starting user client (2GB+ downloads enabled)...")
        await user_app.start()

    logger.info("Bot is running and polling for updates!")
    
    # Block and listen for updates
    await idle()

    # Graceful shutdown
    logger.info("Stopping clients...")
    await app.stop()
    if user_app:
        await user_app.stop()

if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
