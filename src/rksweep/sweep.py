"""Parameter sweeps: config, designs (grid / Latin hypercube), identity, parallel runner.

A sweep config (TOML) is validated into a :class:`SweepConfig` (unknown keys are an error).
:func:`design_points` turns it into parameter dicts, :func:`run_one` runs one of them and
:func:`run_sweep` runs them all in a process pool and stores the results (see
:mod:`rksweep.storage`). See ``docs/02-sweeps-and-doe.md`` for the format.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import math
import multiprocessing
import os
import platform
import re
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import scipy
from scipy.stats import qmc, spearmanr

import rksweep
from rksweep.constants import KEEPIN_U235_THERMAL, get_data_set
from rksweep.kinetics import METHODS, solve
from rksweep.metrics import compute_metrics
from rksweep.reactivity import INPUT_TYPES, build_input
from rksweep.storage import (
    INPUT_COLUMNS,
    METRIC_COLUMNS,
    SeriesBlock,
    StorageOptions,
    file_metadata,
    open_store,
    runs_table,
    series_table,
)

DEFAULT_RTOL = 1e-8
DEFAULT_ATOL = 1e-12

#: Parameters each input type uses: (required, optional).
INPUT_PARAMS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "step": (frozenset({"rho_dollars", "gen_time_s"}), frozenset()),
    "ramp": (frozenset({"ramp_rate_dps", "rho_dollars", "gen_time_s"}), frozenset()),
    "sine": (frozenset({"rho_dollars", "gen_time_s"}), frozenset({"freq_hz"})),
    "step_feedback": (
        frozenset({"rho_dollars", "gen_time_s", "alpha_fb"}),
        frozenset({"fb_power_coeff", "fb_tau_s"}),
    ),
}
SWEEP_KEYS = frozenset(
    {
        "name",
        "design",
        "seed",
        "t_end",
        "input",
        "data_set",
        "n_samples",
        "method",
        "rtol",
        "atol",
        "expected_failures",
    }
)
STORAGE_KEYS = frozenset({"backend", "compression", "float32", "precursors", "layout"})
TOP_LEVEL_KEYS = frozenset({"sweep", "params", "storage"})
PARAM_VALUE_KEYS = frozenset({"values", "value"})
PARAM_RANGE_KEYS = frozenset({"low", "high", "scale"})


class ConfigError(ValueError):
    """The sweep config is invalid."""


# --------------------------------------------------------------------------- config


@dataclass(frozen=True)
class ParamSpec:
    """One swept (or fixed) parameter: explicit ``values`` or a ``low``..``high`` range."""

    values: tuple[float, ...] | None = None
    low: float | None = None
    high: float | None = None
    scale: str = "linear"

    @property
    def is_range(self) -> bool:
        return self.values is None


@dataclass(frozen=True)
class SweepConfig:
    """Validated sweep configuration."""

    name: str
    design: str
    input: str
    t_end: float
    params: dict[str, ParamSpec]
    data_set: str = KEEPIN_U235_THERMAL.name
    seed: int = 0
    n_samples: int | None = None
    method: str = "Radau"
    rtol: float = DEFAULT_RTOL
    atol: float = DEFAULT_ATOL
    expected_failures: int = 0
    storage: StorageOptions = field(default_factory=StorageOptions)


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{where}: expected a number, got {value!r}")
    if not math.isfinite(value):
        raise ConfigError(f"{where}: must be finite, got {value!r}")
    return float(value)


def _unknown(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    extra = sorted(set(table) - allowed)
    if extra:
        raise ConfigError(f"{where}: unknown key(s) {extra}; allowed: {sorted(allowed)}")


def _parse_param(name: str, table: Mapping[str, Any], design: str) -> ParamSpec:
    where = f"[params.{name}]"
    _unknown(table, PARAM_VALUE_KEYS | PARAM_RANGE_KEYS, where)
    has_values = "values" in table or "value" in table
    has_range = bool({"low", "high", "scale"} & set(table))
    if has_values and has_range:
        raise ConfigError(f"{where}: give either values/value or low/high/scale, not both")
    if "values" in table and "value" in table:
        raise ConfigError(f"{where}: give 'values' or 'value', not both")
    if has_values:
        raw = table["values"] if "values" in table else [table["value"]]
        if not isinstance(raw, list) or not raw:
            raise ConfigError(f"{where}: 'values' must be a non-empty list")
        values = tuple(_number(v, f"{where}.values") for v in raw)
        if "values" in table and len(values) > 1 and design == "lhs":
            raise ConfigError(f"{where}: an lhs design needs low/high (or a single 'value')")
        return ParamSpec(values=values)
    if "low" not in table or "high" not in table:
        raise ConfigError(f"{where}: needs 'values', 'value' or both 'low' and 'high'")
    low = _number(table["low"], f"{where}.low")
    high = _number(table["high"], f"{where}.high")
    scale = table.get("scale", "linear")
    if scale not in ("linear", "log"):
        raise ConfigError(f"{where}.scale: must be 'linear' or 'log', got {scale!r}")
    if not low < high:
        raise ConfigError(f"{where}: low must be below high")
    if scale == "log" and low <= 0.0:
        raise ConfigError(f"{where}: log scale needs low > 0")
    if design == "grid":
        raise ConfigError(f"{where}: a grid design needs explicit 'values'")
    return ParamSpec(low=low, high=high, scale=scale)


def parse_config(raw: Mapping[str, Any]) -> SweepConfig:
    """Validate a parsed TOML mapping into a :class:`SweepConfig`."""
    _unknown(raw, TOP_LEVEL_KEYS, "config")
    sweep = raw.get("sweep")
    if not isinstance(sweep, dict):
        raise ConfigError("missing [sweep] table")
    _unknown(sweep, SWEEP_KEYS, "[sweep]")
    for key in ("name", "design", "input", "t_end"):
        if key not in sweep:
            raise ConfigError(f"[sweep]: missing required key {key!r}")

    name = sweep["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ConfigError("[sweep].name: use letters, digits, '_', '-' and '.' only")
    design = sweep["design"]
    if design not in ("grid", "lhs"):
        raise ConfigError(f"[sweep].design: must be 'grid' or 'lhs', got {design!r}")
    input_name = sweep["input"]
    if input_name not in INPUT_TYPES:
        raise ConfigError(f"[sweep].input: {input_name!r} not one of {sorted(INPUT_TYPES)}")
    t_end = _number(sweep["t_end"], "[sweep].t_end")
    if t_end <= 1e-6:
        raise ConfigError("[sweep].t_end: must exceed 1e-6 s")
    data_set = sweep.get("data_set", KEEPIN_U235_THERMAL.name)
    try:
        get_data_set(data_set)
    except KeyError as exc:
        raise ConfigError(f"[sweep].data_set: {exc.args[0]}") from None
    seed = sweep.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ConfigError("[sweep].seed: must be an integer")
    n_samples = sweep.get("n_samples")
    if design == "lhs":
        if isinstance(n_samples, bool) or not isinstance(n_samples, int) or n_samples < 1:
            raise ConfigError("[sweep].n_samples: an lhs design needs a positive integer")
    elif n_samples is not None:
        raise ConfigError("[sweep].n_samples: only valid for design = 'lhs'")
    method = sweep.get("method", "Radau")
    if method not in METHODS:
        raise ConfigError(f"[sweep].method: {method!r} not one of {METHODS}")
    rtol = _number(sweep.get("rtol", DEFAULT_RTOL), "[sweep].rtol")
    atol = _number(sweep.get("atol", DEFAULT_ATOL), "[sweep].atol")
    expected = sweep.get("expected_failures", 0)
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise ConfigError("[sweep].expected_failures: must be a non-negative integer")

    raw_params = raw.get("params")
    if not isinstance(raw_params, dict) or not raw_params:
        raise ConfigError("missing [params.*] tables")
    required, optional = INPUT_PARAMS[input_name]
    unused = sorted(set(raw_params) - required - optional)
    if unused:
        raise ConfigError(
            f"[params]: {unused} not used by input {input_name!r}; "
            f"known: {sorted(required | optional)}"
        )
    missing = sorted(required - set(raw_params))
    if missing:
        raise ConfigError(f"[params]: input {input_name!r} needs {missing}")
    params: dict[str, ParamSpec] = {}
    for pname, table in raw_params.items():
        if not isinstance(table, dict):
            raise ConfigError(f"[params.{pname}]: must be a table")
        params[pname] = _parse_param(pname, table, design)
    if design == "lhs" and not any(p.is_range for p in params.values()):
        raise ConfigError("an lhs design needs at least one low/high parameter")

    storage = raw.get("storage", {})
    if not isinstance(storage, dict):
        raise ConfigError("[storage] must be a table")
    _unknown(storage, STORAGE_KEYS, "[storage]")
    try:
        options = StorageOptions(**storage)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"[storage]: {exc}") from None

    return SweepConfig(
        name=name,
        design=design,
        input=input_name,
        t_end=t_end,
        params=params,
        data_set=data_set,
        seed=seed,
        n_samples=n_samples,
        method=method,
        rtol=rtol,
        atol=atol,
        expected_failures=expected,
        storage=options,
    )


def load_config(path: str | Path) -> SweepConfig:
    """Read and validate a TOML config file."""
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from None
    return parse_config(raw)


# --------------------------------------------------------------------------- designs


def grid_design(params: Mapping[str, ParamSpec]) -> list[dict[str, float]]:
    """Full factorial: one point per combination of the value lists."""
    names = list(params)
    lists = []
    for name in names:
        values = params[name].values
        if values is None:
            raise ConfigError(f"param {name!r}: a grid design needs explicit values")
        lists.append(values)
    return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*lists)]


def lhs_design(
    params: Mapping[str, ParamSpec], n_samples: int, seed: int
) -> list[dict[str, float]]:
    """Latin hypercube over the range parameters; single-value parameters stay fixed."""
    sampled = [name for name, spec in params.items() if spec.is_range]
    if not sampled:
        raise ConfigError("an lhs design needs at least one low/high parameter")
    u = qmc.LatinHypercube(d=len(sampled), seed=seed).random(n_samples)
    columns: dict[str, Any] = {}
    for k, name in enumerate(sampled):
        spec = params[name]
        assert spec.low is not None
        assert spec.high is not None
        if spec.scale == "log":
            lo, hi = math.log10(spec.low), math.log10(spec.high)
            columns[name] = 10.0 ** (lo + u[:, k] * (hi - lo))
        else:
            columns[name] = qmc.scale(u[:, [k]], [spec.low], [spec.high])[:, 0]
    points = []
    for i in range(n_samples):
        point: dict[str, float] = {}
        for name, spec in params.items():
            if spec.is_range:
                point[name] = float(columns[name][i])
            else:
                assert spec.values is not None
                point[name] = spec.values[0]
        points.append(point)
    return points


def design_points(cfg: SweepConfig) -> list[dict[str, float]]:
    """All parameter points of the sweep's design."""
    if cfg.design == "grid":
        return grid_design(cfg.params)
    assert cfg.n_samples is not None
    return lhs_design(cfg.params, cfg.n_samples, cfg.seed)


def run_params(cfg: SweepConfig, point: Mapping[str, float]) -> dict[str, Any]:
    """The flat parameter dict that :func:`run_one` takes for one design point."""
    return {
        "input": cfg.input,
        "data_set": cfg.data_set,
        "t_end": cfg.t_end,
        **point,
        "method": cfg.method,
        "rtol": cfg.rtol,
        "atol": cfg.atol,
    }


# --------------------------------------------------------------------------- identity


def canonical_inputs(params: Mapping[str, Any]) -> dict[str, float]:
    """The physical inputs of a run (known input columns only), all as floats."""
    return {k: float(params[k]) for k in INPUT_COLUMNS if k in params}


def compute_run_id(params: Mapping[str, Any]) -> str:
    """First 12 hex characters of SHA-256 over canonical JSON of inputs + data-set name."""
    payload = {
        "data_set": params["data_set"],
        "input": params["input"],
        "inputs": canonical_inputs(params),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def make_sweep_id(name: str, now: dt.datetime | None = None) -> str:
    """``<name>-<UTC timestamp>``, e.g. ``smoke-20261116T120000Z``."""
    now = now or dt.datetime.now(dt.UTC)
    return f"{name}-{now:%Y%m%dT%H%M%SZ}"


# --------------------------------------------------------------------------- one run


@dataclass
class RunResult:
    """Outcome of one run (picklable; arrays are plain NumPy)."""

    run_id: str
    status: str
    error: str | None
    inputs: dict[str, float]
    metrics: dict[str, float | int | None]
    t: Any = None
    n: Any = None
    C: Any = None
    elapsed_s: float = 0.0


_EMPTY_METRICS: dict[str, float | int | None] = dict.fromkeys(METRIC_COLUMNS)


def _failed(params: Mapping[str, Any], exc: BaseException, elapsed: float) -> RunResult:
    message = f"{type(exc).__name__}: {exc}"[:500]
    return RunResult(
        compute_run_id(params),
        "failed",
        message,
        canonical_inputs(params),
        dict(_EMPTY_METRICS),
        elapsed_s=elapsed,
    )


def run_one(params: dict[str, Any]) -> RunResult:
    """Run one parameter set. Never raises: failures come back as ``status="failed"``."""
    start = time.perf_counter()
    try:
        data = get_data_set(params["data_set"])
        reactivity = build_input(params["input"], data, params)
        sol = solve(
            reactivity,
            data,
            params["gen_time_s"],
            params["t_end"],
            method=params.get("method", "Radau"),
            rtol=params.get("rtol", DEFAULT_RTOL),
            atol=params.get("atol", DEFAULT_ATOL),
        )
        return RunResult(
            compute_run_id(params),
            "ok",
            None,
            canonical_inputs(params),
            compute_metrics(sol),
            t=sol.t,
            n=sol.n,
            C=sol.C,
            elapsed_s=time.perf_counter() - start,
        )
    except Exception as exc:
        return _failed(params, exc, time.perf_counter() - start)


# --------------------------------------------------------------------------- provenance


def git_sha() -> str:
    """Short git SHA: CI variables first, then ``git rev-parse --short HEAD``, else unknown."""
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"][:7]
    if os.environ.get("CI_COMMIT_SHORT_SHA"):
        return os.environ["CI_COMMIT_SHORT_SHA"]
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return proc.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def provenance(cfg: SweepConfig) -> dict[str, Any]:
    """Fields stored on every ``runs`` row."""
    return {
        "git_sha": git_sha(),
        "package_version": rksweep.__version__,
        "solver_method": cfg.method,
        "rtol": cfg.rtol,
        "atol": cfg.atol,
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
    }


def result_row(
    res: RunResult,
    cfg: SweepConfig,
    sweep_id: str,
    prov: Mapping[str, Any],
    created_at: dt.datetime,
) -> dict[str, Any]:
    """One ``runs`` table row."""
    return {
        "run_id": res.run_id,
        "sweep_id": sweep_id,
        "input_type": cfg.input,
        "data_set": cfg.data_set,
        "status": res.status,
        "error": res.error,
        **res.inputs,
        **res.metrics,
        **prov,
        "created_at": created_at,
    }


# --------------------------------------------------------------------------- the runner


@dataclass
class SweepReport:
    """What :func:`run_sweep` did."""

    sweep_id: str
    out_dir: Path
    n_total: int
    n_skipped: int
    n_run: int
    n_failed: int
    failed: list[tuple[str, str]]
    wall_s: float


def _execute(
    tasks: Sequence[dict[str, Any]],
    workers: int,
    on_result: Callable[[RunResult], None],
) -> None:
    if workers <= 1 or len(tasks) <= 1:
        for params in tasks:
            on_result(run_one(params))
        return
    # "spawn" everywhere: identical behaviour on macOS/Linux/Windows and no fork-after-threads
    # hazards (NumPy, PyArrow and BLAS all start threads).
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futures = {pool.submit(run_one, p): p for p in tasks}
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except Exception as exc:  # worker crashed (e.g. killed), not a solver error
                res = _failed(futures[fut], exc, 0.0)
            on_result(res)


def run_sweep(
    cfg: SweepConfig,
    out_dir: str | Path,
    *,
    max_workers: int | None = None,
    force: bool = False,
    progress: bool = True,
    stream: TextIO | None = None,
) -> SweepReport:
    """Run the sweep and store ``runs`` and ``timeseries`` in ``out_dir``.

    Run IDs already stored with status ``ok`` are skipped (unless ``force``); failed runs
    are retried. ``max_workers=1`` runs in-process (no pool). Progress goes to stderr.
    """
    stream = stream if stream is not None else sys.stderr
    started = time.perf_counter()
    store = open_store(out_dir, cfg.storage)
    created_at = dt.datetime.now(dt.UTC).replace(microsecond=0)
    sweep_id = make_sweep_id(cfg.name, created_at)

    tasks: dict[str, dict[str, Any]] = {}
    for point in design_points(cfg):
        params = run_params(cfg, point)
        tasks.setdefault(compute_run_id(params), params)
    stored = set() if force else store.run_ids("ok")
    pending = [p for rid, p in tasks.items() if rid not in stored]
    n_skipped = len(tasks) - len(pending)

    workers = max(1, min(max_workers or os.cpu_count() or 1, len(pending) or 1))
    if progress:
        print(
            f"sweep {sweep_id}: {len(tasks)} runs, {n_skipped} already stored, "
            f"{len(pending)} to run on {workers} worker(s)",
            file=stream,
            flush=True,
        )

    prov = provenance(cfg)
    results: list[RunResult] = []

    def on_result(res: RunResult) -> None:
        results.append(res)
        if progress:
            tail = "" if res.status == "ok" else f"  FAILED: {res.error}"
            print(
                f"[{len(results)}/{len(pending)}] run {res.run_id}  {res.elapsed_s:.2f} s{tail}",
                file=stream,
                flush=True,
            )

    _execute(pending, workers, on_result)

    if results:
        meta = file_metadata(
            cfg.data_set,
            {
                "name": cfg.name,
                "sweep_id": sweep_id,
                "design": cfg.design,
                "input": cfg.input,
                "data_set": cfg.data_set,
                "git_sha": prov["git_sha"],
                "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        rows = [result_row(r, cfg, sweep_id, prov, created_at) for r in results]
        store.write_runs(runs_table(rows, meta))
        blocks = [SeriesBlock(r.run_id, r.t, r.n, r.C) for r in results if r.status == "ok"]
        if blocks:
            store.write_series(series_table(blocks, meta))

    failed = [(r.run_id, r.error or "") for r in results if r.status != "ok"]
    return SweepReport(
        sweep_id=sweep_id,
        out_dir=Path(out_dir),
        n_total=len(tasks),
        n_skipped=n_skipped,
        n_run=len(results),
        n_failed=len(failed),
        failed=failed,
        wall_s=time.perf_counter() - started,
    )


# --------------------------------------------------------------------------- sensitivity


def spearman_sensitivity(
    runs: pa.Table,
    params: Sequence[str] | None = None,
    metrics: Sequence[str] = ("period_s", "n_max"),
) -> dict[str, dict[str, float]]:
    """Spearman rank correlation of each input with each metric (use on an LHS sweep).

    Returns ``{metric: {param: rho}}`` over the ``ok`` runs with finite values. A parameter
    that does not vary, or fewer than three usable runs, gives ``nan``.
    """
    ok = runs.filter(pc.equal(runs["status"], "ok"))
    names = (
        list(params)
        if params is not None
        else [c for c in INPUT_COLUMNS if c != "t_end" and ok[c].null_count < ok.num_rows]
    )
    out: dict[str, dict[str, float]] = {m: {} for m in metrics}
    for pname in names:
        x_all = np.asarray(ok[pname].to_numpy(zero_copy_only=False), dtype=float)
        for metric in metrics:
            y_all = np.asarray(ok[metric].to_numpy(zero_copy_only=False), dtype=float)
            keep = np.isfinite(x_all) & np.isfinite(y_all)
            x, y = x_all[keep], y_all[keep]
            if x.size < 3 or np.ptp(x) == 0.0 or np.ptp(y) == 0.0:
                out[metric][pname] = math.nan
            else:
                out[metric][pname] = float(spearmanr(x, y).statistic)
    return out
