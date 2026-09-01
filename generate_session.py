import asyncio
from pyrogram import Client

async def main():
    print("=== Hydrogram Session Generator ===")
    api_id = input("Enter your API ID: ")
    api_hash = input("Enter your API HASH: ")
    
    # In-memory session so it just prints the string and doesn't save a .session file
    async with Client("my_account", api_id=api_id, api_hash=api_hash, in_memory=True) as app:
        session_string = await app.export_session_string()
        print("\n✅ Successfully Generated Session String!\n")
        print("Copy the string below and paste it into your .env file as USER_SESSION_STRING:\n")
        print(session_string)
        print("\n⚠️ Keep this string safe! It gives full access to your Telegram account.")

if __name__ == "__main__":
    asyncio.run(main())
