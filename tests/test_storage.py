"""Tests for the Parquet (and optional HDF5) result stores."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from conftest import make_config

from rksweep.storage import (
    INPUT_COLUMNS,
    RUNS_SCHEMA,
    Hdf5Store,
    ParquetStore,
    SeriesBlock,
    StorageOptions,
    atomic_write,
    file_metadata,
    open_store,
    runs_table,
    series_table,
    timeseries_schema,
)
from rksweep.sweep import SweepReport, run_sweep

Results = tuple[Path, SweepReport]


def _blocks(n_runs: int = 3, n_points: int = 50, seed: int = 0) -> list[SeriesBlock]:
    rng = np.random.default_rng(seed)
    blocks = []
    for i in range(n_runs):
        t = np.concatenate(([0.0], np.geomspace(1e-6, 200.0, n_points - 1)))
        n = 1.0 + rng.random(n_points) * 10 ** (i + 1)
        C = rng.random((6, n_points)) * 1e3
        blocks.append(SeriesBlock(f"run{i:08d}", t, n, C))
    return blocks


def _rows(ids: list[str], rho: list[float]) -> list[dict[str, object]]:
    return [
        {"run_id": rid, "status": "ok", "rho_dollars": r, "n_max": 1.0 + r, "nfev": 10}
        for rid, r in zip(ids, rho, strict=True)
    ]


META = file_metadata("KEEPIN_U235_THERMAL", {"name": "t"})


def test_round_trip_float64_is_exact(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, StorageOptions(precursors=True))
    blocks = _blocks()
    store.write_series(series_table(blocks, META))
    for block in blocks:
        got = store.read_series([block.run_id])
        assert got["run_id"].to_pylist() == [block.run_id] * block.t.size
        np.testing.assert_array_equal(got["t"].to_numpy(), block.t)
        np.testing.assert_array_equal(got["n"].to_numpy(), block.n)
        assert block.C is not None
        for i in range(6):
            np.testing.assert_array_equal(got[f"C{i + 1}"].to_numpy(), block.C[i])


def test_round_trip_float32_is_within_a_stated_tolerance(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, StorageOptions(precursors=True, float32=True))
    blocks = _blocks()
    store.write_series(series_table(blocks, META))
    got = store.read_series([blocks[0].run_id])
    assert got.schema.field("n").type == pa.float32()
    assert got.schema.field("t").type == pa.float64()
    np.testing.assert_allclose(got["n"].to_numpy(), blocks[0].n, rtol=1e-6, atol=0)
    assert blocks[0].C is not None
    np.testing.assert_allclose(got["C3"].to_numpy(), blocks[0].C[2], rtol=1e-6, atol=0)


def test_precursor_columns_are_optional(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, StorageOptions(precursors=False))
    store.write_series(series_table(_blocks(), META))
    assert pq.read_schema(store.series_file).names == ["run_id", "t", "n"]
    with pytest.raises(ValueError, match="no C columns"):
        ParquetStore(tmp_path / "x", StorageOptions(precursors=True)).write_series(
            series_table(_blocks()).select(["run_id", "t", "n"])
        )


def test_schema_on_disk_matches_declared_schema_and_metadata(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, StorageOptions(precursors=True))
    ids = [b.run_id for b in _blocks()]
    store.write_runs(runs_table(_rows(ids, [0.1, 0.2, 0.3]), META))
    store.write_series(series_table(_blocks(), META))

    runs_schema = pq.read_schema(store.runs_path)
    assert runs_schema.remove_metadata().equals(RUNS_SCHEMA.remove_metadata())
    ts_schema = pq.read_schema(store.series_file)
    assert ts_schema.remove_metadata().equals(timeseries_schema(precursors=True))
    for schema in (runs_schema, ts_schema):
        assert {b"data_set", b"units", b"model"} <= set(schema.metadata)
        assert schema.metadata[b"data_set"] == b"KEEPIN_U235_THERMAL"
        assert json.loads(schema.metadata[b"units"])["t"] == "s"
    assert RUNS_SCHEMA.field("created_at").type == pa.timestamp("us", tz="UTC")
    assert RUNS_SCHEMA.field("rho_dollars").type == pa.float64()
    assert RUNS_SCHEMA.field("run_id").type == pa.string()
    assert store.metadata()["data_set"] == "KEEPIN_U235_THERMAL"
    assert json.loads(store.metadata()["sweep"]) == {"name": "t"}


def test_parquet_files_are_zstd_by_default_and_sorted(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    shuffled = list(reversed(_blocks()))
    store.write_series(series_table(shuffled, META))
    meta = pq.ParquetFile(store.series_file).metadata
    assert meta.row_group(0).column(0).compression == "ZSTD"
    ids = store.read_series(columns=["run_id"])["run_id"].to_pylist()
    assert ids == sorted(ids)
    for codec, name in (("snappy", "SNAPPY"), ("none", "UNCOMPRESSED")):
        other = ParquetStore(tmp_path / codec, StorageOptions(compression=codec))
        other.write_series(series_table(_blocks(), META))
        assert pq.ParquetFile(other.series_file).metadata.row_group(0).column(0).compression == name


def test_filtered_reads_return_exactly_the_expected_run_ids(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ids = [f"id{i:010d}" for i in range(6)]
    rho = [0.1, 0.3, 0.5, 0.6, 0.8, 0.9]
    store.write_runs(runs_table(_rows(ids, rho), META))
    got = store.read_runs(pc.field("rho_dollars") > 0.5, ["run_id", "rho_dollars"])
    assert sorted(got["run_id"].to_pylist()) == ids[3:]
    assert store.run_ids("ok") == set(ids)
    assert store.run_ids("failed") == set()
    store.write_series(
        series_table([SeriesBlock(i, np.array([0.0, 1.0, 2.0]), np.ones(3)) for i in ids])
    )
    sub = store.read_series(ids[3:], ["run_id", "t", "n"], t_range=(0.5, 2.0))
    assert sorted(set(sub["run_id"].to_pylist())) == ids[3:]
    assert sub["t"].to_pylist() == [1.0, 2.0] * 3


def test_merge_replaces_same_run_and_keeps_others(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path, StorageOptions(precursors=True))
    blocks = _blocks()
    store.write_series(series_table(blocks[:2], META))
    store.write_series(series_table(blocks[1:], META))
    got = store.read_series(columns=["run_id"])["run_id"].to_pylist()
    assert sorted(set(got)) == [b.run_id for b in blocks]
    assert len(got) == sum(b.t.size for b in blocks)


@pytest.mark.parametrize("layout", ["single", "hive"])
def test_layouts_read_back_identically(tmp_path: Path, layout: str) -> None:
    store = ParquetStore(tmp_path, StorageOptions(precursors=True, layout=layout))
    blocks = _blocks()
    store.write_series(series_table(blocks, META))
    one = store.read_series([blocks[1].run_id])
    assert one.column_names == ["run_id", "t", "n", *[f"C{i}" for i in range(1, 7)]]
    np.testing.assert_array_equal(one["n"].to_numpy(), blocks[1].n)
    assert set(store.read_series()["run_id"].to_pylist()) == {b.run_id for b in blocks}
    if layout == "hive":
        assert sorted(p.name for p in store.series_dir.iterdir()) == [
            f"run_id={b.run_id}" for b in blocks
        ]
        # digit-only IDs must stay strings, not become integers
        digits = SeriesBlock("123456789012", np.array([0.0, 1.0]), np.ones(2), np.ones((6, 2)))
        store.write_series(series_table([digits]))
        assert store.read_series(["123456789012"])["run_id"].to_pylist() == ["123456789012"] * 2


def test_atomic_write_never_leaves_a_partial_file(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")

    def boom(tmp: Path) -> None:
        tmp.write_bytes(b"half")
        raise RuntimeError("crash")

    with pytest.raises(RuntimeError, match="crash"):
        atomic_write(target, boom)
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]
    atomic_write(target, lambda tmp: tmp.write_bytes(b"new"))
    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]


def test_every_ok_run_has_a_non_empty_series(tiny_results: Results) -> None:
    out, report = tiny_results
    assert report.n_failed == 0
    store = open_store(out)
    ok_ids = store.run_ids("ok")
    assert len(ok_ids) == 4
    series = store.read_series(columns=["run_id", "n"])
    counts = pc.value_counts(series["run_id"]).to_pylist()
    per_run = {c["values"]: c["counts"] for c in counts}
    assert set(per_run) == ok_ids
    assert all(count > 100 for count in per_run.values())
    assert not any(np.isnan(series["n"].to_numpy()))


def test_sweep_writes_both_tables(tiny_results: Results) -> None:
    out, _ = tiny_results
    assert (out / "runs.parquet").is_file()
    assert (out / "timeseries.parquet").is_file()
    assert not [p for p in out.iterdir() if p.name.startswith(".")]
    runs = open_store(out).read_runs()
    assert runs.schema.remove_metadata().equals(RUNS_SCHEMA.remove_metadata())
    unset = [c for c in INPUT_COLUMNS if runs[c].null_count == runs.num_rows]
    assert unset == ["ramp_rate_dps", "alpha_fb", "freq_hz", "fb_power_coeff", "fb_tau_s"]


def test_sweep_with_hive_and_float32_options(tmp_path: Path) -> None:
    cfg = make_config(storage={"layout": "hive", "float32": True, "compression": "snappy"})
    run_sweep(cfg, tmp_path, max_workers=1, progress=False)
    store = open_store(tmp_path)
    assert (tmp_path / "timeseries").is_dir()
    assert store.read_series().schema.field("n").type == pa.float32()
    assert store.read_series().column_names == ["run_id", "t", "n"]


def test_open_store_needs_results(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_store(tmp_path)


def test_storage_options_validation() -> None:
    with pytest.raises(ValueError, match="compression"):
        StorageOptions(compression="gzip")
    with pytest.raises(ValueError, match="compression"):
        StorageOptions(backend="hdf5", compression="zstd")
    with pytest.raises(ValueError, match="backend"):
        StorageOptions(backend="sqlite")
    with pytest.raises(ValueError, match="hdf5"):
        StorageOptions(backend="hdf5", layout="hive")


# ---- HDF5 (optional extra)


@pytest.fixture
def h5store(tmp_path: Path) -> Hdf5Store:
    pytest.importorskip("h5py")
    return Hdf5Store(tmp_path, StorageOptions(backend="hdf5", precursors=True))


def test_hdf5_round_trip_matches_parquet(tmp_path: Path, h5store: Hdf5Store) -> None:
    blocks = _blocks()
    ids = [b.run_id for b in blocks]
    failed = {"run_id": "failedrun001", "status": "failed", "error": "boom", "rho_dollars": 1.2}
    rows = [*_rows(ids, [0.1, 0.2, 0.3]), failed]
    runs = runs_table(rows, META)
    h5store.write_runs(runs)
    h5store.write_series(series_table(blocks, META))
    pq_store = ParquetStore(tmp_path / "pq", StorageOptions(precursors=True))
    pq_store.write_runs(runs)
    pq_store.write_series(series_table(blocks, META))

    assert h5store.read_runs().equals(pq_store.read_runs())  # incl. nulls vs NaN
    assert h5store.metadata()["data_set"] == "KEEPIN_U235_THERMAL"
    assert h5store.read_series().equals(pq_store.read_series())
    one = h5store.read_series([ids[0]], ["t", "n"], t_range=(0.0, 1.0))
    assert one.column_names == ["t", "n"]
    assert one["t"].to_numpy().max() <= 1.0
    got = h5store.read_runs(pc.field("rho_dollars") > 0.15, ["run_id"])
    assert sorted(got["run_id"].to_pylist()) == sorted([ids[1], ids[2], "failedrun001"])
    assert h5store.run_ids("ok") == set(ids)
    # merge keeps other runs, replaces same id
    h5store.write_series(series_table(blocks[:1], META))
    assert set(h5store.read_series()["run_id"].to_pylist()) == set(ids)
    assert open_store(tmp_path).__class__ is Hdf5Store
    assert h5store.disk_bytes() > 0


def test_hdf5_sweep_end_to_end(tmp_path: Path) -> None:
    pytest.importorskip("h5py")
    cfg = make_config(storage={"backend": "hdf5", "precursors": False})
    report = run_sweep(cfg, tmp_path, max_workers=1, progress=False)
    assert report.n_failed == 0
    store = open_store(tmp_path)
    assert isinstance(store, Hdf5Store)
    assert len(store.read_runs()) == 4
    assert store.read_series().column_names == ["run_id", "t", "n"]
    again = run_sweep(cfg, tmp_path, max_workers=1, progress=False)
    assert again.n_skipped == 4
