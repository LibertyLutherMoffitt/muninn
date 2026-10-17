"""Self-update for the standalone Linux/Windows builds (PyInstaller).

Nix and pip installs update the usual way; this only acts when running as a
frozen binary named like a release asset (muninn-gui-linux-x86_64, …). It
asks GitHub for the latest release, downloads the matching file, checks it
against the `<name>.sha256` CI publishes next to it, swaps the binary and
restarts.

Linux: write next to the binary, then an atomic rename over it (the running
process keeps its open inode). Windows can't overwrite a running .exe but
can rename it: the old one becomes `<exe>.old` and is deleted next start.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from muninn import __version__

REPO = "LibertyLutherMoffitt/muninn"
CHECK_EVERY = 12 * 3600


@dataclass
class Release:
    version: str
    url: str
    sha_url: str | None
    notes: str
    size: int


def newer(a: str, b: str) -> bool:
    def parts(v: str) -> list[int]:
        out = []
        for p in v.lstrip("v").replace("-", ".").split("."):
            digits = "".join(ch for ch in p if ch.isdigit())
            out.append(int(digits) if digits else 0)
        return out

    x, y = parts(a), parts(b)
    n = max(len(x), len(y))
    return x + [0] * (n - len(x)) > y + [0] * (n - len(y))


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def asset_name() -> str | None:
    """Release asset this binary updates from, or None if not a release build."""
    if not frozen():
        return None
    name = Path(sys.executable).name
    stem = name[:-4] if name.endswith(".exe") else name
    if stem.startswith(("muninn-gui-", "muninn-")) and stem.endswith("-x86_64"):
        return name
    return None


def cleanup_old() -> None:
    """Windows: remove the previous binary left behind by an update."""
    if frozen():
        old = Path(sys.executable + ".old")
        try:
            old.unlink()
        except OSError:
            pass


def _get(url: str, timeout: float = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"muninn/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check(name: str | None = None) -> Release | None:
    """The newer release for this binary, or None (also when offline)."""
    name = name or asset_name()
    if not name:
        return None
    j = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
    tag = str(j.get("tag_name", "")).lstrip("v")
    if not newer(tag, __version__):
        return None
    assets = {a["name"]: a for a in j.get("assets", [])}
    a = assets.get(name)
    if a is None:
        return None
    sha = assets.get(name + ".sha256")
    return Release(
        tag,
        a["browser_download_url"],
        sha["browser_download_url"] if sha else None,
        j.get("body", ""),
        int(a.get("size", 0)),
    )


def due(storage) -> bool:
    last = float(storage.get_setting("selfupdate_checked", "0") or 0)
    return time.time() - last > CHECK_EVERY


def mark_checked(storage) -> None:
    storage.set_setting("selfupdate_checked", str(int(time.time())))


def install(rel: Release, progress=None) -> Path:
    """Download, verify, swap. Returns the path to restart into."""
    exe = Path(sys.executable)
    data = bytearray()
    req = urllib.request.Request(
        rel.url, headers={"User-Agent": f"muninn/{__version__}"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        total = int(r.headers.get("Content-Length") or rel.size or 0)
        while chunk := r.read(256 * 1024):
            data += chunk
            if progress and total:
                progress(len(data) / total)
    if rel.sha_url:
        want = _get(rel.sha_url).decode().split()[0].strip().lower()
        got = hashlib.sha256(data).hexdigest()
        if want != got:
            raise ValueError("download doesn't match its checksum — not installed")
    tmp = exe.with_name(exe.name + ".new")
    tmp.write_bytes(data)
    if sys.platform == "win32":
        old = Path(str(exe) + ".old")
        try:
            old.unlink()
        except OSError:
            pass
        os.replace(exe, old)  # a running .exe can be renamed, not overwritten
        os.replace(tmp, exe)
    else:
        tmp.chmod(exe.stat().st_mode | 0o111)
        os.replace(tmp, exe)
    return exe


def restart(exe: Path) -> None:
    args = [a for a in sys.argv[1:]]
    if sys.platform == "win32":
        import subprocess

        subprocess.Popen([str(exe), *args], close_fds=True)
        os._exit(0)
    os.execv(str(exe), [str(exe), *args])
