"""Benchmark the ODE solvers on a representative reactivity step.

Compares RK45 (explicit), Radau and BDF (implicit, stiff) and LSODA (automatic switching),
with and without the analytic Jacobian where it applies, and prints a Markdown table.

RK45 must keep its step below the fast time scale (about ``Lambda / beta``) for stability
even when the solution is smooth, so it is impractically slow over 200 s. It is run over a
shorter horizon in the fast-spectrum case (its ``t_end`` column says so); compare its cost
per simulated second there, not the raw totals.

Usage::

    python benchmarks/solver.py [--repeats 3] [--t-end 200]
"""

from __future__ import annotations

import argparse
import platform
import sys

import numpy as np
import scipy

from rksweep.constants import KEEPIN_U235_THERMAL
from rksweep.kinetics import solve
from rksweep.reactivity import Step

DATA = KEEPIN_U235_THERMAL
DOLLARS = 0.3
# (label, generation time, RK45 horizon in s or None for the full t_end)
CASES: list[tuple[str, float, float | None]] = [
    ("thermal, L=1e-5 s", 1e-5, None),
    ("fast, L=1e-7 s", 1e-7, 2.0),  # RK45 would need ~100x the steps for the full 200 s
]
# (method, use_jac)
CONFIGS = [
    ("RK45", False),
    ("Radau", True),
    ("Radau", False),
    ("BDF", True),
    ("BDF", False),
    ("LSODA", True),
    ("LSODA", False),
]


def best_of(repeats: int, **kwargs: object) -> tuple[float, object]:
    """Run ``solve`` ``repeats`` times; return the best wall time and the last Solution."""
    best = float("inf")
    sol = None
    for _ in range(repeats):
        sol = solve(**kwargs)  # type: ignore[arg-type]
        best = min(best, sol.wall_time_s)
    return best, sol


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--t-end", type=float, default=200.0)
    args = parser.parse_args()

    rho = Step(DATA.dollars_to_rho(DOLLARS))
    print(
        f"Step ${DOLLARS}, rtol=1e-8, atol=1e-12, best of {args.repeats}. "
        f"Python {platform.python_version()}, NumPy {np.__version__}, SciPy {scipy.__version__}, "
        f"{platform.system()} {platform.machine()}.\n",
        file=sys.stderr,
    )
    print("| Case | Method | Jacobian | t_end (s) | Wall (ms) | Steps | nfev | njev | rhs calls |")
    print("|---|---|---|---:|---:|---:|---:|---:|---:|")
    for label, gen_time, rk45_t_end in CASES:
        for method, use_jac in CONFIGS:
            t_end = rk45_t_end if (method == "RK45" and rk45_t_end) else args.t_end
            wall, sol = best_of(
                args.repeats,
                rho=rho,
                data=DATA,
                gen_time=gen_time,
                t_end=t_end,
                method=method,
                use_jac=use_jac,
            )
            jac = "n/a" if method == "RK45" else ("analytic" if use_jac else "finite diff")
            print(
                f"| {label} | {method} | {jac} | {t_end:g} | {wall * 1e3:.1f} | {sol.nsteps} | "  # type: ignore[attr-defined]
                f"{sol.nfev} | {sol.njev} | {sol.rhs_calls} |"  # type: ignore[attr-defined]
            )


if __name__ == "__main__":
    main()
