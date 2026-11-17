"""Command line interface: ``rksweep run | sweep | export``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

from rksweep.constants import KEEPIN_U235_THERMAL
from rksweep.export import export_results
from rksweep.reactivity import INPUT_TYPES
from rksweep.storage import METRIC_COLUMNS
from rksweep.sweep import ConfigError, compute_run_id, load_config, run_one, run_sweep


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rksweep",
        description="Educational point-kinetics reactor model: runs, sweeps and export.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="one run; prints its metrics")
    run.add_argument("--input", choices=sorted(INPUT_TYPES), default="step")
    run.add_argument("--rho-dollars", type=float, required=True, help="reactivity in dollars")
    run.add_argument("--gen-time", type=float, required=True, help="generation time Lambda (s)")
    run.add_argument("--t-end", type=float, default=200.0, help="end time (s)")
    run.add_argument("--ramp-rate", type=float, help="ramp input: dollars per second")
    run.add_argument("--freq-hz", type=float, help="sine input: frequency (Hz)")
    run.add_argument("--alpha-fb", type=float, help="step_feedback input: alpha (1/K)")
    run.add_argument("--data-set", default=KEEPIN_U235_THERMAL.name)
    run.add_argument("--method", default="Radau")

    sweep = sub.add_parser("sweep", help="run a sweep config and store the results")
    sweep.add_argument("config", help="TOML config, e.g. configs/smoke.toml")
    sweep.add_argument("--out", required=True, help="results directory")
    sweep.add_argument("--workers", type=int, help="worker processes (default: all cores)")
    sweep.add_argument("--force", action="store_true", help="re-run runs that are already stored")
    sweep.add_argument(
        "--allow-failures",
        action="store_true",
        help="exit 0 even if runs failed (otherwise only [sweep].expected_failures is tolerated)",
    )

    export = sub.add_parser("export", help="write JSON for the compare UI")
    export.add_argument("results", help="results directory")
    export.add_argument("--out", required=True, help="output directory, e.g. ui/public/data/")
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {
        "input": args.input,
        "data_set": args.data_set,
        "t_end": args.t_end,
        "rho_dollars": args.rho_dollars,
        "gen_time_s": args.gen_time,
        "method": args.method,
    }
    for key, value in (
        ("ramp_rate_dps", args.ramp_rate),
        ("freq_hz", args.freq_hz),
        ("alpha_fb", args.alpha_fb),
    ):
        if value is not None:
            params[key] = value
    result = run_one(params)
    print(f"run_id  {compute_run_id(params)}")
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    for key in METRIC_COLUMNS:
        print(f"{key:<12}{result.metrics[key]:.6g}")
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    report = run_sweep(cfg, args.out, max_workers=args.workers, force=args.force)
    print(
        f"sweep {report.sweep_id}: {report.n_total} runs "
        f"({report.n_run} run, {report.n_skipped} skipped, {report.n_failed} failed) "
        f"in {report.wall_s:.1f} s -> {report.out_dir}"
    )
    if args.allow_failures or report.n_failed == cfg.expected_failures:
        return 0
    for rid, error in report.failed:
        print(f"failed run {rid}: {error}", file=sys.stderr)
    print(
        f"error: {report.n_failed} run(s) failed, expected {cfg.expected_failures} "
        "(see [sweep].expected_failures or --allow-failures)",
        file=sys.stderr,
    )
    return 1


def _cmd_export(args: argparse.Namespace) -> int:
    summary = export_results(args.results, args.out)
    print(
        f"exported {summary.n_runs} runs ({summary.n_failed} failed, {summary.n_series} series, "
        f"{summary.bytes_total / 1024:.0f} kB) -> {args.out}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return _cmd_run(args)
        if args.command == "sweep":
            return _cmd_sweep(args)
        return _cmd_export(args)
    except (ConfigError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
