from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError


class ImageValidationError(ValueError):
    """The upload cannot be decoded as an allowed image."""


class UnsupportedImageError(ImageValidationError):
    """The decoded image format is not supported."""


class ImageLimitError(ImageValidationError):
    """The encoded bytes or decoded dimensions exceed a safety limit."""


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    content: bytes
    suffix: str
    media_type: str
    width: int
    height: int

    def open(self) -> BinaryIO:
        return io.BytesIO(self.content)


_FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
}


def _read_bounded(stream: BinaryIO, max_bytes: int) -> bytes:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    remaining = max_bytes + 1
    chunks: list[bytes] = []
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > max_bytes:
        raise ImageLimitError("image exceeds byte limit")
    return content


def validate_image(
    stream: BinaryIO,
    max_bytes: int,
    max_pixels: int,
) -> ValidatedImage:
    """Fully verify and decode a bounded PNG, JPEG, or WebP stream."""
    if max_pixels < 1:
        raise ValueError("max_pixels must be positive")
    content = _read_bounded(stream, max_bytes)
    if not content:
        raise ImageValidationError("image is empty")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as candidate:
                decoded_format = candidate.format
                candidate.verify()

            if decoded_format not in _FORMATS:
                raise UnsupportedImageError("unsupported image format")

            with Image.open(io.BytesIO(content)) as decoded:
                width, height = decoded.size
                if width * height > max_pixels:
                    raise ImageLimitError("image exceeds decoded pixel limit")
                decoded.load()
    except ImageLimitError:
        raise
    except UnsupportedImageError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageValidationError("image is corrupt or invalid") from exc

    suffix, media_type = _FORMATS[decoded_format]
    return ValidatedImage(
        content=content,
        suffix=suffix,
        media_type=media_type,
        width=width,
        height=height,
    )
