<script lang="ts">
  import { orderedKeys } from './data';
  import { KNOWN_INPUTS, KNOWN_METRICS, formatValue, keyHeading, shortId } from './format';
  import { RAMP_CSS, ramp, runColorVar } from './colors';
  import { frac, makeAxis } from './scale';
  import { MAX_SELECTED, type RunSummary } from './types';

  interface Props {
    runs: RunSummary[];
    selected: string[];
    ontoggle: (id: string) => void;
  }
  let { runs, selected, ontoggle }: Props = $props();

  const okRuns = $derived(runs.filter((r) => r.status === 'ok'));
  const inputKeys = $derived(orderedKeys(okRuns, (r) => r.inputs, KNOWN_INPUTS));
  const metricKeys = $derived(orderedKeys(okRuns, (r) => r.metrics, KNOWN_METRICS));

  function distinct(key: string): number {
    return new Set(okRuns.map((r) => r.inputs[key])).size;
  }
  const varying = $derived(inputKeys.filter((k) => distinct(k) > 1));

  let xPick = $state<string>('');
  let yPick = $state<string>('');
  let cPick = $state<string | null>(null);
  let logXPick = $state<boolean | null>(null);
  let logY = $state(false);

  // Sensible defaults derived from the data until the user chooses.
  const xKey = $derived(inputKeys.includes(xPick) ? xPick : (varying[0] ?? inputKeys[0] ?? ''));
  const yKey = $derived(
    metricKeys.includes(yPick)
      ? yPick
      : metricKeys.includes('period_s')
        ? 'period_s'
        : (metricKeys[0] ?? ''),
  );
  const cKey = $derived(
    cPick !== null && (cPick === '' || inputKeys.includes(cPick))
      ? cPick
      : (varying.find((k) => k !== xKey) ?? ''),
  );

  interface Pt {
    id: string;
    x: number;
    y: number;
    c: number | null;
  }

  const allPts = $derived(
    okRuns.flatMap((r): Pt[] => {
      const x = r.inputs[xKey];
      const y = r.metrics[yKey];
      if (typeof x !== 'number' || typeof y !== 'number') return [];
      const c = cKey ? (r.inputs[cKey] ?? null) : null;
      return [{ id: r.run_id, x, y, c }];
    }),
  );

  function wide(vals: number[]): boolean {
    const pos = vals.filter((v) => v > 0);
    return pos.length === vals.length && vals.length > 0 && Math.max(...vals) / Math.min(...vals) >= 100;
  }
  const autoLogX = $derived(wide(allPts.map((p) => p.x)));
  const logX = $derived(logXPick ?? autoLogX);

  const pts = $derived(
    allPts.filter((p) => (!logX || p.x > 0) && (!logY || p.y > 0)),
  );
  const omitted = $derived(okRuns.length - pts.length);

  const W = 600;
  const H = 360;
  const M = { l: 64, r: 16, t: 12, b: 48 };

  const xAxis = $derived(pts.length ? makeAxis(pts.map((p) => p.x), logX) : null);
  const yAxis = $derived(pts.length ? makeAxis(pts.map((p) => p.y), logY) : null);

  const cVals = $derived(
    pts.map((p) => p.c).filter((c): c is number => typeof c === 'number'),
  );
  const cMin = $derived(cVals.length ? Math.min(...cVals) : 0);
  const cMax = $derived(cVals.length ? Math.max(...cVals) : 0);
  const cLog = $derived(cVals.length > 0 && wide(cVals));

  function colorOf(c: number | null): string {
    if (c === null || cMax === cMin) return 'var(--muted)';
    const t = cLog
      ? (Math.log10(c) - Math.log10(cMin)) / (Math.log10(cMax) - Math.log10(cMin))
      : (c - cMin) / (cMax - cMin);
    return ramp(t);
  }

  const px = (v: number) => M.l + frac(xAxis!, v) * (W - M.l - M.r);
  const py = (v: number) => H - M.b - frac(yAxis!, v) * (H - M.t - M.b);

  function toggle(id: string) {
    const isSel = selected.includes(id);
    if (!isSel && selected.length >= MAX_SELECTED) return;
    ontoggle(id);
  }

  function key(e: KeyboardEvent, id: string) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      toggle(id);
    }
  }
</script>

<section aria-labelledby="par-h">
  <h2 id="par-h">Parameter view</h2>
  {#if okRuns.length === 0 || inputKeys.length === 0 || metricKeys.length === 0}
    <p class="notice">No successful runs to plot.</p>
  {:else}
    <div class="controls">
      <label>
        X (input)
        <select value={xKey} onchange={(e) => (xPick = e.currentTarget.value)}>
          {#each inputKeys as k (k)}<option value={k}>{keyHeading(k)}</option>{/each}
        </select>
      </label>
      <label>
        Y (metric)
        <select value={yKey} onchange={(e) => (yPick = e.currentTarget.value)}>
          {#each metricKeys as k (k)}<option value={k}>{keyHeading(k)}</option>{/each}
        </select>
      </label>
      <label>
        Color by (input)
        <select value={cKey} onchange={(e) => (cPick = e.currentTarget.value)}>
          <option value="">None</option>
          {#each inputKeys as k (k)}<option value={k}>{keyHeading(k)}</option>{/each}
        </select>
      </label>
      <label class="check"><input type="checkbox" checked={logX} onchange={(e) => (logXPick = e.currentTarget.checked)} /> Log X</label>
      <label class="check"><input type="checkbox" bind:checked={logY} /> Log Y</label>
    </div>

    {#if pts.length === 0 || !xAxis || !yAxis}
      <p class="notice">No runs have values for both {keyHeading(xKey)} and {keyHeading(yKey)}.</p>
    {:else}
      <svg viewBox="0 0 {W} {H}" role="group" aria-label={`Scatter of ${keyHeading(yKey)} against ${keyHeading(xKey)}`}>
        {#each yAxis.ticks as t (t)}
          <line class="grid" x1={M.l} x2={W - M.r} y1={py(t)} y2={py(t)} />
          <text class="tick" x={M.l - 6} y={py(t)} text-anchor="end" dominant-baseline="middle">{formatValue(t)}</text>
        {/each}
        {#each xAxis.ticks as t (t)}
          <line class="grid" x1={px(t)} x2={px(t)} y1={M.t} y2={H - M.b} />
          <text class="tick" x={px(t)} y={H - M.b + 16} text-anchor="middle">{formatValue(t)}</text>
        {/each}
        <rect class="frame" x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} />
        <text class="label" x={(M.l + W - M.r) / 2} y={H - 6} text-anchor="middle">{keyHeading(xKey)}{logX ? ', log' : ''}</text>
        <text class="label" transform={`translate(14 ${(M.t + H - M.b) / 2}) rotate(-90)`} text-anchor="middle">{keyHeading(yKey)}{logY ? ', log' : ''}</text>

        {#each pts as p (p.id)}
          {@const slot = selected.indexOf(p.id)}
          {@const blocked = slot < 0 && selected.length >= MAX_SELECTED}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <g
            class="pt"
            class:blocked
            role="button"
            tabindex="0"
            aria-pressed={slot >= 0}
            aria-disabled={blocked}
            aria-label={`Run ${shortId(p.id)}: ${keyHeading(xKey)} ${formatValue(p.x)}, ${keyHeading(yKey)} ${formatValue(p.y)}${cKey ? `, ${keyHeading(cKey)} ${formatValue(p.c)}` : ''}`}
            onclick={() => toggle(p.id)}
            onkeydown={(e) => key(e, p.id)}
          >
            <title>{shortId(p.id)}: ({formatValue(p.x)}, {formatValue(p.y)}){cKey ? `, ${keyHeading(cKey)} ${formatValue(p.c)}` : ''}</title>
            {#if slot >= 0}
              <circle cx={px(p.x)} cy={py(p.y)} r="11" fill="none" stroke={runColorVar(slot)} stroke-width="3" />
            {/if}
            <circle class="dot" cx={px(p.x)} cy={py(p.y)} r="6.5" fill={colorOf(p.c)} />
          </g>
        {/each}
      </svg>

      {#if cKey}
        <div class="legend">
          <span>{keyHeading(cKey)}{cLog ? ' (log)' : ''}:</span>
          <span>{formatValue(cMin)}</span>
          <span class="bar" style:background={RAMP_CSS} aria-hidden="true"></span>
          <span>{formatValue(cMax)}</span>
        </div>
      {/if}
      <p class="muted small">
        Click or press Enter on a point to select that run (ringed points are selected).
        {#if omitted > 0}{omitted} run{omitted === 1 ? '' : 's'} not shown (missing or non-positive values).{/if}
      </p>
    {/if}
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
    align-items: end;
    margin-bottom: 0.5rem;
  }
  .controls label {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    font-size: 0.85rem;
  }
  .controls label.check {
    flex-direction: row;
    align-items: center;
    min-height: 2.25rem;
  }
  svg {
    width: 100%;
    max-width: 720px;
    height: auto;
    display: block;
  }
  .grid {
    stroke: var(--grid);
  }
  .frame {
    fill: none;
    stroke: var(--border);
  }
  .tick {
    fill: var(--muted);
    font-size: 11px;
  }
  .label {
    fill: var(--text);
    font-size: 12px;
  }
  .pt {
    cursor: pointer;
  }
  .pt.blocked {
    cursor: not-allowed;
    opacity: 0.6;
  }
  .dot {
    stroke: var(--bg);
    stroke-width: 1.5;
  }
  .pt:focus-visible {
    outline: none;
  }
  .pt:focus-visible .dot {
    stroke: var(--text);
    stroke-width: 3;
  }
  .legend {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.85rem;
    flex-wrap: wrap;
  }
  .bar {
    display: inline-block;
    width: 8rem;
    height: 0.8rem;
    border-radius: 3px;
    border: 1px solid var(--border);
  }
  .small {
    font-size: 0.85rem;
  }
</style>
