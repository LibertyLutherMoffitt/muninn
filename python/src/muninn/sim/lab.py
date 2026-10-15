"""SimLab — run a hub + N in-process nodes. Used by tests and `muninn-lab`."""

from __future__ import annotations

import asyncio
import socket
import tempfile
import threading
import time
from pathlib import Path

from muninn.node import Node
from muninn.sim.hub import Hub, serve
from muninn.sim.transport import Admin, SimTransport


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SimLab:
    def __init__(self, port: int | None = None, verbose: bool = False, **params):
        self.port = port or free_port()
        self.addr = f"127.0.0.1:{self.port}"
        self.params = params
        self.verbose = verbose
        self.hub: Hub | None = None
        self.loop = asyncio.new_event_loop()
        self.nodes: dict[str, Node] = {}
        self.tmp = Path(tempfile.mkdtemp(prefix="muninn-lab-"))
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)

            async def boot():
                self.hub, self._server = await serve("127.0.0.1", self.port, **params)
                ready.set()

            self.loop.run_until_complete(boot())
            self.loop.run_forever()

        threading.Thread(target=run, daemon=True).start()
        ready.wait(5)
        self.admin = Admin(self.addr)

    def log(self, s: str) -> None:
        if self.verbose:
            print(f"{time.strftime('%H:%M:%S')} {s}", flush=True)

    def node(self, name: str, pos=(0, 0), start: bool = True) -> Node:
        """Create (or restart, keeping its data) a node."""
        addr = None
        db = self.tmp / name / "muninn.db"
        old = self.nodes.get(name)
        if old is not None:
            addr = old.transport.local_addr()
        t = SimTransport(self.addr, name=name, pos=pos, addr=addr)
        n = Node(t, db_path=db, name=name, log=self.log)
        self.nodes[name] = n
        if start:
            n.start()
        return n

    def stop_node(self, name: str) -> None:
        self.nodes[name].stop()

    def move(self, name: str, pos) -> None:
        self.admin("move", addr=self.nodes[name].transport.local_addr(), pos=list(pos))

    def addr_of(self, name: str) -> str:
        return self.nodes[name].transport.local_addr()

    def pair_cmd(self, cmd: str, a: str, b: str, **kw) -> dict:
        return self.admin(cmd, a=self.addr_of(a), b=self.addr_of(b), **kw)

    def close(self) -> None:
        for n in self.nodes.values():
            try:
                n.stop()
            except Exception:
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)


def wait_until(
    cond, timeout: float = 30.0, interval: float = 0.1, msg: str = ""
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s: {msg}")
