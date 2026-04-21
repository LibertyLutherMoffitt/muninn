"""Windows Bluetooth backend for Muninn via WinRT APIs.

Mirrors the interface exposed by bt/bluez.py so the rest of the codebase
doesn't branch on platform. `bt/__init__.py` dispatches to this module when
`sys.platform == 'win32'`.

**Status:** implemented but not yet tested on real Windows hardware. Review
the WinRT call paths against current `winrt-*` package docs before shipping.

### Architecture

WinRT Bluetooth APIs are async and event-driven. Our core (peers.py,
protocol.py) is threaded and uses a synchronous Python-socket interface. Two
adapters bridge those worlds:

1. **Async-to-sync bridge.** A single asyncio event loop runs in a background
   thread (started lazily on first use). `_run_async(coro)` submits a
   coroutine to it via `run_coroutine_threadsafe` and blocks for the result.
   Every public function in this module that needs WinRT calls wraps them in
   a small `async def _foo_async` helper and calls `_run_async`.

2. **StreamSocket → Python socket adapter.** WinRT's `StreamSocket` exposes
   async `InputStream` / `OutputStream`. `_StreamSocketAdapter` wraps one
   and exposes the subset of the Python socket API that peers.py uses:
   `recv`, `sendall`, `close`, `settimeout`, `gettimeout`, `setblocking`.

### Pairing

Windows provides pairing via `DeviceInformation.Pairing.Custom`. We register
a `PairingRequested` handler that accepts without user interaction
(Just Works / NoInputNoOutput equivalent), then call `PairAsync` with
`DevicePairingKinds.CONFIRM_ONLY` + `DevicePairingProtectionLevel.NONE`.

### Discoverability

Unlike BlueZ (`bluetoothctl discoverable on`), Windows controls
discoverability via the OS Settings app — no programmatic API without
elevated privileges. `set_discoverable()` is a no-op here; the user must
enable Bluetooth and allow their PC to be visible in Settings →
Devices → Bluetooth.

### Outgoing connect model

Windows doesn't have the `ConnectProfile`/`NewConnection` callback pattern
that the BlueZ backend uses. Instead we call
`StreamSocket.ConnectAsync(service.ConnectionHostName, ...)` directly and
get the socket back synchronously. Much simpler than the BlueZ path.
"""

import array
import asyncio
import queue
import re
import threading
import uuid
from typing import Any

from winrt.windows.devices.bluetooth import (  # type: ignore[import-not-found]
    BluetoothAdapter,
    BluetoothDevice,
    BluetoothError,
)
from winrt.windows.devices.bluetooth.rfcomm import (  # type: ignore[import-not-found]
    RfcommDeviceService,
    RfcommServiceId,
    RfcommServiceProvider,
)
from winrt.windows.devices.enumeration import (  # type: ignore[import-not-found]
    DeviceInformation,
    DevicePairingKinds,
    DevicePairingProtectionLevel,
    DevicePairingResultStatus,
)
from winrt.windows.networking.sockets import (  # type: ignore[import-not-found]
    StreamSocket,
    StreamSocketListener,
)
from winrt.windows.storage.streams import (  # type: ignore[import-not-found]
    DataReader,
    DataWriter,
    InputStreamOptions,
)

SERVICE_UUID = "320bcf9c-94fe-46f4-b9bf-83535cafcd55"
SERVICE_NAME = "Muninn"

_RFCOMM_UUID = uuid.UUID(SERVICE_UUID)
_RFCOMM_SERVICE_ID = RfcommServiceId.from_uuid(_RFCOMM_UUID)

# Standard SDP attribute IDs — see Bluetooth Core Spec, Service Discovery.
_SDP_SERVICE_NAME_ATTRIBUTE_ID = 0x0100
# Attribute type byte for a text-string header; len follows as 1 byte.
_SDP_SERVICE_NAME_ATTRIBUTE_TYPE = 0x25


# ---------------------------------------------------------------------------
# Async bridge — single background asyncio loop
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Start the background event loop on first use, then reuse forever."""
    global _loop
    with _loop_lock:
        if _loop is not None:
            return _loop
        ready = threading.Event()

        def run() -> None:
            global _loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _loop = loop
            ready.set()
            loop.run_forever()

        threading.Thread(target=run, daemon=True, name="muninn-winrt-loop").start()
        ready.wait()
        assert _loop is not None
        return _loop


def _run_async(coro) -> Any:
    """Submit a coroutine to the background loop; block until it resolves."""
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


# ---------------------------------------------------------------------------
# MAC helpers
# ---------------------------------------------------------------------------


def mac_to_int(mac: str) -> int:
    return int(mac.replace(":", ""), 16)


def _mac_from_int(addr: int) -> str:
    # 48-bit unsigned, MSB first.
    return ":".join(f"{(addr >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))


def should_keep_outgoing(local_mac: str, peer_mac: str) -> bool:
    """Lower MAC keeps its outgoing socket (higher MAC's outgoing is dropped)."""
    return mac_to_int(local_mac) < mac_to_int(peer_mac)


def _addr_from_host_name(host_name: Any) -> str:
    """Parse a WinRT HostName for a Bluetooth remote into an uppercase MAC.

    BT remote HostNames come through as strings like '(AA:BB:CC:DD:EE:FF)' —
    parens included. Some builds strip the parens. Handle both.
    """
    if host_name is None:
        return ""
    raw = str(getattr(host_name, "raw_name", "") or "").strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    return raw.upper()


def _parse_mac_from_device_id(did: str) -> str | None:
    """Extract a MAC from a Bluetooth DeviceInformation.id.

    BT device IDs embed a trailing 12-hex-digit peer address; format varies.
    This helper looks for a trailing 12-hex-digit group and formats it.
    """
    m = re.search(r"([0-9A-Fa-f]{12})$", did)
    if not m:
        return None
    h = m.group(1)
    return ":".join(h[i : i + 2] for i in (0, 2, 4, 6, 8, 10)).upper()


# ---------------------------------------------------------------------------
# Local adapter
# ---------------------------------------------------------------------------


async def _get_local_mac_async() -> str:
    adapter = await BluetoothAdapter.get_default_async()
    if adapter is None:
        raise RuntimeError("No Bluetooth adapter found")
    return _mac_from_int(int(adapter.bluetooth_address))


def get_local_mac() -> str:
    return _run_async(_get_local_mac_async())


def set_discoverable(enabled: bool) -> None:
    """No-op on Windows — discoverability is controlled in Settings."""
    return


# ---------------------------------------------------------------------------
# StreamSocket adapter — exposes just enough of the Python socket API
# ---------------------------------------------------------------------------


class _StreamSocketAdapter:
    """Wraps a WinRT StreamSocket so peers.py/protocol.py can treat it as a
    Python socket.

    Only these methods are used by the core: `recv`, `sendall`, `close`,
    `settimeout`, `gettimeout`, `setblocking`. Each I/O call hops through the
    background asyncio loop via `_run_async`.
    """

    def __init__(self, sock: StreamSocket, peer_addr: str):
        self._sock = sock
        self._reader = DataReader(sock.input_stream)
        # PARTIAL = return as soon as any data is available (mirrors recv()).
        # WAIT_COMPLETE (the default) would block until the full count arrives,
        # which is wrong for framed protocols that recv() incrementally.
        self._reader.input_stream_options = InputStreamOptions.PARTIAL
        self._writer = DataWriter(sock.output_stream)
        self._timeout: float | None = None
        self.peer_addr = peer_addr
        self._closed = False

    # --- socket-compat surface ---

    def settimeout(self, t: float | None) -> None:
        self._timeout = t

    def gettimeout(self) -> float | None:
        return self._timeout

    def setblocking(self, flag: bool) -> None:
        # Core always uses blocking mode (with optional timeout). Non-blocking
        # maps to a minimal timeout so _recv_async doesn't silently block —
        # if a caller ever passes False, they get poll-like semantics instead
        # of an infinite wait.
        if flag:
            self._timeout = None
        else:
            self._timeout = 0.0

    def recv(self, n: int) -> bytes:
        if self._closed or n <= 0:
            return b""
        return _run_async(self._recv_async(n))

    def sendall(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionError("socket closed")
        _run_async(self._sendall_async(bytes(data)))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        except Exception:
            pass

    # --- async implementations (run on the background loop) ---

    async def _recv_async(self, n: int) -> bytes:
        # Hold the IAsyncOperation directly so we can cancel it on timeout —
        # asyncio.wait_for only cancels the awaiting task, not the underlying
        # WinRT op, which would otherwise keep running and conflict with the
        # next load_async call on the same DataReader.
        op = self._reader.load_async(n)
        try:
            if self._timeout is None:
                got = int(await op)
            else:
                effective = max(float(self._timeout), 0.001)
                try:
                    got = int(await asyncio.wait_for(op, timeout=effective))
                except asyncio.TimeoutError as e:
                    try:
                        op.cancel()
                    except Exception:
                        pass
                    raise TimeoutError("recv timed out") from e
        except OSError as e:
            raise ConnectionError(str(e)) from e

        if got == 0:
            # Peer closed the stream cleanly — mirror socket EOF semantics.
            return b""

        # read_bytes wants a winrt.system.Array[Byte]-shaped buffer. An
        # `array.array('B', ...)` is the most portable stand-in across
        # winrt-python versions (bytearray is rejected by some builds).
        buf = array.array("B", b"\x00" * got)
        self._reader.read_bytes(buf)
        return buf.tobytes()

    async def _sendall_async(self, data: bytes) -> None:
        try:
            # write_bytes needs a winrt.system.Array[Byte]-shaped value.
            # array.array('B', ...) is the portable path; passing list[int]
            # silently works on some bindings and TypeErrors on others.
            self._writer.write_bytes(array.array("B", data))
            await self._writer.store_async()
        except OSError as e:
            raise ConnectionError(str(e)) from e


# ---------------------------------------------------------------------------
# Server (listen) — RfcommServiceProvider + StreamSocketListener
# ---------------------------------------------------------------------------

_incoming_queue: queue.Queue = queue.Queue()

_provider: RfcommServiceProvider | None = None
_listener: StreamSocketListener | None = None
_connection_token: Any = None


def _publish_service_name_sdp(provider: RfcommServiceProvider) -> None:
    """Advertise our friendly name in the SDP record.

    Not strictly required for discovery (clients filter by UUID) but makes
    the service show up with a readable name in `bluetoothctl info` / the
    Windows Devices inspector.
    """
    try:
        writer = DataWriter()
        writer.write_byte(_SDP_SERVICE_NAME_ATTRIBUTE_TYPE)
        name_bytes = SERVICE_NAME.encode("utf-8")
        writer.write_byte(len(name_bytes))
        writer.write_bytes(array.array("B", name_bytes))
        provider.sdp_raw_attributes.insert(
            _SDP_SERVICE_NAME_ATTRIBUTE_ID, writer.detach_buffer()
        )
    except Exception:
        # Advertising still works without the name attribute; swallow.
        pass


async def _create_server_async() -> None:
    global _provider, _listener, _connection_token
    _provider = await RfcommServiceProvider.create_async(_RFCOMM_SERVICE_ID)
    _publish_service_name_sdp(_provider)
    _listener = StreamSocketListener()

    def on_connection(_sender: Any, args: Any) -> None:
        # Event fires on the WinRT thread. We only enqueue the socket — the
        # acceptor thread (cli.acceptor) will pick it up and drive the handshake.
        sock = args.socket
        peer = _addr_from_host_name(sock.information.remote_host_name)
        adapter = _StreamSocketAdapter(sock, peer)
        _incoming_queue.put((adapter, peer))

    _connection_token = _listener.add_connection_received(on_connection)

    # Binding with the provider's service_id.as_string() makes this an RFCOMM
    # listener (not TCP). SocketProtectionLevel.PLAIN_SOCKET = no SSL/etc.
    await _listener.bind_service_name_async(_provider.service_id.as_string())
    _provider.start_advertising(_listener)


def create_server() -> None:
    """Register the Muninn RFCOMM service + start advertising."""
    _run_async(_create_server_async())


async def _close_server_async() -> None:
    global _provider, _listener, _connection_token
    if _provider is not None:
        try:
            _provider.stop_advertising()
        except Exception:
            pass
        _provider = None
    if _listener is not None:
        if _connection_token is not None:
            try:
                _listener.remove_connection_received(_connection_token)
            except Exception:
                pass
            _connection_token = None
        try:
            _listener.close()
        except Exception:
            pass
        _listener = None


def close_server() -> None:
    try:
        _run_async(_close_server_async())
    finally:
        # Sentinel so any thread blocked in accept() wakes up.
        _incoming_queue.put((None, None))


def accept() -> tuple:
    """Block until an incoming connection arrives. Returns (sock, peer_addr)."""
    sock, addr = _incoming_queue.get()
    if sock is None:
        raise ConnectionError("Server closed")
    return sock, addr


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


async def _discover_async() -> list[tuple[str, str]]:
    """Enumerate Bluetooth devices advertising the Muninn RFCOMM service."""
    selector = RfcommDeviceService.get_device_selector(_RFCOMM_SERVICE_ID)

    # Python winrt wrappers have notoriously buggy overload resolution for
    # single-string FindAllAsync/CreateWatcher. Passing a concrete list of strings
    # forces it to match the (String, IIterable<String>) overload successfully.
    properties = ["System.Devices.Aep.DeviceAddress"]
    try:
        devices = list(await DeviceInformation.find_all_async(selector, properties))
    except Exception:
        # Fallback to watcher approach
        watcher = DeviceInformation.create_watcher(selector, properties)
        found_devices = []
        completed = asyncio.Event()
        loop = asyncio.get_running_loop()

        def on_added(_sender: Any, info: Any) -> None:
            found_devices.append(info)

        def on_enum_completed(_sender: Any, _args: Any) -> None:
            loop.call_soon_threadsafe(completed.set)

        watcher.add_added(on_added)
        watcher.add_enumeration_completed(on_enum_completed)
        watcher.start()
        await completed.wait()
        watcher.stop()
        devices = found_devices

    results: list[tuple[str, str]] = []
    for di in devices:
        try:
            service = await RfcommDeviceService.from_id_async(di.id)
        except OSError:
            continue
        if service is None:
            continue
        addr = _addr_from_host_name(service.connection_host_name)
        if not addr:
            addr = _parse_mac_from_device_id(di.id) or ""
        if addr:
            results.append((addr, (di.name or addr)))
    return results


def discover() -> list[tuple[str, str]]:
    # RfcommDeviceService.get_device_selector only matches *paired* devices
    # on Windows — unpaired peers' SDP records aren't queryable without a
    # prior pairing. Users should pair via scan_devices/pair first.
    try:
        return _run_async(_discover_async())
    except Exception as e:
        print(f"Discover error: {e}")
        return []


async def _scan_devices_async(duration: float) -> list[tuple[str, str]]:
    """Broad BT Classic inquiry via a DeviceWatcher.

    Windows doesn't expose a direct `StartDiscovery`/`StopDiscovery` like
    BlueZ; enumeration is event-driven. Run a watcher for `duration` seconds
    and collect whatever it emits.
    """
    # Default get_device_selector() returns paired-only, so the watcher
    # never fires for new devices. Filtering on pairing_state=False makes
    # this an actual inquiry for nearby unpaired peers.
    selector = BluetoothDevice.get_device_selector_from_pairing_state(False)

    # Pass properties to hit the correct 2-arg Python wrapper overload reliably
    properties = ["System.Devices.Aep.DeviceAddress"]
    watcher = DeviceInformation.create_watcher(selector, properties)
    found: dict[str, str] = {}
    completed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_added(_sender: Any, info: Any) -> None:
        if info and info.id:
            found[info.id] = info.name or ""

    def on_updated(_sender: Any, _info: Any) -> None:
        pass

    def on_enum_completed(_sender: Any, _args: Any) -> None:
        # This fires on a background thread — schedule the asyncio.Event
        # through the loop to stay single-threaded.
        loop.call_soon_threadsafe(completed.set)

    watcher.add_added(on_added)
    watcher.add_updated(on_updated)
    watcher.add_enumeration_completed(on_enum_completed)
    watcher.start()
    try:
        try:
            await asyncio.wait_for(completed.wait(), timeout=duration)
        except asyncio.TimeoutError:
            pass
    finally:
        try:
            watcher.stop()
        except Exception:
            pass

    # BT device ids encode the peer MAC as a trailing hex blob. We don't need
    # perfect fidelity here — scan_devices exists only to prime BlueZ's SDP
    # cache on Linux; on Windows the equivalent happens via DeviceWatcher
    # and this result is purely informational.
    results: list[tuple[str, str]] = []

    async def _prime_sdp(did: str) -> None:
        try:
            device = await BluetoothDevice.from_id_async(did)
            if device:
                # Query SDP with a timeout to avoid hanging if peer disappears
                await asyncio.wait_for(
                    device.get_rfcomm_services_for_id_async(_RFCOMM_SERVICE_ID),
                    timeout=5.0,
                )
        except Exception:
            pass

    tasks = []
    for did, name in found.items():
        mac = _parse_mac_from_device_id(did)
        if mac:
            results.append((mac, name or mac))
        tasks.append(asyncio.create_task(_prime_sdp(did)))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    return results


def scan_devices(duration: float = 10.0, quiet: bool = False) -> list[tuple[str, str]]:
    if not quiet:
        print(f"Scanning for nearby Bluetooth devices ({duration:.0f}s)...")
    try:
        return _run_async(_scan_devices_async(duration))
    except Exception as e:
        if not quiet:
            print(f"Scan error: {e}")
        return []


# ---------------------------------------------------------------------------
# Pairing — Just Works via custom PairingRequested handler
# ---------------------------------------------------------------------------


async def _get_device(addr: str) -> BluetoothDevice:
    device = await BluetoothDevice.from_bluetooth_address_async(mac_to_int(addr))
    if device is None:
        raise ConnectionError(f"Device {addr} not found")
    return device


async def _is_paired_async(addr: str) -> bool:
    try:
        device = await _get_device(addr)
        return bool(device.device_information.pairing.is_paired)
    except (OSError, ConnectionError):
        return False


def is_paired(addr: str) -> bool:
    return _run_async(_is_paired_async(addr))


async def _pair_async(addr: str) -> None:
    device = await _get_device(addr)
    pairing = device.device_information.pairing
    if pairing.is_paired:
        return

    custom = pairing.custom

    def on_pairing_requested(_sender: Any, args: Any) -> None:
        # ConfirmOnly + NoInputNoOutput → Just Works: accept without interaction.
        args.accept()

    token = custom.add_pairing_requested(on_pairing_requested)
    try:
        result = await custom.pair_async(
            DevicePairingKinds.CONFIRM_ONLY,
            DevicePairingProtectionLevel.NONE,
        )
    finally:
        try:
            custom.remove_pairing_requested(token)
        except Exception:
            pass

    status = result.status
    if status not in (
        DevicePairingResultStatus.PAIRED,
        DevicePairingResultStatus.ALREADY_PAIRED,
    ):
        raise ConnectionError(f"Pairing failed: {status}")


def pair(addr: str) -> None:
    print(f"Pairing with {addr}...")
    _run_async(_pair_async(addr))
    print(f"Paired {addr}")


def ensure_paired(addr: str) -> None:
    if not is_paired(addr):
        pair(addr)


# ---------------------------------------------------------------------------
# Connect — outbound RFCOMM
# ---------------------------------------------------------------------------


async def _connect_async(addr: str) -> _StreamSocketAdapter:
    device = await _get_device(addr)
    services_result = await device.get_rfcomm_services_for_id_async(_RFCOMM_SERVICE_ID)

    if services_result.error != BluetoothError.SUCCESS:
        raise ConnectionError(
            f"GetRfcommServicesForId failed for {addr}: {services_result.error}"
        )

    service_list = list(services_result.services)
    if not service_list:
        raise ConnectionError(f"No Muninn service advertised by {addr}")

    service = service_list[0]
    sock = StreamSocket()
    try:
        await sock.connect_async(
            service.connection_host_name, service.connection_service_name
        )
    except OSError as e:
        try:
            sock.close()
        except Exception:
            pass
        raise ConnectionError(f"Connect failed: {e}") from e

    return _StreamSocketAdapter(sock, addr)


def connect(addr: str) -> tuple:
    """Connect to a peer's Muninn RFCOMM service. Returns (sock, peer_addr)."""
    addr = addr.upper()
    adapter = _run_async(_connect_async(addr))
    print(f"Connected to {addr}")
    return adapter, addr
