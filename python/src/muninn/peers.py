"""ConnectionManager — every live session, and how frames get between them.

Manages simultaneous BT connections, each with its own socket, NaCl Box, recv
thread and send lock, and decides how a frame reaches a device we are not
directly connected to.

**Routing.** Each device tells its neighbours who it can reach (a ROUTES frame:
wire id + hop count, a full snapshot, resent whenever it changes). From those
we keep a small distance-vector table and pick a next hop:

1. a live direct session, if we have one;
2. otherwise the neighbour advertising the fewest hops to the destination;
3. otherwise every neighbour (a flood — each device forwards a given frame once
   per `RELAY_DEDUP_WINDOW`), and a copy is kept in the relay queue in case the
   destination turns up here first.

Rule 3 is store-and-forward on purpose. On a flight people walk to the galley
and back; a message can ride with whoever next sits near the recipient.

**Delivery guarantees.** The sender keeps every message until the recipient's
ACK arrives. It resends on a direct reconnect, as soon as a relay path to the
recipient appears, and every `RETRY_INTERVAL` while one exists. Relays are
best-effort; the sender's retry is what makes delivery reliable.

Thread safety:
- `peers` and `relay_queue` are protected by `peers_lock`.
- The routing table (`routes_from`, `indirect_via`) is protected by
  `_routes_lock`. It is taken *before* `peers_lock` when both are needed, and
  never while sending.
- Individual socket sends use the per-peer `send_lock`.
- Dedup windows (`seen_relayed`, `seen_acks`, …) are `_Recent`, internally
  locked.
- Message dedup is delegated to Storage (atomic INSERT OR IGNORE).
"""

import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nacl.public import PrivateKey

from muninn import crypto, protocol
from muninn.groups import Group, GroupStore
from muninn.presence import PresenceTracker
from muninn.protocol import GROUP_ZERO_ID

if TYPE_CHECKING:
    import socket

    from muninn.storage import Storage

# Re-exported under the historical name for the CLI/GUI import sites.
GROUP_ZERO = GROUP_ZERO_ID

# A relay forwards a given (msg_id, dest) at most once per window. A flood
# looping around a triangle of devices comes back within milliseconds, so a
# few seconds stops it; anything later is a deliberate resend (the sender's
# retry, or a held copy being handed on) and should get through.
RELAY_DEDUP_WINDOW = 10.0
# Same for ACK / READ / GROUP_SETUP floods. Expiring rather than permanent so
# the ACK for a resent message reaches a sender who missed the first one.
RECEIPT_DEDUP_WINDOW = 5.0
# How often an unacknowledged message is pushed again down a relay path.
RETRY_INTERVAL = 30.0
# Two sessions to one peer completing this close together are a crossed dial
# (both sides connected at once), resolved by the tiebreak. Further apart, the
# newer one simply replaces a stale session the old socket has not noticed.
DUPLICATE_SESSION_WINDOW = 10.0
# Store-and-forward bounds for frames held on someone else's behalf.
RELAY_QUEUE_PER_DEST = 200
RELAY_QUEUE_TTL = 12 * 3600.0
HANDSHAKE_TIMEOUT = 15.0


class _Recent:
    """Keys seen within the last `window` seconds. Thread-safe."""

    def __init__(self, window: float):
        self.window = window
        self._seen: dict = {}
        self._lock = threading.Lock()

    def claim(self, key, now: float | None = None) -> bool:
        """True (and records the key) unless it was claimed within the window."""
        now = time.monotonic() if now is None else now
        with self._lock:
            at = self._seen.get(key)
            if at is not None and now - at < self.window:
                return False
            self._seen[key] = now
            if len(self._seen) > 4096:
                self._prune(now)
            return True

    def add(self, key, now: float | None = None) -> None:
        with self._lock:
            self._seen[key] = time.monotonic() if now is None else now

    def __contains__(self, key) -> bool:
        with self._lock:
            at = self._seen.get(key)
            return at is not None and time.monotonic() - at < self.window

    def _prune(self, now: float) -> None:
        stale = [k for k, at in self._seen.items() if now - at >= self.window]
        for k in stale:
            del self._seen[k]

    def prune(self) -> None:
        with self._lock:
            self._prune(time.monotonic())


@dataclass
class PeerState:
    addr: str
    sock: "socket.socket"
    box: object  # nacl.public.Box
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    recv_thread: threading.Thread | None = None
    # True if we dialled, False if they did, None if the caller didn't say.
    outbound: bool | None = None
    connected_at: float = field(default_factory=time.monotonic)
    # False until our introductions (profile, keys) have gone out. Until then
    # the session carries no routed traffic: a message relayed to this peer
    # before it holds the sender's key could not be decrypted.
    introduced: bool = False


@dataclass
class _Outgoing:
    """A message we originated and still owe to at least one recipient."""

    group_id: bytes
    text: str
    timestamp: int
    # Recipients whose ACK has not arrived yet.
    pending: set[str] = field(default_factory=set)


@dataclass
class _Held:
    """A frame held for a destination we could not reach."""

    key: tuple
    frame: bytes
    at: float


class ConnectionManager:
    def __init__(
        self,
        local_mac: str,
        private_key: PrivateKey,
        group_store: GroupStore,
        display_name: str = "",
        storage: "Storage | None" = None,
    ):
        self.local_mac = local_mac.upper()
        self.local_mac_bytes = protocol.mac_to_bytes(self.local_mac)
        self.private_key = private_key
        self.group_store = group_store
        self.storage = storage
        # Leave empty when unset so group_store.display_name() falls back to
        # the MAC. Broadcasting our own MAC as a name would produce a pointless
        # "AA:BB:… is now known as AA:BB:…" line on every peer reconnect.
        self.display_name = display_name
        if display_name:
            self.group_store.set_name(self.local_mac, display_name)

        # Shared connectivity view. Fed here (connect/disconnect/relay) and by
        # the scanner (sightings, dial failures); read by the CLI and GUI.
        self.presence = PresenceTracker(storage=storage, local_mac=self.local_mac)

        self.peers: dict[str, PeerState] = {}
        self.peers_lock = threading.Lock()

        # --- Outbound delivery state ---
        self.unacked: dict[bytes, dict[str, bytes]] = {}  # msg_id -> {addr -> frame}
        self._outgoing: dict[bytes, _Outgoing] = {}
        # Every msg_id we originated this run, acked or not, so receipts for
        # our own messages stop here instead of being flooded onward.
        self._sent_ids: set[bytes] = set()
        self._last_attempt: dict[tuple[bytes, str], float] = {}
        # (group_id, member) pairs known to hold the group, so we stop sending
        # them its GROUP_SETUP ahead of every message.
        self._group_confirmed: set[tuple[bytes, str]] = set()
        # Read receipts we could not deliver because the sender had no path.
        self._incoming_sender: dict[bytes, str] = {}
        self._reads_owed: dict[str, list[bytes]] = {}

        # --- Dedup ---
        # Fallback in-memory message dedup, used only when storage is None
        # (tests). Production always goes through Storage.claim_seen().
        self._seen_fallback: set[bytes] = set()
        self._seen_fallback_lock = threading.Lock()
        self.seen_relayed = _Recent(RELAY_DEDUP_WINDOW)  # (msg_id, dest_bytes)
        self.seen_acks = _Recent(RECEIPT_DEDUP_WINDOW)  # (msg_id, from_bytes)
        self.seen_reads = _Recent(RECEIPT_DEDUP_WINDOW)  # (msg_id, from_bytes)
        self._seen_setups = _Recent(RECEIPT_DEDUP_WINDOW)  # group_id (non-member relay)
        # (receipt key, neighbour) — each receipt crosses each link at most once
        # per window, which is what stops a receipt flood looping.
        self._receipt_edges = _Recent(RECEIPT_DEDUP_WINDOW)
        # msg_id -> original sender, for messages we relayed, so their
        # receipts can be steered (or held) for that sender instead of flooded.
        self._relayed_sender: dict[bytes, str] = {}

        # --- Routing ---
        self.relay_queue: dict[str, list[_Held]] = {}  # dest -> held frames
        # neighbour -> {wire id: hops as that neighbour advertised them}
        self.routes_from: dict[str, dict[str, int]] = {}
        # Best next hop for every destination reachable only through a relay.
        # Replaced wholesale (never mutated) so readers need no lock.
        self.indirect_via: dict[str, str] = {}
        self._hops: dict[str, int] = {}
        self._routes_lock = threading.RLock()
        self._routes_sent: dict[str, bytes] = {}
        # Re-entrant: a send that fails while advertising removes the peer,
        # which re-advertises on the same thread.
        self._advertise_lock = threading.RLock()

        # transport bond MAC -> peer wire id, for peers whose wire id differs
        # from the BT address we dialed/accepted (e.g. Android, which hides its
        # real MAC and announces a random wire id in the handshake). Lets the
        # scanner dedup redials by transport while peers stay keyed by wire id.
        self.peer_by_transport: dict[str, str] = {}

        # Rebuild outbound state from storage. Frames are re-encrypted (fresh
        # nonce) but keep their msg_id, so a receiver that already has one
        # drops the resend as a duplicate and just re-ACKs.
        if storage is not None:
            for msg in storage.load_unacked_outbound(self.local_mac):
                out = _Outgoing(
                    group_id=msg.group_id,
                    text=msg.body,
                    timestamp=msg.ts or int(time.time()),
                    pending=set(msg.recipients),
                )
                self._outgoing[msg.msg_id] = out
                self._sent_ids.add(msg.msg_id)
                entry: dict[str, bytes] = {}
                for recipient in msg.recipients:
                    frame = self._build_frame(msg.msg_id, out, recipient)
                    if frame is not None:
                        entry[recipient] = frame
                if entry:
                    self.unacked[msg.msg_id] = entry

        # Callbacks (set by CLI layer)
        self.on_message: Callable | None = None  # (group_id, sender_mac, text, msg_id)
        self.on_peer_change: Callable | None = None  # (addr, connected)
        self.on_group_setup: Callable | None = None  # (Group)
        self.on_ack: Callable | None = None  # (msg_id, from_mac)
        self.on_read: Callable | None = None  # (msg_id, from_mac)
        self.on_profile: Callable | None = None  # (addr, name)

    # --- Peer lifecycle ---

    def add_peer(
        self, sock: "socket.socket", addr: str, outbound: bool | None = None
    ) -> bool:
        """Handshake with a peer and start its recv thread.

        `outbound` is True when we dialled, False when we accepted. It feeds
        the duplicate-session tiebreak (PROTOCOL.md): when both sides dial at
        once, both keep the session opened by the lower wire id. Returns True
        when we end up with a live session to this peer — including when this
        socket lost the tiebreak to one that is already up.
        """
        transport_mac = addr.upper()

        try:
            pubkey_bytes = bytes(self.private_key.public_key)
            sock.sendall(protocol.encode_handshake(pubkey_bytes, self.local_mac_bytes))

            prev_timeout = sock.gettimeout()
            sock.settimeout(HANDSHAKE_TIMEOUT)
            try:
                frame_type, payload = protocol.read_frame(sock)
            finally:
                sock.settimeout(prev_timeout)

            if frame_type != protocol.TYPE_HANDSHAKE:
                sock.close()
                return False
            try:
                peer_pubkey, wire_id = protocol.decode_handshake(payload)
            except ValueError:
                sock.close()
                return False
        except (ConnectionError, OSError):
            try:
                sock.close()
            except Exception:
                pass
            return False

        # The wire id is the peer's addressing identity. For Linux/Windows it
        # equals the transport BT MAC; for Android it's a random id that stands
        # in for the API-31-masked hardware MAC. Key the peer by wire id so MSG
        # sender/dest, ACKs, and routing line up across platforms. Legacy peers
        # send no wire id — fall back to transport.
        addr = protocol.bytes_to_mac(wire_id) if wire_id else transport_mac
        if addr == self.local_mac:
            # Our own advertisement echoed back, or a clone of our identity.
            sock.close()
            return False

        previous_key = self.group_store.get_pubkey(addr)
        self.group_store.add_pubkey(addr, peer_pubkey)
        if previous_key is not None and previous_key != peer_pubkey:
            # The peer reinstalled or reset its identity. Anything we queued
            # for it is sealed to a key it no longer holds and would be
            # retried forever — reseal it.
            self._reseal_for(addr)

        box = crypto.derive_box(self.private_key, peer_pubkey)
        peer = PeerState(addr=addr, sock=sock, box=box, outbound=outbound)
        peer.recv_thread = threading.Thread(
            target=self._recv_loop,
            args=(peer,),
            daemon=True,
        )

        with self.peers_lock:
            old = self.peers.get(addr)
            if old is not None and not self._should_replace(old, peer):
                keep_old = True
            else:
                keep_old = False
                self.peers[addr] = peer

        if keep_old:
            # This socket lost the tiebreak. The peer applies the same rule
            # and closes its end too, so nobody is left holding a dead half.
            try:
                sock.close()
            except Exception:
                pass
            return True

        if transport_mac != addr:
            self.peer_by_transport[transport_mac] = addr

        if old is not None:
            # Tear down the replaced session. Its recv loop will see the
            # closed socket, exit, and call remove_peer(expected=old) — which
            # is a no-op now that self.peers[addr] points at the new one.
            old.stop.set()
            try:
                old.sock.close()
            except Exception:
                pass

        peer.recv_thread.start()

        # Introductions, in dependency order: who we are and the keys we know
        # to the newcomer; the newcomer's key to everyone else. Only then does
        # the session start carrying routed traffic and appear in our ROUTES,
        # so nobody is sent a message whose sender's key has not reached them.
        if self.display_name:
            self.send_to(addr, protocol.encode_profile(self.display_name))
        self._send_peer_annc(addr)
        self._announce_newcomer(addr)
        with self.peers_lock:
            peer.introduced = True
            # Pop the relay queue atomically with the flag so a concurrent
            # _hold either sees the peer as routable or queues before we pop.
            held = self.relay_queue.pop(addr, [])

        self.presence.record_connected(addr)
        self._on_topology_change()
        self._send_shared_group_setups(addr)

        if self.on_peer_change:
            self.on_peer_change(addr, True)

        for item in held:
            self.send_to(addr, item.frame)
        self._resend_to(addr, force=True)
        self._flush_reads(addr)

        return True

    def _should_replace(self, old: PeerState, new: PeerState) -> bool:
        """Duplicate-session tiebreak. Both ends must reach the same answer.

        Sessions that complete within DUPLICATE_SESSION_WINDOW of each other
        in opposite directions are a crossed dial: keep the one initiated by
        the lower wire id. Otherwise the newer one wins — the old socket is
        most likely dead and has not noticed yet.
        """
        if old.stop.is_set():
            return True
        if (
            old.outbound is None
            or new.outbound is None
            or old.outbound == new.outbound
            or new.connected_at - old.connected_at > DUPLICATE_SESSION_WINDOW
        ):
            return True
        local_is_lower = self.local_mac_bytes < protocol.mac_to_bytes(new.addr)
        # The session we initiated is the one the lower id opened iff we are
        # the lower id. Keep whichever of the two that is.
        new_opened_by_lower = new.outbound == local_is_lower
        return new_opened_by_lower

    def remove_peer(self, addr: str, expected: PeerState | None = None) -> None:
        """Disconnect peer. If expected is set, only remove if it matches."""
        addr = addr.upper()
        with self.peers_lock:
            peer = self.peers.get(addr)
            if peer is None:
                return
            if expected is not None and peer is not expected:
                return  # Don't remove a reconnected peer
            del self.peers[addr]
        peer.stop.set()
        try:
            peer.sock.close()
        except Exception:
            pass
        with self._routes_lock:
            self.routes_from.pop(addr, None)
            self._routes_sent.pop(addr, None)
        # Drop the transport mapping (peers whose wire id != their bond MAC).
        stale_tp = [t for t, w in self.peer_by_transport.items() if w == addr]
        for t in stale_tp:
            self.peer_by_transport.pop(t, None)
        self.presence.record_disconnected(addr)
        if self.on_peer_change:
            self.on_peer_change(addr, False)
        self._on_topology_change()

    def is_connected(self, transport_mac: str) -> bool:
        """True if a live peer owns this transport BT MAC — keyed directly by it
        (Linux/Windows) or via a learned wire-id mapping (Android). The scanner
        uses this to skip redialing devices that are already connected."""
        transport_mac = transport_mac.upper()
        with self.peers_lock:
            if transport_mac in self.peers:
                return True
            wire = self.peer_by_transport.get(transport_mac)
            return wire is not None and wire in self.peers

    def is_reachable(self, addr: str) -> bool:
        """A message sent now has a path: a live session or a relay route."""
        addr = addr.upper()
        with self.peers_lock:
            if addr in self.peers:
                return True
        return addr in self.indirect_via

    def send_to(self, addr: str, frame: bytes) -> bool:
        """Send raw frame to peer. Returns False on error (removes peer)."""
        with self.peers_lock:
            peer = self.peers.get(addr)
        if peer is None:
            return False
        try:
            with peer.send_lock:
                peer.sock.sendall(frame)
            return True
        except (ConnectionError, OSError):
            self.remove_peer(addr, expected=peer)
            return False

    # --- Messaging ---

    def _build_frame(self, msg_id: bytes, out: _Outgoing, dest: str) -> bytes | None:
        """Seal `out` for `dest`. None if we hold no key for it yet."""
        pubkey = self.group_store.get_pubkey(dest)
        if pubkey is None:
            return None
        try:
            dest_bytes = protocol.mac_to_bytes(dest)
        except ValueError:
            return None
        box = crypto.derive_box(self.private_key, pubkey)
        encrypted = crypto.encrypt(box, out.text.encode("utf-8"))
        try:
            return protocol.encode_message(
                out.group_id,
                msg_id,
                self.local_mac_bytes,
                dest_bytes,
                encrypted,
                timestamp=out.timestamp,
            )
        except protocol.FrameTooLarge:
            return None

    def _reseal_for(self, addr: str) -> None:
        for msg_id, out in list(self._outgoing.items()):
            if addr not in out.pending:
                continue
            frame = self._build_frame(msg_id, out, addr)
            if frame is not None:
                self.unacked.setdefault(msg_id, {})[addr] = frame

    def send_message(
        self, group_id: bytes, text: str, dest_addrs: list[str]
    ) -> tuple[bytes, list[str], list[str]]:
        """Encrypt and send message to all dests.

        Returns (msg_id, sent_addrs, skipped_addrs). `skipped_addrs` are
        dests without a pubkey; they are kept pending and sent as soon as a
        key arrives. Raises protocol.FrameTooLarge if the text exceeds the wire
        frame limit.
        """
        msg_id = protocol.new_msg_id()
        now = int(time.time())
        recipients = []
        for dest_addr in dest_addrs:
            dest_addr = dest_addr.upper()
            if dest_addr != self.local_mac and dest_addr not in recipients:
                recipients.append(dest_addr)
        out = _Outgoing(
            group_id=group_id, text=text, timestamp=now, pending=set(recipients)
        )

        # Build all frames first, before any network send. Must register in
        # self.unacked BEFORE routing so an ACK returning on a fast loop can't
        # race past and find the entry missing.
        unacked_entry: dict[str, bytes] = {}
        skipped: list[str] = []
        # Size depends only on the text, so check it once up front and let
        # FrameTooLarge escape before anything is persisted.
        protocol.encode_message(
            group_id,
            msg_id,
            self.local_mac_bytes,
            self.local_mac_bytes,
            b"\x00" * (len(text.encode("utf-8")) + 40),
        )
        for dest_addr in recipients:
            frame = self._build_frame(msg_id, out, dest_addr)
            if frame is None:
                skipped.append(dest_addr)
            else:
                unacked_entry[dest_addr] = frame

        # Persist before sending so a crash mid-send still gives us a chance
        # to retransmit on restart (we rebuild self.unacked from storage).
        if self.storage is not None and recipients:
            self.storage.save_outgoing_message(
                msg_id, group_id, self.local_mac, text, now, recipients
            )

        self._sent_ids.add(msg_id)
        if recipients:
            self._outgoing[msg_id] = out
        if not unacked_entry:
            return msg_id, [], skipped

        self.unacked[msg_id] = unacked_entry
        # Iterate a copy: on a fast link the first recipient's ACK can arrive,
        # and pop from this very dict, before we have sent to the second.
        for dest_addr, frame in list(unacked_entry.items()):
            self._deliver(msg_id, dest_addr, frame)

        return msg_id, list(unacked_entry.keys()), skipped

    def _deliver(self, msg_id: bytes, dest: str, frame: bytes) -> None:
        """One delivery attempt of one of our own messages."""
        self._last_attempt[(msg_id, dest)] = time.monotonic()
        out = self._outgoing.get(msg_id)
        if out is not None and out.group_id != GROUP_ZERO_ID:
            # Make sure the group reaches them ahead of its first message, over
            # the same path, so they can file it. Stops once they confirm.
            if (out.group_id, dest) not in self._group_confirmed:
                setup = self._group_setup_frame(out.group_id)
                if setup is not None:
                    self._route_frame(dest, setup)
        # Our own message is held by `unacked`, not the relay queue.
        self._route_frame(dest, frame, hold=False)

    def _resend_to(self, addr: str, force: bool = False) -> None:
        """Push every message still owed to `addr` down its current path."""
        now = time.monotonic()
        for msg_id, dests in list(self.unacked.items()):
            frame = dests.get(addr)
            if frame is None:
                continue
            last = self._last_attempt.get((msg_id, addr), 0.0)
            if not force and now - last < RETRY_INTERVAL:
                continue
            self._deliver(msg_id, addr, frame)

    def create_group(self, name: str, member_addrs: list[str]) -> Group:
        """Create a group and send GROUP_SETUP to all members."""
        members: dict[str, bytes] = {}
        members[self.local_mac] = bytes(self.private_key.public_key)

        for addr in member_addrs:
            addr = addr.upper()
            pubkey = self.group_store.get_pubkey(addr)
            if pubkey is None:
                raise ValueError(f"No pubkey for {addr} — not yet connected")
            members[addr] = pubkey

        group_id = protocol.new_group_id()
        group = Group(group_id=group_id, members=members, name=name)
        self.group_store.add_group(group)

        frame = self._group_setup_frame(group_id)
        assert frame is not None
        for addr in members:
            if addr != self.local_mac:
                self._route_frame(addr, frame)

        return group

    def _group_setup_frame(self, group_id: bytes) -> bytes | None:
        group = self.group_store.groups.get(group_id)
        if group is None:
            return None
        try:
            member_list = [
                (protocol.mac_to_bytes(a), pk) for a, pk in group.members.items()
            ]
            return protocol.encode_group_setup(group_id, member_list, group.name)
        except (ValueError, protocol.FrameTooLarge):
            return None

    def _send_shared_group_setups(self, addr: str) -> None:
        """Remind a newly connected member of every group we share.

        Cheap (a few hundred bytes each) and it covers every way a member can
        miss a setup: offline at creation, a relay that dropped it, a restart
        that lost an in-memory queue. The receiver drops a group it already has.
        """
        for group_id, group in list(self.group_store.groups.items()):
            if addr in group.members and self.local_mac in group.members:
                frame = self._group_setup_frame(group_id)
                if frame is not None:
                    self.send_to(addr, frame)

    # --- Routing ---

    def _next_hops(self, dest: str, exclude: str | None = None) -> list[str]:
        """Neighbours advertising a route to `dest`, best first."""
        with self._routes_lock:
            options = [
                (hops, neighbour)
                for neighbour, table in self.routes_from.items()
                if neighbour not in (dest, exclude)
                and (hops := table.get(dest)) is not None
            ]
        options.sort()
        return [n for _, n in options]

    def _route_frame(
        self,
        dest_addr: str,
        frame: bytes,
        exclude: str | None = None,
        hold: bool = True,
        key: tuple | None = None,
    ) -> bool:
        """Send `frame` toward `dest_addr` — direct, via a route, or flood.

        `exclude` names the neighbour the frame came from, so it is never
        bounced straight back. `hold` keeps a copy in the relay queue when no
        path is known; the sender's own messages pass False because `unacked`
        already holds them.
        """
        with self.peers_lock:
            direct = self._routable(dest_addr)
        if direct and self.send_to(dest_addr, frame):
            return True

        for hop in self._next_hops(dest_addr, exclude):
            if self.send_to(hop, frame):
                return True

        # No known path. Hand a copy to every neighbour: whoever meets the
        # destination first delivers it (each forwards at most once per
        # dedup window, which bounds the flood).
        with self.peers_lock:
            neighbours = [
                a
                for a in self.peers
                if a not in (dest_addr, exclude) and self._routable(a)
            ]
        for neighbour in neighbours:
            self.send_to(neighbour, frame)

        if hold:
            self._hold(dest_addr, frame, key)
        return True

    def _hold(self, dest: str, frame: bytes, key: tuple | None) -> None:
        key = key if key is not None else (frame,)
        with self.peers_lock:
            if self._routable(dest):
                connected_now = True
            else:
                connected_now = False
                held = self.relay_queue.setdefault(dest, [])
                if not any(h.key == key for h in held):
                    held.append(_Held(key=key, frame=frame, at=time.monotonic()))
                    del held[:-RELAY_QUEUE_PER_DEST]
        if connected_now:
            # dest connected while we were trying relays; add_peer's flush
            # would have missed this frame.
            self.send_to(dest, frame)

    def _routable(self, addr: str) -> bool:
        """A live, introduced session. Caller holds peers_lock."""
        peer = self.peers.get(addr)
        return peer is not None and peer.introduced

    def _drop_held(self, dest: str, key: tuple) -> None:
        with self.peers_lock:
            held = self.relay_queue.get(dest)
            if not held:
                return
            kept = [h for h in held if h.key != key]
            if kept:
                self.relay_queue[dest] = kept
            else:
                del self.relay_queue[dest]

    def _hand_off_held(self, dest: str) -> None:
        """A path to `dest` appeared: pass on whatever we were holding for it."""
        hops = self._next_hops(dest)
        if not hops:
            return
        with self.peers_lock:
            held = self.relay_queue.pop(dest, [])
        for item in held:
            if not any(self.send_to(hop, item.frame) for hop in hops):
                self._hold(dest, item.frame, item.key)

    def _on_topology_change(self) -> None:
        """Recompute routes after any session or advertisement change, tell
        the neighbours, update presence, and push anything that just became
        deliverable."""
        with self._routes_lock:
            with self.peers_lock:
                direct = {a for a in self.peers if self._routable(a)}
            best: dict[str, tuple[int, str]] = {}
            for neighbour, table in self.routes_from.items():
                if neighbour not in direct:
                    continue
                for dest, hops in table.items():
                    if dest == self.local_mac or dest in direct:
                        continue
                    candidate = (hops + 1, neighbour)
                    if dest not in best or candidate < best[dest]:
                        best[dest] = candidate
            old_via = self.indirect_via
            self.indirect_via = {dest: via for dest, (_, via) in best.items()}
            self._hops = {dest: hops for dest, (hops, _) in best.items()}
            new_via = self.indirect_via

        for dest, via in new_via.items():
            if old_via.get(dest) != via:
                self.presence.record_relay(dest, via, hops=self._hops.get(dest))
        for dest in old_via:
            if dest not in new_via and dest not in direct:
                self.presence.clear_relay(dest)

        self._advertise_routes()

        for dest in new_via:
            if dest not in old_via:
                # Newly reachable through a relay: deliver what is waiting.
                self._resend_to(dest, force=True)
                self._hand_off_held(dest)
                self._flush_reads(dest)

    def _routes_for(self, neighbour: str, direct: set[str]) -> list[tuple[bytes, int]]:
        """What we tell `neighbour` we can reach. Split horizon: never
        advertise a route back to the neighbour it goes through."""
        routes: list[tuple[bytes, int]] = []
        for dest in sorted(direct):
            if dest != neighbour:
                routes.append((protocol.mac_to_bytes(dest), 1))
        for dest, via in sorted(self.indirect_via.items()):
            hops = self._hops.get(dest, 0)
            if via == neighbour or dest == neighbour or hops > protocol.MAX_ROUTE_HOPS:
                continue
            routes.append((protocol.mac_to_bytes(dest), hops))
        return routes[:255]

    def _advertise_routes(self) -> None:
        with self._advertise_lock:
            with self._routes_lock:
                with self.peers_lock:
                    direct = {a for a in self.peers if self._routable(a)}
                outgoing = []
                for neighbour in direct:
                    try:
                        frame = protocol.encode_routes(
                            self._routes_for(neighbour, direct)
                        )
                    except ValueError:
                        continue
                    if self._routes_sent.get(neighbour) != frame:
                        outgoing.append((neighbour, frame))
            for neighbour, frame in outgoing:
                if self.send_to(neighbour, frame):
                    with self._routes_lock:
                        self._routes_sent[neighbour] = frame

    def _handle_routes(self, from_addr: str, payload: bytes) -> None:
        table: dict[str, int] = {}
        for wire_id, hops in protocol.decode_routes(payload):
            dest = protocol.bytes_to_mac(wire_id)
            if dest != self.local_mac and dest != from_addr:
                table[dest] = min(hops, table.get(dest, hops))
        with self._routes_lock:
            if self.routes_from.get(from_addr, {}) == table:
                self.routes_from[from_addr] = table
                return
            self.routes_from[from_addr] = table
        self._on_topology_change()

    # --- Maintenance ---

    def maintain(self) -> None:
        """Periodic housekeeping. Call every few seconds (discovery.maintainer).

        Retries messages whose recipient is reachable only through a relay —
        the one kind of loss nothing else notices — and expires old state.
        """
        # Recipients whose key has arrived since we sent.
        for msg_id, out in list(self._outgoing.items()):
            have = self.unacked.get(msg_id, {})
            for dest in out.pending - set(have):
                frame = self._build_frame(msg_id, out, dest)
                if frame is not None:
                    self.unacked.setdefault(msg_id, {})[dest] = frame
                    self._deliver(msg_id, dest, frame)

        for dest in list(self.indirect_via):
            self._resend_to(dest)

        now = time.monotonic()
        with self.peers_lock:
            for dest in list(self.relay_queue):
                kept = [
                    h for h in self.relay_queue[dest] if now - h.at < RELAY_QUEUE_TTL
                ]
                if kept:
                    self.relay_queue[dest] = kept
                else:
                    del self.relay_queue[dest]
        for recent in (
            self.seen_relayed,
            self.seen_acks,
            self.seen_reads,
            self._seen_setups,
            self._receipt_edges,
        ):
            recent.prune()

    # --- Seen dedup (delegates to storage, falls back to in-memory) ---

    def _claim_seen(self, msg_id: bytes) -> bool:
        """Return True if this is the first time we've seen msg_id."""
        if self.storage is not None:
            return self.storage.claim_seen(msg_id)
        with self._seen_fallback_lock:
            if msg_id in self._seen_fallback:
                return False
            self._seen_fallback.add(msg_id)
            return True

    def _release_seen(self, msg_id: bytes) -> None:
        if self.storage is not None:
            self.storage.release_seen(msg_id)
            return
        with self._seen_fallback_lock:
            self._seen_fallback.discard(msg_id)

    # --- Receive loop ---

    def _recv_loop(self, peer: PeerState) -> None:
        handlers = {
            protocol.TYPE_MESSAGE: self._handle_message,
            protocol.TYPE_ACK: self._handle_ack,
            protocol.TYPE_READ: self._handle_read,
            protocol.TYPE_GROUP_SETUP: self._handle_group_setup,
            protocol.TYPE_PROFILE: self._handle_profile,
            protocol.TYPE_PEER_ANNC: self._handle_peer_annc,
            protocol.TYPE_ROUTES: self._handle_routes,
        }
        try:
            while not peer.stop.is_set():
                frame_type, payload = protocol.read_frame(peer.sock)
                handler = handlers.get(frame_type)
                if handler is None:
                    # A newer peer's frame type. Ignoring it is what lets the
                    # protocol grow without breaking older builds.
                    continue
                try:
                    handler(peer.addr, payload)
                except (ConnectionError, OSError):
                    raise
                except Exception as e:
                    # One bad frame (or a UI callback bug) costs that frame,
                    # never the whole session.
                    print(f"[recv {peer.addr}] dropped frame 0x{frame_type:02x}: {e!r}")
                    if not isinstance(e, ValueError):
                        traceback.print_exc()
        except (ConnectionError, OSError):
            pass
        except Exception as e:
            print(f"[recv {peer.addr}] unexpected error: {e!r}")
        finally:
            self.remove_peer(peer.addr, expected=peer)

    def _handle_message(self, from_addr: str, payload: bytes) -> None:
        gid, msg_id, sender_bytes, dest_bytes, ts, encrypted = protocol.decode_message(
            payload
        )
        final_dest = protocol.bytes_to_mac(dest_bytes)
        sender = protocol.bytes_to_mac(sender_bytes)

        if final_dest != self.local_mac:
            self._relay_message(
                from_addr, sender, final_dest, msg_id, dest_bytes, payload
            )
            return

        # Atomic dedup claim. Two relay paths delivering the same msg_id
        # concurrently must not both reach on_message.
        if not self._claim_seen(msg_id):
            self.send_to(from_addr, protocol.encode_ack(msg_id, self.local_mac_bytes))
            return

        # Decrypt using sender's pubkey. On any failure we must release the
        # claim — otherwise sender's retransmit (e.g. once their pubkey reaches
        # us) would be silently dropped by the dedup check above.
        pubkey = self.group_store.get_pubkey(sender)
        if pubkey is None:
            self._release_seen(msg_id)
            return

        box = crypto.derive_box(self.private_key, pubkey)
        try:
            plaintext = crypto.decrypt(box, encrypted)
            text = plaintext.decode("utf-8")
        except Exception:
            self._release_seen(msg_id)
            return

        if gid != GROUP_ZERO_ID:
            # They sent into this group, so they hold it.
            self._group_confirmed.add((gid, sender))
        self._incoming_sender[msg_id] = sender

        if self.storage is not None:
            self.storage.save_incoming_body(msg_id, gid, sender, text, ts)

        # ACK before the UI callback: a slow or failing UI must not cost the
        # sender its delivery receipt.
        self.send_to(from_addr, protocol.encode_ack(msg_id, self.local_mac_bytes))

        if self.on_message:
            self.on_message(gid, sender, text, msg_id)

    def _relay_message(
        self,
        from_addr: str,
        sender: str,
        final_dest: str,
        msg_id: bytes,
        dest_bytes: bytes,
        payload: bytes,
    ) -> None:
        if sender == self.local_mac:
            return  # our own frame came back around a loop
        if final_dest == from_addr:
            return  # nonsense: would echo back to the peer that sent it
        key = (msg_id, dest_bytes)
        fresh = self.seen_relayed.claim(key)
        # A frame handed over by its own sender is always passed on: the
        # sender only resends on purpose (a retry, a route that just came
        # back), and a loop can never bring a frame back *from* its sender.
        # Dedup exists for copies that have already been relayed.
        if not fresh and from_addr != sender:
            return
        self._relayed_sender[msg_id] = sender
        if len(self._relayed_sender) > 8192:
            for old_id in list(self._relayed_sender)[:4096]:
                self._relayed_sender.pop(old_id, None)
        frame = protocol.encode_frame(protocol.TYPE_MESSAGE, payload)
        self._route_frame(final_dest, frame, exclude=from_addr, key=key)

    def _handle_ack(self, from_addr: str, payload: bytes) -> None:
        msg_id, from_mac_bytes = protocol.decode_ack(payload)
        ack_from = protocol.bytes_to_mac(from_mac_bytes)
        key = (msg_id, from_mac_bytes)

        if self.seen_acks.claim(key):
            out = self._outgoing.get(msg_id)
            if out is not None:
                out.pending.discard(ack_from)
                if out.group_id != GROUP_ZERO_ID:
                    self._group_confirmed.add((out.group_id, ack_from))
                if not out.pending:
                    self._outgoing.pop(msg_id, None)
            dests = self.unacked.get(msg_id)
            if dests is not None:
                dests.pop(ack_from, None)
                if not dests:
                    self.unacked.pop(msg_id, None)
            self._last_attempt.pop((msg_id, ack_from), None)

            # Delivered: stop carrying copies of it around on the recipient's
            # behalf.
            self._drop_held(ack_from, key)

            # mark_acked is a no-op for relay traffic (no matching row); safe
            # to call unconditionally.
            if self.storage is not None:
                self.storage.mark_acked(msg_id, ack_from)

            if self.on_ack:
                self.on_ack(msg_id, ack_from)

        if msg_id in self._sent_ids:
            return  # it reached the sender; nobody else needs it
        frame = protocol.encode_frame(protocol.TYPE_ACK, payload)
        self._forward_receipt(key, frame, from_addr, ack_from)

    def _forward_receipt(
        self, key: tuple[bytes, bytes], frame: bytes, from_addr: str, origin: str
    ) -> None:
        """Pass an ACK / READ on toward the message's sender.

        If we relayed the message we know who that is: steer the receipt
        there, or hold it until they are back. Otherwise flood it. Each receipt
        crosses each link at most once per window, which ends any loop; one
        handed over by the device that issued it always goes on, because that
        device only resends it deliberately.
        """
        msg_id = key[0]
        sender = self._relayed_sender.get(msg_id)
        if sender is not None and sender != from_addr:
            with self.peers_lock:
                direct = sender in self.peers
            if direct:
                targets = [sender]
            else:
                targets = self._next_hops(sender, exclude=from_addr)[:1]
            if not targets:
                self._hold(sender, frame, ("receipt", frame[0]) + key)
                return
        else:
            with self.peers_lock:
                targets = [a for a in self.peers if a != from_addr]
        first_hop = from_addr == origin
        for target in targets:
            if self._receipt_edges.claim((frame[0], key, target)) or first_hop:
                self.send_to(target, frame)

    def send_read(self, msg_id: bytes) -> None:
        """Flood a READ receipt toward the original sender (like ACK
        flood-back). Held for later if the sender has no path right now."""
        frame = protocol.encode_read(msg_id, self.local_mac_bytes)
        self.seen_reads.add((msg_id, self.local_mac_bytes))
        sender = self._incoming_sender.get(msg_id)
        if sender is not None and not self.is_reachable(sender):
            self._reads_owed.setdefault(sender, []).append(msg_id)
            return
        with self.peers_lock:
            targets = list(self.peers.keys())
        if sender is not None and sender in targets:
            targets = [sender]
        for addr in targets:
            self.send_to(addr, frame)

    def _flush_reads(self, sender: str) -> None:
        owed = self._reads_owed.pop(sender, [])
        for msg_id in owed:
            self.send_read(msg_id)

    def _handle_read(self, from_addr: str, payload: bytes) -> None:
        msg_id, from_mac_bytes = protocol.decode_read(payload)
        reader = protocol.bytes_to_mac(from_mac_bytes)
        key = (msg_id, from_mac_bytes)

        if self.seen_reads.claim(key):
            if self.storage is not None:
                self.storage.mark_read(msg_id, reader)
            if self.on_read:
                self.on_read(msg_id, reader)

        if msg_id in self._sent_ids:
            return  # a receipt for our own message; nobody else needs it
        frame = protocol.encode_frame(protocol.TYPE_READ, payload)
        self._forward_receipt(key, frame, from_addr, reader)

    def _handle_profile(self, from_addr: str, payload: bytes) -> None:
        name = protocol.decode_profile(payload)
        # Treat empty name and name-equals-MAC as "no self-chosen name" —
        # clear any previous entry so display falls back to MAC or override.
        # The MAC check handles peers running pre-fix code that broadcast
        # their own MAC as a default name.
        if not name or name == from_addr:
            had_name = from_addr in self.group_store.names
            self.group_store.clear_name(from_addr)
            if had_name and self.on_profile:
                self.on_profile(from_addr, "")
            return
        # Only surface an actual change. A reconnect re-sends PROFILE, and
        # announcing "X is now known as X" on every reconnect is noise.
        changed = self.group_store.names.get(from_addr) != name
        self.group_store.set_name(from_addr, name)
        if changed and self.on_profile:
            self.on_profile(from_addr, name)
        if not changed:
            return
        # Re-announce updated name to all other connected peers so indirect
        # peers learn this device's nick without waiting for a reconnect.
        pubkey = self.group_store.get_pubkey(from_addr)
        if pubkey is not None:
            annc = protocol.encode_peer_annc(
                [(protocol.mac_to_bytes(from_addr), pubkey, name)]
            )
            with self.peers_lock:
                targets = [a for a in self.peers if a != from_addr]
            for addr in targets:
                self.send_to(addr, annc)

    def _send_peer_annc(self, to_addr: str) -> None:
        """Send our known peers (MAC+pubkey+name) to a single peer."""
        entries = []
        for addr, pubkey in list(self.group_store.pubkeys.items()):
            if addr == self.local_mac or addr == to_addr or len(pubkey) != 32:
                continue
            try:
                mac_bytes = protocol.mac_to_bytes(addr)
            except ValueError:
                # A row written by an older build, or hand-edited. Skip it
                # rather than letting one bad address abort the introduction —
                # this runs inside add_peer, on the accept/scan thread.
                continue
            entries.append((mac_bytes, pubkey, self.group_store.names.get(addr, "")))
        # peer_count is a uint8; announce the first 255 and let the rest
        # propagate on later connections.
        if entries:
            self.send_to(to_addr, protocol.encode_peer_annc(entries[:255]))

    def _announce_newcomer(self, addr: str) -> None:
        """Tell existing peers about a new one, so they hold its key before
        any message from it arrives through us."""
        pubkey = self.group_store.get_pubkey(addr)
        if pubkey is None:
            return
        annc = protocol.encode_peer_annc(
            [
                (
                    protocol.mac_to_bytes(addr),
                    pubkey,
                    self.group_store.names.get(addr, ""),
                )
            ]
        )
        with self.peers_lock:
            existing = [a for a in self.peers if a != addr]
        for existing_addr in existing:
            self.send_to(existing_addr, annc)

    def _handle_peer_annc(self, from_addr: str, payload: bytes) -> None:
        learned: list[str] = []
        onward: list[tuple[bytes, bytes, str]] = []
        with self.peers_lock:
            direct = set(self.peers)
        for mac_bytes, pubkey, name in protocol.decode_peer_annc(payload):
            addr = protocol.bytes_to_mac(mac_bytes)
            if addr == self.local_mac or len(pubkey) != 32:
                continue
            new_key = self.group_store.get_pubkey(addr) is None
            self.group_store.add_pubkey_if_missing(addr, pubkey)
            if new_key:
                learned.append(addr)
            renamed = bool(name) and self._accept_name(addr, name, from_addr, direct)
            if renamed:
                self.group_store.set_name(addr, name)
                if self.on_profile:
                    self.on_profile(addr, name)
            if new_key or renamed:
                known = self.group_store.names.get(addr, "")
                onward.append(
                    (mac_bytes, self.group_store.get_pubkey(addr) or pubkey, known)
                )
        # Reachability comes from ROUTES, not from here: an announcement lists
        # everyone the sender has *ever* met, and calling those reachable would
        # promise delivery to people who left the plane last week.
        if learned:
            self.presence.note_known(learned)
        if not onward:
            return
        # Pass new keys and fresher names on, so a device several hops away can
        # be written to — by name — as soon as ROUTES says it is reachable.
        # Only *changes* travel, so each piece of news crosses the mesh once.
        annc = protocol.encode_peer_annc(onward[:255])
        with self.peers_lock:
            targets = [a for a in self.peers if a != from_addr]
        for addr in targets:
            self.send_to(addr, annc)

    def _accept_name(
        self, addr: str, name: str, from_addr: str, direct: set[str]
    ) -> bool:
        """Should a second-hand name for `addr` replace what we have?

        Names carry no version, so a stale one could circulate forever. The
        rule that prevents it: a name travels outward from its owner along the
        route. We take a changed name only from our next hop toward `addr` —
        someone strictly closer to the source — and fill a gap from anyone.
        A peer we talk to directly has told us its own name; nobody overrides it.
        """
        if addr in direct:
            return False
        current = self.group_store.names.get(addr)
        if current == name:
            return False
        if current is None:
            return True
        return self.indirect_via.get(addr) == from_addr

    def set_display_name(self, name: str) -> None:
        """Update our own display name and broadcast to all connected peers."""
        self.display_name = name
        if name:
            self.group_store.set_name(self.local_mac, name)
        else:
            self.group_store.clear_name(self.local_mac)
        if self.storage is not None:
            self.storage.set_display_name(name)
        frame = protocol.encode_profile(name)
        with self.peers_lock:
            targets = list(self.peers.keys())
        for addr in targets:
            self.send_to(addr, frame)

    def _handle_group_setup(self, from_addr: str, payload: bytes) -> None:
        group_id, member_list, name = protocol.decode_group_setup(payload)

        members: dict[str, bytes] = {}
        for mac_bytes, pubkey in member_list:
            members[protocol.bytes_to_mac(mac_bytes)] = pubkey
        frame = protocol.encode_frame(protocol.TYPE_GROUP_SETUP, payload)

        if self.local_mac not in members:
            # We are only a relay for this group. Pass it on toward the
            # members, but never adopt it — it would appear in our UI as a
            # conversation we are not part of.
            if not self._seen_setups.claim(group_id):
                return
            for member_addr in members:
                if member_addr != from_addr:
                    self._route_frame(member_addr, frame, exclude=from_addr)
            return

        if from_addr in members:
            self._group_confirmed.add((group_id, from_addr))
        if group_id in self.group_store.groups:
            return  # Already have this group — don't forward again

        group = Group(group_id=group_id, members=members, name=name)
        self.group_store.add_group(group)

        if self.on_group_setup:
            self.on_group_setup(group)

        # Forward to the other members, who may not have received it.
        for member_addr in members:
            if member_addr in (from_addr, self.local_mac):
                continue
            self._route_frame(member_addr, frame, exclude=from_addr)
