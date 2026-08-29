"""Bluetooth backend dispatch.

Selects the platform-specific module at import time so the rest of the
codebase can `from muninn import bt` and use the same functions everywhere.

`MUNINN_BT_BACKEND=loopback` overrides the platform choice with a TCP-based
backend (see `loopback.py`). That is how the app is run without a radio — in
CI, in a container, or with two clients on one desktop.
"""

import os
import sys

_FORCED = os.environ.get("MUNINN_BT_BACKEND", "").strip().lower()

if _FORCED == "loopback":
    from muninn.bt.loopback import (
        SERVICE_UUID,
        accept,
        close_server,
        connect,
        create_server,
        discover,
        ensure_paired,
        get_local_mac,
        mac_to_int,
        scan_devices,
        set_discoverable,
        should_keep_outgoing,
    )
elif _FORCED and _FORCED not in ("bluez", "winrt"):
    raise ImportError(
        f"unknown MUNINN_BT_BACKEND {_FORCED!r} "
        "(expected 'loopback', 'bluez' or 'winrt')"
    )
elif _FORCED == "bluez" or (not _FORCED and sys.platform == "linux"):
    from muninn.bt.bluez import (
        SERVICE_UUID,
        accept,
        close_server,
        connect,
        create_server,
        discover,
        ensure_paired,
        get_local_mac,
        mac_to_int,
        scan_devices,
        set_discoverable,
        should_keep_outgoing,
    )
elif _FORCED == "winrt" or sys.platform == "win32":
    from muninn.bt.winrt import (
        SERVICE_UUID,
        accept,
        close_server,
        connect,
        create_server,
        discover,
        ensure_paired,
        get_local_mac,
        mac_to_int,
        scan_devices,
        set_discoverable,
        should_keep_outgoing,
    )
else:
    raise ImportError(f"Muninn has no Bluetooth backend for platform {sys.platform!r}")

__all__ = [
    "SERVICE_UUID",
    "accept",
    "close_server",
    "connect",
    "create_server",
    "discover",
    "ensure_paired",
    "get_local_mac",
    "mac_to_int",
    "scan_devices",
    "set_discoverable",
    "should_keep_outgoing",
]
