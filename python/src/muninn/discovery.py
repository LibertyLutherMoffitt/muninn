"""Peer discovery and connection loops, shared by every desktop front end.

The CLI and the Qt GUI both need to accept inbound sockets and keep hunting for
peers. These loops used to be copy-pasted into both entry points, and the
copies drifted.

**Why this does not simply trust the adapter's service cache.** `bt.discover()`
returns devices whose cached UUID list already contains the Muninn service. That
list is filled from the inquiry EIR, which BlueZ routinely omits 128-bit UUIDs
from, or from an SDP browse, which only happens after a pair or connect. So a
peer that has never been connected to may never appear there — the app looks
broken while the person is sitting two rows away.

The fix is to treat every device the radio can see as a candidate and *try* it.
A non-Muninn device refuses quickly and is backed off hard; see `dialer.py` for
how that is rationed so a cabin full of headsets cannot starve a real peer.
"""

import threading
import time

from muninn import bt
from muninn.dialer import DialScheduler
from muninn.peers import ConnectionManager
from muninn.scanpolicy import DEFAULT, ScanPolicy

# How long the higher-MAC device waits for the lower-MAC one to dial first.
# The deferral is the primary defence against both sides connecting at once;
# see PROTOCOL.md, "Simultaneous Connection Tiebreak".
TIEBREAK_DEFER = 10.0

INQUIRY_SECONDS = 5.0


def acceptor(conn_mgr: ConnectionManager) -> None:
    """Accept inbound connections and hand each to the ConnectionManager.

    A failure handling one peer must never end the loop: if this thread dies
    the device silently stops accepting connections for the rest of the
    session, which looks exactly like being out of range.
    """
    while True:
        try:
            sock, addr = bt.accept()
        except ConnectionError:
            break
        try:
            conn_mgr.add_peer(sock, addr)
        except Exception as e:
            print(f"[accept {addr}] handshake failed: {e!r}")
            try:
                sock.close()
            except Exception:
                pass


class Scanner:
    """Owns the discovery loop and the dial schedule.

    Kept as an object so the UI can change the scan policy while it runs and
    read back what it is currently doing.
    """

    def __init__(
        self,
        conn_mgr: ConnectionManager,
        local_mac: str,
        stop: threading.Event,
        policy: ScanPolicy = DEFAULT,
    ):
        self.conn_mgr = conn_mgr
        self.local_mac = local_mac.upper()
        self.stop = stop
        self.policy = policy
        self.scheduler = DialScheduler(policy, self.local_mac)
        self._deferred: dict[str, float] = {}
        self._last_inquiry = 0.0

        # Anything we already hold a key for is a peer, whatever the adapter
        # thinks — that is the whole point of remembering them across restarts.
        for addr in conn_mgr.group_store.pubkeys:
            if addr != self.local_mac:
                self.scheduler.mark_peer(addr)

    def set_policy(self, policy: ScanPolicy) -> None:
        self.policy = policy
        self.scheduler.set_policy(policy)

    # --- One pass ---

    def _inquiry(self, now: float) -> None:
        """Full inquiry: everything the radio can hear, Muninn or not."""
        try:
            found = bt.scan_devices(duration=INQUIRY_SECONDS, quiet=True)
        except Exception:
            # A scan failure must never take the loop down.
            return
        self._last_inquiry = now
        for addr, _name in found:
            addr = addr.upper()
            if addr == self.local_mac:
                continue
            self.scheduler.saw(addr, now)
            self.conn_mgr.presence.record_sighting(addr)

    def _advertised(self, now: float) -> None:
        """Devices whose cached SDP record already names the Muninn service."""
        try:
            services = bt.discover()
        except Exception:
            services = []
        for addr, _name in services:
            addr = addr.upper()
            if addr == self.local_mac:
                continue
            self.scheduler.saw(addr, now, is_peer=True)
            self.conn_mgr.presence.record_sighting(addr, advertises_muninn=True)

    def _should_defer(self, addr: str, now: float) -> bool:
        """Higher MAC holds off briefly so the lower one dials first."""
        try:
            if bt.should_keep_outgoing(self.local_mac, addr):
                self._deferred.pop(addr, None)
                return False
        except ValueError:
            return False
        started = self._deferred.setdefault(addr, now)
        if now - started < TIEBREAK_DEFER:
            return True
        self._deferred.pop(addr, None)
        return False

    def _dial(self, addr: str, now: float, is_probe: bool) -> None:
        try:
            bt.ensure_paired(addr)
            sock, peer_addr = bt.connect(addr)
        except (ConnectionError, OSError) as e:
            self.scheduler.failed(addr, now, str(e))
            # Only report a dial failure as a presence problem for devices we
            # believe are peers. A headset refusing us is not news.
            if not is_probe:
                self.conn_mgr.presence.record_dial_failure(addr, str(e))
            return
        except Exception as e:
            self.scheduler.failed(addr, now, str(e))
            return

        if self.conn_mgr.add_peer(sock, peer_addr):
            self.scheduler.succeeded(addr)
        else:
            self.scheduler.failed(addr, now, "handshake failed")
            if not is_probe:
                self.conn_mgr.presence.record_dial_failure(addr, "handshake failed")

    def sweep(self, now: float | None = None) -> None:
        """One pass: inquire if due, then dial whoever is worth dialling."""
        now = time.time() if now is None else now
        if now - self._last_inquiry >= self.policy.inquiry_interval:
            self._inquiry(now)
        self._advertised(now)

        # Peers we hold keys for stay dial-worthy even when this inquiry
        # missed them; inquiry misses are routine in a noisy cabin.
        for addr in list(self.conn_mgr.group_store.pubkeys):
            if addr != self.local_mac:
                self.scheduler.mark_peer(addr)

        plan = self.scheduler.plan(now, self.conn_mgr.is_connected)
        for addr in plan.peers:
            if self.stop.is_set():
                return
            if self._should_defer(addr, now):
                continue
            self._dial(addr, now, is_probe=False)
        for addr in plan.probes:
            if self.stop.is_set():
                return
            self._dial(addr, now, is_probe=True)

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.sweep()
            except Exception as e:
                print(f"[scan] sweep failed: {e!r}")
            self.stop.wait(self.policy.dial_interval)


def scanner(
    conn_mgr: ConnectionManager,
    local_mac: str,
    stop: threading.Event,
    policy: ScanPolicy = DEFAULT,
) -> Scanner:
    """Build and run a Scanner. Returns it so callers can retune it live."""
    s = Scanner(conn_mgr, local_mac, stop, policy)
    s.run()
    return s
