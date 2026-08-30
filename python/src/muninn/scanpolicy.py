"""How hard to look for peers.

Scanning is a trade: a Bluetooth inquiry is slow, floods the band, and briefly
degrades any link already up — including the one carrying your messages. Doing
it constantly finds a new peer sooner and costs battery and throughput; doing it
rarely is kind to both and can leave someone two rows away invisible for
minutes.

Three presets, shared by the CLI, the GUI and the Android client, chosen by the
user. `AGGRESSIVE` is the default: the app's whole purpose is that a peer who
sits down near you starts working without anybody doing anything.

The Kotlin counterpart is `ScanPolicy.kt`; keep the numbers in step.
"""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from muninn.storage import Storage

SETTING_KEY = "scan_policy"


@dataclass(frozen=True)
class ScanPolicy:
    """Timings for one aggressiveness level. All values in seconds."""

    name: str
    label: str

    # Gap between full inquiries. An inquiry is the expensive, disruptive part.
    inquiry_interval: float
    # Gap between dial sweeps over devices we already know about. Cheap.
    dial_interval: float

    # Retry curve for a device we believe runs Muninn. Capped low: a peer
    # temporarily out of range must be picked up quickly when they return, so
    # this backoff exists only to avoid hammering, never to give up.
    peer_backoff_base: float
    peer_backoff_max: float

    # Retry curve for an unidentified device. In a full cabin most of these are
    # headsets and car kits; each probe costs a slow, blocking connect attempt,
    # so failures back off hard and far.
    probe_backoff_base: float
    probe_backoff_max: float

    # Unknown devices probed per sweep. The cap is what keeps a crowded cabin
    # from starving the peers we actually care about.
    probe_budget: int

    @property
    def description(self) -> str:
        return (
            f"inquiry every {_mins(self.inquiry_interval)}, "
            f"dial every {int(self.dial_interval)}s, "
            f"{self.probe_budget} new devices per sweep"
        )


def _mins(seconds: float) -> str:
    return f"{int(seconds)}s" if seconds < 60 else f"{seconds / 60:.0f}m"


AGGRESSIVE = ScanPolicy(
    name="aggressive",
    label="Aggressive",
    inquiry_interval=30.0,
    dial_interval=8.0,
    peer_backoff_base=5.0,
    peer_backoff_max=45.0,
    probe_backoff_base=60.0,
    probe_backoff_max=900.0,
    probe_budget=6,
)

BALANCED = ScanPolicy(
    name="balanced",
    label="Balanced",
    inquiry_interval=120.0,
    dial_interval=15.0,
    peer_backoff_base=10.0,
    peer_backoff_max=120.0,
    probe_backoff_base=300.0,
    probe_backoff_max=3600.0,
    probe_budget=3,
)

CONSERVATIVE = ScanPolicy(
    name="conservative",
    label="Conservative",
    inquiry_interval=300.0,
    dial_interval=30.0,
    peer_backoff_base=30.0,
    peer_backoff_max=300.0,
    probe_backoff_base=900.0,
    probe_backoff_max=7200.0,
    probe_budget=2,
)

POLICIES = {p.name: p for p in (AGGRESSIVE, BALANCED, CONSERVATIVE)}

# Finding peers unattended is the point of the app, so the eager setting is the
# one you get unless you ask for otherwise.
DEFAULT = AGGRESSIVE


def by_name(name: str | None) -> ScanPolicy | None:
    if not name:
        return None
    return POLICIES.get(name.strip().lower())


def resolve(storage: "Storage | None" = None) -> ScanPolicy:
    """The policy in force: env override, else stored choice, else default.

    MUNINN_SCAN_POLICY wins so a single run can be made quiet (or frantic)
    without changing what the user picked.
    """
    from_env = by_name(os.environ.get("MUNINN_SCAN_POLICY"))
    if from_env is not None:
        return from_env
    if storage is not None:
        stored = by_name(storage.get_setting(SETTING_KEY))
        if stored is not None:
            return stored
    return DEFAULT


def store(storage: "Storage | None", policy: ScanPolicy) -> None:
    if storage is not None:
        storage.set_setting(SETTING_KEY, policy.name)
