# Code review — cross-platform interoperability

A pass over the wire codecs, `ConnectionManager`, the three Bluetooth backends
and the three front ends, aimed at one question: **can a Linux desktop, a
Windows desktop and an Android phone hold a conversation without anyone
babysitting them?**

Fixed items are done and covered by tests. Open items are listed with enough
detail to act on, and deliberately not fixed — either because they need
hardware to verify, or because they are out of scope per `CLAUDE.md`.

---

## 1. Fixed

### Wire format

**`mac_to_bytes` accepted malformed input.** `bytes(int(b, 16) for b in s.split(":"))`
returns whatever length it is given. A five-octet address produced a five-byte
field, silently shifting every subsequent field in the frame — the receiver
would then read a garbage `final_dest`, fail to route, and drop the message
with no error anywhere. Kotlin's `macToBytes` already required six octets, so
the two clients disagreed about what a valid address was. Both now validate
strictly.

**`encode_peer_annc` split UTF-8 sequences.** `name.encode()[:255]` can cut a
multi-byte codepoint in half; the decoder uses `errors="replace"`, so a peer
with a non-ASCII display name propagated as mojibake to everyone one hop away.
Both clients now truncate on a codepoint boundary.

**`peer_count` could overflow.** With more than 255 known peers, `struct.pack("!B", n)`
raised inside `add_peer` — on the accept thread (see below). Now capped, with
the remainder propagating on later connections.

**Kotlin `Frame` / `MessageFrame` compared by identity.** Both were `data class`es
over `ByteArray`, whose generated `equals` is reference equality. Any use in a
`Set` or `==` was silently wrong. Dedup now keys on `msg_id` hex, and the
classes compare contents.

**Android was missing four codecs.** `READ`, `PROFILE`, `GROUP_SETUP` and
`PEER_ANNC` were logged as "unhandled". Consequences, all visible to a user:
the phone never announced its name, so desktops showed a bare wire id forever;
it never learned peers from a relay; and read receipts never came back. All four
are implemented, plus the `decodeHandshake` it lacked, with bounds checks so a
malformed frame costs one frame rather than the session.

### Connection lifecycle

**A malformed stored address could kill the accept loop.** `_send_peer_annc`
runs inside `add_peer`, which runs on the accept thread, and it called
`mac_to_bytes` over every row in `group_store.pubkeys`. One bad row raised
there, the exception propagated out of `acceptor()`, and the thread died. From
then on the device silently refused all inbound connections for the rest of the
session — indistinguishable, from the other side, from being out of range. Bad
rows are now skipped and `acceptor()` survives a failing peer.

**Android delivered retransmits twice.** The sender resends every unacked
message after a reconnect; the phone had no `msg_id` dedup, so the text
appeared again. `PeerBook.claimSeen` mirrors the desktop's first-wins claim,
including the release-on-decrypt-failure rule — without which a message that
arrived before its sender's key would be dropped forever.

**Android decrypted with the wrong key for relayed traffic.** It used the socket
peer's key rather than the originating sender's. Identical for a direct
message, wrong for anything relayed.

### Presentation

**A new peer was announced by MAC, then renamed.** `add_peer` fires
`on_peer_change` before the peer's `PROFILE` frame arrives, and the two race —
whichever side completes the handshake last sees the name first. The CLI now
briefly holds a first-sight connect line for the Profile to land.

**Renames were announced when nothing changed.** Every reconnect re-sends
`PROFILE`, printing "X is now known as X". Reporting is now driven by the
*displayed* label, so a local override (which hides the peer's own name) stays
quiet too.

**The GUI could not see unreachable devices.** `gui/main.py` carried its own
copy of the accept and scan loops, and the copy never recorded sightings or
dial failures. Both front ends now use `muninn/discovery.py`.

---

## 2. Linux vs Windows

Both backends satisfy the same interface, but three behaviours differ in ways
that change what a user must do. These are the reason a first-time pairing is
still manual.

### Discoverability is not programmable on Windows

| | Linux (BlueZ) | Windows (WinRT) |
|---|---|---|
| `set_discoverable(True)` | `bluetoothctl discoverable on` + `pairable on` | **no-op** |

`create_server()` makes a Linux box visible automatically. On Windows there is
no non-elevated API, so the machine is only discoverable while the user has
Settings → Bluetooth open. **Consequence:** a phone or Linux box will not find a
Windows peer that has never been paired. The Windows user must initiate, or
open that panel while the other side scans.

### Windows discovery only sees paired devices

`RfcommDeviceService.get_device_selector` matches bonded devices; an unpaired
peer's SDP record is not queryable. `bluez.discover()` reads BlueZ's
`ObjectManager` cache, which is populated by inquiry and includes devices that
have never been bonded.

**Consequence:** on Linux the scanner finds and dials a new peer unattended. On
Windows, pairing must happen first — `scan_devices()` then `pair()`, or through
Settings. After that, reconnects are automatic on both. This is the "manual
pairing once, then seamless" boundary, and it is a platform limitation rather
than a bug.

### The outgoing connect model is inverted

BlueZ has no synchronous connect for a registered profile. `connect()` calls
`Device1.ConnectProfile` **asynchronously** and waits for BlueZ to hand the
socket back through the `Profile1.NewConnection` callback — so `create_server()`
must have run first, even to dial out. Calling `ConnectProfile` synchronously
deadlocks: the caller blocks on the D-Bus reply while `bluetoothd` waits for our
`NewConnection` reply, which needs the GLib loop the caller is blocking.

WinRT has no such callback: `StreamSocket.connect_async` returns the socket
directly.

**Consequence:** on Linux, an inbound and an outbound socket both surface
through the same profile callback, distinguished only by the per-address waiter
queue. On Windows the two paths are genuinely separate. The simultaneous-connect
tiebreak therefore matters more on Linux, and the 10-second higher-MAC deferral
in `discovery.scanner` is what keeps it rare.

### Same on both

Frame encoding, crypto, the addressing model, static keys, the SQLite schema and
`should_keep_outgoing` are shared code. The `_StreamSocketAdapter` in
`bt/winrt.py` exposes exactly the socket subset `protocol.py` and `peers.py`
use (`recv`, `sendall`, `close`, `settimeout`, `gettimeout`, `setblocking`), and
its recv timeout raises the builtin `TimeoutError` — an `OSError` subclass, so
the `except (ConnectionError, OSError)` handlers in `add_peer` and `_recv_loop`
catch it the same way they catch a `socket.timeout` on Linux.

### Android's difference

API 31+ returns `02:00:00:00:00:00` from `BluetoothAdapter.getAddress()`, so the
phone cannot use its hardware MAC as an identity. It announces a stable random
6-byte **wire id** in the handshake instead, and peers key it by that rather
than by the transport address they dialled. `peer_by_transport` keeps the
mapping so the scanner still recognises the phone as connected and does not
redial it every cycle. This is why `PROTOCOL.md` says "wire id" everywhere it
used to say MAC.

---

## 3. Open

Ordered by how likely they are to bite.

**The WinRT backend has never run on hardware.** Everything below the interface
is unverified. On first Windows run, check in order: `get_local_mac()`;
`create_server()` advertising (`bluetoothctl info` from a Linux box should show
the Muninn UUID); `scan_devices()` returning MACs, since
`_parse_mac_from_device_id` guesses at an ID format that varies by driver; then
`connect()`. The log at `%TEMP%\muninn-winrt.log` traces each step.

**Dedup sets grow without bound.** `seen_acks`, `seen_reads` and `seen_relayed`
never shrink. At real message volumes this is nothing; a long-lived relay node
would leak slowly. Out of scope per `CLAUDE.md` (storage limits), noted for
completeness.

**A peer's wire id is asserted, not proven.** Any device can claim any wire id
in its handshake and receive traffic addressed to that identity. Messages stay
confidential — they are sealed to the *pubkey*, which a relayed announcement can
never overwrite — so an impostor gets ciphertext it cannot read. MITM is
explicitly out of scope.

**`GROUP_SETUP` is recorded but not acted on by Android.** Member keys are
learned so a later 1:1 works, but the phone cannot join a group conversation.
Group support there needs the `storage.py` port.

**Android has no on-disk history.** `ChatRepository` is an in-memory list;
restarting the app loses the thread, and unacked messages are not retransmitted.
The desktop clients persist through `storage.py`. This is the largest remaining
gap between the clients.

**`sendMessage` ignores an unrecognised conversation id.** `bridge.sendMessage`
returns silently if `conv_id` is neither `dm:` nor `group:`-prefixed. Only
reachable from a QML bug, but it fails invisibly rather than surfacing through
`errorOccurred`.
