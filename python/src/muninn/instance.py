"""Single-instance lock (per data dir) — one Muninn owns the radio + DB.

Taken by every front end (GUI, terminal, bot) through Node, so a second copy
fails with a clear message instead of fighting over Bluetooth and the DB.
"""

import os
import sys
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self._path = state_dir / ".instance.lock"
        self._fd = None
        self._held = False

    def try_acquire(self) -> bool:
        try:
            self._fd = open(self._path, "a+")
            self._fd.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd.truncate()
                self._fd.write(str(os.getpid()))  # for the error message
                self._fd.flush()
            self._held = True
            return True
        except (OSError, IOError):
            if self._fd:
                self._fd.close()
                self._fd = None
            return False

    def release(self) -> None:
        if not (self._fd and self._held):
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except Exception:
            pass
        self._fd.close()
        self._fd = None
        self._held = False

    def holder(self) -> str:
        try:
            return self._path.read_text().strip()
        except OSError:
            return ""
