#!/usr/bin/env python3
"""Tiny Android UI driver for headless emulator testing (adb + uiautomator).

    droid.py -s emulator-5554 tap "Tic-tac-toe"     # tap node whose text/desc contains it
    droid.py -s emulator-5554 wait "your move" 20   # wait until text appears
    droid.py -s emulator-5554 type "hello :fire:"
    droid.py -s emulator-5554 key BACK
    droid.py -s emulator-5554 shot out.png [scale]
    droid.py -s emulator-5554 texts                 # dump visible texts
    droid.py -s emulator-5554 sim kim [hub]         # fresh app joined to the sim hub as "kim"

Usable as a library too (Droid class) for scripted scenarios.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

APK = "android/app/build/outputs/apk/debug/app-debug.apk"


class Droid:
    def __init__(self, serial: str):
        self.serial = serial

    def adb(self, *args: str, capture: bool = True) -> str:
        r = subprocess.run(
            ["adb", "-s", self.serial, *args], capture_output=capture, text=False
        )
        return r.stdout.decode("utf-8", "replace") if capture and r.stdout else ""

    def nodes(self) -> list[dict]:
        xml = ""
        for _ in range(3):
            xml = self.adb("exec-out", "uiautomator", "dump", "/dev/tty")
            if "<hierarchy" in xml:
                break
            time.sleep(0.5)
        xml = xml[xml.find("<?xml") : xml.rfind(">") + 1] if "<?xml" in xml else xml
        try:
            root = ET.fromstring(xml.split("UI hierchary dumped")[0])
        except ET.ParseError:
            return []
        out = []
        for n in root.iter("node"):
            b = [int(x) for x in re.findall(r"\d+", n.get("bounds", ""))]
            if len(b) == 4:
                out.append(
                    {
                        "text": n.get("text", ""),
                        "desc": n.get("content-desc", ""),
                        "bounds": b,
                        "click": n.get("clickable") == "true",
                    }
                )
        return out

    def find(self, needle: str) -> dict | None:
        for n in self.nodes():
            if needle in n["text"] or needle in n["desc"]:
                return n
        return None

    def tap_xy(self, x: int, y: int) -> None:
        self.adb("shell", "input", "tap", str(x), str(y))

    def tap(self, needle: str, timeout: float = 10) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            n = self.find(needle)
            if n:
                x1, y1, x2, y2 = n["bounds"]
                self.tap_xy((x1 + x2) // 2, (y1 + y2) // 2)
                return True
            time.sleep(0.5)
        print(f"tap: not found: {needle}", file=sys.stderr)
        return False

    def wait(self, needle: str, timeout: float = 20) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.find(needle):
                return True
            time.sleep(0.7)
        return False

    def texts(self) -> list[str]:
        return [n["text"] or n["desc"] for n in self.nodes() if n["text"] or n["desc"]]

    def type(self, text: str) -> None:
        # `input text` can't do non-ASCII; shortcodes keep emoji testable.
        self.adb("shell", "input", "text", text.replace(" ", "%s").replace("'", "\\'"))

    def key(self, name: str) -> None:
        self.adb(
            "shell",
            "input",
            "keyevent",
            name if name.startswith("KEYCODE_") else "KEYCODE_" + name,
        )

    def shot(self, path: str, scale: float = 0.4) -> None:
        png = subprocess.run(
            ["adb", "-s", self.serial, "exec-out", "screencap", "-p"],
            capture_output=True,
        ).stdout
        with open(path, "wb") as f:
            f.write(png)
        if scale != 1:
            subprocess.run(["magick", path, "-resize", f"{int(scale * 100)}%", path])

    def install(self, apk: str = APK) -> None:
        print(self.adb("install", "-r", "-g", apk).strip().splitlines()[-1:])

    def sim(
        self,
        name: str,
        hub: str = "127.0.0.1:7420",
        clear: bool = True,
        pos: str | None = None,
    ) -> None:
        port = hub.rsplit(":", 1)[1]
        self.adb("reverse", f"tcp:{port}", f"tcp:{port}")
        if clear:
            self.adb("shell", "pm", "clear", "com.muninn")
            for p in (
                "BLUETOOTH_CONNECT",
                "BLUETOOTH_SCAN",
                "BLUETOOTH_ADVERTISE",
                "POST_NOTIFICATIONS",
            ):
                self.adb(
                    "shell", "pm", "grant", "com.muninn", f"android.permission.{p}"
                )
        self.adb(
            "shell",
            "am",
            "start",
            "-n",
            "com.muninn/.MainActivity",
            "--es",
            "sim",
            hub,
            "--es",
            "name",
            name,
        )

    def logcat(self, grep: str = "Muninn") -> list[str]:
        return [ln for ln in self.adb("logcat", "-d").splitlines() if grep in ln]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--serial", default="emulator-5554")
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    d = Droid(a.serial)
    c, args = a.cmd, a.args
    if c == "tap":
        sys.exit(0 if d.tap(args[0]) else 1)
    elif c == "wait":
        sys.exit(0 if d.wait(args[0], float(args[1]) if len(args) > 1 else 20) else 1)
    elif c == "type":
        d.type(args[0])
    elif c == "key":
        d.key(args[0])
    elif c == "shot":
        d.shot(args[0], float(args[1]) if len(args) > 1 else 0.4)
    elif c == "texts":
        print("\n".join(d.texts()))
    elif c == "install":
        d.install()
    elif c == "sim":  # sim NAME [HUB] [ROW,SEAT]
        d.sim(
            args[0],
            args[1] if len(args) > 1 else "127.0.0.1:7420",
            pos=args[2] if len(args) > 2 else None,
        )
    elif c == "xy":
        d.tap_xy(int(args[0]), int(args[1]))
    else:
        sys.exit(f"unknown command {c}")


if __name__ == "__main__":
    main()
