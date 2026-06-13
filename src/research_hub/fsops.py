"""Shared filesystem operations."""

from __future__ import annotations

import shutil
import time


# Windows antivirus / Search-indexer can briefly hold handles on just-moved
# files or directories. Retry only PermissionError; other OSError subclasses are
# structural failures and should reach the caller immediately.
_MOVE_RETRY_ATTEMPTS = 5
_MOVE_RETRY_BASE_DELAY = 0.1


def robust_move(src: str, dst: str) -> None:
    """Move a path with retry/backoff for transient Windows lock errors."""
    last_exc: PermissionError | None = None
    for attempt in range(_MOVE_RETRY_ATTEMPTS):
        try:
            shutil.move(src, dst)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < _MOVE_RETRY_ATTEMPTS - 1:
                time.sleep(_MOVE_RETRY_BASE_DELAY * (2 ** attempt))
    raise last_exc  # type: ignore[misc]
