"""
gen_session.py - One-time helper to generate a Telethon StringSession.

Run this ONCE on your local PC (not VPS) to log in interactively.
Copy the printed session string into TELEGRAM_SESSION_STRING in .env.
The session string lets the bot run headlessly on the VPS without
needing phone/SMS verification again.

Usage:
    python -m bot.gen_session
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from bot.config import Config


async def main():
    print("=" * 60)
    print("  JKT48 Live Bot - Telegram Session Generator")
    print("=" * 60)
    print(f"\nUsing API ID: {Config.TELEGRAM_API_ID}")
    print(f"Phone: {Config.TELEGRAM_PHONE}\n")

    async with TelegramClient(StringSession(), Config.TELEGRAM_API_ID, Config.TELEGRAM_API_HASH) as client:
        await client.start(phone=Config.TELEGRAM_PHONE)
        session_str = client.session.save()
        me = await client.get_me()
        print(f"\n✅ Logged in as: {me.first_name} (@{me.username})")
        print("\n" + "=" * 60)
        print("  Copy this session string to your .env file as:")
        print("  TELEGRAM_SESSION_STRING=<paste below>")
        print("=" * 60)
        print(f"\n{session_str}\n")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
