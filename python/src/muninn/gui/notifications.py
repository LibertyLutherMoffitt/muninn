"""Best-effort desktop notifications.

Linux: `notify-send` (libnotify). Everywhere else (Windows, or Linux without
notify-send): a QSystemTrayIcon balloon. Never raises — notifications are
quality-of-life, never critical to delivery.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading

_tray = None
_notify_send = shutil.which("notify-send") if sys.platform == "linux" else None


def init(app, icon) -> None:
    global _tray
    if _notify_send:
        return
    try:
        from PySide6.QtWidgets import QSystemTrayIcon

        if QSystemTrayIcon.isSystemTrayAvailable():
            _tray = QSystemTrayIcon(icon, app)
            _tray.setToolTip("Muninn")
            _tray.show()
    except Exception:
        _tray = None


def notify(title: str, body: str) -> None:
    ns = _notify_send
    if ns:

        def run():
            try:
                subprocess.run(
                    [
                        ns,
                        "--app-name",
                        "Muninn",
                        "--expire-time",
                        "5000",
                        "--category",
                        "im.received",
                        title,
                        body[:200],
                    ],
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()
    elif _tray is not None:
        try:
            _tray.showMessage(title, body[:200])
        except Exception:
            pass
