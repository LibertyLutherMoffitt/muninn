# Muninn — Linux Client

Encrypted peer-to-peer chat over Bluetooth Classic (RFCOMM). No internet required.

## Prerequisites

- Two Linux devices with Bluetooth
- Nix package manager

## Run

```
nix run .#muninn-linux
```

No flags. Every device both listens and scans continuously. Each discovers nearby Muninn
peers, connects to all of them simultaneously, and maintains independent sessions.
Pairing is handled on first connect; no OS-level pairing setup required.

## Usage

Once running, type `/help` for the full command list:

```
/dm <name|addr>         — switch to DM with the given peer
/group <name>           — switch to a named group
/new <name> <p1> [p2…]  — create a group with currently-connected peers
/nick <name>            — set your own self-chosen display name (broadcast to peers)
/nick <peer> <name>     — set a local-only override for a peer
/list                   — list all conversations (DMs + groups)
/peers                  — list connected peers
```

Tab-complete works for peer display names + MAC addresses (under `/dm`, `/new`, `/nick`) and group names (under `/group`).

### Display names

Two knobs:

- **Self-chosen** — set via `/nick <name>` (or the `MUNINN_NAME` env var at launch).
  Broadcast to every peer you connect to. Peers see this as your name by default.
- **Local override** — set via `/nick <peer> <name>`. Per-device, not sent over the
  wire. Overrides what the peer announced. Useful when someone self-names "Odin" but
  you want them shown as "Dave".

Overrides win on display and when resolving names in commands (e.g. `/dm Dave`).
Both names fall back to the MAC address if unset. In-memory only — lost on exit.

Launch with a name pre-set:

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
  ⧗ sent
  ✓ AA:BB:CC:DD:EE:FF       ← delivered (recipient ACKed)
  ✓✓ AA:BB:CC:DD:EE:FF      ← read (recipient viewed the conversation)
```

Ctrl+C to quit.

## Dev Shell

```
nix develop
python -m muninn.cli --help
```

## What works today

- RFCOMM connect/listen via BlueZ D-Bus (`org.bluez.ProfileManager1`)
- Automatic pairing via `org.bluez.Device1.Pair()` — no OS UI needed
- X25519 key exchange + XSalsa20-Poly1305 encryption (NaCl Box)
- Simultaneous multi-peer connections — each peer has independent socket/box/recv thread
- Continuous scan + acceptor threads — peers find and reconnect to each other automatically
- 1:1 DMs and named groups (up to 6 members); any peer can create a group
- Pairwise E2EE per recipient — a group message is encrypted separately for each member
- Relay — frames destined for unreachable peers are forwarded by connected intermediaries
  and queued if no path exists yet
- End-to-end ACKs (flood-back through all peers) — sender sees delivery per recipient
- Read receipts (`✓✓`) when the recipient switches to the conversation
- Resend of unacked messages on peer reconnect
- Receiver deduplicates by message ID
- MAC tiebreaker (higher MAC defers 10s) to avoid simultaneous `ConnectProfile` deadlocks

## What doesn't exist yet

- GUI (Qt6/QML planned)
- Message persistence (in-memory only — messages, known pubkeys, and display names lost on process exit)
- Android client

## Troubleshooting

**`No Muninn service found`** — the other device isn't running Muninn yet, or BlueZ
hasn't cached its SDP record. Start Muninn on both devices; they will find each other.

**`No Bluetooth adapter found`** — check `bluetoothctl show` to verify adapter exists
and is powered on.
