"""Tests for the golden-metrics regression check."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from rksweep import golden
from rksweep.storage import ParquetStore, runs_table
from rksweep.sweep import SweepReport, load_config, run_sweep

ROOT = Path(__file__).resolve().parents[1]
Results = tuple[Path, SweepReport]


def test_committed_golden_matches_a_fresh_smoke_sweep(tmp_path: Path) -> None:
    """The real physics regression check, on the real smoke config (9 runs, in-process)."""
    cfg = load_config(ROOT / "configs" / "smoke.toml")
    report = run_sweep(cfg, tmp_path, max_workers=1, progress=False)
    assert report.n_failed == 0
    ok, lines = golden.check_golden(tmp_path, ROOT / "tests" / "golden" / "smoke_metrics.json")
    assert ok, "\n".join(lines)


def test_update_then_check_passes(tiny_results: Results, tmp_path: Path) -> None:
    results, _ = tiny_results
    path = tmp_path / "g.json"
    assert golden.update_golden(results, path) == 4
    ok, lines = golden.check_golden(results, path)
    assert ok
    assert lines[-1].startswith("golden check passed: 4 golden runs, 4 result runs")
    doc = json.loads(path.read_text())
    assert doc["metrics"] == ["period_s", "n_max"]
    assert len(doc["runs"]) == 4


def _perturb(path: Path, factor: float, key: str = "period_s") -> str:
    doc = json.loads(path.read_text())
    rid = sorted(doc["runs"])[0]
    doc["runs"][rid][key] *= factor
    path.write_text(json.dumps(doc))
    return rid


def test_drift_beyond_tolerance_fails_with_a_clear_diff(
    tiny_results: Results, tmp_path: Path
) -> None:
    results, _ = tiny_results
    path = tmp_path / "g.json"
    golden.update_golden(results, path)
    rid = _perturb(path, 1.001)  # 1e-3 > 1e-4
    ok, lines = golden.check_golden(results, path)
    assert not ok
    text = "\n".join(lines)
    assert "DRIFT" in text
    assert rid in text
    assert "period_s: expected" in text
    assert "rel diff" in text
    assert "n_max: expected" not in text  # only the drifting metric is spelled out
    assert lines[-1].startswith("golden check FAILED")
    # a drift inside the tolerance passes; a looser --rtol accepts the big one
    path2 = tmp_path / "g2.json"
    golden.update_golden(results, path2)
    _perturb(path2, 1.00001)
    assert golden.check_golden(results, path2)[0]
    assert golden.check_golden(results, path, rtol=1e-2)[0]


def test_missing_and_extra_runs_fail(tiny_results: Results, tmp_path: Path) -> None:
    results, _ = tiny_results
    path = tmp_path / "g.json"
    golden.update_golden(results, path)
    doc = json.loads(path.read_text())
    gone = sorted(doc["runs"])[0]
    extra = dict(doc["runs"][gone])
    del doc["runs"][gone]
    doc["runs"]["feedfeedfeed"] = extra
    path.write_text(json.dumps(doc))
    ok, lines = golden.check_golden(results, path)
    assert not ok
    text = "\n".join(lines)
    assert f"EXTRA    {gone}" in text
    assert "MISSING  feedfeedfeed" in text


def test_failed_run_in_results_fails(tiny_results: Results, tmp_path: Path) -> None:
    results, _ = tiny_results
    path = tmp_path / "g.json"
    golden.update_golden(results, path)
    bad = tmp_path / "bad"
    store = ParquetStore(results)
    rows = store.read_runs().to_pylist()
    rows[0]["status"] = "failed"
    rows[0]["error"] = "boom"
    ParquetStore(bad).write_runs(runs_table(rows))
    ok, lines = golden.check_golden(bad, path)
    assert not ok
    assert any(line.startswith("FAILED") for line in lines)


def test_non_finite_values_round_trip_through_the_file(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "r")
    rows = [
        {
            "run_id": "a" * 12,
            "status": "ok",
            "rho_dollars": 0.0,
            "period_s": math.inf,
            "n_max": 1.0,
        },
        {
            "run_id": "b" * 12,
            "status": "ok",
            "rho_dollars": 0.1,
            "period_s": math.nan,
            "n_max": 2.0,
        },
    ]
    store.write_runs(runs_table(rows))
    path = tmp_path / "g.json"
    golden.update_golden(tmp_path / "r", path)
    json.loads(path.read_text())  # valid strict-ish JSON (strings, not Infinity tokens)
    assert "Infinity" not in path.read_text()
    assert golden.check_golden(tmp_path / "r", path)[0]


def test_main_exit_codes(
    tiny_results: Results, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    results, _ = tiny_results
    path = tmp_path / "g.json"
    assert golden.main([str(results), str(path), "--update"]) == 0
    assert golden.main([str(results), str(path)]) == 0
    _perturb(path, 2.0, "n_max")
    assert golden.main([str(results), str(path)]) == 1
    assert "n_max: expected" in capsys.readouterr().out
    assert golden.main([str(results), str(tmp_path / "nope.json")]) == 2
