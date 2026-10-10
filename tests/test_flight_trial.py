"""The AAIB flight trial's lateral observations on a Cessna 172S (AAIB Bulletin 12/2020,
G-CBXJ; docs/REFERENCES.md), flown in c172p_tuned at the trial's condition (2300 lb, CG
41.43 in, 3500 ft, 110 KIAS = 107 KCAS, Figure 5-1): in the cruise stick free "the
aircraft was reluctant to depart from wings-level flight"; with a small (2 cm) right rudder
pedal input "the aircraft began to roll slowly to the right. As the roll amplitude
increased, the nose dropped and the airspeed progressively increased, resulting in a
spiral dive within 10 to 15 seconds". The model holds its controls at trim (no hinge
moments: not stick free) and the pedal's travel is not given, so the input is a small
fraction of full rudder; these are qualitative checks."""

import math
from dataclasses import replace

import pytest

from flightsim.analysis.maneuvers import trim_at_cas
from flightsim.analysis.validation import _loading

AC = "c172p_tuned"
KT = 1852 / 3600


def fly(rudder: float, seconds: float):
    core, _, trim = trim_at_cas(AC, 3500 * 0.3048, 107 * KT, _loading(AC, 2300, 41.43, 240))
    u = replace(trim, rudder=trim.rudder + rudder)
    trace = []
    for _ in range(round(seconds / core.dt_s)):
        s = core.step(u)
        trace.append(s)
    return trace


def test_hands_off_cruise_stays_wings_level():
    trace = fly(0.0, 30.0)
    assert max(abs(math.degrees(s.phi_rad)) for s in trace) < 2.0


@pytest.mark.parametrize("rudder", [-0.1, -0.2])  # right pedal: nose right (negative rudder)
def test_small_right_rudder_leads_to_a_spiral_dive(rudder):
    trace = fly(rudder, 15.0)
    end = trace[-1]
    assert math.degrees(end.phi_rad) > 20.0  # rolled right
    assert end.v_down_mps > 500 * 0.00508  # descending faster than 500 ft/min
    assert end.cas_mps > trace[0].cas_mps + 3 * KT  # speed building
    banks = [math.degrees(s.phi_rad) for s in trace]
    assert all(b2 >= b1 - 0.05 for b1, b2 in zip(banks, banks[1:]))  # rolling steadily, not oscillating back
