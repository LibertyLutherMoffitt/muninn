"""Platform Bluetooth backends (BlueZ on Linux, WinRT on Windows).

Import lazily via `load()` so the sim transport and tests never need D-Bus /
WinRT installed.
"""

import importlib
import sys


def load():
    if sys.platform == "linux":
        return importlib.import_module("muninn.bt.bluez")
    if sys.platform == "win32":
        return importlib.import_module("muninn.bt.winrt")
    raise ImportError(f"Muninn has no Bluetooth backend for platform {sys.platform!r}")
