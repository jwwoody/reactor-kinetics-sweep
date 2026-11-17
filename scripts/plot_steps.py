"""Plot n(t) on log-log axes for steps of $0.1, $0.3, $0.5 and -$0.5.

Usage::

    python scripts/plot_steps.py OUTPUT.png [--gen-time 1e-5] [--t-end 200]

Needs matplotlib (``pip install -e ".[plot]"``). The image is written only where you say;
do not commit it.
"""

from __future__ import annotations

import argparse
import sys

from rksweep.constants import KEEPIN_U235_THERMAL
from rksweep.kinetics import solve
from rksweep.reactivity import Step

DOLLARS = (0.1, 0.3, 0.5, -0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output", help="path of the PNG to write")
    parser.add_argument("--gen-time", type=float, default=1e-5, help="Lambda in seconds")
    parser.add_argument("--t-end", type=float, default=200.0)
    args = parser.parse_args()

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit('matplotlib is required: pip install -e ".[plot]"')

    data = KEEPIN_U235_THERMAL
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for dollars in DOLLARS:
        sol = solve(Step(data.dollars_to_rho(dollars)), data, args.gen_time, args.t_end)
        # t = 0 cannot sit on a log axis; start the curve at the first nonzero time.
        ax.loglog(sol.t[1:], sol.n[1:], label=f"{dollars:+.1f} $")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("relative power n")
    ax.set_title(f"Step response, generation time {args.gen_time:g} s")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(title="step")
    fig.tight_layout()
    fig.savefig(args.output, dpi=150)
    print(f"wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
