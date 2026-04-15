import queue
import socket
import subprocess
import threading
import time

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

SERVICE_UUID = "320bcf9c-94fe-46f4-b9bf-83535cafcd55"
SERVICE_NAME = "Muninn"
RFCOMM_CHANNEL = 3
_PROFILE_PATH = "/org/muninn/rfcomm"
_AGENT_PATH = "/org/muninn/agent"

_accept_queue: queue.Queue = queue.Queue()  # for accept() in --listen mode
# Per-call queue set by connect_with_listen so each iteration gets its own
# listener. Replaced (with sentinel to unblock stale workers) on each new call.
_listener_queue: queue.Queue | None = None
_listener_lock = threading.Lock()
# Per-address queues for connections we initiated via ConnectProfile.
# NewConnection routes here when addr matches.
_waiters: dict[str, queue.Queue] = {}
_waiters_lock = threading.Lock()
_loop: GLib.MainLoop | None = None


class _Agent(dbus.service.Object):
    """NoInputNoOutput pairing agent — auto-accepts all pairing requests.

    Ensures the link key is stored (store_hint=1) when Device1.Pair() is used,
    which is required for subsequent ConnectProfile calls to succeed.
    """

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        return "0000"

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        return dbus.UInt32(0)

    @dbus.service.method("org.bluez.Agent1", in_signature="ouu", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        # Just Works: auto-confirm without user interaction
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self):
        pass


class _Profile(dbus.service.Object):
    @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
    def NewConnection(self, device_path, fd, properties):
        raw_fd = fd.take()
        sock = socket.socket(fileno=raw_fd)
        sock.setblocking(True)  # BlueZ hands us a non-blocking fd
        addr = str(properties.get("Address", "")).upper()
        if not addr:
            # device_path = /org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF
            dev = str(device_path).rsplit("/", 1)[-1]  # dev_AA_BB_CC_DD_EE_FF
            addr = dev[4:].replace("_", ":").upper()
        with _waiters_lock:
            waiter_q = _waiters.get(addr)
        if waiter_q is not None:
            waiter_q.put((sock, addr))
            return
        with _listener_lock:
            lq = _listener_queue
        if lq is not None:
            lq.put((sock, addr))
        else:
            _accept_queue.put((sock, addr))

    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, device_path):
        pass

    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self):
        pass


def _dbus_loop() -> None:
    global _loop
    bus = dbus.SystemBus()

    # Register pairing agent so link keys are persisted (store_hint=1).
    _Agent(bus, _AGENT_PATH)
    agent_manager = dbus.Interface(
        bus.get_object("org.bluez", "/org/bluez"),
        "org.bluez.AgentManager1",
    )
    agent_manager.RegisterAgent(_AGENT_PATH, "NoInputNoOutput")
    agent_manager.RequestDefaultAgent(_AGENT_PATH)

    # Register RFCOMM profile.
    _Profile(bus, _PROFILE_PATH)
    profile_manager = dbus.Interface(
        bus.get_object("org.bluez", "/org/bluez"),
        "org.bluez.ProfileManager1",
    )
    profile_manager.RegisterProfile(
        _PROFILE_PATH,
        SERVICE_UUID,
        {
            "Name": dbus.String(SERVICE_NAME),
            "Channel": dbus.UInt16(RFCOMM_CHANNEL),
            "AutoConnect": dbus.Boolean(False),
            "RequireAuthentication": dbus.Boolean(False),
            "RequireAuthorization": dbus.Boolean(False),
        },
    )

    _loop = GLib.MainLoop()
    _loop.run()


def _device_path(addr: str) -> str:
    return "/org/bluez/hci0/dev_" + addr.upper().replace(":", "_")


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


def set_discoverable(enabled: bool) -> None:
    state = "on" if enabled else "off"
    subprocess.run(["bluetoothctl", "discoverable", state], capture_output=True)
    subprocess.run(["bluetoothctl", "pairable", state], capture_output=True)


def create_server() -> None:
    """Register agent + RFCOMM profile via D-Bus. Starts background GLib loop."""
    # Must be called before any SystemBus() so the mainloop is set first.
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    set_discoverable(True)
    threading.Thread(target=_dbus_loop, daemon=True).start()
    # Let the GLib loop start and both registrations complete before returning.
    time.sleep(0.5)


def close_server() -> None:
    set_discoverable(False)
    if _loop:
        _loop.quit()
    _accept_queue.put((None, None))


def accept(_=None) -> tuple:
    """Block until incoming connection. Returns (sock, peer_addr)."""
    sock, addr = _accept_queue.get()
    if sock is None:
        raise ConnectionError("Server closed")
    print(f"Connected: {addr}")
    return sock, addr


def set_listener_queue(q: queue.Queue | None) -> None:
    """Set the per-call incoming queue for connect_with_listen.

    Drains the previous queue first: any real connections that arrived
    while the caller was between iterations (e.g. sleeping in the reconnect
    delay) are forwarded to the new queue rather than orphaned.
    Then sends a sentinel to unblock any stale listen_worker still blocked
    on the old queue.
    """
    global _listener_queue
    with _listener_lock:
        old_q = _listener_queue
        _listener_queue = q
    if old_q is not None:
        # Forward connections that arrived before the swap.
        # Once _listener_queue points to q, NewConnection sends new
        # connections there directly — only pre-swap items can be in old_q.
        try:
            while True:
                item = old_q.get_nowait()
                if item[0] is not None and q is not None:
                    q.put(item)
        except queue.Empty:
            pass
        old_q.put((None, None))  # unblock stale listener


def discover() -> list[dict]:
    """Find nearby devices advertising the Muninn service UUID via BlueZ D-Bus.

    Uses BlueZ's ObjectManager cache, populated after bluetoothctl scanning.
    Returns list of dicts with 'host' (MAC) and 'name' keys.
    """
    print("Scanning for Muninn devices...")
    try:
        bus = dbus.SystemBus()
        manager = dbus.Interface(
            bus.get_object("org.bluez", "/"),
            "org.freedesktop.DBus.ObjectManager",
        )
        objects = manager.GetManagedObjects()
    except dbus.DBusException:
        return []

    results = []
    for _path, interfaces in objects.items():
        device = interfaces.get("org.bluez.Device1")
        if device is None:
            continue
        uuids = [str(u).lower() for u in device.get("UUIDs", [])]
        if SERVICE_UUID.lower() in uuids:
            addr = str(device.get("Address", "")).upper()
            name = str(device.get("Name", addr))
            results.append({"host": addr, "name": name})
    return results


def scan_devices(duration: float = 10.0) -> list[tuple[str, str]]:
    """General BT scan via BlueZ D-Bus Adapter1.StartDiscovery.

    Using D-Bus directly (rather than a bluetoothctl subprocess killed by
    timeout) ensures discovered devices are committed to bluetoothd's cache
    and appear in ObjectManager results.
    """
    print(f"Scanning for nearby Bluetooth devices ({duration:.0f}s)...")
    try:
        bus = dbus.SystemBus()
        adapter = dbus.Interface(
            bus.get_object("org.bluez", "/org/bluez/hci0"),
            "org.bluez.Adapter1",
        )
        adapter.StartDiscovery()
        time.sleep(duration)
        try:
            adapter.StopDiscovery()
        except dbus.DBusException:
            pass
    except dbus.DBusException as e:
        print(f"Scan error: {e}")
        return []

    manager = dbus.Interface(
        bus.get_object("org.bluez", "/"),
        "org.freedesktop.DBus.ObjectManager",
    )
    objects = manager.GetManagedObjects()
    devices = []
    for _path, interfaces in objects.items():
        device = interfaces.get("org.bluez.Device1")
        if not device:
            continue
        addr = str(device.get("Address", ""))
        name = str(device.get("Name", addr))
        if addr:
            devices.append((addr, name))
    return devices


def is_paired(addr: str) -> bool:
    try:
        bus = dbus.SystemBus()
        props = dbus.Interface(
            bus.get_object("org.bluez", _device_path(addr)),
            "org.freedesktop.DBus.Properties",
        )
        return bool(props.Get("org.bluez.Device1", "Paired"))
    except dbus.DBusException:
        return False


def pair(addr: str) -> None:
    """Pair via D-Bus Device1.Pair() with our registered NoInputNoOutput agent.

    Using the D-Bus API (rather than a bluetoothctl subprocess) ensures the
    link key is stored with store_hint=1, which is required for ConnectProfile
    to succeed afterwards.
    """
    print(f"Pairing with {addr}...")
    try:
        bus = dbus.SystemBus()
        device = dbus.Interface(
            bus.get_object("org.bluez", _device_path(addr)),
            "org.bluez.Device1",
        )
        device.Pair(timeout=30)
        # Mark trusted so future connections don't require confirmation.
        props = dbus.Interface(
            bus.get_object("org.bluez", _device_path(addr)),
            "org.freedesktop.DBus.Properties",
        )
        props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True))
        print(f"Paired and trusted {addr}")
    except dbus.exceptions.DBusException as e:
        name = e.get_dbus_name()
        if name == "org.bluez.Error.AlreadyExists":
            # Already paired — mark trusted and continue.
            try:
                props = dbus.Interface(
                    bus.get_object("org.bluez", _device_path(addr)),
                    "org.freedesktop.DBus.Properties",
                )
                props.Set("org.bluez.Device1", "Trusted", dbus.Boolean(True))
            except dbus.DBusException:
                pass
        else:
            raise ConnectionError(f"Pairing failed: {e}") from e


def ensure_paired(addr: str) -> None:
    if not is_paired(addr):
        pair(addr)


def mac_to_int(mac: str) -> int:
    return int(mac.replace(":", ""), 16)


def should_keep_outgoing(local_mac: str, peer_mac: str) -> bool:
    """Lower MAC keeps its outgoing socket (higher MAC's outgoing is dropped)."""
    return mac_to_int(local_mac) < mac_to_int(peer_mac)


def connect(addr: str) -> tuple:
    """Connect to remote Muninn profile via BlueZ D-Bus ConnectProfile.

    Requires create_server() to have been called first so the local profile
    is registered — BlueZ delivers the connected socket via NewConnection.

    ConnectProfile is called asynchronously so the GLib loop can process
    both the D-Bus reply and the NewConnection callback concurrently.
    Calling it synchronously deadlocks: the main thread waits for the reply
    while bluetoothd waits for our NewConnection reply, which needs the GLib
    loop — but the GLib loop is sharing the D-Bus connection with the blocked
    main thread call.
    """
    addr = addr.upper()
    q: queue.Queue = queue.Queue()
    with _waiters_lock:
        _waiters[addr] = q
    try:
        bus = dbus.SystemBus()
        device = dbus.Interface(
            bus.get_object("org.bluez", _device_path(addr)),
            "org.bluez.Device1",
        )

        connect_error: list[Exception] = []
        connect_done = threading.Event()

        def on_reply():
            connect_done.set()

        def on_error(e: Exception) -> None:
            connect_error.append(e)
            connect_done.set()

        device.ConnectProfile(
            SERVICE_UUID,
            reply_handler=on_reply,
            error_handler=on_error,
            timeout=20000,
        )

        # Wait for NewConnection to fire (delivers socket via waiter queue).
        try:
            sock, peer_addr = q.get(timeout=20)
            print(f"Connected to {peer_addr}")
            return sock, peer_addr
        except queue.Empty:
            connect_done.wait(timeout=5)
            if connect_error:
                raise ConnectionError(f"ConnectProfile failed: {connect_error[0]}")
            raise ConnectionError(f"Timed out waiting for connection from {addr}")

    finally:
        with _waiters_lock:
            _waiters.pop(addr, None)
