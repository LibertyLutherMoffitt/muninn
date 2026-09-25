"""Shared harness for full-stack tests: real client processes on the loopback.

`Client` is a Python CLI subprocess. `set_topology` decides who can hear whom
(see `muninn/bt/loopback.py`). `spec/kotlin-conformance` provides a Kotlin node
that joins the same loopback; `kotlin_node.py` wraps it with the same surface.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"


def set_topology(rendezvous: Path, *links: tuple[str, str]) -> None:
    """Only these pairs can hear each other. Written atomically: every client
    re-reads the file continuously and must never see half of it."""
    rendezvous.mkdir(parents=True, exist_ok=True)
    tmp = rendezvous / "topology.json.tmp"
    tmp.write_text(json.dumps({"links": [list(pair) for pair in links]}))
    os.replace(tmp, rendezvous / "topology.json")


class Client:
    """A Muninn CLI subprocess with line-buffered capture."""

    def __init__(
        self,
        mac: str,
        name: str,
        rendezvous: Path,
        home: Path,
        ghosts="",
        noise="",
        hide_uuid=False,
    ):
        env = dict(os.environ)
        env.update(
            PYTHONPATH=str(SRC),
            PYTHONUNBUFFERED="1",
            MUNINN_BT_BACKEND="loopback",
            MUNINN_LOOPBACK_DIR=str(rendezvous),
            MUNINN_LOOPBACK_MAC=mac,
            MUNINN_LOOPBACK_NAME=name,
            MUNINN_LOOPBACK_GHOSTS=ghosts,
            MUNINN_LOOPBACK_NOISE=noise,
            MUNINN_LOOPBACK_HIDE_UUID="1" if hide_uuid else "",
            MUNINN_NAME=name,
            XDG_DATA_HOME=str(home),
            TERM="dumb",
        )
        home.mkdir(parents=True, exist_ok=True)
        self.mac = mac.upper()
        self.name = name
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "muninn.cli"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            with self._lock:
                self.lines.append(raw.rstrip("\n"))

    def send(self, line: str) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    def output(self) -> str:
        with self._lock:
            return "\n".join(self.lines)

    def wait_for(self, needle: str, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.output():
                return True
            if self.proc.poll() is not None:
                return needle in self.output()
            time.sleep(0.05)
        return needle in self.output()

    def wait_for_after(self, mark: int, needle: str, timeout: float = 30.0) -> bool:
        """Like wait_for, but only output produced after `mark` (a length of
        output() taken earlier) counts — so a stale match cannot satisfy it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.output()[mark:]:
                return True
            time.sleep(0.05)
        return needle in self.output()[mark:]

    def wait_for_count(self, needle: str, count: int, timeout: float = 30.0) -> bool:
        """Wait until `needle` has appeared at least `count` times."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.output().count(needle) >= count:
                return True
            time.sleep(0.05)
        return self.output().count(needle) >= count

    def close(self) -> None:
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass


@pytest.fixture
def clients(tmp_path):
    made: list[Client] = []

    def spawn(
        mac: str,
        name: str,
        ghosts: str = "",
        noise: str = "",
        hide_uuid: bool = False,
    ) -> Client:
        client = Client(
            mac,
            name,
            tmp_path / "rendezvous",
            tmp_path / name,
            ghosts,
            noise,
            hide_uuid,
        )
        made.append(client)
        return client

    yield spawn
    for c in made:
        c.close()


def poll(client, command: str, needle: str, timeout: float = 60.0) -> bool:
    """Re-run a command until its fresh output contains `needle`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        mark = len(client.output())
        client.send(command)
        if client.wait_for_after(mark, needle, timeout=2.0):
            return True
    return False


def diagnose(*cs) -> str:
    return "\n\n".join(f"--- {c.name} ---\n{c.output()}" for c in cs)
