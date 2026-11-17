"""Shared fixtures: tiny sweeps that run in well under a second."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from rksweep.sweep import SweepConfig, SweepReport, parse_config, run_sweep

TINY: dict[str, Any] = {
    "sweep": {"name": "tiny", "design": "grid", "t_end": 20.0, "input": "step"},
    "params": {
        "rho_dollars": {"values": [0.2, 0.4]},
        "gen_time_s": {"values": [1e-5, 1e-4]},
    },
}


def make_config(**overrides: Any) -> SweepConfig:
    """A valid 2 x 2 grid config; ``overrides`` replace top-level tables wholesale."""
    raw = copy.deepcopy(TINY)
    raw.update(copy.deepcopy(overrides))
    return parse_config(raw)


@pytest.fixture
def tiny_config() -> SweepConfig:
    return make_config()


@pytest.fixture(scope="module")
def tiny_results(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, SweepReport]:
    """A finished 2 x 2 sweep with precursors stored, shared by a module's tests."""
    out = tmp_path_factory.mktemp("tiny") / "results"
    cfg = make_config(storage={"precursors": True})
    report = run_sweep(cfg, out, max_workers=1, progress=False)
    return out, report
