# Muninn — Claude Context

## Build & Dev

```bash
nix develop                       # enter dev shell (installs prek hook automatically)
nix run .#muninn-linux -- --help  # CLI
nix run .#muninn-gui              # Qt6/QML GUI
```

Use `nix run` not `nix build` — avoids creating `result/` symlink in repo root.

**Untracked files are excluded from the nix build.** Flakes only see the git
tree; new QML files (or any new source) must be `git add`-ed before
`nix build`/`nix run` will pick them up. Bump `version` in both
`python/pyproject.toml` and `flake.nix` whenever you need to invalidate the
build cache.

### Linting

Run all pre-commit hooks (ruff format, ruff check, ty check, alejandra, nix flake check):

```bash
nix develop --command prek run --all-files
```

Don't run individual linters — `prek` runs the full suite as configured in `.pre-commit-config.yaml`.

Pre-commit hooks run automatically via prek, which enters the nix dev shell itself — commits work from anywhere.

### Headless testing (over ssh, no display)

```bash
nix run .#gui-headless &                 # GUI on Xvfb :98, throwaway data dir
DISPLAY=:98 xdotool type ':help'         # drive it (xdotool in dev shell)
DISPLAY=:98 import -window root out.png  # screenshot

nix run .#android-emu &                  # headless KVM emulator (API 34 x86_64)
adb install -r -g android/app/build/outputs/apk/debug/app-debug.apk
adb shell monkey -p com.muninn -c android.intent.category.LAUNCHER 1
adb exec-out screencap -p > out.png      # drive with `adb shell input tap/text`
adb emu kill
```

Xvfb has no usable GLX, so `gui-headless` sets `QT_QUICK_BACKEND=software`.
The headless GUI still owns the host's real BT adapter when it is the writer.
Emulator Bluetooth is virtual (netsim) and can't reach the host adapter, but
emulators on the same host share it: `MUNINN_AVD=b nix run .#android-emu --
-port 5556` boots a second independent device (use `adb -s emulator-5556`).
Make the target discoverable (`am start -a
android.bluetooth.adapter.action.REQUEST_DISCOVERABLE`, tap Allow), then
pair/chat between them over real RFCOMM.

## Intentional Decisions (don't "fix" these)

- **Static keypairs** — generated once per install, persisted, pinned by peers (TOFU). Reinstalls are detected and migrated, not trusted blindly.
- **SQLite write-through persistence** — messages, pubkeys, groups, display names, unacked state, and seen-dedup are all persisted via `storage.py`. WAL mode, `threading.Lock` serialization. The DB file growing is not a bug; pruning is out of scope.
- **D-Bus pairing via Device1.Pair()** — not bluetoothctl subprocess. Required: subprocess pairing uses store_hint=0 so link keys aren't persisted, causing br-connection-key-missing on ConnectProfile.
- **No forward secrecy** — acceptable for this use case.
- **Routing metadata is plaintext** — src/dst/ids/ACKs; bodies are encrypted. Needed for relaying.
- **Good Bluetooth citizen** — never pair with, prompt, or dial devices that aren't (possibly) Muninn; headsets/watches are never contacted.

## Scope

Weekend project for personal use on flights. Don't over-engineer. MITM attacks, forward secrecy, and storage limits (no DB pruning) are explicitly out of scope.

## Docs

- `DESIGN.md` — motivation, decisions, architecture, implementation steps
- `PROTOCOL.md` — wire spec v2 (the cross-platform contract)
- `TESTING.md` — test suites, simulated cabin, emulator labs
- `TODO.md` — gap analysis, plan, progress log, backlog
- `python/src/muninn/games/README.md` — game plugin API
- `python/src/muninn/gui/GUI_PLAN.md` — GUI design, Vim keybindings, layout, milestones

## Key files

Python (`python/src/muninn/`) — shared by CLI, GUI, bot, tests:
- `protocol.py` / `content.py` — wire v2 frames / encrypted body kinds (see PROTOCOL.md)
- `mesh.py` — `Mesh`: links, routing, outbox/retries, relaying, gossip, groups, files, apps, reinstalls
- `node.py` — `Node`: identity + storage + mesh + transport, dialer, discovery, presence
- `storage.py` — SQLite (schema migrations in `_migrate`)
- `transport.py` (`BtTransport`, `ScanResult`), `bt/bluez.py`, `bt/winrt.py`
- `scanpolicy.py` — eager/balanced/quiet (mirror: `android/core/.../ScanPolicy.kt`, tested)
- `gamehost.py` + `games/*.js` — generic game sessions + plugins (API in games/README.md)
- `emoji.py` + `assets/emoji.json`, `assets/stickers/*.gif` — shared with Android
- `sim/hub.py`, `sim/transport.py`, `sim/lab.py` — radio simulator; `bot.py` — headless node
- `cli.py`; `gui/` (`bridge.py` command dispatcher, `models.py`, `vim.py`, `qml/`)

Kotlin:
- `android/core/` — pure-JVM port of the core (`Nacl.kt` TweetNaCl, `Protocol.kt`,
  `Mesh.kt`, `Node.kt`, `Storage.kt`, `GameHost.kt`, `SimTransport.kt`, `SimNode.kt`)
- `android/app/` — `Runtime.kt` (process-wide node), `MuninnService.kt`,
  `BtTransport.kt`, `AndroidStorage.kt`, Compose screens, `assets/gamehost.html`

Any protocol or behaviour change must land in **both** cores; `tests/test_interop.py`
and the parity tests catch drift. See TESTING.md.

### Sim + Android emulator

`tools/droid.py sim <name>` restarts the app in simulator mode joined to the hub on
127.0.0.1:7420 through `adb reverse` (the emulator's own network route is unreliable).

## GUI command surface (don't drift from this)

The GUI uses `:`-prefixed commands; both vim cmdline and the palette route
through `bridge.runCommand`. Adding a new command means:
1. Add a branch in `runCommand` (and to `_HELP` and `_COMMANDS` for tab completion).
2. If it should appear in palette suggestions, add an entry in
   `CommandPalette.qml`'s `filterModel`.
3. If it returns text/lists, emit `infoMenuRequested(title, items)`; otherwise emit
   `notify` for success and `errorOccurred` for failure.

CLI commands stay `/`-prefixed in `cli.py` — no plan to unify.
