"""muninn-bot — a headless, scriptable Muninn node.

Used to exercise GUIs, Android emulators and real devices:

    MUNINN_SIM=127.0.0.1:7420 muninn-bot --name bob --echo --play
    muninn-bot --name bob --control 7501     # JSON-lines control port

Behaviours:
  --echo      reply "echo: <text>" to every DM / group message
  --react     react 👍 to every message
  --play      join every game invite and play random legal moves
              (game plugins run in QJSEngine; plugin must implement moves())
  --say N     send a message every N seconds to everyone reachable

Control port commands (one JSON object per line, reply is one JSON line):
  {"cmd":"status"}  {"cmd":"send","to":"name|id","text":"…"}
  {"cmd":"group","name":"g","members":["a","b"]}  {"cmd":"gsend","group":"g","text":"…"}
  {"cmd":"linkcode"}  {"cmd":"link","to":"name|id","code":"123456"}  {"cmd":"unlink","device":"id"}
  {"cmd":"history","conv":"dm:ID"}  {"cmd":"file","to":"name","path":"/x"}
  {"cmd":"game","to":"name","game":"tictactoe"}  {"cmd":"name","name":"new"}
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socketserver
import threading
import time

from muninn.gamehost import GameRegistry
from muninn.mesh import dm_conv
from muninn.node import Node, default_transport


class GameBot:
    """Plays games by evaluating plugin JS in a QJSEngine (no GUI needed)."""

    def __init__(self, registry: GameRegistry):
        import queue

        self.reg = registry
        self.cache: dict[str, object] = {}
        self.q: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, session: str) -> None:
        self.q.put(session)

    def _worker(self) -> None:
        # QJSEngine is thread-affine: create and use it only on this thread.
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtQml import QJSEngine

        if QCoreApplication.instance() is None:
            self._app = QCoreApplication([])
        self.engine = QJSEngine()
        while True:
            session = self.q.get()
            try:
                self.maybe_move(session)
            except Exception as e:
                print(f"[bot] game error: {e!r}", flush=True)

    def _game(self, session: str):
        code = self.reg.code_for(session)
        if session not in self.cache:
            self.cache[session] = self.engine.evaluate(
                "(function(){" + code + "\n;return game;})()"
            )
        return self.cache[session]

    def _js(self, value):
        return self.engine.toScriptValue(value)

    def maybe_move(self, session: str) -> None:
        with self.lock:
            info = self.reg.session_info(session)
            me = info.get("me")
            if not info or info.get("ended"):
                return
            if info.get("invitedMe"):
                self.reg.act(session, "join", {})
                return
            if not info.get("started") or me not in info.get("players", []):
                return
            g = self._game(session)
            state = g.property("setup").callWithInstance(
                g,
                [
                    self._js(info["players"]),
                    self._js(info["seed"]),
                    self._js(info.get("opts") or {}),
                ],
            )
            turn_fn = g.property("turn")
            move_fn = g.property("move")
            for mv in info["moves"]:
                t = turn_fn.callWithInstance(g, [state]).toVariant()
                if t is not None and t != mv["sender"]:
                    continue
                nxt = move_fn.callWithInstance(
                    g, [state, self._js(mv["sender"]), self._js(mv["m"])]
                )
                if not nxt.isError():
                    state = nxt
            t = turn_fn.callWithInstance(g, [state]).toVariant()
            if t is not None and t != me:
                return
            moves_fn = g.property("moves")
            if not moves_fn.isCallable():
                return
            legal = (
                moves_fn.callWithInstance(g, [state, self._js(me)]).toVariant() or []
            )
            if not legal:
                return
            time.sleep(random.uniform(0.3, 1.0))
            mv = random.choice(legal)
            self.reg.act(session, "move", mv)
            # the player whose move ends the game records the result
            after = move_fn.callWithInstance(g, [state, self._js(me), self._js(mv)])
            status_fn = g.property("status")
            if (
                not after.isError()
                and status_fn.isCallable()
                and not info.get("result")
            ):
                st = (
                    status_fn.callWithInstance(
                        g, [after, self._js(info.get("names") or {})]
                    ).toVariant()
                    or {}
                )
                if st.get("over"):
                    self.reg.act(
                        session,
                        "result",
                        {
                            "winners": st.get("winners") or [],
                            "text": st.get("text", ""),
                        },
                    )


def main() -> None:
    ap = argparse.ArgumentParser(description="headless Muninn node")
    ap.add_argument("--name", default=os.environ.get("MUNINN_NAME", "bot"))
    ap.add_argument("--data", help="data dir (default: per-name temp dir)")
    ap.add_argument("--pos", help="sim seat position row,seat")
    ap.add_argument("--echo", action="store_true")
    ap.add_argument("--react", action="store_true")
    ap.add_argument("--play", action="store_true")
    ap.add_argument("--say", type=float, default=0)
    ap.add_argument("--control", type=int, help="JSON control port on 127.0.0.1")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    data = a.data or os.path.join(
        os.environ.get("TMPDIR", "/tmp"), f"muninn-bot-{a.name}"
    )
    os.makedirs(data, exist_ok=True)
    transport = default_transport()
    from muninn.sim.transport import SimTransport

    if isinstance(transport, SimTransport):
        transport.name = a.name
        if a.pos:
            r, s = (float(x) for x in a.pos.split(","))
            transport.pos = (r, s)
    log = print if a.verbose else None
    node = Node(
        transport, db_path=os.path.join(data, "muninn.db"), name=a.name, log=log
    )
    mesh = node.mesh
    reg = GameRegistry(node)
    gamebot = GameBot(reg) if a.play else None

    def on_message(m):
        if m.sender == mesh.me or m.kind not in ("text", "file"):
            return
        print(
            f"[{a.name}] {mesh.display_name(m.sender)} @ {m.conv}: {m.text}", flush=True
        )
        mesh.mark_read(m.conv)
        if a.react:
            mesh.react(m.conv, m.msg_id, "👍")
        if a.echo and m.text and not m.text.startswith("echo:"):
            mesh.send_text(m.conv, f"echo: {m.text}", reply_to=m.msg_id)

    mesh.on("message", on_message)
    if gamebot:
        mesh.on("app", lambda ev: gamebot.submit(ev.session))
    mesh.on("peers", lambda: None)
    node.start()
    print(f"[{a.name}] up as {mesh.node_id} on {transport.local_addr()}", flush=True)

    if a.control:
        start_control(node, reg, a.control)

    try:
        while True:
            time.sleep(a.say or 3600)
            if a.say:
                for nid in list(mesh.peers):
                    if mesh.reachable(nid):
                        mesh.send_text(
                            dm_conv(nid),
                            f"ping from {a.name} at {time.strftime('%H:%M:%S')}",
                        )
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()


def start_control(node: Node, reg: GameRegistry, port: int) -> None:
    mesh = node.mesh

    def peer(s: str) -> str:
        r = mesh.resolve(s)
        if r is None:
            raise ValueError(f"unknown peer {s}")
        return r

    def group(name: str):
        for g in mesh.groups.values():
            if g.name == name:
                return g
        raise ValueError(f"unknown group {name}")

    def run(c: dict):
        cmd = c.get("cmd")
        if cmd == "status":
            st = mesh.status()
            st["peers"] = {n: p.display for n, p in mesh.peers.items()}
            st["groups"] = {g.group_id.hex(): g.name for g in mesh.groups.values()}
            return st
        if cmd == "send":
            return {"msg_id": mesh.send_text(dm_conv(peer(c["to"])), c["text"]).hex()}
        if cmd == "group":
            gid = mesh.create_group(c["name"], [peer(x) for x in c["members"]])
            return {"group": gid.hex()}
        if cmd == "gsend":
            return {"msg_id": mesh.send_text(group(c["group"]).conv, c["text"]).hex()}
        if cmd == "history":
            return [
                {
                    "id": m.msg_id.hex(),
                    "sender": m.sender,
                    "text": m.text,
                    "kind": m.kind,
                    "state": m.state,
                    "reactions": m.reactions,
                }
                for m in mesh.storage.load_history(c["conv"], int(c.get("n", 50)))
            ]
        if cmd == "file":
            import mimetypes

            mime = mimetypes.guess_type(c["path"])[0] or "application/octet-stream"
            return {
                "msg_id": mesh.send_file(dm_conv(peer(c["to"])), c["path"], mime).hex()
            }
        if cmd == "game":
            return {"session": reg.invite(dm_conv(peer(c["to"])), c["game"])}
        if cmd == "name":
            mesh.set_name(c["name"])
            return {}
        if cmd == "react":  # react to the newest message from someone
            conv = dm_conv(peer(c["to"])) if c.get("to") else group(c["group"]).conv
            last = [
                m for m in mesh.storage.load_history(conv, 50) if m.sender != mesh.me
            ]
            if not last:
                raise ValueError("nothing to react to")
            mesh.react(conv, last[-1].msg_id, c["emoji"])
            return {}
        if cmd == "linkcode":
            return {"code": mesh.link_code()}
        if cmd == "link":
            mesh.link_to(peer(c["to"]), str(c["code"]))
            return {}
        if cmd == "unlink":
            mesh.unlink(c["device"])
            return {}
        raise ValueError(f"unknown cmd {cmd}")

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                try:
                    res = {"ok": True, "result": run(json.loads(line))}
                except Exception as e:
                    res = {"ok": False, "error": repr(e)}
                self.wfile.write((json.dumps(res, default=str) + "\n").encode())

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()


if __name__ == "__main__":
    main()
