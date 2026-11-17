<script lang="ts">
  import { untrack } from 'svelte';
  import uPlot from 'uplot';
  import 'uplot/dist/uPlot.min.css';
  import { seriesForAxis } from './data';
  import { formatValue } from './format';
  import type { RunSeries } from './types';

  export interface PlotItem {
    id: string;
    label: string;
    slot: number;
    data: RunSeries;
  }

  interface Props {
    items: PlotItem[];
    pending: number;
    errors: string[];
    requested: number;
  }
  let { items, pending, errors, requested }: Props = $props();

  let logPower = $state(true);
  let logTime = $state(true);
  let host = $state<HTMLDivElement>();
  let themeTick = $state(0);
  let width = 600;
  let chart: uPlot | undefined;

  const heightFor = (w: number) => (w < 480 ? 260 : 340);

  $effect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const bump = () => themeTick++;
    mq.addEventListener('change', bump);
    return () => mq.removeEventListener('change', bump);
  });

  $effect(() => {
    if (!host) return;
    const el = host;
    const ro = new ResizeObserver((entries) => {
      width = Math.max(240, Math.floor(entries[0].contentRect.width));
      chart?.setSize({ width, height: heightFor(width) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  });

  $effect(() => {
    void themeTick;
    const its = items;
    const lp = logPower;
    const lt = logTime;
    const el = host;
    if (!el || its.length === 0) return;

    return untrack(() => {
      const css = getComputedStyle(el);
      const col = (name: string) => css.getPropertyValue(name).trim();
      const text = col('--text');
      const grid = col('--grid');
      const w = el.clientWidth || width;

      const tables = its.map((it) => {
        const s = seriesForAxis(it.data, lt);
        return [s.t, s.n] as uPlot.AlignedData;
      });
      const joined = uPlot.join(tables);
      const fmt = (_u: uPlot, v: number | null) => (v === null ? '—' : formatValue(v));
      const axis = (label: string, side: 0 | 1 | 2 | 3, log: boolean): uPlot.Axis => ({
        label,
        stroke: text,
        grid: { stroke: grid, width: 1 },
        ticks: { show: false },
        font: '12px system-ui, sans-serif',
        labelFont: '13px system-ui, sans-serif',
        side,
        values: (_u: uPlot, vals: (number | null)[]): (string | null)[] =>
          vals.map((v) =>
            v === null || (log && Math.abs(Math.log10(v) - Math.round(Math.log10(v))) > 1e-9)
              ? null
              : formatValue(v),
          ),
      });

      const opts: uPlot.Options = {
        width: w,
        height: heightFor(w),
        scales: {
          x: { time: false, distr: lt ? 3 : 1 },
          y: { distr: lp ? 3 : 1 },
        },
        axes: [axis('Time (s)', 2, lt), axis('n (relative)', 3, lp)],
        legend: { live: false },
        cursor: { drag: { x: true, y: false } },
        series: [
          { label: 't (s)', value: fmt },
          ...its.map(
            (it): uPlot.Series => ({
              label: it.label,
              stroke: col(`--run-${(it.slot % 4) + 1}`),
              width: 2,
              spanGaps: true,
              points: { show: false },
              value: fmt,
            }),
          ),
        ],
      };
      chart = new uPlot(opts, joined, el);
      return () => {
        chart?.destroy();
        chart = undefined;
      };
    });
  });
</script>

<section aria-labelledby="plot-h">
  <h2 id="plot-h">Power vs. time</h2>
  <div class="controls">
    <fieldset>
      <legend>Power axis</legend>
      <label><input type="radio" name="pscale" checked={logPower} onchange={() => (logPower = true)} /> Log</label>
      <label><input type="radio" name="pscale" checked={!logPower} onchange={() => (logPower = false)} /> Linear</label>
    </fieldset>
    <fieldset>
      <legend>Time axis</legend>
      <label><input type="radio" name="tscale" checked={logTime} onchange={() => (logTime = true)} /> Log</label>
      <label><input type="radio" name="tscale" checked={!logTime} onchange={() => (logTime = false)} /> Linear</label>
    </fieldset>
  </div>

  {#if requested === 0}
    <p class="notice">Select one or more runs in the table to plot n(t). Drag on the plot to zoom; double-click to reset.</p>
  {/if}
  {#if pending > 0}
    <p class="notice" role="status">Loading {pending} series&hellip;</p>
  {/if}
  {#each errors as msg (msg)}
    <p class="notice error" role="alert">{msg}</p>
  {/each}

  <div
    class="plot"
    bind:this={host}
    role="group"
    aria-label={`Chart: overlay of n(t) for ${items.length} selected runs. The summary values are in the comparison table below.`}
    hidden={items.length === 0}
  ></div>
  {#if items.length > 0 && logTime}
    <p class="muted small">The t = 0 sample is not shown on a log time axis.</p>
  {/if}
</section>

<style>
  section {
    margin: 1.5rem 0;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    margin-bottom: 0.5rem;
  }
  fieldset {
    display: flex;
    gap: 0.9rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    margin: 0;
    padding: 0.2rem 0.7rem 0.4rem;
  }
  label {
    display: inline-flex;
    gap: 0.3rem;
    align-items: center;
  }
  .plot {
    width: 100%;
    min-height: 1px;
  }
  .plot[hidden] {
    display: none;
  }
  .small {
    font-size: 0.85rem;
    margin: 0.25rem 0 0;
  }
  .plot :global(.u-legend) {
    text-align: left;
    font-size: 0.85rem;
  }
</style>
