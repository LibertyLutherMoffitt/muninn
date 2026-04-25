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

## Intentional Decisions (don't "fix" these)

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
- `GUI_PLAN.md` — GUI design, Vim keybindings, layout, milestones

## Key files (Python client)

- `peers.py` — `ConnectionManager`: all BT connections, relay, ACKs, read receipts
- `groups.py` — `GroupStore`: in-memory cache of peers/groups/names, write-through to `Storage`
- `storage.py` — `Storage`: SQLite persistence layer, schema migrations
- `protocol.py` — wire encoding/decoding
- `cli.py` — readline CLI + `ChatUI` (uses `/`-prefixed commands)
- `bt/bluez.py` — BlueZ D-Bus backend
- `bt/winrt.py` — WinRT backend (written, not yet hardware-tested)
- `gui/main.py` — GUI entrypoint, QML engine, theme dict, default font (JetBrains Mono)
- `gui/bridge.py` — `ChatBridge`: Qt signals ↔ `ConnectionManager` callbacks; also the
  single command dispatcher (`runCommand`) and tab-completion engine (`completeCommand`)
  shared by the vim cmdline (`:`) and the `<space>f` palette
- `gui/vim.py` — `VimEditor`: modal text editor state machine — motions, operators,
  text objects, registers (linewise vs charwise), counts, dot repeat, cmdline
- `gui/models.py` — `PeerListModel`, `MessageListModel`
- `gui/qml/Main.qml` — window, status bar, global shortcuts, overlay wiring
- `gui/qml/CommandPalette.qml` — `<space>f` fuzzy palette with raw `:` mode
- `gui/qml/InfoMenu.qml` — popup used by `:list` / `:peers` / `:known` / `:help`
- `gui/qml/ChatView.qml` — top-to-bottom message list, auto-scroll, bubble delegate

## GUI command surface (don't drift from this)

The GUI uses `:`-prefixed commands; both vim cmdline and the palette route
through `bridge.runCommand`. Adding a new command means:
1. Add a branch in `runCommand` (and to `_HELP` and `_COMMANDS` for tab completion).
2. If it should appear in palette suggestions, add an entry in
   `CommandPalette.qml`'s `filterModel`.
3. If it returns text/lists, emit `infoMenuRequested(title, items)`; otherwise emit
   `notify` for success and `errorOccurred` for failure.

CLI commands stay `/`-prefixed in `cli.py` — no plan to unify.
