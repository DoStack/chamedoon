"""Bot API sends with uploaded files (multipart): the daily album and the PNG fallback. Same token as every housing post."""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from home.bot import API, BotError, bot_token

TIMEOUT = 300


def _multipart(fields: dict, files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, path in files.items():
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{path.name}"\r\nContent-Type: {kind}\r\n\r\n'
        chunks += [head.encode(), path.read_bytes(), b"\r\n"]
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def upload(method: str, fields: dict, files: dict[str, Path]):
    token = bot_token()
    if not token:
        raise BotError("The housing bot token is not set in Home settings.")
    body, kind = _multipart(fields, files)
    request = urllib.request.Request(API.format(token=token, method=method), data=body, headers={"Content-Type": kind}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode())
        except (ValueError, OSError):
            raise BotError(f"HTTP {exc.code}", code=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise BotError(f"Telegram unreachable: {exc}") from exc
    if not data.get("ok"):
        raise BotError(str(data.get("description") or "refused"), code=int(data.get("error_code") or 0))
    return data["result"]


def album(chat, video: Path, cover: Path, thumb: Path, seconds: float, photo: Path, caption: str) -> list[int]:
    """Item 1 the video (cover + thumbnail = final frame, caption), item 2 the stats image (spec §2.0)."""
    media = [
        {"type": "video", "media": "attach://v", "thumbnail": "attach://t", "cover": "attach://c", "caption": caption, "parse_mode": "HTML",
         "width": 1080, "height": 1350, "duration": round(seconds), "supports_streaming": True},
        {"type": "photo", "media": "attach://s"},
    ]
    result = upload("sendMediaGroup", {"chat_id": chat, "media": json.dumps(media, ensure_ascii=False)}, {"v": video, "t": thumb, "c": cover, "s": photo})
    return [int(item["message_id"]) for item in result]


def photos(chat, images: list[Path], caption: str) -> list[int]:
    """Fallback when the video cannot be made: the still card (and the stats image) with the same caption."""
    if len(images) == 1:
        result = upload("sendPhoto", {"chat_id": chat, "caption": caption, "parse_mode": "HTML"}, {"photo": images[0]})
        return [int(result["message_id"])]
    media = [{"type": "photo", "media": f"attach://p{i}"} for i in range(len(images))]
    media[0].update(caption=caption, parse_mode="HTML")
    result = upload("sendMediaGroup", {"chat_id": chat, "media": json.dumps(media, ensure_ascii=False)}, {f"p{i}": p for i, p in enumerate(images)})
    return [int(item["message_id"]) for item in result]
