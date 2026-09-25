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
MIN_DELAY_SECONDS = int(env("MIN_DELAY_SECONDS", "150") or "150")
MAX_DELAY_SECONDS = int(env("MAX_DELAY_SECONDS", "330") or "330")
IDLE_SECONDS = int(env("IDLE_SECONDS", "600") or "600")
# Shown in the account's Settings -> Devices list, so the session is easy to recognise.
DEVICE_MODEL = "Chamedoon Outreach"
