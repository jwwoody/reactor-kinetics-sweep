# 00 — Overview and plan

## What this project is

A small Python package that simulates how a reactor's power changes over time after a
change in reactivity, runs that simulation across many parameter combinations, stores the
results efficiently, checks itself in CI on every push, and shows the results in a web page
where runs can be compared side by side.

It is an educational model. It uses textbook equations and textbook data. It is not a design
or safety tool, and the README should say so in its first paragraph.

## The five parts

1. **Physics kernel** — solve the point-kinetics equations (one neutron population plus six
   delayed-neutron precursor groups) with NumPy/SciPy, and prove the solver right against
   known answers.
2. **Parameter sweeps** — run the kernel over a grid or a Latin-hypercube sample of inputs
   (reactivity, generation time, ramp rate, feedback strength) and compute summary metrics.
3. **Storage** — write run summaries and time series to Parquet, compare formats and layouts,
   and record the measured results in the README.
4. **CI** — lint, type-check, test, and run a small sweep on every push, on both GitHub
   Actions and GitLab CI, and publish the UI to Pages.
5. **Compare UI** — a static Svelte + TypeScript page: a run table, a log-scale power plot
   that overlays selected runs, and a metric comparison.

## Repo layout

```text
reactor-kinetics-sweep/
├── README.md
├── LICENSE                     # MIT
├── pyproject.toml
├── src/rksweep/
│   ├── __init__.py
│   ├── constants.py            # delayed-neutron data sets
│   ├── reactivity.py           # step, ramp, sine, feedback inputs
│   ├── kinetics.py             # the ODE right-hand side, Jacobian, solve()
│   ├── reference.py            # exact/analytic answers used by tests (expm, inhour)
│   ├── metrics.py              # peak, time-to-peak, period, energy
│   ├── sweep.py                # grid + LHS designs, parallel runner
│   ├── storage.py              # Parquet / HDF5 writers and readers
│   ├── export.py               # JSON export for the UI
│   └── cli.py                  # `rksweep run`, `rksweep sweep`, `rksweep export`
├── configs/
│   ├── smoke.toml              # ~10 runs, used in CI
│   └── full.toml               # a few hundred runs, run locally
├── tests/
├── benchmarks/                 # storage and solver benchmarks
├── ui/                         # Svelte + Vite app
├── docs/                       # copies of these guide files
├── .github/workflows/ci.yml
└── .gitlab-ci.yml
```

## Tools

| Purpose | Choice | Why |
|---|---|---|
| Python | 3.12 | Current, well supported by SciPy and PyArrow |
| Environment | `uv` (or plain `venv` + `pip`) | Fast installs, lockfile, easy in CI |
| Numerics | NumPy, SciPy | `solve_ivp`, `expm`, `brentq`, `qmc` |
| Storage | PyArrow (Parquet); h5py (optional HDF5) | Columnar storage, compression, predicate pushdown |
| Querying (optional) | DuckDB | SQL straight over Parquet files |
| CLI | `argparse` or Typer | Keep it simple |
| Tests | pytest, Hypothesis | Example tests plus property tests |
| Lint/types | Ruff, mypy | One fast linter, strict types on the kernel |
| UI | Svelte 5 + Vite + TypeScript, uPlot | Small, fast, log-scale plots |

## Seven-day schedule (about 15-20 hours)

| Day | Work | Done when |
|---|---|---|
| 1 | Repo setup, constants, `kinetics.solve()` for a step input | A $0.10 step runs and plots |
| 2 | Verification tests (steady state, prompt jump, `expm`, inhour period) | All tests pass; solver choice measured |
| 3 | Reactivity inputs, metrics, sweep runner, configs | `rksweep sweep configs/smoke.toml` writes results |
| 4 | Parquet storage, layout comparison, benchmark table | README has measured numbers |
| 5 | GitHub Actions + GitLab CI | Both pipelines green on a push |
| 6 | Svelte UI: table, overlay plot, metric diff | Runs locally against exported JSON |
| 7 | Pages deploy, README, screenshots, demo script | Public link works |

If a day runs long, cut scope from the end (feedback model, HDF5, Sobol sensitivity), never
from the verification tests. The tests are what make the physics credible.

## Definition of done

- `pip install -e .` and `rksweep sweep configs/smoke.toml` work on a clean machine.
- The README opens with what it is, the "educational model" disclaimer, a screenshot, and the
  Pages link.
- Every physics claim in the README is backed by a test (see 01).
- The storage section shows a real table of sizes and read times you measured (see 03).
- CI is green on both GitHub and GitLab, and the badge is in the README.
- Every line of `kinetics.py` and `reference.py` is documented well enough to explain
  without notes.

## Repo hygiene

- Commit with your GitHub noreply address, never a work address:
  `git config user.email "<id>+jwwoody@users.noreply.github.com"` inside this repo.
- MIT license. Cite every data source (Keepin, Duderstadt & Hamilton) in the README.
- Small commits with clear messages. A reviewer will read the history.

## Reading list

- Duderstadt & Hamilton, *Nuclear Reactor Analysis* (1976), ch. 6 "Nuclear Reactor Kinetics".
- Lamarsh & Baratta, *Introduction to Nuclear Engineering*, the reactor kinetics chapter.
- Keepin, *Physics of Nuclear Kinetics* (1965), the source of the six-group data.
- Hetrick, *Dynamics of Nuclear Reactors* (1971), for feedback models.
- SciPy docs: `scipy.integrate.solve_ivp` (the "stiff problems" notes), `scipy.linalg.expm`,
  `scipy.stats.qmc`.
