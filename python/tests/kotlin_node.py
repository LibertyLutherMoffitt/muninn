"""The Android client's protocol core, as a process the Python tests can start.

`spec/kotlin-conformance` builds a headless node around `Mesh.kt` — the same
routing, relaying and group code the phone runs — that joins the loopback
backend's rendezvous directory and obeys its `topology.json`. This module
builds it when needed and wraps it with the same surface as `cli_harness.Client`
so a test can mix Kotlin and Python clients in one cabin.

Tests using it are skipped, not failed, when no JDK/Gradle is available.
"""

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "spec" / "kotlin-conformance"
NODE_BIN = PROJECT / "build" / "install" / "muninn-node" / "bin" / "muninn-node"
NODE_JAR = (
    PROJECT
    / "build"
    / "install"
    / "muninn-node"
    / "lib"
    / "muninn-wire-conformance.jar"
)
SOURCES = [
    REPO / "android" / "app" / "src" / "main" / "kotlin" / "com" / "muninn",
    PROJECT / "src" / "node",
    PROJECT / "build.gradle.kts",
]

_build_lock = threading.Lock()
_build_error: str | None = None


def _newest_source() -> float:
    newest = 0.0
    for root in SOURCES:
        paths = [root] if root.is_file() else root.rglob("*.kt")
        for path in paths:
            newest = max(newest, path.stat().st_mtime)
    return newest


def ensure_built() -> str | None:
    """Build the node if it is missing or stale. Returns an error, or None."""
    global _build_error
    with _build_lock:
        if (
            NODE_BIN.exists()
            and NODE_JAR.exists()
            and NODE_JAR.stat().st_mtime >= _newest_source()
        ):
            return None
        if _build_error is not None:
            return _build_error
        gradle = shutil.which("gradle")
        if gradle is None:
            _build_error = "gradle not found — cannot build the Kotlin node"
            return _build_error
        result = subprocess.run(
            [gradle, "installDist", "-q"],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if result.returncode != 0 or not NODE_BIN.exists():
            _build_error = (
                "Kotlin node build failed:\n"
                + result.stdout[-2000:]
                + result.stderr[-2000:]
            )
            return _build_error
        return None


class KotlinNode:
    """A running Kotlin node. Same send/output/wait_for surface as Client."""

    def __init__(self, mac: str, name: str, rendezvous: Path):
        rendezvous.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(
            MUNINN_LOOPBACK_DIR=str(rendezvous),
            MUNINN_LOOPBACK_MAC=mac,
            MUNINN_NAME=name,
        )
        self.mac = mac.upper()
        self.name = name
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            [str(NODE_BIN)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._pump, daemon=True).start()

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
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.output()[mark:]:
                return True
            time.sleep(0.05)
        return needle in self.output()[mark:]

    def close(self) -> None:
        self.send("quit")
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
