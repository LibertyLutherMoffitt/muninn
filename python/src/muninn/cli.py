import os
import queue
import readline
import shlex
import shutil
import sys
import threading
import time

from muninn import bt
from muninn.crypto import generate_keypair
from muninn.groups import Group, GroupStore
from muninn.peers import GROUP_ZERO, ConnectionManager
from muninn.protocol import FrameTooLarge

COMMANDS = ["/dm ", "/group ", "/new ", "/nick ", "/list", "/peers", "/rpeers", "/help"]


def setup_completer(conn_mgr: ConnectionManager, group_store: GroupStore):
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
            known.update(group_store.pubkeys.keys())
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
        if name:
            self._display(f"  {addr} is now known as {name}")
        else:
            self._display(f"  {addr} cleared their display name")

    def _flush_reads(self, conv_key: tuple[str, str | bytes]) -> None:
        for msg_id in self.unread.pop(conv_key, []):
            self.conn_mgr.send_read(msg_id)

    def _on_peer_change(self, addr: str, connected: bool) -> None:
        label = self._name(addr)
        if connected:
            self._display(f"+ {label} connected")
            if self.active_conv is None:
                self.active_conv = ("dm", addr)
                self._display(f"  Active conversation: DM with {label}")
        else:
            self._display(f"- {label} disconnected")

    def _on_group_setup(self, group: Group) -> None:
        members = len(group.members)
        self._display(f"+ Group '{group.name}' created ({members} members)")

    def _input_reader(self) -> None:
        try:
            while True:
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
            with self.conn_mgr.peers_lock:
                addrs = list(self.conn_mgr.peers.keys())
            if not addrs:
                print("No connected peers.")
            else:
                print("Connected peers:")
                for addr in addrs:
                    name = self._name(addr)
                    suffix = f" ({addr})" if name != addr else ""
                    print(f"  {name}{suffix}")

        elif cmd == "/rpeers":
            with self.conn_mgr.peers_lock:
                direct = set(self.conn_mgr.peers.keys())
            indirect = [
                (addr, self.conn_mgr.indirect_via.get(addr, "?"))
                for addr in self.group_store.pubkeys
                if addr != self.local_mac and addr not in direct
            ]
            if not indirect:
                print("No reachable indirect peers.")
            else:
                print("Reachable via relay:")
                for addr, via in indirect:
                    name = self._name(addr)
                    suffix = f" ({addr})" if name != addr else ""
                    via_name = self._name(via)
                    print(f"  {name}{suffix}  via {via_name}")

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

        elif cmd == "/help":
            print("Commands:")
            print("  /dm <name|addr>         — switch to DM")
            print("  /group <name>           — switch to group")
            print("  /new <name> <p1> [p2..] — create group")
            print("  /nick <name>            — set your own display name")
            print("  /nick <peer> <name>     — local override for a peer")
            print('  /nick <peer> ""         — clear a local override')
            print("  /list                   — show conversations")
            print("  /peers                  — show connected peers")
            print("  /rpeers                 — show reachable peers via relay")

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
                            self._status(f"! skipped {self._name(addr)} (no pubkey)")

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
    # Initial scan to populate BlueZ cache
    try:
        bt.scan_devices(duration=5)
    except Exception:
        pass

    deferred: dict[str, float] = {}  # MAC tiebreaker deferral

    while not stop.is_set():
        try:
            services = bt.discover()
        except Exception:
            services = []

        for addr, _name in services:
            addr = addr.upper()
            if addr == local_mac:
                continue
            with conn_mgr.peers_lock:
                if addr in conn_mgr.peers:
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
                conn_mgr.add_peer(sock, peer_addr)
            except (ConnectionError, OSError):
                pass

        stop.wait(15)


def main():
    local_mac = bt.get_local_mac()
    print(f"Local MAC: {local_mac}")

    private_key = generate_keypair()
    group_store = GroupStore()
    display_name = os.environ.get("MUNINN_NAME", "")
    conn_mgr = ConnectionManager(
        local_mac, private_key, group_store, display_name=display_name
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

    print("\nBye.")


if __name__ == "__main__":
    main()
