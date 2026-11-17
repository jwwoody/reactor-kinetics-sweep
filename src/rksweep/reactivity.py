"""Reactivity inputs: how the reactor is pushed out of balance.

Reactivity ``rho`` is the fractional distance from criticality. ``rho = 0`` means the chain
reaction exactly sustains itself, ``rho > 0`` means the neutron population grows,
``rho < 0`` means it dies away. It is usually quoted in *dollars*: ``rho / beta``, where
``beta`` is the delayed-neutron fraction. One dollar is the edge of *prompt criticality*:
at ``rho >= beta`` the chain reaction runs on prompt neutrons alone and the response time
collapses from seconds to microseconds. The equations are still solvable there, but a sweep
should never wander in by accident, so every input can report its peak reactivity and the
solver refuses prompt-critical inputs unless explicitly told to allow them
(:class:`PromptCriticalError`).

Each input is a small frozen dataclass with

* ``__call__(t, temperature=0.0) -> rho`` (absolute units, not dollars),
* ``to_dict()`` for storage (JSON-friendly, round-trips through :func:`input_from_dict`),
* ``peak_rho(t_end)``, the largest reactivity it can reach on ``[0, t_end]``.

The first three are *external* inputs (a function of time only). :class:`StepFeedback` adds
a lumped fuel temperature as an extra state variable, which makes the problem nonlinear.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from rksweep.constants import DelayedData


class PromptCriticalError(ValueError):
    """Raised when an input reaches ``rho >= beta`` (one dollar or more)."""


class ReactivityInput(ABC):
    """Base class for reactivity inputs. Subclasses are frozen dataclasses."""

    #: Registry name used in configs and ``to_dict()["type"]``.
    kind: ClassVar[str]
    #: True if the input needs a temperature state variable (see :class:`StepFeedback`).
    has_feedback: ClassVar[bool] = False

    @abstractmethod
    def __call__(self, t: float, temperature: float = 0.0) -> float:
        """Reactivity (absolute, not dollars) at time ``t`` seconds.

        ``temperature`` is the lumped temperature rise above the start (K). External
        inputs ignore it.
        """

    @abstractmethod
    def peak_rho(self, t_end: float) -> float:
        """Largest reactivity this input can reach on ``[0, t_end]``."""

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly description, including ``"type"``."""

    def is_prompt_critical(self, beta: float, t_end: float) -> bool:
        """True if ``rho >= beta`` anywhere on ``[0, t_end]``."""
        return self.peak_rho(t_end) >= beta

    def check_prompt_critical(self, beta: float, t_end: float) -> None:
        """Raise :class:`PromptCriticalError` if the input reaches ``rho >= beta``."""
        peak = self.peak_rho(t_end)
        if peak >= beta:
            raise PromptCriticalError(
                f"{self.kind} input reaches rho = {peak:.6g} = ${peak / beta:.3f} "
                f">= beta = {beta:.6g} (prompt critical)"
            )


@dataclass(frozen=True)
class Step(ReactivityInput):
    """Constant reactivity ``rho0`` from t = 0 onwards (a sudden rod move).

    For this input the equations are linear with constant coefficients, so an exact
    matrix-exponential answer exists (see :mod:`rksweep.reference`).
    """

    rho0: float
    kind: ClassVar[str] = "step"

    def __call__(self, t: float, temperature: float = 0.0) -> float:
        return self.rho0 if t >= 0.0 else 0.0

    def peak_rho(self, t_end: float) -> float:
        return max(self.rho0, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "rho0": self.rho0}


@dataclass(frozen=True)
class Ramp(ReactivityInput):
    """Reactivity changes linearly, ``rate * t``, until it hits ``rho_max`` (rod withdrawal).

    ``rate`` is in 1/s (absolute reactivity per second). ``rho_max`` is the cap and has the
    same sign as ``rate``; once ``rate * t`` reaches it, reactivity stays there.
    """

    rate: float
    rho_max: float
    kind: ClassVar[str] = "ramp"

    def __post_init__(self) -> None:
        if self.rate * self.rho_max < 0.0:
            raise ValueError("rate and rho_max must have the same sign")

    def __call__(self, t: float, temperature: float = 0.0) -> float:
        if t <= 0.0:
            return 0.0
        value = self.rate * t
        if self.rate >= 0.0:
            return min(value, self.rho_max)
        return max(value, self.rho_max)

    def peak_rho(self, t_end: float) -> float:
        if self.rate <= 0.0:
            return 0.0
        return min(self.rate * t_end, self.rho_max)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "rate": self.rate, "rho_max": self.rho_max}


@dataclass(frozen=True)
class Sine(ReactivityInput):
    """``rho0 * sin(2 pi f t)``: a periodic disturbance.

    Plotting the power response against ``f`` shows the reactor acting as a low-pass
    filter: slow wiggles pass through, fast ones are smoothed out by the delayed neutrons.
    """

    rho0: float
    freq_hz: float
    kind: ClassVar[str] = "sine"

    def __call__(self, t: float, temperature: float = 0.0) -> float:
        return self.rho0 * math.sin(2.0 * math.pi * self.freq_hz * t)

    def peak_rho(self, t_end: float) -> float:
        # Conservative: the amplitude, even if t_end is shorter than a quarter period.
        return abs(self.rho0)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, "rho0": self.rho0, "freq_hz": self.freq_hz}


@dataclass(frozen=True)
class StepFeedback(ReactivityInput):
    """A step ``rho0`` plus lumped temperature feedback (the nonlinear case).

    Total reactivity is ``rho = rho0 + alpha * T`` where ``T`` is the fuel temperature
    *rise* above its starting value (K) and obeys a one-lump heat balance

    ``dT/dt = power_coeff * n - T / tau``

    ``power_coeff`` (K/s per unit relative power) is how fast power heats the fuel and
    ``tau`` (s) is how fast the heat leaks away. With a negative ``alpha`` (the usual,
    stable case) a hotter core is less reactive, so the power rises after the step, heats
    the fuel, loses reactivity, peaks, and settles where ``rho = 0``, i.e. at
    ``n = -rho0 / (alpha * power_coeff * tau)``. This is a teaching toy: the temperature
    scale is arbitrary, not a model of any real core.

    ``T`` is one extra state variable, so the state vector is ``[n, C_1..C_6, T]``.
    """

    rho0: float
    alpha: float
    power_coeff: float = 0.1
    tau: float = 100.0
    kind: ClassVar[str] = "step_feedback"
    has_feedback: ClassVar[bool] = True

    def __post_init__(self) -> None:
        if self.tau <= 0.0:
            raise ValueError("tau must be positive")

    def __call__(self, t: float, temperature: float = 0.0) -> float:
        return self.rho0 + self.alpha * temperature

    def peak_rho(self, t_end: float) -> float:
        # T starts at 0 and only rises (n >= 0), so with alpha <= 0 reactivity never
        # exceeds rho0. With alpha > 0 it can run away, so we report that as unbounded.
        if self.alpha > 0.0:
            return math.inf
        return max(self.rho0, 0.0)

    def equilibrium_power(self) -> float:
        """Settled relative power where feedback cancels the step (needs ``alpha < 0``)."""
        return -self.rho0 / (self.alpha * self.power_coeff * self.tau)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind,
            "rho0": self.rho0,
            "alpha": self.alpha,
            "power_coeff": self.power_coeff,
            "tau": self.tau,
        }


INPUT_TYPES: dict[str, type[ReactivityInput]] = {
    cls.kind: cls for cls in (Step, Ramp, Sine, StepFeedback)
}


def input_from_dict(d: dict[str, Any]) -> ReactivityInput:
    """Rebuild an input from its ``to_dict()`` output."""
    fields = dict(d)
    kind = fields.pop("type")
    try:
        cls = INPUT_TYPES[kind]
    except KeyError:
        raise KeyError(f"unknown input type {kind!r}; known: {sorted(INPUT_TYPES)}") from None
    return cls(**fields)


def build_input(name: str, data: DelayedData, params: dict[str, Any]) -> ReactivityInput:
    """Build an input from a config ``input`` name and sweep parameters.

    Parameters use the sweep vocabulary (reactivity in dollars; converted here, once, with
    ``data.dollars_to_rho``):

    =================  =====================================================
    ``step``           ``rho_dollars``
    ``ramp``           ``ramp_rate_dps`` (dollars/s), ``rho_dollars`` (the cap)
    ``sine``           ``rho_dollars`` (amplitude), ``freq_hz`` (default 1.0)
    ``step_feedback``  ``rho_dollars``, ``alpha_fb`` (1/K), optional
                       ``fb_power_coeff`` (K/s, default 0.1), ``fb_tau_s`` (s, default 100.0)
    =================  =====================================================

    Unrelated keys (for example ``gen_time_s``) are ignored.
    """
    to_rho = data.dollars_to_rho
    if name == "step":
        return Step(rho0=to_rho(params["rho_dollars"]))
    if name == "ramp":
        return Ramp(rate=to_rho(params["ramp_rate_dps"]), rho_max=to_rho(params["rho_dollars"]))
    if name == "sine":
        return Sine(rho0=to_rho(params["rho_dollars"]), freq_hz=params.get("freq_hz", 1.0))
    if name == "step_feedback":
        return StepFeedback(
            rho0=to_rho(params["rho_dollars"]),
            alpha=params["alpha_fb"],
            power_coeff=params.get("fb_power_coeff", 0.1),
            tau=params.get("fb_tau_s", 100.0),
        )
    raise KeyError(f"unknown input {name!r}; known: {sorted(INPUT_TYPES)}")
