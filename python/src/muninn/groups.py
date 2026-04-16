from dataclasses import dataclass


@dataclass
class Group:
    group_id: bytes
    members: dict[str, bytes]  # addr -> pubkey_bytes
    name: str


class GroupStore:
    def __init__(self):
        self.groups: dict[bytes, Group] = {}
        self.pubkeys: dict[str, bytes] = {}  # addr -> pubkey (global registry)
        # Display names. `names` holds the peer's self-chosen name (received
        # via PROFILE frames). `overrides` wins over `names` for a given addr,
        # letting the local user rename a peer regardless of what the peer
        # broadcasts.
        self.names: dict[str, str] = {}
        self.overrides: dict[str, str] = {}

    def add_group(self, group: Group) -> None:
        self.groups[group.group_id] = group
        for addr, pubkey in group.members.items():
            self.pubkeys[addr] = pubkey

    def add_pubkey(self, addr: str, pubkey: bytes) -> None:
        self.pubkeys[addr] = pubkey

    def get_pubkey(self, addr: str) -> bytes | None:
        return self.pubkeys.get(addr)

    def set_name(self, addr: str, name: str) -> None:
        self.names[addr] = name

    def set_override(self, addr: str, name: str) -> None:
        self.overrides[addr] = name

    def clear_override(self, addr: str) -> None:
        self.overrides.pop(addr, None)

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
