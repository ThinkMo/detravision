from __future__ import annotations

import threading
import time


class LoginRateLimiter:
    """Bound failed logins by normalized username and source IP."""

    WINDOW_SECONDS = 15 * 60
    MAX_FAILURES = 5

    def __init__(self) -> None:
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str, ip: str) -> tuple[str, str]:
        return username.casefold(), ip

    def allow(self, username: str, ip: str, now: float | None = None) -> bool:
        instant = time.monotonic() if now is None else now
        key = self._key(username, ip)
        with self._lock:
            failures = [
                value
                for value in self._failures.get(key, ())
                if instant - value < self.WINDOW_SECONDS
            ]
            if failures:
                self._failures[key] = failures
            else:
                self._failures.pop(key, None)
            return len(failures) < self.MAX_FAILURES

    def record_failure(self, username: str, ip: str, now: float | None = None) -> None:
        instant = time.monotonic() if now is None else now
        key = self._key(username, ip)
        with self._lock:
            failures = [
                value
                for value in self._failures.get(key, ())
                if instant - value < self.WINDOW_SECONDS
            ]
            failures.append(instant)
            self._failures[key] = failures[-self.MAX_FAILURES :]

    def record_failure_if_allowed(
        self,
        username: str,
        ip: str,
        now: float | None = None,
    ) -> bool:
        """Atomically consume one failure slot, if one remains."""
        instant = time.monotonic() if now is None else now
        key = self._key(username, ip)
        with self._lock:
            failures = [
                value
                for value in self._failures.get(key, ())
                if instant - value < self.WINDOW_SECONDS
            ]
            if len(failures) >= self.MAX_FAILURES:
                self._failures[key] = failures
                return False
            failures.append(instant)
            self._failures[key] = failures
            return True

    def reset(self, username: str, ip: str) -> None:
        with self._lock:
            self._failures.pop(self._key(username, ip), None)
