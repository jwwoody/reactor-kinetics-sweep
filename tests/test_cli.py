"""Smoke tests for the command line interface (in-process, tiny configs)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rksweep.cli import main

CONFIG = """\
[sweep]
name = "clitest"
design = "grid"
t_end = 20.0
input = "step"
{extra}
[params.rho_dollars]
values = {rho}

[params.gen_time_s]
value = 1e-5
"""


def _config(tmp_path: Path, rho: str = "[0.2, 0.4]", extra: str = "") -> Path:
    path = tmp_path / "c.toml"
    path.write_text(CONFIG.format(rho=rho, extra=extra))
    return path


def test_run_prints_metrics(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "--rho-dollars", "0.3", "--gen-time", "1e-5", "--t-end", "20"])
    out = capsys.readouterr().out
    assert code == 0
    for key in ("run_id", "n_max", "period_s", "energy", "nfev"):
        assert key in out


def test_run_prompt_critical_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "--rho-dollars", "1.5", "--gen-time", "1e-5"])
    assert code == 1
    assert "PromptCriticalError" in capsys.readouterr().err


def test_sweep_export_golden_pipeline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _config(tmp_path)
    res, data, gold = tmp_path / "res", tmp_path / "data", tmp_path / "g.json"
    assert main(["sweep", str(cfg), "--out", str(res), "--workers", "1"]) == 0
    assert (res / "runs.parquet").is_file()
    assert (res / "timeseries.parquet").is_file()
    assert main(["export", str(res), "--out", str(data)]) == 0
    index = json.loads((data / "index.json").read_text())
    assert [r["status"] for r in index["runs"]] == ["ok", "ok"]
    out = capsys.readouterr().out
    assert "2 runs (2 run, 0 skipped, 0 failed)" in out
    assert "exported 2 runs" in out
    from rksweep import golden

    assert golden.main([str(res), str(gold), "--update"]) == 0
    assert golden.main([str(res), str(gold)]) == 0
    # a second sweep skips everything
    assert main(["sweep", str(cfg), "--out", str(res), "--workers", "1"]) == 0
    assert "0 run, 2 skipped" in capsys.readouterr().out


def test_sweep_fails_the_build_when_a_run_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _config(tmp_path, rho="[0.2, 1.3]")
    out = tmp_path / "res"
    assert main(["sweep", str(cfg), "--out", str(out), "--workers", "1"]) == 1
    err = capsys.readouterr().err
    assert "failed run" in err
    assert "PromptCriticalError" in err
    # results are still stored, and the failure can be allowed explicitly or by config
    assert (out / "runs.parquet").is_file()
    assert main(["sweep", str(cfg), "--out", str(out), "--workers", "1", "--allow-failures"]) == 0
    expected = _config(tmp_path, rho="[0.2, 1.3]", extra="expected_failures = 1\n")
    assert main(["sweep", str(expected), "--out", str(out), "--workers", "1"]) == 0
    # expecting a failure that does not happen is an error too
    unexpected = _config(tmp_path, rho="[0.2, 0.4]", extra="expected_failures = 1\n")
    assert main(["sweep", str(unexpected), "--out", str(tmp_path / "r2"), "--workers", "1"]) == 1


def test_bad_config_and_missing_results_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _config(tmp_path, extra="bogus = 1\n")
    assert main(["sweep", str(cfg), "--out", str(tmp_path / "r")]) == 2
    assert "unknown key" in capsys.readouterr().err
    assert main(["sweep", str(tmp_path / "missing.toml"), "--out", str(tmp_path / "r")]) == 2
    assert main(["export", str(tmp_path / "empty"), "--out", str(tmp_path / "o")]) == 2


def test_console_script_and_module_entry_points(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "rksweep.cli", "run", "--rho-dollars", "0.1", "--gen-time", "1e-5",
         "--t-end", "10"],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0
    assert "n_max" in proc.stdout
    gold = subprocess.run(
        [sys.executable, "-m", "rksweep.golden", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert gold.returncode == 0
    assert "--update" in gold.stdout
