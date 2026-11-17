"""Tests for summary metrics."""

from __future__ import annotations

import math

import numpy as np
import pytest

from rksweep.constants import KEEPIN_U235_THERMAL
from rksweep.kinetics import solve
from rksweep.metrics import (
    compute_metrics,
    energy,
    n_max,
    period_s,
    t_double,
    t_peak,
)
from rksweep.reactivity import Step, StepFeedback
from rksweep.reference import expm_solution, stable_omega

DATA = KEEPIN_U235_THERMAL
BETA = DATA.beta


def test_flat_run() -> None:
    sol = solve(Step(0.0), DATA, 1e-5, 100.0)
    assert n_max(sol) == pytest.approx(1.0, abs=1e-9)
    assert period_s(sol) == math.inf
    assert energy(sol) == pytest.approx(100.0, rel=1e-9)  # integral of 1 over 100 s
    assert math.isnan(t_double(sol))


def test_period_matches_inhour() -> None:
    rho = 0.3 * BETA
    sol = solve(Step(rho), DATA, 1e-5, 200.0)
    assert period_s(sol) == pytest.approx(1.0 / stable_omega(rho, DATA, 1e-5), rel=0.01)


def test_growing_step_peaks_at_the_end() -> None:
    sol = solve(Step(0.3 * BETA), DATA, 1e-5, 200.0)
    assert t_peak(sol) == 200.0
    assert n_max(sol) == sol.n[-1]


def test_t_double_is_where_power_reaches_two() -> None:
    rho = 0.3 * BETA
    sol = solve(Step(rho), DATA, 1e-5, 200.0)
    td = t_double(sol)
    assert 0.0 < td < 200.0
    n_at = expm_solution(rho, DATA, 1e-5, np.array([td]))[0, 0]
    assert n_at == pytest.approx(2.0, rel=1e-3)


def test_t_double_is_nan_for_negative_step() -> None:
    sol = solve(Step(-0.5 * BETA), DATA, 1e-5, 200.0)
    assert math.isnan(t_double(sol))
    assert period_s(sol) < 0.0  # decaying: negative period


def test_t_double_prompt_jump_is_resolved_at_a_grid_point() -> None:
    # A $0.6 step jumps to 2.5 in microseconds: first crossing is within the first points.
    sol = solve(Step(0.6 * BETA), DATA, 1e-7, 10.0)
    assert t_double(sol) < 1e-3


def test_energy_trapezoid_on_known_curve() -> None:
    sol = solve(Step(-0.5 * BETA), DATA, 1e-5, 200.0)
    manual = float(np.sum(0.5 * (sol.n[1:] + sol.n[:-1]) * np.diff(sol.t)))
    assert energy(sol) == pytest.approx(manual, rel=1e-12)


def test_feedback_metrics() -> None:
    fb = StepFeedback(rho0=0.3 * BETA, alpha=-2e-5)
    sol = solve(fb, DATA, 1e-5, 3000.0)
    m = compute_metrics(sol)
    assert m["n_max"] > fb.equilibrium_power()
    assert 10.0 < m["t_peak"] < 1000.0
    assert m["period_s"] == math.inf or abs(m["period_s"]) > 1e3  # settled: flat
    assert m["energy"] > 3000.0


def test_compute_metrics_keys_and_types() -> None:
    sol = solve(Step(0.1 * BETA), DATA, 1e-5, 100.0)
    m = compute_metrics(sol)
    assert list(m) == [
        "n_max",
        "t_peak",
        "period_s",
        "energy",
        "t_double",
        "wall_time_s",
        "nfev",
        "njev",
    ]
    assert isinstance(m["nfev"], int)
    assert isinstance(m["njev"], int)
    assert m["nfev"] == sol.nfev
    assert m["njev"] == sol.njev
    assert m["wall_time_s"] == sol.wall_time_s
    assert all(isinstance(m[k], float) for k in ("n_max", "t_peak", "period_s", "energy"))
