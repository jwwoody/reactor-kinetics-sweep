"""Tests for the JSON export (the contract the UI is built against)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from conftest import make_config

from rksweep.export import SCHEMA_VERSION, clean, downsample, dumps_strict, export_results
from rksweep.kinetics import output_grid
from rksweep.storage import METRIC_COLUMNS, SeriesBlock, open_store
from rksweep.sweep import SweepReport, run_sweep

Results = tuple[Path, SweepReport]


def _forbid_constant(name: str) -> None:
    raise AssertionError(f"non-strict JSON constant {name}")


def _load_strict(path: Path) -> dict:
    """Parse JSON refusing NaN/Infinity tokens, as a browser's JSON.parse would."""
    return json.loads(path.read_text(), parse_constant=_forbid_constant)


def test_clean_turns_every_non_finite_number_into_null() -> None:
    value = {
        "a": float("nan"),
        "b": [float("inf"), -float("inf"), 1.5],
        "c": np.float64("nan"),
        "d": np.int64(3),
        "e": np.array([1.0, np.nan]),
        "f": {"g": (math.inf, 2)},
    }
    assert clean(value) == {
        "a": None,
        "b": [None, None, 1.5],
        "c": None,
        "d": 3,
        "e": [1.0, None],
        "f": {"g": [None, 2]},
    }
    text = dumps_strict(value)
    assert "NaN" not in text
    assert "Infinity" not in text
    with pytest.raises(ValueError, match="Out of range"):
        json.dumps(value, allow_nan=False)  # the unsanitised value really is not strict JSON


def test_downsample_keeps_first_last_peak_and_log_spacing() -> None:
    t = output_grid(200.0)
    n = np.exp(-((np.log(t[1:] + 1e-12) - 1.0) ** 2))  # smooth bump
    n = np.concatenate(([1.0], n))
    n[137] = 50.0  # a sharp peak that uniform subsampling would likely miss
    t_ds, n_ds = downsample(t, n)
    assert 290 <= t_ds.size <= 300
    assert t_ds[0] == 0.0
    assert t_ds[-1] == t[-1]
    assert n_ds.max() == n.max()
    assert t_ds[np.argmax(n_ds)] == t[137]
    assert np.all(np.diff(t_ds) > 0)
    ratios = t_ds[2:] / t_ds[1:-1]  # log spacing: successive ratios stay close to constant
    assert ratios.max() < 1.2
    short_t, short_n = downsample(t[:100], n[:100])
    assert short_t.size == 100
    assert short_n.size == 100


def test_export_matches_the_ui_contract(tiny_results: Results, tmp_path: Path) -> None:
    results, _ = tiny_results
    summary = export_results(results, tmp_path / "data")
    assert (summary.n_runs, summary.n_failed, summary.n_series) == (4, 0, 4)

    index = _load_strict(tmp_path / "data" / "index.json")
    assert index["schema_version"] == SCHEMA_VERSION == 1
    assert set(index) == {"schema_version", "sweep", "runs"}
    sweep = index["sweep"]
    assert set(sweep) == {
        "name",
        "sweep_id",
        "design",
        "input",
        "data_set",
        "git_sha",
        "created_at",
    }
    assert sweep["name"] == "tiny"
    assert sweep["design"] == "grid"
    assert sweep["input"] == "step"
    assert sweep["data_set"] == "KEEPIN_U235_THERMAL"
    assert sweep["sweep_id"].startswith("tiny-")
    assert sweep["created_at"].endswith("Z")
    assert len(index["runs"]) == 4

    for run in index["runs"]:
        assert set(run) == {"run_id", "status", "error", "inputs", "metrics"}
        assert run["status"] == "ok"
        assert run["error"] is None
        assert set(run["inputs"]) == {"rho_dollars", "gen_time_s", "t_end"}
        assert all(isinstance(v, float) for v in run["inputs"].values())
        assert set(run["metrics"]) == set(METRIC_COLUMNS) == {
            "n_max", "t_peak", "period_s", "energy", "t_double", "wall_time_s", "nfev", "njev",
        }  # fmt: skip
        assert isinstance(run["metrics"]["nfev"], int)
        series = _load_strict(tmp_path / "data" / "runs" / f"{run['run_id']}.json")
        assert set(series) == {"run_id", "t", "n"}
        assert series["run_id"] == run["run_id"]
        assert series["t"][0] == 0.0
        assert len(series["t"]) == len(series["n"]) <= 300
        assert all(v > 0 for v in series["n"])
        assert max(series["n"]) == pytest.approx(run["metrics"]["n_max"], rel=1e-6)
    assert sorted(p.stem for p in (tmp_path / "data" / "runs").glob("*.json")) == sorted(
        r["run_id"] for r in index["runs"]
    )


def test_failed_and_nan_runs_export_nulls_and_no_series(tmp_path: Path) -> None:
    cfg = make_config(params={"rho_dollars": {"values": [0.2, 1.2]}, "gen_time_s": {"value": 1e-5}})
    results = tmp_path / "results"
    run_sweep(cfg, results, max_workers=1, progress=False)
    # Force non-finite metrics on the ok run to check they become null, not NaN/Infinity.
    store = open_store(results)
    runs = store.read_runs()
    ok_id = next(r["run_id"] for r in runs.to_pylist() if r["status"] == "ok")
    rows = runs.to_pylist()
    for row in rows:
        if row["run_id"] == ok_id:
            row["period_s"] = math.inf
            row["t_double"] = math.nan
    from rksweep.storage import runs_table

    store.write_runs(runs_table(rows, store.metadata()))

    out = tmp_path / "data"
    summary = export_results(results, out)
    assert (summary.n_runs, summary.n_failed, summary.n_series) == (2, 1, 1)
    index = _load_strict(out / "index.json")
    text = (out / "index.json").read_text()
    assert "NaN" not in text
    assert "Infinity" not in text
    by_status = {r["status"]: r for r in index["runs"]}
    failed = by_status["failed"]
    assert isinstance(failed["error"], str)
    assert "PromptCriticalError" in failed["error"]
    assert all(v is None for v in failed["metrics"].values())
    assert set(failed["metrics"]) == set(METRIC_COLUMNS)
    assert failed["inputs"]["rho_dollars"] == 1.2
    assert not (out / "runs" / f"{failed['run_id']}.json").exists()
    ok = by_status["ok"]
    assert ok["metrics"]["period_s"] is None
    assert ok["metrics"]["t_double"] is None
    assert ok["metrics"]["n_max"] > 1.0
    assert (out / "runs" / f"{ok['run_id']}.json").exists()


def test_export_removes_stale_series_and_is_repeatable(
    tiny_results: Results, tmp_path: Path
) -> None:
    results, _ = tiny_results
    out = tmp_path / "data"
    (out / "runs").mkdir(parents=True)
    (out / "runs" / "oldsample001.json").write_text("{}")
    export_results(results, out)
    assert not (out / "runs" / "oldsample001.json").exists()
    first = (out / "index.json").read_bytes()
    export_results(results, out)
    assert (out / "index.json").read_bytes() == first
    assert not [p for p in out.rglob("*") if ".tmp-" in p.name]


def test_export_from_hdf5_results(tmp_path: Path) -> None:
    pytest.importorskip("h5py")
    cfg = make_config(storage={"backend": "hdf5"})
    run_sweep(cfg, tmp_path / "r", max_workers=1, progress=False)
    summary = export_results(tmp_path / "r", tmp_path / "o")
    assert summary.n_series == 4


def test_series_block_roundtrip_is_used_by_export(tmp_path: Path) -> None:
    # A store written by hand (no sweep) still exports: sweep info falls back to row values.
    from rksweep.storage import ParquetStore, runs_table, series_table

    store = ParquetStore(tmp_path / "r")
    t = output_grid(10.0)
    store.write_runs(
        runs_table([{"run_id": "aaaaaaaaaaaa", "status": "ok", "rho_dollars": 0.1, "n_max": 2.0}])
    )
    store.write_series(series_table([SeriesBlock("aaaaaaaaaaaa", t, np.ones(t.size))]))
    export_results(tmp_path / "r", tmp_path / "o")
    index = _load_strict(tmp_path / "o" / "index.json")
    assert index["sweep"]["name"] == "unknown"
    assert index["runs"][0]["inputs"] == {"rho_dollars": 0.1}
