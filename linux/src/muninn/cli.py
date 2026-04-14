import argparse
import sys
import threading
import time

import bluetooth.btcommon

from muninn import bt, crypto, protocol

# 1:1 uses a zeroed group_id
GROUP_ID = b"\x00" * 16


def handshake(sock, private_key):
    pubkey_bytes = bytes(private_key.public_key)
    sock.send(protocol.encode_handshake(pubkey_bytes))

    frame_type, payload = protocol.read_frame(sock)
    if frame_type != protocol.TYPE_HANDSHAKE:
        raise ConnectionError(f"Expected handshake frame, got 0x{frame_type:02x}")
    if len(payload) != 32:
        raise ConnectionError(f"Bad handshake pubkey length: {len(payload)}")

    box = crypto.derive_box(private_key, payload)
    print("E2EE established.")
    return box


def recv_loop(sock, box, local_mac_bytes, unacked, seen, stop_event):
    try:
        while not stop_event.is_set():
            frame_type, payload = protocol.read_frame(sock)

            if frame_type == protocol.TYPE_MESSAGE:
                gid, msg_id, sender, dest, ts, encrypted = protocol.decode_message(
                    payload
                )
                plaintext = crypto.decrypt(box, encrypted)

                if msg_id in seen:
                    sock.send(protocol.encode_ack(msg_id, local_mac_bytes))
                    continue

                seen.add(msg_id)
                text = plaintext.decode("utf-8")
                print(f"\r< {text}")
                print("> ", end="", flush=True)

                sock.send(protocol.encode_ack(msg_id, local_mac_bytes))

            elif frame_type == protocol.TYPE_ACK:
                msg_id, _ = protocol.decode_ack(payload)
                unacked.pop(msg_id, None)

    except (ConnectionError, OSError, bluetooth.btcommon.BluetoothError):
        stop_event.set()
        print("\nConnection lost.")


def chat(sock, box, local_mac, peer_addr, unacked, seen):
    local_mac_bytes = protocol.mac_to_bytes(local_mac)
    peer_mac_bytes = protocol.mac_to_bytes(peer_addr)
    stop_event = threading.Event()

    # Resend unacked messages from previous connection
    for msg_id, frame_bytes in list(unacked.items()):
        sock.send(frame_bytes)

    recv_thread = threading.Thread(
        target=recv_loop,
        args=(sock, box, local_mac_bytes, unacked, seen, stop_event),
        daemon=True,
    )
    recv_thread.start()

    print("> ", end="", flush=True)
    try:
        for line in sys.stdin:
            if stop_event.is_set():
                break
            text = line.strip()
            if not text:
                print("> ", end="", flush=True)
                continue

            msg_id = protocol.new_msg_id()
            encrypted = crypto.encrypt(box, text.encode("utf-8"))
            frame = protocol.encode_message(
                GROUP_ID, msg_id, local_mac_bytes, peer_mac_bytes, encrypted
            )
            unacked[msg_id] = frame
            try:
                sock.send(frame)
            except (ConnectionError, OSError, bluetooth.btcommon.BluetoothError):
                stop_event.set()
                break
            print("> ", end="", flush=True)
    except (KeyboardInterrupt, EOFError):
        stop_event.set()
        raise KeyboardInterrupt

    if stop_event.is_set():
        raise ConnectionError("Disconnected")


def pick_from_list(items: list[tuple[str, str]]) -> str:
    if len(items) == 1:
        addr, name = items[0]
        print(f"Found: {name} ({addr})")
        return addr

    print("Found devices:")
    for i, (addr, name) in enumerate(items, 1):
        print(f"  {i}) {name} ({addr})")

    while True:
        try:
            choice = int(input("Pick device: "))
            if 1 <= choice <= len(items):
                return items[choice - 1][0]
        except (ValueError, EOFError):
            pass
        print(f"Enter 1-{len(items)}")


def pick_device() -> str:
    # First try SDP — finds paired devices already running Muninn
    services = bt.discover()
    if services:
        items = [(s["host"], s.get("name", s["host"])) for s in services]
        return pick_from_list(items)

    # No Muninn service found — fall back to general BT scan
    print("No Muninn services found. Scanning all nearby devices...")
    devices = bt.scan_devices()
    if not devices:
        raise ConnectionError("No Bluetooth devices found nearby")

    addr = pick_from_list(devices)
    bt.ensure_paired(addr)
    return addr


def listen_worker(server_sock, result, connected):
    """Background thread: accept incoming connection."""
    try:
        sock, peer_addr = bt.accept(server_sock)
        result["sock"] = sock
        result["peer_addr"] = peer_addr
        connected.set()
    except (OSError, bluetooth.btcommon.BluetoothError):
        pass  # server socket closed or adapter error


def connect_with_listen(local_mac, existing_server=None):
    """Listen for incoming AND scan/connect outgoing simultaneously.

    Returns (sock, peer_addr, server_sock).
    """
    server_sock = existing_server or bt.create_server()
    result = {}
    connected = threading.Event()

    listen_thread = threading.Thread(
        target=listen_worker,
        args=(server_sock, result, connected),
        daemon=True,
    )
    listen_thread.start()

    # Scan while listening in background
    print("Scanning for Muninn devices (or waiting for incoming)...")

    def use_incoming():
        """Incoming won the race — use that connection, keep server for reconnect."""
        print(f"Incoming connection from {result['peer_addr']}")
        return result["sock"], result["peer_addr"], server_sock

    def use_outgoing(addr):
        """We picked a device — connect outgoing, resolve conflicts."""
        bt.ensure_paired(addr)
        sock, peer_addr = bt.connect(addr)

        if connected.is_set():
            # Both connections formed — apply tiebreaker
            incoming_sock = result["sock"]
            incoming_addr = result["peer_addr"]

            if bt.should_keep_outgoing(local_mac, peer_addr):
                print("Simultaneous connection — keeping outgoing (lower MAC)")
                try:
                    incoming_sock.close()
                except Exception:
                    pass
                server_sock.close()
                return sock, peer_addr, None
            else:
                print("Simultaneous connection — keeping incoming (lower MAC)")
                try:
                    sock.close()
                except Exception:
                    pass
                return incoming_sock, incoming_addr, server_sock

        server_sock.close()
        return sock, peer_addr, None

    services = bt.discover()
    if connected.is_set():
        return use_incoming()

    if services:
        items = [(s["host"], s.get("name", s["host"])) for s in services]
        print("(Incoming connections still accepted while you choose)")
        addr = pick_from_list(items)
        if connected.is_set():
            return use_incoming()
        return use_outgoing(addr)

    print("No Muninn services found. Scanning all nearby devices...")
    devices = bt.scan_devices()
    if connected.is_set():
        return use_incoming()

    if devices:
        print("(Incoming connections still accepted while you choose)")
        addr = pick_from_list(devices)
        if connected.is_set():
            return use_incoming()
        return use_outgoing(addr)

    print("No devices found. Waiting for incoming connection...")
    connected.wait()
    return use_incoming()


def main():
    parser = argparse.ArgumentParser(
        prog="muninn",
        description="Encrypted P2P chat over Bluetooth Classic",
    )
    parser.add_argument(
        "--listen", action="store_true", help="Listen only (don't scan)"
    )
    parser.add_argument(
        "--connect",
        metavar="BT_ADDR",
        nargs="?",
        const="",
        help="Connect only (scan if no address given)",
    )
    args = parser.parse_args()

    local_mac = bt.get_local_mac()
    print(f"Local MAC: {local_mac}")

    private_key = crypto.generate_keypair()
    unacked = {}
    seen = set()
    server_sock = None

    try:
        while True:
            try:
                if args.listen:
                    if not server_sock:
                        server_sock = bt.create_server()
                    sock, peer_addr = bt.accept(server_sock)  # ty:ignore[invalid-argument-type]
                elif args.connect is not None and args.connect:
                    bt.ensure_paired(args.connect)
                    sock, peer_addr = bt.connect(args.connect)
                elif args.connect is not None:
                    addr = pick_device()
                    sock, peer_addr = bt.connect(addr)
                else:
                    # Default: listen + scan simultaneously
                    sock, peer_addr, server_sock = connect_with_listen(
                        local_mac, server_sock
                    )

                box = handshake(sock, private_key)
                chat(sock, box, local_mac, peer_addr, unacked, seen)

            except (
                ConnectionError,
                OSError,
                bluetooth.btcommon.BluetoothError,
            ) as e:
                print(f"Reconnecting in 2s... ({e})")
                time.sleep(2)

    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        if server_sock:
            server_sock.close()


if __name__ == "__main__":
    main()
