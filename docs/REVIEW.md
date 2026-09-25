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

**A peer's wire id is asserted, not proven.** Any device can claim any wire id
in its handshake and receive traffic addressed to that identity. Messages stay
confidential — they are sealed to the *pubkey*, which a relayed announcement can
never overwrite — so an impostor gets ciphertext it cannot read. MITM is
explicitly out of scope.

**What a relay carries lives in memory.** A bystander holding a message for
someone out of range loses it if their app restarts. Nothing is lost overall —
the sender keeps every message until it is ACKed and resends when a path
appears — but the "rides along after the sender left" case then fails.

**Names carry no version.** A second-hand name is accepted only from the next
hop toward its owner (it flows outward along the route), which stops a stale
name circulating, but two relays disagreeing after a rename can take a
reconnect to settle. Keys are unaffected: a handshake key always wins.

**Groups are fixed at creation.** There is no add/remove-member frame; a new
member means a new group. Same on every client.

**First contact with a phone needs one tap.** Android only answers inquiry while
the user has made it discoverable (Android caps this at 300 s), and pairing a
laptop with a phone shows a consent prompt. After that first meeting both sides
redial each other unattended.

**`sendMessage` ignores an unrecognised conversation id.** `bridge.sendMessage`
returns silently if `conv_id` is neither `dm:` nor `group:`-prefixed. Only
reachable from a QML bug, but it fails invisibly rather than surfacing through
`errorOccurred`.

---

## 4. User scenarios

The question behind this section: **does every client behave sensibly in the
situations a flight actually produces, including the ones the protocol finds
awkward?** "Desktop" is the Python core (Linux CLI + GUI, Windows); "Android"
is the Kotlin core. Both follow the same rules (`PROTOCOL.md`, "Relay &
Routing"), and each row names the tests that pin it down on both.

| Situation | What happens | Tests |
|---|---|---|
| Two people in adjacent seats, never met | Scanner finds them unattended (probes devices whose SDP record doesn't show; see `dialer.py`). Windows needs a one-time pairing in Settings; a phone needs to be made discoverable once. | `test_integration_loopback`: discovery, hidden UUID, 40-headset cabin |
| Weak signal: the link keeps dropping | Both sides redial (aggressive: every ~8 s, backoff capped at 45 s). Nothing typed in between is lost or duplicated, and order is kept. | `test_nothing_is_lost_on_a_link_that_keeps_dropping`; `nothing is lost or reordered…` (Kotlin); `test_a_dropped_link_mid_conversation_loses_nothing` |
| Both phones notice each other at once and both dial | The crossed-dial tiebreak keeps the session the lower wire id opened, on both ends. Previously each side could keep a different one, so both dropped and redialled in a loop. | `test_a_crossed_dial_settles…` (Python and Kotlin, both orders) |
| A ↔ B ↔ C: A writes to C, out of A's range | B advertises C in its Routes frame; A sends through B; B can't read it. C's ACK and READ come back through B. The UI says "relay via B". | `test_alice_writes_to_carol_through_bob`; `test_a_phone_relays_between_two_laptops`; `test_a_laptop_relays_between_two_phones` |
| A chain of four seats | Routes reach four hops; C's key and name are passed along too, so A can write to D by name. | `test_a_chain_of_four_seats`; `test_a_mixed_chain_of_four` (laptop–phone–laptop–phone) |
| A group that spans a relay | Members get the group through a relay that isn't in it. The relay passes it on without joining. Each member's reachability is shown ("2 of 3 in reach"). | `test_a_group_spans_a_relay`; `test_a_group_spans_both_implementations`; `test_a_phone_creates_a_group_for_two_laptops_it_bridges` |
| Someone absent when the group is created | The setup is sent ahead of their first group message, and again whenever they connect to a member. | `test_a_member_offline_at_creation_…` (both cores) |
| Writing to someone out of range | Allowed. The message waits ("⧗ waiting" on Android, "held for …" in the CLI, a banner in the GUI) and goes out as soon as any path appears. | `test_a_message_is_resent_the_moment_a_relay_path_appears` (both); `test_sending_works_with_nobody_in_range…` |
| Sender goes offline before the recipient is back | Neighbours each keep a copy; whoever meets the recipient delivers it. | `test_a_message_rides_along_after_the_sender_closes_the_laptop`; `test_a_phone_carries_a_message_after_the_sender_leaves` |
| Recipient walks to another row | The route moves with them; the conversation carries on through whoever is now near them. | `test_a_conversation_follows_someone_walking_down_the_aisle`; `test_a_walk_down_the_aisle_moves_the_route` |
| A relay loses a frame (its own link died mid-send) | The sender retries every 30 s while a path exists. | `test_maintain_retries_down_a_relay_path` (both) |
| The sender returns and resends | The relay passes on a frame handed over by its own sender even inside the dedup window. It also holds the recipient's ACK for a sender who is away. So the tick appears straight away, not after the next retry. | `test_the_ack_for_a_resent_message_reaches…` (both) |
| Read while the sender is unreachable | The READ is held and sent when a path to the sender appears. | `test_read_receipts_wait_for_a_path…` (both) |
| A peer reinstalls (new keys, same address) | Queued messages are resealed to the new key instead of being retried forever. | `test_a_peer_that_reinstalled_still_gets…` (both) |
| Phone killed by the OS, or rebooted | History, keys, groups, unread state and the outbox are in SQLite. The service restarts (`START_STICKY`) and resends. | `unacked messages survive a restart`; `history survives a restart, unread and all` |
| Bluetooth or airplane mode toggled mid-flight | Android stops the radio and resumes listening, scanning and dialling when Bluetooth returns. Messages typed meanwhile wait. | Needs a device: `MuninnService.bluetoothState` |
| A newer client sends a frame type this one doesn't know | Ignored; the session continues. Malformed frames cost one frame, not the session. | `test_an_unknown_frame_type…`, `test_a_malformed_frame…` (both) |
| A triangle of devices | A flood around the loop ends: each device forwards a given frame at most once per window, and drops its own frames. | `test_a_relay_never_forwards_a_frame_back_around_a_loop`; `a flood around a loop terminates…` |
| A cabin full of headsets | Probes are rationed per sweep, known peers are always dialled first, and headsets never show in peer lists. | `test_dialer.py` / `DialSchedulerTest`; `a headset that refuses us is never reported as a peer` |

### Client capability, after this pass

| | Linux (CLI, GUI) | Windows | Android |
|---|---|---|---|
| Finds peers unattended | yes | paired devices only | yes |
| Relays for others | yes | yes | yes |
| Writes through a relay / multi-hop | yes | yes | yes |
| Groups (create, join, relay) | yes | yes | yes |
| Offline outbox, resend | yes | yes | yes (SQLite) |
| Store-and-forward for others | yes (memory) | yes (memory) | yes (memory) |
| Survives restart | yes | yes | yes |
| Shows connected / via relay / nearby-unreachable / last seen | yes | yes | yes |
| Notifications | GUI desktop notifications | GUI | yes, per conversation |
| Radio off/on recovery | BlueZ keeps the profile registered | untested | yes |

