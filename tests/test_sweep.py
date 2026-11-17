"""Tests for config parsing, designs, run identity and the sweep runner."""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import make_config

from rksweep.storage import open_store
from rksweep.sweep import (
    ConfigError,
    ParamSpec,
    compute_run_id,
    design_points,
    git_sha,
    grid_design,
    lhs_design,
    load_config,
    parse_config,
    run_one,
    run_params,
    run_sweep,
    spearman_sensitivity,
)

ROOT = Path(__file__).resolve().parents[1]


# ---- designs


def test_grid_is_the_product_of_value_counts() -> None:
    params = {
        "a": ParamSpec(values=(1.0, 2.0, 3.0)),
        "b": ParamSpec(values=(10.0, 20.0)),
        "c": ParamSpec(values=(0.5, 0.6, 0.7, 0.8)),
    }
    points = grid_design(params)
    assert len(points) == 3 * 2 * 4
    assert len({tuple(p.values()) for p in points}) == len(points)


def test_lhs_same_seed_same_points_and_inside_bounds() -> None:
    params = {
        "rho_dollars": ParamSpec(low=0.05, high=0.9),
        "gen_time_s": ParamSpec(low=1e-7, high=1e-4, scale="log"),
        "alpha_fb": ParamSpec(values=(-1e-5,)),
    }
    a = lhs_design(params, 50, seed=7)
    b = lhs_design(params, 50, seed=7)
    c = lhs_design(params, 50, seed=8)
    assert a == b
    assert a != c
    for p in a:
        assert 0.05 <= p["rho_dollars"] <= 0.9
        assert 1e-7 <= p["gen_time_s"] <= 1e-4
        assert p["alpha_fb"] == -1e-5


def test_lhs_covers_each_dimension_evenly() -> None:
    n = 40
    points = lhs_design({"x": ParamSpec(low=0.0, high=1.0)}, n, seed=1)
    strata = sorted(int(p["x"] * n) for p in points)
    assert strata == list(range(n))  # exactly one point per stratum


def test_log_scale_median_is_near_the_geometric_mean() -> None:
    low, high = 1e-7, 1e-4
    points = lhs_design({"g": ParamSpec(low=low, high=high, scale="log")}, 2000, seed=3)
    median = float(np.median([p["g"] for p in points]))
    assert median == pytest.approx(math.sqrt(low * high), rel=0.02)


# ---- run identity


def test_run_id_is_stable_and_changes_with_any_input() -> None:
    cfg = make_config()
    params = run_params(cfg, {"rho_dollars": 0.3, "gen_time_s": 1e-5})
    rid = compute_run_id(params)
    assert rid == compute_run_id(dict(params))
    assert len(rid) == 12
    int(rid, 16)
    assert compute_run_id({**params, "rho_dollars": 0.3000001}) != rid
    assert compute_run_id({**params, "gen_time_s": 2e-5}) != rid
    assert compute_run_id({**params, "t_end": 21.0}) != rid
    assert compute_run_id({**params, "data_set": "OTHER"}) != rid
    # ints and floats hash alike, and control settings do not change the ID
    assert compute_run_id({**params, "t_end": 20}) == rid
    assert compute_run_id({**params, "method": "BDF"}) == rid


def test_run_id_known_value_is_pinned() -> None:
    # Guards against accidental changes to the canonical JSON (which would orphan stored runs).
    params = {"input": "step", "data_set": "KEEPIN_U235_THERMAL", "t_end": 200.0}
    params |= {"rho_dollars": 0.1, "gen_time_s": 1e-6}
    assert compute_run_id(params) == "1c7f5929f8a2"


# ---- config validation


def test_committed_configs_parse() -> None:
    smoke = load_config(ROOT / "configs" / "smoke.toml")
    assert len(design_points(smoke)) == 9
    full = load_config(ROOT / "configs" / "full.toml")
    assert full.design == "lhs"
    assert len(design_points(full)) == full.n_samples
    assert load_config(ROOT / "configs" / "feedback.toml").input == "step_feedback"


def _raw(**sweep: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "sweep": {"name": "x", "design": "grid", "t_end": 10.0, "input": "step"},
        "params": {"rho_dollars": {"values": [0.1]}, "gen_time_s": {"values": [1e-5]}},
    }
    raw["sweep"].update(sweep)
    return raw


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda r: r.update(extra={}), "unknown key"),
        (lambda r: r["sweep"].update(sed=1), "unknown key"),
        (lambda r: r["params"]["rho_dollars"].update(valuez=[1]), "unknown key"),
        (lambda r: r["params"].update(bogus={"values": [1]}), "not used by input"),
        (lambda r: r["params"].pop("gen_time_s"), "needs"),
        (lambda r: r["sweep"].update(design="sobol"), "design"),
        (lambda r: r["sweep"].update(input="nope"), "input"),
        (lambda r: r["sweep"].update(data_set="NOPE"), "data_set"),
        (lambda r: r["sweep"].update(t_end="long"), "number"),
        (lambda r: r["sweep"].update(n_samples=5), "only valid"),
        (lambda r: r["sweep"].update(design="lhs"), "n_samples"),
        (lambda r: r["params"].update(rho_dollars={"low": 0.1, "high": 0.2}), "explicit"),
        (lambda r: r["params"]["rho_dollars"].update(values=[]), "non-empty"),
        (lambda r: r.update(storage={"compresion": "zstd"}), "unknown key"),
        (lambda r: r.update(storage={"layout": "tree"}), "layout"),
    ],
)
def test_invalid_configs_fail_loudly(mutate: Any, message: str) -> None:
    raw = _raw()
    mutate(raw)
    with pytest.raises(ConfigError, match=message):
        parse_config(raw)


def test_lhs_config_validation() -> None:
    raw = _raw(design="lhs", n_samples=5)
    raw["params"]["rho_dollars"] = {"low": 0.1, "high": 0.5}
    raw["params"]["gen_time_s"] = {"low": 0.0, "high": 1e-4, "scale": "log"}
    with pytest.raises(ConfigError, match="log scale needs low > 0"):
        parse_config(raw)
    raw["params"]["gen_time_s"] = {"values": [1e-5, 1e-4]}
    with pytest.raises(ConfigError, match="lhs design needs low/high"):
        parse_config(raw)
    raw["params"]["gen_time_s"] = {"value": 1e-5}
    assert len(design_points(parse_config(raw))) == 5


def test_load_config_reports_bad_toml(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[sweep\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(path)


# ---- running


def test_run_one_ok_and_failed() -> None:
    cfg = make_config()
    ok = run_one(run_params(cfg, {"rho_dollars": 0.3, "gen_time_s": 1e-5}))
    assert ok.status == "ok"
    assert ok.error is None
    assert ok.metrics["n_max"] > 1.0
    assert ok.t[0] == 0.0
    assert ok.C.shape[0] == 6
    bad = run_one(run_params(cfg, {"rho_dollars": 1.2, "gen_time_s": 1e-5}))
    assert bad.status == "failed"
    assert bad.error is not None
    assert "PromptCriticalError" in bad.error
    assert all(v is None for v in bad.metrics.values())
    assert bad.t is None


def test_sweep_with_a_prompt_critical_run_finishes_and_marks_only_it_failed(
    tmp_path: Path,
) -> None:
    cfg = make_config(params={"rho_dollars": {"values": [0.2, 1.2]}, "gen_time_s": {"value": 1e-5}})
    log = io.StringIO()
    report = run_sweep(cfg, tmp_path, max_workers=1, stream=log)
    assert (report.n_total, report.n_run, report.n_failed) == (2, 2, 1)
    store = open_store(tmp_path)
    runs = store.read_runs().to_pylist()
    by_status = {r["status"]: r for r in runs}
    assert by_status["failed"]["rho_dollars"] == 1.2
    assert "PromptCriticalError" in by_status["failed"]["error"]
    assert by_status["failed"]["n_max"] is None
    assert by_status["ok"]["n_max"] > 1.0
    assert "FAILED" in log.getvalue()
    assert "[2/2] run " in log.getvalue()
    # the failed run has no series, the ok one does
    assert set(store.read_series()["run_id"].to_pylist()) == {by_status["ok"]["run_id"]}


def test_pool_matches_in_process_results(tmp_path: Path) -> None:
    cfg = make_config()
    serial = run_sweep(cfg, tmp_path / "a", max_workers=1, progress=False)
    pooled = run_sweep(cfg, tmp_path / "b", max_workers=2, progress=False)
    assert serial.n_failed == pooled.n_failed == 0
    cols = ["run_id", "n_max", "period_s", "energy"]
    a = open_store(tmp_path / "a").read_runs(columns=cols)
    b = open_store(tmp_path / "b").read_runs(columns=cols)
    assert a.equals(b)


def test_rerun_skips_stored_ids_and_does_not_duplicate(tmp_path: Path) -> None:
    cfg = make_config()
    first = run_sweep(cfg, tmp_path, max_workers=1, progress=False)
    assert (first.n_run, first.n_skipped) == (4, 0)
    mtime = (tmp_path / "runs.parquet").stat().st_mtime_ns
    second = run_sweep(cfg, tmp_path, max_workers=1, progress=False)
    assert (second.n_run, second.n_skipped) == (0, 4)
    assert (tmp_path / "runs.parquet").stat().st_mtime_ns == mtime  # nothing rewritten
    # a bigger grid only runs the new points
    bigger = make_config(
        params={"rho_dollars": {"values": [0.2, 0.4, 0.6]}, "gen_time_s": {"values": [1e-5, 1e-4]}}
    )
    third = run_sweep(bigger, tmp_path, max_workers=1, progress=False)
    assert (third.n_run, third.n_skipped) == (2, 4)
    store = open_store(tmp_path)
    assert len(store.read_runs()) == 6
    assert len(set(store.read_series(columns=["run_id"])["run_id"].to_pylist())) == 6
    forced = run_sweep(bigger, tmp_path, max_workers=1, progress=False, force=True)
    assert forced.n_run == 6
    assert len(store.read_runs()) == 6


def test_provenance_is_on_every_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SHA", "0123456789abcdef0123456789abcdef01234567")
    run_sweep(make_config(), tmp_path, max_workers=1, progress=False)
    rows = open_store(tmp_path).read_runs().to_pylist()
    assert len(rows) == 4
    for row in rows:
        assert row["git_sha"] == "0123456"
        assert row["solver_method"] == "Radau"
        assert (row["rtol"], row["atol"]) == (1e-8, 1e-12)
        for key in ("package_version", "python_version", "numpy_version", "scipy_version"):
            assert row[key]
        assert row["sweep_id"].startswith("tiny-")
        assert row["created_at"].tzinfo is not None
        assert row["input_type"] == "step"
        assert row["data_set"] == "KEEPIN_U235_THERMAL"


def test_git_sha_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setenv("CI_COMMIT_SHORT_SHA", "abcd1234")
    assert git_sha() == "abcd1234"
    monkeypatch.delenv("CI_COMMIT_SHORT_SHA")
    sha = git_sha()  # a real repo gives hex, anything else gives "unknown"
    assert sha == "unknown" or all(c in "0123456789abcdef" for c in sha)


def test_provenance_has_no_hostname_or_home_path(tmp_path: Path) -> None:
    run_sweep(make_config(), tmp_path, max_workers=1, progress=False)
    store = open_store(tmp_path)
    text = repr(store.read_runs().to_pylist()) + repr(store.metadata())
    assert str(Path.home()) not in text
    import socket

    assert socket.gethostname() not in text or len(socket.gethostname()) < 4


def test_spearman_sensitivity_on_an_lhs_sweep(tmp_path: Path) -> None:
    cfg = parse_config(
        {
            "sweep": {
                "name": "lhs",
                "design": "lhs",
                "n_samples": 12,
                "seed": 1,
                "t_end": 20.0,
                "input": "step",
            },
            "params": {
                "rho_dollars": {"low": 0.1, "high": 0.6},
                "gen_time_s": {"low": 1e-6, "high": 1e-4, "scale": "log"},
            },
        }
    )
    run_sweep(cfg, tmp_path, max_workers=1, progress=False)
    runs = open_store(tmp_path).read_runs()
    sens = spearman_sensitivity(runs)
    assert set(sens) == {"period_s", "n_max"}
    assert set(sens["n_max"]) == {"rho_dollars", "gen_time_s"}
    # bigger step -> bigger peak and shorter (more positive growth) period
    assert sens["n_max"]["rho_dollars"] > 0.8
    assert all(-1.0 <= v <= 1.0 for m in sens.values() for v in m.values())
    # a constant input gives nan rather than a warning
    const = spearman_sensitivity(runs, ["t_end"])
    assert math.isnan(const["n_max"]["t_end"])
