"""Run policies on seeded episodes and summarize task performance."""

import math
from dataclasses import dataclass

import numpy as np

from flightsim.envs.altitude_heading import AltitudeHeadingHoldEnv
from flightsim.envs.policies import Policy

ALT_TOLERANCE_M = 10.0
HEADING_TOLERANCE_RAD = math.radians(3.0)


@dataclass(frozen=True)
class EpisodeMetrics:
    seed: int
    policy: str
    episode_return: float
    duration_s: float
    termination_reason: str | None
    alt_rms_m: float
    heading_rms_deg: float
    tas_rms_mps: float
    alt_final_abs_m: float
    heading_final_abs_deg: float
    alt_settle_s: float  # time after which |alt error| stays within ALT_TOLERANCE_M (inf if never)
    heading_settle_s: float
    action_rate: float  # mean |change in action| per second, summed over channels
    # How hard it was flown (decision-step samples):
    max_bank_deg: float
    min_load_factor: float  # g, from body z specific force
    max_load_factor: float
    max_abs_climb_mps: float
    max_tas_dev_mps: float  # largest |airspeed - target|
    comfort_cost: float  # summed comfort penalty (0 for tasks without a comfort envelope)


def _settle_time(t: np.ndarray, err: np.ndarray, tol: float) -> float:
    outside = np.nonzero(np.abs(err) > tol)[0]
    if len(outside) == 0:
        return 0.0
    if outside[-1] == len(err) - 1:
        return math.inf
    return float(t[outside[-1] + 1])


def run_episode(env: AltitudeHeadingHoldEnv, policy: Policy, seed: int) -> EpisodeMetrics:
    obs, info = env.reset(seed=seed)
    policy.reset(info)
    dt = 1.0 / env.cfg.control_rate_hz
    t, e_alt, e_hdg, e_tas, actions = [], [], [], [], []
    bank, nz, climb = [], [], []
    comfort = 0.0
    total, reason = 0.0, None
    while True:
        action = np.asarray(policy(obs, info), dtype=np.float32)
        obs, reward, terminated, truncated, info = env.step(action)
        total += reward
        a, h, v = env.errors()
        t.append(len(t) * dt + dt)
        e_alt.append(a)
        e_hdg.append(h)
        e_tas.append(v)
        actions.append(action)
        s = info["state"]
        bank.append(abs(s.phi_rad))
        nz.append(-s.az_mps2 / 9.80665)
        climb.append(abs(s.v_down_mps))
        comfort += info["comfort_cost"]
        if terminated or truncated:
            reason = info["termination_reason"]
            break
    t_arr, alt, hdg, tas = (np.array(x) for x in (t, e_alt, e_hdg, e_tas))
    act = np.array(actions)
    duration = t_arr[-1]
    return EpisodeMetrics(
        seed=seed,
        policy=policy.name,
        episode_return=total,
        duration_s=duration,
        termination_reason=reason,
        alt_rms_m=float(np.sqrt(np.mean(alt**2))),
        heading_rms_deg=math.degrees(float(np.sqrt(np.mean(hdg**2)))),
        tas_rms_mps=float(np.sqrt(np.mean(tas**2))),
        alt_final_abs_m=abs(float(alt[-1])),
        heading_final_abs_deg=math.degrees(abs(float(hdg[-1]))),
        alt_settle_s=_settle_time(t_arr, alt, ALT_TOLERANCE_M),
        heading_settle_s=_settle_time(t_arr, hdg, HEADING_TOLERANCE_RAD),
        action_rate=float(np.abs(np.diff(act, axis=0)).sum() / duration) if len(act) > 1 else 0.0,
        max_bank_deg=math.degrees(max(bank)),
        min_load_factor=min(nz),
        max_load_factor=max(nz),
        max_abs_climb_mps=max(climb),
        max_tas_dev_mps=float(np.abs(tas).max()),
        comfort_cost=comfort,
    )
