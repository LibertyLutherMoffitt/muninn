# Muninn — Desktop Client (CLI + GUI)

Encrypted peer-to-peer chat over Bluetooth Classic (RFCOMM). No internet required.

Runs on Linux (BlueZ) and Windows (WinRT). A single Python package, two BT
backends — the right one is picked at import time via `sys.platform`. Two
frontends share the full core: a readline CLI and a Qt6/QML GUI with Vim-modal
editing.

## Linux — quick start

**Prerequisites:** Two Linux devices with Bluetooth; Nix package manager.

```
nix run .#muninn-linux     # CLI
nix run .#muninn-gui       # GUI
```

No flags needed for CLI. Every device both listens and scans continuously. Each discovers nearby Muninn
peers, connects to all of them simultaneously, and maintains independent sessions.
Pairing is handled on first connect; no OS-level pairing setup required.

## Windows — quick start

**Status:** backend written (`muninn/bt/winrt.py`) but not yet hardware-tested.
Treat this as beta — report what breaks.

**Prerequisites:** Python 3.11+; Bluetooth adapter; peer device discoverable
in **Settings → Bluetooth & devices** (Windows has no programmatic
discoverability toggle, unlike Linux).

```powershell
# From the python/ directory
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install .
muninn
```

`pip install .` picks up the `winrt-*` dependency group automatically via
`sys_platform == 'win32'` markers in `pyproject.toml`.

### Standalone .exe (optional)

Bundle everything into one executable for distribution:

```powershell
pip install .[windows-build]
pyinstaller --onefile --name muninn -c python/src/muninn/cli.py
# Output: dist\muninn.exe
```

Ship `muninn.exe` — no Python install required on the target machine.

## Usage

Once running, type `/help` for the full command list:

```
/dm <name|addr>         — switch to DM with the given peer
/group <name>           — switch to a named group
/new <name> <p1> [p2…]  — create a group with currently-connected peers
/nick <name>            — set your own self-chosen display name (broadcast to peers)
/nick <peer> <name>     — set a local-only override for a peer
/nick <peer> ""         — clear a local override
/list                   — list all conversations (DMs + groups)
/peers                  — list connected peers and relay-reachable peers
/known                  — list all peers ever seen, with current status
/history [N]            — show last N messages in the active conversation (default 20)
```

Tab-complete works for peer display names + MAC addresses (under `/dm`, `/new`, `/nick`) and group names (under `/group`).

### Display names

Two knobs:

- **Self-chosen** — set via `/nick <name>` (or the `MUNINN_NAME` env var at launch).
  Broadcast to every peer you connect to. Peers see this as your name by default.
  Persisted to SQLite — survives restarts.
- **Local override** — set via `/nick <peer> <name>`. Per-device, not sent over the
  wire. Overrides what the peer announced. Useful when someone self-names "Odin" but
  you want them shown as "Dave". Persisted to SQLite.

Overrides win on display and when resolving names in commands (e.g. `/dm Dave`).
Both fall back to the MAC address if unset.

Launch with a name pre-set (takes precedence over the persisted name for this session,
without overwriting the persisted value):

```bash
MUNINN_NAME=Josh nix run .#muninn-linux
```

Incoming messages print inline, tagged by conversation:

```
[DM:AA:BB:CC:DD:EE:FF] < hey
[Flight] < BB:CC:DD:EE:FF:00: anyone want coffee?
```

Outbound messages show status icons as they progress:

```
[DM:AA:BB:CC:DD:EE:FF] > hello
                                                                         ⧗
                                                          ✓ AA:BB:CC:DD:EE:FF
                                                         ✓✓ AA:BB:CC:DD:EE:FF
```

(`⧗` = pending; `✓` = delivered/ACKed; `✓✓` = read; each status line is right-aligned)

Ctrl+C to quit.

## Dev Shell

```
nix develop
python -m muninn.cli --help
```

## What works today

- RFCOMM connect/listen via BlueZ D-Bus (`org.bluez.ProfileManager1`) — Linux
- RFCOMM connect/listen via WinRT (`RfcommServiceProvider` / `StreamSocket`) — Windows
- Automatic pairing — `org.bluez.Device1.Pair()` on Linux,
  `DeviceInformation.Pairing.Custom.PairAsync()` on Windows; both use Just Works /
  NoInputNoOutput and require no OS pairing UI
- X25519 key exchange + XSalsa20-Poly1305 encryption (NaCl Box)
- Simultaneous multi-peer connections — each peer has independent socket/box/recv thread
- Continuous scan + acceptor threads — peers find and reconnect to each other automatically
- Periodic BT cache refresh (every ~2 min) to keep UUID discovery current
- 1:1 DMs and named groups (up to 6 members); any peer can create a group
- Pairwise E2EE per recipient — a group message is encrypted separately for each member
- Relay — frames destined for unreachable peers are forwarded by connected intermediaries
  and queued if no path exists yet
- End-to-end ACKs (flood-back through all peers) — sender sees delivery per recipient
- Read receipts (`✓✓`) when the recipient switches to the conversation
- Resend of unacked messages on peer reconnect
- Receiver deduplicates by message ID
- MAC tiebreaker (higher MAC defers 10s) to avoid simultaneous `ConnectProfile` deadlocks
- **SQLite persistence** — messages, pubkeys, groups, display names, unacked state, and
  dedup table survive restarts. Keypair persisted to identity table (same shared secret
  with each peer across restarts). DB stored at `~/.local/share/muninn/muninn.db` on
  Linux, `%APPDATA%\muninn\muninn.db` on Windows.
- Message history via `/history [N]`

## What doesn't exist yet

- Android client
- Windows backend hardware-tested (`bt/winrt.py` is written but not yet validated on
  real Windows hardware — expect rough edges)

## Troubleshooting

**`No Muninn service found`** — the other device isn't running Muninn yet, or the OS
BT stack hasn't cached its SDP record. Start Muninn on both devices; they will find each
other. On Windows, ensure the peer is discoverable in **Settings → Bluetooth & devices**.

**`No Bluetooth adapter found`** — Linux: `bluetoothctl show` to verify adapter is
present and powered on. Windows: check Device Manager and confirm Bluetooth is enabled
in Settings.

**Windows pairing hangs** — Windows requires the peer to be either discoverable or
already in its known-devices list. Make the peer discoverable (Settings) before first
connect; subsequent connects work in the background.
