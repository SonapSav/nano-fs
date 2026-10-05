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


# --- Low altitude (MIL-F-8785C 3.7.3, Category C: approach and landing) -----------------
#
# 3.7.3.2 wind shear: mean wind u(h) = u20 ln(h / z0) / ln(20 / z0), z0 = 0.15 ft.
# 3.7.3.4 turbulence: sigma_w = 0.1 u20; figures 10 and 11 (plots) give the scale lengths
# and sigma_u, sigma_v. Their standard fits (MIL-F-8785C form, as in the MathWorks Dryden
# model documentation), h in ft, valid 10-1000 ft:
#   L_w = h,  L_u = L_v = h / (0.177 + 0.000823 h)^1.2
#   sigma_u / sigma_w = sigma_v / sigma_w = 1 / (0.177 + 0.000823 h)^0.4
# Components are aligned with the mean wind (u along it, v across, w down). Between 1000
# and 2000 ft the parameters blend linearly into the medium/high altitude model.
# Typical u20: 15 kt light, 30 kt moderate, 45 kt severe turbulence.

Z0_CATEGORY_C_FT = 0.15
LOW_ALTITUDE_TOP_FT = 1000.0
MEDIUM_ALTITUDE_BOTTOM_FT = 2000.0
MIN_HEIGHT_FT = 10.0  # the low-altitude fits are not used below 10 ft


def wind_at_height_mps(u20_mps: float, height_m: float) -> float:
    """Mean wind speed at a height above the ground (MIL-F-8785C 3.7.3.2, Category C)."""
    h_ft = height_m / FT_TO_M
    if h_ft <= Z0_CATEGORY_C_FT:
        return 0.0
    return u20_mps * math.log(h_ft / Z0_CATEGORY_C_FT) / math.log(20.0 / Z0_CATEGORY_C_FT)


def low_altitude_parameters(u20_mps: float, height_m: float, sigma_high_mps: float = 0.0):
    """((sigma_u, sigma_v, sigma_w) m/s, (L_u, L_v, L_w) m) at a height above the ground."""
    h = min(max(height_m / FT_TO_M, MIN_HEIGHT_FT), MEDIUM_ALTITUDE_BOTTOM_FT)
    hl = min(h, LOW_ALTITUDE_TOP_FT)
    k = 0.177 + 0.000823 * hl
    sigma_w = 0.1 * u20_mps
    sigma_uv = sigma_w / k**0.4
    l_w, l_uv = hl, hl / k**1.2
    if h > LOW_ALTITUDE_TOP_FT:  # blend into the isotropic medium/high altitude model
        f = (h - LOW_ALTITUDE_TOP_FT) / (MEDIUM_ALTITUDE_BOTTOM_FT - LOW_ALTITUDE_TOP_FT)
        hi_l = MEDIUM_HIGH_ALTITUDE_SCALE_LENGTH_M / FT_TO_M
        sigma_w, sigma_uv = sigma_w + f * (sigma_high_mps - sigma_w), sigma_uv + f * (sigma_high_mps - sigma_uv)
        l_w, l_uv = l_w + f * (hi_l - l_w), l_uv + f * (hi_l - l_uv)
    return (sigma_uv, sigma_uv, sigma_w), (l_uv * FT_TO_M, l_uv * FT_TO_M, l_w * FT_TO_M)


_C1, _C2 = math.sqrt(3.0), 1.0 - math.sqrt(3.0)


def _second_order_scale(a: float) -> float:
    """1 / standard deviation of y = sqrt(3) x1 + (1 - sqrt(3)) x2 for the cascaded lags with
    pole a and unit-variance x1. Stationary: cov(x1, x2) = a / (1 + a), var(x2) =
    (1 + a^2) / (1 + a)^2."""
    var = _C1 * _C1 + 2.0 * _C1 * _C2 * a / (1.0 + a) + _C2 * _C2 * (1.0 + a * a) / (1.0 + a) ** 2
    return 1.0 / math.sqrt(var)


class LowAltitudeTurbulence:
    """Dryden turbulence whose intensities and scale lengths follow the height above the
    ground (MIL-F-8785C 3.7.3.4). The filter states have unit variance (their recursions
    keep it when the pole changes) and are scaled by the current intensities, so the
    parameters can change every step. Frozen turbulence at the given airspeed."""

    def __init__(self, u20_mps: float, airspeed_mps: float, dt_s: float, rng: np.random.Generator, sigma_high_mps: float = 0.0):
        self.u20, self.v, self.dt, self.sigma_high = u20_mps, airspeed_mps, dt_s, sigma_high_mps
        self._rng = rng
        self._u = float(rng.standard_normal())  # unit-variance states, stationary at the start
        self._x = []
        for _ in range(2):  # v and w: [x1, x2] with the stationary covariance for a starting pole
            a = 0.9
            c = a / (1.0 + a)
            v2 = (1.0 + a * a) / (1.0 + a) ** 2
            n1, n2 = rng.standard_normal(2).tolist()
            x2 = math.sqrt(v2) * n2
            x1 = c / v2 * x2 + math.sqrt(max(1.0 - c * c / v2, 0.0)) * n1
            self._x.append([x1, x2])
        self._out = (0.0, 0.0, 0.0)

    def step(self, height_m: float) -> tuple[float, float, float]:
        """Advance one step at this height; returns (u along the wind, v across it to the
        right, w down), m/s."""
        (su, sv, sw), (lu, lv, lw) = low_altitude_parameters(self.u20, height_m, self.sigma_high)
        n_u, n_v, n_w = self._rng.standard_normal(3).tolist()
        a_u = math.exp(-self.v * self.dt / lu)
        self._u = a_u * self._u + math.sqrt(1.0 - a_u * a_u) * n_u
        out = [su * self._u]
        for (x, sigma, length, n) in ((self._x[0], sv, lv, n_v), (self._x[1], sw, lw, n_w)):
            a = math.exp(-self.v * self.dt / length)
            x[0], x[1] = a * x[0] + math.sqrt(1.0 - a * a) * n, a * x[1] + (1.0 - a) * x[0]
            out.append(sigma * _second_order_scale(a) * (_C1 * x[0] + _C2 * x[1]))
        self._out = tuple(out)
        return self._out
