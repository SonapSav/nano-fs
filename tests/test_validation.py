"""Physics validation against published data (step 2). See docs/REFERENCES.md and
docs/VALIDATION.md (c172p) and docs/VALIDATION_c172p_tuned.md. Known deviations run as
strict expected failures, so a model change that fixes one makes the suite fail until the
config is updated."""

from pathlib import Path

import pytest

from flightsim.analysis.validation import load_validation_config, plan_checks

CONFIGS = Path(__file__).parent.parent / "configs" / "validation"
PLANNED = [(name, p) for name in ("c172p", "c172p_tuned") for p in plan_checks(load_validation_config(CONFIGS / f"{name}.yaml"))]


@pytest.mark.parametrize(
    "planned",
    [
        pytest.param(p, id=f"{name}-{p.id}", marks=pytest.mark.xfail(reason=p.known_deviation, strict=True))
        if p.known_deviation
        else pytest.param(p, id=f"{name}-{p.id}")
        for name, p in PLANNED
    ],
)
def test_validation_check(planned):
    check = planned.run()
    assert check.passed, f"{check.description}: model {check.model} {check.unit}, allowed {check.allowed}"
