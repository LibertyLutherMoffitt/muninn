"""Node — one Muninn device: identity + storage + mesh + transport + dialer.

Shared by the CLI, the GUI, the headless bot and the test-suite.

Who gets dialled (good Bluetooth citizen):
1. peers we've talked to before (their stored radio addresses) — always,
   with a short capped backoff, so a returning seat neighbour reconnects fast;
2. devices whose service record says Muninn;
3. within the scan policy's budget, an *unknown phone or computer* gets one
   quiet probe without pairing. Headsets, watches and the like are never
   contacted, and a device that isn't Muninn is left alone for a long time.
"""

from __future__ import annotations

import os
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path

from muninn import scanpolicy
from muninn.crypto import generate_keypair, privkey_from_bytes
from muninn.mesh import Mesh
from muninn.storage import Storage
from muninn.transport import ScanResult, Transport

DIAL_BACKOFF_MIN = 2.0
DEFER_SECONDS = 6.0
SIGHTING_FRESH = 120.0  # a scan sighting counts as "nearby" this long


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
        self.policy = scanpolicy.get(self.storage.get_setting(scanpolicy.SETTING_KEY))
        self._stop = threading.Event()
        self._scan_now = threading.Event()
        self._backoff: dict[str, tuple[float, float]] = {}  # addr -> (next_ok, delay)
        self._deferred: dict[str, float] = {}
        self._confirmed: dict[str, float] = {}  # Muninn-capable addr -> last seen
        self._probe_queue: list[str] = []
        self._refused: dict[str, float] = {}  # probed, not Muninn -> until
        self.sightings: dict[str, ScanResult] = {}
        self._sighted_at: dict[str, float] = {}
        self._dial_fail: dict[str, float] = {}
        self.scan_results: list[ScanResult] = []
        self._dialing: set[str] = set()
        self._dial_lock = threading.Lock()
        self.last_inquiry = 0.0
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
        self._scan_now.set()
        self.transport.stop()
        self.mesh.stop()
        # let in-flight dials/handshakes finish before the DB goes away
        deadline = time.monotonic() + 3
        while self._dialing and time.monotonic() < deadline:
            time.sleep(0.05)
        self.storage.close()

    # --- settings -------------------------------------------------------------

    def set_scan_mode(self, name: str) -> scanpolicy.ScanPolicy:
        if name not in scanpolicy.POLICIES:
            raise ValueError(
                f"scan mode must be one of: {', '.join(scanpolicy.POLICIES)}"
            )
        self.policy = scanpolicy.POLICIES[name]
        self.storage.set_setting(scanpolicy.SETTING_KEY, name)
        self._scan_now.set()
        return self.policy

    # --- inbound ------------------------------------------------------------

    def _incoming(self, sock, addr: str) -> None:
        threading.Thread(
            target=self.mesh.add_link, args=(sock, addr, False), daemon=True
        ).start()

    # --- presence -------------------------------------------------------------

    def presence(self, node_id: str) -> dict:
        """What the UI should say about a peer right now."""
        m = self.mesh
        h = m.hops(node_id)
        if h == 1:
            return {"state": "direct", "text": "nearby · connected", "hops": 1}
        if h:
            via = m.display_name(m.next_hop(node_id) or "")
            return {
                "state": "relay",
                "text": f"in range via {via} · {h} hops",
                "hops": h,
            }
        now = time.time()
        addrs = [a for n, a, _ in self.storage.load_transports() if n == node_id]
        seen = max((self._sighted_at.get(a, 0) for a in addrs), default=0)
        if now - seen < SIGHTING_FRESH:
            return {"state": "nearby", "text": "nearby, not connecting yet", "hops": 0}
        p = m.peers.get(node_id)
        last = p.last_seen if p else 0
        return {
            "state": "offline",
            "text": "last seen " + ago(last) if last else "not seen yet",
            "hops": 0,
        }

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
            self._scan_now.set()
        else:
            self._backoff.pop(addr.upper(), None)

    def _candidates(self) -> list[tuple[str, bool]]:
        """[(addr, is_probe)] in priority order."""
        linked_nodes = set(self.mesh.connected())
        out: list[tuple[str, bool]] = []
        seen: set[str] = set()
        for node_id, addr, _ in self.storage.load_transports():
            if node_id in linked_nodes or addr in seen:
                continue
            out.append((addr, False))
            seen.add(addr)
        now = time.time()
        for addr, t in sorted(self._confirmed.items(), key=lambda kv: -kv[1]):
            if now - t < 180 and addr not in seen:
                out.append((addr, False))
                seen.add(addr)
        for addr in list(self._probe_queue):
            if addr not in seen:
                out.append((addr, True))
                seen.add(addr)
        try:
            own = self.transport.local_addr().upper()
        except Exception:
            own = ""
        return [(a, p) for a, p in out if a != own]

    def _dial_loop(self) -> None:
        while not self._stop.wait(0.5):
            try:
                for addr, probe in self._candidates():
                    if self._stop.is_set():
                        return
                    self._maybe_dial(addr, probe)
            except Exception as e:
                self.log(f"dial loop: {e!r}")

    def _maybe_dial(self, addr: str, probe: bool) -> None:
        now = time.monotonic()
        if self.mesh.has_transport(addr):
            self._backoff.pop(addr, None)
            if addr in self._probe_queue:
                self._probe_queue.remove(addr)
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
        if probe and addr in self._probe_queue:
            self._probe_queue.remove(addr)
        threading.Thread(target=self._dial, args=(addr, probe), daemon=True).start()

    def _dial(self, addr: str, probe: bool) -> None:
        try:
            sock = self.transport.probe(addr) if probe else self.transport.connect(addr)
        except Exception:
            self._dial_fail[addr] = time.time()
            if probe:
                self._refused[addr] = time.time() + self.policy.probe_backoff
            else:
                _, delay = self._backoff.get(addr, (0.0, DIAL_BACKOFF_MIN / 2))
                delay = min(
                    max(delay * 2, DIAL_BACKOFF_MIN), self.policy.peer_backoff_max
                )
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
            if probe:  # answered, but not Muninn
                self._refused[addr] = time.time() + self.policy.probe_backoff
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
                    self._mark_confirmed(addr)
                pol = self.policy
                interval = (
                    pol.inquiry_linked if self.mesh.connected() else pol.inquiry_alone
                )
                due = interval and time.monotonic() - self.last_inquiry >= interval
                if (first and pol.inquiry_alone) or due or self._scan_now.is_set():
                    self._scan_now.clear()
                    self._ingest(self.transport.scan(5.0))
            except Exception as e:
                self.log(f"scan: {e!r}")
            first = False
            self._scan_now.wait(2.0 * random.uniform(0.8, 1.2))

    def _ingest(self, results: list[ScanResult]) -> None:
        self.last_inquiry = time.monotonic()
        self.scan_results = results
        now = time.time()
        budget = self.policy.probe_budget
        known = {a for _, a, _ in self.storage.load_transports()}
        for r in sorted(results, key=lambda r: -(r.rssi or -100)):
            addr = r.addr.upper()
            self.sightings[addr] = r
            self._sighted_at[addr] = now
            if r.muninn:
                self._mark_confirmed(addr)
            elif (
                r.probeable
                and budget > 0
                and addr not in known
                and self._refused.get(addr, 0) < now
                and addr not in self._probe_queue
                and not self.mesh.has_transport(addr)
            ):
                self._probe_queue.append(addr)
                budget -= 1

    def _mark_confirmed(self, addr: str) -> None:
        addr = addr.upper()
        fresh = time.time() - self._confirmed.get(addr, 0) > 60
        self._confirmed[addr] = time.time()
        self._sighted_at[addr] = time.time()
        if fresh:
            self._backoff.pop(addr, None)

    def scan_now(self, duration: float = 5.0) -> list[ScanResult]:
        results = self.transport.scan(duration)
        self._ingest(results)
        return results

    def connect_to(self, addr: str, probe: bool = False) -> str | None:
        """Explicit user-initiated connect (e.g. from a scan dialog). `probe`
        for a device not confirmed to run Muninn: never pairs."""
        addr = addr.upper()
        self._backoff.pop(addr, None)
        sock = (
            self.transport.probe(addr)
            if probe
            else self.transport.connect_interactive(addr)
        )
        return self.mesh.add_link(sock, addr, True)

    def status(self) -> dict:
        return {
            "scan_mode": self.policy.name,
            "last_inquiry_ago": round(time.monotonic() - self.last_inquiry)
            if self.last_inquiry
            else None,
            "sighted": len(
                [
                    t
                    for t in self._sighted_at.values()
                    if time.time() - t < SIGHTING_FRESH
                ]
            ),
            "probe_queue": len(self._probe_queue),
            "refused": len([t for t in self._refused.values() if t > time.time()]),
        }


def ago(ts: float) -> str:
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    return f"{int(d // 86400)} d ago"


def default_transport() -> Transport:
    """Sim transport if MUNINN_SIM=host:port is set, else real Bluetooth."""
    sim = os.environ.get("MUNINN_SIM")
    if sim:
        from muninn.sim.transport import SimTransport

        return SimTransport(sim, name=os.environ.get("MUNINN_NAME", ""))
    from muninn.transport import BtTransport

    return BtTransport()
