"""Keep running in the background with a tray icon (Linux, Windows, Nix).

Closing the window then only hides it: Bluetooth links, relaying and
notifications keep going. Only when a system tray actually exists — without
one a hidden window could never be brought back, so closing quits as before.
Also: start at login (`:autostart on`), hidden (`--background`).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

ICON = Path(__file__).resolve().parent / "assets" / "muninn.svg"


def icon() -> QIcon:
    return QIcon(str(ICON))


def available() -> bool:
    return QSystemTrayIcon.isSystemTrayAvailable()


class Tray(QObject):
    def __init__(self, app, window, node, run_command):
        super().__init__(app)
        self.app, self.window, self.node = app, window, node
        self.tray = QSystemTrayIcon(icon(), app)
        menu = QMenu()
        for label, fn in (
            ("Show Muninn", self.show_window),
            ("Find people nearby (5 min)", lambda: run_command("find")),
            (None, None),
            ("Quit Muninn", app.quit),
        ):
            if label is None:
                menu.addSeparator()
                continue
            act = QAction(label, menu)
            act.triggered.connect(fn)
            menu.addAction(act)
        self._menu = menu  # keep a reference
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(4000)
        self.refresh()

    def _activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.window.isVisible() and self.window.isActive():
                self.window.hide()
            else:
                self.show_window()

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()

    def refresh(self) -> None:
        try:
            unread = sum(self.node.storage.unread_counts().values())
            n = len(self.node.mesh.connected())
        except Exception:
            return
        tip = f"Muninn — {n} connected"
        if unread:
            tip += f" · {unread} unread"
        self.tray.setToolTip(tip)

    def message(self, title: str, body: str) -> None:
        self.tray.showMessage(title, body, icon(), 5000)


# --- start at login -------------------------------------------------------------


def _command() -> list[str]:
    if exe := shutil.which("muninn-gui"):  # nix profile / pip install
        return [exe, "--background"]
    if getattr(sys, "frozen", False):  # standalone build
        return [sys.executable, "--background"]
    return [sys.executable, "-m", "muninn.gui.main", "--background"]


def _desktop_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "muninn.desktop"


def autostart_enabled() -> bool:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as k:
                winreg.QueryValueEx(k, "Muninn")
                return True
        except OSError:
            return False
    return _desktop_file().exists()


def set_autostart(on: bool) -> str:
    cmd = _command()
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as k:
            if on:
                winreg.SetValueEx(
                    k, "Muninn", 0, winreg.REG_SZ, " ".join(f'"{c}"' for c in cmd)
                )
            else:
                try:
                    winreg.DeleteValue(k, "Muninn")
                except OSError:
                    pass
        return "Windows startup"
    f = _desktop_file()
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "[Desktop Entry]\nType=Application\nName=Muninn\n"
            "Comment=Encrypted Bluetooth mesh chat\n"
            f"Exec={' '.join(cmd)}\nIcon={ICON}\nX-GNOME-Autostart-enabled=true\n"
        )
    elif f.exists():
        f.unlink()
    return str(f)
