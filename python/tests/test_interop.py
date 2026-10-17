"""Python ↔ Kotlin (JVM build of the Android core) interop over the sim hub.

Requires `./gradlew :core:installDist` (android/core/build/install/core/bin/core).
"""

import hashlib
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest

from muninn.mesh import dm_conv, group_conv
from muninn.sim.lab import free_port, wait_until

JVM = Path(__file__).resolve().parents[2] / "android/core/build/install/core/bin/core"
pytestmark = pytest.mark.skipif(not JVM.exists(), reason="JVM core node not built")


class JvmNode:
    def __init__(self, lab, name, pos, echo=False):
        self.port = free_port()
        args = [
            str(JVM),
            "--hub",
            lab.addr,
            "--name",
            name,
            "--pos",
            f"{pos[0]},{pos[1]}",
            "--control",
            str(self.port),
            "--data",
            str(lab.tmp / f"jvm-{name}"),
        ]
        if echo:
            args.append("--echo")
        self.proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        self.sock = None
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.port))
                break
            except OSError:
                time.sleep(0.2)
        assert self.sock, "jvm node control port"
        self.rf = self.sock.makefile("r")
        self.node_id = self("status")["node"]

    def __call__(self, cmd, **kw):
        self.sock.sendall((json.dumps({"cmd": cmd, **kw}) + "\n").encode())
        res = json.loads(self.rf.readline())
        if not res["ok"]:
            raise RuntimeError(res["error"])
        return res["result"]

    def texts(self, conv):
        return [
            m["text"] for m in self("history", conv=conv, n=1000) if m["kind"] == "text"
        ]

    def reachable(self, node_id):
        return node_id in self("status")["reachable"]

    def stop(self):
        self.proc.kill()
        self.proc.wait()


@pytest.fixture
def jvm(lab):
    nodes = []

    def make(name, pos, **kw):
        n = JvmNode(lab, name, pos, **kw)
        nodes.append(n)
        return n

    yield make
    for n in nodes:
        n.stop()


def py_texts(node, conv):
    return [m.text for m in node.storage.load_history(conv, 1000) if m.kind == "text"]


def test_dm_both_ways(lab, jvm):
    a = lab.node("py", (0, 0))
    k = jvm("kt", (1, 0))
    wait_until(lambda: k.node_id in a.mesh.connected(), 30)
    mid = a.mesh.send_text(dm_conv(k.node_id), "hi kotlin 🐍→☕")
    wait_until(lambda: "hi kotlin 🐍→☕" in k.texts(dm_conv(a.node_id)), 15)
    wait_until(
        lambda: a.storage.get_message(mid).state == "read", 15
    )  # kt bot marks read
    k("send", to=a.node_id, text="hi python")
    wait_until(lambda: "hi python" in py_texts(a, dm_conv(k.node_id)), 15)
    assert a.mesh.display_name(k.node_id) == "kt"


def test_relay_through_kotlin(lab, jvm):
    a = lab.node("a", (0, 0))
    jvm("relay", (5, 0))
    c = lab.node("c", (10, 0))
    wait_until(lambda: a.mesh.reachable(c.node_id), 40)
    for i in range(10):
        a.mesh.send_text(dm_conv(c.node_id), f"r{i}")
    wait_until(lambda: len(py_texts(c, dm_conv(a.node_id))) == 10, 40)


def test_relay_through_python_between_kotlin(lab, jvm):
    k1 = jvm("k1", (0, 0))
    lab.node("mid", (5, 0))
    k2 = jvm("k2", (10, 0))
    wait_until(lambda: k1.reachable(k2.node_id), 60)
    for i in range(10):
        k1("send", to=k2.node_id, text=f"k{i}")
    wait_until(lambda: len(k2.texts(dm_conv(k1.node_id))) == 10, 40)
    states = [m["state"] for m in k1("history", conv=dm_conv(k2.node_id))]
    wait_until(
        lambda: all(
            m["state"] != "sent" for m in k1("history", conv=dm_conv(k2.node_id))
        ),
        30,
    )
    assert states


def test_mixed_group(lab, jvm):
    a = lab.node("a", (0, 0))
    k = jvm("k", (1, 0))
    c = lab.node("c", (2, 0))
    wait_until(
        lambda: (
            a.mesh.reachable(k.node_id)
            and a.mesh.reachable(c.node_id)
            and k.reachable(c.node_id)
        ),
        40,
    )
    gid = a.mesh.create_group("mix", [k.node_id, c.node_id])
    wait_until(lambda: "mix" in k("status")["groups"].values(), 20)
    k("gsend", group="mix", text="kotlin in the group")
    wait_until(lambda: "kotlin in the group" in py_texts(c, group_conv(gid)), 20)
    a.mesh.send_text(group_conv(gid), "python too")
    wait_until(lambda: "python too" in k.texts(group_conv(gid)), 20)


def test_files_both_ways_with_flaps(lab, jvm):
    a = lab.node("a", (0, 0))
    k = jvm("k", (5, 0))
    c = lab.node("c", (10, 0))
    wait_until(lambda: a.mesh.reachable(c.node_id), 40)
    data = os.urandom(300_000)
    p = lab.tmp / "f.bin"
    p.write_bytes(data)
    # python -> kotlin (direct-ish) and python -> python via kotlin relay
    a.mesh.send_file(dm_conv(k.node_id), str(p))
    a.mesh.send_file(dm_conv(c.node_id), str(p))
    want = hashlib.sha256(data).hexdigest()
    wait_until(
        lambda: any(
            f["state"] == "complete" and f["sha256"] == want for f in k("files")
        ),
        120,
    )
    wait_until(lambda: any(f.state == "complete" for f in c.mesh.files.values()), 120)
    # kotlin -> python
    kp = lab.tmp / "k.bin"
    kp.write_bytes(data[:100_000])
    k("file", to=a.node_id, path=str(kp))
    wait_until(
        lambda: any(
            f.state == "complete" and f.size == 100_000 for f in a.mesh.files.values()
        ),
        120,
    )


def test_flapping_kotlin_relay(lab, jvm):
    a = lab.node("a", (0, 0))
    k = jvm("k", (5, 0))
    c = lab.node("c", (10, 0))
    wait_until(lambda: a.mesh.reachable(c.node_id), 40)
    kaddr = next(d["addr"] for d in lab.admin("state")["devices"] if d["name"] == "k")
    lab.admin("flap", a=lab.addr_of("c"), b=kaddr, up=2.0, down=2.5)
    ids = [a.mesh.send_text(dm_conv(c.node_id), f"f{i}") for i in range(20)]
    time.sleep(10)
    lab.admin("flap", a=lab.addr_of("c"), b=kaddr)
    wait_until(lambda: len(py_texts(c, dm_conv(a.node_id))) == 20, 120)
    wait_until(lambda: all(a.storage.get_message(i).state != "sent" for i in ids), 60)
    assert k.node_id


def test_mixed_cabin_soak(lab, jvm):
    """Python and Kotlin seats interleaved in a line; a group spanning both;
    random DMs, moves and flapping links for 40 s; all delivered once."""
    import random

    rng = random.Random(9)
    py = {
        "p0": lab.node("p0", (0, 0)),
        "p2": lab.node("p2", (8, 0)),
        "p4": lab.node("p4", (16, 0)),
    }
    kt = {"k1": jvm("k1", (4, 0)), "k3": jvm("k3", (12, 0)), "k5": jvm("k5", (20, 0))}
    ids = {
        **{n: v.node_id for n, v in py.items()},
        **{n: v.node_id for n, v in kt.items()},
    }
    wait_until(
        lambda: all(py["p0"].mesh.reachable(ids[n]) for n in ids if n != "p0"),
        120,
        msg="mixed chain converged",
    )
    gid = py["p0"].mesh.create_group("mixed", [ids[n] for n in ids if n != "p0"])
    wait_until(
        lambda: all("mixed" in kt[k]("status")["groups"].values() for k in kt), 60
    )
    sent = []
    addr = {d["name"]: d["addr"] for d in lab.admin("state")["devices"]}
    stop = time.time() + 40
    i = 0
    while time.time() < stop:
        x, y = rng.sample(list(ids), 2)
        r = rng.random()
        t = f"{x}>{y}#{i}"
        if r < 0.4:
            if x in py:
                py[x].mesh.send_text(dm_conv(ids[y]), t)
            else:
                kt[x]("send", to=ids[y], text=t)
            sent.append(("dm", x, y, t))
        elif r < 0.6:
            if x in py:
                py[x].mesh.send_text(group_conv(gid), t)
            else:
                kt[x]("gsend", group="mixed", text=t)
            sent.append(("g", x, None, t))
        elif r < 0.8:
            lab.admin(
                "flap",
                a=addr[x],
                b=addr[y],
                up=rng.uniform(1, 3),
                down=rng.uniform(0.5, 2),
            )
        else:
            lab.admin("kill", addr=addr[x])
        i += 1
        time.sleep(0.3)
    lab.admin("reset")

    def texts(n, conv):
        if n in py:
            return py_texts(py[n], conv)
        return kt[n].texts(conv)

    for kind, x, y, t in sent:
        if kind == "dm":
            wait_until(lambda y=y, x=x, t=t: t in texts(y, dm_conv(ids[x])), 180, msg=t)
        else:
            for n in ids:
                if n != x:
                    wait_until(
                        lambda n=n, t=t: t in texts(n, group_conv(gid)),
                        180,
                        msg=f"{t}@{n}",
                    )
    for n in ids:
        g = texts(n, group_conv(gid))
        assert len(g) == len(set(g)), f"group duplicates at {n}"


def test_tap_to_add_stranger_to_room(lab, jvm):
    """NFC tap from a room (Android): the reader learns a stranger's id + key,
    adds them to the room while no radio link exists yet; once they come into
    range they get the room and its messages."""
    b = lab.node("member", (0, 0))
    k = jvm("reader", (1, 0))
    c = lab.node("stranger", (60, 60))  # far away: never seen over the radio
    wait_until(lambda: k.reachable(b.node_id), 30)
    gid = k("group", name="trip", members=[b.node_id])["group"]
    k("introduce", id=c.node_id, pubkey=c.mesh.pubkey.hex(), name="stranger")
    k("gadd", group="trip", members=[c.node_id])
    k("gsend", group="trip", text="welcome aboard")
    time.sleep(2)
    assert bytes.fromhex(gid) not in c.mesh.groups
    lab.move("stranger", (1, 1))
    wait_until(lambda: bytes.fromhex(gid) in c.mesh.groups, 60)
    assert {b.node_id, k.node_id, c.node_id} <= set(
        c.mesh.groups[bytes.fromhex(gid)].members
    )
    c.mesh.send_text(group_conv(bytes.fromhex(gid)), "thanks!")
    wait_until(lambda: "thanks!" in py_texts(b, group_conv(bytes.fromhex(gid))), 30)
