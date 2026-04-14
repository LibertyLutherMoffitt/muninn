import argparse
import sys
import threading
import time

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

    except ConnectionError:
        stop_event.set()
        print("\nConnection lost.")


def chat(sock, box, local_mac, peer_addr, unacked, seen):
    local_mac_bytes = protocol.mac_to_bytes(local_mac)
    peer_mac_bytes = protocol.mac_to_bytes(peer_addr)
    stop_event = threading.Event()

    # Resend unacked messages from previous connection
    for msg_id, frame_bytes in list(unacked.items()):
        try:
            sock.send(frame_bytes)
        except ConnectionError:
            raise

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
            except ConnectionError:
                stop_event.set()
                break
            print("> ", end="", flush=True)
    except (KeyboardInterrupt, EOFError):
        stop_event.set()
        raise KeyboardInterrupt

    if stop_event.is_set():
        raise ConnectionError("Disconnected")


def main():
    parser = argparse.ArgumentParser(
        prog="muninn",
        description="Encrypted P2P chat over Bluetooth Classic",
    )
    parser.add_argument(
        "--listen", action="store_true", help="Listen for incoming connections"
    )
    parser.add_argument(
        "--connect", metavar="BT_ADDR", help="Connect to a device by BT MAC address"
    )
    args = parser.parse_args()

    if not args.listen and not args.connect:
        parser.print_help()
        sys.exit(1)

    local_mac = bt.get_local_mac()
    print(f"Local MAC: {local_mac}")

    private_key = crypto.generate_keypair()
    unacked = {}
    seen = set()
    server_sock = None

    if args.listen:
        server_sock = bt.create_server()

    try:
        while True:
            try:
                if args.listen:
                    sock, peer_addr = bt.accept(server_sock)  # ty:ignore[invalid-argument-type]
                else:
                    sock, peer_addr = bt.connect(args.connect)

                box = handshake(sock, private_key)
                chat(sock, box, local_mac, peer_addr, unacked, seen)

            except ConnectionError as e:
                print(f"Reconnecting in 2s... ({e})")
                time.sleep(2)

    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        if server_sock:
            server_sock.close()


if __name__ == "__main__":
    main()
