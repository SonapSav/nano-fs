from pathlib import Path

import pytest

from flightsim.config import RunConfig, load_config

CRUISE = Path(__file__).parent.parent / "configs" / "cruise.yaml"


@pytest.fixture
def cruise() -> RunConfig:
    return load_config(CRUISE)


def shortened(duration_s: float, **overrides) -> RunConfig:
    """The cruise config with overrides applied through the config data (so the hash follows)."""
    return load_config(CRUISE, {"duration_s": duration_s, **overrides})
