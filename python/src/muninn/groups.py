from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from muninn.storage import Storage


@dataclass
class Group:
    group_id: bytes
    members: dict[str, bytes]  # addr -> pubkey_bytes
    name: str


class GroupStore:
    """In-memory cache of peer/group state with write-through to Storage.

    All reads hit the in-memory dicts (hot path). All mutations update both
    the dict and the underlying DB. On construction, the DB is loaded into
    memory so the CLI/GUI can share state across restarts.
    """

    def __init__(self, storage: "Storage | None" = None):
        self.storage = storage
        self.groups: dict[bytes, Group] = {}
        self.pubkeys: dict[str, bytes] = {}  # addr -> pubkey (global registry)
        # Display names. `names` holds the peer's self-chosen name (received
        # via PROFILE frames). `overrides` wins over `names` for a given addr,
        # letting the local user rename a peer regardless of what the peer
        # broadcasts.
        self.names: dict[str, str] = {}
        self.overrides: dict[str, str] = {}

        if storage is not None:
            for mac, pubkey, name, override in storage.load_peers():
                self.pubkeys[mac] = pubkey
                if name:
                    self.names[mac] = name
                if override:
                    self.overrides[mac] = override
            for group in storage.load_groups():
                self.groups[group.group_id] = group

    def add_group(self, group: Group) -> None:
        self.groups[group.group_id] = group
        # Only populate pubkeys for members we haven't yet handshaken with.
        # A known pubkey came from a direct handshake and is authoritative;
        # overwriting it with a value forwarded in a plaintext GROUP_SETUP
        # would let any group creator redirect our encryption for that peer.
        for addr, pubkey in group.members.items():
            if addr not in self.pubkeys:
                self.pubkeys[addr] = pubkey
                if self.storage is not None:
                    self.storage.save_peer_pubkey_if_missing(addr, pubkey)
        if self.storage is not None:
            self.storage.save_group(group)

    def add_pubkey(self, addr: str, pubkey: bytes) -> None:
        # Always wins: direct handshake is the source of truth for a peer's
        # pubkey. Group-setup seeding uses setdefault; this one overwrites.
        self.pubkeys[addr] = pubkey
        if self.storage is not None:
            self.storage.save_peer_pubkey(addr, pubkey)

    def get_pubkey(self, addr: str) -> bytes | None:
        return self.pubkeys.get(addr)

    def set_name(self, addr: str, name: str) -> None:
        self.names[addr] = name
        if self.storage is not None:
            self.storage.set_peer_name(addr, name)

    def set_override(self, addr: str, name: str) -> None:
        self.overrides[addr] = name
        if self.storage is not None:
            self.storage.set_peer_override(addr, name)

    def clear_override(self, addr: str) -> None:
        self.overrides.pop(addr, None)
        if self.storage is not None:
            self.storage.clear_peer_override(addr)

    def display_name(self, addr: str) -> str:
        if addr in self.overrides:
            return self.overrides[addr]
        if addr in self.names:
            return self.names[addr]
        return addr

    def resolve(self, name_or_addr: str) -> str | None:
        """Map a display name (override or self-chosen) back to an addr.

        Falls through to MAC-address match if the input looks like a MAC.
        Overrides win over self-chosen names on collision. Case-insensitive.
        """
        upper = name_or_addr.upper()
        if upper in self.pubkeys or upper in self.names or upper in self.overrides:
            return upper
        lower = name_or_addr.lower()
        for addr, n in self.overrides.items():
            if n.lower() == lower:
                return addr
        for addr, n in self.names.items():
            if n.lower() == lower and addr not in self.overrides:
                return addr
        return None
