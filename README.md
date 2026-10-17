<div align="center">

# 🪶 Muninn

*Memory. Encrypted. Carried on dark wings.*

</div>

> *Muninn* (Old Norse: *memory*, *mind*) — one of Óðinn's two ravens. Each dawn he flies out over all the worlds; each dusk he returns and whispers what he has seen. Óðinn fears Muninn's loss above all else — that memory might not come back.

---

## What is this?

**Encrypted chat over Bluetooth, for when there is no network** — airplane
cabins first of all. No internet, no cell signal, no Wi-Fi, no accounts.

Phones and laptops a few rows apart find each other, and every device also
relays for the others, so a message to someone at the back of the cabin hops
seat to seat until it gets there — or waits, safely stored, until a path
appears. Everything is end-to-end encrypted.

- 1:1 and group chats; replies, reactions, edit / delete, typing indicators
- delivery ✓ and read ✓✓ receipts that work across relays
- pictures, GIFs and any file (resumable, verified, even through relays)
- 25 animated stickers built in, plus any GIF you save — no internet needed
- emoji picker and `:shortcodes:`
- games: chess, checkers, connect four, reversi, dots & boxes (2-4 players),
  tic-tac-toe and real-time pong — any game is one JavaScript file, and the
  code travels with the invite, so the other side doesn't need it installed
- presence: *connected* / *via a relay* / *nearby, not connecting* / *last seen*
- linked devices: your phone and laptop act as one person (`:link` shows a
  code, the other device joins); chats, rooms and games follow you to both
- backup and restore (`:export` / Settings → Backup), photos shrunk before
  sending, light and dark themes
- survives flaky links, app restarts, reinstalls and people walking down the
  aisle; a good Bluetooth citizen (never pairs with or prompts strangers'
  devices, never touches headsets or watches)

---

## Clients

| Client | Platform | UI | Status |
|--------|----------|----|--------|
| Desktop GUI | Linux (Windows backend untested) | Qt 6 / QML, Vim-modal composer | working |
| Terminal | Linux (Windows) | full-screen TUI (curses), `/commands`; `--plain` line mode | working |
| Android | Android 12+ | Jetpack Compose | working |
| Headless bot | anywhere | JSON control port | testing tool |

The Python CLI, GUI and bot share one core (`python/src/muninn/`). The Android
app runs a Kotlin port of the same core (`android/core/`), which is also built
for the JVM so the two implementations are tested against each other.

---

## Install

Download from the [latest release](https://github.com/LibertyLutherMoffitt/muninn/releases/latest):

| | File | Notes |
|---|---|---|
| Android 12+ | `muninn-android.apk` | install once (allow "install unknown apps"); after that Muninn updates itself — *Settings → Updates* |
| Linux | `muninn-gui-linux-x86_64`, `muninn-linux-x86_64` | `chmod +x`, run; GUI and terminal client, no Python needed (glibc 2.35+, BlueZ) |
| Windows | `muninn-gui-windows-x86_64.exe`, `muninn-windows-x86_64.exe` | Bluetooth backend not yet tested on hardware |
| Nix | `nix run github:LibertyLutherMoffitt/muninn#muninn-gui` | or `#muninn-linux` for the terminal |

Releases are cut with `tools/release.sh X.Y.Z "notes"` (signed APK + tag +
GitHub release; CI attaches the desktop builds).

## Quick start (from source)

```bash
nix run .#muninn-gui              # desktop GUI
nix run .#muninn-linux            # terminal client (TUI; --plain for line mode)
```

Android: `nix develop`, then `cd android && ./gradlew :app:assembleDebug` and
`adb install -r app/build/outputs/apk/debug/app-debug.apk`. See
[`android/README.md`](android/README.md).

Start Muninn on two devices near each other. Laptops find each other and
phones automatically; a phone that has never met anyone may need *Settings →
Make me visible* once. After the first meeting everything reconnects by itself.

**No second device?** Run a whole simulated cabin on one machine — see
[`TESTING.md`](TESTING.md):

```bash
nix develop
cd python && PYTHONPATH=src python -m muninn.sim.hub &          # virtual radio
MUNINN_SIM=127.0.0.1:7420 PYTHONPATH=src python -m muninn.bot --name bob --echo --play &
MUNINN_SIM=127.0.0.1:7420 MUNINN_DATA=/tmp/me nix run .#muninn-gui
```

---

## How it works

- **Transport:** Bluetooth Classic RFCOMM (or the simulator).
- **Crypto:** NaCl box — X25519 + XSalsa20-Poly1305. Static keys, no forward
  secrecy (deliberate). Routing metadata (sender, recipient, ids) is plaintext
  so relays can route; message content never is.
- **Mesh:** distance-vector routing, persistent outbox with retries until an
  end-to-end ACK, store-and-forward at relays, keepalive dead-link detection,
  gossip of names and keys over any number of hops.

Wire spec: [`PROTOCOL.md`](PROTOCOL.md). Architecture: [`DESIGN.md`](DESIGN.md).
Game API: [`python/src/muninn/games/README.md`](python/src/muninn/games/README.md).

---

## Windows — install & run GUI

Requires Python 3.11+ ([download](https://www.python.org/downloads/) — check "Add Python to PATH" during install).

Download and double-click [`install-gui.bat`](install-gui.bat). It installs Muninn + PySide6 and launches the GUI. The WinRT Bluetooth backend has not been tested on hardware yet.

---

## Security model

Messages, files, reactions and game moves are end-to-end encrypted per
recipient. Keys are pinned on first contact (trust on first use); a reinstall
is recognised and handled. No PKI, so a man-in-the-middle at first contact is
possible — out of scope for an in-flight chat tool.

---

## Licenses

Muninn is **MIT** (see [`LICENSE`](LICENSE)). It dynamically links Qt 6 and
PySide6, both **LGPL-3.0**. The full third-party attribution and LGPL
compliance notes live in [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

The GUI's default UI font is [JetBrains Mono](https://www.jetbrains.com/lp/mono/)
(SIL OFL-1.1). Muninn does not bundle it — Qt loads it from the system if
present and falls back to whatever the OS provides for the Monospace style
hint.

---

## Roadmap

See [`TODO.md`](TODO.md): Windows hardware validation, NFC tap-to-pair,
linked devices (phone + laptop as one person), a full-screen TUI.

---

## Name

Muninn (Old Norse: *memory*, *mind*) is one of the two ravens sent out by Óðinn each day. Huginn (*thought*) and Muninn fly over all the worlds and return to whisper what they have seen. Óðinn worries more for Muninn — that memory might not return.

Your messages, carried on short wings, between two ravens in the dark.

---

*Weekend project. Personal use. Don't over-engineer it.* 🪶
