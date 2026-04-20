"""Bluetooth backend dispatch.

Selects the platform-specific module at import time so the rest of the
codebase can `from muninn import bt` and use the same functions everywhere.
"""

import sys

if sys.platform == "linux":
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
elif sys.platform == "win32":
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
