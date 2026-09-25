"""One-time login for the Chamedoon Telegram account.

Run it yourself in a terminal: Telegram asks for the phone number, the login
code and the 2FA password. The session is written to .env as TG_SESSION.
"""

from __future__ import annotations

import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import API_HASH, API_ID, DEVICE_MODEL, ENV_PATH


def save_session(value: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    lines = [line for line in lines if not line.startswith("TG_SESSION=")]
    lines.append(f"TG_SESSION={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    if not API_ID or not API_HASH:
        raise SystemExit("Set TG_API_ID and TG_API_HASH in outreach_worker/.env first.")
    client = TelegramClient(StringSession(), API_ID, API_HASH, device_model=DEVICE_MODEL)
    await client.start()
    me = await client.get_me()
    save_session(client.session.save())
    await client.disconnect()
    handle = f"@{me.username}" if me.username else "no username"
    print(f"Logged in as {me.first_name} ({handle}). Session saved to {ENV_PATH}. Keep that file private.")


if __name__ == "__main__":
    asyncio.run(main())
