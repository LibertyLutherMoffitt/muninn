"""SimTransport — a Muninn transport backed by muninn-simhub."""

from __future__ import annotations

import itertools
import json
import socket
import threading

from muninn.transport import ScanResult, Transport


def _parse(hub: str) -> tuple[str, int]:
    host, _, port = hub.rpartition(":")
    return host or "127.0.0.1", int(port)


def _readline(sock: socket.socket) -> dict:
    buf = bytearray()
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("hub closed")
        if b == b"\n":
            return json.loads(buf)
        buf += b


class SimTransport(Transport):
    max_parallel_dials = 2

    def __init__(
        self,
        hub: str,
        name: str = "",
        addr: str | None = None,
        pos: tuple[float, float] | None = None,
        kind: str = "computer",
        advertise: bool = True,
    ):
        self.kind = kind
        self.advertise = advertise
        self.hub = _parse(hub) if isinstance(hub, str) else hub
        self.name = name
        self._addr = addr
        self.pos = pos
        self._ctrl: socket.socket | None = None
        self._lock = threading.Lock()
        self._rids = itertools.count(1)
        self._waiting: dict[int, tuple[threading.Event, list]] = {}
        self._on_incoming = None
        self._stopped = threading.Event()
        self._registered = threading.Event()

    def _open(self) -> socket.socket:
        s = socket.create_connection(self.hub, timeout=10)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return s

    def _register(self) -> None:
        s = self._open()
        msg = {
            "op": "register",
            "name": self.name,
            "addr": self._addr,
            "kind": self.kind,
            "advertise": self.advertise,
        }
        if self.pos is not None:
            msg["pos"] = list(self.pos)
        s.sendall((json.dumps(msg) + "\n").encode())
        res = _readline(s)
        s.settimeout(None)
        self._addr = res["addr"]
        self._ctrl = s
        self._registered.set()

    def local_addr(self) -> str:
        if self._addr is None or not self._registered.is_set():
            try:
                self._register()
            except OSError:
                return self._addr or "00:00:00:00:00:00"  # hub not up (yet)
        assert self._addr is not None
        return self._addr

    def start(self, on_incoming) -> None:
        self._on_incoming = on_incoming
        if not self._registered.is_set():
            try:
                self._register()
            except OSError:
                pass  # the control loop keeps retrying until the hub is up
        threading.Thread(target=self._ctrl_loop, daemon=True).start()

    def _ctrl_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                ctrl = self._ctrl
                assert ctrl is not None
                while True:
                    m = _readline(ctrl)
                    if m.get("op") == "incoming":
                        threading.Thread(
                            target=self._accept, args=(m["id"], m["from"]), daemon=True
                        ).start()
                    elif m.get("op") == "discovered":
                        w = self._waiting.pop(int(m.get("rid") or 0), None)
                        if w:
                            w[1].extend(m["devices"])
                            w[0].set()
            except Exception:
                if self._stopped.is_set():
                    return
                # Hub restarted? Re-register with the same address.
                self._registered.clear()
                while not self._stopped.wait(1.0):
                    try:
                        self._register()
                        break
                    except Exception:
                        continue

    def _accept(self, cid: int, frm: str) -> None:
        try:
            s = self._open()
            s.sendall((json.dumps({"op": "accept", "id": cid}) + "\n").encode())
            res = _readline(s)
            if not res.get("ok"):
                s.close()
                return
            s.settimeout(None)
        except Exception:
            return
        if self._on_incoming:
            self._on_incoming(s, frm)

    def stop(self) -> None:
        self._stopped.set()
        if self._ctrl:
            try:
                self._ctrl.close()
            except Exception:
                pass

    def connect(self, addr: str, timeout: float = 20.0):
        s = self._open()
        s.settimeout(timeout)
        s.sendall(
            (
                json.dumps({"op": "connect", "from": self.local_addr(), "to": addr})
                + "\n"
            ).encode()
        )
        try:
            res = _readline(s)
        except Exception:
            s.close()
            raise ConnectionError(f"connect {addr}: hub error")
        if not res.get("ok"):
            s.close()
            raise ConnectionError(f"connect {addr}: {res.get('error')}")
        s.settimeout(None)
        return s

    def scan(self, duration: float = 5.0):
        if self._ctrl is None:
            return []
        rid = next(self._rids)
        ev, out = threading.Event(), []
        self._waiting[rid] = (ev, out)
        with self._lock:
            self._ctrl.sendall(
                (json.dumps({"op": "discover", "rid": rid}) + "\n").encode()
            )
        ev.wait(timeout=max(duration * 3, 15))
        return [
            ScanResult(
                d["addr"], d["name"], d["muninn"], d.get("kind", "other"), d.get("rssi")
            )
            for d in out
        ]


class Admin:
    """Synchronous admin client for scripted scenarios and `muninn-simctl`."""

    def __init__(self, hub: str | tuple[str, int]):
        self.s = socket.create_connection(_parse(hub) if isinstance(hub, str) else hub)

    def __call__(self, cmd: str, **kw) -> dict:
        self.s.sendall((json.dumps({"op": "admin", "cmd": cmd, **kw}) + "\n").encode())
        return _readline(self.s)

    def close(self) -> None:
        self.s.close()
