"""Scheduled stats posts for the housing channels (spec: projects/chamedoon/exec/CONTENT-SPEC.md).

data      numbers from the listings (dedupe, type guard, medians, deal of the day)
text      Persian digits, Jalali dates, captions, voice scripts, start params
render    HTML card -> PNG and frame-by-frame video (Playwright + ffmpeg)
voice     free TTS, transcription number check, music mix
send      sendMediaGroup / sendPhoto with uploaded files
schedule  the 15-minute tick: what is due per chat, idempotent by home_contentpost
"""
