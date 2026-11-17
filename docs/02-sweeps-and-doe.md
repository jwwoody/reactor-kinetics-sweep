# 02 — Parameter sweeps and design of experiments

## Goal

Run the kernel over many input combinations, reproducibly, and produce one summary row per run
plus its time series. "Design of experiments" means choosing *which* combinations to run so a
small number of runs tells you the most.

## Inputs worth sweeping

| Parameter | Range | Scale | Why it matters |
|---|---|---|---|
| `rho_dollars` (step size) | 0.05 – 0.9 | linear | Main driver of period and peak |
| `gen_time_s` (Λ) | 1e-7 – 1e-4 | **log** | Separates fast-spectrum from thermal behavior |
| `ramp_rate_dps` (dollars/s, ramp input) | 0.01 – 0.5 | log | Rod-withdrawal speed |
| `alpha_fb` (feedback, stretch) | −5e-5 – −5e-6 per K | linear | How strongly the reactor limits itself |

Always convert dollars to ρ in one place: `rho = dollars * data.beta`.

## Config file (`configs/*.toml`)

```toml
[sweep]
name = "smoke"
design = "grid"            # "grid" or "lhs"
seed = 42                  # used by lhs
t_end = 200.0
input = "step"             # "step" | "ramp" | "sine" | "step_feedback"
data_set = "KEEPIN_U235_THERMAL"

[params.rho_dollars]
values = [0.1, 0.3, 0.5]

[params.gen_time_s]
values = [1e-6, 1e-5, 1e-4]
```

For LHS, give bounds and a scale instead of values:

```toml
[params.gen_time_s]
low = 1e-7
high = 1e-4
scale = "log"

[sweep]
design = "lhs"
n_samples = 200
```

Read TOML with the standard library (`tomllib`). Validate it into a dataclass and fail loudly on
unknown keys.

## Designs

1. **Full grid (factorial):** `itertools.product` over the value lists. Easy to read, but it
   grows fast: 3 parameters × 10 values = 1,000 runs.
2. **Latin hypercube (LHS):** `scipy.stats.qmc.LatinHypercube(d=k, seed=seed)` gives `n`
   points in `[0,1)^k` that cover each dimension evenly. Map them to your ranges with
   `qmc.scale` for linear parameters, or `10 ** (log_low + u·(log_high − log_low))` for log
   parameters. For the same number of runs, LHS usually covers the space better than a coarse
   grid.
3. **Stretch, sensitivity analysis:** with an LHS sample, compute rank correlations
   (`scipy.stats.spearmanr`) between each input and `period_s` / `n_max`. That answers "which
   input matters most?" in one table. The SALib package does full Sobol indices if you want
   to go further. Optional.

Write one sentence in the README on why you chose LHS for the big sweep, with a coverage
comparison plot: grid points vs LHS points for two parameters.

## Run identity and reproducibility

- `run_id` = the first 12 hex characters of a SHA-256 hash of the canonical JSON of that run's
  inputs (sorted keys) plus the data-set name. The same inputs always give the same ID, so
  re-running a sweep skips work that is already stored.
- `sweep_id` = name + UTC timestamp.
- Store on every row: `git_sha` (from `git rev-parse --short HEAD`, or the CI variable),
  package version, solver method, tolerances, Python/NumPy/SciPy versions.

## Running in parallel

Each run is independent, so use `concurrent.futures.ProcessPoolExecutor`:

- Keep the worker function top-level and picklable: `run_one(params: dict) -> RunResult`.
- Default `max_workers` to `os.cpu_count()`. CI runners have 2-4 cores.
- Catch exceptions per run and record `status = "failed"` with the error message. One bad
  combination must not kill the whole sweep.
- Show progress on stderr: `[37/200] run 4f1a…  0.21 s`.

Measure the speedup from 1 to N workers and add it to the README. It will be less than linear,
because of process start-up and result transfer. Say so.

## CLI

```text
rksweep run    --rho-dollars 0.3 --gen-time 1e-5 --t-end 200          # one run, prints metrics
rksweep sweep  configs/smoke.toml --out results/                       # runs + stores
rksweep export results/ --out ui/public/data/                          # JSON for the UI
```

## Tests

- The grid design produces exactly the product of value counts.
- LHS with the same seed gives the same points, and every point is inside its bounds.
- Log-scale mapping puts the median of many samples near the geometric mean of the bounds.
- `run_id` is stable across runs and changes when any input changes.
- A sweep with one deliberately invalid run (ρ ≥ β) finishes and marks just that run failed.

## Done when

`rksweep sweep configs/smoke.toml` finishes in under a minute on a laptop, and
`configs/full.toml` (a few hundred LHS runs) finishes in a few minutes, with the results in
`results/`.
