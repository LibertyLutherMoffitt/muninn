"""The stdio JSON API used by the Bubble Tea client (tea/)."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from muninn.mesh import dm_conv
from muninn.sim.lab import wait_until

SRC = str(Path(__file__).resolve().parents[1] / "src")


class Core:
    def __init__(self, lab, tmp_path, name="me"):
        env = dict(
            os.environ,
            MUNINN_SIM=lab.addr,
            MUNINN_NAME=name,
            MUNINN_DATA=str(tmp_path / name),
            PYTHONPATH=SRC,
        )
        self.p = subprocess.Popen(
            [sys.executable, "-m", "muninn.cli", "--api"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.n = 0
        self.events = []
        ready = self._read()
        assert ready["ev"] == "ready", ready
        self.hello = ready

    def _read(self):
        line = self.p.stdout.readline()
        assert line, "core exited"
        return json.loads(line)

    def __call__(self, cmd, **kw):
        self.n += 1
        self.p.stdin.write(json.dumps({"req": self.n, "cmd": cmd, **kw}) + "\n")
        while True:
            msg = self._read()
            if "ev" in msg:
                self.events.append(msg)
                continue
            assert msg["req"] == self.n
            if not msg["ok"]:
                raise RuntimeError(msg["error"])
            return msg["result"]

    def close(self):
        try:
            self("quit")
        except Exception:
            pass
        self.p.wait(10)


def test_api_chat_roundtrip(lab, tmp_path):
    bob = lab.node("bob", (1, 0))
    core = Core(lab, tmp_path)
    try:
        assert core.hello["name"] == "me"
        wait_until(lambda: any(c["title"] == "bob" for c in core("convs")), 30)
        conv = core("dm", who="bob")
        core("send", conv=conv, text="hello :airplane:")
        me = core.hello["me"]
        wait_until(
            lambda: any(
                m.text == "hello ✈️" for m in bob.storage.load_history(dm_conv(me))
            ),
            15,
        )
        bid = bob.mesh.send_text(dm_conv(me), "hi back")
        wait_until(
            lambda: any(m["text"] == "hi back" for m in core("history", conv=conv)), 15
        )
        assert any(e["ev"] == "message" for e in core.events)
        c = next(c for c in core("convs") if c["conv"] == conv)
        assert c["unread"] == 1 and c["status"] == "direct"
        core("read", conv=conv)
        assert next(c for c in core("convs") if c["conv"] == conv)["unread"] == 0
        core("react", conv=conv, id=bid.hex(), emoji=":fire:")
        wait_until(lambda: bob.storage.get_message(bid).reactions == {"🔥": [me]}, 15)
        hist = core("history", conv=conv)
        mine = next(m for m in hist if m["text"] == "hello ✈️")
        assert mine["mine"] and mine["state"] in ("delivered", "read")
        # any /command of the line client, output captured
        out = core("command", line="/nick Ann", conv=conv)
        assert out["lines"] and core("hello")["name"] == "Ann"
        out = core("command", line="/new trip bob", conv=conv)
        assert out["conv"].startswith("group:")
        assert core("header", conv=out["conv"])["title"] == "trip"
        assert core("emoji", q="fir")[0]["emoji"] == "🔥"
    finally:
        core.close()
    time.sleep(0.2)
