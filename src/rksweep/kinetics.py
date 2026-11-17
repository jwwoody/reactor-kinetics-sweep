"""Point-kinetics equations: right-hand side, Jacobian, steady state and ``solve()``.

The model
---------
Treat the whole core as one point. Let ``n`` be the neutron population (proportional to
power) and ``C_i`` the number of precursors of delayed group ``i`` (``i = 1..G``, ``G = 6``)::

    dn/dt   = ((rho(t) - beta) / Lambda) * n  +  sum_i lambda_i * C_i
    dC_i/dt = (beta_i / Lambda) * n           -  lambda_i * C_i

In words: prompt neutrons are produced at net rate ``(rho - beta)/Lambda`` per neutron (the
``- beta`` removes the fraction that will arrive late; ``Lambda`` is the prompt generation
time). Precursors are made in proportion to ``n`` and decay at rate ``lambda_i``, and each
decay hands one delayed neutron back to ``n``.

We stack the unknowns as ``x = [n, C_1, ..., C_G]``. With constant ``rho`` the system is
linear, ``dx/dt = A x``, and ``A`` is also its Jacobian.

Feedback case
-------------
For a :class:`~rksweep.reactivity.StepFeedback` input there is one more unknown, the
temperature rise ``T``, so ``x = [n, C_1, ..., C_G, T]`` and

    rho = rho0 + alpha * T
    dT/dt = K * n - T / tau

Now ``rho`` depends on the state ``T``, so ``dn/dt`` contains the product
``alpha * T * n / Lambda``: the system is nonlinear and the Jacobian gains an extra row and
column (see :func:`jacobian`).

Why the problem is stiff
------------------------
The fastest time scale is about ``Lambda / beta`` (milliseconds thermal, microseconds fast
spectrum); the slowest is about ``1 / lambda_1`` (80 s). A ratio of 1e4 to 1e6 between the
time scales is what "stiff" means. An explicit method (RK45) must keep its step below the
fast scale for stability even when nothing fast is happening, so it crawls. Implicit
methods (Radau, BDF) do not have that limit. They solve an equation involving the Jacobian
at every step, so supplying it analytically saves many function evaluations.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_ivp

from rksweep.constants import DelayedData
from rksweep.reactivity import ReactivityInput

FloatArray = NDArray[np.float64]
RhoLike = ReactivityInput | Callable[[float], float]

METHODS = ("Radau", "BDF", "LSODA", "RK45")
T_FIRST = 1e-6  # first nonzero output time, seconds


def _has_feedback(rho: RhoLike) -> bool:
    return isinstance(rho, ReactivityInput) and rho.has_feedback


def _reactivity(t: float, x: FloatArray, rho: RhoLike, g: int) -> float:
    """Current reactivity; for feedback inputs it is read from the temperature state."""
    if isinstance(rho, ReactivityInput) and rho.has_feedback:
        return rho(t, float(x[g + 1]))
    return rho(t)


def rhs(
    t: float,
    x: FloatArray,
    rho: RhoLike,
    data: DelayedData,
    gen_time: float,
) -> FloatArray:
    """Time derivative ``dx/dt`` of the state ``x = [n, C_1..C_G(, T)]``.

    Vectorized over the precursor groups (no Python loop). ``rho`` is either a
    :class:`~rksweep.reactivity.ReactivityInput` or a plain ``rho(t)`` function; the
    temperature entry is present only for feedback inputs.
    """
    g = data.n_groups
    n = x[0]
    C = x[1 : g + 1]
    lam = data.lam
    rho_now = _reactivity(t, x, rho, g)

    dx = np.empty_like(x)
    dx[0] = (rho_now - data.beta) / gen_time * n + lam @ C
    dx[1 : g + 1] = data.beta_i / gen_time * n - lam * C
    if _has_feedback(rho):
        fb: Any = rho
        dx[g + 1] = fb.power_coeff * n - x[g + 1] / fb.tau
    return dx


def jacobian(
    t: float,
    x: FloatArray,
    rho: RhoLike,
    data: DelayedData,
    gen_time: float,
) -> FloatArray:
    """Analytic Jacobian ``d(dx/dt)/dx`` (a square matrix the size of ``x``).

    Without feedback this is the matrix ``A`` from the module docstring::

        J[0, 0] = (rho - beta) / Lambda     J[0, i] = lambda_i
        J[i, 0] = beta_i / Lambda           J[i, i] = -lambda_i

    With feedback, ``rho`` depends on ``T`` so, by the product rule, ``dn/dt`` picks up
    ``d/dT [alpha * T * n / Lambda] = alpha * n / Lambda`` (column ``T`` of row ``n``).
    The temperature row is ``dT/dt = K n - T/tau``: ``J[T, 0] = K``, ``J[T, T] = -1/tau``.
    """
    g = data.n_groups
    size = x.size
    J = np.zeros((size, size))
    J[0, 0] = (_reactivity(t, x, rho, g) - data.beta) / gen_time
    J[0, 1 : g + 1] = data.lam
    J[1 : g + 1, 0] = data.beta_i / gen_time
    idx = np.arange(1, g + 1)
    J[idx, idx] = -data.lam
    if _has_feedback(rho):
        fb: Any = rho
        J[0, g + 1] = fb.alpha * x[0] / gen_time
        J[g + 1, 0] = fb.power_coeff
        J[g + 1, g + 1] = -1.0 / fb.tau
    return J


def steady_state(
    data: DelayedData,
    gen_time: float,
    n0: float = 1.0,
    temperature: float | None = None,
) -> FloatArray:
    """Equilibrium state at ``rho = 0``: ``C_i = beta_i * n0 / (lambda_i * Lambda)``.

    Setting ``dC_i/dt = 0`` gives that ratio; then ``dn/dt = -beta n/Lambda + sum lambda_i
    C_i = 0`` holds automatically because ``sum beta_i = beta``. Pass ``temperature`` (the
    starting temperature rise, normally 0.0) to append the extra feedback state.
    """
    x = np.concatenate(([n0], data.beta_i * n0 / (data.lam * gen_time)))
    if temperature is not None:
        x = np.append(x, temperature)
    return x.astype(np.float64)


def output_grid(t_end: float, n_points: int = 400) -> FloatArray:
    """Output times: ``t = 0`` followed by ``n_points`` log-spaced points to ``t_end``.

    Log spacing resolves both the microsecond transient and the minutes-long tail.
    """
    if t_end <= T_FIRST:
        raise ValueError(f"t_end must exceed {T_FIRST:g} s")
    return np.concatenate(([0.0], np.geomspace(T_FIRST, t_end, n_points)))


@dataclass
class Solution:
    """Result of :func:`solve`.

    Attributes:
        t: Output times, shape ``(N,)``; ``t[0] == 0``.
        n: Relative power, shape ``(N,)``.
        C: Precursor populations, shape ``(G, N)``.
        T: Temperature rise (K), shape ``(N,)``, or ``None`` without feedback.
        method: Solver name (``"Radau"``, ``"BDF"``, ``"LSODA"`` or ``"RK45"``).
        use_jac: Whether the analytic Jacobian was supplied.
        rtol, atol: Solver tolerances.
        nsteps: Accepted internal steps (not the number of output points).
        nfev, njev, nlu: Function, Jacobian and LU-decomposition counts from SciPy.
            Note SciPy's ``nfev`` does not include the extra right-hand-side calls it makes
            to estimate a Jacobian by finite differences when ``use_jac`` is False.
        rhs_calls: Every call to the right-hand side, including those finite-difference
            Jacobian calls. This is the honest cost for comparing ``use_jac`` on and off.
        wall_time_s: Wall-clock time of the solve, from ``time.perf_counter()``.
        gen_time: Prompt generation time ``Lambda`` used (s).
        data_set: Name of the delayed-neutron data set.
        input: ``to_dict()`` of the reactivity input.
    """

    t: FloatArray
    n: FloatArray
    C: FloatArray
    T: FloatArray | None
    method: str
    use_jac: bool
    rtol: float
    atol: float
    nsteps: int
    nfev: int
    njev: int
    nlu: int
    rhs_calls: int
    wall_time_s: float
    gen_time: float
    data_set: str
    input: dict[str, Any] = field(default_factory=dict)


def solve(
    rho: ReactivityInput,
    data: DelayedData,
    gen_time: float,
    t_end: float,
    method: str = "Radau",
    n_points: int = 400,
    *,
    use_jac: bool = True,
    rtol: float = 1e-8,
    atol: float = 1e-12,
    allow_prompt_critical: bool = False,
) -> Solution:
    """Integrate the point-kinetics equations from steady state at ``t = 0``.

    The reactor starts at ``n = 1`` with precursors in equilibrium (and ``T = 0`` for a
    feedback input), then ``rho`` is applied. Output is on :func:`output_grid`.

    Args:
        rho: Reactivity input (Step, Ramp, Sine, StepFeedback).
        data: Delayed-neutron data set.
        gen_time: Prompt generation time ``Lambda`` in seconds.
        t_end: Final time in seconds (must exceed 1e-6).
        method: ``"Radau"`` (default), ``"BDF"``, ``"LSODA"`` or ``"RK45"``.
        n_points: Number of log-spaced output points after ``t = 0``.
        use_jac: Pass the analytic Jacobian to implicit solvers. Set ``False`` to measure
            what it buys (SciPy then estimates it by finite differences). Ignored by RK45.
        rtol, atol: Solver tolerances.
        allow_prompt_critical: By default an input with ``rho >= beta`` raises
            :class:`~rksweep.reactivity.PromptCriticalError`.

    Raises:
        PromptCriticalError: if the input reaches ``rho >= beta`` and it is not allowed.
        ValueError: for an unknown method.
        RuntimeError: if the integrator fails.
    """
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}; choose from {METHODS}")
    if not allow_prompt_critical:
        rho.check_prompt_critical(data.beta, t_end)

    g = data.n_groups
    x0 = steady_state(data, gen_time, temperature=0.0 if rho.has_feedback else None)
    t_grid = output_grid(t_end, n_points)

    calls = 0

    def f(t: float, x: FloatArray) -> FloatArray:
        nonlocal calls
        calls += 1
        return rhs(t, x, rho, data, gen_time)

    def jac(t: float, x: FloatArray) -> FloatArray:
        return jacobian(t, x, rho, data, gen_time)

    jac_arg = jac if (use_jac and method != "RK45") else None
    extra: dict[str, Any] = {"jac": jac_arg} if method != "RK45" else {}  # RK45 rejects jac=

    start = time.perf_counter()
    # Let the solver choose its own steps (dense_output), then read the solution off its
    # interpolant at the output grid. This is exactly what solve_ivp(t_eval=...) does
    # internally, but it also lets us count the real steps taken.
    res = solve_ivp(
        f,
        (0.0, t_end),
        x0,
        method=method,
        rtol=rtol,
        atol=atol,
        dense_output=True,
        **extra,
    )
    if not res.success:
        raise RuntimeError(f"{method} failed: {res.message}")
    y = res.sol(t_grid)
    wall = time.perf_counter() - start

    return Solution(
        t=t_grid,
        n=np.ascontiguousarray(y[0]),
        C=np.ascontiguousarray(y[1 : g + 1]),
        T=np.ascontiguousarray(y[g + 1]) if rho.has_feedback else None,
        method=method,
        use_jac=jac_arg is not None,
        rtol=rtol,
        atol=atol,
        nsteps=len(res.t) - 1,
        nfev=int(res.nfev),
        njev=int(res.njev),
        nlu=int(res.nlu),
        rhs_calls=calls,
        wall_time_s=wall,
        gen_time=gen_time,
        data_set=data.name,
        input=rho.to_dict(),
    )
