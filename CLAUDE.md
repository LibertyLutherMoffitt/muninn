# Muninn — Claude Context

## Build & Dev

```bash
nix develop          # enter dev shell (installs prek hook automatically)
nix run .#muninn-linux -- --help
```

Use `nix run` not `nix build` — avoids creating `result/` symlink in repo root.

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

## Key files (Python client)

- `peers.py` — `ConnectionManager`: all BT connections, relay, ACKs, read receipts
- `groups.py` — `GroupStore`: in-memory cache of peers/groups/names, write-through to `Storage`
- `storage.py` — `Storage`: SQLite persistence layer, schema migrations
- `protocol.py` — wire encoding/decoding
- `cli.py` — readline CLI + `ChatUI`
- `bt/bluez.py` — BlueZ D-Bus backend
