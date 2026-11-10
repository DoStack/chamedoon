from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


API_ID = int(env("TG_API_ID", "0") or "0")
API_HASH = env("TG_API_HASH")
SESSION = env("TG_SESSION")
API_BASE_URL = env("API_BASE_URL", "http://localhost:8000").rstrip("/")
OUTREACH_SERVICE_SECRET = env("OUTREACH_SERVICE_SECRET")
# The sending accounts. Each has its own session in <NAME>_TG_SESSION: a session lives in one place only.
ACCOUNTS = [name.strip().lower() for name in env("OUTREACH_ACCOUNTS", "niloofar,sara,monis").split(",") if name.strip()]
# The intro videos sent after a first message: <VIDEO_DIR>/seeker.mp4 and owner.mp4.
VIDEO_DIR = env("VIDEO_DIR", "/app/videos")
# Shown in the account's Settings -> Devices list, so the session is easy to recognise.
DEVICE_MODEL = "Chamedoon Outreach"


def account_device(name: str) -> dict:
    """How this account shows up in Telegram's device list. <NAME>_TG_DEVICE, _TG_SYSTEM and _TG_APP give it a real
    phone (saeid, 2026-11-08); without them the account keeps DEVICE_MODEL as before (Mohammad: do not touch them)."""
    prefix = name.upper()
    device = env(f"{prefix}_TG_DEVICE")
    if not device:
        return {"device_model": DEVICE_MODEL}
    settings = {"device_model": device, "lang_code": env(f"{prefix}_TG_LANG", "fa"), "system_lang_code": env(f"{prefix}_TG_SYSTEM_LANG", "fa-IR")}
    if env(f"{prefix}_TG_SYSTEM"):
        settings["system_version"] = env(f"{prefix}_TG_SYSTEM")
    if env(f"{prefix}_TG_APP"):
        settings["app_version"] = env(f"{prefix}_TG_APP")
    return settings


def account_config(name: str) -> tuple[int, str, str]:
    """API id, API hash and session of one account. The id and hash fall back to the shared pair."""
    prefix = name.upper()
    api_id = int(env(f"{prefix}_TG_API_ID", "0") or "0") or API_ID
    api_hash = env(f"{prefix}_TG_API_HASH") or API_HASH
    return api_id, api_hash, env(f"{prefix}_TG_SESSION")
