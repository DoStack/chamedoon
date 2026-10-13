"""Photo processing: shrink for the app, make a thumbnail, and take a perceptual hash.

Pillow only. The hash is a 64-bit difference hash computed on the photo without its
bottom tenth, where channel watermarks sit. Two copies of a photo that were
recompressed or lightly resized land a few bits apart; unrelated photos land about
32 bits apart.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

FULL_MAX_PX = 1280
THUMB_MAX_PX = 480
MAX_INPUT_PIXELS = 60_000_000
SAME_PHOTO_BITS = 6


class ImageError(ValueError):
    pass


@dataclass
class ProcessedImage:
    full: bytes
    thumb: bytes
    width: int
    height: int
    phash: int
    digest: str


def to_signed(value: int) -> int:
    """Store an unsigned 64-bit hash in a signed BigIntegerField."""
    return value - (1 << 64) if value >= (1 << 63) else value


def to_unsigned(value: int) -> int:
    return value + (1 << 64) if value < 0 else value


def hamming(left: int, right: int) -> int:
    return bin(to_unsigned(left) ^ to_unsigned(right)).count("1")


def same_photo(left: int | None, right: int | None) -> bool:
    return left is not None and right is not None and hamming(left, right) <= SAME_PHOTO_BITS


def dhash(image) -> int:
    from PIL import Image

    width, height = image.size
    cropped = image.crop((0, 0, width, max(1, int(height * 0.9))))
    small = cropped.convert("L").resize((9, 8), Image.LANCZOS)
    pixels = list(getattr(small, "get_flattened_data", None) and small.get_flattened_data() or small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return to_signed(bits)


def _jpeg(image, max_px: int, quality: int) -> tuple[bytes, tuple[int, int]]:
    from PIL import Image

    copy = image.copy()
    copy.thumbnail((max_px, max_px), Image.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue(), copy.size


def process_image(data: bytes) -> ProcessedImage:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types for broken files
        raise ImageError(f"not a readable image: {exc}") from exc
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        image = image.convert("RGB")
    if min(image.size) < 32:
        raise ImageError("image too small")
    full, size = _jpeg(image, FULL_MAX_PX, 82)
    thumb, _ = _jpeg(image, THUMB_MAX_PX, 74)
    return ProcessedImage(
        full=full,
        thumb=thumb,
        width=size[0],
        height=size[1],
        phash=dhash(image),
        digest=hashlib.sha1(full).hexdigest(),
    )
