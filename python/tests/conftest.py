import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from muninn.sim.lab import SimLab  # noqa: E402


@pytest.fixture
def lab():
    lab = SimLab(verbose=bool(os.environ.get("LAB_VERBOSE")), time_scale=0.3)
    yield lab
    lab.close()
