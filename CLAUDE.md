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

- **Static keypairs** — generated once per process, reused across reconnects. Same shared secret every handshake. Intentional for simplicity.
- **No message persistence** — unacked/seen dicts are in-memory. Messages lost on process exit. Intentional.
- **D-Bus pairing via Device1.Pair()** — not bluetoothctl subprocess. Required: subprocess pairing uses store_hint=0 so link keys aren't persisted, causing br-connection-key-missing on ConnectProfile.
- **No forward secrecy** — acceptable for this use case.
- **ACK and message metadata are plaintext** — only message text is encrypted. Intentional for future relay routing.
- **Zeroed group_id for 1:1** — 16 zero bytes. Not a bug.

## Scope

Weekend project for personal use on flights. Don't over-engineer. MITM attacks, key persistence, forward secrecy, and storage limits are explicitly out of scope.

## Docs

- `DESIGN.md` — motivation, decisions, architecture, implementation steps
- `PROTOCOL.md` — wire spec only (the cross-platform contract)
