from pyrogram.types import LinkPreviewOptions
"""
Authorization management – persists allowed user IDs in the .env file.
"""

import os
from pathlib import Path
from typing import Set

from dotenv import load_dotenv


from bot.config import ENV_FILE, OWNER_ID


from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import PeerIdInvalid, UserIsBlocked, BadRequest

class AuthManager:
    """Manages authorized users with persistence in .env."""

    def __init__(self, filepath: Path, owner_id: int):
        self.filepath = filepath
        self.owner_id = owner_id
        self.authorized_users: Set[int] = set()
        self._load()

    # ── persistence ──────────────────────────────────────────────────────
    def _load(self):
        """Load authorized users from .env."""
        load_dotenv(self.filepath)
        users_str = os.getenv("AUTHORIZED_USERS", "")
        if users_str:
            try:
                self.authorized_users = set(
                    int(x.strip(" '\""))
                    for x in users_str.split(",")
                    if x.strip(" '\"")
                )
            except ValueError:
                self.authorized_users = set()
        else:
            self.authorized_users = set()

    def _save(self):
        """Save authorized users to .env without breaking docker bind-mount inodes."""
        users_str = ",".join(str(x) for x in self.authorized_users)
        if not self.filepath.exists():
            self.filepath.touch()

        with open(self.filepath, "r") as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            if line.startswith("AUTHORIZED_USERS="):
                lines[i] = f"AUTHORIZED_USERS={users_str}\n"
                updated = True
                break

        if not updated:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"AUTHORIZED_USERS={users_str}\n")

        with open(self.filepath, "w") as f:
            f.writelines(lines)

    # ── public API ───────────────────────────────────────────────────────
    def is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized (owner always is)."""
        return user_id == self.owner_id or user_id in self.authorized_users

    def authorize(self, user_id: int) -> bool:
        """Add user to authorized list. Returns True if newly added."""
        if user_id not in self.authorized_users:
            self.authorized_users.add(user_id)
            self._save()
            return True
        return False

    def deauthorize(self, user_id: int) -> bool:
        """Remove user from authorized list. Returns True if removed."""
        if user_id in self.authorized_users:
            self.authorized_users.discard(user_id)
            self._save()
            return True
        return False

# ── singleton & decorator ────────────────────────────────────────────────────
auth_manager = AuthManager(ENV_FILE, OWNER_ID)

def require_auth(func):
    """Decorator to require authorization for commands (Hydrogram)."""

    async def wrapper(client: Client, message: Message, *args, **kwargs):
        if not message.from_user:
            return
        
        user_id = message.from_user.id
        user_display = message.from_user.username
        user_display = f"@{user_display}" if user_display else (message.from_user.first_name or str(user_id))
        
        from bot.config import GLOBAL_DASHBOARD_GROUPS
        is_explicitly_auth = auth_manager.is_authorized(user_id)
        is_private_chat = message.chat.type.name == "PRIVATE" if hasattr(message.chat.type, 'name') else str(message.chat.type) == "ChatType.PRIVATE"
        
        is_group_member = False
        user_group = None
        
        if not is_explicitly_auth:
            for group_id in GLOBAL_DASHBOARD_GROUPS:
                try:
                    member = await client.get_chat_member(chat_id=group_id, user_id=user_id)
                    if member.status.name not in ['LEFT', 'BANNED']:
                        is_group_member = True
                        user_group = await client.get_chat(chat_id=group_id)
                        break
                except Exception:
                    pass
        
        # 3. Tiered Access Logic
        if not is_explicitly_auth:
            if is_private_chat:
                if user_group:
                    group_title = user_group.title
                    group_link = f"https://t.me/{user_group.username}" if user_group.username else getattr(user_group, 'invite_link', "")
                    
                    if group_link:
                        msg = f"⛔ You are not an Admin.\n\nPlease use my commands inside [{group_title}]({group_link})!"
                    else:
                        msg = f"⛔ You are not an Admin.\n\nPlease use my commands inside **{group_title}**!"
                else:
                    msg = "⛔ You are not an Admin. Please use my commands inside the group chat!"
                    
                await message.reply_text(msg, link_preview_options=LinkPreviewOptions(is_disabled=True))
                return
                
            elif not is_group_member:
                await message.reply_text("⛔ You are not authorized to use this bot here.", reply_to_message_id=message.id)
                return
            
        # 4. Ensure bot can DM the user if command is sent in a group
        if not is_private_chat:
            try:
                # Hydrogram's send_chat_action
                from pyrogram.enums import ChatAction
                await client.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
            except (PeerIdInvalid, UserIsBlocked, BadRequest):
                bot_info = await client.get_me()
                await message.reply_text(
                    f"⚠️ **Action Required, {user_display}!**\n\n"
                    f"You must start the bot in private chat before using it here.\n"
                    f"Please DM @{bot_info.username.replace('_', '\\_')}, press **Start**, and try again.",
                    reply_to_message_id=message.id
                )
                return
                
        return await func(client, message, *args, **kwargs)

    return wrapper
