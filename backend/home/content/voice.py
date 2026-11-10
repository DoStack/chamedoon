"""Voice-over and music for channel videos (spec §5). $0 only: Gemini TTS or edge-tts for dailies, ElevenLabs free tier for weeklies.

The number check is a hard gate: the clip is transcribed and every number must equal the card's, or the video goes out with music only.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import json
import logging
import os
import re
import subprocess
import urllib.request
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TTS_MODELS = ("gemini-2.5-flash-preview-tts",)
STT_MODELS = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash", "gemini-flash-latest")
ELEVEN_VOICE = "FGY2WhTYpPnrIDTdsKH5"  # Laura, chosen by Mohammad for the promo
EDGE_VOICE = "fa-IR-DilaraNeural"
VOICE_START = 0.5
TAIL = 1.6


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def engine() -> str:
    """`gemini:<voice>`, `edge` or `eleven`; CONTENT_VOICE on the worker. Default: Gemini Kore."""
    return _env("CONTENT_VOICE") or "gemini:Kore"


def weekly_engine() -> str:
    """CONTENT_VOICE_WEEKLY (e.g. `eleven` for Laura while the free quota lasts); default: the daily engine."""
    return _env("CONTENT_VOICE_WEEKLY") or engine()


def _post(url: str, body: dict, headers: dict, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _gemini_tts(text: str, voice: str, out: Path) -> Path:
    body = {
        "contents": [{"parts": [{"text": "Say in a warm, friendly, casual Persian tone: " + text}]}],
        "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}},
    }
    last = None
    for key, model in [(key, model) for key in _gemini_keys() for model in TTS_MODELS]:
        try:
            data = json.loads(_post(GEMINI.format(model=model), body, {"x-goog-api-key": key}))
            pcm = base64.b64decode(data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
            wav = out.with_suffix(".wav")
            with wave.open(str(wav), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(24000)
                f.writeframes(pcm)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-b:a", "128k", str(out)], check=True)
            wav.unlink(missing_ok=True)
            return out
        except Exception as exc:  # noqa: BLE001 - try the next model, then the caller falls back
            last = exc
    raise RuntimeError(f"gemini tts failed: {type(last).__name__}: {str(last)[:200]}")


def _edge_tts(text: str, out: Path) -> Path:
    import edge_tts

    asyncio.run(edge_tts.Communicate(text, EDGE_VOICE).save(str(out)))
    return out


def _eleven_tts(text: str, out: Path) -> Path:
    body = {
        "text": text,
        "model_id": "eleven_v4",
        "language_code": "fa",
        "voice_settings": {"stability": 0.3, "similarity_boost": 0.75, "style": 0.55, "speed": 1.0},
    }
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE}?output_format=mp3_44100_128"
    out.write_bytes(_post(url, body, {"xi-api-key": _env("ELEVENLABS_API_KEY")}))
    return out


def speak(text: str, out: Path, choice: str | None = None) -> tuple[Path, str]:
    """TTS with the chosen engine, falling back to edge-tts (free, no key). Returns the clip and the engine used."""
    choice = choice or engine()
    try:
        if choice.startswith("gemini"):
            return _gemini_tts(text, choice.partition(":")[2] or "Kore", out), choice
        if choice == "eleven":
            return _eleven_tts(text, out), choice
    except Exception as exc:  # noqa: BLE001
        logger.warning("tts %s failed, falling back to edge: %s", choice, exc)
    return _edge_tts(text, out), "edge"


def transcribe(clip: Path) -> str:
    body = {
        "contents": [
            {
                "parts": [
                    {"text": "Transcribe this Persian audio exactly. Write every number as Western digits (0-9), without thousands separators. Output only the transcript."},
                    {"inline_data": {"mime_type": "audio/mp3", "data": base64.b64encode(clip.read_bytes()).decode()}},
                ]
            }
        ]
    }
    last = None
    for key in _gemini_keys():
        for model in STT_MODELS:
            try:
                data = json.loads(_post(GEMINI.format(model=model), body, {"x-goog-api-key": key}, timeout=90))
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as exc:  # noqa: BLE001 - retired model, quota or overload: next model, then next key
                last = exc
    raise RuntimeError(f"transcription failed: {type(last).__name__}: {str(last)[:120]}")


def _gemini_keys() -> list[str]:
    """GEMINI_API_KEY, then GEMINI_API_KEY_2 (another free project) when the first is out of quota."""
    return [key for key in (_env("GEMINI_API_KEY"), _env("GEMINI_API_KEY_2")) if key]


_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def numbers_in(text: str) -> list[int]:
    """Every number in a transcript, digits only (the prompt asks for digits); «1,900» and «۱٬۹۰۰» read as 1900."""
    text = text.translate(_FA_DIGITS)
    text = re.sub(r"(?<=\d)[,٬](?=\d{3}\b)", "", text)
    return [int(found) for found in re.findall(r"\d+", text)]


def numbers_match(expected: list[int], transcript: str) -> bool:
    """Every number said equals the card's, none missing, none extra. «یه» (a/one) may come back as 1, so a stray 1 is ignored."""
    want = collections.Counter(int(n) for n in expected)
    heard = collections.Counter(numbers_in(transcript))
    if 1 not in want:
        heard.pop(1, None)
    return heard == want


def checked_voice(text: str, expected: list[int], out: Path, choice: str | None = None) -> tuple[Path | None, dict]:
    """A voice clip whose numbers were heard right, or None. One regeneration on a mismatch, then give up (music only)."""
    log: dict = {"script": text, "expected": expected, "tries": []}
    for attempt in range(2):
        clip = out.with_name(f"{out.stem}_{attempt}{out.suffix}")
        try:
            clip, used = speak(text, clip, choice)
            heard = transcribe(clip)
        except Exception as exc:  # noqa: BLE001
            log["tries"].append({"error": str(exc)[:200]})
            continue
        ok = numbers_match(expected, heard)
        log["tries"].append({"engine": used, "heard": heard, "numbers": numbers_in(heard), "ok": ok})
        if ok:
            log["ok"] = True
            return clip, log
    log["ok"] = False
    return None, log


def mix(silent: Path, voice: Path | None, music: Path, out: Path, anim_seconds: float) -> float:
    """Video + voice from 0.5 s + music from 0 s ducked under the voice, final frame held until voice end + 1.6 s, loudnorm −15 LUFS."""
    from home.content.render import duration

    voice_len = duration(voice) if voice else 0.0
    total = round(max(anim_seconds, VOICE_START + voice_len + TAIL), 2)
    hold = round(total - anim_seconds, 2)
    music_chain = f"atrim=0:{total},asetpts=PTS-STARTPTS,afade=t=in:d=0.02,afade=t=out:st={total - 1.2}:d=1.2,volume=0.55"
    if voice:
        delay = int(VOICE_START * 1000)
        graph = (
            f"[1:a]adelay={delay}|{delay},apad=whole_dur={total},atrim=0:{total},volume=1.6,asplit=2[v1][v2];"
            f"[2:a]{music_chain}[m];"
            f"[m][v1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[md];"
            f"[md][v2]amix=inputs=2:normalize=0,loudnorm=I=-15:TP=-1.5:LRA=11[a];"
            f"[0:v]tpad=stop_mode=clone:stop_duration={hold}[vv]"
        )
        inputs = ["-i", str(silent), "-i", str(voice), "-i", str(music)]
    else:
        graph = f"[1:a]{music_chain},loudnorm=I=-15:TP=-1.5:LRA=11[a];[0:v]tpad=stop_mode=clone:stop_duration={hold}[vv]"
        inputs = ["-i", str(silent), "-i", str(music)]
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[vv]", "-map", "[a]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast", "-threads", "2",
         "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", "-t", str(total), str(out)],
        check=True,
    )
    return total
