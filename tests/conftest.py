from dataclasses import replace
from pathlib import Path

import pytest

from flightsim.config import RunConfig, load_config

CRUISE = Path(__file__).parent.parent / "configs" / "cruise.yaml"


@pytest.fixture
def cruise() -> RunConfig:
    return load_config(CRUISE)


def shortened(cfg: RunConfig, duration_s: float) -> RunConfig:
    return replace(cfg, duration_s=duration_s)
