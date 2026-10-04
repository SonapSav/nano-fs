"""Headless run: reset, trim, then step the core with the heading hold at a fixed timestep."""

from dataclasses import dataclass

import numpy as np

from flightsim.config import RunConfig
from flightsim.control.heading_hold import HeadingHold
from flightsim.core import Controls, JSBSimCore, State


@dataclass(frozen=True)
class RunResult:
    trim: Controls
    states: list[State]  # states[0] is the trimmed initial state
    controls: list[Controls]  # controls[i] was applied to go from states[i] to states[i + 1]
    jsbsim_version: str


def run(cfg: RunConfig) -> RunResult:
    # Seeded even though step 1 draws no random numbers, so every run has the same entry point.
    _rng = np.random.default_rng(cfg.seed)
    core = JSBSimCore(cfg.aircraft, cfg.dt_s)
    core.reset(cfg.initial_conditions, cfg.loading)
    trim = core.trim()
    controller = HeadingHold(cfg.heading_hold, trim, cfg.target_heading_rad)

    state = core.state()
    states, controls = [state], []
    for _ in range(cfg.n_steps):
        u = controller(state)
        state = core.step(u)
        controls.append(u)
        states.append(state)
    return RunResult(trim=trim, states=states, controls=controls, jsbsim_version=core.jsbsim_version)
