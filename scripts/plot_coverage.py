"""Plot design coverage: a grid versus a Latin hypercube with the same number of runs.

Usage::

    python scripts/plot_coverage.py OUTPUT.png [--side 8] [--seed 42]

Both panels sweep ``rho_dollars`` (linear, 0.05-0.9) and ``gen_time_s`` (log, 1e-7 to 1e-4).
A ``side x side`` grid has only ``side`` distinct values of each parameter; a Latin
hypercube with the same ``side**2`` runs has ``side**2`` distinct values of each, so each
parameter's range is covered more evenly. Needs matplotlib (``pip install -e ".[plot]"``).
"""

from __future__ import annotations

import argparse
import sys

from rksweep.sweep import ParamSpec, grid_design, lhs_design

RHO = (0.05, 0.9)
GEN = (1e-7, 1e-4)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output", help="path of the PNG to write")
    parser.add_argument("--side", type=int, default=8, help="grid points per parameter")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        sys.exit('matplotlib is required: pip install -e ".[plot]"')

    side = args.side
    grid = grid_design(
        {
            "rho_dollars": ParamSpec(values=tuple(np.linspace(*RHO, side))),
            "gen_time_s": ParamSpec(values=tuple(np.geomspace(*GEN, side))),
        }
    )
    lhs = lhs_design(
        {
            "rho_dollars": ParamSpec(low=RHO[0], high=RHO[1]),
            "gen_time_s": ParamSpec(low=GEN[0], high=GEN[1], scale="log"),
        },
        side * side,
        args.seed,
    )

    fig, axes = plt.subplots(1, 2, figsize=(9, 4), sharex=True, sharey=True)
    for ax, points, title, color in (
        (axes[0], grid, f"Grid: {len(grid)} runs, {side} values per parameter", "#1f5f8b"),
        (axes[1], lhs, f"Latin hypercube: {len(lhs)} runs, {len(lhs)} values each", "#b5541c"),
    ):
        ax.scatter(
            [p["rho_dollars"] for p in points],
            [p["gen_time_s"] for p in points],
            s=22,
            color=color,
        )
        ax.set_yscale("log")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("step size (dollars)")
        ax.grid(True, which="major", alpha=0.3)
    axes[0].set_ylabel("generation time (s)")
    fig.tight_layout()
    fig.savefig(args.output, dpi=100)
    print(f"wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
