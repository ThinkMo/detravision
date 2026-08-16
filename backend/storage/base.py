from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import BinaryIO


class FileStorage(ABC):
    """Storage boundary for files referenced by opaque relative keys."""

    @abstractmethod
    def save(self, stream: BinaryIO, namespace: str, suffix: str) -> str:
        """Persist a stream and return an opaque storage key."""

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        """Open a previously saved key for binary reading."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete a key if present."""

    @abstractmethod
    def iter_keys(self, namespace: str) -> Iterable[str]:
        """Enumerate persisted keys below a namespace for orphan recovery."""
