"""Transport abstraction: anything that can listen, dial and discover.

`Mesh` only ever sees connected socket-like objects (sendall / recv / close /
settimeout / gettimeout). Implementations:

- `BtTransport`  — real Bluetooth RFCOMM (BlueZ or WinRT backend module)
- `SimTransport` — the radio simulator (muninn.sim), for tests and labs
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class Transport:
    #: how many dials may run concurrently (radio contention on real BT)
    max_parallel_dials = 1

    def local_addr(self) -> str:
        raise NotImplementedError

    def start(self, on_incoming: Callable[[object, str], None]) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def connect(self, addr: str, timeout: float = 20.0):
        raise NotImplementedError

    def discover(self) -> list[tuple[str, str]]:
        """Muninn-capable devices currently known to be nearby: [(addr, name)]."""
        return []

    def scan(self, duration: float = 5.0) -> list[tuple[str, str, bool]]:
        """Active inquiry. Returns every device seen: [(addr, name, is_muninn)]."""
        return []

    def should_defer(self, addr: str) -> bool:
        """True if we should give the peer a head start dialing us first."""
        return False


class BtTransport(Transport):
    """Wraps a backend module (muninn.bt.bluez / muninn.bt.winrt)."""

    def __init__(self, backend=None):
        if backend is None:
            from muninn import bt

            backend = bt.load()
        self.bt = backend
        self._addr: str | None = None
        self._stop = threading.Event()

    def local_addr(self) -> str:
        if self._addr is None:
            self._addr = self.bt.get_local_mac()
        return self._addr

    def start(self, on_incoming) -> None:
        self.bt.create_server()

        def acceptor():
            while not self._stop.is_set():
                try:
                    sock, addr = self.bt.accept()
                except ConnectionError:
                    return
                except Exception:
                    continue
                on_incoming(sock, addr)

        threading.Thread(target=acceptor, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.bt.close_server()
        except Exception:
            pass

    def connect(self, addr: str, timeout: float = 20.0):
        self.bt.ensure_paired(addr)
        sock, _ = self.bt.connect(addr)
        return sock

    def discover(self):
        try:
            return self.bt.discover()
        except Exception:
            return []

    def scan(self, duration: float = 5.0):
        try:
            seen = self.bt.scan_devices(duration=duration, quiet=True)
        except Exception:
            return []
        muninn = {a.upper() for a, _ in self.discover()}
        return [(a.upper(), n, a.upper() in muninn) for a, n in seen]

    def should_defer(self, addr: str) -> bool:
        # Simultaneous ConnectProfile calls can wedge bluetoothd: the higher
        # MAC gives the lower one a head start.
        try:
            return not self.bt.should_keep_outgoing(self.local_addr(), addr)
        except Exception:
            return False
