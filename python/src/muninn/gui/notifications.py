"""Best-effort desktop notifications.

Linux uses `notify-send` (libnotify) which every modern desktop ships. If it
is missing, we silently no-op — notifications are quality-of-life, never
critical to message delivery.

Windows support is deferred (the GUI is Linux-first); a Win32 toast helper
can land alongside `bt/winrt.py` hardware validation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

_APP_NAME = "Muninn"

# Cache the lookup so we don't re-shutil.which on every message.
_NOT_PROBED: object = object()
_notify_send: str | None | object = _NOT_PROBED


def _resolve() -> str | None:
    global _notify_send
    if _notify_send is _NOT_PROBED:
        _notify_send = shutil.which("notify-send")
    return _notify_send if isinstance(_notify_send, str) else None


def notify(title: str, body: str) -> None:
    """Fire a transient desktop notification. Never raises."""
    if sys.platform != "linux":
        return
    path = _resolve()
    if not path:
        return

    def _run() -> None:
        try:
            subprocess.run(
                [
                    path,
                    "--app-name",
                    _APP_NAME,
                    "--expire-time",
                    "5000",
                    "--category",
                    "im.received",
                    title,
                    body,
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ},
            )
        except Exception:
            pass

    # notify-send is fast but still touches D-Bus; keep the GUI thread
    # off the wait by spawning a daemon thread.
    threading.Thread(target=_run, daemon=True).start()
