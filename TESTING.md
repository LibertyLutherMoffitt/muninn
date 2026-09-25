# Testing Muninn

Muninn talks to itself across three clients on two independent protocol
implementations: Python (`peers.py`, used by the CLI, the Qt GUI and Windows)
and Kotlin (`Mesh.kt`, used by the Android app). Most of what breaks lives in
the seams — between a Bluetooth backend and the core, or between the Python
relay and the Kotlin one — so the suites below are arranged by which seam they
cover, not by which file they touch.

Nothing here needs a Bluetooth radio or a second device. Only building the
Android app and rendering its screens needs the Android SDK.

## Quick start

```bash
# Python: unit + integration (needs pytest and pynacl)
cd python && python -m pytest tests/ -q

# Kotlin: wire conformance, the Android mesh core, notification rules
# (needs a JDK; no Android SDK)
cd spec/kotlin-conformance && gradle test

# Android app: compiles the real thing (needs an SDK, see below)
cd android && gradle assembleDebug

# Android screens, rendered to PNG with no emulator (needs the SDK)
cd android && gradle :app:recordPaparazziDebug   # -> app/src/test/snapshots/images/
```

The Python suite includes `test_interop_kotlin.py`, which puts Kotlin nodes and
Python clients in one simulated cabin. It builds the Kotlin node with `gradle
installDist` on first use (and again whenever a Kotlin source changes), and
skips — rather than fails — when no JDK/Gradle is installed.

### Building the Android app

Everything in `android/app` outside the pure-JVM files is only checked by the
compiler, so building it is the difference between "looks right" and "is right".

```bash
# One-time, on a machine without Android Studio:
export ANDROID_HOME=/opt/android-sdk
sdkmanager "platforms;android-34" "build-tools;34.0.0" "platform-tools"
echo "sdk.dir=$ANDROID_HOME" > android/local.properties   # gitignored
```

`gradle assembleDebug` then produces `app/build/outputs/apk/debug/app-debug.apk`.
Run it after any change under `android/`; a Compose mistake is a compile error,
not a runtime surprise, so this catches most of them for the price of ~25s.

Inside the nix dev shell, `nix develop --command prek run --all-files` runs the
linters. The Python suite has no dependency on `dbus` or WinRT: `tests/conftest.py`
pins `MUNINN_BT_BACKEND=loopback` for the session.

## The layers

### 1. Wire format — `python/tests/test_protocol.py`

Asserts **exact byte offsets**, not just round-trips. A round-trip test passes
happily while both sides of it drift away from `PROTOCOL.md`; these fail the
moment a field moves, which is what matters when a second implementation is
reading the same bytes.

### 2. Cross-language conformance — `spec/`

`spec/wire-vectors.json` pins one canonical encoding per frame type plus an
X25519 / NaCl-Box crypto vector. Both clients decode it:

- `python/tests/test_wire_vectors.py`
- `spec/kotlin-conformance/src/test/kotlin/com/muninn/WireVectorsTest.kt`

This is the only test that can catch "the desktop and the phone no longer speak
the same protocol", because no single-language suite can. It also proves
lazysodium (Android) and PyNaCl (desktop) produce **byte-identical** ciphertext
for the same key and nonce.

`Protocol.kt`, `PeerBook.kt`, `Mesh.kt`, `MeshStore.kt`, `DialScheduler.kt` and
`ChatRepository.kt` deliberately import nothing from `android.*`, so the
conformance project compiles them on a plain JVM. Keep them that way — the
rules that must match the desktop client live there, and rules that cannot be
tested drift.

Regenerate the vectors only when `PROTOCOL.md` changes on purpose:

```bash
python3 spec/generate_vectors.py
```

A diff in `wire-vectors.json` is a wire-compatibility break. `test_wire_vectors.py`
fails if the checked-in file is stale.

### 3. Core behaviour — `python/tests/test_peers.py`

Stands two (or three) real `ConnectionManager`s up over `socket.socketpair()`
and drives the actual handshake, framing, dedup, relay and persistence code.
No Bluetooth backend is involved, but it is the same code path all three
clients use. Covers handshake variants (including the legacy 32-byte form and
Android's differing wire id), relay through a middle peer, ACK/READ flood-back,
dedup of retransmits, and reconnect resend.

Two in-process gotchas the fixtures handle for you, both in `conftest.py`:

- **`drop_link`, not `close`.** Closing an fd another thread is blocked reading
  does *not* deliver EOF to the far end — the blocked `recv` keeps it open. A
  half-close (`shutdown`) is what a real remote disconnect looks like.
- **`RecordingSock`.** `socket.socket` attributes are read-only, so a test that
  needs to inspect the wire substitutes a proxy into `peers[addr].sock`.

### 3b. Routing scenarios — `test_routing.py` and `MeshTest.kt`

The situations a flight produces, one test each, written twice — once against
the Python core and once against the Kotlin one — so the two relays are held
to the same rules: a device two and three hops away, the shortest route
winning, a route following someone down the aisle, a message carried by a
bystander after the sender has gone, a resend the moment a relay path appears,
an ACK reaching a sender who missed the first one, a relay that silently loses
a frame, a flood around a loop terminating, read receipts waiting for a path,
groups relayed by non-members, a member absent at creation, a crossed dial, a
reinstalled peer, junk and unknown frames, and a link that keeps dropping.

### 4. Full stack — `python/tests/test_integration_loopback.py`

Starts real `muninn.cli` subprocesses and drives them through stdin as a user
would: discovery with no configuration, messaging both ways, ACKs and read
receipts, name propagation, presence rendering, history across a restart, and
redelivery of a message sent while the peer was away.

This is the layer that catches wiring bugs between the backend and the core —
where most real breakage has historically lived. It is also the slowest
(~60s); it runs real scan cycles.

### 5. A cabin — `python/tests/test_integration_mesh.py`

Real CLI processes where not everyone can hear everyone. `topology.json` in the
rendezvous directory (see below) decides who is in range of whom, and the tests
rewrite it mid-run: a three-seat relay, a four-seat chain, a group across a
relay, someone walking from one row to another, a message that reaches its
recipient after the sender has shut the laptop, and a link that flaps while
someone keeps typing.

### 6. Python and Kotlin together — `python/tests/test_interop_kotlin.py`

`spec/kotlin-conformance/src/node` wraps the Android app's protocol core
(`Mesh.kt`) in a headless process that joins the loopback cabin and is driven
over stdin/stdout. Every test sends at least one message from one
implementation to the other *through a third device*: a phone relaying between
two laptops, a laptop relaying between two phones, groups created on either
side, a phone carrying a message for someone who is away, and a
laptop–phone–laptop–phone chain. This is the test that the two relays actually
agree; conformance vectors alone cannot show that.

### 7. Android screens — `android/app/src/test/.../ScreensTest.kt`

Paparazzi renders the Compose screens on the JVM with Android's own layout
engine, seeded with a cabin: someone connected, someone reachable only through
them, someone visible who won't connect, someone who has left, and a group
spanning them. It writes PNGs to look at, not goldens to diff (they contain
the time of day). It exists because there is no emulator here, and it has
already caught three layout bugs.

## Running the apps without a radio

`MUNINN_BT_BACKEND=loopback` swaps the BlueZ/WinRT backend for one that carries
frames over TCP on `127.0.0.1` and finds peers through a rendezvous directory.
Two clients on one machine then behave like two devices in a cabin.

```bash
export MUNINN_BT_BACKEND=loopback
export MUNINN_LOOPBACK_DIR=/tmp/muninn-demo

# terminal 1
MUNINN_LOOPBACK_MAC=AA:AA:AA:AA:AA:01 MUNINN_LOOPBACK_NAME=alice \
  MUNINN_NAME=alice XDG_DATA_HOME=/tmp/muninn-demo/alice python -m muninn.cli

# terminal 2
MUNINN_LOOPBACK_MAC=BB:BB:BB:BB:BB:02 MUNINN_LOOPBACK_NAME=bob \
  MUNINN_NAME=bob XDG_DATA_HOME=/tmp/muninn-demo/bob python -m muninn.cli
```

They discover each other and connect on their own. `muninn-gui` works the same
way, and a GUI and a CLI instance interoperate — which is the cheapest way to
check a change to the shared core did not break one of them.

| Variable | Meaning |
|---|---|
| `MUNINN_BT_BACKEND` | `loopback`, `bluez`, or `winrt`. Unset picks by platform. |
| `MUNINN_LOOPBACK_MAC` | This instance's address. Defaults to one derived from the pid. |
| `MUNINN_LOOPBACK_NAME` | Device name peers see in scans. |
| `MUNINN_LOOPBACK_DIR` | Rendezvous directory. Instances only find each other if this matches. |
| `MUNINN_LOOPBACK_GHOSTS` | `MAC=Name,…` — devices that advertise the service and refuse every connection. |
| `MUNINN_LOOPBACK_NOISE` | `MAC=Name,…` — devices that are not Muninn at all: visible, never advertise, always refuse. A cabin full of headsets. |
| `MUNINN_LOOPBACK_HIDE_UUID` | `1` to omit this instance's service record, modelling an adapter whose SDP cache never resolves. |
| `MUNINN_LOOPBACK_PAIRING` | `1` to require `ensure_paired()` before connecting. |
| `MUNINN_SCAN_POLICY` | `aggressive` / `balanced` / `conservative`, overriding the stored choice for one run. |

### Simulating a full cabin

The case the dial scheduler exists for is forty Bluetooth devices in range and
one of them the person you want. `MUNINN_LOOPBACK_NOISE` fabricates the other
thirty-nine, and `MUNINN_LOOPBACK_HIDE_UUID` models the failure that makes this
hard: an adapter whose SDP cache never learns the Muninn UUID, so the peer is
invisible to a service-filtered lookup and only a blind dial can find it.

```bash
export MUNINN_LOOPBACK_NOISE="$(python - <<'PY'
print(",".join(f"C0:FF:EE:00:{i//256:02X}:{i%256:02X}=Headset{i}" for i in range(40)))
PY
)"
```

`test_integration_loopback.py` runs both scenarios. The hidden-UUID test hides
the record on *both* sides on purpose — hiding one proves nothing, because the
other side would still discover it and dial in.

### Ghosts

A ghost advertises the Muninn service and refuses every RFCOMM connection —
what a peer with a stale link key or a busy radio looks like. Being dialled and
failing is the point: it is how the presence tracker learns *nearby, can't
connect*, which is otherwise awkward to reproduce on demand.

```bash
MUNINN_LOOPBACK_GHOSTS="DE:AD:BE:EF:00:01=Row 12 Pixel" python -m muninn.cli
# then: /known
```

### Who can hear whom: `topology.json`

By default every loopback instance is in range of every other. Write
`topology.json` into the rendezvous directory to change that:

```json
{"links": [["AA:AA:AA:AA:AA:01", "BB:BB:BB:BB:BB:02"],
           ["BB:BB:BB:BB:BB:02", "CC:CC:CC:CC:CC:03"]]}
```

Only listed pairs can see and connect to each other. The file is re-read
continuously, so removing a pair cuts a live link within ~100 ms — that is how
the tests model walking away. Write it atomically (`os.replace`), as
`cli_harness.set_topology` does. The Kotlin node obeys the same file.

### Driving the GUI headless

The Qt GUI runs under `QT_QPA_PLATFORM=offscreen`, which is enough to
screenshot it in CI or in a container. Grab from the `QQuickWindow`
(`QGuiApplication.allWindows()`), not the base `QWindow` — only the former has
`grabWindow()`.

## What is not covered

- **Real Bluetooth.** Pairing quirks, RFCOMM channel negotiation, SDP caching
  and adapter resets need hardware. The loopback backend deliberately does not
  simulate them; it models only success, refusal, and absence.
- **The WinRT backend.** No Windows host here, and no fake for the WinRT call
  surface. See `docs/REVIEW.md` for the behavioural differences to check by
  hand on first run.
- **Android glue.** The protocol core (`Protocol.kt`, `Mesh.kt`, `PeerBook.kt`,
  `DialScheduler.kt`) and the conversation model (`ChatRepository.kt`) are
  unit-tested and run against Python; the screens are rendered. What only a
  phone can check: `MuninnService` (radio on/off, accept/dial), `BtDiscovery`,
  `SqliteMeshStore`, notifications, and permissions.
- **Timing on real radios.** The cabin tests run in seconds because TCP connects
  in microseconds. An RFCOMM page takes ~1–5 s and can fail at range; the
  retry and backoff rules are tested, the real-world timings are not.
- **QML.** Rendered and smoke-driven, not asserted on.
