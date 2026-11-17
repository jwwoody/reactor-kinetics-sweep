# 05 — Compare UI: Svelte + TypeScript

## Goal

A static web page where someone can pick runs from a sweep, overlay their power curves, and
see how their inputs and metrics differ. No server: CI exports JSON, and the page is published
to GitHub Pages and GitLab Pages.

## Setup

```bash
npm create vite@latest ui -- --template svelte-ts
cd ui && npm install && npm install uplot
```

That gives Svelte 5 with TypeScript. Use runes (`$state`, `$derived`, `$props`) for state. A
single-page Vite app is enough. SvelteKit is not needed.

For GitHub Pages, set `base: "/reactor-kinetics-sweep/"` in `vite.config.ts` (the repo name).
GitLab Pages uses the same path for a project site.

## Data contract (written by `rksweep export`)

Keep the Python side and the UI side agreeing through one documented format:

```text
ui/public/data/
├── index.json          # one entry per run: run_id, inputs, metrics, status
└── runs/
    └── <run_id>.json   # { "t": [...], "n": [...] }, downsampled to about 300 points
```

- Define the types once in `ui/src/lib/types.ts` (`RunSummary`, `RunSeries`) and match them
  to the Parquet schema in 03.
- Downsample on the Python side, keeping the log-spaced points plus the peak. The browser
  should never load the full time series.
- Add `"schema_version": 1` to `index.json` and check it in the UI, so a format change fails
  clearly instead of drawing nonsense.

## Screens and features

1. **Run table:** sortable columns for inputs and metrics. Filter by input range
   (for example `rho_dollars` 0.3-0.6) and by status. A checkbox per row selects it, up to 4.
2. **Overlay plot:** `n(t)` for the selected runs, log-scale time axis and log-scale power
   axis (uPlot `distr: 3`), one color per run, with a legend showing each run's key inputs.
   Toggle between log and linear power.
3. **Metric comparison:** for 2+ selected runs, a table of each metric side by side, with the
   difference from the first selected run in absolute and percent terms.
4. **Parameter view:** a scatter of one input against one metric over all runs (for example
   `rho_dollars` vs `period_s`, colored by `gen_time_s`). This is where the sweep's design of
   experiments becomes visible.
5. **Shareable state:** keep the selected run IDs in the URL query string
   (`?runs=4f1a…,9c2e…`) so a comparison can be linked.

## Component sketch

```text
src/
├── App.svelte                 # loads index.json, holds selection state
├── lib/
│   ├── types.ts
│   ├── data.ts                # fetch + cache run series, validate schema_version
│   ├── RunTable.svelte
│   ├── OverlayPlot.svelte     # wraps uPlot; recreates the chart when selection changes
│   ├── MetricDiff.svelte
│   └── ParamScatter.svelte
```

Wrapping uPlot: create the chart in `$effect` on mount, call `chart.setData` when the selected
series change, and `chart.destroy()` in the cleanup. Use a `ResizeObserver` to keep the width
right.

## Quality

- `npm run check` (svelte-check) runs in CI with zero errors.
- A couple of Vitest unit tests for the pure functions in `data.ts` (the percent-diff helper,
  the schema check).
- Readable at phone width. Keyboard accessible table and checkboxes. Colors that also work for
  red-green color blindness (use a palette like Okabe-Ito).
- Empty, loading and error states. A failed fetch shows a message, never a blank page.

## Done when

The Pages link loads the smoke sweep, two runs can be selected and overlaid, the metric table
shows their differences, and a copied URL reopens the same comparison. Put a screenshot of the
overlay plot at the top of the README.
