"""Benchmark storage layouts on a finished sweep and print a Markdown table.

Measures, for every option in docs/03-storage.md ("Layout choices"): bytes on disk, write
time, read-all time, read-one-run time and a filtered query ("runs with rho_dollars > 0.5,
their n at t = 100 s"). Times are the best of ``--repeats`` runs with a warm OS cache.
Everything is written into a temporary directory, never into the repository.

The series must contain the precursor columns (``configs/full.toml`` stores them).

Usage::

    python benchmarks/storage.py results-full/ [--repeats 3]
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import scipy

from rksweep.storage import (
    Hdf5Store,
    ParquetStore,
    ResultStore,
    StorageOptions,
    open_store,
)

QUERY_RHO = 0.5
QUERY_T = 100.0


def best_of(repeats: int, fn: Callable[[], Any]) -> float:
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best


def nearest_n(table: pa.Table, t_target: float) -> dict[str, float]:
    """Per run, ``n`` at the stored output point closest to ``t_target``."""
    ids = table["run_id"].to_numpy(zero_copy_only=False)
    t = table["t"].to_numpy()
    n = table["n"].to_numpy()
    out: dict[str, float] = {}
    best: dict[str, float] = {}
    for rid, ti, ni in zip(ids, t, n, strict=True):
        d = abs(ti - t_target)
        if d < best.get(rid, float("inf")):
            best[rid] = d
            out[rid] = float(ni)
    return out


@dataclass
class Case:
    label: str
    options: StorageOptions | None  # None: CSV baseline
    float32: bool = False


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


class CsvBackend:
    """CSV baseline: two files, no index, so every question reads the whole file."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, runs: pa.Table, series: pa.Table) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        pacsv.write_csv(runs.replace_schema_metadata(None), self.root / "runs.csv")
        pacsv.write_csv(series.replace_schema_metadata(None), self.root / "timeseries.csv")

    def read_all(self) -> int:
        a = pacsv.read_csv(self.root / "runs.csv")
        b = pacsv.read_csv(self.root / "timeseries.csv")
        return a.num_rows + b.num_rows

    def read_one(self, rid: str) -> pa.Table:
        table = pacsv.read_csv(self.root / "timeseries.csv")
        return table.filter(pc.equal(table["run_id"], rid))

    def query(self) -> dict[str, float]:
        runs = pacsv.read_csv(self.root / "runs.csv")
        ids = runs.filter(pc.field("rho_dollars") > QUERY_RHO)["run_id"].to_pylist()
        table = pacsv.read_csv(self.root / "timeseries.csv")
        keep = pc.and_(
            pc.is_in(table["run_id"], pa.array(ids)),
            pc.less_equal(pc.abs(pc.subtract(table["t"], QUERY_T)), 10.0),
        )
        return nearest_n(table.filter(keep), QUERY_T)

    def size(self) -> int:
        return dir_bytes(self.root)


class StoreBackend:
    def __init__(self, root: Path, store: ResultStore) -> None:
        self.root = root
        self.store = store

    def write(self, runs: pa.Table, series: pa.Table) -> None:
        self.store.write_runs(runs, merge=False)
        self.store.write_series(series, merge=False)

    def read_all(self) -> int:
        return self.store.read_runs().num_rows + self.store.read_series().num_rows

    def read_one(self, rid: str) -> pa.Table:
        return self.store.read_series([rid])

    def query(self) -> dict[str, float]:
        ids = self.store.read_runs(pc.field("rho_dollars") > QUERY_RHO, ["run_id"])[
            "run_id"
        ].to_pylist()
        sub = self.store.read_series(
            ids, ["run_id", "t", "n"], t_range=(QUERY_T - 10, QUERY_T + 10)
        )
        return nearest_n(sub, QUERY_T)

    def size(self) -> int:
        return dir_bytes(self.root)


def float32_error(series: pa.Table) -> float:
    """Largest relative round-trip error of casting n and C1..C6 to float32."""
    worst = 0.0
    for name in series.column_names:
        if name == "n" or (name.startswith("C") and name[1:].isdigit()):
            x = series[name].to_numpy()
            y = x.astype(np.float32).astype(np.float64)
            nz = x != 0.0
            worst = max(worst, float(np.max(np.abs(y[nz] - x[nz]) / np.abs(x[nz]))))
    return worst


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", nargs="?", default="results-full/")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    src = open_store(args.results)
    runs = src.read_runs()
    series = src.read_series()
    if "C1" not in series.column_names:
        sys.exit("the results must contain the precursor columns (use configs/full.toml)")
    run_ids = sorted(src.run_ids("ok"))
    one = run_ids[len(run_ids) // 2]
    err32 = float32_error(series)

    try:
        import h5py  # noqa: F401

        have_h5 = True
    except ImportError:
        have_h5 = False

    pq_opts = StorageOptions
    cases: list[tuple[str, Any, bool]] = [
        ("CSV, float64, all columns", None, False),
        ("Parquet none, f64, C1-C6", pq_opts(compression="none", precursors=True), False),
        ("Parquet snappy, f64, C1-C6", pq_opts(compression="snappy", precursors=True), False),
        ("Parquet zstd, f64, C1-C6", pq_opts(compression="zstd", precursors=True), False),
        ("Parquet zstd, f32, C1-C6", pq_opts(float32=True, precursors=True), True),
        ("Parquet zstd, f64, no C", pq_opts(precursors=False), False),
        ("Parquet zstd, f32, no C", pq_opts(float32=True, precursors=False), True),
        ("Parquet zstd, f64, C1-C6, hive by run", pq_opts(precursors=True, layout="hive"), False),
        (
            "Parquet zstd, f64, C1-C6, 8k row groups",
            pq_opts(precursors=True, row_group_size=8192),
            False,
        ),
    ]
    if have_h5:
        cases.append(
            ("HDF5 gzip, f64, C1-C6", StorageOptions(backend="hdf5", precursors=True), False)
        )
        cases.append(("HDF5 gzip, f32, no C", StorageOptions(backend="hdf5", float32=True), True))

    rows: list[dict[str, Any]] = []
    reference: dict[str, float] | None = None
    with tempfile.TemporaryDirectory(prefix="rksweep-bench-") as tmp_name:
        tmp = Path(tmp_name)
        for i, (label, options, f32) in enumerate(cases):
            root = tmp / f"case{i}"

            def make(root: Path = root, options: StorageOptions | None = options) -> Any:
                if options is None:
                    return CsvBackend(root)
                store: ResultStore
                store = (
                    Hdf5Store(root, options)
                    if options.backend == "hdf5"
                    else ParquetStore(root, options)
                )
                return StoreBackend(root, store)

            backend = make()

            def write(root: Path = root, backend: Any = backend) -> None:
                shutil.rmtree(root, ignore_errors=True)
                backend.write(runs, series)

            t_write = best_of(args.repeats, write)
            # sanity: the query answer must agree with the float64 source
            answer = backend.query()
            if reference is None:
                reference = answer
            assert answer.keys() == reference.keys(), f"{label}: query returned other runs"
            tol = 1e-5 if f32 else 1e-12
            assert all(abs(answer[k] - reference[k]) <= tol * abs(reference[k]) for k in answer), (
                label
            )
            rows.append(
                {
                    "label": label,
                    "bytes": backend.size(),
                    "write": t_write,
                    "read_all": best_of(args.repeats, backend.read_all),
                    "read_one": best_of(args.repeats, lambda b=backend: b.read_one(one)),
                    "query": best_of(args.repeats, backend.query),
                    "n_query_runs": len(answer),
                    "err": err32 if f32 else None,
                }
            )

    csv_bytes = rows[0]["bytes"]
    print("<!-- generated by benchmarks/storage.py -->")
    print(
        f"Sweep: {len(run_ids)} runs, {series.num_rows} series rows "
        f"({series.num_rows // max(len(run_ids), 1)} points per run), series columns "
        f"{', '.join(series.column_names)}."
    )
    print(
        f"Python {platform.python_version()}, NumPy {np.__version__}, SciPy {scipy.__version__}, "
        f"PyArrow {pa.__version__}, {platform.system()} {platform.machine()}, "
        f"{os.cpu_count()} cores, {dt.date.today().isoformat()}. "
        f"Best of {args.repeats}, warm file cache."
    )
    print(
        f"Query: runs with rho_dollars > {QUERY_RHO}, their n at the output point nearest "
        f"t = {QUERY_T:g} s ({rows[0]['n_query_runs']} runs match)."
    )
    print()
    print(
        "| Option | Size (MB) | vs CSV | Write (s) | Read all (s) | Read one run (ms) "
        "| Query (ms) | Max rel. error |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|")

    def ratio(size: int) -> str:
        return "baseline" if size == csv_bytes else f"{csv_bytes / size:.1f}x smaller"

    for r in rows:
        err = "0" if r["err"] is None else f"{r['err']:.2e}"
        print(
            f"| {r['label']} | {r['bytes'] / 1e6:.2f} | {ratio(r['bytes'])} "
            f"| {r['write']:.3f} | {r['read_all']:.3f} | {r['read_one'] * 1e3:.1f} "
            f"| {r['query'] * 1e3:.1f} | {err} |"
        )
    print()
    print(
        "Max rel. error is the largest |float32(x) - x| / |x| over n and C1-C6 of this "
        "sweep (the solver's rtol is 1e-8)."
    )


if __name__ == "__main__":
    main()
