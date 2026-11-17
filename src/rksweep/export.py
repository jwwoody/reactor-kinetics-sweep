"""Export stored results as the static JSON the compare UI reads (schema_version 1).

Layout of ``<out>/``::

    index.json          sweep info + one entry per run (inputs, metrics, status)
    runs/<run_id>.json  {"run_id", "t": [...], "n": [...]}  (about 300 points)

JSON has no NaN or Infinity, so every non-finite number is written as ``null`` and the files
are produced with ``allow_nan=False`` (strict JSON). Failed runs have all-null metrics and no
series file. See ``ui/README.md`` for the contract.
"""

from __future__ import annotations

import itertools
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.compute as pc

from rksweep.storage import INPUT_COLUMNS, METRIC_COLUMNS, open_store

SCHEMA_VERSION = 1
MAX_POINTS = 300
SIG_DIGITS = 7


def clean(value: Any) -> Any:
    """Make a value strict-JSON safe: NaN/+-Inf become ``None``, NumPy scalars become Python."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return [clean(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def dumps_strict(obj: Any) -> str:
    """Compact strict-JSON text (raises ``ValueError`` if a NaN/Infinity slipped through)."""
    return json.dumps(clean(obj), allow_nan=False, separators=(",", ":"))


def downsample(
    t: np.ndarray[Any, Any], n: np.ndarray[Any, Any], max_points: int = MAX_POINTS
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    """Reduce a series to at most ``max_points`` points.

    The output grid is already log-spaced, so taking evenly spaced *indices* keeps the log
    spacing. The first point (``t = 0``), the last point and the peak are always kept.
    """
    size = t.size
    if size <= max_points:
        return t, n
    base = np.round(np.linspace(0, size - 1, max_points - 1)).astype(int)
    idx = np.unique(np.append(base, int(np.argmax(n))))
    return t[idx], n[idx]


def _round(values: np.ndarray[Any, Any]) -> list[float | None]:
    out: list[float | None] = []
    for v in values.tolist():
        out.append(float(f"{v:.{SIG_DIGITS}g}") if math.isfinite(v) else None)
    return out


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


@dataclass
class ExportSummary:
    """What :func:`export_results` wrote."""

    n_runs: int
    n_failed: int
    n_series: int
    bytes_total: int


def export_results(results_dir: str | Path, out_dir: str | Path) -> ExportSummary:
    """Write ``index.json`` and ``runs/<run_id>.json`` for the UI from a results directory."""
    store = open_store(results_dir)
    out = Path(out_dir)
    runs_dir = out / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    runs = store.read_runs()
    meta = store.metadata()
    sweep_meta: dict[str, Any] = json.loads(meta["sweep"]) if "sweep" in meta else {}
    first = runs.slice(0, 1).to_pylist()[0] if runs.num_rows else {}
    sweep = {
        "name": sweep_meta.get("name", "unknown"),
        "sweep_id": sweep_meta.get("sweep_id", first.get("sweep_id")),
        "design": sweep_meta.get("design", "unknown"),
        "input": sweep_meta.get("input", first.get("input_type")),
        "data_set": sweep_meta.get("data_set", meta.get("data_set")),
        "git_sha": sweep_meta.get("git_sha", first.get("git_sha")),
        "created_at": sweep_meta.get("created_at"),
    }

    ok_ids = runs.filter(pc.equal(runs["status"], "ok"))["run_id"].to_pylist()
    series = store.read_series(ok_ids, ["run_id", "t", "n"]).sort_by(
        [("run_id", "ascending"), ("t", "ascending")]
    )
    ids = np.array(series["run_id"].to_pylist(), dtype=object)
    t_all = series["t"].to_numpy()
    n_all = series["n"].to_numpy().astype(np.float64)
    bounds = [0, *(np.flatnonzero(ids[1:] != ids[:-1]) + 1).tolist(), len(ids)]
    spans = {str(ids[a]): (a, b) for a, b in itertools.pairwise(bounds) if b > a}

    entries: list[dict[str, Any]] = []
    written: set[str] = set()
    total = 0
    for row in runs.to_pylist():
        rid = row["run_id"]
        ok = row["status"] == "ok"
        inputs = {k: row[k] for k in INPUT_COLUMNS if row.get(k) is not None}
        metrics = {k: (row[k] if ok else None) for k in METRIC_COLUMNS}
        entries.append(
            {
                "run_id": rid,
                "status": "ok" if ok else "failed",
                "error": None if ok else (row["error"] or "failed"),
                "inputs": inputs,
                "metrics": metrics,
            }
        )
        if ok and rid in spans:
            a, b = spans[rid]
            t_ds, n_ds = downsample(t_all[a:b], n_all[a:b])
            text = dumps_strict({"run_id": rid, "t": _round(t_ds), "n": _round(n_ds)})
            _write_text(runs_dir / f"{rid}.json", text)
            total += len(text)
            written.add(rid)

    # Remove series of runs that are no longer part of this export (e.g. older sample data).
    for stale in runs_dir.glob("*.json"):
        if stale.stem not in written:
            stale.unlink()

    index_text = dumps_strict({"schema_version": SCHEMA_VERSION, "sweep": sweep, "runs": entries})
    _write_text(out / "index.json", index_text)
    total += len(index_text)
    return ExportSummary(
        n_runs=len(entries),
        n_failed=sum(1 for e in entries if e["status"] != "ok"),
        n_series=len(written),
        bytes_total=total,
    )
