"""Single-instance lock using flock(2).

Prevents concurrent executions of the orchestrator by acquiring an exclusive
file lock on a well-known path.  Mirrors the bash pattern:

    exec 200>"$LOCK_FILE"
    if ! flock -n 200; then
        echo "ERROR: Another instance is already running (lock: ${LOCK_FILE})"
        exit 1
    fi
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)


class LockFile:
    """Exclusive file lock using ``fcntl.flock``.

    Supports both manual ``acquire()``/``release()`` and context-manager usage.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._fh: IO[str] | None = None

    # -- public API ----------------------------------------------------------

    def acquire(self) -> None:
        """Acquire an exclusive lock.

        Raises
        ------
        RuntimeError
            If the lock is already held by another process.
        """
        if self._fh is not None:
            return  # Already acquired (idempotent)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "w")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            self._fh.close()
            self._fh = None
            raise RuntimeError(
                f"Another instance is already running (lock: {self._path})"
            )
        logger.debug("Acquired lock: %s", self._path)

    def release(self) -> None:
        """Release the lock (idempotent)."""
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass  # Best-effort release
        finally:
            fh = self._fh
            self._fh = None
            try:
                fh.close()
            except OSError:
                pass
        logger.debug("Released lock: %s", self._path)

    def __enter__(self) -> "LockFile":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire_lock(path: Path | str) -> IO[str]:
    """Convenience function: acquire an exclusive lock and return the file handle.

    The caller is responsible for closing the returned handle (which releases
    the lock).  Useful when you need the raw fd for ``exec``-style patterns.

    Raises
    ------
    RuntimeError
        If the lock is already held.
    """
    lock = LockFile(path)
    lock.acquire()
    return lock._fh  # type: ignore[return-value]
