import logging
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

_incoming_queue: queue.Queue = queue.Queue()
# Per-address queues for connections we initiated via ConnectProfile.
# NewConnection routes here when addr matches.
_waiters: dict[str, queue.Queue] = {}
_waiters_lock = threading.Lock()
_loop: GLib.MainLoop | None = None
_ready = threading.Event()
_start_error: list[Exception] = []
log = logging.getLogger("muninn.bt")


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
        _incoming_queue.put((sock, addr))

    @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
    def RequestDisconnection(self, device_path):
        pass

    @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
    def Release(self):
        pass


def _dbus_loop() -> None:
    global _loop
    try:
        _register()
    except Exception as e:
        _start_error.append(e)
        _ready.set()
        return
    _loop = GLib.MainLoop()
    _ready.set()
    _loop.run()


def _register() -> None:
    bus = dbus.SystemBus()

    # No pairing agent: Muninn never pairs (a bond is what lets a phone use
    # this computer as a headset), so it never needs to answer pairing
    # requests either.

    # Register RFCOMM profile.
    _Profile(bus, _PROFILE_PATH)
    profile_manager = dbus.Interface(
        bus.get_object("org.bluez", "/org/bluez"),
        "org.bluez.ProfileManager1",
    )
    try:
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
    except dbus.DBusException as e:
        if e.get_dbus_name() == "org.bluez.Error.NotPermitted":
            raise RuntimeError(
                "Another Muninn is already using Bluetooth on this computer "
                "(close the other window or terminal client first)."
            ) from e
        raise


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


def _adapter_props():
    return dbus.Interface(
        dbus.SystemBus().get_object("org.bluez", "/org/bluez/hci0"),
        "org.freedesktop.DBus.Properties",
    )


def _get_adapter(prop: str) -> bool | None:
    try:
        return bool(_adapter_props().Get("org.bluez.Adapter1", prop))
    except dbus.DBusException:
        return None


def _set_adapter(prop: str, value: bool) -> None:
    try:
        _adapter_props().Set("org.bluez.Adapter1", prop, dbus.Boolean(value))
    except dbus.DBusException as e:
        log.warning("set %s=%s failed: %s", prop, value, e)


# Adapter state before Muninn touched it — restored on exit.
_prev_discoverable: bool | None = None


def set_discoverable(enabled: bool) -> None:
    """Visible to inquiries (so seat neighbours can find us). Deliberately
    NOT pairable: strangers must not be able to start pairing with this
    machine just because Muninn is running. Muninn's own links never need
    a bond (open RFCOMM profile) and Muninn never pairs."""
    _set_adapter("Discoverable", enabled)


def create_server() -> None:
    """Register the RFCOMM profile via D-Bus. Starts background GLib loop."""
    # Must be called before any SystemBus() so the mainloop is set first.
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    _ready.clear()
    _start_error.clear()
    threading.Thread(target=_dbus_loop, daemon=True).start()
    # Registrations must complete (or fail loudly) before we go on.
    _ready.wait(10)
    if _start_error:
        raise _start_error[0]
    global _prev_discoverable
    if _prev_discoverable is None:
        _prev_discoverable = _get_adapter("Discoverable")
    set_discoverable(True)


def close_server() -> None:
    set_discoverable(bool(_prev_discoverable))  # leave the adapter as we found it
    if _loop:
        _loop.quit()
    _incoming_queue.put((None, None))


def accept() -> tuple:
    """Block until incoming connection. Returns (sock, peer_addr)."""
    sock, addr = _incoming_queue.get()
    if sock is None:
        raise ConnectionError("Server closed")
    return sock, addr


def discover() -> list[tuple[str, str]]:
    """Find nearby devices advertising the Muninn service UUID via BlueZ D-Bus.

    Uses BlueZ's ObjectManager cache, populated after bluetoothctl scanning.
    Returns list of (addr, name) tuples.
    """
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
            results.append((addr, name))
    return results


_MAJOR_CLASS = {1: "computer", 2: "phone", 4: "audio", 5: "peripheral", 7: "wearable"}


def device_kind(cls: int) -> str:
    """Bluetooth Class of Device → coarse kind (major device class)."""
    return _MAJOR_CLASS.get((int(cls) >> 8) & 0x1F, "other") if cls else "other"


def inquiry(duration: float = 5.0) -> list[dict]:
    """Run a Classic (BR/EDR) inquiry and report every device seen:
    {addr, name, muninn (True/False/None = unknown), kind, rssi}."""
    try:
        bus = dbus.SystemBus()
        adapter = dbus.Interface(
            bus.get_object("org.bluez", "/org/bluez/hci0"), "org.bluez.Adapter1"
        )
        try:  # Muninn is RFCOMM-only: don't also scan the LE band
            adapter.SetDiscoveryFilter({"Transport": dbus.String("bredr")})
        except dbus.DBusException:
            pass
        try:
            adapter.StartDiscovery()
        except dbus.DBusException:
            pass  # already discovering
        time.sleep(duration)
        # RSSI is only present while discovery runs: read before stopping.
        objects = dbus.Interface(
            bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager"
        ).GetManagedObjects()
        try:
            adapter.StopDiscovery()
        except dbus.DBusException:
            pass
        try:
            adapter.SetDiscoveryFilter({})
        except dbus.DBusException:
            pass
    except dbus.DBusException:
        return []
    out = []
    for _path, ifaces in objects.items():
        d = ifaces.get("org.bluez.Device1")
        if not d or not d.get("Address"):
            continue
        if str(d.get("AddressType", "public")) == "random":
            continue  # LE-only device: can't speak RFCOMM
        if "RSSI" not in d:
            continue  # only in BlueZ's cache, not actually seen this time
        uuids = [str(u).lower() for u in d.get("UUIDs", [])]
        muninn = True if SERVICE_UUID.lower() in uuids else (False if uuids else None)
        out.append(
            {
                "addr": str(d["Address"]).upper(),
                "name": str(d.get("Name", d.get("Alias", ""))),
                "muninn": muninn,
                "kind": device_kind(int(d.get("Class", 0))),
                "rssi": int(d["RSSI"]) if "RSSI" in d else None,
            }
        )
    return out


def scan_devices(duration: float = 10.0, quiet: bool = False) -> list[tuple[str, str]]:
    """General BT scan via BlueZ D-Bus Adapter1.StartDiscovery.

    Using D-Bus directly (rather than a bluetoothctl subprocess killed by
    timeout) ensures discovered devices are committed to bluetoothd's cache
    and appear in ObjectManager results.
    """
    if not quiet:
        log.info("scanning for nearby Bluetooth devices (%.0fs)", duration)
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
        log.warning("scan error: %s", e)
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


def _props(addr: str):
    return dbus.Interface(
        dbus.SystemBus().get_object("org.bluez", _device_path(addr)),
        "org.freedesktop.DBus.Properties",
    )


def health() -> tuple[str, str]:
    """For the pre-flight check: adapter present, powered, discoverable."""
    powered = _get_adapter("Powered")
    if powered is None:
        return ("bad", "no Bluetooth adapter found")
    if not powered:
        return ("bad", "Bluetooth is off — turn it on")
    visible = _get_adapter("Discoverable")
    return (
        "ok",
        f"{get_local_mac()} on"
        + (
            ", visible to phones"
            if visible
            else ", not visible (phones can't find this computer)"
        ),
    )


def distrust(addrs: list[str]) -> list[str]:
    """Clear Trusted on these devices (Muninn ≤ 0.2 set it after pairing; a
    trusted bonded phone could then use this computer as a headset without
    asking). Returns the addresses changed."""
    changed = []
    for a in addrs:
        try:
            p = _props(a)
            if bool(p.Get("org.bluez.Device1", "Trusted")):
                p.Set("org.bluez.Device1", "Trusted", dbus.Boolean(False))
                changed.append(a)
                log.info("untrusted %s (Muninn never needs trust)", a)
        except dbus.DBusException:
            continue
    return changed


def unpair(addrs: list[str]) -> list[str]:
    """Remove bonds with these devices (Muninn never needs one)."""
    done = []
    for a in addrs:
        if is_paired(a):
            forget_device(a)
            done.append(a)
    return done


def forget_device(addr: str) -> None:
    """Unpair and drop BlueZ's record of a device (user forgot that person)."""
    try:
        adapter = dbus.Interface(
            dbus.SystemBus().get_object("org.bluez", "/org/bluez/hci0"),
            "org.bluez.Adapter1",
        )
        adapter.RemoveDevice(dbus.ObjectPath(_device_path(addr)))
        log.info("removed %s", addr)
    except dbus.DBusException as e:
        if e.get_dbus_name() != "org.bluez.Error.DoesNotExist":
            log.warning("remove %s: %s", addr, e)


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
        # Poll both the queue and the error channel so an immediate
        # ConnectProfile failure surfaces without waiting the full timeout.
        deadline = time.monotonic() + 20
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                sock, peer_addr = q.get(timeout=min(0.5, remaining))
                log.info("connected to %s", peer_addr)
                return sock, peer_addr
            except queue.Empty:
                if connect_error:
                    raise ConnectionError(f"ConnectProfile failed: {connect_error[0]}")
                # connect_done set without error means ConnectProfile returned
                # OK but NewConnection hasn't fired yet — keep waiting.
        if connect_error:
            raise ConnectionError(f"ConnectProfile failed: {connect_error[0]}")
        raise ConnectionError(f"Timed out waiting for connection from {addr}")

    finally:
        with _waiters_lock:
            _waiters.pop(addr, None)
