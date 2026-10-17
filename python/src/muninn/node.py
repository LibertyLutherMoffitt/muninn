"""Node — one Muninn device: identity + storage + mesh + transport + dialer.

Shared by the CLI, the GUI, the headless bot and the test-suite.

Who gets dialled (good Bluetooth citizen):
1. peers we've talked to before (their stored radio addresses) — always,
   with a short capped backoff, so a returning seat neighbour reconnects fast;
2. devices whose service record says Muninn;
3. only while finding new people (scanpolicy: a few minutes on request, or
   while we know nobody), an *unknown phone or computer* gets one
   quiet probe without pairing. Headsets, watches and the like are never
   contacted, and a device that isn't Muninn is left alone for a long time.
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path

from muninn import scanpolicy
from muninn.crypto import generate_keypair, privkey_from_bytes
from muninn.instance import AlreadyRunning, InstanceLock
from muninn.mesh import Mesh
from muninn.storage import Storage
from muninn.transport import ScanResult, Transport

DIAL_BACKOFF_MIN = 2.0
DEFER_SECONDS = 6.0
SIGHTING_FRESH = 120.0  # a scan sighting counts as "nearby" this long
HINT_MAX_HOPS = 3  # dial gossiped addresses of peers at most this far away
HINT_BACKOFF_MIN = 10.0
HINT_BACKOFF_MAX = 300.0
MANUAL_PROBES = 6  # a user-initiated scan checks at most this many unknowns
MANUAL_RECHECK = 60.0  # ...and may re-check a "not Muninn" verdict this old


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
        self._instance = InstanceLock(self.storage.path.parent)
        if not self._instance.try_acquire():
            self.storage.close()
            pid = self._instance.holder()
            raise AlreadyRunning(
                "Muninn is already running"
                + (f" (pid {pid})" if pid else "")
                + " — only one copy can use Bluetooth and the message store."
            )
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
        self.reconnect = scanpolicy.get(
            self.storage.get_setting(scanpolicy.SETTING_KEY)
        )
        self.find_until = 0.0  # wall clock; find mode on until then
        # find automatically while we know nobody (first run "just works")
        self.auto_find = self.storage.get_setting("auto_find", "on") != "off"
        self._stop = threading.Event()
        self._scan_now = threading.Event()
        self._backoff: dict[str, tuple[float, float]] = {}  # addr -> (next_ok, delay)
        self._deferred: dict[str, float] = {}
        self._confirmed: dict[str, float] = {}  # Muninn-capable addr -> last seen
        self._probe_queue: list[str] = []
        self._refused: dict[str, float] = {}  # probed, not Muninn -> until
        self._refusals: dict[str, int] = {}
        self._refused_at: dict[str, float] = {}
        self.sightings: dict[str, ScanResult] = {}
        self._sighted_at: dict[str, float] = {}
        self._dial_fail: dict[str, float] = {}
        self.scan_results: list[ScanResult] = []
        self._dialing: set[str] = set()
        self._hints: set[str] = set()
        # radio addresses of people the user forgot: never auto-dialled again
        # (they can still reach us; a manual connect clears it)
        self._forgotten: set[str] = set(
            json.loads(self.storage.get_setting("forgotten_addrs", "[]") or "[]")
        )
        self._dial_lock = threading.Lock()
        self.last_inquiry = 0.0
        self._scan_lock = threading.Lock()  # concurrent inquiries cancel each other
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
        self._instance.release()

    # --- settings -------------------------------------------------------------

    def set_reconnect_mode(self, name: str) -> scanpolicy.ReconnectMode:
        if name not in scanpolicy.MODES:
            raise ValueError(f"reconnect must be one of: {', '.join(scanpolicy.MODES)}")
        self.reconnect = scanpolicy.MODES[name]
        self.storage.set_setting(scanpolicy.SETTING_KEY, name)
        return self.reconnect

    def set_auto_find(self, on: bool) -> None:
        self.auto_find = on
        self.storage.set_setting("auto_find", "on" if on else "off")

    @property
    def finding(self) -> bool:
        """Looking for new people right now (scans + polite probes)?"""
        if time.time() < self.find_until:
            return True
        return self.auto_find and not self.mesh.active_peers()

    def find_left(self) -> int:
        return max(0, int(self.find_until - time.time()))

    def find_people(self, seconds: float = scanpolicy.FIND_SECONDS) -> None:
        """Look for new people for a while (Nearby / ⟳ / "Find people")."""
        self.find_until = max(self.find_until, time.time() + seconds)
        self._scan_now.set()

    def stop_finding(self) -> None:
        self.find_until = 0.0

    # --- inbound ------------------------------------------------------------

    def _incoming(self, sock, addr: str) -> None:
        if addr.upper() in self._forgotten:  # they came back on their own
            self._set_forgotten(self._forgotten - {addr.upper()})
        threading.Thread(
            target=self.mesh.add_link, args=(sock, addr, False), daemon=True
        ).start()

    # --- presence -------------------------------------------------------------

    def presence(self, node_id: str) -> dict:
        """What the UI should say about a peer right now."""
        m = self.mesh
        h = m.hops(node_id)
        if h == 1:
            return {"state": "direct", "text": "connected directly", "hops": 1}
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
        hints: set[str] = set()
        for node_id, addr, last_ok in self.storage.load_transports():
            if node_id in linked_nodes or addr in seen:
                continue
            if not last_ok:
                # Gossiped by a neighbour, never connected there yet: worth a
                # try only while they're close (a short relay away).
                h = self.mesh.hops(node_id)
                if not h or h > HINT_MAX_HOPS:
                    continue
                hints.add(addr)
            out.append((addr, False))
            seen.add(addr)
        self._hints = hints
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
        return [(a, p) for a, p in out if a != own and a not in self._forgotten]

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
        # Gossiped addresses are hearsay: dial them like a stranger (never
        # pair), even though they claim to be a known peer.
        quiet = probe or addr in self._hints
        try:
            sock = self.transport.probe(addr) if quiet else self.transport.connect(addr)
        except Exception:
            self._dial_fail[addr] = time.time()
            if probe:
                self._refuse(addr)
            else:
                _, delay = self._backoff.get(addr, (0.0, DIAL_BACKOFF_MIN / 2))
                if addr in self._hints:  # maybe out of range: back off further
                    delay = min(max(delay * 2, HINT_BACKOFF_MIN), HINT_BACKOFF_MAX)
                else:
                    delay = min(
                        max(delay * 2, DIAL_BACKOFF_MIN), self.reconnect.backoff_max
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
                self._refuse(addr)
            self._backoff[addr] = (
                time.monotonic() + DIAL_BACKOFF_MIN,
                DIAL_BACKOFF_MIN,
            )
        else:
            self._backoff.pop(addr, None)

    # --- discovery ----------------------------------------------------------

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for addr, _ in self.transport.discover():
                    self._mark_confirmed(addr)
                # No scanning at all unless we're looking for new people:
                # known ones are dialled by address.
                due = (
                    self.finding
                    and time.monotonic() - self.last_inquiry >= scanpolicy.FIND_INQUIRY
                )
                if due or self._scan_now.is_set():
                    self._scan_now.clear()
                    with self._scan_lock:
                        self._ingest(self.transport.scan(5.0))
            except Exception as e:
                self.log(f"scan: {e!r}")
            self._scan_now.wait(2.0 * random.uniform(0.8, 1.2))

    def _ingest(self, results: list[ScanResult], manual: bool = False) -> None:
        """`manual`: the user asked — check every phone/laptop in sight,
        including ones that weren't running Muninn last time."""
        self.last_inquiry = time.monotonic()
        self.scan_results = results
        now = time.time()
        budget = MANUAL_PROBES if manual else scanpolicy.FIND_PROBES
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
                and (
                    self._refused.get(addr, 0) < now
                    or (manual and now - self._refused_at.get(addr, 0) > MANUAL_RECHECK)
                )
                and addr not in self._probe_queue
                and not self.mesh.has_transport(addr)
            ):
                self._probe_queue.append(addr)
                budget -= 1

    def _refuse(self, addr: str) -> None:
        """A probe didn't find Muninn. Could be a radio hiccup the first time
        (short pause); after that the device is left alone for hours."""
        n = self._refusals[addr] = self._refusals.get(addr, 0) + 1
        wait = scanpolicy.PROBE_BACKOFF if n > 1 else 600.0
        self._refused[addr] = time.time() + wait
        self._refused_at[addr] = time.time()

    def _mark_confirmed(self, addr: str) -> None:
        addr = addr.upper()
        fresh = time.time() - self._confirmed.get(addr, 0) > 60
        self._confirmed[addr] = time.time()
        self._sighted_at[addr] = time.time()
        if fresh:
            self._backoff.pop(addr, None)

    def scan_now(self, duration: float = 5.0) -> list[ScanResult]:
        # the user is looking: keep looking for a while (this scan counts)
        self.find_until = max(self.find_until, time.time() + scanpolicy.FIND_SECONDS)
        with self._scan_lock:
            results = self.transport.scan(duration)
            self._ingest(results, manual=True)
        return results

    def device_state(self, addr: str) -> str:
        """What a scan list shows for a device: connected / checking / muninn /
        not_muninn / ignored (headset, watch…) / unknown."""
        a = addr.upper()
        now = time.time()
        seen = self.sightings.get(a)
        if self.mesh.has_transport(a):
            return "connected"
        if a in self._dialing or a in self._probe_queue:
            return "checking"
        if now - self._confirmed.get(a, 0) < 180:
            return "muninn"
        if self._refused.get(a, 0) > now:
            return "not_muninn"
        if seen is not None and not seen.probeable and not seen.muninn:
            return "ignored"
        return "unknown"

    def forget(self, node_id: str, block: bool = False) -> None:
        """Forget (and optionally block) a person; also drops any OS-level
        Bluetooth pairing with their device and never auto-dials it again."""
        addrs = {a.upper() for a in self.mesh.forget(node_id, block)}
        self._set_forgotten(self._forgotten | addrs)
        for a in addrs:
            try:
                self.transport.forget(a)
            except Exception as e:
                self.log(f"unpair {a}: {e!r}")

    def _set_forgotten(self, addrs: set[str]) -> None:
        self._forgotten = addrs
        self.storage.set_setting("forgotten_addrs", json.dumps(sorted(addrs)))

    def connect_to(self, addr: str, probe: bool = False) -> str | None:
        """Explicit user-initiated connect (e.g. from a scan dialog). `probe`
        for a device not confirmed to run Muninn: never pairs."""
        addr = addr.upper()
        if addr in self._forgotten:
            self._set_forgotten(self._forgotten - {addr})
        self._backoff.pop(addr, None)
        sock = (
            self.transport.probe(addr)
            if probe
            else self.transport.connect_interactive(addr)
        )
        return self.mesh.add_link(sock, addr, True)

    def status(self) -> dict:
        return {
            "reconnect": self.reconnect.name,
            "finding": self.finding,
            "find_left": self.find_left(),
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
