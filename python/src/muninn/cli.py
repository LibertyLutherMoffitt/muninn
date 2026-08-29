import os
import queue
import shlex
import shutil
import sys
import threading
import time

try:
    import readline

    _HAS_READLINE = True
except ImportError:
    _HAS_READLINE = False

from muninn import bt, presence
from muninn.crypto import generate_keypair, privkey_from_bytes
from muninn.groups import Group, GroupStore
from muninn.peers import GROUP_ZERO, ConnectionManager
from muninn.protocol import FrameTooLarge
from muninn.storage import Storage

COMMANDS = [
    "/dm ",
    "/group ",
    "/new ",
    "/nick ",
    "/list",
    "/peers",
    "/known",
    "/history",
    "/help",
]

HISTORY_DEFAULT = 20
HISTORY_MAX = 500

# How long to hold a new peer's "connected" line waiting for its Profile
# frame, so we can name it instead of printing a MAC and correcting it.
PROFILE_GRACE = 1.0


def setup_completer(conn_mgr: ConnectionManager, group_store: GroupStore):
    if not _HAS_READLINE:
        return

    def completer(text, state):
        buf = readline.get_line_buffer().lstrip()
        if (
            buf.startswith("/dm ")
            or buf.startswith("/new ")
            or buf.startswith("/nick ")
        ):
            # Offer MACs + display names for every peer we know about, not
            # just the currently-connected ones — matches what resolve()
            # accepts so tab-complete never refuses something the command
            # parser would have accepted.
            low = text.lower()
            upper = text.upper()
            with conn_mgr.peers_lock:
                known = set(conn_mgr.peers.keys())
            known.update(a for a in group_store.pubkeys if a != conn_mgr.local_mac)
            known.update(group_store.names.keys())
            known.update(group_store.overrides.keys())
            options = [a for a in known if a.startswith(upper)]
            seen_addrs = set(options)
            for addr in known:
                name = group_store.display_name(addr)
                if (
                    name != addr
                    and name.lower().startswith(low)
                    and addr not in seen_addrs
                ):
                    options.append(name)
                    seen_addrs.add(addr)
        elif buf.startswith("/group "):
            options = [
                g.name for g in group_store.groups.values() if g.name.startswith(text)
            ]
        elif buf.startswith("/"):
            options = [c for c in COMMANDS if c.startswith(buf)]
        else:
            options = []
        try:
            return options[state]
        except IndexError:
            return None

    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")
    readline.set_completer_delims(" ")


class ChatUI:
    def __init__(
        self,
        conn_mgr: ConnectionManager,
        group_store: GroupStore,
        local_mac: str,
    ):
        self.conn_mgr = conn_mgr
        self.group_store = group_store
        self.local_mac = local_mac
        self.active_conv: tuple[str, str | bytes] | None = None
        self.input_queue: queue.Queue = queue.Queue()
        self._display_lock = threading.Lock()
        # Set while input() is blocking so _display() knows to redraw the prompt.
        self._input_active = threading.Event()

        # msg_id -> set of dest addrs (for our outbound msgs)
        self.outbound: dict[bytes, set[str]] = {}
        # conv_key -> [(msg_id, sender_addr)] — unread incoming msgs per conv
        self.unread: dict[tuple[str, str | bytes], list[bytes]] = {}
        # Connect announcements held back until the peer's Profile arrives.
        self._pending_connects: dict[str, threading.Timer] = {}
        self._pending_lock = threading.Lock()
        # Blocks _input_reader from drawing the next prompt until the current
        # command has finished printing all output.
        self._ready_for_prompt = threading.Event()
        self._ready_for_prompt.set()

        conn_mgr.on_message = self._on_message
        conn_mgr.on_peer_change = self._on_peer_change
        conn_mgr.on_group_setup = self._on_group_setup
        conn_mgr.on_ack = self._on_ack
        conn_mgr.on_read = self._on_read
        conn_mgr.on_profile = self._on_profile

    def _name(self, addr: str) -> str:
        return self.group_store.display_name(addr)

    def _prompt(self) -> str:
        if self.active_conv is None:
            return "> "
        conv_type, key = self.active_conv
        if conv_type == "dm":
            assert isinstance(key, str)
            return f"[DM:{self._name(key)}] > "
        assert isinstance(key, bytes)
        group = self.group_store.groups.get(key)
        name = group.name if group else "?"
        return f"[{name}] > "

    def _display(self, msg: str) -> None:
        """Print a line without corrupting the readline input in progress.

        Clears the current terminal line (which readline drew as prompt +
        partial input), prints msg, then redraws prompt + buffer so the user
        can keep typing from where they left off. The redraw is skipped when
        input() is not currently blocking (e.g. between commands) to avoid
        spurious blank prompt lines.
        """
        if not _HAS_READLINE:
            with self._display_lock:
                print(msg)
            return
        buf = readline.get_line_buffer()
        prompt = self._prompt()
        with self._display_lock:
            sys.stdout.write("\r\033[K" + msg + "\n")
            if self._input_active.is_set():
                sys.stdout.write(prompt + buf)
            sys.stdout.flush()

    def _status(self, text: str) -> None:
        """Print a right-aligned delivery status indicator."""
        cols = shutil.get_terminal_size(fallback=(80, 24)).columns
        self._display(text.rjust(cols))

    def _on_message(
        self, group_id: bytes, sender_mac: str, text: str, msg_id: bytes
    ) -> None:
        sender_name = self._name(sender_mac)
        if group_id == GROUP_ZERO:
            conv_key: tuple[str, str | bytes] = ("dm", sender_mac)
            self._display(f"[DM:{sender_name}] < {text}")
        else:
            conv_key = ("group", group_id)
            group = self.group_store.groups.get(group_id)
            name = group.name if group else "?"
            self._display(f"[{name}] < {sender_name}: {text}")

        if self.active_conv == conv_key:
            self.conn_mgr.send_read(msg_id)
        else:
            self.unread.setdefault(conv_key, []).append(msg_id)

    def _on_ack(self, msg_id: bytes, from_mac: str) -> None:
        if msg_id in self.outbound:
            self._status(f"\u2713 {self._name(from_mac)}")

    def _on_read(self, msg_id: bytes, from_mac: str) -> None:
        if msg_id in self.outbound:
            self._status(f"\u2713\u2713 {self._name(from_mac)}")

    def _on_profile(self, addr: str, name: str) -> None:
        # A peer's Profile frame usually lands within milliseconds of its
        # handshake, but the two race: whichever side finishes add_peer() last
        # sees the name first. If we are still holding this peer's "connected"
        # line waiting for exactly this, announce it now with the real name
        # instead of printing a MAC and correcting it a moment later.
        if self._resolve_pending_connect(addr):
            return
        if name:
            self._display(f"  {addr} is now known as {name}")
        else:
            self._display(f"  {addr} cleared their display name")

    def _resolve_pending_connect(self, addr: str) -> bool:
        """Flush a deferred connect announcement for `addr`. True if we did."""
        with self._pending_lock:
            timer = self._pending_connects.pop(addr, None)
        if timer is None:
            return False
        timer.cancel()
        self._announce_connected(addr)
        return True

    def _announce_connected(self, addr: str) -> None:
        label = self._name(addr)
        self._display(f"+ {label} connected")
        if self.active_conv is None:
            self.active_conv = ("dm", addr)
            self._display(f"  Active conversation: DM with {label}")
            self._render_history(self.active_conv)

    def _flush_reads(self, conv_key: tuple[str, str | bytes]) -> None:
        for msg_id in self.unread.pop(conv_key, []):
            self.conn_mgr.send_read(msg_id)

    def _render_history(
        self, conv_key: tuple[str, str | bytes], limit: int = HISTORY_DEFAULT
    ) -> None:
        """Print recent stored messages for a conv. No-op if storage unset."""
        storage = self.conn_mgr.storage
        if storage is None:
            return
        conv_type, key = conv_key
        msgs: list[tuple[bytes, str, str, int, str]]
        if conv_type == "dm":
            assert isinstance(key, str)
            peer_name = self._name(key)
            msgs = storage.load_dm_history(self.local_mac, key, limit)
            if not msgs:
                return
            plural = "s" if len(msgs) != 1 else ""
            self._display(f"--- {len(msgs)} previous message{plural} ---")
            for _msg_id, sender, body, ts, _ack in msgs:
                t = time.strftime("%H:%M", time.localtime(ts))
                arrow = ">" if sender == self.local_mac else "<"
                self._display(f"  {t} [DM:{peer_name}] {arrow} {body}")
        else:
            assert isinstance(key, bytes)
            group = self.group_store.groups.get(key)
            gname = group.name if group else "?"
            msgs = storage.load_group_history(key, self.local_mac, limit)
            if not msgs:
                return
            plural = "s" if len(msgs) != 1 else ""
            self._display(f"--- {len(msgs)} previous message{plural} ---")
            for _msg_id, sender, body, ts, _ack in msgs:
                t = time.strftime("%H:%M", time.localtime(ts))
                if sender == self.local_mac:
                    self._display(f"  {t} [{gname}] > {body}")
                else:
                    self._display(f"  {t} [{gname}] < {self._name(sender)}: {body}")
        self._display("---")

    def _presence_label(self, addr: str) -> str:
        """A peer's connectivity, rendered for a terminal."""
        status = self.conn_mgr.presence.status(addr)
        if status.state == presence.CONNECTED:
            return "connected"
        if status.state == presence.RELAY:
            via = status.via
            return f"relay via {self._name(via)}" if via else "relay"
        return status.describe()

    def _print_presence(self, reachable_only: bool) -> None:
        """Render the peer list grouped by how reachable each peer is.

        `/peers` shows only what we can talk to right now; `/known` adds every
        peer we have ever exchanged keys with, plus any device the radio has
        seen but we could not connect to.
        """
        tracker = self.conn_mgr.presence
        tracker.sync_from_manager(self.conn_mgr)
        statuses = tracker.all_statuses()

        known = {a for a in self.group_store.pubkeys if a != self.local_mac}
        # Devices seen in a scan that we have no key for are still worth
        # showing — that is the "someone nearby isn't paired yet" case.
        candidates = known | {
            a
            for a, s in statuses.items()
            if s.state != presence.OFFLINE or s.last_seen is not None
        }

        buckets: dict[str, list[str]] = {
            presence.CONNECTED: [],
            presence.RELAY: [],
            presence.NEARBY: [],
            presence.OFFLINE: [],
        }
        for addr in sorted(candidates):
            state = statuses.get(addr, tracker.status(addr)).state
            buckets.setdefault(state, []).append(addr)

        headings = [
            (presence.CONNECTED, "Connected"),
            (presence.RELAY, "Reachable via relay"),
            (presence.NEARBY, "Nearby"),
        ]
        if not reachable_only:
            headings.append((presence.OFFLINE, "Not in range"))

        printed = False
        for state, heading in headings:
            addrs = buckets.get(state) or []
            if not addrs:
                continue
            printed = True
            print(f"{heading}:")
            for addr in addrs:
                name = self._name(addr)
                suffix = f" ({addr})" if name != addr else ""
                unknown = " · no key yet" if addr not in known else ""
                print(f"  {name}{suffix}  — {self._presence_label(addr)}{unknown}")
        if not printed:
            print(
                "No peers yet."
                if reachable_only
                else "No known peers and nothing seen nearby."
            )

    def _on_peer_change(self, addr: str, connected: bool) -> None:
        if not connected:
            self._resolve_pending_connect(addr)
            self._display(f"- {self._name(addr)} disconnected")
            return

        if self._name(addr) != addr:
            # We already know what to call them (stored from a previous run, or
            # their Profile beat our handshake) — nothing to wait for.
            self._announce_connected(addr)
            return

        # First sight of this peer. Give their Profile frame a moment to land so
        # the user sees a name rather than a MAC that renames itself.
        timer = threading.Timer(
            PROFILE_GRACE, lambda: self._resolve_pending_connect(addr)
        )
        timer.daemon = True
        with self._pending_lock:
            self._pending_connects[addr] = timer
        timer.start()

    def _on_group_setup(self, group: Group) -> None:
        members = len(group.members)
        self._display(f"+ Group '{group.name}' created ({members} members)")

    def _input_reader(self) -> None:
        try:
            while True:
                self._ready_for_prompt.wait()
                self._ready_for_prompt.clear()
                self._input_active.set()
                line = input(self._prompt())
                self._input_active.clear()
                self.input_queue.put(line)
        except (EOFError, KeyboardInterrupt):
            self._input_active.clear()
            self.input_queue.put(None)

    def _handle_command(self, text: str) -> None:
        # shlex lets names/values contain spaces when quoted
        # (e.g. `/nick "Long Name"`, `/new "Sky Team" alice bob`).
        try:
            parts = shlex.split(text)
        except ValueError as e:
            print(f"Parse error: {e}")
            return
        if not parts:
            return
        cmd = parts[0].lower()

        if cmd == "/peers":
            self._print_presence(reachable_only=True)

        elif cmd == "/known":
            self._print_presence(reachable_only=False)

        elif cmd == "/dm":
            if len(parts) < 2:
                print("Usage: /dm <name|addr>")
                return
            resolved = self.group_store.resolve(parts[1])
            if resolved is None:
                print(f"Unknown peer: {parts[1]}")
                return
            self.active_conv = ("dm", resolved)
            self._flush_reads(self.active_conv)
            print(f"Switched to DM with {self._name(resolved)}")
            self._render_history(self.active_conv)

        elif cmd == "/group":
            if len(parts) < 2:
                print("Usage: /group <name>")
                return
            name = parts[1]
            for gid, group in self.group_store.groups.items():
                if group.name == name:
                    self.active_conv = ("group", gid)
                    self._flush_reads(self.active_conv)
                    print(f"Switched to group '{name}'")
                    self._render_history(self.active_conv)
                    return
            print(f"Group '{name}' not found.")

        elif cmd == "/new":
            if len(parts) < 3:
                print("Usage: /new <name> <peer1> [peer2] ...")
                return
            name = parts[1]
            addrs: list[str] = []
            for p in parts[2:]:
                resolved = self.group_store.resolve(p)
                if resolved is None:
                    print(f"Unknown peer: {p}")
                    return
                addrs.append(resolved)
            try:
                group = self.conn_mgr.create_group(name, addrs)
                self.active_conv = ("group", group.group_id)
                self._flush_reads(self.active_conv)
                print(f"Created group '{name}'")
            except ValueError as e:
                print(f"Error: {e}")

        elif cmd == "/nick":
            if len(parts) == 2:
                # /nick <name> — set our own name and broadcast.
                new_name = parts[1]
                self.conn_mgr.set_display_name(new_name)
                if new_name:
                    print(f"You are now known as '{new_name}'")
                else:
                    print("Cleared your display name")
            elif len(parts) == 3:
                # /nick <peer> <name> — set local override, or clear it
                # when <name> is empty (pass as "" via shell quoting).
                resolved = self.group_store.resolve(parts[1])
                if resolved is None:
                    print(f"Unknown peer: {parts[1]}")
                    return
                if parts[2] == "":
                    self.group_store.clear_override(resolved)
                    print(f"Cleared override for {resolved}")
                else:
                    self.group_store.set_override(resolved, parts[2])
                    print(f"Local override: {resolved} → '{parts[2]}'")
            else:
                print(
                    "Usage: /nick <name>  |  /nick <peer> <name>  "
                    '|  /nick <peer> ""  (clear override)'
                )

        elif cmd == "/list":
            print("Conversations:")
            with self.conn_mgr.peers_lock:
                for addr in self.conn_mgr.peers:
                    marker = " *" if self.active_conv == ("dm", addr) else ""
                    print(f"  DM: {self._name(addr)}{marker}")
            for gid, group in self.group_store.groups.items():
                marker = " *" if self.active_conv == ("group", gid) else ""
                n = len(group.members)
                print(f"  Group: {group.name} ({n} members){marker}")

        elif cmd == "/history":
            if self.active_conv is None:
                print("No active conversation.")
                return
            limit = HISTORY_DEFAULT
            if len(parts) >= 2:
                try:
                    limit = int(parts[1])
                except ValueError:
                    print(f"Usage: /history [N]  (default {HISTORY_DEFAULT})")
                    return
                if limit <= 0:
                    print("N must be positive.")
                    return
                limit = min(limit, HISTORY_MAX)
            self._render_history(self.active_conv, limit=limit)

        elif cmd == "/help":
            print("Commands:")
            print("  /dm <name|addr>         — switch to DM")
            print("  /group <name>           — switch to group")
            print("  /new <name> <p1> [p2..] — create group")
            print("  /nick <name>            — set your own display name")
            print("  /nick <peer> <name>     — local override for a peer")
            print('  /nick <peer> ""         — clear a local override')
            print("  /list                   — show conversations")
            print("  /peers                  — who is reachable right now")
            print("  /known                  — every known peer + who is nearby")
            print(
                f"  /history [N]            — show last N msgs (default {HISTORY_DEFAULT})"
            )

        else:
            print(f"Unknown command: {cmd}. Type /help")

    def run(self) -> None:
        threading.Thread(target=self._input_reader, daemon=True).start()

        try:
            while True:
                try:
                    line = self.input_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                if line is None:
                    break

                text = line.strip()
                try:
                    if not text:
                        continue

                    if text.startswith("/"):
                        self._handle_command(text)
                        continue

                    if self.active_conv is None:
                        print("No active conversation. Use /dm <addr> or /peers")
                        continue

                    conv_type, key = self.active_conv
                    dests: list[str] = []
                    gid: bytes = GROUP_ZERO
                    if conv_type == "dm":
                        assert isinstance(key, str)
                        dests = [key]
                    elif conv_type == "group":
                        assert isinstance(key, bytes)
                        group = self.group_store.groups.get(key)
                        if group:
                            dests = [a for a in group.members if a != self.local_mac]
                            gid = key
                        else:
                            print("Group not found.")

                    if dests:
                        # Display before send so peer-disconnect messages from
                        # send_to's error path appear after, not before.
                        self._status("\u29d7")
                        try:
                            result = self.conn_mgr.send_message(gid, text, dests)
                        except FrameTooLarge as e:
                            self._status(f"! message too large: {e}")
                            continue
                        if result is None:
                            self._status("! no reachable recipient (no pubkey)")
                        else:
                            msg_id, sent, skipped = result
                            self.outbound[msg_id] = set(sent)
                            for addr in skipped:
                                self._status(
                                    f"! skipped {self._name(addr)} (no pubkey)"
                                )
                finally:
                    self._ready_for_prompt.set()

        except KeyboardInterrupt:
            pass


def acceptor(conn_mgr: ConnectionManager) -> None:
    """Accept incoming connections and hand to ConnectionManager."""
    while True:
        try:
            sock, addr = bt.accept()
            conn_mgr.add_peer(sock, addr)
        except ConnectionError:
            break


def scanner(conn_mgr: ConnectionManager, local_mac: str, stop: threading.Event) -> None:
    """Periodically discover and connect to new Muninn peers."""
    # Initial scan to populate the BlueZ cache. Its results are sightings too —
    # discarding them left a device visible at launch looking offline until the
    # first periodic re-scan, two minutes later.
    try:
        for addr, _name in bt.scan_devices(duration=5):
            conn_mgr.presence.record_sighting(addr)
    except Exception:
        pass

    deferred: dict[str, float] = {}  # MAC tiebreaker deferral
    cycles = 0
    presence = conn_mgr.presence

    while not stop.is_set():
        cycles += 1
        # Refresh BT cache every ~2 min so UUIDs stay current and newly-online
        # peers are discoverable even if they missed the initial scan window.
        # Every device the inquiry returns counts as a sighting, Muninn or not —
        # that is what lets the UI say "nearby but can't connect" rather than
        # silently showing a peer as offline.
        if cycles % 8 == 0:
            try:
                for addr, _name in bt.scan_devices(duration=5, quiet=True):
                    presence.record_sighting(addr)
            except Exception:
                pass
        try:
            services = bt.discover()
        except Exception:
            services = []

        for addr, _name in services:
            addr = addr.upper()
            if addr == local_mac:
                continue
            presence.record_sighting(addr)
            if conn_mgr.is_connected(addr):
                deferred.pop(addr, None)
                continue

            # Higher MAC defers 10s to let lower MAC initiate
            if not bt.should_keep_outgoing(local_mac, addr):
                if addr not in deferred:
                    deferred[addr] = time.time()
                    continue
                if time.time() - deferred[addr] < 10:
                    continue

            deferred.pop(addr, None)
            try:
                bt.ensure_paired(addr)
                sock, peer_addr = bt.connect(addr)
                if not conn_mgr.add_peer(sock, peer_addr):
                    presence.record_dial_failure(addr, "handshake failed")
            except (ConnectionError, OSError) as e:
                # Visible to the radio but unreachable. Recorded so the peer
                # list can distinguish this from "out of range entirely".
                presence.record_dial_failure(addr, str(e))

        stop.wait(15)


def main():
    local_mac = bt.get_local_mac()
    print(f"Local MAC: {local_mac}")

    storage = Storage()

    # Load or create persistent identity. Privkey is reused across runs so
    # our X25519 pubkey (and therefore shared secrets with peers) is stable.
    identity = storage.get_identity()
    if identity is None:
        private_key = generate_keypair()
        identity = storage.create_identity(bytes(private_key))
    else:
        private_key = privkey_from_bytes(identity.privkey)

    # Register ourselves in the peers table so load_groups can JOIN on
    # local_mac and include us in every group's member list.
    storage.save_peer_pubkey(local_mac, bytes(private_key.public_key))

    # MUNINN_NAME env var takes precedence over persisted name so users can
    # override per-session without clobbering persisted state.
    env_name = os.environ.get("MUNINN_NAME", "")
    display_name = env_name or identity.display_name

    group_store = GroupStore(storage=storage)
    conn_mgr = ConnectionManager(
        local_mac,
        private_key,
        group_store,
        display_name=display_name,
        storage=storage,
    )
    if display_name:
        print(f"Display name: {display_name}")

    setup_completer(conn_mgr, group_store)
    bt.create_server()

    stop = threading.Event()
    threading.Thread(target=acceptor, args=(conn_mgr,), daemon=True).start()
    threading.Thread(
        target=scanner, args=(conn_mgr, local_mac, stop), daemon=True
    ).start()

    print("Scanning for peers... (type /help for commands)")

    ui = ChatUI(conn_mgr, group_store, local_mac)
    try:
        ui.run()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        bt.close_server()
        storage.close()

    print("\nBye.")


if __name__ == "__main__":
    main()
