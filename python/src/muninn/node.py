"""Node — one Muninn device: identity + storage + mesh + transport + dialer.

Shared by the CLI, the GUI, the headless bot and the test-suite.
"""

from __future__ import annotations

import os
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path

from muninn.crypto import generate_keypair, privkey_from_bytes
from muninn.mesh import Mesh
from muninn.storage import Storage
from muninn.transport import Transport

DIAL_BACKOFF_MIN = 2.0
DIAL_BACKOFF_MAX = 45.0
DEFER_SECONDS = 6.0
SCAN_INTERVAL_FAST = 12.0  # while we have no links
SCAN_INTERVAL_SLOW = 45.0


class Node:
    def __init__(
        self,
        transport: Transport,
        db_path: Path | str | None = None,
        name: str | None = None,
        log: Callable[[str], None] | None = None,
    ):
        self.transport = transport
        self.storage = Storage(db_path)
        ident = self.storage.get_identity()
        if ident is None:
            ident = self.storage.create_identity(bytes(generate_keypair()), name or "")
        if name is not None and name != ident.display_name:
            ident.display_name = name
            ident.name_ts = max(int(time.time()), ident.name_ts + 1)
            self.storage.set_display_name(name, ident.name_ts)
        self.identity = ident
        self.mesh = Mesh(
            self.storage,
            privkey_from_bytes(ident.privkey),
            ident.node_id,
            ident.display_name,
            ident.name_ts,
            log=log,
        )
        self.log = log or (lambda s: None)
        self._stop = threading.Event()
        self._backoff: dict[str, tuple[float, float]] = {}  # addr -> (next_ok, delay)
        self._deferred: dict[str, float] = {}
        self._discovered: dict[str, float] = {}  # addr -> last seen (muninn-capable)
        self.scan_results: list[tuple[str, str, bool]] = []
        self._dialing: set[str] = set()
        self._dial_lock = threading.Lock()
        self.mesh.on("peers", self._on_peers)

    @property
    def node_id(self) -> str:
        return self.identity.node_id

    def start(self) -> None:
        self.mesh.start()
        self.transport.start(self._incoming)
        for target in (self._dial_loop, self._scan_loop):
            threading.Thread(target=target, daemon=True, name=target.__name__).start()

    def stop(self) -> None:
        self._stop.set()
        self.transport.stop()
        self.mesh.stop()
        self.storage.close()

    # --- inbound ------------------------------------------------------------

    def _incoming(self, sock, addr: str) -> None:
        threading.Thread(
            target=self.mesh.add_link, args=(sock, addr, False), daemon=True
        ).start()

    # --- dialing ------------------------------------------------------------

    def _on_peers(self) -> None:
        # A link just dropped? Let us redial it soon instead of waiting out a
        # long backoff — flaky links come back quickly.
        linked = {lk["addr"] for lk in self.mesh.link_info()}
        for addr, (next_ok, delay) in list(self._backoff.items()):
            if addr not in linked and delay > DIAL_BACKOFF_MIN * 4:
                self._backoff[addr] = (min(next_ok, time.monotonic() + 3), delay / 2)

    def poke(self, addr: str | None = None) -> None:
        """Forget backoff (all, or one addr) — e.g. user pressed 'reconnect'."""
        if addr is None:
            self._backoff.clear()
        else:
            self._backoff.pop(addr.upper(), None)

    def _candidates(self) -> list[str]:
        linked_nodes = set(self.mesh.connected())
        out: list[str] = []
        seen: set[str] = set()
        now = time.time()
        # Discovered-and-capable first (they're in range right now).
        for addr, t in sorted(self._discovered.items(), key=lambda kv: -kv[1]):
            if now - t < 180 and addr not in seen:
                out.append(addr)
                seen.add(addr)
        for node_id, addr, _ in self.storage.load_transports():
            if node_id in linked_nodes or addr in seen:
                continue
            out.append(addr)
            seen.add(addr)
        own = self.transport.local_addr().upper()
        return [a for a in out if a != own]

    def _dial_loop(self) -> None:
        while not self._stop.wait(0.5):
            try:
                for addr in self._candidates():
                    if self._stop.is_set():
                        return
                    self._maybe_dial(addr)
            except Exception as e:
                self.log(f"dial loop: {e!r}")

    def _maybe_dial(self, addr: str) -> None:
        now = time.monotonic()
        if self.mesh.has_transport(addr):
            self._backoff.pop(addr, None)
            return
        next_ok, _ = self._backoff.get(addr, (0.0, 0.0))
        if now < next_ok:
            return
        if self.transport.should_defer(addr):
            first = self._deferred.setdefault(addr, now)
            if now - first < DEFER_SECONDS:
                return
        with self._dial_lock:
            if (
                addr in self._dialing
                or len(self._dialing) >= self.transport.max_parallel_dials
            ):
                return
            self._dialing.add(addr)
        threading.Thread(target=self._dial, args=(addr,), daemon=True).start()

    def _dial(self, addr: str) -> None:
        try:
            sock = self.transport.connect(addr)
        except Exception:
            _, delay = self._backoff.get(addr, (0.0, DIAL_BACKOFF_MIN / 2))
            delay = min(max(delay * 2, DIAL_BACKOFF_MIN), DIAL_BACKOFF_MAX)
            self._backoff[addr] = (
                time.monotonic() + delay * random.uniform(0.8, 1.2),
                delay,
            )
            return
        finally:
            with self._dial_lock:
                self._dialing.discard(addr)
        self._deferred.pop(addr, None)
        if self.mesh.add_link(sock, addr, True) is None:
            self._backoff[addr] = (
                time.monotonic() + DIAL_BACKOFF_MIN,
                DIAL_BACKOFF_MIN,
            )
        else:
            self._backoff.pop(addr, None)

    # --- discovery ----------------------------------------------------------

    def _scan_loop(self) -> None:
        first = True
        while not self._stop.is_set():
            try:
                for addr, _ in self.transport.discover():
                    self._mark_seen(addr)
                if first or not self.mesh.connected() or random.random() < 0.3:
                    results = self.transport.scan(5.0)
                    self.scan_results = results
                    for addr, _, capable in results:
                        if capable:
                            self._mark_seen(addr)
            except Exception as e:
                self.log(f"scan: {e!r}")
            first = False
            interval = (
                SCAN_INTERVAL_FAST if not self.mesh.connected() else SCAN_INTERVAL_SLOW
            )
            self._stop.wait(interval * random.uniform(0.8, 1.2))

    def _mark_seen(self, addr: str) -> None:
        addr = addr.upper()
        fresh = time.time() - self._discovered.get(addr, 0) > 60
        self._discovered[addr] = time.time()
        if fresh:
            self._backoff.pop(addr, None)

    def scan_now(self, duration: float = 5.0) -> list[tuple[str, str, bool]]:
        results = self.transport.scan(duration)
        self.scan_results = results
        for addr, _, capable in results:
            if capable:
                self._mark_seen(addr)
        return results

    def connect_to(self, addr: str) -> str | None:
        """Explicit user-initiated connect (e.g. from a scan dialog)."""
        addr = addr.upper()
        self._backoff.pop(addr, None)
        sock = self.transport.connect(addr)
        return self.mesh.add_link(sock, addr, True)


def default_transport() -> Transport:
    """Sim transport if MUNINN_SIM=host:port is set, else real Bluetooth."""
    sim = os.environ.get("MUNINN_SIM")
    if sim:
        from muninn.sim.transport import SimTransport

        return SimTransport(sim, name=os.environ.get("MUNINN_NAME", ""))
    from muninn.transport import BtTransport

    return BtTransport()
