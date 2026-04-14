import bluetooth

SERVICE_UUID = "320bcf9c-94fe-46f4-b9bf-83535cafcd55"
SERVICE_NAME = "Muninn"


def get_local_mac() -> str:
    with open("/sys/class/bluetooth/hci0/address") as f:
        return f.read().strip().upper()


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


def connect(addr: str) -> tuple:
    services = bluetooth.find_service(uuid=SERVICE_UUID, address=addr)
    if not services:
        raise ConnectionError(f"No Muninn service found on {addr}")

    match = services[0]
    sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
    sock.connect((match["host"], match["port"]))
    print(f"Connected to {addr}")
    return sock, addr.upper()
