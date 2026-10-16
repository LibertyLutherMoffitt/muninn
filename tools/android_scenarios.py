#!/usr/bin/env python3
"""End-to-end Android scenarios in the simulated cabin.

Needs a running emulator (nix run .#android-emu) with the debug APK built.
Starts its own hub + two Python bots, puts the phone in seat row 0, bob
(relay) in row 5 and carol in row 10 — the phone can only reach carol
through bob — then drives the phone's UI with droid.py and checks results
through the bots' control ports.

    nix develop --command python tools/android_scenarios.py [-s emulator-5554]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "python/src"))

from droid import Droid  # noqa: E402

from muninn.sim.transport import Admin  # noqa: E402

HUB_PORT = 7420
RESULTS: list[tuple[str, bool, str]] = []


class Bot:
    def __init__(self, name: str, pos: str, port: int, tmp: Path, *extra: str):
        env = dict(
            os.environ,
            MUNINN_SIM=f"127.0.0.1:{HUB_PORT}",
            PYTHONPATH=str(ROOT / "python/src"),
        )
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "muninn.bot",
                "--name",
                name,
                "--pos",
                pos,
                "--control",
                str(port),
                "--data",
                str(tmp / name),
                *extra,
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(300):
            try:
                self.s = socket.create_connection(("127.0.0.1", port))
                break
            except OSError:
                time.sleep(0.2)
        else:
            self.proc.kill()
            raise RuntimeError(f"bot {name} did not start")
        self.f = self.s.makefile("r")
        self.id = self("status")["node"]

    def __call__(self, cmd: str, **kw):
        self.s.sendall((json.dumps({"cmd": cmd, **kw}) + "\n").encode())
        r = json.loads(self.f.readline())
        if not r["ok"]:
            raise RuntimeError(r["error"])
        return r["result"]

    def texts(self, conv: str) -> list[str]:
        return [
            m["text"] for m in self("history", conv=conv, n=200) if m["kind"] == "text"
        ]

    def stop(self):
        self.proc.kill()


def wait(cond, timeout: float, interval: float = 1.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--serial", default="emulator-5554")
    ap.add_argument("--shots", default=tempfile.mkdtemp(prefix="muninn-android-"))
    a = ap.parse_args()
    shots = Path(a.shots)
    tmp = Path(tempfile.mkdtemp(prefix="muninn-scn-"))
    env = dict(os.environ, PYTHONPATH=str(ROOT / "python/src"))
    hub = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "muninn.sim.hub",
            "--port",
            str(HUB_PORT),
            "--decoys",
            "15",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    bots: list[Bot] = []
    d = Droid(a.serial)
    try:
        admin = Admin(f"127.0.0.1:{HUB_PORT}")
        bob = Bot("bob", "5,0", 7511, tmp)
        bots.append(bob)
        carol = Bot("carol", "10,0", 7512, tmp, "--echo")
        bots.append(carol)
        d.adb("logcat", "-c")
        d.sim("kim", pos="0,0")
        kim = None

        def find_kim():
            nonlocal kim
            for ln in d.logcat("node "):
                m = re.search(r"node ([0-9A-F:]{17}) up on sim", ln)
                if m:
                    kim = m.group(1)
            return kim

        check("phone joins the cabin", wait(find_kim, 40), str(kim))
        check(
            "phone reachable from carol only via bob",
            wait(
                lambda: (
                    kim in carol("status")["reachable"]
                    and kim not in [lk["node"] for lk in carol("status")["links"]]
                ),
                90,
            ),
        )

        # carol -> phone (2 hops)
        carol("send", to=kim, text="hello phone from row 10")
        check(
            "relayed message shows in the phone's list",
            d.wait("hello phone from row 10", 40),
        )
        d.tap("carol")
        time.sleep(2)
        check("phone shows relay status in chat header", d.wait("via bob", 15))
        d.shot(str(shots / "relay_chat.png"))
        check(
            "carol sees read receipt (✓✓) through the relay",
            wait(lambda: carol("history", conv="dm:" + kim)[-1]["state"] == "read", 30),
        )

        # phone -> carol: typed in the UI, echoed back by carol's bot
        d.tap("Message")
        d.type("reply from seat 1 :airplane:")
        d.key("BACK")
        d.tap("Send")
        check(
            "carol receives the phone's reply via relay",
            wait(
                lambda: any("reply from seat 1" in t for t in carol.texts("dm:" + kim)),
                40,
            ),
        )
        check("echo comes back to the phone", d.wait("echo: reply from seat 1", 40))

        # group across the relay
        carol("group", name="row party", members=[kim, bob.id])
        d.key("BACK")
        check("group appears on the phone", d.wait("row party", 40))
        carol("gsend", group="row party", text="group hello")
        check("group message reaches the phone", d.wait("group hello", 40))

        # flapping relay link while carol sends a burst
        bob_addr = next(
            x["addr"] for x in admin("state")["devices"] if x["name"] == "bob"
        )
        phone_addr = next(
            x["addr"] for x in admin("state")["devices"] if x["name"] == "kim"
        )
        admin("flap", a=bob_addr, b=phone_addr, up=3, down=2)
        for i in range(8):
            carol("send", to=kim, text=f"burst {i}")
            time.sleep(0.7)
        time.sleep(8)
        admin("flap", a=bob_addr, b=phone_addr)
        ok = wait(
            lambda: all(
                st["state"] != "sent"
                for st in carol("history", conv="dm:" + kim, n=50)
                if st["text"].startswith("burst")
            ),
            90,
        )
        check("burst over a flapping relay link: all 8 delivered", ok)
        d.tap("carol")
        check("last burst message visible on phone", d.wait("burst 7", 30))
        d.shot(str(shots / "after_burst.png"))

        # file from carol to the phone
        pic = tmp / "pic.gif"
        stickers = ROOT / "python/src/muninn/assets/stickers/takeoff.gif"
        pic.write_bytes(stickers.read_bytes())
        carol("file", to=kim, path=str(pic))
        check(
            "file delivered to the phone (receipt)",
            wait(
                lambda: carol("history", conv="dm:" + kim, n=80)[-1]["state"] != "sent",
                60,
            ),
        )
        time.sleep(6)
        d.shot(str(shots / "gif.png"))
    finally:
        for b in bots:
            b.stop()
        hub.kill()
    failed = [r for r in RESULTS if not r[1]]
    print(
        f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed · screenshots in {shots}"
    )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
