"""Dynamic modes from a linear model, and from a simulated time response."""

import math
from dataclasses import dataclass

import numpy as np

from flightsim.core import LinearModel

LONGITUDINAL_STATES = ("tas_mps", "alpha_rad", "theta_rad", "q_radps")
LATERAL_STATES = ("beta_rad", "phi_rad", "p_radps", "r_radps")


@dataclass(frozen=True)
class Oscillation:
    wn_radps: float  # undamped natural frequency
    zeta: float  # damping ratio

    @property
    def period_s(self) -> float:
        """Damped period."""
        return 2.0 * math.pi / (self.wn_radps * math.sqrt(1.0 - self.zeta**2))

    @classmethod
    def from_eigenvalue(cls, lam: complex) -> "Oscillation":
        wn = abs(lam)
        return cls(wn_radps=wn, zeta=-lam.real / wn)


@dataclass(frozen=True)
class LongitudinalModes:
    short_period: Oscillation
    phugoid: Oscillation


@dataclass(frozen=True)
class LateralModes:
    dutch_roll: Oscillation
    roll_time_constant_s: float
    spiral_eigenvalue: float  # 1/s; positive = divergent

    @property
    def spiral_time_to_double_s(self) -> float:
        """inf when the spiral mode is stable."""
        return math.log(2.0) / self.spiral_eigenvalue if self.spiral_eigenvalue > 0 else math.inf


def _complex_pairs(eigs: np.ndarray) -> list[complex]:
    return sorted((complex(e) for e in eigs if e.imag > 1e-9), key=abs)


def longitudinal_modes(lm: LinearModel) -> LongitudinalModes:
    """From the 4-state longitudinal subsystem (speed, alpha, pitch, pitch rate)."""
    pairs = _complex_pairs(np.linalg.eigvals(lm.submatrix(LONGITUDINAL_STATES)))
    if len(pairs) != 2:
        raise ValueError(f"expected two oscillatory longitudinal modes, got {pairs}")
    phugoid, short_period = pairs
    return LongitudinalModes(
        short_period=Oscillation.from_eigenvalue(short_period),
        phugoid=Oscillation.from_eigenvalue(phugoid),
    )


def lateral_modes(lm: LinearModel) -> LateralModes:
    """From the 4-state lateral-directional subsystem (sideslip, bank, roll rate, yaw rate)."""
    eigs = np.linalg.eigvals(lm.submatrix(LATERAL_STATES))
    pairs = _complex_pairs(eigs)
    real = sorted(e.real for e in eigs if abs(e.imag) <= 1e-9)
    if len(pairs) != 1 or len(real) != 2:
        raise ValueError(f"expected Dutch roll pair plus roll and spiral roots, got {eigs}")
    roll, spiral = real  # roll subsidence is the fast (most negative) root
    return LateralModes(
        dutch_roll=Oscillation.from_eigenvalue(pairs[0]),
        roll_time_constant_s=-1.0 / roll,
        spiral_eigenvalue=spiral,
    )


def oscillation_from_response(t_s: np.ndarray, y: np.ndarray, y_trim: float, n_extrema: int = 6) -> Oscillation:
    """Estimate period and damping from the first extrema of a free oscillation about y_trim.

    Period: mean spacing of alternate extrema. Damping: mean amplitude ratio over one
    full cycle (logarithmic decrement).
    """
    d = np.diff(y)
    idx = np.nonzero(d[:-1] * d[1:] < 0)[0] + 1
    if len(idx) < 4:
        raise ValueError("not enough extrema to identify an oscillation")
    idx = idx[:n_extrema]
    t_ext, dev = t_s[idx], y[idx] - y_trim
    periods = t_ext[2:] - t_ext[:-2]
    ratios = np.abs(dev[2:] / dev[:-2])
    delta = float(np.mean(np.log(1.0 / ratios)))
    zeta = delta / math.sqrt(4.0 * math.pi**2 + delta**2)
    period = float(np.mean(periods))
    wn = 2.0 * math.pi / (period * math.sqrt(1.0 - zeta**2))
    return Oscillation(wn_radps=wn, zeta=zeta)
