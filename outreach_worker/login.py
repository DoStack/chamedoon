"""One-time login for the Chamedoon Telegram account.

Run it yourself in a terminal: Telegram asks for the phone number, the login
code and the 2FA password. The session is written to .env:
  python login.py           -> TG_SESSION, for this worker
  python login.py --server  -> OUTREACH_TG_SESSION, a separate session for the
                               Vercel sender (a session used from two places
                               at once gets revoked by Telegram)
"""

from __future__ import annotations

import argparse
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import API_HASH, API_ID, DEVICE_MODEL, ENV_PATH

SERVER_DEVICE_MODEL = "Chamedoon Outreach (server)"


VERCEL_COMMENT = "# Paste these three lines into Vercel -> Settings -> Environment Variables (Production):"


def save_env(values: dict[str, str], *, comment: str = "") -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    lines = [line for line in lines if line != comment and line.split("=", 1)[0] not in values]
    if comment:
        lines.append(comment)
    lines.extend(f"{key}={value}" for key, value in values.items())
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main(server: bool) -> None:
    if not API_ID or not API_HASH:
        raise SystemExit("Set TG_API_ID and TG_API_HASH in outreach_worker/.env first.")
    device = SERVER_DEVICE_MODEL if server else DEVICE_MODEL
    client = TelegramClient(StringSession(), API_ID, API_HASH, device_model=device)
    await client.start()
    me = await client.get_me()
    session = client.session.save()
    if server:
        key = "OUTREACH_TG_SESSION"
        save_env(
            {"OUTREACH_TG_API_ID": str(API_ID), "OUTREACH_TG_API_HASH": API_HASH, key: session},
            comment=VERCEL_COMMENT,
        )
    else:
        key = "TG_SESSION"
        save_env({key: session})
    await client.disconnect()
    handle = f"@{me.username}" if me.username else "no username"
    print(f"Logged in as {me.first_name} ({handle}). {key} saved to {ENV_PATH}. Keep that file private.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Log the Chamedoon Telegram account in.")
    parser.add_argument("--server", action="store_true", help="Create a separate session for the Vercel sender.")
    asyncio.run(main(parser.parse_args().server))
