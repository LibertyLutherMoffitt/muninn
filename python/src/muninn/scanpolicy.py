"""Finding new people vs. reconnecting to known ones. Shared by CLI, GUI and
(as ScanPolicy.kt) the Android app — keep the numbers in step
(tests/test_discovery.py checks).

**Reconnecting** to people you've met never scans: Muninn dials their saved
radio address directly (a page, aimed at one device), with a short back-off
while they're out of range. Two speeds: *fast* (default) and *battery saver*.

**Finding new people** needs an inquiry (a broadcast scan that briefly slows
the band and the user's own Bluetooth audio) and, for phones/laptops that
don't reveal their services, one pairing-free probe each. That only happens
in *find mode*: for a few minutes after the user asks (Nearby / ⟳ /
"Find people"), and automatically while you know nobody yet. New contacts
are rare; the rest of the time Muninn doesn't look around at all.

Good-citizen rules hold always: we never pair with, prompt, or dial
headsets/watches/peripherals; an unknown phone/laptop gets at most one quiet
probe per look, then is left alone for hours if it isn't Muninn.
"""

from __future__ import annotations

from dataclasses import dataclass

SETTING_KEY = "reconnect"

FIND_SECONDS = 300.0  # how long "Find people" stays on
FIND_INQUIRY = 15.0  # seconds between looks while finding
FIND_PROBES = 3  # unknown phones/laptops checked per look
FIND_JITTER = 8.0  # + up to this much, so two phones both finding drift apart
PROBE_BACKOFF = 6 * 3600.0  # a device that isn't Muninn is left alone this long
# A failed probe is often just a busy radio (the other phone was mid-inquiry
# and didn't answer the page): look once more soon, then at 10 min, then hours.
PROBE_RETRY_FIRST = 45.0
PROBE_RETRY_SECOND = 600.0


@dataclass(frozen=True)
class ReconnectMode:
    name: str
    label: str
    backoff_max: float  # cap on redial back-off for a known person out of range

    @property
    def description(self) -> str:
        return (
            f"retry someone who dropped out of range at least every "
            f"{self.backoff_max:.0f}s (never scans)"
        )


FAST = ReconnectMode("fast", "Fast", 15.0)
SAVER = ReconnectMode("saver", "Battery saver", 60.0)

MODES = {m.name: m for m in (FAST, SAVER)}
DEFAULT = FAST


def get(name: str | None) -> ReconnectMode:
    return MODES.get(name or "", DEFAULT)
