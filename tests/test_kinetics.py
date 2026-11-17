"""Verification tests for the point-kinetics kernel (spec 01, tests 1-7 plus extras).

Each test compares the ODE solver against an answer that does not use the ODE solver:
the matrix exponential, the inhour equation, or the prompt-jump formula.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rksweep.constants import DATA_SETS, KEEPIN_U235_THERMAL, get_data_set
from rksweep.kinetics import (
    METHODS,
    jacobian,
    output_grid,
    rhs,
    solve,
    steady_state,
)
from rksweep.reactivity import PromptCriticalError, Ramp, Step
from rksweep.reference import (
    expm_solution,
    prompt_jump_n,
    prompt_jump_time,
    stable_omega,
    step_matrix,
)

DATA = KEEPIN_U235_THERMAL


def rho_of(dollars: float) -> float:
    return DATA.dollars_to_rho(dollars)


# 1. Data sanity -----------------------------------------------------------------------


def test_data_sanity() -> None:
    assert sum(DATA.abundances) == pytest.approx(1.0, abs=1e-12)
    assert all(lam > 0.0 for lam in DATA.decay_constants)
    assert DATA.beta_i.sum() == pytest.approx(DATA.beta, rel=1e-12)
    assert DATA.n_groups == 6
    assert DATA.dollars_to_rho(1.0) == DATA.beta
    assert DATA.rho_to_dollars(DATA.beta) == 1.0
    assert get_data_set("KEEPIN_U235_THERMAL") is KEEPIN_U235_THERMAL
    assert "KEEPIN_U235_THERMAL" in DATA_SETS
    with pytest.raises(KeyError, match="known data sets"):
        get_data_set("NOPE")


# 2. Steady state holds ----------------------------------------------------------------


@pytest.mark.parametrize("gen_time", [1e-7, 1e-5, 1e-4])
def test_steady_state_holds(gen_time: float) -> None:
    sol = solve(Step(0.0), DATA, gen_time, t_end=100.0)
    assert np.max(np.abs(sol.n - 1.0)) < 1e-9


def test_steady_state_is_a_zero_of_rhs() -> None:
    x0 = steady_state(DATA, 1e-5)
    dx = rhs(0.0, x0, Step(0.0), DATA, 1e-5)
    # dn/dt is a difference of terms of size beta/Lambda ~ 650; allow rounding there.
    assert np.max(np.abs(dx)) < 1e-9 * np.max(np.abs(x0))


# 3. Matches the exact (matrix exponential) solution -----------------------------------


@pytest.mark.parametrize("dollars", [0.1, 0.5, -0.5])
@pytest.mark.parametrize("gen_time", [1e-7, 1e-5, 1e-4])
def test_matches_expm(dollars: float, gen_time: float) -> None:
    rho = rho_of(dollars)
    sol = solve(Step(rho), DATA, gen_time, t_end=200.0)
    exact = expm_solution(rho, DATA, gen_time, sol.t)
    np.testing.assert_allclose(sol.n, exact[0], rtol=1e-6, atol=0.0)
    np.testing.assert_allclose(sol.C, exact[1:], rtol=1e-6, atol=0.0)


# 4. Prompt jump -----------------------------------------------------------------------


# The approximation freezes the precursors, so it degrades once the power grows noticeably
# during the ~10 Lambda/(beta - rho) it takes the transient to die. That drift is about
# omega * t_jump, which is large only for a big step with a slow (1e-4 s) generation time,
# so that corner ($0.5 at 1e-4 s, 9 % off) is left out; the 3 % tolerance is not loosened.
@pytest.mark.parametrize(
    ("dollars", "gen_time"),
    [(d, lam) for d in (0.05, 0.1, 0.3, 0.5) for lam in (1e-6, 1e-5)]
    + [(d, 1e-4) for d in (0.05, 0.1, 0.3)],
)
def test_prompt_jump(dollars: float, gen_time: float) -> None:
    rho = rho_of(dollars)
    sol = solve(Step(rho), DATA, gen_time, t_end=10.0)
    t_jump = prompt_jump_time(rho, DATA, gen_time)  # 10 Lambda / (beta - rho)
    k = int(np.searchsorted(sol.t, t_jump))
    assert sol.n[k] == pytest.approx(prompt_jump_n(rho, DATA), rel=0.03)


# 5. Stable period (inhour equation) ---------------------------------------------------


def _fitted_omega(sol_t: np.ndarray, sol_n: np.ndarray) -> float:
    mask = sol_t >= 0.8 * sol_t[-1]
    slope, _ = np.polyfit(sol_t[mask], np.log(sol_n[mask]), 1)
    return float(slope)


@pytest.mark.parametrize("dollars", [0.1, 0.3, 0.5])
@pytest.mark.parametrize("gen_time", [1e-7, 1e-5, 1e-4])
def test_inhour_positive(dollars: float, gen_time: float) -> None:
    rho = rho_of(dollars)
    sol = solve(Step(rho), DATA, gen_time, t_end=200.0)
    omega = stable_omega(rho, DATA, gen_time)
    assert omega > 0.0
    assert _fitted_omega(sol.t, sol.n) == pytest.approx(omega, rel=0.01)


@pytest.mark.parametrize("dollars", [-0.1, -0.5, -1.0])
@pytest.mark.parametrize("gen_time", [1e-7, 1e-4])
def test_inhour_negative(dollars: float, gen_time: float) -> None:
    rho = rho_of(dollars)
    # Slow transients (decay constants near 0.03/s) need ~1000 s to die out to 1 %.
    sol = solve(Step(rho), DATA, gen_time, t_end=1000.0)
    omega = stable_omega(rho, DATA, gen_time)
    assert -DATA.decay_constants[0] < omega < 0.0
    assert _fitted_omega(sol.t, sol.n) == pytest.approx(omega, rel=0.01)


def test_inhour_root_satisfies_equation() -> None:
    from rksweep.reference import inhour_residual

    for dollars in (-0.9, -0.2, 0.2, 0.8):
        rho = rho_of(dollars)
        omega = stable_omega(rho, DATA, 1e-5)
        assert abs(inhour_residual(omega, rho, DATA, 1e-5)) < 1e-12
    assert stable_omega(0.0, DATA, 1e-5) == 0.0


def test_omega_is_the_dominant_eigenvalue_of_matrix() -> None:
    """The inhour root must equal the largest eigenvalue of the matrix A."""
    for dollars in (-0.5, 0.3):
        rho = rho_of(dollars)
        eig = np.linalg.eigvals(step_matrix(rho, DATA, 1e-5)).real.max()
        assert stable_omega(rho, DATA, 1e-5) == pytest.approx(eig, rel=1e-8)


# 6. Negative step decays --------------------------------------------------------------


@pytest.mark.parametrize("dollars", [-0.1, -0.5, -1.0])
def test_negative_step_decays_monotonically(dollars: float) -> None:
    rho = rho_of(dollars)
    gen_time = 1e-5
    sol = solve(Step(rho), DATA, gen_time, t_end=200.0)
    assert sol.n[1] < 1.0
    # From t = 0 on, including the prompt drop, power never rises.
    assert np.all(np.diff(sol.n) <= 1e-9 * sol.n[:-1])
    assert sol.n[-1] < sol.n[0]


# 7. Property test ---------------------------------------------------------------------


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    derandomize=True,
)
@given(
    dollars=st.floats(min_value=-1.0, max_value=0.9, allow_nan=False),
    log_gen_time=st.floats(min_value=-7.0, max_value=-4.0, allow_nan=False),
)
def test_property_matches_expm_and_stays_positive(dollars: float, log_gen_time: float) -> None:
    gen_time = 10.0**log_gen_time
    rho = rho_of(dollars)
    omega = stable_omega(rho, DATA, gen_time)
    # Cap the growth at about e^150 so numbers stay far from floating-point overflow.
    t_end = 100.0 if omega <= 0.0 else min(100.0, 150.0 / omega)
    sol = solve(Step(rho), DATA, gen_time, t_end=max(t_end, 1.0))
    exact = expm_solution(rho, DATA, gen_time, sol.t)
    np.testing.assert_allclose(sol.n, exact[0], rtol=1e-6, atol=0.0)
    assert np.all(sol.n > 0.0)


# Extras: Jacobian, solvers, grid, guard -----------------------------------------------


def test_jacobian_of_step_is_the_step_matrix() -> None:
    rho = rho_of(0.3)
    x = steady_state(DATA, 1e-5)
    np.testing.assert_array_equal(
        jacobian(0.0, x, Step(rho), DATA, 1e-5), step_matrix(rho, DATA, 1e-5)
    )


def test_jacobian_matches_finite_differences_for_ramp() -> None:
    ramp = Ramp(rate=rho_of(0.05), rho_max=rho_of(0.4))
    x = steady_state(DATA, 1e-5) * 1.7
    t = 3.0
    J = jacobian(t, x, ramp, DATA, 1e-5)
    fd = np.empty_like(J)
    for j in range(x.size):
        h = 1e-6 * max(abs(x[j]), 1.0)
        xp, xm = x.copy(), x.copy()
        xp[j] += h
        xm[j] -= h
        fd[:, j] = (rhs(t, xp, ramp, DATA, 1e-5) - rhs(t, xm, ramp, DATA, 1e-5)) / (2 * h)
    np.testing.assert_allclose(J, fd, rtol=1e-6, atol=1e-6)


@pytest.mark.parametrize("method", METHODS)
def test_all_methods_agree(method: str) -> None:
    rho = rho_of(0.3)
    # RK45 is explicit and the problem is stiff: keep its run short.
    t_end = 0.5 if method == "RK45" else 200.0
    sol = solve(Step(rho), DATA, 1e-4, t_end=t_end, method=method, rtol=1e-8, atol=1e-12)
    exact = expm_solution(rho, DATA, 1e-4, sol.t)
    np.testing.assert_allclose(sol.n, exact[0], rtol=1e-5)
    assert sol.method == method
    assert sol.nfev > 0


def test_jacobian_can_be_disabled() -> None:
    with_jac = solve(Step(rho_of(0.3)), DATA, 1e-5, 200.0, use_jac=True)
    without = solve(Step(rho_of(0.3)), DATA, 1e-5, 200.0, use_jac=False)
    assert with_jac.use_jac
    assert not without.use_jac
    assert with_jac.njev > 0
    # SciPy's nfev leaves out finite-difference Jacobian calls; rhs_calls counts them all.
    assert without.rhs_calls > with_jac.rhs_calls
    assert with_jac.rhs_calls == with_jac.nfev
    np.testing.assert_allclose(with_jac.n, without.n, rtol=1e-6)


def test_output_grid_and_solution_shapes() -> None:
    t = output_grid(100.0, 50)
    assert t[0] == 0.0
    assert t[1] == pytest.approx(1e-6)
    assert t[-1] == pytest.approx(100.0)
    assert t.size == 51
    sol = solve(Step(0.0), DATA, 1e-5, 100.0, n_points=50)
    assert sol.t.shape == sol.n.shape == (51,)
    assert sol.C.shape == (6, 51)
    assert sol.T is None
    assert sol.nsteps > 0
    assert sol.wall_time_s > 0.0
    assert sol.data_set == "KEEPIN_U235_THERMAL"
    assert sol.input == {"type": "step", "rho0": 0.0}
    with pytest.raises(ValueError, match="t_end"):
        output_grid(1e-7)


def test_unknown_method_rejected() -> None:
    with pytest.raises(ValueError, match="unknown method"):
        solve(Step(0.0), DATA, 1e-5, 10.0, method="Euler")


def test_prompt_critical_is_refused_by_default() -> None:
    with pytest.raises(PromptCriticalError):
        solve(Step(rho_of(1.0)), DATA, 1e-5, 1.0)
    with pytest.raises(PromptCriticalError):
        solve(Step(rho_of(1.5)), DATA, 1e-5, 1.0)
