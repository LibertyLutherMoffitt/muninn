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

### Testing

```bash
cd python && python -m pytest tests/ -q          # unit + full-stack integration
cd spec/kotlin-conformance && gradle test        # wire conformance, no Android SDK needed
```

Run both after touching anything in `protocol.py`, `Protocol.kt`, `peers.py` or
`PeerBook.kt` — `spec/wire-vectors.json` is the shared fixture that keeps the
desktop and Android clients speaking the same protocol, and only the two suites
together catch a divergence. Regenerate it with `python3 spec/generate_vectors.py`
**only** when `PROTOCOL.md` changes on purpose.

`MUNINN_BT_BACKEND=loopback` runs the CLI or GUI over TCP with no Bluetooth, so
two clients can talk on one machine. See `TESTING.md` for the environment
variables, including `MUNINN_LOOPBACK_GHOSTS` for faking a device that is
visible but unreachable.

### Linting

Run all pre-commit hooks (ruff format, ruff check, ty check, alejandra, nix flake check):

```bash
nix develop --command prek run --all-files
```

Don't run individual linters — `prek` runs the full suite as configured in `.pre-commit-config.yaml`.

Pre-commit hooks run automatically via prek, which enters the nix dev shell itself — commits work from anywhere.

## Intentional Decisions (don't "fix" these)

- **The scanner dials devices that never advertised the Muninn UUID.** Adapter
  service caches are unreliable — BlueZ routinely omits 128-bit UUIDs from
  inquiry EIR, and only browses SDP after a pair or connect — so filtering on
  them loses peers permanently. Probing is rationed by `dialer.py`, not removed.

- **Static keypairs** — generated once, persisted to the SQLite `identity` table, reused across restarts and reconnects. Same shared secret every handshake. Intentional for simplicity.
- **SQLite write-through persistence** — messages, pubkeys, groups, display names, unacked state, and seen-dedup are all persisted via `storage.py`. WAL mode, `threading.Lock` serialization. The DB file growing is not a bug; pruning is out of scope.
- **D-Bus pairing via Device1.Pair()** — not bluetoothctl subprocess. Required: subprocess pairing uses store_hint=0 so link keys aren't persisted, causing br-connection-key-missing on ConnectProfile.
- **No forward secrecy** — acceptable for this use case.
- **ACK and message metadata are plaintext** — only message text is encrypted. Intentional for future relay routing.
- **Zeroed group_id for 1:1** — 16 zero bytes. Not a bug.

## Scope

Weekend project for personal use on flights. Don't over-engineer. MITM attacks, forward secrecy, and storage limits (no DB pruning) are explicitly out of scope.

## Docs

- `DESIGN.md` — motivation, decisions, architecture, implementation steps
- `PROTOCOL.md` — wire spec only (the cross-platform contract)
- `TESTING.md` — how to run each layer, and how to run the apps without hardware
- `docs/REVIEW.md` — cross-platform review; Linux vs Windows behavioural differences
- `python/src/muninn/gui/GUI_PLAN.md` — GUI design, Vim keybindings, layout, milestones

## Key files (Python client)

- `discovery.py` — the accept/scan loops, shared by CLI and GUI (do not re-copy
  them into an entry point; they drifted last time)
- `dialer.py` — `DialScheduler`: who to dial next and when to give up. Mirrored
  by `DialScheduler.kt`; a conformance test compares the two
- `scanpolicy.py` — aggressive / balanced / conservative presets. Mirrored by
  `ScanPolicy.kt`, and the numbers are compared by that same test
- `presence.py` — `PresenceTracker`: connected / relay / nearby / offline per peer

- `peers.py` — `ConnectionManager`: all BT connections, relay, ACKs, read receipts
- `groups.py` — `GroupStore`: in-memory cache of peers/groups/names, write-through to `Storage`
- `storage.py` — `Storage`: SQLite persistence layer, schema migrations
- `protocol.py` — wire encoding/decoding
- `cli.py` — readline CLI + `ChatUI` (uses `/`-prefixed commands)
- `bt/bluez.py` — BlueZ D-Bus backend
- `bt/winrt.py` — WinRT backend (written, not yet hardware-tested)
- `bt/loopback.py` — TCP backend for running and testing without a radio
- `gui/main.py` — GUI entrypoint, QML engine, `_THEME` design tokens, default font
- `gui/bridge.py` — `ChatBridge`: Qt signals ↔ `ConnectionManager` callbacks; also the
  single command dispatcher (`runCommand`) and tab-completion engine (`completeCommand`)
  shared by the vim cmdline (`:`) and the `<space>f` palette
- `gui/vim.py` — `VimEditor`: modal text editor state machine — motions, operators,
  text objects, registers (linewise vs charwise), counts, dot repeat, cmdline
- `gui/models.py` — `PeerListModel`, `MessageListModel`
- `gui/qml/Main.qml` — window, status bar, global shortcuts, overlay wiring
- `gui/qml/CommandPalette.qml` — `<space>f` fuzzy palette with raw `:` mode
- `gui/qml/InfoMenu.qml` — popup used by `:list` / `:peers` / `:known` / `:help`
- `gui/qml/ChatView.qml` — presence header, day dividers, message runs, bubble delegate
- `gui/qml/PresenceDot.qml`, `PillButton.qml`, `EmptyState.qml` — shared pieces; use
  these rather than re-styling a Rectangle in place

## GUI style rules

- **Every colour, size and radius comes from `Theme`** (the `_THEME` dict in
  `gui/main.py`). A hex literal in a `.qml` file fails `test_gui_theme.py`.
- **Never declare a QML property called `state`** — it shadows the built-in
  `Item.state` and silently binds the wrong value. Same test catches it.
- Presence is drawn by `PresenceDot` in both the sidebar and the chat header;
  the two disagreeing about one peer reads as a bug, not as two views.
- Don't read a sibling binding (`isDm`, `peerAddr`) inside an `onXChanged`
  handler — the handler can run before those re-evaluate. Derive from the
  property that changed.

## GUI command surface (don't drift from this)

The GUI uses `:`-prefixed commands; both vim cmdline and the palette route
through `bridge.runCommand`. Adding a new command means:
1. Add a branch in `runCommand` (and to `_HELP` and `_COMMANDS` for tab completion).
2. If it should appear in palette suggestions, add an entry in
   `CommandPalette.qml`'s `filterModel`.
3. If it returns text/lists, emit `infoMenuRequested(title, items)`; otherwise emit
   `notify` for success and `errorOccurred` for failure.

CLI commands stay `/`-prefixed in `cli.py` — no plan to unify.

## Android client

`Protocol.kt` and `PeerBook.kt` deliberately import nothing from `android.*` so
`spec/kotlin-conformance` can compile and unit-test them on a plain JVM. Keep
them that way: they hold the rules that must match the desktop client, and rules
that cannot be tested drift. Anything needing Android APIs belongs in
`PeerSession.kt`, `MuninnService.kt` or `MainActivity.kt`, which need the SDK to
build and are not covered by tests.
