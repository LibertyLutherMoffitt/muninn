"""ConnectionManager — multi-peer connection handling.

Manages simultaneous BT connections, each with independent socket, NaCl Box,
recv thread, and send lock. Handles message routing, relay, ACKs, and group
setup forwarding.

Thread safety:
- peers dict + relay_queue are protected by peers_lock; mutations of the two
  happen atomically so a reconnect can't pop an empty queue while another
  thread is concurrently enqueuing.
- Individual socket sends use per-peer send_lock.
- seen/seen_acks/seen_reads: single-op dict/set mutations rely on the GIL.
- unacked: inner-dict reads/writes rely on the GIL; add_peer snapshots via
  .copy() (C-level atomic) so concurrent del can't raise RuntimeError.
"""

import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from nacl.public import PrivateKey

from muninn import crypto, protocol
from muninn.groups import Group, GroupStore

GROUP_ZERO = b"\x00" * 16


@dataclass
class PeerState:
    addr: str
    sock: socket.socket
    box: object  # nacl.public.Box
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    recv_thread: threading.Thread | None = None


class ConnectionManager:
    def __init__(
        self,
        local_mac: str,
        private_key: PrivateKey,
        group_store: GroupStore,
        display_name: str = "",
    ):
        self.local_mac = local_mac
        self.local_mac_bytes = protocol.mac_to_bytes(local_mac)
        self.private_key = private_key
        self.group_store = group_store
        # Leave empty when unset so group_store.display_name() falls back to
        # the MAC. Previously this defaulted to local_mac, which caused us to
        # broadcast our own MAC as a self-chosen name — producing a pointless
        # "AA:BB:… is now known as AA:BB:…" line on every peer reconnect.
        self.display_name = display_name
        if display_name:
            self.group_store.set_name(local_mac, display_name)

        self.peers: dict[str, PeerState] = {}
        self.peers_lock = threading.Lock()

        # Message state
        self.unacked: dict[bytes, dict[str, bytes]] = {}  # msg_id -> {addr -> frame}
        self.seen: set[bytes] = set()  # msg_id (delivered to us as final dest)
        self.seen_lock = threading.Lock()  # atomic claim for seen
        self.seen_relayed: set[bytes] = set()  # msg_id (forwarded by us as relay)
        self.seen_acks: set[tuple[bytes, bytes]] = set()  # (msg_id, from_mac_bytes)
        self.seen_reads: set[tuple[bytes, bytes]] = set()  # (msg_id, from_mac_bytes)
        self.relay_queue: dict[str, list[bytes]] = {}  # dest_addr -> [frame_bytes]
        self.indirect_via: dict[str, str] = {}  # addr -> relay_addr we learned from

        # Callbacks (set by CLI layer)
        self.on_message: Callable | None = None  # (group_id, sender_mac, text, msg_id)
        self.on_peer_change: Callable | None = None  # (addr, connected)
        self.on_group_setup: Callable | None = None  # (Group)
        self.on_ack: Callable | None = None  # (msg_id, from_mac)
        self.on_read: Callable | None = None  # (msg_id, from_mac)
        self.on_profile: Callable | None = None  # (addr, name)

    # --- Peer lifecycle ---

    def add_peer(self, sock: socket.socket, addr: str) -> bool:
        """Handshake with peer, start recv thread. Returns True on success.

        On collision with an existing peer entry, we treat the old entry as
        stale (half-open/dead connection, undetected) and replace it with the
        new socket — otherwise a silently-dead peer would block all future
        reconnects for that address.
        """
        addr = addr.upper()

        # Handshake — exchange pubkeys
        try:
            pubkey_bytes = bytes(self.private_key.public_key)
            sock.sendall(protocol.encode_handshake(pubkey_bytes))

            prev_timeout = sock.gettimeout()
            sock.settimeout(15)
            try:
                frame_type, payload = protocol.read_frame(sock)
            finally:
                sock.settimeout(prev_timeout)

            if frame_type != protocol.TYPE_HANDSHAKE or len(payload) != 32:
                sock.close()
                return False

            box = crypto.derive_box(self.private_key, payload)
            self.group_store.add_pubkey(addr, payload)
        except (ConnectionError, OSError):
            try:
                sock.close()
            except Exception:
                pass
            return False

        self.indirect_via.pop(addr, None)
        peer = PeerState(addr=addr, sock=sock, box=box)
        peer.recv_thread = threading.Thread(
            target=self._recv_loop,
            args=(peer,),
            daemon=True,
        )

        with self.peers_lock:
            old = self.peers.get(addr)
            self.peers[addr] = peer
            # Pop relay queue atomically with peer insert so concurrent
            # _route_frame either sees the peer (sends direct) or queues
            # before we pop.
            queued = self.relay_queue.pop(addr, [])

        if old is not None:
            # Tear down the stale peer. Its recv loop will see the closed
            # socket, exit, and call remove_peer(expected=old) — which is a
            # no-op now that self.peers[addr] points at the new peer.
            old.stop.set()
            try:
                old.sock.close()
            except Exception:
                pass

        peer.recv_thread.start()

        # Announce our self-chosen display name, only if we have one.
        # Broadcasting an empty name is legal (PROTOCOL.md), but we have
        # nothing useful to say, so skip the frame entirely.
        if self.display_name:
            self.send_to(addr, protocol.encode_profile(self.display_name))

        # Share our peer roster with the new peer and vice versa.
        self._send_peer_annc(addr)
        # Tell existing peers about the new peer (if we have their pubkey).
        new_pubkey = self.group_store.get_pubkey(addr)
        if new_pubkey is not None:
            annc = protocol.encode_peer_annc(
                [(protocol.mac_to_bytes(addr), new_pubkey)]
            )
            with self.peers_lock:
                existing = [a for a in self.peers if a != addr]
            for existing_addr in existing:
                self.send_to(existing_addr, annc)

        if self.on_peer_change:
            self.on_peer_change(addr, True)

        for frame in queued:
            self.send_to(addr, frame)

        # Resend unacked messages destined for this peer. Use dict.copy()
        # (C-level atomic) so a concurrent del in _handle_ack can't raise
        # RuntimeError mid-iteration.
        for dests in self.unacked.copy().values():
            frame = dests.get(addr)
            if frame is not None:
                self.send_to(addr, frame)

        return True

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
        if self.on_peer_change:
            self.on_peer_change(addr, False)

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

    def send_message(
        self, group_id: bytes, text: str, dest_addrs: list[str]
    ) -> tuple[bytes, list[str], list[str]] | None:
        """Encrypt and send message to all dests.

        Returns (msg_id, sent_addrs, skipped_addrs), or None if no dest has a
        known pubkey. `skipped_addrs` are dests without a pubkey. Raises
        protocol.FrameTooLarge if the text exceeds the wire frame limit.
        """
        msg_id = protocol.new_msg_id()
        plaintext = text.encode("utf-8")
        unacked_entry: dict[str, bytes] = {}
        skipped: list[str] = []

        # Build all frames first, before any network send. Must register in
        # self.unacked BEFORE _route_frame so an ACK returning on a fast loop
        # can't race past and find the entry missing (leaving a stale entry
        # that never clears).
        for dest_addr in dest_addrs:
            dest_addr = dest_addr.upper()
            if dest_addr == self.local_mac:
                continue

            pubkey = self.group_store.get_pubkey(dest_addr)
            if pubkey is None:
                skipped.append(dest_addr)
                continue

            box = crypto.derive_box(self.private_key, pubkey)
            encrypted = crypto.encrypt(box, plaintext)
            # encode_message may raise FrameTooLarge. Payload size only
            # depends on plaintext length (every dest produces the same size),
            # so letting it propagate on the first build is correct — the
            # whole send can't proceed regardless of dest.
            frame = protocol.encode_message(
                group_id,
                msg_id,
                self.local_mac_bytes,
                protocol.mac_to_bytes(dest_addr),
                encrypted,
            )
            unacked_entry[dest_addr] = frame

        if not unacked_entry:
            return None

        self.unacked[msg_id] = unacked_entry

        for dest_addr, frame in unacked_entry.items():
            self._route_frame(dest_addr, frame)

        return msg_id, list(unacked_entry.keys()), skipped

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

        member_list = [(protocol.mac_to_bytes(a), pk) for a, pk in members.items()]
        frame = protocol.encode_group_setup(group_id, member_list, name)

        for addr in member_addrs:
            self._route_frame(addr.upper(), frame)

        return group

    # --- Routing ---

    def _route_frame(
        self, dest_addr: str, frame: bytes, exclude: str | None = None
    ) -> bool:
        """Send frame to dest — direct, relay, or queue.

        `exclude` names a peer to skip when picking relay candidates — used
        during forwarding to avoid bouncing a frame back to the sender.
        """
        with self.peers_lock:
            if dest_addr in self.peers:
                direct = True
                candidates: list[str] = []
            else:
                direct = False
                candidates = [a for a in self.peers if a != dest_addr and a != exclude]

        if direct:
            return self.send_to(dest_addr, frame)

        for peer_addr in candidates:
            if self.send_to(peer_addr, frame):
                return True

        # Re-check under lock before queuing — dest may have connected while
        # we were attempting relay (add_peer would miss our frame otherwise).
        with self.peers_lock:
            if dest_addr in self.peers:
                connected_now = True
            else:
                self.relay_queue.setdefault(dest_addr, []).append(frame)
                connected_now = False

        if connected_now:
            return self.send_to(dest_addr, frame)
        return False

    # --- Receive loop ---

    def _recv_loop(self, peer: PeerState) -> None:
        try:
            while not peer.stop.is_set():
                frame_type, payload = protocol.read_frame(peer.sock)

                if frame_type == protocol.TYPE_MESSAGE:
                    self._handle_message(peer.addr, payload)
                elif frame_type == protocol.TYPE_ACK:
                    self._handle_ack(peer.addr, payload)
                elif frame_type == protocol.TYPE_READ:
                    self._handle_read(peer.addr, payload)
                elif frame_type == protocol.TYPE_GROUP_SETUP:
                    self._handle_group_setup(peer.addr, payload)
                elif frame_type == protocol.TYPE_PROFILE:
                    self._handle_profile(peer.addr, payload)
                elif frame_type == protocol.TYPE_PEER_ANNC:
                    self._handle_peer_annc(peer.addr, payload)
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

        # Relay if not for us
        if final_dest != self.local_mac:
            if final_dest == from_addr:
                return  # nonsense: would echo back to the peer that sent it
            if msg_id in self.seen_relayed:
                return  # already forwarded — prevent multi-path storms
            self.seen_relayed.add(msg_id)
            frame = protocol.encode_frame(protocol.TYPE_MESSAGE, payload)
            self._route_frame(final_dest, frame, exclude=from_addr)
            return

        # Atomic dedup claim. Two relay paths delivering the same msg_id
        # concurrently must not both reach on_message.
        with self.seen_lock:
            if msg_id in self.seen:
                already_seen = True
            else:
                self.seen.add(msg_id)
                already_seen = False

        if already_seen:
            ack = protocol.encode_ack(msg_id, self.local_mac_bytes)
            self.send_to(from_addr, ack)
            return

        # Decrypt using sender's pubkey. On any failure we must release the
        # claim — otherwise sender's retransmit (e.g. once their pubkey reaches
        # us) would be silently dropped by the dedup check above.
        pubkey = self.group_store.get_pubkey(sender)
        if pubkey is None:
            with self.seen_lock:
                self.seen.discard(msg_id)
            return

        box = crypto.derive_box(self.private_key, pubkey)
        try:
            plaintext = crypto.decrypt(box, encrypted)
            text = plaintext.decode("utf-8")
        except Exception:
            with self.seen_lock:
                self.seen.discard(msg_id)
            return

        if self.on_message:
            self.on_message(gid, sender, text, msg_id)

        # ACK back toward sender (through relay path)
        ack = protocol.encode_ack(msg_id, self.local_mac_bytes)
        self.send_to(from_addr, ack)

    def _handle_ack(self, from_addr: str, payload: bytes) -> None:
        msg_id, from_mac_bytes = protocol.decode_ack(payload)
        ack_from = protocol.bytes_to_mac(from_mac_bytes)
        ack_key = (msg_id, from_mac_bytes)

        if ack_key in self.seen_acks:
            return  # Already processed — don't loop
        self.seen_acks.add(ack_key)

        # Update our own delivery state
        if msg_id in self.unacked:
            self.unacked[msg_id].pop(ack_from, None)
            if not self.unacked[msg_id]:
                del self.unacked[msg_id]

        if self.on_ack:
            self.on_ack(msg_id, ack_from)

        # Flood ACK to all connected peers except source (relay back)
        ack_frame = protocol.encode_frame(protocol.TYPE_ACK, payload)
        with self.peers_lock:
            targets = [a for a in self.peers if a != from_addr]
        for addr in targets:
            self.send_to(addr, ack_frame)

    def send_read(self, msg_id: bytes) -> None:
        """Flood READ receipt toward original sender (like ACK flood-back)."""
        frame = protocol.encode_read(msg_id, self.local_mac_bytes)
        read_key = (msg_id, self.local_mac_bytes)
        self.seen_reads.add(read_key)
        with self.peers_lock:
            targets = list(self.peers.keys())
        for addr in targets:
            self.send_to(addr, frame)

    def _handle_read(self, from_addr: str, payload: bytes) -> None:
        msg_id, from_mac_bytes = protocol.decode_read(payload)
        reader = protocol.bytes_to_mac(from_mac_bytes)
        read_key = (msg_id, from_mac_bytes)

        if read_key in self.seen_reads:
            return
        self.seen_reads.add(read_key)

        if self.on_read:
            self.on_read(msg_id, reader)

        # Flood to all peers except source
        frame = protocol.encode_frame(protocol.TYPE_READ, payload)
        with self.peers_lock:
            targets = [a for a in self.peers if a != from_addr]
        for addr in targets:
            self.send_to(addr, frame)

    def _handle_profile(self, from_addr: str, payload: bytes) -> None:
        try:
            name = protocol.decode_profile(payload)
        except UnicodeDecodeError:
            return
        # Treat empty name and name-equals-MAC as "no self-chosen name" —
        # clear any previous entry so display falls back to MAC or override.
        # The MAC check handles peers running pre-fix code that broadcast
        # their own MAC as a default name.
        if not name or name == from_addr:
            removed = self.group_store.names.pop(from_addr, None)
            if removed is not None and self.on_profile:
                self.on_profile(from_addr, "")
            return
        self.group_store.set_name(from_addr, name)
        if self.on_profile:
            self.on_profile(from_addr, name)

    def _send_peer_annc(self, to_addr: str) -> None:
        """Send our known peers (MAC+pubkey) to a single peer."""
        entries = []
        for addr, pubkey in self.group_store.pubkeys.items():
            if addr == self.local_mac or addr == to_addr:
                continue
            entries.append((protocol.mac_to_bytes(addr), pubkey))
        if entries:
            self.send_to(to_addr, protocol.encode_peer_annc(entries))

    def _handle_peer_annc(self, from_addr: str, payload: bytes) -> None:
        pairs = protocol.decode_peer_annc(payload)
        with self.peers_lock:
            direct = set(self.peers.keys())
        for mac_bytes, pubkey in pairs:
            addr = protocol.bytes_to_mac(mac_bytes)
            if addr == self.local_mac:
                continue
            self.group_store.pubkeys.setdefault(addr, pubkey)
            if addr not in direct and addr not in self.indirect_via:
                self.indirect_via[addr] = from_addr

    def set_display_name(self, name: str) -> None:
        """Update our own display name and broadcast to all connected peers."""
        self.display_name = name
        if name:
            self.group_store.set_name(self.local_mac, name)
        else:
            self.group_store.names.pop(self.local_mac, None)
        frame = protocol.encode_profile(name)
        with self.peers_lock:
            targets = list(self.peers.keys())
        for addr in targets:
            self.send_to(addr, frame)

    def _handle_group_setup(self, from_addr: str, payload: bytes) -> None:
        group_id, member_list, name = protocol.decode_group_setup(payload)

        if group_id in self.group_store.groups:
            return  # Already have this group — don't forward again

        members: dict[str, bytes] = {}
        for mac_bytes, pubkey in member_list:
            addr = protocol.bytes_to_mac(mac_bytes)
            members[addr] = pubkey

        group = Group(group_id=group_id, members=members, name=name)
        self.group_store.add_group(group)

        if self.on_group_setup:
            self.on_group_setup(group)

        # Forward to connected group members who may not have received it
        frame = protocol.encode_frame(protocol.TYPE_GROUP_SETUP, payload)
        for member_addr in members:
            if member_addr == from_addr or member_addr == self.local_mac:
                continue
            self._route_frame(member_addr, frame)
