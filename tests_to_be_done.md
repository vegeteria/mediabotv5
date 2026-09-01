# 🧪 QA Testing Checklist

Please run through these tests to ensure all the new queue and dashboard changes are working flawlessly.

## 1. 🛡️ Tiered Access & Authorization
- [ ] **DM Blocking:** Have a *normal group member* (not an Owner/Authorized user) try to DM the bot with `/movie <url>`.
  - **Expected:** The bot should reject the command and reply: "⛔ You are not an Admin. Please use my commands inside the group chat!" with the group chat link.
- [ ] **Group Chat Initialization Check:** Have a *normal group member* who has **never DMed the bot** try to use `/mbmovie` inside the group chat.
  - **Expected:** The bot should reply directly to their message, `@mention` them, and say: "⚠️ Action Required! You must start the bot in private chat before using it here."
- [ ] **Admin Bypass:** Have the Owner (or an explicitly `/adduser`'d Admin) use `/movie` inside their DM.
  - **Expected:** It should work perfectly since Admins bypass group-chat restrictions.

## 2. 🚦 The Global Task Queue
- [ ] **Max Concurrency Limit:** Set `MAX_CONCURRENT_TASKS=2` in your `.env` (temporarily for testing). 
- [ ] **Trigger 3 Tasks:** Ask 3 different people (or use your own accounts) to start 3 heavy downloads simultaneously in the group chat (e.g. 3 different `/movie` links).
  - **Expected:** 
    - The first 2 tasks should say `📥 Downloading...` on the dashboard.
    - The 3rd task should say `⏳ Status: Waiting in Queue | 🔢 Position: 1` on the dashboard.
- [ ] **Queue Advancement & DM Ping:** Wait for one of the first 2 tasks to finish.
  - **Expected:** 
    - The 3rd task should automatically jump out of the queue and start `📥 Downloading...` on the dashboard.
    - The user who owned the 3rd task should receive a DM saying: `🚀 Your queued task has started!`

## 3. 🖥️ Dual-Dashboards & Pagination
- [ ] **Dual Dashboard Spawning:** Trigger a download inside the group chat.
  - **Expected:** The live `/status` dashboard should spawn at the bottom of the **Group Chat** AND at the bottom of your **Private DM** simultaneously.
- [ ] **Context-Aware Dashboard Data:** Check both dashboards.
  - **Expected:**
    - The Group Chat dashboard should show `👤 User: @username` next to the task.
    - The Private DM dashboard should NOT show the user tag (since it's private), and it should ONLY show your own tasks (if other people are downloading things, you shouldn't see their tasks in your DM dashboard).
- [ ] **Pagination Test:** Queue up 4 or more tasks in the group chat (with `MAX_CONCURRENT_TASKS=3`).
  - **Expected:** The Group Chat dashboard should say `(Page 1/2)` at the top, and feature `Next ➡️` and `⬅️ Prev` buttons. Click them to ensure the pages flip back and forth correctly.

## 4. 📬 Final Delivery
- [ ] **Dual Completion Messages:** Let a download finish completely (so it uploads to Google Drive).
  - **Expected:**
    - The Group Chat should get a simple message: `🎉 Upload Complete for @username!`
    - Your Private DM should get the detailed message containing the **Index Link** and **Cloud Link**.
