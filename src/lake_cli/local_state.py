"""Atomic local JSON state with bounded Windows sharing-conflict retries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable, TypeVar


T = TypeVar("T")
_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08, 0.16)


def _windows_host() -> bool:
    return os.name == "nt"


def _retry_sharing(operation: Callable[[], T]) -> T:
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return operation()
        except OSError as exc:
            # Access denied, sharing violation, and lock violation can all be
            # transient while another process has the state file open.
            if (not _windows_host() or getattr(exc, "winerror", None) not in (5, 32, 33)
                    or attempt == len(_RETRY_DELAYS)):
                raise
            time.sleep(_RETRY_DELAYS[attempt])
    raise AssertionError("unreachable")


def read_json(path: Path):
    # Malformed JSON is a state error, not a sharing conflict.
    return json.loads(_retry_sharing(lambda: path.read_text(encoding="utf-8")))


def atomic_json(path: Path, value: dict) -> None:
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
        _retry_sharing(lambda: os.replace(temporary, path))
    finally:
        _retry_sharing(lambda: temporary.unlink(missing_ok=True))
