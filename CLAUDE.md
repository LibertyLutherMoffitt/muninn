# Muninn — Claude Context

## Build & Dev

```bash
nix develop                       # enter dev shell (installs prek hook automatically)
nix run .#muninn-linux -- --help  # CLI
nix run .#muninn-gui              # Qt6/QML GUI
nix run .#muninn-tea              # Bubble Tea terminal client (Go, tea/)
```

Use `nix run` not `nix build` — avoids creating `result/` symlink in repo root.

**Untracked files are excluded from the nix build.** Flakes only see the git
tree; new QML files (or any new source) must be `git add`-ed before
`nix build`/`nix run` will pick them up. Bump `version` in both
`python/pyproject.toml` and `flake.nix` whenever you need to invalidate the
build cache. Changing Go dependencies (`tea/go.sum`) also means updating
`vendorHash` of `muninn-tea` in `flake.nix` (set `pkgs.lib.fakeHash`, build,
copy the "got:" hash).

### Linting

Run all pre-commit hooks (ruff format, ruff check, ty check, alejandra, nix flake check, gofmt, go vet + test):

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

tools/tea_lab.sh start                   # hub + bots + Bubble Tea client in tmux
tools/tea_lab.sh keys 'hi' Enter; tools/tea_lab.sh shot   # drive / text capture
tools/tea_lab.sh png                     # colour screenshot (xterm on Xvfb :96)
python tools/netsim_lab.py bench         # two emulators over real Android BT (netsim)

nix run .#android-emu &                  # headless KVM emulator (API 34 x86_64)
adb install -r -g android/app/build/outputs/apk/debug/app-debug.apk
adb shell monkey -p com.muninn -c android.intent.category.LAUNCHER 1
adb exec-out screencap -p > out.png      # drive with `adb shell input tap/text`
adb emu kill
```

Xvfb has no usable GLX, so `gui-headless` sets `QT_QUICK_BACKEND=software`
(the GUI also falls back to it by itself when OpenGL can't start — e.g. the
flake's Mesa differs from the host's driver). Only one front end may run per
data dir (`muninn/instance.py`, taken by `Node`); the TUI logs to
`<data dir>/muninn.log`.
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
- **Muninn never pairs** (and never sets Trusted, never registers a pairing agent). A bond with a phone made Android use the laptop as speakers; Muninn's RFCOMM profile is open on both sides, so links don't need one — if a laptop can't dial a phone unbonded, the phone dials the laptop. Old bonds: `:unpair` / `/unpair`; Trusted is cleared on start.
- **No forward secrecy** — acceptable for this use case.
- **Linked devices keep separate keys** — a device set (signed by the first
  device + each member's consent, XEdDSA with the X25519 keys) maps devices
  to a person; storage and UIs speak person ids (`mesh.me`, `mesh.person()`),
  transport stays per device (`mesh.node_id`). Compare senders with `me`.
- **One core, many front ends** — the Bubble Tea client is Go but holds no
  protocol logic: it runs `muninn --api` (stdio JSON, `api.py`).
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
- `scanpolicy.py` — find mode (temporary scanning for new people) + reconnect speed fast/saver (mirror: `android/core/.../ScanPolicy.kt`, tested)
- `devices.py` + `xeddsa.py` — linked-device sets, signatures (mirrors: `Devices.kt`, `Ed25519.kt`; vectors in `spec/`)
- `backup.py` — backup zip (also the history-sync format; Kotlin: `HistorySync.kt`, app `Backup.kt`)
- `media.py` — photo shrinking before sending; `api.py` — stdio JSON API for `tea/`
- `gamehost.py` + `games/*.js` — generic game sessions + plugins (API in games/README.md)
- `emoji.py` + `assets/emoji.json`, `assets/stickers/*.gif` — shared with Android
- `sim/hub.py`, `sim/transport.py`, `sim/lab.py` — radio simulator; `bot.py` — headless node
- `cli.py` (line mode + shared `/command` handling, `--api`), `tui.py` (curses full-screen, default on a TTY); `gui/` (`bridge.py` command dispatcher, `models.py`, `vim.py`, `qml/`)

Go (`tea/`): Bubble Tea client — `core.go` (spawns the core), `model.go`
(state, keys), `view.go` (rendering), `palette.go` (Ctrl+K), `theme.go`.

Kotlin:
- `android/core/` — pure-JVM port of the core (`Nacl.kt` TweetNaCl, `Ed25519.kt`
  XEdDSA, `Protocol.kt`, `Mesh.kt`, `Devices.kt`, `HistorySync.kt`, `Node.kt`,
  `Storage.kt`, `GameHost.kt`, `SimTransport.kt`, `SimNode.kt`)
- `android/app/` — `Runtime.kt` (process-wide node), `MuninnService.kt`,
  `BtTransport.kt`, `AndroidStorage.kt`, `TapPair.kt` (NFC), `Updater.kt`,
  `Backup.kt`, `Media.kt`, Compose screens, `assets/gamehost.html`

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

CLI commands stay `/`-prefixed in `cli.py` — no plan to unify. The Bubble Tea
client gets every `/command` for free (it runs them through `api.py`); add
common ones to `paletteCommands` in `tea/palette.go`.
