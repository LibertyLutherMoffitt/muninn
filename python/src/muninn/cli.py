"""Readline CLI frontend. Commands are `/`-prefixed; plain text sends to the
active conversation. Messages are shown with a short `#n` handle that other
commands (/reply, /react, /edit, /delete, /get) refer to."""

from __future__ import annotations

import mimetypes
import os
import shlex
import sys
import threading
import time

try:
    import readline

    _HAS_READLINE = True
except ImportError:  # Windows without pyreadline
    _HAS_READLINE = False

from muninn import emoji
from muninn.mesh import conv_group_id, dm_conv, group_conv
from muninn.node import Node, default_transport
from muninn.protocol import FrameTooLarge
from muninn.storage import StoredMessage

COMMANDS = [
    "/dm ",
    "/group ",
    "/new ",
    "/add ",
    "/kick ",
    "/leave",
    "/rename ",
    "/nick ",
    "/list",
    "/peers",
    "/known",
    "/history",
    "/reply ",
    "/react ",
    "/edit ",
    "/delete ",
    "/file ",
    "/get ",
    "/scan",
    "/connect ",
    "/status",
    "/emoji ",
    "/help",
    "/quit",
]

HELP = """\
/dm <peer>                 switch to a direct conversation
/group <name>              switch to a group
/new <name> <peer> [...]   create a group
/add <peer> [...]          add people to the current group
/kick <peer>               remove someone from the current group
/rename <name>             rename the current group
/leave                     leave the current group
/nick <name>               set your display name
/nick <peer> <name|"">     local nickname for a peer ("" clears)
/list                      conversations (with unread counts)
/peers  /known             reachable peers  /  everyone you know
/history [N]               show the last N messages
/reply <#n> <text>         reply to a message
/react <#n> <emoji>        react (again to remove), e.g. /react #3 :fire:
/edit <#n> <text>          edit your message
/delete <#n>               delete your message for everyone
/file <path> [caption]     send a file / picture
/get <#n>                  download an offered file (big files are manual)
/scan                      look for nearby devices
/find [minutes|off]        look for new people for a while (default 5 min)
/reconnect [fast|saver]    retry known people now / how eagerly to redial them
/whoami                    your name, id, radio and scan mode
/connect <addr>            dial a device by Bluetooth address
/status                    links, routes, queues
/emoji <prefix>            search emoji shortcodes
/sticker [name]            list or send a bundled/saved sticker GIF
/members                   who's in this group and who's in range
/mute  /unmute             silence the terminal bell for this chat
/scores                    who won how many of each game (this chat, or all)
/clear                     delete this chat on this device (a room: leave it too)
/forget <peer> [...]       forget people: chat, saved addresses, pairing
/block <peer>  /unblock <peer>   refuse / allow someone again;  /blocked lists
/vim [on|off]              vim keys in the full-screen client (default on)
/search <words>            find messages in all chats
/quit
Text supports :shortcodes: like :thumbsup: :airplane: :joy:"""


class ChatUI:
    def __init__(self, node: Node):
        self.node = node
        self.mesh = node.mesh
        self.conv: str = ""
        self._lock = threading.Lock()
        self._refs: dict[int, bytes] = {}
        self._ref_by_id: dict[bytes, int] = {}
        self._next_ref = 1
        m = self.mesh
        m.on("message", self._on_message)
        m.on("update", self._on_update)
        m.on("group", lambda gid: None)
        m.on("file", self._on_file)
        m.on("typing", self._on_typing)
        self._last_peers: set[str] = set()
        m.on("peers", self._on_peers)

    # --- display -------------------------------------------------------------

    def _prompt(self) -> str:
        if not self.conv:
            return "> "
        return f"[{self._conv_name(self.conv)}] > "

    def _conv_name(self, conv: str) -> str:
        if conv.startswith("dm:"):
            return self.mesh.display_name(conv[3:])
        g = self.mesh.groups.get(conv_group_id(conv) or b"")
        return f"#{g.name}" if g else "#?"

    def out(self, line: str) -> None:
        with self._lock:
            if _HAS_READLINE:
                buf = readline.get_line_buffer()
                sys.stdout.write("\r\033[K" + line + "\n" + self._prompt() + buf)
            else:
                sys.stdout.write("\r" + line + "\n")
            sys.stdout.flush()

    def _ref(self, msg_id: bytes) -> int:
        r = self._ref_by_id.get(msg_id)
        if r is None:
            r = self._next_ref
            self._next_ref += 1
            self._refs[r] = msg_id
            self._ref_by_id[msg_id] = r
        return r

    def _fmt(self, m: StoredMessage) -> str:
        t = time.strftime("%H:%M", time.localtime(m.ts))
        who = (
            "you" if m.sender == self.mesh.node_id else self.mesh.display_name(m.sender)
        )
        ref = self._ref(m.msg_id)
        if m.kind == "system":
            return f"  {t} · {m.text}"
        if m.deleted:
            body = "(deleted)"
        elif m.kind == "file":
            fid = bytes.fromhex(m.meta.get("f", "00"))
            have, total, state = self.mesh.file_progress(fid)
            size = m.meta.get("s", 0)
            status = {"complete": "✓", "downloading": f"{have}/{total}"}.get(
                state, "/get to download"
            )
            body = f"📎 {m.meta.get('n')} ({_human(size)}) [{status}]"
            if m.text:
                body += f" — {m.text}"
        else:
            body = m.text
        if m.edited and not m.deleted:
            body += " (edited)"
        reply = ""
        if m.reply_to:
            orig = self.mesh.storage.get_message(m.reply_to)
            if orig:
                reply = f"↪ {self.mesh.display_name(orig.sender)}: {orig.text[:30]!r} "
        ticks = ""
        if m.sender == self.mesh.node_id:
            ticks = {"sent": " ◌", "delivered": " ✓", "read": " ✓✓"}[m.state]
        reacts = ""
        if m.reactions:
            reacts = "  " + " ".join(
                f"{e}{len(w) if len(w) > 1 else ''}" for e, w in m.reactions.items()
            )
        return f"  {t} #{ref} {who}: {reply}{body}{ticks}{reacts}"

    # --- events (event thread) ---------------------------------------------

    def _on_message(self, m: StoredMessage) -> None:
        if m.conv == self.conv:
            self.out(self._fmt(m))
            if m.sender != self.mesh.node_id:
                self.mesh.mark_read(m.conv)
        elif m.sender != self.mesh.node_id:
            prev = m.text if m.kind != "file" else "📎 " + m.meta.get("n", "file")
            self.out(
                f"  ✉ {self._conv_name(m.conv)} · {self.mesh.display_name(m.sender)}: {prev[:60]}"
            )

    def _on_update(self, msg_id: bytes) -> None:
        m = self.mesh.storage.get_message(msg_id)
        if m and m.conv == self.conv and msg_id in self._ref_by_id:
            self.out(self._fmt(m).replace("  ", "  ↻ ", 1))

    def _on_file(self, file_id: bytes) -> None:
        have, total, state = self.mesh.file_progress(file_id)
        if state == "complete":
            rec = self.mesh.files.get(file_id)
            if rec and rec.sender != self.mesh.node_id:
                self.out(f"  📎 saved {rec.name} → {rec.path}")

    def _on_typing(self, conv: str, node: str) -> None:
        pass  # too chatty for a terminal

    def _on_peers(self) -> None:
        now = set(self.mesh.connected())
        for n in now - self._last_peers:
            self.out(f"  + {self.mesh.display_name(n)} connected")
        for n in self._last_peers - now:
            self.out(f"  - {self.mesh.display_name(n)} disconnected")
        self._last_peers = now

    # --- commands ------------------------------------------------------------

    def _peer(self, s: str) -> str | None:
        r = self.mesh.resolve(s)
        if r is None:
            self.out(f"! unknown peer: {s}")
        return r

    def _peers_of(self, names: list[str]) -> list[str] | None:
        out = []
        for n in names:
            p = self._peer(n)
            if p is None:
                return None
            out.append(p)
        return out

    def _msg(self, ref: str) -> bytes | None:
        try:
            mid = self._refs[int(ref.lstrip("#"))]
        except (ValueError, KeyError):
            self.out(f"! no message {ref}")
            return None
        return mid

    _armed: tuple[str, float] = ("", 0.0)

    def _confirm(self, line: str) -> bool:
        """Destructive commands run when typed twice within 15 s."""
        key = line.strip()
        if self._armed[0] == key and time.monotonic() - self._armed[1] < 15:
            self._armed = ("", 0.0)
            return True
        self._armed = (key, time.monotonic())
        what = {
            "clear": "deletes this chat on this device (a room is also left)",
            "forget": "deletes their chat, saved addresses and pairing; Muninn stops reconnecting",
            "block": "forgets them and refuses their connections and messages",
        }[key.split()[0].lstrip("/")]
        self.out(f"  this {what} — repeat the command to confirm")
        return False

    def _need_conv(self) -> bool:
        if not self.conv:
            self.out("! no active conversation (/dm or /group first)")
            return False
        return True

    def handle(self, line: str) -> bool:
        line = line.strip()
        if not line:
            return True
        if not line.startswith("/"):
            if self._need_conv():
                try:
                    self.mesh.send_text(self.conv, emoji.expand(line))
                except (ValueError, FrameTooLarge) as e:
                    self.out(f"! {e}")
            return True
        try:
            parts = shlex.split(line)
        except ValueError as e:
            self.out(f"! {e}")
            return True
        cmd, args = parts[0][1:].lower(), parts[1:]
        mesh = self.mesh
        if cmd in ("quit", "q", "exit"):
            return False
        if cmd == "help":
            self.out(HELP)
        elif cmd == "dm" and args:
            if p := self._peer(args[0]):
                self.switch(dm_conv(p))
        elif cmd == "group" and args:
            for g in mesh.groups.values():
                if g.name.lower() == args[0].lower():
                    self.switch(g.conv)
                    break
            else:
                self.out(f"! no group {args[0]}")
        elif cmd == "new" and len(args) >= 2:
            peers = self._peers_of(args[1:])
            if peers is not None:
                gid = mesh.create_group(args[0], peers)
                self.switch(group_conv(gid))
        elif cmd in ("add", "kick", "rename", "leave"):
            gid = conv_group_id(self.conv)
            if gid is None:
                self.out("! not in a group conversation")
            elif cmd == "add" and args:
                peers = self._peers_of(args)
                if peers is not None:
                    mesh.update_group(gid, add=peers)
            elif cmd == "kick" and args:
                if p := self._peer(args[0]):
                    mesh.update_group(gid, remove=[p])
            elif cmd == "rename" and args:
                mesh.update_group(gid, name=" ".join(args))
            elif cmd == "leave":
                mesh.leave_group(gid)
        elif cmd == "nick" and len(args) == 1:
            mesh.set_name(args[0])
            self.out(f"  you are now {args[0]}")
        elif cmd == "nick" and len(args) == 2:
            if p := self._peer(args[0]):
                mesh.set_override(p, args[1] or None)
        elif cmd == "list":
            unread = mesh.storage.unread_counts()
            last = mesh.storage.last_messages()
            convs = {dm_conv(p.node_id) for p in mesh.active_peers()} | {
                g.conv for g in mesh.groups.values()
            }
            for c in sorted(convs, key=lambda c: -(last[c].ts if c in last else 0)):
                u = unread.get(c, 0)
                self.out(
                    f"  {'*' if c == self.conv else ' '} {self._conv_name(c)}"
                    f"{f'  ({u} unread)' if u else ''}"
                )
        elif cmd in ("peers", "known"):
            for nid, p in sorted(
                ((p.node_id, p) for p in mesh.active_peers()),
                key=lambda kv: kv[1].display,
            ):
                pres = self.node.presence(nid)
                if cmd == "peers" and pres["state"] not in ("direct", "relay"):
                    continue
                n = mesh.pending_count(nid)
                held = f" · {n} held for them" if n else ""
                self.out(f"  {p.display:20} {pres['text']}{held}   {nid}")
        elif cmd == "history":
            if self._need_conv():
                n = int(args[0]) if args and args[0].isdigit() else 30
                for m in mesh.storage.load_history(self.conv, n):
                    self.out(self._fmt(m))
        elif cmd == "reply" and len(args) >= 2 and self._need_conv():
            if mid := self._msg(args[0]):
                mesh.send_text(
                    self.conv, emoji.expand(" ".join(args[1:])), reply_to=mid
                )
        elif cmd == "react" and len(args) == 2 and self._need_conv():
            if mid := self._msg(args[0]):
                e = emoji.expand(args[1])
                m = mesh.storage.get_message(mid)
                mine = m and mesh.node_id in m.reactions.get(e, [])
                mesh.react(self.conv, mid, e, remove=bool(mine))
        elif cmd == "edit" and len(args) >= 2 and self._need_conv():
            if mid := self._msg(args[0]):
                mesh.edit(self.conv, mid, emoji.expand(" ".join(args[1:])))
        elif cmd == "delete" and args and self._need_conv():
            if mid := self._msg(args[0]):
                mesh.delete(self.conv, mid)
        elif cmd == "file" and args and self._need_conv():
            path = os.path.expanduser(args[0])
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
            try:
                mesh.send_file(self.conv, path, mime, caption=" ".join(args[1:]))
            except (OSError, ValueError) as e:
                self.out(f"! {e}")
        elif cmd == "get" and args:
            if mid := self._msg(args[0]):
                m = mesh.storage.get_message(mid)
                if m and m.kind == "file":
                    mesh.download(bytes.fromhex(m.meta["f"]))
        elif cmd == "scan":
            self.out("  scanning…")
            for r in sorted(
                self.node.scan_now(6), key=lambda r: (not r.muninn, not r.probeable)
            ):
                tag = "★ muninn" if r.muninn else ("? maybe " if r.probeable else "  ")
                self.out(f"  {tag:9} {r.addr}  {r.name}  ({r.kind})")
        elif cmd == "sticker":
            from pathlib import Path

            dirs = [
                Path(mesh.storage.path).parent / "stickers",
                Path(emoji.__file__).parent / "assets" / "stickers",
            ]
            files = {
                p.stem: p for d in dirs if d.is_dir() for p in sorted(d.glob("*.gif"))
            }
            if not args:
                self.out("  stickers: " + " ".join(sorted(files)))
            elif args[0] in files and self._need_conv():
                mesh.send_file(self.conv, str(files[args[0]]), "image/gif")
            else:
                self.out(f"! no sticker {args[0]} — /sticker to list")
        elif cmd == "search" and args:
            for m in mesh.storage.search(" ".join(args), 30):
                who = "you" if m.sender == mesh.node_id else mesh.display_name(m.sender)
                t = time.strftime("%d %b %H:%M", time.localtime(m.ts))
                self.out(
                    f"  {self._conv_name(m.conv)} · {t} · {who}: {(m.text or m.meta.get('n', ''))[:80]}"
                )
        elif cmd in ("clear", "forget", "block"):
            if cmd == "clear" and not self._need_conv():
                return True
            if cmd != "clear" and not args:
                self.out(f"! usage: /{cmd} <peer> [...]")
                return True
            if not self._confirm(line):
                return True
            if cmd == "clear":
                name = self._conv_name(self.conv)
                mesh.delete_conversation(self.conv)
                self.out(f"  deleted {name}")
                self.conv = ""
            else:
                for nid in self._peers_of(args) or []:
                    name = mesh.display_name(nid)
                    self.node.forget(nid, block=cmd == "block")
                    self.out(f"  {'blocked' if cmd == 'block' else 'forgot'} {name}")
                    if self.conv == "dm:" + nid:
                        self.conv = ""
        elif cmd == "scores":
            from muninn.gamehost import GameRegistry

            board = GameRegistry(self.node).scores(self.conv or None)
            me = mesh.node_id
            for g in board:
                wins = " · ".join(
                    f"{'you' if n == me else mesh.display_name(n)} {w}"
                    for n, w in sorted(g["wins"].items(), key=lambda kv: -kv[1])
                )
                draws = f" · {g['draws']} draws" if g["draws"] else ""
                self.out(
                    f"  {g['icon']} {g['name']}: {wins or 'no winner yet'}{draws} ({g['played']} played)"
                )
            if not board:
                self.out("  no finished games yet")
        elif cmd == "unblock" and args:
            hit = [
                b
                for b in mesh.blocked
                if b == args[0].upper() or b.endswith(args[0].upper())
            ]
            for b in hit:
                mesh.unblock(b)
                self.out(f"  unblocked {b}")
            if not hit:
                self.out("! not blocked: " + args[0] + " (see /blocked)")
        elif cmd == "blocked":
            self.out("\n".join(f"  {b}" for b in sorted(mesh.blocked)) or "  nobody")
        elif cmd in ("mute", "unmute") and self._need_conv():
            mesh.set_muted(self.conv, cmd == "mute")
            self.out("  muted" if cmd == "mute" else "  unmuted")
        elif cmd == "members":
            gid = conv_group_id(self.conv)
            g = mesh.groups.get(gid or b"")
            if g is None:
                self.out("! not in a group conversation")
            else:
                for nid in sorted(
                    g.members, key=lambda n: mesh.display_name(n).lower()
                ):
                    who = "you" if nid == mesh.node_id else mesh.display_name(nid)
                    where = (
                        "" if nid == mesh.node_id else self.node.presence(nid)["text"]
                    )
                    self.out(f"  {who:20} {where}")
        elif cmd == "find":
            if args and args[0] == "off":
                self.node.stop_finding()
                self.out("  stopped looking for new people")
            else:
                mins = (
                    float(args[0]) if args and args[0].replace(".", "").isdigit() else 5
                )
                self.node.find_people(mins * 60)
                self.out(
                    f"  looking for new people for {mins:g} min (scans + polite probes)"
                )
        elif cmd == "reconnect":
            if args:
                try:
                    m = self.node.set_reconnect_mode(args[0].lower())
                    self.out(f"  reconnect: {m.label} — {m.description}")
                except ValueError as e:
                    self.out(f"! {e}")
            else:
                self.node.poke()
                self.out("  retrying everyone you know")
        elif cmd == "scanmode":
            self.out(
                "  scan modes are gone: /find looks for new people for a while,"
                " /reconnect fast|saver sets how eagerly known people are redialled"
            )
        elif cmd == "whoami":
            st = self.node.status()
            self.out(f"  {mesh.name or '(no name: /nick <name>)'}  id {mesh.node_id}")
            self.out(
                f"  radio {self.node.transport.local_addr()} · {len(mesh.connected())} connected"
                f" · {st['sighted']} devices seen · reconnect {self.node.reconnect.name}"
                + (f" · finding ({st['find_left']}s left)" if st["finding"] else "")
            )
        elif cmd == "connect" and args:
            threading.Thread(target=self._connect, args=(args[0],), daemon=True).start()
        elif cmd == "status":
            st = mesh.status()
            self.out(f"  me {st['name']} {st['node']}  outbox={st['outbox']}")
            for lk in st["links"]:
                self.out(
                    f"  link {mesh.display_name(lk['node'])} via {lk['addr']} "
                    f"rtt={lk['rtt_ms']}ms idle={lk['idle']}s"
                )
            for d, vias in st["routes"].items():
                self.out(
                    f"  route {mesh.display_name(d)}: "
                    + ", ".join(
                        f"via {mesh.display_name(v)} ({h})" for v, h in vias.items()
                    )
                )
        elif cmd == "emoji":
            res = emoji.search(args[0] if args else "")
            self.out("  " + "  ".join(f"{e} :{c}:" for c, e in res))
        else:
            self.out("! unknown command or missing arguments — /help")
        return True

    def _connect(self, addr: str) -> None:
        try:
            nid = self.node.connect_to(addr)
            self.out(f"  connected to {self.mesh.display_name(nid) if nid else addr}")
        except Exception as e:
            self.out(f"! connect failed: {e}")

    def switch(self, conv: str) -> None:
        self.conv = conv
        self.out(f"── {self._conv_name(conv)} ──")
        for m in self.mesh.storage.load_history(conv, 20):
            self.out(self._fmt(m))
        self.mesh.mark_read(conv)

    def run(self) -> None:
        self._setup_completer()
        while True:
            try:
                line = input(self._prompt())
            except EOFError:
                break
            if not self.handle(line):
                break

    def _setup_completer(self) -> None:
        if not _HAS_READLINE:
            return

        def complete(text, state):
            buf = readline.get_line_buffer().lstrip()
            if buf.startswith(("/dm ", "/new ", "/nick ", "/add ", "/kick ")):
                opts = [
                    p.display
                    for p in self.mesh.active_peers()
                    if p.display.lower().startswith(text.lower())
                ]
            elif buf.startswith("/group "):
                opts = [
                    g.name for g in self.mesh.groups.values() if g.name.startswith(text)
                ]
            elif buf.startswith("/"):
                opts = [c for c in COMMANDS if c.startswith(buf)]
            elif text.startswith(":"):
                opts = [f":{c}:" for c, _ in emoji.search(text)]
            else:
                opts = []
            return opts[state] if state < len(opts) else None

        readline.set_completer(complete)
        readline.parse_and_bind("tab: complete")
        readline.set_completer_delims(" ")


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def main() -> None:
    plain = "--plain" in sys.argv[1:]
    if not plain and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            from muninn import tui
        except ImportError:  # no curses (e.g. Windows without windows-curses)
            pass
        else:
            return tui.main()
    name = os.environ.get("MUNINN_NAME") or None
    node = Node(default_transport(), name=name)
    if not node.mesh.name and sys.stdin.isatty():
        try:
            chosen = input("Welcome to Muninn! What should people call you? ").strip()
        except EOFError:
            chosen = ""
        if chosen:
            node.mesh.set_name(chosen)
    print(
        f"Muninn — you are {node.mesh.name or '(no name: /nick <name>)'} [{node.node_id}]"
    )
    print("Looking for peers… /help for commands")
    node.start()
    ui = ChatUI(node)
    try:
        ui.run()
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
    print("\nBye.")


if __name__ == "__main__":
    main()
