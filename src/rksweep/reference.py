"""Exact and analytic answers used to check the ODE solver.

Nothing here integrates an ODE. Three independent routes to the truth:

1. **Matrix exponential** (:func:`expm_solution`). For a constant ``rho`` the equations
   are linear, ``dx/dt = A x``, so ``x(t) = expm(A t) x(0)`` exactly.
2. **Inhour equation** (:func:`stable_omega`). After the transients die out the power
   grows (or decays) as ``exp(omega t)``; ``omega`` is the largest root of the inhour
   equation, found with ``brentq``.
3. **Prompt-jump approximation** (:func:`prompt_jump_n`). A small step in reactivity makes
   the power jump almost instantly to ``beta / (beta - rho)`` times its old value.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.linalg import expm
from scipy.optimize import brentq

from rksweep.constants import DelayedData

FloatArray = NDArray[np.float64]


def step_matrix(rho: float, data: DelayedData, gen_time: float) -> FloatArray:
    """The matrix ``A`` in ``dx/dt = A x`` for constant ``rho``, ``x = [n, C_1..C_G]``.

    ``A[0, 0] = (rho - beta)/Lambda``, ``A[0, i] = lambda_i``,
    ``A[i, 0] = beta_i/Lambda``, ``A[i, i] = -lambda_i``; everything else is zero.
    """
    g = data.n_groups
    A = np.zeros((g + 1, g + 1))
    A[0, 0] = (rho - data.beta) / gen_time
    for i in range(g):  # clarity over speed: this is the reference, written entry by entry
        A[0, i + 1] = data.lam[i]
        A[i + 1, 0] = data.beta_i[i] / gen_time
        A[i + 1, i + 1] = -data.lam[i]
    return A


def initial_state(data: DelayedData, gen_time: float, n0: float = 1.0) -> FloatArray:
    """Steady state ``[n0, beta_i n0 / (lambda_i Lambda)]``, written out entry by entry."""
    x0 = np.empty(data.n_groups + 1)
    x0[0] = n0
    for i in range(data.n_groups):
        x0[i + 1] = data.beta_i[i] * n0 / (data.lam[i] * gen_time)
    return x0


def expm_solution(
    rho: float, data: DelayedData, gen_time: float, t: FloatArray, n0: float = 1.0
) -> FloatArray:
    """Exact ``x(t) = expm(A t) x(0)`` for a step ``rho`` applied at ``t = 0``.

    Returns an array of shape ``(G + 1, len(t))`` (row 0 is ``n``, rows 1.. are ``C_i``).
    """
    A = step_matrix(rho, data, gen_time)
    x0 = initial_state(data, gen_time, n0)
    out = np.empty((x0.size, len(t)))
    for k, tk in enumerate(t):
        out[:, k] = expm(A * tk) @ x0
    return out


def inhour_residual(omega: float, rho: float, data: DelayedData, gen_time: float) -> float:
    """``omega*Lambda + sum_i beta_i*omega/(omega+lambda_i) - rho``; zero at a valid ``omega``.

    This is the inhour equation. Derivation in one line: look for solutions
    ``n, C_i proportional to exp(omega t)``; the precursor equations give
    ``C_i = beta_i n / (Lambda (omega + lambda_i))``; substituting into the ``n`` equation
    and multiplying by ``Lambda`` gives ``rho = omega Lambda + sum beta_i omega/(omega+lambda_i)``.
    """
    return float(omega * gen_time + np.sum(data.beta_i * omega / (omega + data.lam)) - rho)


def stable_omega(rho: float, data: DelayedData, gen_time: float) -> float:
    """Largest root ``omega`` (1/s) of the inhour equation: the asymptotic growth rate.

    The residual is strictly increasing in ``omega`` on the bracket used, so the root is
    unique there:

    * ``rho > 0``: bracket ``[0, rho/Lambda]``. At 0 the residual is ``-rho < 0``; at
      ``rho/Lambda`` the first term alone equals ``rho`` and the sum is positive, so the
      residual is ``> 0``.
    * ``rho < 0``: the largest root lies in ``(-lambda_1, 0)``, where ``lambda_1`` is the
      smallest decay constant. Near ``-lambda_1`` the residual goes to ``-infinity``; at 0
      it is ``-rho > 0``.
    * ``rho = 0``: ``omega = 0`` (steady state).
    """
    if rho == 0.0:
        return 0.0
    if rho > 0.0:
        lo, hi = 0.0, rho / gen_time
    else:
        lo, hi = -float(np.min(data.lam)) + 1e-12, 0.0
    return float(
        brentq(inhour_residual, lo, hi, args=(rho, data, gen_time), xtol=1e-14, rtol=1e-14)
    )


def stable_period(rho: float, data: DelayedData, gen_time: float) -> float:
    """Stable period ``1/omega`` in seconds (``inf`` at ``rho = 0``; negative for ``rho < 0``)."""
    omega = stable_omega(rho, data, gen_time)
    return float("inf") if omega == 0.0 else 1.0 / omega


def prompt_jump_n(rho: float, data: DelayedData, n0: float = 1.0) -> float:
    """Prompt-jump approximation ``n0 * beta / (beta - rho)`` (for ``rho < beta``).

    Right after a step, the power changes much faster than the precursors can respond, so
    ``C_i`` is frozen at its old value. Setting ``dn/dt = 0`` in the ``n`` equation with
    ``C_i`` fixed gives ``(beta - rho) n / Lambda = sum lambda_i C_i = beta n0 / Lambda``.
    """
    if rho >= data.beta:
        raise ValueError("prompt jump needs rho < beta")
    return n0 * data.beta / (data.beta - rho)


def prompt_jump_time(rho: float, data: DelayedData, gen_time: float, n_taus: float = 10.0) -> float:
    """A time by which the fast transient is over: ``n_taus * Lambda / (beta - rho)``.

    The fast transient decays as ``exp(-(beta - rho) t / Lambda)``.
    """
    return n_taus * gen_time / (data.beta - rho)
