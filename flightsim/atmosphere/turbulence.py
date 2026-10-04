"""Dryden continuous turbulence, MIL-F-8785C 3.7.1.2 (spectra) and 3.7.2 (medium/high
altitude: isotropic, sigma_u = sigma_v = sigma_w, L_u = L_v = L_w = 1750 ft).

Frozen turbulence at a constant airspeed V (the episode's trim airspeed): spatial
frequency maps to temporal frequency via omega = V * Omega, so each component is
white noise through a shaping filter with time constant T = L / V:

  u (along track):        1 / (1 + T s)                 -> R(xi) = sigma^2 exp(-xi/L)
  v (lateral), w (vertical): (1 + sqrt(3) T s) / (1 + T s)^2 -> R(xi) = sigma^2 (1 - xi/(2L)) exp(-xi/L)

The along-track filter is discretized exactly (first-order Gauss-Markov). The second-order
filter is realized as two cascaded first-order lags, y = sqrt(3) x1 + (1 - sqrt(3)) x2, and
normalized with its exact discrete stationary variance, so each component has variance
sigma^2. States start from the stationary distribution, so turbulence is fully developed
at t = 0. Only translational gusts are modelled (no gust angular rates).
"""

import math

import numpy as np

FT_TO_M = 0.3048
MEDIUM_HIGH_ALTITUDE_SCALE_LENGTH_M = 1750 * FT_TO_M  # MIL-F-8785C 3.7.2.1, Dryden form


class DrydenTurbulence:
    def __init__(self, sigma_mps: float, scale_length_m: float, airspeed_mps: float, dt_s: float, rng: np.random.Generator):
        self.sigma = sigma_mps
        self._rng = rng
        a = math.exp(-airspeed_mps * dt_s / scale_length_m)
        self._a = a
        self._b = math.sqrt(1.0 - a * a)
        # Second-order (v, w) filter: state [x1, x2], x1 unit-variance Gauss-Markov,
        # x2 its first-order lag. Stationary covariance P solves P = Phi P Phi^T + Q.
        phi = np.array([[a, 0.0], [1.0 - a, a]])
        q = np.diag([1.0 - a * a, 0.0])
        p = np.linalg.solve(np.eye(4) - np.kron(phi, phi), q.reshape(-1)).reshape(2, 2)
        c = np.array([math.sqrt(3.0), 1.0 - math.sqrt(3.0)])
        y_scale = sigma_mps / math.sqrt(c @ p @ c)
        self._c1, self._c2 = (float(x) for x in c * y_scale)
        chol = np.linalg.cholesky(p)
        self._u = sigma_mps * float(rng.standard_normal())
        # Plain floats from here on: per-step numpy calls on 2-element arrays cost more than the math.
        self._v1, self._v2 = (float(x) for x in chol @ rng.standard_normal(2))
        self._w1, self._w2 = (float(x) for x in chol @ rng.standard_normal(2))

    def current(self) -> tuple[float, float, float]:
        """(u along track, v to the right, w down), m/s."""
        return self._u, self._c1 * self._v1 + self._c2 * self._v2, self._c1 * self._w1 + self._c2 * self._w2

    def step(self) -> tuple[float, float, float]:
        """Advance one time step and return the new (u, v, w)."""
        n_u, n_v, n_w = self._rng.standard_normal(3).tolist()
        a, b = self._a, self._b
        self._u = a * self._u + self.sigma * b * n_u
        self._v1, self._v2 = a * self._v1 + b * n_v, a * self._v2 + (1.0 - a) * self._v1
        self._w1, self._w2 = a * self._w1 + b * n_w, a * self._w2 + (1.0 - a) * self._w1
        return self.current()


def to_ned(u: float, v: float, w: float, heading_rad: float) -> tuple[float, float, float]:
    """Rotate track-aligned gust components (u forward, v right, w down) into NED."""
    c, s = math.cos(heading_rad), math.sin(heading_rad)
    return u * c - v * s, u * s + v * c, w
