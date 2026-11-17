"""Delayed-neutron data sets.

A reactor's neutrons come in two flavours. Almost all are *prompt*: they appear within
about a microsecond of the fission that made them. A small fraction (about 0.65 % for
uranium-235 in a thermal reactor) are *delayed*: they are emitted later, when certain
fission products ("precursors") decay. Those few late neutrons are what make a reactor
controllable, because they stretch the response from microseconds to seconds.

Delayed neutrons are bookkept in six *groups*. Group ``i`` has

* a fraction ``beta_i`` of all fission neutrons, and
* a decay constant ``lambda_i`` (1/s): the precursors of that group decay, and so emit
  their neutron, with a mean life of ``1 / lambda_i`` seconds.

The total delayed fraction is ``beta = sum(beta_i)``. This module stores the *relative
abundances* ``a_i = beta_i / beta`` together with ``beta`` itself.

Source and caveat
-----------------
The numbers below are the classic six-group U-235 thermal-fission values from Keepin,
*Physics of Nuclear Kinetics* (1965), as reproduced in nuclear-engineering textbooks (for
example Duderstadt & Hamilton, *Nuclear Reactor Analysis*, 1976). They are **textbook
values for teaching**, not evaluated nuclear data, and must not be used for design or
safety work.

Adding another data set (say, U-235 fast fission) means defining another
:class:`DelayedData` and adding it to :data:`DATA_SETS`; the solver only ever sees the
:class:`DelayedData` object.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class DelayedData:
    """One named set of delayed-neutron parameters.

    Attributes:
        name: Registry key, e.g. ``"KEEPIN_U235_THERMAL"``.
        beta: Total delayed-neutron fraction (dimensionless).
        abundances: Relative abundance ``a_i = beta_i / beta`` of each group. Sums to 1.
        decay_constants: Precursor decay constant ``lambda_i`` of each group, in 1/s.
        source: Human-readable citation.
    """

    name: str
    beta: float
    abundances: tuple[float, ...]
    decay_constants: tuple[float, ...]
    source: str = ""

    def __post_init__(self) -> None:
        if len(self.abundances) != len(self.decay_constants):
            raise ValueError("abundances and decay_constants must have the same length")
        if len(self.abundances) == 0:
            raise ValueError("at least one delayed-neutron group is required")

    @property
    def n_groups(self) -> int:
        """Number of precursor groups (6 for the Keepin set)."""
        return len(self.abundances)

    @cached_property
    def beta_i(self) -> FloatArray:
        """Per-group delayed fractions ``beta_i = a_i * beta`` (array of length n_groups)."""
        return np.asarray(self.abundances, dtype=np.float64) * self.beta

    @cached_property
    def lam(self) -> FloatArray:
        """Per-group decay constants ``lambda_i`` in 1/s (array of length n_groups)."""
        return np.asarray(self.decay_constants, dtype=np.float64)

    def dollars_to_rho(self, dollars: float) -> float:
        """Convert reactivity in dollars to absolute ``rho``. The one place this is done."""
        return dollars * self.beta

    def rho_to_dollars(self, rho: float) -> float:
        """Convert absolute reactivity ``rho`` to dollars (``rho / beta``)."""
        return rho / self.beta


KEEPIN_U235_THERMAL = DelayedData(
    name="KEEPIN_U235_THERMAL",
    beta=0.0065,
    abundances=(0.033, 0.219, 0.196, 0.395, 0.115, 0.042),
    decay_constants=(0.0124, 0.0305, 0.111, 0.301, 1.14, 3.01),
    source="Keepin (1965), U-235 thermal fission, six groups; textbook values",
)

DATA_SETS: dict[str, DelayedData] = {KEEPIN_U235_THERMAL.name: KEEPIN_U235_THERMAL}


def get_data_set(name: str) -> DelayedData:
    """Look up a data set by name, with a helpful error for typos."""
    try:
        return DATA_SETS[name]
    except KeyError:
        known = ", ".join(sorted(DATA_SETS))
        raise KeyError(f"unknown data set {name!r}; known data sets: {known}") from None
