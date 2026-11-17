"""Tests for reactivity inputs, the prompt-critical guard and the feedback model."""

from __future__ import annotations

import math

import numpy as np
import pytest

from rksweep.constants import KEEPIN_U235_THERMAL
from rksweep.kinetics import jacobian, rhs, solve, steady_state
from rksweep.reactivity import (
    PromptCriticalError,
    Ramp,
    Sine,
    Step,
    StepFeedback,
    build_input,
    input_from_dict,
)

DATA = KEEPIN_U235_THERMAL
BETA = DATA.beta


def test_step() -> None:
    s = Step(0.002)
    assert s(0.0) == s(100.0) == 0.002
    assert s.to_dict() == {"type": "step", "rho0": 0.002}


def test_ramp_rises_then_caps() -> None:
    r = Ramp(rate=1e-4, rho_max=3e-4)
    assert r(0.0) == 0.0
    assert r(1.0) == pytest.approx(1e-4)
    assert r(3.0) == pytest.approx(3e-4)
    assert r(100.0) == 3e-4
    assert r.peak_rho(1.0) == pytest.approx(1e-4)
    assert r.peak_rho(100.0) == 3e-4
    assert r.to_dict() == {"type": "ramp", "rate": 1e-4, "rho_max": 3e-4}


def test_negative_ramp_caps_below_zero() -> None:
    r = Ramp(rate=-1e-4, rho_max=-3e-4)
    assert r(1.0) == pytest.approx(-1e-4)
    assert r(100.0) == -3e-4
    assert r.peak_rho(100.0) == 0.0
    with pytest.raises(ValueError, match="same sign"):
        Ramp(rate=1e-4, rho_max=-1e-4)


def test_sine() -> None:
    s = Sine(rho0=1e-3, freq_hz=0.5)
    assert s(0.0) == 0.0
    assert s(0.5) == pytest.approx(1e-3 * math.sin(math.pi / 2 * 2 * 0.5))
    assert s(0.5) == pytest.approx(1e-3 * math.sin(2 * math.pi * 0.5 * 0.5))
    assert s.peak_rho(10.0) == 1e-3
    assert s.to_dict() == {"type": "sine", "rho0": 1e-3, "freq_hz": 0.5}


@pytest.mark.parametrize(
    "inp",
    [
        Step(0.001),
        Ramp(1e-4, 5e-4),
        Sine(1e-3, 2.0),
        StepFeedback(0.002, -2e-5, 0.2, 50.0),
    ],
)
def test_to_dict_round_trip(inp: object) -> None:
    d = inp.to_dict()  # type: ignore[attr-defined]
    assert input_from_dict(d) == inp
    # JSON-friendly: only plain Python scalars and strings.
    assert all(isinstance(v, (str, float, int)) for v in d.values())


def test_input_from_dict_unknown_type() -> None:
    with pytest.raises(KeyError, match="unknown input type"):
        input_from_dict({"type": "nope"})


def test_build_input_converts_dollars_once() -> None:
    s = build_input("step", DATA, {"rho_dollars": 0.3, "gen_time_s": 1e-5})
    assert s == Step(rho0=0.3 * BETA)
    r = build_input("ramp", DATA, {"rho_dollars": 0.5, "ramp_rate_dps": 0.1})
    assert r == Ramp(rate=0.1 * BETA, rho_max=0.5 * BETA)
    si = build_input("sine", DATA, {"rho_dollars": 0.2})
    assert si == Sine(rho0=0.2 * BETA, freq_hz=1.0)
    fb = build_input("step_feedback", DATA, {"rho_dollars": 0.3, "alpha_fb": -2e-5})
    assert fb == StepFeedback(rho0=0.3 * BETA, alpha=-2e-5)
    with pytest.raises(KeyError, match="unknown input"):
        build_input("nope", DATA, {})


# Prompt-critical guard ----------------------------------------------------------------


@pytest.mark.parametrize(
    "inp",
    [
        Step(BETA),
        Step(1.2 * BETA),
        Ramp(rate=BETA, rho_max=2 * BETA),
        Sine(rho0=BETA, freq_hz=1.0),
        StepFeedback(rho0=BETA, alpha=-1e-5),
        StepFeedback(rho0=0.1 * BETA, alpha=+1e-5),  # positive feedback: unbounded
    ],
)
def test_prompt_critical_detected_and_refused(inp: Step) -> None:
    assert inp.is_prompt_critical(BETA, t_end=10.0)
    with pytest.raises(PromptCriticalError, match="prompt critical"):
        inp.check_prompt_critical(BETA, t_end=10.0)
    with pytest.raises(PromptCriticalError):
        solve(inp, DATA, 1e-5, 10.0)


def test_prompt_critical_is_a_value_error() -> None:
    assert issubclass(PromptCriticalError, ValueError)


def test_sub_prompt_inputs_pass() -> None:
    for inp in (Step(0.99 * BETA), Ramp(rate=0.01 * BETA, rho_max=0.9 * BETA), Step(-5 * BETA)):
        assert not inp.is_prompt_critical(BETA, t_end=10.0)
        inp.check_prompt_critical(BETA, t_end=10.0)


def test_slow_ramp_that_has_not_reached_one_dollar_is_fine() -> None:
    ramp = Ramp(rate=0.01 * BETA, rho_max=2 * BETA)  # would pass $1 only after 100 s
    assert not ramp.is_prompt_critical(BETA, t_end=50.0)
    assert ramp.is_prompt_critical(BETA, t_end=150.0)


def test_prompt_critical_can_be_allowed() -> None:
    sol = solve(Step(1.5 * BETA), DATA, 1e-5, 0.05, allow_prompt_critical=True)
    assert sol.n[-1] > 1e3  # runs away on prompt neutrons in milliseconds


# Ramp and sine through the solver -----------------------------------------------------


def test_ramp_solve_grows_and_stays_finite() -> None:
    sol = solve(Ramp(rate=0.01 * BETA, rho_max=0.3 * BETA), DATA, 1e-5, 100.0)
    assert np.all(np.isfinite(sol.n))
    assert sol.n[-1] > 1.0


def test_sine_solve_oscillates_around_one() -> None:
    sol = solve(Sine(rho0=0.01 * BETA, freq_hz=0.5), DATA, 1e-5, 20.0)
    assert np.all(sol.n > 0.0)
    assert sol.n.max() > 1.0 > sol.n.min()


# Feedback -----------------------------------------------------------------------------


def make_fb(dollars: float = 0.3, alpha: float = -2e-5) -> StepFeedback:
    return StepFeedback(rho0=dollars * BETA, alpha=alpha)


def test_feedback_adds_a_state_and_reactivity_depends_on_it() -> None:
    fb = make_fb()
    assert fb.has_feedback
    assert fb(0.0, temperature=0.0) == pytest.approx(0.3 * BETA)
    assert fb(0.0, temperature=10.0) == pytest.approx(0.3 * BETA - 2e-4)
    x = steady_state(DATA, 1e-5, temperature=0.0)
    assert x.size == 8
    assert rhs(0.0, x, fb, DATA, 1e-5).size == 8
    assert jacobian(0.0, x, fb, DATA, 1e-5).shape == (8, 8)


def test_feedback_jacobian_matches_finite_differences() -> None:
    fb = StepFeedback(rho0=0.3 * BETA, alpha=-2e-5, power_coeff=0.3, tau=40.0)
    x = steady_state(DATA, 1e-5, temperature=0.0)
    x = x * 2.5
    x[-1] = 7.0
    J = jacobian(1.0, x, fb, DATA, 1e-5)
    fd = np.empty_like(J)
    for j in range(x.size):
        h = 1e-6 * max(abs(x[j]), 1.0)
        xp, xm = x.copy(), x.copy()
        xp[j] += h
        xm[j] -= h
        fd[:, j] = (rhs(1.0, xp, fb, DATA, 1e-5) - rhs(1.0, xm, fb, DATA, 1e-5)) / (2 * h)
    np.testing.assert_allclose(J, fd, rtol=1e-6, atol=1e-6)


@pytest.mark.parametrize("alpha", [-5e-6, -2e-5, -5e-5])
def test_feedback_peaks_and_settles(alpha: float) -> None:
    fb = make_fb(0.3, alpha)
    sol = solve(fb, DATA, 1e-5, t_end=3000.0)
    n_eq = fb.equilibrium_power()
    k = int(np.argmax(sol.n))
    assert sol.T is not None
    # Rises: power climbs well above its starting value.
    assert sol.n.max() > 2.0
    # Peaks: an interior maximum that overshoots the settled power.
    assert 0 < k < sol.n.size - 1
    assert sol.n[k] > 1.1 * n_eq
    # Settles: ends at the power where feedback cancels the step, and temperature too.
    assert sol.n[-1] == pytest.approx(n_eq, rel=0.01)
    assert sol.T[-1] == pytest.approx(fb.power_coeff * fb.tau * n_eq, rel=0.01)
    # Self-limiting: total reactivity returns to zero.
    assert fb(0.0, float(sol.T[-1])) == pytest.approx(0.0, abs=2e-6 * BETA)


def test_feedback_stronger_alpha_means_lower_peak() -> None:
    weak = solve(make_fb(0.3, -5e-6), DATA, 1e-5, 500.0)
    strong = solve(make_fb(0.3, -5e-5), DATA, 1e-5, 500.0)
    assert strong.n.max() < weak.n.max()


def test_feedback_with_zero_alpha_matches_plain_step() -> None:
    plain = solve(Step(0.3 * BETA), DATA, 1e-5, 100.0)
    fb = solve(StepFeedback(0.3 * BETA, alpha=0.0), DATA, 1e-5, 100.0)
    np.testing.assert_allclose(fb.n, plain.n, rtol=1e-6)


@pytest.mark.parametrize("method", ["Radau", "BDF", "LSODA"])
def test_feedback_solvers_agree(method: str) -> None:
    ref = solve(make_fb(), DATA, 1e-5, 300.0)
    other = solve(make_fb(), DATA, 1e-5, 300.0, method=method)
    np.testing.assert_allclose(other.n, ref.n, rtol=1e-5)


def test_feedback_validation() -> None:
    with pytest.raises(ValueError, match="tau"):
        StepFeedback(rho0=1e-3, alpha=-1e-5, tau=0.0)
