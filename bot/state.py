from pyrogram.enums import ParseMode
"""
Shared mutable state for user tasks and conversation state machines.

Centralised here so that every module imports from a single source,
avoiding circular-import issues.
"""


import asyncio
# Active long-running tasks, keyed by user_id → list[asyncio.Task]
USER_TASKS: dict[int, list] = {}

def check_concurrency_limit(user_id: int) -> bool:
    from bot.config import SINGLE_USER_CONCURRENT_TASK_LIMIT
    if SINGLE_USER_CONCURRENT_TASK_LIMIT <= 0:
        return True
    
    if user_id not in USER_TASKS:
        return True
        
    USER_TASKS[user_id] = [t for t in USER_TASKS[user_id] if not t.done()]
    return len(USER_TASKS[user_id]) < SINGLE_USER_CONCURRENT_TASK_LIMIT

def register_user_task(user_id: int, task):
    if user_id not in USER_TASKS:
        USER_TASKS[user_id] = []
    USER_TASKS[user_id] = [t for t in USER_TASKS[user_id] if not t.done()]
    USER_TASKS[user_id].append(task)

def preserve_task_for_user_input(state_dict, message_text="⏸️ **Waiting for User Input**\\nPlease respond in Telegram."):
    import logging
    logger = logging.getLogger("mediabot")
    current_asyncio_task = asyncio.current_task()
    found = False
    
    # Try to find by asyncio_task first
    for k, v in list(GLOBAL_TASKS.items()):
        if getattr(v, "asyncio_task", None) == current_asyncio_task:
            v.message = message_text
            v.asyncio_task = None
            state_dict["task_id"] = k
            found = True
            break
            
    if not found:
        # Fallback to finding by task_id in state_dict if it exists
        task_id = state_dict.get("task_id")
        if task_id and task_id in GLOBAL_TASKS:
            GLOBAL_TASKS[task_id].message = message_text
            GLOBAL_TASKS[task_id].asyncio_task = None
            found = True
            logger.warning(f"preserve_task_for_user_input: Found by task_id fallback for {task_id}")
        else:
            logger.error("preserve_task_for_user_input: Could not find matching task to preserve!")

# Conversation state machines (movie rename, series name/season prompts, etc.)
# keyed by user_id → dict with at least a "step" key
USER_STATES: dict[int, dict] = {}

# Callback state machines (direct download audio tracks, mkvmerge options, etc.)
# keyed by task_id -> dict
CALLBACK_STATES: dict[str, dict] = {}

async def update_status_msg(status_msg, text: str):

    from bot.config import get_base_url
    
    current_asyncio_task = asyncio.current_task()
    task_id = None
    for k, v in list(GLOBAL_TASKS.items()):
        if getattr(v, "asyncio_task", None) == current_asyncio_task:
            task_id = k
            break
            
    if task_id:
        dashboard_link = f"{get_base_url()}/dashboard"
        text += f"\n\n🌐 [Open Dashboard]({dashboard_link}) | Task ID: `{task_id}`"
        
    try:
        if hasattr(status_msg, "edit_text"):
            await status_msg.edit_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        else:
            await status_msg.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except Exception:
        pass

class GlobalTask:
    def __init__(self):
        self.message = ""
        self.static_info = ""
        self.chat_id = None
        self.user_id = None
        self.user_display = None
        self.asyncio_task = None
        import random, string
        self.id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))

class TaskManager:
    def __init__(self, max_concurrent=3):
        self.max_concurrent = max_concurrent
        self.active = 0

        self.queue = []
        self.lock = asyncio.Lock()

    async def acquire(self, gtask, bot):
        event = asyncio.Event()
        was_queued = False
        
        from bot.state import GLOBAL_TASKS
        async with self.lock:
            GLOBAL_TASKS[gtask.id] = gtask
            if self.active < self.max_concurrent:
                self.active += 1
                event.set()
            else:
                was_queued = True
                self.queue.append((gtask, event))
                await self.update_queue_positions(bot)
                
        try:
            await event.wait()
        except asyncio.CancelledError:
            async with self.lock:
                # Find and remove from queue
                for i, (q_gtask, q_event) in enumerate(self.queue):
                    if q_event is event:
                        self.queue.pop(i)
                        from bot.state import GLOBAL_TASKS
                        if q_gtask.id in GLOBAL_TASKS:
                            del GLOBAL_TASKS[q_gtask.id]
                        await self.update_queue_positions(bot)
                        break
                else:
                    # Not in queue -> event was set right as we got cancelled
                    if event.is_set():
                        if self.queue:
                            next_gtask, next_event = self.queue.pop(0)
                            next_event.set()
                            await self.update_queue_positions(bot)
                        else:
                            self.active -= 1
            raise
        
        # Ping the user in DMs if they were waiting in line
        if was_queued:
            if getattr(gtask, "user_id", None):
                try:
                    await bot.send_message(
                        chat_id=gtask.user_id,
                        text="🚀 **Your queued task has started!**",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass

    async def release(self, bot):
        async with self.lock:
            # Clean up the task from GLOBAL_TASKS
        
            current_asyncio_task = asyncio.current_task()
            keys_to_delete = []
            for k, v in list(GLOBAL_TASKS.items()):
                if getattr(v, "asyncio_task", None) == current_asyncio_task:
                    keys_to_delete.append(k)
            for k in keys_to_delete:
                del GLOBAL_TASKS[k]
            
            if self.queue:
                next_gtask, next_event = self.queue.pop(0)
                next_event.set()
                await self.update_queue_positions(bot)
            else:
                self.active -= 1

    async def update_queue_positions(self, bot):
        for idx, (gtask, _) in enumerate(self.queue, 1):
            prefix = f"{gtask.static_info}\n\n" if getattr(gtask, "static_info", "") else ""
            gtask.message = f"{prefix}⏳ <b>Status:</b> Waiting in Queue\n🔢 <b>Position:</b> <code>{idx}</code>"
        

# Singleton Instance (Reads from config)
from bot.config import MAX_CONCURRENT_TASKS
task_manager = TaskManager(max_concurrent=MAX_CONCURRENT_TASKS)

# Tracks all active progress string blocks for a global queue, keyed by a unique task ID
GLOBAL_TASKS: dict[str, GlobalTask] = {}

# Tracks the most recent /status message and its updater task per chat
CHAT_STATUS_STATE = {}
CHAT_PENALTIES = {}

# Caches ItemDetails models for the web server to render
# Key: UUID string -> Value: RootItemDetailsModel
WEB_CACHE = {}
