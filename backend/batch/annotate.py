from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from backend.engine import Detection


@dataclass(frozen=True)
class AnnotationStyle:
    color: tuple[int, int, int]
    width: int


_STYLES = {
    "no_helmet": AnnotationStyle((220, 38, 38), 4),
    "person": AnnotationStyle((86, 112, 138), 2),
    "helmet": AnnotationStyle((76, 138, 102), 2),
}


def _color(label: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    return tuple(64 + channel % 160 for channel in digest[:3])


def _style(label: str) -> AnnotationStyle:
    return _STYLES.get(label, AnnotationStyle(_color(label), 2))


def _coordinate(value: float, maximum: int) -> int:
    if not math.isfinite(value):
        return 0
    return max(0, min(maximum, int(round(value))))


def annotate(image: np.ndarray, detections: list[Detection]) -> np.ndarray:
    """Draw deterministic, bounds-safe labels on a BGR OpenCV image."""
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("annotation requires a three-channel image")
    height, width = image.shape[:2]
    canvas = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()

    for detection in detections:
        x1, y1, x2, y2 = detection.bbox
        left, right = sorted((_coordinate(x1, width - 1), _coordinate(x2, width - 1)))
        top, bottom = sorted(
            (_coordinate(y1, height - 1), _coordinate(y2, height - 1))
        )
        style = _style(detection.label)
        draw.rectangle(
            (left, top, right, bottom), outline=style.color, width=style.width
        )
        label = f"{detection.label} {detection.confidence:.2f}"
        label_box = draw.textbbox((0, 0), label, font=font)
        label_width = label_box[2] - label_box[0] + 4
        label_height = label_box[3] - label_box[1] + 4
        if top >= label_height:
            label_top, label_bottom = top - label_height, top
        else:
            label_top, label_bottom = top, min(height - 1, top + label_height)
        label_right = min(width - 1, left + label_width)
        draw.rectangle((left, label_top, label_right, label_bottom), fill=style.color)
        draw.text((left + 2, label_top + 1), label, fill=(255, 255, 255), font=font)

    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
