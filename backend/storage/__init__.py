"""Blob storage for generated documents.

An interface, because the MVP writes to local disk and production will not. Nothing above
this layer knows which.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from backend.core.config import settings

__all__ = ["LocalStorage", "Storage", "get_storage"]


class Storage(Protocol):
    def put(self, key: str, payload: bytes) -> str: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


class LocalStorage:
    """Development storage. Keys are opaque; they never come from user input."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys are generated as `<uuid>.docx`. Refuse anything that could escape the root.
        if "/" in key or "\\" in key or ".." in key:
            raise ValueError(f"illegal storage key {key!r}")
        return self._root / key

    def put(self, key: str, payload: bytes) -> str:
        self._path(key).write_bytes(payload)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


def get_storage() -> Storage:
    return LocalStorage(Path(settings.storage_dir))
