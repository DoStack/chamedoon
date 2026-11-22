"""One-time login for the Chamedoon Telegram account.

Run it yourself in a terminal: Telegram asks for the phone number, the login
code and the 2FA password. The session is written to .env:
  python login.py --account monis -> MONIS_TG_SESSION, for the worker on bigde-vps
  python login.py --account x --own-api -> also asks for this account's own api_id
                               and api_hash (my.telegram.org) and saves them as
                               X_TG_API_ID / X_TG_API_HASH; nothing is printed
  python login.py           -> TG_SESSION, for this worker
  python login.py --account x --qr -> log in by QR code instead of phone + code:
                               the account owner scans it in Telegram ->
                               Settings -> Devices -> Link Desktop Device
  python login.py --server  -> OUTREACH_TG_SESSION, a separate session for the
                               Vercel sender (a session used from two places
                               at once gets revoked by Telegram)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import API_HASH, API_ID, DEVICE_MODEL, ENV_PATH, account_config, account_device

SERVER_DEVICE_MODEL = "Chamedoon Outreach (server)"


VERCEL_COMMENT = "# Paste these three lines into Vercel -> Settings -> Environment Variables (Production):"


def save_env(values: dict[str, str], *, comment: str = "") -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    lines = [line for line in lines if line != comment and line.split("=", 1)[0] not in values]
    if comment:
        lines.append(comment)
    lines.extend(f"{key}={value}" for key, value in values.items())
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def show_qr(url: str) -> None:
    try:
        import qrcode

        code = qrcode.QRCode(border=1)
        code.add_data(url)
        code.print_ascii(invert=True)
    except ImportError:
        print("(pip install qrcode to see the QR here)")
    print(url)


async def qr_start(client: TelegramClient) -> None:
    """Show a QR until the account owner scans it; it is renewed every ~30 s. Then the 2FA password if any."""
    from telethon.errors import SessionPasswordNeededError

    await client.connect()
    if await client.is_user_authorized():
        return
    login = await client.qr_login()
    while True:
        print("
Scan in Telegram on the account's phone: Settings -> Devices -> Link Desktop Device")
        show_qr(login.url)
        try:
            await login.wait(timeout=30)
            return
        except asyncio.TimeoutError:
            await login.recreate()
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("2FA password (not shown): "))
            return


async def main(server: bool, account: str = "", own_api: bool = False, qr: bool = False) -> None:
    api_id, api_hash = API_ID, API_HASH
    if account:
        api_id, api_hash, _session = account_config(account)
    if own_api and account:
        typed_id = input("api_id of this account (my.telegram.org), Enter to keep the shared one: ").strip()
        if typed_id:
            api_id, api_hash = int(typed_id), getpass.getpass("api_hash (not shown): ").strip()
            save_env({f"{account.upper()}_TG_API_ID": str(api_id), f"{account.upper()}_TG_API_HASH": api_hash})
    if not api_id or not api_hash:
        raise SystemExit("Set TG_API_ID and TG_API_HASH in outreach_worker/.env first.")
    # The same device the worker will use for this account: Telegram ties the session to it.
    device = {"device_model": SERVER_DEVICE_MODEL} if server else (account_device(account) if account else {"device_model": DEVICE_MODEL})
    client = TelegramClient(StringSession(), api_id, api_hash, **device)
    if qr:
        await qr_start(client)
    else:
        await client.start()
    me = await client.get_me()
    session = client.session.save()
    if account:
        key = f"{account.upper()}_TG_SESSION"
        save_env({key: session})
    elif server:
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
    parser.add_argument("--account", default="", help="Save the session as <ACCOUNT>_TG_SESSION for the multi-account worker, e.g. monis.")
    parser.add_argument("--own-api", action="store_true", help="Ask for this account's own api_id and api_hash and save them.")
    parser.add_argument("--qr", action="store_true", help="Log in by QR code (scanned on the account's phone) instead of a login code.")
    args = parser.parse_args()
    asyncio.run(main(args.server, args.account.strip().lower(), args.own_api, args.qr))
