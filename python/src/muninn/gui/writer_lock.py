"""Advisory writer lock — first instance to acquire it owns the BT stack."""

import pathlib
import sys


class WriterLock:
    def __init__(self):
        state_dir = pathlib.Path.home() / ".local" / "state" / "muninn"
        state_dir.mkdir(parents=True, exist_ok=True)
        self._path = state_dir / ".writer.lock"
        self._fd = None
        self._held = False

    def try_acquire(self) -> bool:
        try:
            self._fd = open(self._path, "w")
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
