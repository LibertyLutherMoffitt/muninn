#!/usr/bin/env python3
"""Real Android Bluetooth between emulators (netsim), scripted.

Emulators on one host share netsim's virtual radio, so the app runs its real
BtTransport (RFCOMM, discovery, never pairing) — no simulator hub.

    tools/netsim_lab.py up 2           # boot muninn, muninn-b (5554, 5556), fresh app in BT mode
    tools/netsim_lab.py meet a b       # b becomes visible, a finds it; waits for the link
    tools/netsim_lab.py ping a b 20 200  # a pings b 20× with 200-byte datagrams
    tools/netsim_lab.py bench          # up 2 + meet + ping sizes 0/200/1000 (latency table)
    tools/netsim_lab.py down

Devices are named a, b, c… (AVD muninn, muninn-b, muninn-c; ports 5554, 5556…).
Needs `nix develop` (adb) and a debug APK (cd android && gradle :app:assembleDebug).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from droid import APK, Droid  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PKG = "com.muninn"
PERMS = (
    "BLUETOOTH_CONNECT",
    "BLUETOOTH_SCAN",
    "BLUETOOTH_ADVERTISE",
    "POST_NOTIFICATIONS",
)


def serial(letter: str) -> str:
    return f"emulator-{5554 + 2 * (ord(letter) - ord('a'))}"


def dev(letter: str) -> Droid:
    return Droid(serial(letter))


def running() -> set[str]:
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
    return {
        ln.split()[0] for ln in out.splitlines()[1:] if ln.strip().endswith("device")
    }


def boot(letter: str) -> None:
    s = serial(letter)
    if s in running():
        return
    env = dict(os.environ)
    if letter != "a":
        env["MUNINN_AVD"] = letter
    log = open(f"/tmp/netsim-{letter}.log", "w")
    subprocess.Popen(
        [
            "nix",
            "run",
            ".#android-emu",
            "--",
            "-port",
            str(5554 + 2 * (ord(letter) - ord("a"))),
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    print(f"booting {letter} ({s})…")


def wait_boot(letter: str, timeout: float = 240) -> None:
    d = dev(letter)
    end = time.time() + timeout
    while time.time() < end:
        if d.adb("shell", "getprop", "sys.boot_completed").strip() == "1":
            return
        time.sleep(2)
    raise SystemExit(f"{letter} didn't boot")


def fresh(letter: str) -> None:
    d = dev(letter)
    print(d.adb("install", "-r", "-g", str(ROOT / APK)).strip().splitlines()[-1:])
    d.adb("shell", "pm", "clear", PKG)
    for p in PERMS:
        d.adb("shell", "pm", "grant", PKG, f"android.permission.{p}")
    d.adb("shell", "svc", "bluetooth", "enable")
    start(letter, "--es", "name", letter.upper())


def start(letter: str, *extras: str) -> None:
    dev(letter).adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity", *extras)


def node_id(letter: str) -> str:
    for ln in reversed(dev(letter).logcat("up on")):
        m = re.search(r"node (\S+) up on", ln)
        if m:
            return m.group(1)
    raise SystemExit(f"{letter}: app not up (no 'node … up on' in logcat)")


def connected(a: str, b: str) -> bool:
    """a's log says it linked to b (Mesh logs 'link up <id>')."""
    bid = node_id(b)
    return any(
        bid in ln and "link up" in ln for ln in dev(a).adb("logcat", "-d").splitlines()
    )


def meet(a: str, b: str, timeout: float = 120) -> None:
    db = dev(b)
    db.adb(
        "shell",
        "am",
        "start",
        "-a",
        "android.bluetooth.adapter.action.REQUEST_DISCOVERABLE",
    )
    time.sleep(2)
    for label in ("Allow", "ALLOW"):
        if db.tap(label):
            break
    start(a, "--ez", "find", "true")
    print(f"{a} looking for {b}…")
    end = time.time() + timeout
    while time.time() < end:
        if connected(a, b) or connected(b, a):
            print(f"{a} ↔ {b} linked")
            return
        time.sleep(3)
    raise SystemExit(f"{a} and {b} didn't link within {timeout:.0f}s")


def ping(a: str, b: str, n: int = 10, size: int = 0) -> list[float | None]:
    d = dev(a)
    d.adb("logcat", "-c")
    start(
        a,
        "--es",
        "ping",
        node_id(b),
        "--ei",
        "ping_n",
        str(n),
        "--ei",
        "ping_size",
        str(size),
    )
    end = time.time() + n * 3.5 + 10
    while time.time() < end:
        for ln in d.logcat("Ping"):
            m = re.search(r"rtt_ms=(\S+)", ln)
            if m:
                return [
                    None if x == "lost" else float(x) for x in m.group(1).split(",")
                ]
        time.sleep(1)
    raise SystemExit("no ping result")


def summary(rtts: list[float | None]) -> str:
    ok = sorted(r for r in rtts if r is not None)
    if not ok:
        return "all lost"
    return (
        f"median {ok[len(ok) // 2]:.0f} ms, min {ok[0]:.0f}, max {ok[-1]:.0f}, "
        f"lost {len(rtts) - len(ok)}/{len(rtts)}"
    )


def main() -> None:
    args = sys.argv[1:] or ["help"]
    cmd = args[0]
    if cmd == "up":
        letters = [
            chr(ord("a") + i) for i in range(int(args[1]) if len(args) > 1 else 2)
        ]
        for x in letters:
            boot(x)
        for x in letters:
            wait_boot(x)
            fresh(x)
        time.sleep(6)
        for x in letters:
            print(x, node_id(x))
    elif cmd == "meet":
        meet(args[1], args[2])
    elif cmd == "ping":
        n = int(args[3]) if len(args) > 3 else 10
        size = int(args[4]) if len(args) > 4 else 0
        print(summary(ping(args[1], args[2], n, size)))
    elif cmd == "bench":
        main_args = sys.argv
        sys.argv = [main_args[0], "up", "2"]
        main()
        meet("a", "b")
        time.sleep(5)
        print("| datagram | round trip (a → b → a) |\n|---|---|")
        for size in (0, 200, 1000):
            print(f"| {size} B | {summary(ping('a', 'b', 20, size))} |")
    elif cmd == "down":
        for s in sorted(running()):
            if s.startswith("emulator-"):
                subprocess.run(["adb", "-s", s, "emu", "kill"])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
