"""Physics regression check against committed golden metrics.

::

    python -m rksweep.golden results/ tests/golden/smoke_metrics.json            # check
    python -m rksweep.golden results/ tests/golden/smoke_metrics.json --update   # regenerate

Compares ``period_s`` and ``n_max`` of every run (matched by ``run_id``) with a relative
tolerance (default 1e-4). A solver or data change that moves the physics makes this fail,
with a per-run diff. Missing or extra runs also fail. Exit code 0 means no drift.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from rksweep.storage import INPUT_COLUMNS, open_store

KEYS = ("period_s", "n_max")
DEFAULT_RTOL = 1e-4


def _encode(value: float | None) -> float | str | None:
    """JSON-safe: non-finite floats become the strings 'nan', 'inf', '-inf'."""
    if value is None:
        return None
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return value


def _decode(value: Any) -> float:
    if value is None:
        return math.nan
    return float(value)  # float("inf"), float("nan") parse the strings


def _same(expected: float, got: float, rtol: float) -> bool:
    if math.isnan(expected) or math.isnan(got):
        return math.isnan(expected) and math.isnan(got)
    if math.isinf(expected) or math.isinf(got):
        return expected == got
    return math.isclose(expected, got, rel_tol=rtol, abs_tol=0.0)


def _rel(expected: float, got: float) -> float:
    if math.isfinite(expected) and math.isfinite(got) and expected != 0.0:
        return abs(got - expected) / abs(expected)
    return 0.0 if _same(expected, got, 0.0) else math.inf


def load_results(results_dir: str | Path) -> dict[str, dict[str, Any]]:
    """Per run_id: status, inputs and the compared metrics, from a results directory."""
    runs = open_store(results_dir).read_runs()
    out: dict[str, dict[str, Any]] = {}
    for row in runs.to_pylist():
        out[row["run_id"]] = {
            "status": row["status"],
            "inputs": {k: row[k] for k in INPUT_COLUMNS if row.get(k) is not None},
            **{k: row[k] for k in KEYS},
        }
    return out


def update_golden(results_dir: str | Path, golden_path: str | Path) -> int:
    """Write the golden file from the results (only ``ok`` runs). Returns the run count."""
    results = load_results(results_dir)
    runs = {
        rid: {"inputs": r["inputs"], **{k: _encode(r[k]) for k in KEYS}}
        for rid, r in sorted(results.items())
        if r["status"] == "ok"
    }
    doc = {"schema_version": 1, "rel_tol": DEFAULT_RTOL, "metrics": list(KEYS), "runs": runs}
    path = Path(golden_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return len(runs)


def check_golden(
    results_dir: str | Path, golden_path: str | Path, rtol: float = DEFAULT_RTOL
) -> tuple[bool, list[str]]:
    """Compare results to the golden file. Returns ``(ok, report_lines)``."""
    golden = json.loads(Path(golden_path).read_text(encoding="utf-8"))["runs"]
    results = load_results(results_dir)
    lines: list[str] = []
    ok = True

    def label(inputs: dict[str, float]) -> str:
        return ", ".join(f"{k}={v:g}" for k, v in inputs.items() if k != "t_end")

    for rid in sorted(set(golden) | set(results)):
        if rid not in results:
            ok = False
            lines.append(f"MISSING  {rid}  ({label(golden[rid]['inputs'])}): not in results")
            continue
        if rid not in golden:
            ok = False
            lines.append(f"EXTRA    {rid}  ({label(results[rid]['inputs'])}): not in golden file")
            continue
        res = results[rid]
        if res["status"] != "ok":
            ok = False
            lines.append(f"FAILED   {rid}  ({label(res['inputs'])}): run did not finish ok")
            continue
        diffs = []
        bad = False
        for key in KEYS:
            exp, got = _decode(golden[rid][key]), _decode(res[key])
            rel = _rel(exp, got)
            if not _same(exp, got, rtol):
                bad = True
                diffs.append(f"{key}: expected {exp:.9g}, got {got:.9g} (rel diff {rel:.3e})")
            else:
                diffs.append(f"{key} rel {rel:.1e}")
        if bad:
            ok = False
        lines.append(f"{'DRIFT   ' if bad else 'ok      '} {rid}  ({label(res['inputs'])})")
        lines.extend(f"           {d}" for d in diffs)
    lines.append(
        f"golden check {'passed' if ok else 'FAILED'}: {len(golden)} golden runs, "
        f"{len(results)} result runs, rel tol {rtol:g}"
    )
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rksweep.golden", description=__doc__.split("\n\n")[0])
    parser.add_argument("results", help="results directory (runs.parquet)")
    parser.add_argument("golden", help="golden metrics JSON file")
    parser.add_argument("--update", action="store_true", help="regenerate the golden file")
    parser.add_argument("--rtol", type=float, default=DEFAULT_RTOL, help="relative tolerance")
    args = parser.parse_args(argv)
    if args.update:
        count = update_golden(args.results, args.golden)
        print(f"wrote {args.golden} with {count} runs")
        return 0
    try:
        ok, lines = check_golden(args.results, args.golden, args.rtol)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
