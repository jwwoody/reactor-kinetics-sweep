"""Result storage: two tables, Parquet by default, optional HDF5 behind the same interface.

Two kinds of data, two tables (keep them apart: most questions only touch ``runs``):

* ``runs``: one row per run (inputs, metrics, status, provenance). Small.
* ``timeseries``: one row per run and output time (``run_id, t, n`` and optionally the
  precursor columns ``C1..C6``). Large.

Everything is written with an explicit ``pa.schema`` (never inferred), the units and the
data-set name travel inside the file as schema metadata, and every write goes to a temporary
path first and is moved into place with :func:`os.replace`, so a crash never leaves a half
written file behind.

Two backends implement :class:`ResultStore`:

* :class:`ParquetStore` (``results/runs.parquet`` plus either ``results/timeseries.parquet``
  or a hive-partitioned ``results/timeseries/run_id=<id>/`` directory).
* :class:`Hdf5Store` (``results/results.h5``, one group per run). ``h5py`` is an optional
  extra (``pip install "rksweep[hdf5]"``) and is imported lazily.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]

#: Physical input columns of the ``runs`` table (all nullable float64, unset ones are null).
INPUT_COLUMNS = (
    "rho_dollars",
    "gen_time_s",
    "ramp_rate_dps",
    "alpha_fb",
    "freq_hz",
    "fb_power_coeff",
    "fb_tau_s",
    "t_end",
)
METRIC_FLOAT_COLUMNS = ("n_max", "t_peak", "period_s", "energy", "t_double", "wall_time_s")
METRIC_INT_COLUMNS = ("nfev", "njev")
METRIC_COLUMNS = (*METRIC_FLOAT_COLUMNS, *METRIC_INT_COLUMNS)

RUNS_SCHEMA = pa.schema(
    [
        pa.field("run_id", pa.string(), nullable=False),
        pa.field("sweep_id", pa.string()),
        pa.field("input_type", pa.string()),
        pa.field("data_set", pa.string()),
        pa.field("status", pa.string()),
        pa.field("error", pa.string()),
        *[pa.field(name, pa.float64()) for name in INPUT_COLUMNS],
        *[pa.field(name, pa.float64()) for name in METRIC_FLOAT_COLUMNS],
        *[pa.field(name, pa.int64()) for name in METRIC_INT_COLUMNS],
        pa.field("git_sha", pa.string()),
        pa.field("package_version", pa.string()),
        pa.field("solver_method", pa.string()),
        pa.field("rtol", pa.float64()),
        pa.field("atol", pa.float64()),
        pa.field("python_version", pa.string()),
        pa.field("numpy_version", pa.string()),
        pa.field("scipy_version", pa.string()),
        pa.field("created_at", pa.timestamp("us", tz="UTC")),
    ]
)

UNITS: dict[str, str] = {
    "t": "s",
    "n": "relative power, n(0) = 1",
    "C": "relative precursor population of group i (C_i)",
    "rho_dollars": "dollars (rho / beta)",
    "gen_time_s": "s (prompt generation time Lambda)",
    "ramp_rate_dps": "dollars per second",
    "alpha_fb": "1/K (temperature coefficient of reactivity)",
    "freq_hz": "Hz",
    "fb_power_coeff": "K/s per unit relative power",
    "fb_tau_s": "s",
    "t_end": "s",
    "n_max": "relative power",
    "t_peak": "s",
    "period_s": "s (1/omega from the late log-slope; negative when decaying)",
    "energy": "relative power * s (trapezoid integral of n dt)",
    "t_double": "s (first time n >= 2)",
    "wall_time_s": "s (solver wall clock)",
    "nfev": "count",
    "njev": "count",
}
MODEL = (
    "point kinetics, six delayed-neutron groups (textbook teaching data, not for design or safety)"
)


def file_metadata(data_set: str, sweep: dict[str, Any] | None = None) -> dict[str, str]:
    """Key/value metadata embedded in every file: data set, units, model (and sweep info)."""
    meta = {"data_set": data_set, "units": json.dumps(UNITS, sort_keys=True), "model": MODEL}
    if sweep is not None:
        meta["sweep"] = json.dumps(sweep, sort_keys=True)
    return meta


def timeseries_schema(
    *, precursors: bool = True, float32: bool = False, n_groups: int = 6
) -> pa.Schema:
    """Schema of the ``timeseries`` table. ``t`` stays float64; ``n``/``C`` may be float32."""
    value_type = pa.float32() if float32 else pa.float64()
    fields = [
        pa.field("run_id", pa.string(), nullable=False),
        pa.field("t", pa.float64()),
        pa.field("n", value_type),
    ]
    if precursors:
        fields += [pa.field(f"C{i}", value_type) for i in range(1, n_groups + 1)]
    return pa.schema(fields)


def runs_table(rows: Sequence[dict[str, Any]], metadata: dict[str, str] | None = None) -> pa.Table:
    """Build the ``runs`` table from row dicts (missing keys become null), sorted by run_id."""
    table = pa.Table.from_pylist(list(rows), schema=RUNS_SCHEMA)
    table = table.sort_by("run_id")
    if metadata:
        table = table.replace_schema_metadata(metadata)
    return table


@dataclass
class SeriesBlock:
    """One run's series: ``t`` and ``n`` of length N and optional ``C`` of shape (G, N)."""

    run_id: str
    t: FloatArray
    n: FloatArray
    C: FloatArray | None = None


def series_table(blocks: Iterable[SeriesBlock], metadata: dict[str, str] | None = None) -> pa.Table:
    """Concatenate blocks into one float64 ``timeseries`` table sorted by ``(run_id, t)``.

    Precursor columns are included if (and only if) every block carries ``C``.
    """
    ordered = sorted(blocks, key=lambda b: b.run_id)
    with_c = bool(ordered) and all(b.C is not None for b in ordered)
    n_groups = ordered[0].C.shape[0] if with_c and ordered[0].C is not None else 6
    schema = timeseries_schema(precursors=with_c, n_groups=n_groups)
    if not ordered:
        return schema.empty_table()
    lengths = [b.t.size for b in ordered]
    ids = np.repeat(np.array([b.run_id for b in ordered], dtype=object), lengths)
    columns: list[Any] = [
        pa.array(ids, type=pa.string()),
        pa.array(np.concatenate([b.t for b in ordered])),
        pa.array(np.concatenate([b.n for b in ordered])),
    ]
    if with_c:
        stacked = np.concatenate([b.C for b in ordered if b.C is not None], axis=1)
        columns += [pa.array(stacked[i]) for i in range(n_groups)]
    table = pa.Table.from_arrays(columns, schema=schema)
    return table.replace_schema_metadata(metadata) if metadata else table


@dataclass(frozen=True)
class StorageOptions:
    """How results are laid out on disk.

    Attributes:
        backend: ``"parquet"`` or ``"hdf5"``.
        compression: Parquet: ``"zstd"`` (default), ``"snappy"``, ``"none"``; HDF5:
            ``"gzip"`` (default) or ``"none"``. ``None`` picks the backend default.
        float32: Store ``n`` and ``C`` as float32 (``t`` stays float64).
        precursors: Keep the ``C1..C6`` columns of the time series.
        layout: Parquet only: ``"single"`` sorted file or ``"hive"`` (one directory per run).
        row_group_size: Parquet row-group size of the single-file layout.
    """

    backend: str = "parquet"
    compression: str | None = None
    float32: bool = False
    precursors: bool = False
    layout: str = "single"
    row_group_size: int = 65536

    def __post_init__(self) -> None:
        if self.backend not in ("parquet", "hdf5"):
            raise ValueError(f"unknown storage backend {self.backend!r}")
        if self.layout not in ("single", "hive"):
            raise ValueError(f"unknown layout {self.layout!r}; choose 'single' or 'hive'")
        if self.backend == "hdf5" and self.layout != "single":
            raise ValueError("the hdf5 backend has one file; layout must be 'single'")
        allowed = ("zstd", "snappy", "none") if self.backend == "parquet" else ("gzip", "none")
        if self.compression is not None and self.compression not in allowed:
            raise ValueError(
                f"compression {self.compression!r} not valid for {self.backend}; use {allowed}"
            )
        if self.row_group_size < 1:
            raise ValueError("row_group_size must be positive")

    @property
    def codec(self) -> str:
        """Effective compression codec name."""
        if self.compression is not None:
            return self.compression
        return "zstd" if self.backend == "parquet" else "gzip"


def atomic_write(path: Path, write: Callable[[Path], None]) -> None:
    """Call ``write(tmp)`` then move ``tmp`` onto ``path`` with :func:`os.replace`.

    If ``write`` raises, the temporary file is removed and ``path`` is left untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def conform_series(table: pa.Table, options: StorageOptions) -> pa.Table:
    """Select/cast a series table to the columns and precision the options ask for."""
    has_c = "C1" in table.column_names
    if options.precursors and not has_c:
        raise ValueError("precursors=True but the series table has no C columns")
    n_groups = sum(1 for name in table.column_names if name.startswith("C") and name[1:].isdigit())
    schema = timeseries_schema(
        precursors=options.precursors, float32=options.float32, n_groups=max(n_groups, 1)
    )
    out = table.select(schema.names).cast(schema)
    return out.replace_schema_metadata(table.schema.metadata)


def _sorted(table: pa.Table) -> pa.Table:
    meta = table.schema.metadata
    return table.sort_by([("run_id", "ascending"), ("t", "ascending")]).replace_schema_metadata(
        meta
    )


class ResultStore(ABC):
    """Common interface of the storage backends."""

    def __init__(self, root: str | Path, options: StorageOptions | None = None) -> None:
        self.root = Path(root)
        self.options = options or StorageOptions()

    @abstractmethod
    def exists(self) -> bool:
        """True if a ``runs`` table has been written."""

    @abstractmethod
    def write_runs(self, table: pa.Table, *, merge: bool = True) -> None:
        """Store the ``runs`` table; with ``merge`` rows of existing other runs are kept."""

    @abstractmethod
    def write_series(self, table: pa.Table, *, merge: bool = True) -> None:
        """Store the ``timeseries`` table (any superset of the configured columns)."""

    @abstractmethod
    def read_runs(
        self, filter: pc.Expression | None = None, columns: Sequence[str] | None = None
    ) -> pa.Table:
        """Read the ``runs`` table, optionally filtered by an Arrow expression."""

    @abstractmethod
    def read_series(
        self,
        run_ids: Sequence[str] | None = None,
        columns: Sequence[str] | None = None,
        t_range: tuple[float, float] | None = None,
    ) -> pa.Table:
        """Read series rows; filter by run IDs and/or ``t_range = (t_min, t_max)``."""

    @abstractmethod
    def metadata(self) -> dict[str, str]:
        """File-level metadata of the ``runs`` table (data_set, units, model, sweep)."""

    @abstractmethod
    def files(self) -> list[Path]:
        """All files that make up the store (for size accounting)."""

    def disk_bytes(self) -> int:
        """Total bytes on disk of :meth:`files`."""
        return sum(p.stat().st_size for p in self.files())

    def run_ids(self, status: str | None = None) -> set[str]:
        """Stored run IDs (optionally only with the given status)."""
        if not self.exists():
            return set()
        flt = pc.field("status") == status if status is not None else None
        return set(self.read_runs(flt, ["run_id"])["run_id"].to_pylist())


class ParquetStore(ResultStore):
    """Parquet files: ``runs.parquet`` and ``timeseries.parquet`` (or hive ``timeseries/``)."""

    @property
    def runs_path(self) -> Path:
        return self.root / "runs.parquet"

    @property
    def series_file(self) -> Path:
        return self.root / "timeseries.parquet"

    @property
    def series_dir(self) -> Path:
        return self.root / "timeseries"

    def exists(self) -> bool:
        return self.runs_path.exists()

    def _compression(self) -> str:
        return self.options.codec

    def write_runs(self, table: pa.Table, *, merge: bool = True) -> None:
        table = table.cast(RUNS_SCHEMA.with_metadata(table.schema.metadata))
        meta = table.schema.metadata
        if merge and self.runs_path.exists():
            old = pq.read_table(self.runs_path, schema=RUNS_SCHEMA)
            keep = old.filter(~pc.field("run_id").isin(table["run_id"]))
            table = pa.concat_tables([keep, table.replace_schema_metadata(None)])
        table = table.sort_by("run_id").replace_schema_metadata(meta)
        atomic_write(
            self.runs_path,
            lambda p: pq.write_table(table, p, compression=self._compression()),
        )

    def write_series(self, table: pa.Table, *, merge: bool = True) -> None:
        table = conform_series(table, self.options)
        if self.options.layout == "hive":
            self._write_hive(table, merge)
        else:
            self._write_single(table, merge)

    def _write_single(self, table: pa.Table, merge: bool) -> None:
        meta = table.schema.metadata
        if merge and self.series_file.exists():
            old = pq.read_table(self.series_file)
            old = old.filter(~pc.field("run_id").isin(table["run_id"]))
            try:
                old = old.cast(table.schema.remove_metadata())
            except (pa.ArrowInvalid, KeyError) as exc:
                raise ValueError(
                    "existing timeseries file was written with different options "
                    "(precursors/float32); use a new output directory"
                ) from exc
            table = pa.concat_tables([old, table.replace_schema_metadata(None)])
        table = _sorted(table.replace_schema_metadata(meta))
        atomic_write(
            self.series_file,
            lambda p: pq.write_table(
                table,
                p,
                compression=self._compression(),
                row_group_size=self.options.row_group_size,
            ),
        )

    def _write_hive(self, table: pa.Table, merge: bool) -> None:
        tmp = self.root / f".timeseries.tmp-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            fmt = ds.ParquetFileFormat()
            ds.write_dataset(
                _sorted(table),
                tmp,
                format=fmt,
                file_options=fmt.make_write_options(compression=self._compression()),
                partitioning=ds.partitioning(pa.schema([("run_id", pa.string())]), flavor="hive"),
                basename_template="part-{i}.parquet",
                existing_data_behavior="overwrite_or_ignore",
                max_partitions=1_000_000,
                max_open_files=2048,
            )
            if not merge and self.series_dir.exists():
                shutil.rmtree(self.series_dir)
            self.series_dir.mkdir(parents=True, exist_ok=True)
            for part in tmp.iterdir():
                dest = self.series_dir / part.name
                if dest.exists():
                    shutil.rmtree(dest)
                os.replace(part, dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _series_dataset(self) -> ds.Dataset:
        if self.series_file.exists():
            return ds.dataset(self.series_file, format="parquet")
        if self.series_dir.exists():
            part = ds.partitioning(pa.schema([("run_id", pa.string())]), flavor="hive")
            return ds.dataset(self.series_dir, format="parquet", partitioning=part)
        raise FileNotFoundError(f"no timeseries found in {self.root}")

    def read_runs(
        self, filter: pc.Expression | None = None, columns: Sequence[str] | None = None
    ) -> pa.Table:
        return ds.dataset(self.runs_path, format="parquet").to_table(
            filter=filter, columns=list(columns) if columns else None
        )

    def read_series(
        self,
        run_ids: Sequence[str] | None = None,
        columns: Sequence[str] | None = None,
        t_range: tuple[float, float] | None = None,
    ) -> pa.Table:
        dataset = self._series_dataset()
        flt: pc.Expression | None = None
        if run_ids is not None:
            flt = pc.field("run_id").isin(list(run_ids))
        if t_range is not None:
            tr = (pc.field("t") >= t_range[0]) & (pc.field("t") <= t_range[1])
            flt = tr if flt is None else flt & tr
        names = [n for n in dataset.schema.names if n != "run_id"]
        order = ["run_id", *names]  # hive puts the partition column last; normalise
        wanted = [c for c in order if columns is None or c in columns]
        return dataset.to_table(filter=flt, columns=wanted)

    def metadata(self) -> dict[str, str]:
        raw = pq.read_schema(self.runs_path).metadata or {}
        return {k.decode(): v.decode() for k, v in raw.items() if not k.startswith(b"ARROW:")}

    def files(self) -> list[Path]:
        found = [p for p in (self.runs_path, self.series_file) if p.exists()]
        if self.series_dir.exists():
            found += sorted(p for p in self.series_dir.rglob("*") if p.is_file())
        return found


def _h5py() -> Any:
    try:
        import h5py
    except ImportError as exc:  # pragma: no cover - exercised only without h5py
        raise ImportError('HDF5 support needs h5py: pip install "rksweep[hdf5]"') from exc
    return h5py


class Hdf5Store(ResultStore):
    """One HDF5 file: ``runs/<column>`` datasets and ``series/<run_id>/{t,y}`` per run.

    ``y`` holds ``n`` (and ``C1..C6``) as rows, chunked and compressed with gzip.
    """

    @property
    def path(self) -> Path:
        return self.root / "results.h5"

    def exists(self) -> bool:
        return self.path.exists()

    def files(self) -> list[Path]:
        return [self.path] if self.path.exists() else []

    def _modify(self, work: Callable[[Any], None]) -> None:
        h5py = _h5py()

        def write(tmp: Path) -> None:
            if self.path.exists():
                shutil.copyfile(self.path, tmp)
            with h5py.File(tmp, "a") as f:
                work(f)

        atomic_write(self.path, write)

    def _compression(self) -> str | None:
        return None if self.options.codec == "none" else self.options.codec

    @staticmethod
    def _write_runs_group(f: Any, table: pa.Table) -> None:
        h5py = _h5py()
        if "runs" in f:
            del f["runs"]
        grp = f.create_group("runs")
        for field in RUNS_SCHEMA:
            col = table[field.name].combine_chunks()
            valid = pc.is_valid(col).to_numpy(zero_copy_only=False)
            if pa.types.is_string(field.type):
                data = np.array([v or "" for v in col.to_pylist()], dtype=object)
                grp.create_dataset(field.name, data=data, dtype=h5py.string_dtype())
            elif pa.types.is_timestamp(field.type):
                ints = col.cast(pa.int64()).fill_null(0).to_numpy(zero_copy_only=False)
                grp.create_dataset(field.name, data=ints)
            elif pa.types.is_integer(field.type):
                grp.create_dataset(field.name, data=col.fill_null(0).to_numpy(zero_copy_only=False))
            else:
                grp.create_dataset(
                    field.name, data=col.fill_null(float("nan")).to_numpy(zero_copy_only=False)
                )
            if not valid.all():
                grp.create_dataset(f"{field.name}__valid", data=valid)

    @staticmethod
    def _read_runs_group(f: Any) -> pa.Table:
        grp = f["runs"]
        arrays = []
        for field in RUNS_SCHEMA:
            raw = grp[field.name][()]
            mask = ~grp[f"{field.name}__valid"][()] if f"{field.name}__valid" in grp else None
            if pa.types.is_string(field.type):
                values = [v.decode() if isinstance(v, bytes) else v for v in raw]
                arrays.append(pa.array(values, type=field.type, mask=mask))
            elif pa.types.is_timestamp(field.type):
                arrays.append(pa.array(raw, type=pa.int64(), mask=mask).cast(field.type))
            else:
                arrays.append(pa.array(raw, type=field.type, mask=mask))
        return pa.Table.from_arrays(arrays, schema=RUNS_SCHEMA)

    def write_runs(self, table: pa.Table, *, merge: bool = True) -> None:
        table = table.cast(RUNS_SCHEMA.with_metadata(table.schema.metadata))
        meta = table.schema.metadata or {}

        def work(f: Any) -> None:
            new = table
            if merge and "runs" in f:
                old = self._read_runs_group(f)
                keep = old.filter(~pc.field("run_id").isin(table["run_id"]))
                new = pa.concat_tables([keep, table.replace_schema_metadata(None)])
            self._write_runs_group(f, new.sort_by("run_id"))
            f.attrs["metadata"] = json.dumps({k.decode(): v.decode() for k, v in meta.items()})

        self._modify(work)

    def write_series(self, table: pa.Table, *, merge: bool = True) -> None:
        table = conform_series(table, self.options)
        value_cols = [c for c in table.column_names if c not in ("run_id", "t")]
        float32 = self.options.float32
        comp = self._compression()

        def work(f: Any) -> None:
            if not merge and "series" in f:
                del f["series"]
            series = f.require_group("series")
            ids = np.array(table["run_id"].to_pylist(), dtype=object)
            t = table["t"].to_numpy()
            y = np.stack([table[c].to_numpy() for c in value_cols])
            bounds = [0, *(np.flatnonzero(ids[1:] != ids[:-1]) + 1).tolist(), len(ids)]
            for a, b in itertools.pairwise(bounds):
                rid = str(ids[a])
                if rid in series:
                    del series[rid]
                grp = series.create_group(rid)
                kw: dict[str, Any] = {"chunks": True}
                if comp:
                    kw["compression"] = comp
                    if comp == "gzip":
                        kw["compression_opts"] = 4
                grp.create_dataset("t", data=t[a:b], **kw)
                grp.create_dataset("y", data=y[:, a:b], **kw)
            f.attrs["series_columns"] = json.dumps(value_cols)
            f.attrs["float32"] = float32

        self._modify(work)

    def read_runs(
        self, filter: pc.Expression | None = None, columns: Sequence[str] | None = None
    ) -> pa.Table:
        with _h5py().File(self.path, "r") as f:
            table = self._read_runs_group(f)
        if filter is not None:
            table = table.filter(filter)
        return table.select(list(columns)) if columns else table

    def read_series(
        self,
        run_ids: Sequence[str] | None = None,
        columns: Sequence[str] | None = None,
        t_range: tuple[float, float] | None = None,
    ) -> pa.Table:
        with _h5py().File(self.path, "r") as f:
            value_cols: list[str] = json.loads(f.attrs["series_columns"])
            vtype = pa.float32() if bool(f.attrs["float32"]) else pa.float64()
            series = f["series"]
            wanted = sorted(series) if run_ids is None else sorted(set(run_ids) & set(series))
            want_cols = [c for c in ["run_id", "t", *value_cols] if columns is None or c in columns]
            idx = [value_cols.index(c) for c in want_cols if c in value_cols]
            ids: list[str] = []
            ts: list[Any] = []
            ys: list[Any] = []
            for rid in wanted:
                t = series[rid]["t"][()]
                y = series[rid]["y"][idx, :] if idx else np.empty((0, t.size))
                if t_range is not None:
                    keep = (t >= t_range[0]) & (t <= t_range[1])
                    t, y = t[keep], y[:, keep]
                ids.extend([rid] * t.size)
                ts.append(t)
                ys.append(y)
        t_all = np.concatenate(ts) if ts else np.empty(0)
        out: dict[str, Any] = {}
        if "run_id" in want_cols:
            out["run_id"] = pa.array(ids, type=pa.string())
        if "t" in want_cols:
            out["t"] = pa.array(t_all, type=pa.float64())
        y_all = np.concatenate(ys, axis=1) if ys else np.empty((len(idx), 0))
        for k, name in enumerate(c for c in want_cols if c in value_cols):
            out[name] = pa.array(y_all[k], type=vtype)
        full = timeseries_schema(
            precursors=len(value_cols) > 1,
            float32=vtype == pa.float32(),
            n_groups=len(value_cols) - 1,
        )
        return pa.table(out, schema=pa.schema([full.field(name) for name in out]))

    def metadata(self) -> dict[str, str]:
        with _h5py().File(self.path, "r") as f:
            raw = f.attrs.get("metadata", "{}")
        parsed: dict[str, str] = json.loads(raw)
        return parsed


def open_store(root: str | Path, options: StorageOptions | None = None) -> ResultStore:
    """Open a store. Without ``options`` the backend is detected from the files present."""
    root = Path(root)
    if options is None:
        if (root / "runs.parquet").exists():
            return ParquetStore(root)
        if (root / "results.h5").exists():
            return Hdf5Store(root)
        raise FileNotFoundError(f"no rksweep results (runs.parquet or results.h5) in {root}")
    if options.backend == "hdf5":
        return Hdf5Store(root, options)
    return ParquetStore(root, options)
