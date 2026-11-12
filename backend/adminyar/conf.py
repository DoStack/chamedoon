"""Config from the environment: ADMINYAR_BOT_TOKEN, ADMINYAR_ALLOWED_IDS, ADMINYAR_OWNER_IDS (comma-separated ids)."""

from __future__ import annotations

import os
from pathlib import Path

# Who each team account is, by Telegram user id. The bot greets with this name and later hands out that account's tasks.
TEAM_ACCOUNTS = {
    8854699370: ("sara", "سارا"),
    8785728624: ("saeid", "سعید"),
    8851362395: ("niloofar", "نیلوفر"),
    7555556445: ("monis", "مونس"),
}


def load_env_file(path: str | Path) -> None:
    """KEY=VALUE lines into os.environ (existing values win). Values are never printed."""
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _ids(name: str) -> set[int]:
    out = set()
    for part in (os.environ.get(name) or "").replace(" ", "").split(","):
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


def token() -> str:
    return (os.environ.get("ADMINYAR_BOT_TOKEN") or "").strip()


def owner_ids() -> set[int]:
    return _ids("ADMINYAR_OWNER_IDS")


def allowed_ids() -> set[int]:
    return _ids("ADMINYAR_ALLOWED_IDS") | owner_ids()
