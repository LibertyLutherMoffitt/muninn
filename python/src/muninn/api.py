"""Local JSON API for other front ends (the Bubble Tea client in tea/).

    muninn --api        (same data dir / env as the terminal client)

Newline-delimited JSON on stdin/stdout — no sockets, works the same on
Linux and Windows, and the core lives exactly as long as the front end that
started it. Everything else (logs, library chatter) goes to
<data dir>/muninn.log.

Requests:  {"req": 1, "cmd": "send", "conv": "dm:…", "text": "hi"}
Replies:   {"req": 1, "ok": true, "result": …}  /  {"req": 1, "ok": false, "error": "…"}
Events:    {"ev": "message"|"update"|"peers"|"typing"|"file"|"link"|"notice", …}

Front ends refetch on events (`convs`, `history`) — the core stays the
single source of truth. `command` runs any /command of the line-mode client
(cli.py) and returns its output, so every feature is reachable.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
import time

from muninn import __version__, emoji
from muninn.cli import ChatUI
from muninn.mesh import conv_group_id, dm_conv
from muninn.node import Node, default_transport
from muninn.storage import StoredMessage


class _Capture(ChatUI):
    """The CLI's command handler, printing into a buffer instead of a tty.
    Its live printouts (✉ new message…) are left to the front end."""

    def __init__(self, node: Node, notice):
        self._buf: list[str] | None = None
        self._notice = notice
        super().__init__(node)

    def out(self, line: str) -> None:
        if self._buf is not None:
            self._buf.append(line)
        else:
            self._notice(line.strip())

    def _on_message(self, m) -> None:  # the front end shows messages itself
        pass

    def _on_update(self, msg_id) -> None:
        pass

    def _on_file(self, file_id) -> None:
        pass

    def _on_peers(self) -> None:
        pass

    def run_line(self, line: str, conv: str) -> dict:
        self.conv = conv
        self._buf = []
        try:
            alive = self.handle(line)
        finally:
            lines, self._buf = self._buf, None
        return {"lines": lines, "conv": self.conv, "quit": not alive}


class Api:
    def __init__(self, node: Node, out):
        self.node = node
        self.mesh = node.mesh
        self._out = out
        self._wlock = threading.Lock()
        self.ui = _Capture(node, lambda s: self.event("notice", text=s))
        m = self.mesh
        m.on("message", lambda msg: self.event("message", conv=msg.conv))
        m.on("update", lambda mid: self.event("update"))
        m.on("peers", lambda: self.event("peers"))
        m.on("group", lambda gid: self.event("peers"))
        m.on(
            "typing",
            lambda conv, who: self.event("typing", conv=conv, name=m.display_name(who)),
        )
        m.on("link", lambda what, d: self.event("link", what=what, detail=str(d)))
        m.on("devices", lambda: self.event("peers"))
        self._file_ts = 0.0
        m.on("file", self._on_file)

    # --- wire -----------------------------------------------------------------

    def write(self, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False, default=str)
        with self._wlock:
            self._out.write(line + "\n")
            self._out.flush()

    def event(self, ev: str, **kw) -> None:
        self.write({"ev": ev, **kw})

    def _on_file(self, file_id) -> None:
        now = time.monotonic()  # progress can be chatty: ≤ 4 updates/s
        if now - self._file_ts > 0.25:
            self._file_ts = now
            self.event("update")

    def serve(self, inp) -> None:
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = req.get("req")
            try:
                fn = getattr(self, "cmd_" + str(req.get("cmd")), None)
                if fn is None:
                    raise ValueError(f"unknown cmd {req.get('cmd')!r}")
                res = fn(req)
                self.write({"req": rid, "ok": True, "result": res})
                if req.get("cmd") == "quit":
                    return
            except Exception as e:
                self.write({"req": rid, "ok": False, "error": str(e) or repr(e)})

    # --- views ------------------------------------------------------------------

    def _title(self, conv: str) -> str:
        if conv.startswith("dm:"):
            return self.mesh.display_name(conv[3:])
        g = self.mesh.groups.get(conv_group_id(conv) or b"")
        return g.name if g else "room"

    def _subtitle(self, conv: str) -> str:
        m = self.mesh
        typing = [m.display_name(x) for x in m.typing_in(conv)]
        if typing:
            return ", ".join(typing) + (
                " is typing…" if len(typing) == 1 else " are typing…"
            )
        if conv.startswith("dm:"):
            who = conv[3:]
            pend = m.pending_count(who)
            return self.node.presence(who)["text"] + (
                f" · {pend} waiting to send" if pend else ""
            )
        g = m.groups.get(conv_group_id(conv) or b"")
        if g is None:
            return ""
        others = [x for x in g.members if x != m.me]
        here = sum(1 for x in others if m.person_reachable(x))
        names = ", ".join(sorted(m.display_name(x) for x in others))
        return f"{here}/{len(others)} in range · {names}" + (
            " · you left" if g.left else ""
        )

    def _preview(self, msg: StoredMessage | None) -> str:
        if msg is None:
            return ""
        if msg.deleted:
            return "message deleted"
        who = "You: " if msg.sender == self.mesh.me and msg.kind != "system" else ""
        if msg.kind == "file":
            return who + "📎 " + (msg.text or msg.meta.get("n", "file"))
        return who + msg.text.replace("\n", " ")

    def cmd_hello(self, req) -> dict:
        m = self.mesh
        return {
            "version": __version__,
            "name": m.name,
            "me": m.me,
            "node": m.node_id,
            "devices": len(m.devices_of(m.me)),
            "sim": bool(os.environ.get("MUNINN_SIM")),
        }

    def cmd_convs(self, req) -> list:
        m = self.mesh
        last = m.storage.last_messages()
        unread = m.storage.unread_counts()
        out = []
        for p in m.active_peers():
            conv = dm_conv(p.node_id)
            pres = self.node.presence(p.node_id)
            msg = last.get(conv)
            if msg is None and pres["state"] == "offline" and not unread.get(conv):
                continue  # known, never talked to, not around
            out.append(
                {
                    "conv": conv,
                    "title": m.display_name(p.node_id),
                    "group": False,
                    "preview": self._preview(msg),
                    "ts": msg.ts if msg else p.last_seen,
                    "unread": unread.get(conv, 0),
                    "status": pres["state"],
                    "subtitle": pres["text"],
                    "typing": bool(m.typing_in(conv)),
                    "pending": m.pending_count(p.node_id),
                    "muted": m.is_muted(conv),
                }
            )
        with m.lock:
            groups = list(m.groups.values())
        for g in groups:
            conv = g.conv
            msg = last.get(conv)
            others = [x for x in g.members if x != m.me]
            here = sum(1 for x in others if m.person_reachable(x))
            out.append(
                {
                    "conv": conv,
                    "title": g.name + (" (left)" if g.left else ""),
                    "group": True,
                    "preview": self._preview(msg),
                    "ts": msg.ts if msg else 0,
                    "unread": unread.get(conv, 0),
                    "status": "group",
                    "subtitle": f"{here}/{len(others)} in range",
                    "typing": bool(m.typing_in(conv)),
                    "pending": 0,
                    "muted": m.is_muted(conv),
                }
            )
        out.sort(key=lambda c: -c["ts"])
        return out

    def cmd_people(self, req) -> list:
        m = self.mesh
        return sorted(
            (
                {
                    "id": p.node_id,
                    "name": m.display_name(p.node_id),
                    "status": self.node.presence(p.node_id)["state"],
                    "subtitle": self.node.presence(p.node_id)["text"],
                }
                for p in m.active_peers()
            ),
            key=lambda x: x["name"].lower(),
        )

    def _msg(self, x: StoredMessage) -> dict:
        m = self.mesh
        mine = x.sender == m.me
        d = {
            "id": x.msg_id.hex(),
            "sender": x.sender,
            "name": "You" if mine else m.display_name(x.sender),
            "mine": mine,
            "ts": x.ts,
            "kind": x.kind,
            "text": x.text,
            "edited": x.edited,
            "deleted": x.deleted,
            "state": x.state if mine else "",
            "reactions": [
                {
                    "emoji": e,
                    "count": len(who),
                    "mine": m.me in who,
                    "names": [m.display_name(w) for w in who],
                }
                for e, who in x.reactions.items()
            ],
        }
        if x.reply_to:
            o = m.storage.get_message(x.reply_to)
            if o:
                d["reply"] = {
                    "id": o.msg_id.hex(),
                    "name": "You" if o.sender == m.me else m.display_name(o.sender),
                    "text": o.text
                    if o.kind != "file"
                    else "📎 " + o.meta.get("n", "file"),
                }
        if x.kind == "file":
            fid = x.meta.get("f", "")
            have, total, state = (
                m.file_progress(bytes.fromhex(fid)) if fid else (0, 0, "")
            )
            rec = m.files.get(bytes.fromhex(fid)) if fid else None
            d["file"] = {
                "id": fid,
                "name": x.meta.get("n", "file"),
                "size": x.meta.get("s", 0),
                "mime": x.meta.get("mt", ""),
                "state": state,
                "progress": have / total if total else 0,
                "path": rec.path if rec and state == "complete" else "",
            }
        return d

    def cmd_history(self, req) -> list:
        n = int(req.get("n", 200))
        return [self._msg(x) for x in self.mesh.storage.load_history(req["conv"], n)]

    def cmd_header(self, req) -> dict:
        conv = req["conv"]
        return {"title": self._title(conv), "subtitle": self._subtitle(conv)}

    # --- actions ----------------------------------------------------------------

    def cmd_send(self, req) -> str:
        reply = bytes.fromhex(req["reply"]) if req.get("reply") else None
        return self.mesh.send_text(req["conv"], emoji.expand(req["text"]), reply).hex()

    def cmd_read(self, req) -> None:
        self.mesh.mark_read(req["conv"])

    def cmd_typing(self, req) -> None:
        self.mesh.send_typing(req["conv"])

    def cmd_react(self, req) -> None:
        mid = bytes.fromhex(req["id"])
        e = emoji.expand(req["emoji"])
        msg = self.mesh.storage.get_message(mid)
        remove = bool(msg and self.mesh.me in msg.reactions.get(e, []))
        self.mesh.react(req["conv"], mid, e, remove=remove)

    def cmd_edit(self, req) -> None:
        self.mesh.edit(req["conv"], bytes.fromhex(req["id"]), emoji.expand(req["text"]))

    def cmd_delete(self, req) -> None:
        self.mesh.delete(req["conv"], bytes.fromhex(req["id"]))

    def cmd_file(self, req) -> str:
        from muninn import media

        path = os.path.expanduser(req["path"])
        if not os.path.isfile(path):
            raise ValueError(f"no such file: {path}")
        return media.send_file(
            self.mesh, req["conv"], path, caption=req.get("caption", "")
        ).hex()

    def cmd_download(self, req) -> None:
        self.mesh.download(bytes.fromhex(req["file"]))

    def cmd_dm(self, req) -> str:
        p = self.mesh.resolve(req["who"])
        if p is None:
            raise ValueError(f"unknown person {req['who']!r}")
        return dm_conv(p)

    def cmd_command(self, req) -> dict:
        return self.ui.run_line(str(req["line"]), str(req.get("conv") or ""))

    def cmd_emoji(self, req) -> list:
        return [
            {"code": c, "emoji": e} for c, e in emoji.search(str(req.get("q", "")), 40)
        ]

    def cmd_quit(self, req) -> None:
        return None


def main() -> None:
    """`muninn --api`: serve the JSON API on stdio until stdin closes."""
    from muninn.instance import AlreadyRunning

    proto_out = io.TextIOWrapper(
        os.fdopen(os.dup(1), "wb"), encoding="utf-8", line_buffering=True
    )
    proto_in = io.TextIOWrapper(os.fdopen(os.dup(0), "rb"), encoding="utf-8")
    try:
        node = Node(
            default_transport(),
            name=os.environ.get("MUNINN_NAME") or None,
            log=logging.getLogger("muninn").info,
        )
    except AlreadyRunning as e:
        proto_out.write(json.dumps({"ev": "fatal", "text": str(e)}) + "\n")
        proto_out.flush()
        return
    # nothing but the protocol may reach stdout (C-level chatter included)
    logf = open(
        node.storage.path.parent / "muninn.log", "a", buffering=1, encoding="utf-8"
    )
    logging.basicConfig(
        stream=logf,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=True,
    )
    sys.stdout = sys.stderr = logf
    for fd in (1, 2):
        try:
            os.dup2(logf.fileno(), fd)
        except OSError:
            pass
    api = Api(node, proto_out)
    try:
        node.start()
    except Exception as e:
        api.event("fatal", text=f"Could not start Bluetooth: {e}")
        node.stop()
        return
    api.event("ready", **api.cmd_hello({}))
    try:
        api.serve(proto_in)
    finally:
        node.stop()
