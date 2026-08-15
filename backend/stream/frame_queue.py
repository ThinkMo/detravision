from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Frame:
    camera_id: int
    captured_at: float
    image: Any


class DropOldestQueue:
    """Bounded queue that favors freshness over completeness."""

    def __init__(self, maxsize: int):
        if maxsize < 1:
            raise ValueError("maxsize must be positive")
        self._queue: queue.Queue[Frame] = queue.Queue(maxsize=maxsize)
        self.dropped = 0

    def put(self, item: Frame) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self.dropped += 1
            except queue.Empty:
                pass
            self._queue.put_nowait(item)

    def get(self, timeout: float | None = None) -> Frame:
        return self._queue.get(timeout=timeout)

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()
