#!/usr/bin/env python3
"""Send one command to a muninn-bot / JVM sim node control port.

tools/botctl.py 7501 send to=kim text="hello there"
tools/botctl.py 7501 history conv=dm:AA:BB:CC:DD:EE:FF n=5
tools/botctl.py 7501 status
"""

import json
import socket
import sys


def main() -> None:
    port, cmd, *kv = sys.argv[1:]
    msg: dict = {"cmd": cmd}
    for item in kv:
        k, _, v = item.partition("=")
        try:
            msg[k] = json.loads(v)
        except json.JSONDecodeError:
            msg[k] = v
    s = socket.create_connection(("127.0.0.1", int(port)))
    s.sendall((json.dumps(msg) + "\n").encode())
    print(json.dumps(json.loads(s.makefile().readline()), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
