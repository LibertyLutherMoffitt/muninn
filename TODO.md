Real bugs

1. Empty PROFILE payload poisons display
peers.py:397 _handle_profile → set_name(from_addr, name) — if name == "", stores empty string. Then display_name() returns "" (it's "in names") instead of falling back to MAC. Guard:

if not name:
    return  # or: self.group_store.names.pop(from_addr, None)

2. "X is now known as X" on every connect
peers.py:52 — self.display_name = display_name or local_mac. If user launches without MUNINN_NAME, we broadcast the MAC as the name. Receiver stores MAC string as name; _on_profile fires
AA:BB:CC:DD:EE:FF is now known as AA:BB:CC:DD:EE:FF on every reconnect. Fix: keep display_name empty when unset, only broadcast when non-empty, and suppress _on_profile callback for empty or
 name==addr.

Minor

- clear_override dead code — groups.py:39. No CLI wiring. Either delete or add /nick <peer> (one-arg, MAC-looking) to clear. Weekend scope — fine to leave.
- /nick <peer> <name with spaces> — text.split() splits on whitespace, so multi-word names silently get truncated to first word. Document or accept.
- Tab-complete vs resolve asymmetry — completer iterates conn_mgr.peers (connected only); resolve() searches all known names including disconnected. Typing works, tab doesn't. Minor.

Looks good

- PROFILE explicitly not relayed — documented in PROTOCOL.md, matches _recv_loop dispatch (no forwarding path).
- Overrides win over self-chosen, case-insensitive resolve, upper in pubkeys/names/overrides handles MAC input cleanly.
- set_display_name snapshots targets under peers_lock before iterating — no race.
- Prior review items (seen-mark ordering, relay-queue atomicity, unacked.copy iteration) all still intact after this round.

Recommend fixing #1 and #2 — one-liner each, real UX impact.
