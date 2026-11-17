# 03 — Storing results: Parquet (and HDF5)

## Goal

Store sweep results so they are small, fast to query, and self-describing. Then **measure**
the choices instead of asserting them. The measured table in the README is the deliverable.

## Two kinds of data, two tables

| Table | One row per | Columns | Size |
|---|---|---|---|
| `runs` | run | `run_id`, `sweep_id`, all inputs, all metrics, `status`, `error`, `git_sha`, versions, `created_at` | Small (hundreds of rows) |
| `timeseries` | run × time point | `run_id`, `t`, `n`, optionally `C1`…`C6` | Large (runs × ~400 points × columns) |

Keep them separate. Most questions ("which runs peaked above 10?") only touch `runs`.

## Parquet with PyArrow

- Build Arrow tables with an explicit `pa.schema(...)`. Do not let types be inferred: `float64`
  for physics values, `string` for IDs, `timestamp("us", tz="UTC")` for `created_at`.
- Write with `pyarrow.parquet.write_table(table, path, compression="zstd")`.
- Put metadata in the file: `schema.with_metadata({"data_set": ..., "units": ..., "model": ...})`.
  Units belong in the file, not only in the README.
- Read a filtered subset with `pyarrow.dataset.dataset(path).to_table(filter=pc.field("rho_dollars") > 0.5)`,
  or in SQL with DuckDB: `duckdb.sql("SELECT run_id, n_max FROM 'results/runs.parquet' WHERE n_max > 10")`.

## Layout choices to compare

For `timeseries`:

1. **One file per sweep**, rows sorted by `run_id, t`, with a sensible row-group size
   (for example 64k rows). Parquet keeps min/max stats per row group, so a filter on `run_id`
   can skip most of the file.
2. **Hive-partitioned by run**: `pyarrow.dataset.write_dataset(table, "results/ts",
   format="parquet", partitioning=["run_id"], partitioning_flavor="hive")` creates
   `run_id=…/part-0.parquet`. Reading one run is trivial, but hundreds of tiny files is a known
   anti-pattern. Measure it.
3. **Precision:** `float64` vs `float32` for `n` and `C_i`. Check the round-trip error stays far
   below your solver tolerance before you claim `float32` is acceptable.
4. **Compression:** none vs `snappy` vs `zstd`.
5. **Baseline:** CSV, so the comparison has an obvious reference point.
6. **Optional, HDF5** with `h5py`: one group per run, chunked datasets, `compression="gzip"`.
   HDF5 is common in scientific computing, so knowing both is useful. Keep the reader in the
   same `storage.py` module behind one interface.

Also store **less**: the output grid is already log-spaced (01). Saving precursor columns is
optional. Make it a flag and measure the difference.

## Benchmark (`benchmarks/storage.py`)

For the full sweep, measure for each option:

| Measure | How |
|---|---|
| Bytes on disk | Sum of file sizes |
| Write time | `time.perf_counter()` around the write, best of 3 |
| Read all | Time to read everything into memory |
| Read one run | Time to load the series for one `run_id` |
| Filtered query | Time for "runs with `rho_dollars > 0.5`, their `n` at t = 100 s" |

Write the table into the README with the machine, the number of runs and the date.
**Only report numbers you measured.** If zstd + float32 is 8× smaller than CSV on your data,
say "8× smaller on this data set", not "8× smaller" in general.

## Integrity

- Each `runs` row should point to series that exist. Write a test that every successful
  `run_id` in `runs` has a non-empty series.
- Writes go to a temp path, then `os.replace` into place, so a crash never leaves half a file.
- Re-running a sweep skips run IDs already stored (see 02), and the test suite proves it.

## Tests

- Round trip: write then read a small sweep; arrays are equal (float64) or within a stated
  tolerance (float32).
- The schema on disk matches the declared schema, including metadata keys.
- The filtered read returns exactly the expected run IDs.

## Done when

`results/runs.parquet` and `results/timeseries.parquet` exist after a sweep, the storage
benchmark table is in the README, and one DuckDB query example is shown there.
