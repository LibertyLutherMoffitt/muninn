import subprocess

import bluetooth
import bluetooth.btcommon

SERVICE_UUID = "320bcf9c-94fe-46f4-b9bf-83535cafcd55"
SERVICE_NAME = "Muninn"


def get_local_mac() -> str:
    result = subprocess.run(
        ["bluetoothctl", "show"],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if "Controller" in line:
            return line.split()[1].upper()
    raise RuntimeError("No Bluetooth adapter found")


def create_server() -> bluetooth.BluetoothSocket:
    sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
    sock.bind(("", bluetooth.PORT_ANY))
    sock.listen(1)
    port = sock.getsockname()[1]

    bluetooth.advertise_service(
        sock,
        SERVICE_NAME,
        service_id=SERVICE_UUID,
        service_classes=[SERVICE_UUID, bluetooth.SERIAL_PORT_CLASS],
        profiles=[bluetooth.SERIAL_PORT_PROFILE],
    )

    print(f"Listening on RFCOMM channel {port}...")
    return sock


def accept(server_sock: bluetooth.BluetoothSocket):
    client_sock, (addr, _) = server_sock.accept()
    print(f"Connected: {addr}")
    return client_sock, addr.upper()


def discover() -> list[dict]:
    """Scan all nearby devices for the Muninn SDP service."""
    print("Scanning for Muninn devices...")
    services = bluetooth.find_service(uuid=SERVICE_UUID)
    return services


def scan_devices() -> list[tuple[str, str]]:
    """General BT scan — returns all nearby discoverable devices."""
    print("Scanning for nearby Bluetooth devices...")
    devices = bluetooth.discover_devices(
        duration=8, lookup_names=True, lookup_class=False
    )
    return [(addr, name or addr) for addr, name in devices]


def is_paired(addr: str) -> bool:
    result = subprocess.run(
        ["bluetoothctl", "info", addr],
        capture_output=True,
        text=True,
    )
    return "Paired: yes" in result.stdout


def pair(addr: str) -> None:
    print(f"Pairing with {addr}...")
    print("Confirm the pairing on both devices if prompted.")

    result = subprocess.run(
        ["bluetoothctl", "pair", addr],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise ConnectionError(f"Pairing failed: {result.stderr.strip()}")

    subprocess.run(
        ["bluetoothctl", "trust", addr],
        capture_output=True,
        text=True,
    )
    print(f"Paired and trusted {addr}")


def ensure_paired(addr: str) -> None:
    if not is_paired(addr):
        pair(addr)


def mac_to_int(mac: str) -> int:
    """Convert MAC string to integer for comparison."""
    return int(mac.replace(":", ""), 16)


def should_keep_outgoing(local_mac: str, peer_mac: str) -> bool:
    """Tiebreak for simultaneous connections.

    The device with the LOWER MAC keeps its outgoing socket.
    Equivalently: drop the socket initiated by the higher MAC.
    """
    return mac_to_int(local_mac) < mac_to_int(peer_mac)


def connect(addr: str) -> tuple:
    services = bluetooth.find_service(uuid=SERVICE_UUID, address=addr)
    if not services:
        raise ConnectionError(f"No Muninn service found on {addr}")

    match = services[0]
    sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
    sock.connect((match["host"], match["port"]))
    print(f"Connected to {addr}")
    return sock, addr.upper()
