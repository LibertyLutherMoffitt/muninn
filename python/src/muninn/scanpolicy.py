"""How hard to look for new people. Shared by CLI, GUI and (as ScanPolicy.kt)
the Android app — keep the numbers in step (tests/test_scanpolicy.py checks).

An inquiry floods the band for a few seconds and briefly degrades links that
are already up (and the user's own Bluetooth audio), so it is rationed. Known
peers are always redialled regardless of mode — that's cheap and is what
makes "they walked back to their seat" just work.

Good-citizen rules hold in every mode: we never pair with, prompt, or dial
headsets/watches/peripherals; an *unknown phone or computer* may get one quiet
probe (no pairing), then is left alone for a long time if it isn't Muninn.
"""

from __future__ import annotations

from dataclasses import dataclass

SETTING_KEY = "scan_mode"


@dataclass(frozen=True)
class ScanPolicy:
    name: str
    label: str
    inquiry_alone: float  # seconds between inquiries while we have no links
    inquiry_linked: float  # ...while connected to someone (0 = never)
    probe_budget: int  # unknown devices probed per inquiry
    probe_backoff: float  # wait before re-probing a device that refused
    peer_backoff_max: float  # cap on redial backoff for known peers

    @property
    def description(self) -> str:
        linked = (
            "never" if not self.inquiry_linked else f"every {_t(self.inquiry_linked)}"
        )
        alone = "never" if not self.inquiry_alone else f"every {_t(self.inquiry_alone)}"
        return (
            f"look around {alone} when alone, {linked} when connected; "
            f"probe {self.probe_budget} unknown phone/laptop per look"
        )


def _t(s: float) -> str:
    return f"{int(s)}s" if s < 60 else f"{s / 60:.0f} min"


EAGER = ScanPolicy("eager", "Eager", 15.0, 60.0, 4, 600.0, 20.0)
BALANCED = ScanPolicy("balanced", "Balanced", 25.0, 180.0, 2, 1200.0, 30.0)
QUIET = ScanPolicy("quiet", "Quiet", 0.0, 0.0, 0, 3600.0, 45.0)

POLICIES = {p.name: p for p in (EAGER, BALANCED, QUIET)}
DEFAULT = BALANCED


def get(name: str | None) -> ScanPolicy:
    return POLICIES.get(name or "", DEFAULT)
