"""Summary numbers for one run, computed from a :class:`~rksweep.kinetics.Solution`."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.integrate import trapezoid

from rksweep.kinetics import Solution

#: Slopes smaller than this (1/s) count as "flat": the period is reported as infinite.
FLAT_OMEGA = 1e-9


def n_max(sol: Solution) -> float:
    """Peak relative power over the output grid."""
    return float(np.max(sol.n))


def t_peak(sol: Solution) -> float:
    """Time (s) of the peak power (first occurrence)."""
    return float(sol.t[int(np.argmax(sol.n))])


def late_slope(sol: Solution, window: float = 0.2) -> float:
    """Slope of ``ln n`` against ``t`` over the last ``window`` fraction of the run (1/s).

    That is the measured growth rate ``omega``. The window is the time span
    ``t >= (1 - window) * t_end`` (the output grid is logarithmic, so this is a handful of
    points, not 20 % of them).
    """
    t_end = float(sol.t[-1])
    mask = sol.t >= (1.0 - window) * t_end
    t = sol.t[mask]
    n = sol.n[mask]
    if t.size < 2 or np.any(n <= 0.0):
        return math.nan
    slope, _ = np.polyfit(t, np.log(n), 1)
    return float(slope)


def period_s(sol: Solution, window: float = 0.2) -> float:
    """``1 / omega`` with ``omega`` from :func:`late_slope`; ``inf`` if the power is flat.

    Negative for a decaying power. ``nan`` if the late window cannot be fitted.
    """
    omega = late_slope(sol, window)
    if math.isnan(omega):
        return math.nan
    if abs(omega) < FLAT_OMEGA:
        return math.inf
    return 1.0 / omega


def energy(sol: Solution) -> float:
    """``integral n dt`` over the run by the trapezoid rule (relative units)."""
    return float(trapezoid(sol.n, sol.t))


def t_double(sol: Solution, target: float = 2.0) -> float:
    """First time ``n >= target`` (default 2), or ``nan`` if never reached.

    The time is interpolated between the two bracketing output points, assuming
    exponential growth there (linear in ``ln n``), because the grid is coarse at late times.
    """
    above = np.nonzero(sol.n >= target)[0]
    if above.size == 0:
        return math.nan
    k = int(above[0])
    if k == 0:
        return float(sol.t[0])
    t0, t1 = sol.t[k - 1], sol.t[k]
    l0, l1 = math.log(sol.n[k - 1]), math.log(sol.n[k])
    if l1 == l0:
        return float(t1)
    return float(t0 + (math.log(target) - l0) / (l1 - l0) * (t1 - t0))


def compute_metrics(sol: Solution) -> dict[str, Any]:
    """All metrics for one run as a flat dict.

    Keys: ``n_max``, ``t_peak``, ``period_s``, ``energy``, ``t_double`` (floats),
    ``wall_time_s`` (float), ``nfev`` and ``njev`` (ints).
    """
    return {
        "n_max": n_max(sol),
        "t_peak": t_peak(sol),
        "period_s": period_s(sol),
        "energy": energy(sol),
        "t_double": t_double(sol),
        "wall_time_s": sol.wall_time_s,
        "nfev": sol.nfev,
        "njev": sol.njev,
    }
