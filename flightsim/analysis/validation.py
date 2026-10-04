"""Step 2 validation checks: the physics core against published reference data.

`plan_checks` lists every check without running anything (so tests can be
parametrized cheaply); each planned check runs on demand and shares expensive
simulations through caches. Checks report in the reference's own units.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from flightsim.analysis.maneuvers import KT_TO_MPS, loading_for, phugoid_response, stall_speed, trim_at_cas
from flightsim.analysis.modes import lateral_modes, longitudinal_modes
from flightsim.core import InitialConditions, JSBSimCore, Loading
from flightsim.core.jsbsim_core import FT_TO_M, HP_TO_W, IN_TO_M, LBM_TO_KG

RATED_POWER_HP = 160.0  # Lycoming O-320-D2J, POH Section 1


@dataclass(frozen=True)
class Check:
    id: str
    source: str
    description: str
    reference: str
    model: float
    unit: str
    low: float | None
    high: float | None
    known_deviation: str | None = None

    @property
    def passed(self) -> bool:
        return (self.low is None or self.model >= self.low) and (self.high is None or self.model <= self.high)

    @property
    def allowed(self) -> str:
        if self.low is not None and self.high is not None:
            return f"{self.low:g} .. {self.high:g}"
        return f">= {self.low:g}" if self.low is not None else f"<= {self.high:g}"


@dataclass(frozen=True)
class PlannedCheck:
    id: str
    known_deviation: str | None
    run: Callable[[], Check]


def load_validation_config(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


@cache
def _loading(aircraft: str, weight_lb: float, cg_in: float, fuel_lb: float) -> Loading:
    return loading_for(aircraft, weight_lb * LBM_TO_KG, cg_in * IN_TO_M, fuel_lb * LBM_TO_KG)


@cache
def _cruise_trim(aircraft: str, loading: Loading, alt_ft: float, ktas: float) -> tuple[float, float]:
    core = JSBSimCore(aircraft, 1 / 120)
    core.reset(InitialConditions(alt_ft * FT_TO_M, ktas * KT_TO_MPS, 0.0), loading)
    core.trim()
    s = core.state()
    return s.engine_rpm, s.engine_power_w / HP_TO_W / RATED_POWER_HP * 100.0


@cache
def _stall(aircraft: str, loading: Loading, flaps_deg: float, alt_ft: float):
    return stall_speed(aircraft, loading, flaps_deg / 30.0, alt_msl_m=alt_ft * FT_TO_M)


@cache
def _linear(aircraft: str, loading: Loading, alt_ft: float, ktas: float):
    core = JSBSimCore(aircraft, 1 / 120)
    core.reset(InitialConditions(alt_ft * FT_TO_M, ktas * KT_TO_MPS, 0.0), loading)
    core.trim()
    lm = core.linearize()
    return longitudinal_modes(lm), lateral_modes(lm)


@cache
def _phugoid_test(aircraft: str, loading: Loading, alt_ft: float, trim_kias: float, release_kias: float):
    osc, _, _ = phugoid_response(
        aircraft,
        loading,
        alt_msl_m=alt_ft * FT_TO_M,
        trim_cas_mps=trim_kias * KT_TO_MPS,
        release_cas_mps=release_kias * KT_TO_MPS,
    )
    core, _, _ = trim_at_cas(aircraft, alt_ft * FT_TO_M, trim_kias * KT_TO_MPS, loading)
    return osc, longitudinal_modes(core.linearize()).phugoid


def plan_checks(cfg: dict) -> list[PlannedCheck]:
    ac = cfg["aircraft"]
    src = cfg["sources"]
    planned: list[PlannedCheck] = []

    def add(id: str, run: Callable[[], Check], known: str | None = None) -> None:
        planned.append(PlannedCheck(id, known, run))

    # Cruise performance (POH Figure 5-8)
    cr = cfg["cruise"]
    for p in cr["points"]:
        tag = f"{p['alt_ft']}ft_{p['ktas']}ktas"

        def trim(p=p):
            return _cruise_trim(ac, _loading(ac, cr["weight_lb"], cr["cg_in"], cr["fuel_lb"]), p["alt_ft"], p["ktas"])

        def rpm(p=p, tag=tag, trim=trim):
            tol = cr["tolerance"]["rpm"]
            return Check(f"cruise_rpm_{tag}", src[cr["source"]], f"Cruise RPM at {p['alt_ft']} ft, {p['ktas']} KTAS ({cr['ref']})",
                         f"{p['rpm']} rpm", trim()[0], "rpm", p["rpm"] - tol, p["rpm"] + tol)

        def power(p=p, tag=tag, trim=trim):
            tol = cr["tolerance"]["power_pct"]
            return Check(f"cruise_power_{tag}", src[cr["source"]], f"Cruise power at {p['alt_ft']} ft, {p['ktas']} KTAS ({cr['ref']})",
                         f"{p['power_pct']} %BHP", trim()[1], "%BHP", p["power_pct"] - tol, p["power_pct"] + tol)

        add(f"cruise_rpm_{tag}", rpm)
        add(f"cruise_power_{tag}", power)

    # Stall speeds (POH Figure 5-3)
    st = cfg["stall"]
    for p in st["points"]:
        case = st["cg_cases"][p["cg"]]
        tag = f"{p['cg']}cg_flaps{p['flaps_deg']}"

        def stall(p=p, case=case, tag=tag):
            load = _loading(ac, st["weight_lb"], case["cg_in"], case["fuel_lb"])
            r = _stall(ac, load, p["flaps_deg"], st["alt_ft"])
            tol = st["tolerance_kcas"]
            note = " (elevator-limited)" if r.elevator_limited else ""
            return Check(f"stall_{tag}", src[st["source"]],
                         f"Stall speed, {p['cg']} CG ({case['cg_in']} in), flaps {p['flaps_deg']} deg{note} ({st['ref']})",
                         f"{p['kcas']} KCAS", r.vs_cas_mps / KT_TO_MPS, "KCAS", p["kcas"] - tol, p["kcas"] + tol,
                         p.get("known_deviation"))

        add(f"stall_{tag}", stall, p.get("known_deviation"))

    # Phugoid against the AAIB flight test
    ph = cfg["phugoid_flight_test"]

    def phugoid():
        load = _loading(ac, ph["weight_lb"], ph["cg_in"], ph["fuel_lb"])
        return _phugoid_test(ac, load, ph["alt_ft"], ph["trim_kias"], ph["release_kias"])

    def ph_period():
        tol = ph["period_s"] * ph["period_tolerance_pct"] / 100
        return Check("phugoid_period_flight_test", src[ph["source"]], f"Phugoid period, stick fixed, released at {ph['release_kias']} KIAS ({ph['ref']})",
                     f"{ph['period_s']} s", phugoid()[0].period_s, "s", ph["period_s"] - tol, ph["period_s"] + tol,
                     ph.get("period_known_deviation"))

    def ph_zeta():
        return Check("phugoid_damping_flight_test", src[ph["source"]], f"Phugoid damping ratio ({ph['ref']})",
                     f"{ph['zeta']}", phugoid()[0].zeta, "-", ph["zeta"] - ph["zeta_tolerance"], ph["zeta"] + ph["zeta_tolerance"])

    def ph_consistency():
        nonlinear, linear = phugoid()
        tol = cfg["consistency"]["phugoid_period_linear_vs_nonlinear_pct"]
        diff = (nonlinear.period_s - linear.period_s) / linear.period_s * 100
        return Check("phugoid_linear_vs_nonlinear", "internal consistency", "Phugoid period: nonlinear simulation vs linear model",
                     f"{linear.period_s:.2f} s (linear)", diff, "% difference", -tol, tol)

    add("phugoid_period_flight_test", ph_period, ph.get("period_known_deviation"))
    add("phugoid_damping_flight_test", ph_zeta)
    add("phugoid_linear_vs_nonlinear", ph_consistency)

    # Flying qualities (MIL-F-8785C, Class I, Category B, Level 1)
    fq = cfg["flying_qualities"]
    c = fq["condition"]

    def modes():
        load = _loading(ac, c["weight_lb"], c["cg_in"], c["fuel_lb"])
        return _linear(ac, load, c["alt_ft"], c["ktas"])

    fq_items: dict[str, tuple[str, str, Callable[[], float]]] = {
        "short_period_zeta": ("Short-period damping ratio", "-", lambda: modes()[0].short_period.zeta),
        "phugoid_zeta": ("Phugoid damping ratio", "-", lambda: modes()[0].phugoid.zeta),
        "dutch_roll_zeta": ("Dutch roll damping ratio", "-", lambda: modes()[1].dutch_roll.zeta),
        "dutch_roll_zeta_wn_radps": ("Dutch roll zeta x wn", "rad/s", lambda: modes()[1].dutch_roll.zeta * modes()[1].dutch_roll.wn_radps),
        "dutch_roll_wn_radps": ("Dutch roll natural frequency", "rad/s", lambda: modes()[1].dutch_roll.wn_radps),
        "roll_time_constant_s": ("Roll-mode time constant", "s", lambda: modes()[1].roll_time_constant_s),
        "spiral_time_to_double_s": ("Spiral mode time to double (inf = stable)", "s", lambda: modes()[1].spiral_time_to_double_s),
    }
    for key, (desc, unit, value) in fq_items.items():
        limits = fq[key]

        def flying_quality(key=key, desc=desc, unit=unit, value=value, limits=limits):
            low, high = limits.get("min"), limits.get("max")
            ref = " and ".join(f"{k} {v}" for k, v in limits.items())
            return Check(f"mil_{key}", src[fq["source"]], f"{desc} at {c['alt_ft']} ft, {c['ktas']} KTAS ({fq['ref']})",
                         ref, value(), unit, low, high)

        add(f"mil_{key}", flying_quality)

    return planned


def run_checks(cfg: dict) -> list[Check]:
    return [p.run() for p in plan_checks(cfg)]


def format_value(v: float) -> str:
    return "inf" if math.isinf(v) else f"{v:.3g}" if abs(v) < 10 else f"{v:.1f}"
