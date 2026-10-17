# Testing Muninn

Nothing here needs a Bluetooth radio or a second device. Everything runs in the
nix dev shell (`nix develop`).

## Quick start

```bash
cd python && python -m pytest -q                 # all Python suites (~11 min, 150+ tests)
cd tea && go test ./...                          # Bubble Tea client unit tests
cd android && ./gradlew :core:test               # Kotlin core unit tests
cd android && ./gradlew :core:installDist        # JVM node used by interop tests
cd android && ./gradlew :app:assembleDebug       # Android app
nix develop --command prek run --all-files       # linters
```

`tests/test_interop.py` is skipped unless `:core:installDist` has been run;
`tests/test_tea.py` needs `go` and `tmux` (both in the dev shell).

## The simulated cabin

`muninn-simhub` (`python/src/muninn/sim/hub.py`) is a virtual Bluetooth radio.
Every simulated device — Python node, JVM node, Android emulator — connects to
it over TCP. It models what makes a cabin hard:

| Effect | Model |
|---|---|
| Range | seat positions in rows; solid ≤ 3 rows, degrading to nothing at 7 |
| Weak links | connect failures, slow connects, random drops ∝ (1 − quality)² |
| Airtime | per-device shared bandwidth (~90 KB/s), so relays slow down |
| Latency | 12 ms per hop, +80 ms at the edge of range |
| Noise | decoy headsets / watches / phones (`--decoys N`), some phones with hidden service records |
| Chaos | admin commands: `move`, `block`, `flap` (up/down cycle), `quality`, `offline`, `kill`, `hide` |

`SimLab` (`sim/lab.py`) starts a hub plus in-process nodes for tests.

### Test suites (python/tests)

| File | What it pins down |
|---|---|
| `test_protocol.py` | frame codecs, malformed input, UTF-8-safe name cutting |
| `test_mesh.py` | DM + ACK + read, relay line, store-and-forward, 3-way group |
| `test_chaos.py` | flapping relay link, weak edge both ways, a peer walking past, sender restart with queued messages, 6-seat cabin with 40 decoys and concurrent group chatter, file through a flapping relay, multi-hop names/keys, group created while a member is away, simultaneous dial, replies/reactions/edits/receipts via relay, app events, 2 random-chaos soak runs, reinstalled peer |
| `test_discovery.py` | hidden service records found by polite probes; headsets/watches never contacted; quiet mode; relayed neighbours upgrade to a direct link via address hints; presence states; Python/Kotlin scan-policy parity |
| `test_games.py` | every game plugin played to the end with random moves, JSON round trips, canvas-subset rendering, rule spot checks |
| `test_forget.py` | delete a chat, forget / block people, leaving a room by deleting it, privacy switches, `:doctor` |
| `test_backup.py` | backup with identity into a fresh install, merge without duplicates, someone else's backup refused, crafted zips can't write outside the files dir |
| `test_media.py` | photos shrunk, turned upright and stripped of metadata; small images / GIFs / "original" untouched; thumbnails |
| `test_devices.py` | linked devices: link with code (wrong codes, lockout), forged sets rejected, DM fan-out + mirroring, read marks, merged earlier chats, history sync with files and rooms, offline catch-up, edit/react across devices, games from either device, unlink / leave, restart |
| `test_xeddsa.py`, `test_wire_vectors.py` | byte-exact signatures and frames, shared with the Kotlin tests (`spec/`) |
| `test_vim.py` | the Vim engine shared by the GUI and the TUI |
| `test_api.py` | the stdio JSON API (`muninn --api`) used by the Bubble Tea client |
| `test_tea.py` | the Bubble Tea binary end to end in tmux: chat, emoji completion, ticks, reactions, replies, palette, command panels |
| `test_interop.py` | Python ↔ Kotlin: DMs, relays both ways, mixed group, files both ways, flapping Kotlin relay, tap-to-add a stranger to a room, ping through a relay, linked devices with either side as the first device (incl. history sync both ways) |

Kotlin unit tests (`android/core/src/test`): NaCl, wire vectors, XEdDSA
vectors, device-set validation.

## Manual labs

```bash
# hub + two bots
cd python
PYTHONPATH=src python -m muninn.sim.hub --decoys 20 &
MUNINN_SIM=127.0.0.1:7420 PYTHONPATH=src python -m muninn.bot --name bob --echo --play --control 7501 &
MUNINN_SIM=127.0.0.1:7420 PYTHONPATH=src python -m muninn.bot --name carol --pos 5,0 --echo &

# GUI on the sim (headless: see CLAUDE.md)
MUNINN_SIM=127.0.0.1:7420 MUNINN_DATA=/tmp/me MUNINN_NAME=me PYTHONPATH=src \
  python -c 'from muninn.gui.main import main; main()'

# drive a bot
echo '{"cmd":"send","to":"me","text":"hi"}' | nc 127.0.0.1 7501
```

### Terminal clients

`tools/tui_lab.sh start` runs a hub, two bots and the curses TUI in a detached
tmux session; `tools/tui_lab.sh keys 'hello' Enter` types, `tools/tui_lab.sh
shot` prints the screen.

`tools/tea_lab.sh` does the same for the Bubble Tea client (builds it from
`tea/`): `start`, `keys`, `shot`, `bot '<botctl args>'`, and `png` for a
colour screenshot (xterm attached to the session on Xvfb `:96`).

### Desktop GUI lab

`tools/gui_lab.sh start` runs a hub, bots bob and carol and the GUI on Xvfb
`:97` with a private D-Bus; `click x y`, `key K`, `type TEXT`, `shot`,
`bot '<botctl args>'`.

### Android emulator on the sim

```bash
nix run .#android-emu &                       # boots emulator-5554
python tools/droid.py install
python tools/droid.py sim kim                 # fresh app, joins 127.0.0.1:7420 via adb reverse
python tools/droid.py tap "bob"; python tools/droid.py type "hello :airplane:"
python tools/droid.py shot /tmp/a.png
```

`tools/droid.py` taps by visible text (uiautomator), types, takes screenshots.
Debug builds also take test hooks as intent extras (`--es backup_export`,
`backup_import`, `ping`, `--ez find true`; see `MainActivity.kt`).

### Real Android Bluetooth between emulators (netsim)

```bash
nix develop --command python tools/netsim_lab.py bench   # boot 2, meet, ping table
nix develop --command python tools/netsim_lab.py up 3    # a, b, c with a fresh app each
nix develop --command python tools/netsim_lab.py meet a b
nix develop --command python tools/netsim_lab.py ping a b 20 200
nix develop --command python tools/netsim_lab.py down
```

The app runs its real `BtTransport` (insecure RFCOMM, discovery, never
pairing). netsim has no notion of range, so relays can't be forced here — use
the sim for multi-hop. If every connect ends in `PAGE_TIMEOUT` (adb logcat),
netsim's state is stale: `down`, kill `netsimd` (`pkill -x netsimd`), `up`
again.

Measured (2026-10-17, netsim, end-to-end RT ping = the game datagram path):
~20 ms median round trip for 0–1000 B datagrams, no loss. netsim's radio is
virtual; real RFCOMM adds air time (expect tens of ms per hop), so turn-based
games are fine anywhere and pong-style games want ≤ 2 hops.

## What isn't covered

- Real RFCOMM on real hardware (BlueZ ↔ Android ↔ Windows). The sim and netsim
  emulators exercise everything above the socket.
- WinRT backend (never run on Windows hardware).
- Android UI beyond manual/scripted emulator runs.
