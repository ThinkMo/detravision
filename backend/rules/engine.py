from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from backend.engine import Detection


@dataclass(frozen=True, slots=True)
class SemanticEvent:
    event_type: str
    confidence: float
    bbox: tuple[float, float, float, float]


def center_in_polygon(bbox: tuple[float, float, float, float], polygon: list[list[float]]) -> bool:
    if len(polygon) < 3:
        return True
    x = (bbox[0] + bbox[2]) / 2
    y = (bbox[1] + bbox[3]) / 2
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        crosses = (y1 > y) != (y2 > y)
        if crosses and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
        previous = current
    return inside


def center_inside(inner: Detection, outer_bbox: tuple[float, float, float, float]) -> bool:
    x = (inner.bbox[0] + inner.bbox[2]) / 2
    y = (inner.bbox[1] + inner.bbox[3]) / 2
    return outer_bbox[0] <= x <= outer_bbox[2] and outer_bbox[1] <= y <= outer_bbox[3]


class RuleEngine:
    def __init__(self, required_hits: int = 3, window_seconds: float = 5, cooldown_seconds: float = 30):
        self.required_hits = required_hits
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._hits: dict[tuple[int, str], deque[float]] = defaultdict(deque)
        self._last_emitted: dict[tuple[int, str], float] = {}

    @staticmethod
    def semantic_candidates(detections: list[Detection]) -> list[SemanticEvent]:
        candidates = [
            SemanticEvent(d.label, d.confidence, d.bbox)
            for d in detections
            if d.label in {"fire", "smoke"}
        ]
        persons = [d for d in detections if d.label == "person"]
        unsafe_heads = [d for d in detections if d.label == "no_helmet"]
        for person in persons:
            x1, y1, x2, y2 = person.bbox
            head_region = (x1, y1, x2, y1 + (y2 - y1) * 0.4)
            matches = [head for head in unsafe_heads if center_inside(head, head_region)]
            if matches:
                best = max(matches, key=lambda detection: detection.confidence)
                candidates.append(SemanticEvent("no_helmet", best.confidence, person.bbox))
        return candidates

    def evaluate(
        self,
        camera_id: int,
        detections: list[Detection],
        roi: list[list[float]] | None = None,
        allowed_classes: list[str] | None = None,
        now: float | None = None,
    ) -> list[SemanticEvent]:
        current = time.monotonic() if now is None else now
        emitted = []
        for event in self.semantic_candidates(detections):
            if allowed_classes and event.event_type not in allowed_classes:
                continue
            if roi and not center_in_polygon(event.bbox, roi):
                continue
            key = (camera_id, event.event_type)
            hits = self._hits[key]
            hits.append(current)
            while hits and hits[0] < current - self.window_seconds:
                hits.popleft()
            last = self._last_emitted.get(key, float("-inf"))
            if len(hits) >= self.required_hits and current - last >= self.cooldown_seconds:
                emitted.append(event)
                self._last_emitted[key] = current
                hits.clear()
        return emitted
