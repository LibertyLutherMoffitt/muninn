"""Mesh — the Muninn core: links, routing, reliable delivery, gossip.

One `Mesh` per device. Transport-agnostic: anything that yields a connected
socket-like object (sendall/recv/close/settimeout) can feed `add_link()`.

Threads
-------
- per link: reader (blocking recv) + writer (priority queue → sendall)
- one maintenance thread: keepalive, retransmit, route adverts, expiry
- one event thread: delivers events to subscribers in order, outside locks

Delivery model
--------------
Every reliable body goes into the persistent outbox per destination and is
retransmitted with backoff whenever the destination is reachable (direct or
via a route), until an end-to-end ACK arrives. Relays forward by next hop from
a distance-vector table; when no route exists they store-and-forward. All
dedup windows are time-bounded so retransmits always get through.
"""

from __future__ import annotations

import hashlib
import json
import itertools
import os
import queue
import random
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from nacl.public import PrivateKey

from muninn import content as C
from muninn import protocol as P
from muninn.crypto import BoxCache
from muninn.storage import (
    AppEvent,
    FileRecord,
    OutboxEntry,
    Storage,
    StoredMessage,
)

ZERO_GROUP = b"\x00" * 16

# --- tunables (seconds) ------------------------------------------------------
HELLO_TIMEOUT = 10.0
PING_INTERVAL = 4.0
LINK_TIMEOUT = 15.0  # no frame received for this long → link is dead
ADVERT_INTERVAL = 4.0
ROUTE_TTL = 13.0  # route entries not refreshed for this long expire
MAX_HOPS = 8
RELAY_DEDUP = 5.0
RETRY_BASE = 6.0
RETRY_MAX = 60.0
RELAY_MAX_AGE = 6 * 3600
RELAY_MAX_ROWS = 5000
TYPING_TTL = 6.0

# file transfer
CHUNK_SIZE = 16000
FILE_WINDOW = 12
CHUNK_TIMEOUT = 5.0
AUTO_DOWNLOAD_MAX = 15 * 1024 * 1024
MAX_FILE_SIZE = 64 * 1024 * 1024
BULK_QUEUE_MAX = 48

PRIO_CONTROL = 0
PRIO_REALTIME = 1
PRIO_CHAT = 2
PRIO_BULK = 3


def dm_conv(node_id: str) -> str:
    return "dm:" + node_id


def group_conv(gid: bytes) -> str:
    return "group:" + gid.hex()


def conv_group_id(conv: str) -> bytes | None:
    return bytes.fromhex(conv[6:]) if conv.startswith("group:") else None


@dataclass
class PeerInfo:
    node_id: str
    pubkey: bytes
    name: str = ""
    name_ts: int = 0
    override: str | None = None
    last_seen: int = 0
    replaced_by: str | None = None  # this identity reinstalled as another

    @property
    def display(self) -> str:
        return self.override or self.name or self.node_id


@dataclass
class Group:
    group_id: bytes
    name: str
    version: int
    members: set[str]
    left: bool = False

    @property
    def conv(self) -> str:
        return group_conv(self.group_id)


@dataclass
class OutItem:
    packet_id: bytes
    dst: str
    body: bytes  # plaintext body (re-encrypted lazily if pubkey was missing)
    ts: int
    msg_id: bytes | None
    flags: int
    frame: bytes | None = None
    attempts: int = 0
    next_try: float = 0.0


@dataclass
class Download:
    rec: FileRecord
    inflight: dict[int, float] = field(default_factory=dict)
    last_chunk: float = 0.0
    started: float = field(default_factory=time.monotonic)
    dirty: int = 0


class Link:
    _seq = itertools.count()

    def __init__(self, mesh: Mesh, sock, transport_addr: str, initiated: bool):
        self.mesh = mesh
        self.sock = sock
        self.transport_addr = transport_addr.upper()
        self.initiated = initiated
        self.node_id: str | None = None
        self.hello: P.Hello | None = None
        self.q: queue.PriorityQueue = queue.PriorityQueue()
        self.bulk_pending = 0
        self.closed = threading.Event()
        self.created = time.monotonic()
        self.last_rx = self.created
        self.last_tx = self.created
        self.rtt: float | None = None
        self._ping: tuple[bytes, float] | None = None
        self.bytes_in = 0
        self.bytes_out = 0

    def send(self, frame: bytes, prio: int = PRIO_CHAT) -> bool:
        if self.closed.is_set():
            return False
        if prio == PRIO_BULK:
            if self.bulk_pending >= BULK_QUEUE_MAX:
                return False
            self.bulk_pending += 1
        self.q.put((prio, next(self._seq), frame))
        return True

    def bulk_room(self) -> int:
        return BULK_QUEUE_MAX - self.bulk_pending

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.sock.close()
        except Exception:
            pass
        self.q.put((-1, -1, None))

    def writer(self) -> None:
        try:
            while not self.closed.is_set():
                prio, _, frame = self.q.get()
                if frame is None:
                    break
                if prio == PRIO_BULK:
                    self.bulk_pending -= 1
                self.sock.sendall(frame)
                self.last_tx = time.monotonic()
                self.bytes_out += len(frame)
        except Exception:
            pass
        finally:
            self.mesh._link_down(self)

    def reader(self) -> None:
        try:
            while not self.closed.is_set():
                ftype, payload = P.read_frame(self.sock)
                self.last_rx = time.monotonic()
                self.bytes_in += len(payload) + 3
                try:
                    self.mesh._on_frame(self, ftype, payload)
                except P.MalformedFrame:
                    continue
                except Exception as e:  # never let one bad frame kill the link
                    self.mesh._log(f"frame {ftype:#x} from {self.node_id}: {e!r}")
        except Exception:
            pass
        finally:
            self.mesh._link_down(self)


class Mesh:
    def __init__(
        self,
        storage: Storage,
        private_key: PrivateKey,
        node_id: str,
        name: str = "",
        name_ts: int = 0,
        log: Callable[[str], None] | None = None,
    ):
        self.storage = storage
        self.private_key = private_key
        self.pubkey = bytes(private_key.public_key)
        self.node_id = node_id
        self.name = name
        self.name_ts = name_ts
        self.boxes = BoxCache(private_key)
        self._logfn = log
        self.lock = threading.RLock()

        self.links: dict[str, Link] = {}  # node_id -> established link
        self._pending: set[Link] = set()
        self.peers: dict[str, PeerInfo] = {}
        self.groups: dict[bytes, Group] = {}
        # dest -> via -> (hops, refreshed_at)
        self.routes: dict[str, dict[str, tuple[int, float]]] = defaultdict(dict)
        self._reachable: set[str] = set()
        self.outbox: dict[bytes, OutItem] = {}
        self._relay_seen: dict[bytes, float] = {}
        self._ack_seen: dict[tuple[bytes, str], float] = {}
        self._unrel_seen: dict[bytes, float] = {}
        self._advert_due = 0.0
        self.downloads: dict[bytes, Download] = {}
        self.files: dict[bytes, FileRecord] = {}
        self._serve_q: list[tuple[str, bytes, int]] = []  # (requester, file_id, idx)
        self.typing: dict[tuple[str, str], float] = {}  # (conv, node) -> until
        self.auto_download_max = AUTO_DOWNLOAD_MAX
        self.stats = defaultdict(int)
        self.blocked: set[str] = storage.blocked()
        self.own_addr: str | None = None  # as seen by a neighbour (ADDRS)
        # forgotten people stay out of the lists (their device may still be
        # around and relaying) until they actually write to us again
        self.hidden: set[str] = set(
            json.loads(storage.get_setting("forgotten_ids", "[]") or "[]")
        )

        self._subs: dict[str, list[Callable]] = defaultdict(list)
        self._events: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        self._load()

    # ------------------------------------------------------------------ setup

    def _load(self) -> None:
        for (
            nid,
            pk,
            name,
            name_ts,
            override,
            last_seen,
            repl,
        ) in self.storage.load_peers():
            self.peers[nid] = PeerInfo(
                nid, pk, name, name_ts, override, last_seen, repl
            )
        for gid, name, version, members, left in self.storage.load_groups():
            self.groups[gid] = Group(gid, name, version, set(members), left)
        for e in self.storage.outbox_load():
            flags = P.F_RELIABLE
            self.outbox[e.packet_id] = OutItem(
                e.packet_id, e.dst, e.body, e.ts, e.msg_id, flags
            )
        for f in self.storage.load_files():
            self.files[f.file_id] = f
            if f.state == "downloading":
                self.downloads[f.file_id] = Download(f)

    def start(self) -> None:
        for target in (self._event_loop, self._maintenance_loop, self._serve_loop):
            t = threading.Thread(target=target, daemon=True, name=target.__name__)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        with self.lock:
            links = list(self.links.values()) + list(self._pending)
        for link in links:
            link.close()
        self._events.put(None)
        for t in self._threads:
            t.join(timeout=2)

    def _log(self, msg: str) -> None:
        if self._logfn:
            self._logfn(f"[{self.name or self.node_id}] {msg}")

    # ------------------------------------------------------------------ events

    def on(self, event: str, fn: Callable) -> None:
        """Subscribe. Events: message, update, peers, group, file, app, rt,
        typing, error. Callbacks run on the event thread, in order."""
        self._subs[event].append(fn)

    def _emit(self, event: str, *args) -> None:
        self._events.put((event, args))

    def _event_loop(self) -> None:
        while True:
            item = self._events.get()
            if item is None:
                return
            event, args = item
            for fn in list(self._subs.get(event, ())):
                try:
                    fn(*args)
                except Exception as e:
                    self._log(f"event handler {event}: {e!r}")

    # ------------------------------------------------------------------ links

    def add_link(self, sock, transport_addr: str, initiated: bool) -> str | None:
        """Handshake on a freshly connected socket. Blocks up to HELLO_TIMEOUT.
        Returns the peer node id on success, None on failure."""
        link = Link(self, sock, transport_addr, initiated)
        with self.lock:
            self._pending.add(link)
        try:
            sock.sendall(
                P.encode_hello(self.pubkey, self.node_id, self.name, self.name_ts)
            )
            prev = sock.gettimeout()
            sock.settimeout(HELLO_TIMEOUT)
            try:
                ftype, payload = P.read_frame(sock)
            finally:
                sock.settimeout(prev)
            if ftype != P.T_HELLO:
                raise ConnectionError(f"expected HELLO, got {ftype:#x}")
            hello = P.decode_hello(payload)
            if hello.version != P.PROTOCOL_VERSION:
                raise ConnectionError(f"protocol version {hello.version}")
            if hello.node_id == self.node_id:
                raise ConnectionError("connected to self")
            if hello.node_id in self.blocked:
                raise ConnectionError("blocked")
        except Exception as e:
            self._log(f"handshake with {transport_addr} failed: {e!r}")
            with self.lock:
                self._pending.discard(link)
            link.close()
            return None

        link.node_id = hello.node_id
        link.hello = hello
        link.last_rx = time.monotonic()
        with self.lock:
            self._pending.discard(link)
            peer = self.peers.get(hello.node_id)
            if peer is not None and peer.pubkey != hello.pubkey:
                self._log(f"pubkey mismatch for {hello.node_id}; refusing link")
                link.close()
                return None
            old = self.links.get(hello.node_id)
            if old is not None and not old.closed.is_set():
                if not self._prefer_new(old, link):
                    link.close()
                    return hello.node_id  # existing link stays; still "connected"
                old_to_close = old
            else:
                old_to_close = None
            self.links[hello.node_id] = link
        if old_to_close is not None:
            old_to_close.close()

        changed = self._learn_peer(
            P.RosterEntry(hello.node_id, hello.pubkey, hello.name, hello.name_ts)
        )
        with self.lock:
            self.peers[hello.node_id].last_seen = int(time.time())
        self._check_reinstall(hello.node_id, hello.name, link.transport_addr)
        self.storage.save_transport(hello.node_id, link.transport_addr)
        self.storage.touch_peer(hello.node_id)

        for target in (link.reader, link.writer):
            threading.Thread(target=target, daemon=True).start()

        # Initial sync: full roster, routes, anything stored for this peer.
        for frame in P.encode_roster(self._roster_entries(exclude=hello.node_id)):
            link.send(frame, PRIO_CONTROL)
        link.send(self._routes_frame_for(hello.node_id), PRIO_CONTROL)
        link.send(P.encode_addrs(self._link_addrs()), PRIO_CONTROL)
        if changed:
            self._gossip(changed, exclude=hello.node_id)
        # Tell our other neighbours where the newcomer can be reached: they
        # are probably in radio range of it too and can link up directly
        # instead of relaying through us (no inquiry/visibility needed).
        self._broadcast(
            P.encode_addrs([(hello.node_id, link.transport_addr)]),
            exclude=hello.node_id,
        )
        self._advert_due = 0.0
        self._log(f"link up {hello.node_id} ({hello.name}) via {link.transport_addr}")
        self._on_topology_change()
        return hello.node_id

    def _prefer_new(self, old: Link, new: Link) -> bool:
        """Deterministic duplicate-link tiebreak, evaluated identically on both
        ends: keep the link initiated by the lower node id. If the old link has
        gone quiet it's probably half-open — take the new one regardless."""
        if time.monotonic() - old.last_rx > PING_INTERVAL * 2.5:
            return True
        peer = new.node_id or ""
        lower_is_me = self.node_id < peer

        def canonical(link: Link) -> bool:
            return link.initiated == lower_is_me

        if canonical(new) and not canonical(old):
            return True
        if canonical(old) and not canonical(new):
            return False
        return True  # same initiator on both: newest wins (old likely dead)

    def _link_down(self, link: Link) -> None:
        link.close()
        with self.lock:
            self._pending.discard(link)
            if link.node_id is None or self.links.get(link.node_id) is not link:
                return
            del self.links[link.node_id]
            for dest in list(self.routes):
                self.routes[dest].pop(link.node_id, None)
                if not self.routes[dest]:
                    del self.routes[dest]
        with self.lock:
            if link.node_id in self.peers:
                self.peers[link.node_id].last_seen = int(time.time())
        self.storage.touch_peer(link.node_id)
        self._log(f"link down {link.node_id}")
        self._advert_due = 0.0
        self._on_topology_change()

    # ------------------------------------------------------------------ reinstalls

    def _check_reinstall(self, new: str, name: str, addr: str) -> None:
        """Same radio address, same name, different identity → the person
        reinstalled Muninn. Carry their conversation over instead of leaving
        it stranded on a key that no longer exists."""
        for old, a, last_ok in self.storage.load_transports():
            if a != addr or old == new or not last_ok:  # hints don't count
                continue
            with self.lock:
                p = self.peers.get(old)
                live = old in self.links
            if p is None or p.replaced_by or live:
                continue
            if p.name and name and p.name != name:
                continue
            self._supersede(old, new, announce=True)

    def _supersede(self, old: str, new: str, announce: bool) -> None:
        with self.lock:
            p_old = self.peers.get(old)
            if p_old is None or p_old.replaced_by or new not in self.peers:
                return
            p_old.replaced_by = new
            if p_old.override and not self.peers[new].override:
                self.peers[new].override = p_old.override
                self.storage.set_peer_override(new, p_old.override)
            for item in [i for i in self.outbox.values() if i.dst == old]:
                del self.outbox[item.packet_id]
            groups = [g for g in self.groups.values() if old in g.members]
            for g in groups:
                g.members.discard(old)
                g.members.add(new)
        entries = self.storage.migrate_peer(old, new)
        now = time.monotonic()
        for e in entries:  # reseal for the new key
            item = OutItem(
                P.new_packet_id(), new, e.body, int(time.time()), e.msg_id, P.F_RELIABLE
            )
            self.storage.outbox_add(
                OutboxEntry(item.packet_id, new, e.body, item.ts, e.msg_id, 0)
            )
            item.next_try = now
            with self.lock:
                self.outbox[item.packet_id] = item
        self._log(f"{old} reinstalled as {new}; moved {len(entries)} pending")
        self._system(
            dm_conv(new),
            f"{self.display_name(new)} reinstalled Muninn (new security key). "
            "Your conversation continues here.",
        )
        if announce:
            with self.lock:
                others = [
                    n
                    for n, p in self.peers.items()
                    if not p.replaced_by and n not in (old, new)
                ]
            self._send_body(others, C.Body(C.K_REPLACED, {"o": old, "n": new}))
            for g in groups:
                if not g.left and self.node_id in g.members:
                    with self.lock:
                        g.version += 1
                    self._save_group(g)
                    self._send_body(g.members - {self.node_id}, self._group_body(g))
        self._emit("peers")

    def connected(self) -> list[str]:
        with self.lock:
            return list(self.links)

    def has_transport(self, addr: str) -> bool:
        addr = addr.upper()
        with self.lock:
            return any(
                lk.transport_addr == addr
                for lk in itertools.chain(self.links.values(), self._pending)
            )

    def link_info(self) -> list[dict]:
        now = time.monotonic()
        with self.lock:
            return [
                {
                    "node": nid,
                    "addr": lk.transport_addr,
                    "rtt_ms": round(lk.rtt * 1000) if lk.rtt else None,
                    "idle": round(now - lk.last_rx, 1),
                    "in": lk.bytes_in,
                    "out": lk.bytes_out,
                    "initiated": lk.initiated,
                }
                for nid, lk in self.links.items()
            ]

    # ------------------------------------------------------------------ frames

    def _on_frame(self, link: Link, ftype: int, payload: bytes) -> None:
        if ftype == P.T_PACKET:
            self._on_packet(link, payload)
        elif ftype == P.T_ACK:
            self._on_ack(link, payload)
        elif ftype == P.T_PING:
            link.send(P.encode_pong(payload), PRIO_CONTROL)
        elif ftype == P.T_PONG:
            if link._ping and link._ping[0] == payload:
                link.rtt = time.monotonic() - link._ping[1]
                link._ping = None
        elif ftype == P.T_ROUTES:
            self._on_routes(link, P.decode_routes(payload))
        elif ftype == P.T_ADDRS:
            self._on_addrs(link, P.decode_addrs(payload))
        elif ftype == P.T_ROSTER:
            changed = []
            for e in P.decode_roster(payload):
                changed += self._learn_peer(e)
            if changed:
                self._gossip(changed, exclude=link.node_id)

    # ------------------------------------------------------------------ peers / roster

    def _learn_peer(self, e: P.RosterEntry) -> list[P.RosterEntry]:
        """Merge roster info. Returns entries that changed (to gossip on)."""
        if e.node_id == self.node_id or e.node_id in self.blocked:
            return []
        with self.lock:
            peer = self.peers.get(e.node_id)
            if peer is None:
                peer = PeerInfo(e.node_id, e.pubkey, e.name, e.name_ts)
                self.peers[e.node_id] = peer
            elif peer.pubkey != e.pubkey:
                return []  # TOFU: first pubkey seen for an id is pinned
            elif e.name_ts > peer.name_ts:
                peer.name, peer.name_ts = e.name, e.name_ts
            else:
                return []
        self.storage.upsert_peer(e.node_id, e.pubkey, peer.name, peer.name_ts)
        self._emit("peers")
        return [P.RosterEntry(peer.node_id, peer.pubkey, peer.name, peer.name_ts)]

    def _roster_entries(self, exclude: str | None = None) -> list[P.RosterEntry]:
        with self.lock:
            out = [P.RosterEntry(self.node_id, self.pubkey, self.name, self.name_ts)]
            out += [
                P.RosterEntry(p.node_id, p.pubkey, p.name, p.name_ts)
                for p in self.peers.values()
                if p.node_id != exclude and not p.replaced_by
            ]
        return out

    def _link_addrs(self) -> list[tuple[str, str]]:
        with self.lock:
            return [(nid, lk.transport_addr) for nid, lk in self.links.items()]

    def _on_addrs(self, link: Link, entries: list[tuple[str, str]]) -> None:
        """Address hints from a neighbour: remember them as dial candidates
        for peers we only reach through relays. Never forwarded further —
        only neighbours-of-neighbours are likely to be in radio range."""
        learned = False
        for nid, addr in entries:
            if nid == self.node_id:  # how a neighbour sees our own radio
                self.own_addr = addr
                continue
            with self.lock:
                skip = (
                    nid == self.node_id
                    or nid in self.blocked
                    or nid in self.links
                    or nid not in self.peers
                    or bool(self.peers[nid].replaced_by)
                )
            if not skip and self.storage.add_transport_hint(nid, addr):
                self._log(f"addr hint {nid} at {addr} from {link.node_id}")
                learned = True
        if learned:
            self._emit("addrs")

    def _broadcast(self, frame: bytes, exclude: str | None = None) -> None:
        with self.lock:
            targets = [lk for nid, lk in self.links.items() if nid != exclude]
        for lk in targets:
            lk.send(frame, PRIO_CONTROL)

    def _gossip(self, entries: list[P.RosterEntry], exclude: str | None) -> None:
        frames = P.encode_roster(entries)
        with self.lock:
            targets = [lk for nid, lk in self.links.items() if nid != exclude]
        for link in targets:
            for f in frames:
                link.send(f, PRIO_CONTROL)

    def set_name(self, name: str) -> None:
        self.name = name
        self.name_ts = max(int(time.time()), self.name_ts + 1)
        self.storage.set_display_name(name, self.name_ts)
        self._gossip(
            [P.RosterEntry(self.node_id, self.pubkey, name, self.name_ts)], None
        )
        self._emit("peers")

    def set_override(self, node_id: str, name: str | None) -> None:
        with self.lock:
            if node_id in self.peers:
                self.peers[node_id].override = name or None
        self.storage.set_peer_override(node_id, name)
        self._emit("peers")

    def is_muted(self, conv: str) -> bool:
        return self.storage.get_setting("mute:" + conv) == "1"

    def set_muted(self, conv: str, muted: bool) -> None:
        self.storage.set_setting("mute:" + conv, "1" if muted else "0")
        self._emit("peers")

    def active_peers(self) -> list[PeerInfo]:
        """Known peers, minus identities that were replaced by a reinstall."""
        with self.lock:
            return [
                p
                for p in self.peers.values()
                if not p.replaced_by and p.node_id not in self.hidden
            ]

    def display_name(self, node_id: str) -> str:
        if node_id == self.node_id:
            return self.name or "me"
        p = self.peers.get(node_id)
        if p is None:
            return node_id
        label = p.display
        if label != node_id and not p.override:
            with self.lock:
                clash = (
                    any(
                        q.node_id != node_id
                        and not q.replaced_by
                        and q.display.lower() == label.lower()
                        for q in self.peers.values()
                    )
                    or label.lower() == (self.name or "").lower()
                )
            if clash:  # two "Alex"es: tell them apart
                label += " ·" + node_id.replace(":", "")[-4:]
        return label

    def resolve(self, name_or_id: str) -> str | None:
        up = name_or_id.upper()
        if up in self.peers or up == self.node_id:
            return up
        low = name_or_id.lower()
        with self.lock:
            peers = [p for p in self.peers.values() if not p.replaced_by]
        for p in peers:
            if p.override and p.override.lower() == low:
                return p.node_id
        for p in peers:
            if p.name and p.name.lower() == low:
                return p.node_id
        for p in peers:  # "Alex ·4F2A"
            if self.display_name(p.node_id).lower() == low:
                return p.node_id
        for p in peers:  # unique prefix
            if p.display.lower().startswith(low):
                return p.node_id
        return None

    # ------------------------------------------------------------------ routing

    def _on_routes(self, link: Link, entries: list[tuple[str, int]]) -> None:
        via = link.node_id
        if via is None:
            return
        now = time.monotonic()
        with self.lock:
            fresh = {}
            for dest, hops in entries:
                if dest == self.node_id or dest == via:
                    continue
                if hops != P.ROUTE_UNREACHABLE and hops + 1 <= MAX_HOPS:
                    fresh[dest] = hops + 1
            # full-table semantics: anything via this neighbour not re-advertised
            # is withdrawn.
            for dest in list(self.routes):
                if via in self.routes[dest] and dest not in fresh:
                    del self.routes[dest][via]
                    if not self.routes[dest]:
                        del self.routes[dest]
            for dest, hops in fresh.items():
                self.routes[dest][via] = (hops, now)
        self._on_topology_change()

    def next_hop(self, dest: str, exclude: str | None = None) -> str | None:
        with self.lock:
            if dest in self.links:
                return dest
            best = None
            for via, (hops, t) in self.routes.get(dest, {}).items():
                if via == exclude or via not in self.links:
                    continue
                if best is None or (hops, -t) < (best[1], -best[2]):
                    best = (via, hops, t)
            return best[0] if best else None

    def hops(self, dest: str) -> int | None:
        with self.lock:
            if dest in self.links:
                return 1
            cands = [
                h
                for via, (h, _) in self.routes.get(dest, {}).items()
                if via in self.links
            ]
            return min(cands) if cands else None

    def reachable(self, dest: str) -> bool:
        return self.next_hop(dest) is not None

    def _routes_frame_for(self, neighbour: str) -> bytes:
        entries: list[tuple[str, int]] = []
        with self.lock:
            for nid in self.links:
                if nid != neighbour:
                    entries.append((nid, 1))
            for dest, vias in self.routes.items():
                if dest in self.links or dest == neighbour:
                    continue
                live = [(h, v) for v, (h, _) in vias.items() if v in self.links]
                if not live:
                    continue
                h, v = min(live)
                # split horizon with poisoned reverse
                entries.append((dest, P.ROUTE_UNREACHABLE if v == neighbour else h))
        return P.encode_routes(entries)

    def _on_topology_change(self) -> None:
        with self.lock:
            now_reach = set(self.links) | {
                d
                for d, vias in self.routes.items()
                if any(v in self.links for v in vias)
            }
            newly = now_reach - self._reachable
            gone = self._reachable - now_reach
            self._reachable = now_reach
            stamp = int(time.time())
            for nid in newly | gone:  # "last seen" covers relayed peers too
                if nid in self.peers:
                    self.peers[nid].last_seen = stamp
            self._advert_due = min(self._advert_due, time.monotonic() + 0.3)
        for dest in newly:
            self._flush_for(dest)
        self._emit("peers")

    def _flush_for(self, dest: str) -> None:
        """A destination just became reachable: hand it stored relay traffic
        and retry our own pending packets now."""
        for frame in self.storage.relay_take(dest):
            hop = self.next_hop(dest)
            if hop is None:
                break
            self._send_via(hop, frame)
        now = time.monotonic()
        with self.lock:
            for item in self.outbox.values():
                if item.dst == dest:
                    item.next_try = now
                    item.attempts = min(item.attempts, 1)
            stalled = [
                fid
                for fid, dl in self.downloads.items()
                if self._file_source(dl.rec) == dest
            ]
            for fid in stalled:
                self.downloads[fid].inflight.clear()
                self.downloads[fid].started = now
        for fid in stalled:
            self._request_chunks(fid)

    def _send_via(self, hop: str, frame: bytes, prio: int | None = None) -> bool:
        with self.lock:
            link = self.links.get(hop)
        if link is None:
            return False
        if prio is None:
            prio = _frame_prio(frame)
        return link.send(frame, prio)

    # ------------------------------------------------------------------ packets out

    def _encrypt_frame(self, item: OutItem) -> bytes | None:
        peer = self.peers.get(item.dst)
        if peer is None:
            return None
        box = self.boxes.get(peer.pubkey)
        sealed = bytes(box.encrypt(item.body))
        if len(sealed) > P.MAX_BOX:
            raise P.FrameTooLarge(f"body {len(item.body)} bytes too large")
        return P.encode_packet(
            P.Packet(
                item.packet_id,
                self.node_id,
                item.dst,
                P.DEFAULT_TTL,
                item.flags,
                item.ts,
                self.pubkey,
                sealed,
            )
        )

    def _send_body(
        self, dsts: list[str] | set[str], body: C.Body, msg_id: bytes | None = None
    ) -> list[bytes]:
        """Encrypt + send a body to each destination. Reliable bodies are
        persisted to the outbox and retried until ACKed. Returns packet ids."""
        raw = C.encode_body(body)
        flags = 0
        if body.reliable:
            flags |= P.F_RELIABLE
        if body.kind == C.K_CHUNK:
            flags |= P.F_BULK
        if body.kind in (C.K_RT, C.K_TYPING):
            flags |= P.F_REALTIME
        ts = int(time.time())
        pids = []
        for dst in dsts:
            if dst == self.node_id:
                continue
            item = OutItem(P.new_packet_id(), dst, raw, ts, msg_id, flags)
            item.frame = self._encrypt_frame(item)
            pids.append(item.packet_id)
            if body.reliable:
                self.storage.outbox_add(
                    OutboxEntry(item.packet_id, dst, raw, ts, msg_id, 0)
                )
                item.attempts = 1
                item.next_try = time.monotonic() + RETRY_BASE
                with self.lock:
                    self.outbox[item.packet_id] = item
            if item.frame is not None:
                hop = self.next_hop(dst)
                if hop is not None:
                    self._send_via(hop, item.frame)
                    self.stats["sent"] += 1
        return pids

    def _send_ack(self, pkt: P.Packet) -> None:
        frame = P.encode_ack(P.Ack(pkt.packet_id, self.node_id, pkt.src, P.DEFAULT_TTL))
        hop = self.next_hop(pkt.src)
        if hop is not None:
            self._send_via(hop, frame, PRIO_CONTROL)
        else:
            self.storage.relay_put(pkt.packet_id + b"A", pkt.src, frame)

    # ------------------------------------------------------------------ packets in

    def _on_packet(self, link: Link, payload: bytes) -> None:
        pkt = P.decode_packet(payload)
        if pkt.dst != self.node_id:
            self._relay_packet(link, pkt, payload)
            return
        if pkt.src == self.node_id:
            return
        with self.lock:
            peer = self.peers.get(pkt.src)
        if peer is not None and peer.pubkey != pkt.src_pubkey:
            self._log(f"dropping packet from {pkt.src}: pubkey mismatch")
            return
        try:
            plain = self.boxes.get(pkt.src_pubkey).decrypt(pkt.box)
            body = C.decode_body(plain)
        except Exception:
            return
        if peer is None:
            self._learn_peer(P.RosterEntry(pkt.src, pkt.src_pubkey, "", 0))
        if pkt.reliable:
            self._send_ack(pkt)
            if not self.storage.claim_seen(pkt.packet_id):
                return
        else:
            now = time.monotonic()
            with self.lock:
                if pkt.packet_id in self._unrel_seen:
                    return
                self._unrel_seen[pkt.packet_id] = now
        self.stats["received"] += 1
        try:
            self._dispatch(pkt, body)
        except Exception as e:
            self._log(f"dispatch kind {body.kind}: {e!r}")

    def _relay_packet(self, link: Link, pkt: P.Packet, payload: bytes) -> None:
        if pkt.src == self.node_id or pkt.ttl <= 1:
            return
        now = time.monotonic()
        with self.lock:
            t = self._relay_seen.get(pkt.packet_id)
            if t is not None and now - t < RELAY_DEDUP:
                return
            self._relay_seen[pkt.packet_id] = now
        frame = P.packet_with_ttl(payload, pkt.ttl - 1)
        hop = self.next_hop(pkt.dst, exclude=link.node_id)
        self.stats["relayed"] += 1
        if hop is not None:
            if self._send_via(hop, frame):
                return
        if pkt.reliable:
            self.storage.relay_put(pkt.packet_id, pkt.dst, frame)
            self.stats["stored"] += 1

    def _on_ack(self, link: Link, payload: bytes) -> None:
        ack = P.decode_ack(payload)
        if ack.dst != self.node_id:
            if ack.ttl <= 1:
                return
            key = (ack.packet_id, ack.src)
            now = time.monotonic()
            with self.lock:
                t = self._ack_seen.get(key)
                if t is not None and now - t < RELAY_DEDUP:
                    return
                self._ack_seen[key] = now
            frame = P.encode_ack(P.Ack(ack.packet_id, ack.src, ack.dst, ack.ttl - 1))
            hop = self.next_hop(ack.dst, exclude=link.node_id)
            if hop is None or not self._send_via(hop, frame, PRIO_CONTROL):
                self.storage.relay_put(ack.packet_id + b"A", ack.dst, frame)
            return
        with self.lock:
            item = self.outbox.pop(ack.packet_id, None)
        res = self.storage.outbox_ack(ack.packet_id)
        msg_id = item.msg_id if item else (res[1] if res else None)
        if msg_id is not None and self.storage.mark_delivered(msg_id, ack.src):
            self._emit("update", msg_id)

    # ------------------------------------------------------------------ dispatch

    def _conv_for(self, src: str, meta: dict) -> str:
        g = meta.get("g")
        if not g:
            return dm_conv(src)
        gid = bytes.fromhex(g)
        with self.lock:
            group = self.groups.get(gid)
        if group is None:
            # Message for a group we haven't heard about yet: placeholder, ask.
            group = Group(gid, "group", 0, {src, self.node_id})
            with self.lock:
                self.groups[gid] = group
            self._save_group(group)
            self._send_body([src], C.Body(C.K_GROUP_REQ, {"g": g}))
            self._emit("group", gid)
        return group_conv(gid)

    def _dispatch(self, pkt: P.Packet, body: C.Body) -> None:
        k, m, src = body.kind, body.meta, pkt.src
        if src in self.blocked:
            return  # acked (so they stop retrying), never shown
        if src in self.hidden and k in (C.K_TEXT, C.K_FILE):
            self._set_hidden(self.hidden - {src})  # forgotten, but wrote again
        ts = min(pkt.ts, int(time.time()) + 60)  # sender clock (app event order)
        # Chat is ordered by arrival: a wrong clock on the sender's device or a
        # long wait at a relay can't bury a new message in the history.
        arrived = int(time.time())
        if k == C.K_TEXT:
            msg = StoredMessage(
                msg_id=bytes.fromhex(m["i"]),
                conv=self._conv_for(src, m),
                sender=src,
                ts=arrived,
                kind="text",
                text=str(m.get("t", "")),
                reply_to=bytes.fromhex(m["r"]) if m.get("r") else None,
            )
            if self.storage.save_message(msg):
                self.typing.pop((msg.conv, src), None)
                self._emit("message", msg)
        elif k == C.K_FILE:
            self._on_file_offer(src, arrived, m, body.blob)
        elif k == C.K_REACT:
            mid = bytes.fromhex(m["m"])
            self.storage.set_reaction(mid, src, str(m["e"])[:32], not m.get("x"))
            self._emit("update", mid)
        elif k == C.K_EDIT:
            mid = bytes.fromhex(m["m"])
            if self.storage.edit_message(mid, src, str(m["t"])):
                self._emit("update", mid)
        elif k == C.K_DELETE:
            mid = bytes.fromhex(m["m"])
            if self.storage.delete_message(mid, src):
                self._emit("update", mid)
        elif k == C.K_RECEIPT:
            for h in m.get("r", []):
                mid = bytes.fromhex(h)
                if self.storage.mark_read(mid, src):
                    self._emit("update", mid)
        elif k == C.K_GROUP:
            self._on_group_info(src, m)
        elif k == C.K_GROUP_REQ:
            gid = bytes.fromhex(m["g"])
            with self.lock:
                g = self.groups.get(gid)
            if g is not None and src in g.members and g.version > 0:
                self._send_body([src], self._group_body(g))
        elif k == C.K_CHUNK:
            self._on_chunk(src, m, body.blob)
        elif k == C.K_FILE_REQ:
            self._on_file_req(src, m)
        elif k == C.K_TYPING:
            conv = self._conv_for(src, m)
            self.typing[(conv, src)] = time.monotonic() + TYPING_TTL
            self._emit("typing", conv, src)
        elif k == C.K_APP:
            ev = AppEvent(
                bytes.fromhex(m["i"]),
                str(m["s"]),
                str(m["a"]),
                self._conv_for(src, m),
                src,
                int(m.get("q", 0)),
                str(m["y"]),
                m.get("b"),
                ts,
            )
            if self.storage.save_app_event(ev):
                self._emit("app", ev)
        elif k == C.K_REPLACED:
            old, new = str(m.get("o", "")), str(m.get("n", ""))
            with self.lock:
                p_old, p_new = self.peers.get(old), self.peers.get(new)
            same_name = p_old and p_new and (not p_old.name or p_old.name == p_new.name)
            if same_name and old not in self.links:
                self._supersede(old, new, announce=False)
        elif k == C.K_RT:
            self._emit(
                "rt", src, str(m.get("a")), str(m.get("s")), m.get("b"), body.blob
            )

    # ------------------------------------------------------------------ chat API

    def conv_members(self, conv: str) -> list[str]:
        if conv.startswith("dm:"):
            return [conv[3:]]
        gid = conv_group_id(conv)
        with self.lock:
            g = self.groups.get(gid) if gid else None
        if g is None or g.left:
            return []
        return [m for m in g.members if m != self.node_id]

    def _conv_meta(self, conv: str) -> dict:
        gid = conv_group_id(conv)
        return {"g": gid.hex()} if gid else {}

    def send_text(self, conv: str, text: str, reply_to: bytes | None = None) -> bytes:
        dsts = self.conv_members(conv)
        if not dsts:
            raise ValueError("no recipients for this conversation")
        msg_id = os.urandom(16)
        meta = {"i": msg_id.hex(), "t": text, **self._conv_meta(conv)}
        if reply_to:
            meta["r"] = reply_to.hex()
        body = C.Body(C.K_TEXT, meta)
        if len(C.encode_body(body)) > 60000:
            raise P.FrameTooLarge("message too long")
        msg = StoredMessage(
            msg_id, conv, self.node_id, int(time.time()), "text", text, reply_to
        )
        self.storage.save_message(msg, dsts)
        self._emit("message", msg)
        self._send_body(dsts, body, msg_id)
        return msg_id

    def react(self, conv: str, msg_id: bytes, emoji: str, remove: bool = False) -> None:
        self.storage.set_reaction(msg_id, self.node_id, emoji, not remove)
        self._emit("update", msg_id)
        meta = {"m": msg_id.hex(), "e": emoji, **self._conv_meta(conv)}
        if remove:
            meta["x"] = 1
        self._send_body(self.conv_members(conv), C.Body(C.K_REACT, meta))

    def edit(self, conv: str, msg_id: bytes, text: str) -> None:
        if self.storage.edit_message(msg_id, self.node_id, text):
            self._emit("update", msg_id)
            self._send_body(
                self.conv_members(conv),
                C.Body(
                    C.K_EDIT, {"m": msg_id.hex(), "t": text, **self._conv_meta(conv)}
                ),
            )

    def delete(self, conv: str, msg_id: bytes) -> None:
        if self.storage.delete_message(msg_id, self.node_id):
            self._emit("update", msg_id)
            self._send_body(
                self.conv_members(conv),
                C.Body(C.K_DELETE, {"m": msg_id.hex(), **self._conv_meta(conv)}),
            )

    def mark_read(self, conv: str) -> None:
        """Local user viewed conv: send read receipts to the senders."""
        by_sender: dict[str, list[str]] = defaultdict(list)
        for mid, sender in self.storage.mark_read_local(conv):
            if sender != self.node_id:
                by_sender[sender].append(mid.hex())
        for sender, ids in by_sender.items():
            for i in range(0, len(ids), 500):
                self._send_body([sender], C.Body(C.K_RECEIPT, {"r": ids[i : i + 500]}))

    def send_typing(self, conv: str) -> None:
        self._send_body(
            self.conv_members(conv), C.Body(C.K_TYPING, self._conv_meta(conv))
        )

    def typing_in(self, conv: str) -> list[str]:
        now = time.monotonic()
        return [
            n
            for (c, n), until in list(self.typing.items())
            if c == conv and until > now
        ]

    # ------------------------------------------------------------------ groups

    def _group_body(self, g: Group) -> C.Body:
        members = []
        for nid in sorted(g.members):
            pk = self.pubkey if nid == self.node_id else self.peers[nid].pubkey
            members.append([nid, pk.hex()])
        return C.Body(
            C.K_GROUP,
            {"g": g.group_id.hex(), "n": g.name, "v": g.version, "m": members},
        )

    def _save_group(self, g: Group) -> None:
        self.storage.save_group(
            g.group_id, g.name, g.version, sorted(g.members), g.left
        )

    def _system(self, conv: str, text: str) -> None:
        msg = StoredMessage(
            os.urandom(16), conv, self.node_id, int(time.time()), "system", text
        )
        self.storage.save_message(msg)
        self._emit("message", msg)

    def create_group(self, name: str, members: list[str]) -> bytes:
        members = [m for m in members if m != self.node_id]
        for m in members:
            if m not in self.peers:
                raise ValueError(f"unknown peer {m}")
        g = Group(os.urandom(16), name, 1, set(members) | {self.node_id})
        with self.lock:
            self.groups[g.group_id] = g
        self._save_group(g)
        self._send_body(members, self._group_body(g))
        self._system(
            g.conv, f"{self.display_name(self.node_id)} created group “{name}”"
        )
        self._emit("group", g.group_id)
        return g.group_id

    def update_group(
        self,
        gid: bytes,
        name: str | None = None,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        add, remove = add or [], remove or []
        with self.lock:
            g = self.groups[gid]
            old_members = set(g.members)
            g.version += 1
            if name:
                g.name = name
            g.members |= {a for a in add if a in self.peers}
            g.members -= set(remove)
            if self.node_id not in g.members:
                g.left = True
        self._save_group(g)
        self._send_body((old_members | g.members) - {self.node_id}, self._group_body(g))
        bits = []
        if name:
            bits.append(f"renamed the group to “{name}”")
        if add:
            bits.append("added " + ", ".join(self.display_name(a) for a in add))
        removed = [r for r in remove if r != self.node_id]
        if removed:
            bits.append("removed " + ", ".join(self.display_name(r) for r in removed))
        if self.node_id in remove:
            bits.append("left")
        self._system(g.conv, f"{self.display_name(self.node_id)} " + ", ".join(bits))
        self._emit("group", gid)

    def leave_group(self, gid: bytes) -> None:
        self.update_group(gid, remove=[self.node_id])

    # ------------------------------------------------------------------ forgetting

    def _drop_files(self, paths: list[str]) -> None:
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass
        gone = set(paths)
        with self.lock:
            for fid in [f for f, r in self.files.items() if r.path in gone]:
                self.files.pop(fid, None)
                self.downloads.pop(fid, None)

    def delete_conversation(self, conv: str) -> None:
        """Delete a chat's history on this device only. A group is also left
        (the others are told) and removed from the list."""
        gid = conv_group_id(conv)
        doomed = {m.msg_id for m in self.storage.load_history(conv, 1_000_000)}
        if gid is not None:
            g = self.groups.get(gid)
            if g is not None and not g.left and self.node_id in g.members:
                try:
                    self.leave_group(gid)
                except Exception as e:
                    self._log(f"leave {conv}: {e!r}")
            paths = self.storage.delete_group(gid)
            with self.lock:
                self.groups.pop(gid, None)
        else:
            paths = self.storage.delete_conversation(conv)
        with self.lock:  # unsent messages of this chat won't be retried
            self.outbox = {
                k: v for k, v in self.outbox.items() if v.msg_id not in doomed
            }
        self._drop_files(paths)
        self._emit("peers")
        if gid is not None:
            self._emit("group", gid)

    def forget(self, node_id: str, block: bool = False) -> list[str]:
        """Forget a person: their chat, saved addresses, queued messages and
        pinned key; Muninn stops reconnecting to them. They come back only
        if they get in touch again — unless `block`, which also refuses
        their links and messages. Returns their radio addresses (so the
        transport can drop an OS-level Bluetooth pairing)."""
        addrs = [a for n, a, _ in self.storage.load_transports() if n == node_id]
        if block:
            self.blocked.add(node_id)
            self.storage.set_blocked(self.blocked)
        self._set_hidden(self.hidden | {node_id})
        with self.lock:
            link = self.links.get(node_id)
            self.peers.pop(node_id, None)
            self.outbox = {k: v for k, v in self.outbox.items() if v.dst != node_id}
        if link is not None and block:
            link.close()
        self._drop_files(self.storage.forget_peer(node_id))
        self._log(f"forgot {node_id}{' (blocked)' if block else ''}")
        self._emit("peers")
        return addrs

    def _set_hidden(self, ids: set[str]) -> None:
        self.hidden = ids
        self.storage.set_setting("forgotten_ids", json.dumps(sorted(ids)))

    def unblock(self, node_id: str) -> None:
        self.blocked.discard(node_id)
        self.storage.set_blocked(self.blocked)
        self._emit("peers")

    def _on_group_info(self, src: str, m: dict) -> None:
        gid = bytes.fromhex(m["g"])
        version = int(m["v"])
        members = {}
        for nid, pkhex in m["m"]:
            members[nid] = bytes.fromhex(pkhex)
        with self.lock:
            g = self.groups.get(gid)
            if g is not None and g.version >= version:
                return
            if g is not None and src not in g.members and g.version > 0:
                return  # only members may change a group
        for nid, pk in members.items():
            if nid != self.node_id and nid not in self.peers:
                self._learn_peer(P.RosterEntry(nid, pk, "", 0))
        new = g is None or g.version == 0
        with self.lock:
            prev_members = set(g.members) if g and g.version else set()
            g = Group(gid, str(m["n"]), version, set(members))
            g.left = self.node_id not in g.members
            self.groups[gid] = g
        self._save_group(g)
        who = self.display_name(src)
        if new:
            self._system(g.conv, f"{who} added you to “{g.name}”")
        else:
            added = g.members - prev_members
            removed = prev_members - g.members
            parts = []
            if added:
                parts.append("added " + ", ".join(self.display_name(a) for a in added))
            if removed:
                parts.append(
                    "removed " + ", ".join(self.display_name(r) for r in removed)
                )
            if parts:
                self._system(g.conv, f"{who} " + ", ".join(parts))
        self._emit("group", gid)

    # ------------------------------------------------------------------ files

    def send_file(
        self,
        conv: str,
        path: str,
        mime: str = "application/octet-stream",
        name: str | None = None,
        caption: str = "",
        thumb: bytes = b"",
        reply_to: bytes | None = None,
        meta_extra: dict | None = None,
    ) -> bytes:
        dsts = self.conv_members(conv)
        if not dsts:
            raise ValueError("no recipients for this conversation")
        data = open(path, "rb").read()
        if len(data) > MAX_FILE_SIZE:
            raise ValueError(f"file too large (max {MAX_FILE_SIZE // 1024 // 1024} MB)")
        name = name or os.path.basename(path)
        file_id = os.urandom(16)
        msg_id = os.urandom(16)
        store = self.storage.files_dir / file_id.hex()
        store.mkdir(parents=True, exist_ok=True)
        dest = store / _safe_name(name)
        dest.write_bytes(data)
        chunks = max(1, -(-len(data) // CHUNK_SIZE))
        rec = FileRecord(
            file_id,
            msg_id,
            conv,
            self.node_id,
            name,
            mime,
            len(data),
            hashlib.sha256(data).hexdigest(),
            CHUNK_SIZE,
            chunks,
            str(dest),
            "complete",
            bytearray(b"\xff" * (-(-chunks // 8))),
        )
        self.storage.save_file(rec)
        with self.lock:
            self.files[file_id] = rec
        meta = {
            "i": msg_id.hex(),
            "f": file_id.hex(),
            "n": name,
            "mt": mime,
            "s": len(data),
            "h": rec.sha256,
            "cs": CHUNK_SIZE,
            "c": chunks,
            **self._conv_meta(conv),
            **(meta_extra or {}),
        }
        if caption:
            meta["t"] = caption
        if reply_to:
            meta["r"] = reply_to.hex()
        thumb = thumb if len(thumb) <= 12000 else b""
        msg = StoredMessage(
            msg_id,
            conv,
            self.node_id,
            int(time.time()),
            "file",
            caption,
            reply_to,
            meta={k: v for k, v in meta.items() if k not in ("i", "g")},
        )
        if thumb:
            msg.meta["thumb"] = _save_thumb(self.storage, file_id, thumb)
        self.storage.save_message(msg, dsts)
        self._emit("message", msg)
        self._send_body(dsts, C.Body(C.K_FILE, meta, thumb), msg_id)
        return msg_id

    def _on_file_offer(self, src: str, ts: int, m: dict, thumb: bytes) -> None:
        file_id = bytes.fromhex(m["f"])
        msg_id = bytes.fromhex(m["i"])
        conv = self._conv_for(src, m)
        size = int(m["s"])
        chunks = int(m["c"])
        if size > MAX_FILE_SIZE or chunks > 100000:
            return
        meta = {k: v for k, v in m.items() if k not in ("i", "g")}
        if thumb:
            meta["thumb"] = _save_thumb(self.storage, file_id, thumb)
        msg = StoredMessage(
            msg_id,
            conv,
            src,
            ts,
            "file",
            str(m.get("t", "")),
            bytes.fromhex(m["r"]) if m.get("r") else None,
            meta=meta,
        )
        if not self.storage.save_message(msg):
            return
        rec = FileRecord(
            file_id,
            msg_id,
            conv,
            src,
            str(m["n"]),
            str(m.get("mt", "")),
            size,
            str(m["h"]),
            int(m["cs"]),
            chunks,
            "",
            "offered",
            bytearray(-(-chunks // 8)),
        )
        with self.lock:
            self.files[file_id] = rec
        self.storage.save_file(rec)
        self._emit("message", msg)
        if size <= self.auto_download_max:
            self.download(file_id)

    def download(self, file_id: bytes) -> None:
        with self.lock:
            rec = self.files.get(file_id)
            if rec is None or rec.state in ("complete", "downloading"):
                return
            rec.state = "downloading"
            store = self.storage.files_dir / file_id.hex()
            store.mkdir(parents=True, exist_ok=True)
            rec.path = str(store / (_safe_name(rec.name) + ".part"))
            if not os.path.exists(rec.path):
                with open(rec.path, "wb") as f:
                    f.truncate(rec.size)
            self.downloads[file_id] = Download(rec)
        self.storage.save_file(rec)
        self._request_chunks(file_id)
        self._emit("file", file_id)

    def file_progress(self, file_id: bytes) -> tuple[int, int, str]:
        rec = self.files.get(file_id)
        if rec is None:
            return 0, 0, "unknown"
        return rec.have_count, rec.chunks, rec.state

    def _file_source(self, rec: FileRecord) -> str | None:
        cands = [rec.sender] + [
            m for m in self.conv_members(rec.conv) if m != rec.sender
        ]
        for c in cands:
            if self.reachable(c):
                return c
        return None

    def _request_chunks(self, file_id: bytes) -> None:
        with self.lock:
            dl = self.downloads.get(file_id)
            if dl is None:
                return
            rec = dl.rec
            src = self._file_source(rec)
            if src is None:
                return
            now = time.monotonic()
            # Only re-request when the transfer has stalled: while chunks keep
            # arriving, outstanding ones are just queued behind them.
            stalled = now - max(dl.last_chunk, dl.started) > CHUNK_TIMEOUT
            for i, t in list(dl.inflight.items()):
                if rec.has(i) or (stalled and now - t > CHUNK_TIMEOUT):
                    del dl.inflight[i]
            want = []
            for i in range(rec.chunks):
                if len(dl.inflight) + len(want) >= FILE_WINDOW:
                    break
                if not rec.has(i) and i not in dl.inflight:
                    want.append(i)
            for i in want:
                dl.inflight[i] = now
        if want:
            self._send_body(
                [src], C.Body(C.K_FILE_REQ, {"f": file_id.hex(), "w": want})
            )

    def _on_file_req(self, src: str, m: dict) -> None:
        file_id = bytes.fromhex(m["f"])
        with self.lock:
            rec = self.files.get(file_id)
            if rec is None or rec.state != "complete":
                return
            if src not in self.conv_members(rec.conv) and src != rec.sender:
                return
            queued = set(self._serve_q)
            for i in m.get("w", [])[: FILE_WINDOW * 2]:
                item = (src, file_id, int(i))
                if 0 <= item[2] < rec.chunks and item not in queued:
                    self._serve_q.append(item)

    def _serve_loop(self) -> None:
        while not self._stop.is_set():
            item = None
            with self.lock:
                for idx, (dst, fid, i) in enumerate(self._serve_q):
                    hop = self.next_hop(dst)
                    if hop is None:
                        continue
                    link = self.links.get(hop)
                    if link is not None and link.bulk_room() > 0:
                        item = self._serve_q.pop(idx)
                        break
                rec = self.files.get(item[1]) if item else None
            if item is None or rec is None:
                time.sleep(0.02)
                continue
            dst, fid, i = item
            try:
                with open(rec.path, "rb") as f:
                    f.seek(i * rec.chunk_size)
                    data = f.read(rec.chunk_size)
            except OSError:
                continue
            self._send_body([dst], C.Body(C.K_CHUNK, {"f": fid.hex(), "i": i}, data))

    def _on_chunk(self, src: str, m: dict, data: bytes) -> None:
        file_id = bytes.fromhex(m["f"])
        i = int(m["i"])
        done = False
        with self.lock:
            dl = self.downloads.get(file_id)
            if dl is None:
                return
            rec = dl.rec
            if not 0 <= i < rec.chunks or rec.has(i):
                return
            expected = min(rec.chunk_size, rec.size - i * rec.chunk_size)
            if len(data) != expected:
                return
            with open(rec.path, "r+b") as f:
                f.seek(i * rec.chunk_size)
                f.write(data)
            rec.mark(i)
            dl.inflight.pop(i, None)
            dl.last_chunk = time.monotonic()
            dl.dirty += 1
            if rec.have_count == rec.chunks:
                done = True
                del self.downloads[file_id]
        if done:
            self._finish_download(rec)
        else:
            if dl.dirty >= 16:
                dl.dirty = 0
                self.storage.save_file(rec)
            self._request_chunks(file_id)
        self._emit("file", file_id)

    def _finish_download(self, rec: FileRecord) -> None:
        h = hashlib.sha256()
        with open(rec.path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest() != rec.sha256:
            self._log(f"file {rec.name}: hash mismatch, restarting")
            rec.have = bytearray(len(rec.have))
            rec.state = "offered"
            self.storage.save_file(rec)
            self.download(rec.file_id)
            return
        final = rec.path[: -len(".part")] if rec.path.endswith(".part") else rec.path
        os.replace(rec.path, final)
        rec.path = final
        rec.state = "complete"
        self.storage.save_file(rec)
        self._emit("file", rec.file_id)
        self._emit("update", rec.msg_id)

    # ------------------------------------------------------------------ apps / games

    def app_event(
        self, conv: str, app: str, session: str, type_: str, body, seq: int = 0
    ) -> AppEvent:
        """Reliable app event to all conv members (also stored locally)."""
        ev = AppEvent(
            os.urandom(16),
            session,
            app,
            conv,
            self.node_id,
            seq,
            type_,
            body,
            int(time.time()),
        )
        self.storage.save_app_event(ev)
        self._emit("app", ev)
        meta = {
            "i": ev.event_id.hex(),
            "a": app,
            "s": session,
            "y": type_,
            "b": body,
            "q": seq,
            **self._conv_meta(conv),
        }
        self._send_body(self.conv_members(conv), C.Body(C.K_APP, meta))
        return ev

    def rt_send(
        self,
        conv: str,
        app: str,
        session: str,
        body,
        blob: bytes = b"",
        to: list[str] | None = None,
    ) -> None:
        """Unreliable, low-latency datagram (real-time games). May be lost."""
        dsts = to if to is not None else self.conv_members(conv)
        self._send_body(dsts, C.Body(C.K_RT, {"a": app, "s": session, "b": body}, blob))

    # ------------------------------------------------------------------ maintenance

    def _maintenance_loop(self) -> None:
        last_expire = 0.0
        while not self._stop.wait(0.25):
            now = time.monotonic()
            try:
                self._keepalive(now)
                if now >= self._advert_due:
                    self._advertise()
                    self._advert_due = now + ADVERT_INTERVAL
                self._retransmit(now)
                if now - last_expire > 2.0:
                    last_expire = now
                    self._expire(now)
                    for fid in list(self.downloads):
                        self._request_chunks(fid)
            except Exception as e:
                self._log(f"maintenance: {e!r}")

    def _keepalive(self, now: float) -> None:
        with self.lock:
            links = list(self.links.values())
        for link in links:
            if now - link.last_rx > LINK_TIMEOUT:
                self._log(f"link {link.node_id} timed out")
                link.close()
                continue
            if link._ping is None and now - link.last_tx > PING_INTERVAL:
                token = os.urandom(8)
                link._ping = (token, now)
                link.send(P.encode_ping(token), PRIO_CONTROL)
            elif link._ping is not None and now - link._ping[1] > PING_INTERVAL * 2:
                link._ping = None  # lost; next one goes out on the following tick

    def _advertise(self) -> None:
        with self.lock:
            links = list(self.links.items())
        for nid, link in links:
            link.send(self._routes_frame_for(nid), PRIO_CONTROL)

    def _retransmit(self, now: float) -> None:
        with self.lock:
            due = [i for i in self.outbox.values() if i.next_try <= now]
        for item in due:
            hop = self.next_hop(item.dst)
            if hop is None:
                item.next_try = now + 1.0  # re-checked; flushed on reachability
                continue
            if item.frame is None:
                item.frame = self._encrypt_frame(item)
                if item.frame is None:
                    item.next_try = now + 5.0
                    continue
            if self._send_via(hop, item.frame):
                self.stats["retransmits"] += 1
            item.attempts += 1
            delay = min(RETRY_BASE * 2 ** (item.attempts - 2), RETRY_MAX)
            item.next_try = now + delay * random.uniform(0.85, 1.15)

    def _expire(self, now: float) -> None:
        with self.lock:
            for d in (self._relay_seen, self._unrel_seen):
                for k in [k for k, t in d.items() if now - t > 60]:
                    del d[k]
            for k in [k for k, t in self._ack_seen.items() if now - t > 60]:
                del self._ack_seen[k]
            changed = False
            for dest in list(self.routes):
                for via, (_, t) in list(self.routes[dest].items()):
                    if now - t > ROUTE_TTL:
                        del self.routes[dest][via]
                        changed = True
                if not self.routes[dest]:
                    del self.routes[dest]
            for k in [k for k, until in self.typing.items() if until < now]:
                del self.typing[k]
        self.storage.relay_expire(RELAY_MAX_AGE, RELAY_MAX_ROWS)
        if changed:
            self._on_topology_change()

    # ------------------------------------------------------------------ introspection

    def pending_count(self, dst: str | None = None) -> int:
        with self.lock:
            return sum(1 for i in self.outbox.values() if dst is None or i.dst == dst)

    def status(self) -> dict:
        with self.lock:
            return {
                "node": self.node_id,
                "name": self.name,
                "links": self.link_info(),
                "routes": {
                    d: {v: h for v, (h, _) in vias.items()}
                    for d, vias in self.routes.items()
                },
                "reachable": sorted(self._reachable),
                "outbox": len(self.outbox),
                "downloads": {
                    f.hex(): [d.rec.have_count, d.rec.chunks]
                    for f, d in self.downloads.items()
                },
                "stats": dict(self.stats),
            }


def _frame_prio(frame: bytes) -> int:
    t = frame[0]
    if t == P.T_PACKET and len(frame) > 3 + 29:
        flags = frame[3 + 29]
        if flags & P.F_REALTIME:
            return PRIO_REALTIME
        if flags & P.F_BULK:
            return PRIO_BULK
        return PRIO_CHAT
    return PRIO_CONTROL


def _safe_name(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "._- ()" else "_" for c in name).strip()
    return keep[:120] or "file"


def _save_thumb(storage: Storage, file_id: bytes, thumb: bytes) -> str:
    d = storage.files_dir / file_id.hex()
    d.mkdir(parents=True, exist_ok=True)
    p = d / "thumb.jpg"
    p.write_bytes(thumb)
    return str(p)
