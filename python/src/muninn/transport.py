"""Transport abstraction: anything that can listen, dial and discover.

`Mesh` only ever sees connected socket-like objects (sendall / recv / close /
settimeout / gettimeout). Implementations:

- `BtTransport`  — real Bluetooth RFCOMM (BlueZ or WinRT backend module)
- `SimTransport` — the radio simulator (muninn.sim), for tests and labs
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class ScanResult:
    addr: str
    name: str
    # True: advertises Muninn. False: advertises other services only.
    # None: unknown (BlueZ often omits 128-bit UUIDs from inquiry).
    muninn: bool | None
    kind: str = "other"  # phone | computer | audio | peripheral | wearable | other
    rssi: int | None = None

    @property
    def probeable(self) -> bool:
        """Could be a Muninn peer worth one polite look: an unknown phone or
        computer. Headsets, watches, mice etc. are never contacted."""
        return self.muninn is None and self.kind in ("phone", "computer")


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

    def scan(self, duration: float = 5.0) -> list[ScanResult]:
        """Active inquiry. Returns every device seen."""
        return []

    def probe(self, addr: str):
        """Try a Muninn connection to an *unconfirmed* device without pairing
        or prompting anyone. Returns a socket or raises."""
        return self.connect(addr)

    def connect_interactive(self, addr: str):
        """User explicitly asked to connect: pairing (and its prompt) is OK."""
        return self.connect(addr)

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
        self._pair_tried: dict[str, float] = {}
        self._stop = threading.Event()

    def local_addr(self) -> str:
        if self._addr is None:
            self._addr = self.bt.get_local_mac()
        return self._addr

    def start(self, on_incoming) -> None:
        self.bt.create_server()

        def stay_visible():
            # BlueZ drops discoverability after DiscoverableTimeout (180 s by
            # default); new seat neighbours must still be able to find us.
            while not self._stop.wait(120):
                try:
                    self.bt.set_discoverable(True)
                except Exception:
                    pass

        threading.Thread(target=stay_visible, daemon=True).start()

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

    PAIR_RETRY = 24 * 3600

    def connect(self, addr: str, timeout: float = 20.0):
        # Known / Muninn-confirmed peers only (strangers go through probe()).
        # Try without a bond first; if the link needs one, pair — but at
        # most once a day per device, so a phone never sees repeated
        # "Pair with …?" prompts while a peer keeps redialling it.
        try:
            sock, _ = self.bt.connect(addr)
            return sock
        except Exception:
            if self.bt.is_paired(addr):
                raise
            now = time.time()
            if now - self._pair_tried.get(addr, 0) < self.PAIR_RETRY:
                raise
            self._pair_tried[addr] = now
            self.bt.ensure_paired(addr)
            sock, _ = self.bt.connect(addr)
            return sock

    def connect_interactive(self, addr: str):
        self._pair_tried.pop(addr, None)  # the user asked; a prompt is expected
        return self.connect(addr)

    def probe(self, addr: str):
        # Unconfirmed device: never pair (a pairing prompt on a stranger's
        # phone is exactly what we must not cause). ConnectProfile without a
        # bond only succeeds if the other side runs Muninn's open profile.
        sock, _ = self.bt.connect(addr)
        return sock

    def discover(self):
        try:
            return self.bt.discover()
        except Exception:
            return []

    def scan(self, duration: float = 5.0):
        if hasattr(self.bt, "inquiry"):
            try:
                return [ScanResult(**d) for d in self.bt.inquiry(duration)]
            except Exception:
                return []
        try:  # WinRT: no class/RSSI info
            seen = self.bt.scan_devices(duration=duration, quiet=True)
        except Exception:
            return []
        muninn = {a.upper() for a, _ in self.discover()}
        return [
            ScanResult(a.upper(), n, a.upper() in muninn or None, "other")
            for a, n in seen
        ]

    def should_defer(self, addr: str) -> bool:
        # Simultaneous ConnectProfile calls can wedge bluetoothd: the higher
        # MAC gives the lower one a head start.
        try:
            return not self.bt.should_keep_outgoing(self.local_addr(), addr)
        except Exception:
            return False
