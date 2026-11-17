<script lang="ts">
  import { compareNullable, filterRuns, orderedKeys, type RangeFilter } from './data';
  import {
    DASH,
    KNOWN_INPUTS,
    KNOWN_METRICS,
    formatValue,
    keyHeading,
    shortId,
  } from './format';
  import { runColorVar } from './colors';
  import { MAX_SELECTED, type RunSummary } from './types';

  interface Props {
    runs: RunSummary[];
    selected: string[];
    ontoggle: (id: string) => void;
    onclear: () => void;
  }
  let { runs, selected, ontoggle, onclear }: Props = $props();

  let sortKey = $state<string>('');
  let sortDir = $state<'asc' | 'desc'>('asc');
  let status = $state<'all' | 'ok' | 'failed'>('all');
  let bounds = $state<Record<string, { min: string; max: string }>>({});

  const inputKeys = $derived(orderedKeys(runs, (r) => r.inputs, KNOWN_INPUTS));
  const metricKeys = $derived(orderedKeys(runs, (r) => r.metrics, KNOWN_METRICS));

  function toNum(s: string | undefined): number | null {
    if (s === undefined || s.trim() === '') return null;
    const v = Number(s);
    return Number.isFinite(v) ? v : null;
  }

  const ranges = $derived.by(() => {
    const out: Record<string, RangeFilter> = {};
    for (const [k, b] of Object.entries(bounds)) {
      out[k] = { min: toNum(b.min), max: toNum(b.max) };
    }
    return out;
  });

  function value(r: RunSummary, key: string): number | string | null {
    if (key === 'run_id') return r.run_id;
    if (key === 'status') return r.status;
    if (key.startsWith('i:')) return r.inputs[key.slice(2)] ?? null;
    return r.metrics[key.slice(2)] ?? null;
  }

  const shown = $derived.by(() => {
    const rows = filterRuns(runs, status, ranges);
    if (!sortKey) return rows;
    return [...rows].sort((a, b) =>
      compareNullable(value(a, sortKey), value(b, sortKey), sortDir),
    );
  });

  const filtersActive = $derived(
    status !== 'all' || Object.values(ranges).some((r) => r.min !== null || r.max !== null),
  );

  function sortBy(key: string) {
    if (sortKey === key) {
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      sortKey = key;
      sortDir = 'asc';
    }
  }

  function resetFilters() {
    status = 'all';
    bounds = {};
  }

  function ariaSort(key: string): 'ascending' | 'descending' | 'none' {
    return sortKey !== key ? 'none' : sortDir === 'asc' ? 'ascending' : 'descending';
  }

  const full = $derived(selected.length >= MAX_SELECTED);
</script>

<section aria-labelledby="runs-h">
  <h2 id="runs-h">Runs</h2>

  <details class="filters" open={filtersActive}>
    <summary>Filters{filtersActive ? ' (active)' : ''}</summary>
    <div class="filter-grid">
      <label>
        Status
        <select bind:value={status}>
          <option value="all">All</option>
          <option value="ok">OK</option>
          <option value="failed">Failed</option>
        </select>
      </label>
      {#each inputKeys as key (key)}
        <fieldset>
          <legend>{keyHeading(key)}</legend>
          <label>
            <span class="sr-only">{keyHeading(key)} minimum</span>
            <input
              type="number"
              step="any"
              placeholder="min"
              value={bounds[key]?.min ?? ''}
              oninput={(e) =>
                (bounds[key] = { min: e.currentTarget.value, max: bounds[key]?.max ?? '' })}
            />
          </label>
          <span aria-hidden="true">to</span>
          <label>
            <span class="sr-only">{keyHeading(key)} maximum</span>
            <input
              type="number"
              step="any"
              placeholder="max"
              value={bounds[key]?.max ?? ''}
              oninput={(e) =>
                (bounds[key] = { min: bounds[key]?.min ?? '', max: e.currentTarget.value })}
            />
          </label>
        </fieldset>
      {/each}
      <button type="button" onclick={resetFilters} disabled={!filtersActive}>Reset filters</button>
    </div>
  </details>

  <p class="status muted" aria-live="polite">
    Showing {shown.length} of {runs.length} runs.
    {selected.length} selected (max {MAX_SELECTED}){full ? '; deselect one to pick another' : ''}.
    <button type="button" onclick={onclear} disabled={selected.length === 0}>Clear selection</button>
  </p>

  {#if runs.length === 0}
    <p class="notice">This sweep has no runs.</p>
  {:else if shown.length === 0}
    <p class="notice">
      No runs match the filters. <button type="button" onclick={resetFilters}>Reset filters</button>
    </p>
  {:else}
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
    <div class="scroll-x table-wrap" role="region" aria-labelledby="runs-h" tabindex="0">
      <table>
        <caption class="sr-only">
          Sweep runs. Use the checkboxes to select up to {MAX_SELECTED} runs to compare. Column headers
          are buttons that sort.
        </caption>
        <thead>
          <tr>
            <th scope="col"><span class="sr-only">Select</span></th>
            {#each [['run_id', 'Run'], ['status', 'Status']] as [k, label] (k)}
              <th scope="col" aria-sort={ariaSort(k)}>
                <button type="button" class="sort" onclick={() => sortBy(k)}>
                  {label}<span aria-hidden="true">{sortKey === k ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}</span>
                </button>
              </th>
            {/each}
            {#each inputKeys as key (key)}
              <th scope="col" class="num" aria-sort={ariaSort('i:' + key)}>
                <button type="button" class="sort" onclick={() => sortBy('i:' + key)}>
                  {keyHeading(key)}<span aria-hidden="true">{sortKey === 'i:' + key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}</span>
                </button>
              </th>
            {/each}
            {#each metricKeys as key (key)}
              <th scope="col" class="num metric" aria-sort={ariaSort('m:' + key)}>
                <button type="button" class="sort" onclick={() => sortBy('m:' + key)}>
                  {keyHeading(key)}<span aria-hidden="true">{sortKey === 'm:' + key ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}</span>
                </button>
              </th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each shown as r (r.run_id)}
            {@const slot = selected.indexOf(r.run_id)}
            {@const isSel = slot >= 0}
            <tr class:selected={isSel} class:failed={r.status === 'failed'}>
              <td>
                <input
                  type="checkbox"
                  checked={isSel}
                  disabled={r.status === 'failed' || (!isSel && full)}
                  aria-label={`Select run ${r.run_id}`}
                  onchange={() => ontoggle(r.run_id)}
                />
              </td>
              <td>
                {#if isSel}<span class="swatch" style:background={runColorVar(slot)}></span>{/if}
                <code title={r.run_id}>{shortId(r.run_id)}</code>
              </td>
              <td>
                {#if r.status === 'failed'}
                  <span class="badge failed">failed</span>
                  {#if r.error}<div class="err" title={r.error}>{r.error}</div>{/if}
                {:else}
                  <span class="badge">ok</span>
                {/if}
              </td>
              {#each inputKeys as key (key)}
                <td class="num">{formatValue(r.inputs[key])}</td>
              {/each}
              {#each metricKeys as key (key)}
                <td class="num">{r.status === 'failed' ? DASH : formatValue(r.metrics[key])}</td>
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>

<style>
  section {
    margin: 1.5rem 0;
  }
  .filters {
    margin-bottom: 0.5rem;
  }
  summary {
    cursor: pointer;
    padding: 0.25rem 0;
  }
  .filter-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    align-items: end;
    padding: 0.5rem 0;
  }
  .filter-grid label {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    font-size: 0.85rem;
  }
  fieldset {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.35rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    margin: 0;
    padding: 0.25rem 0.5rem 0.5rem;
    font-size: 0.85rem;
  }
  fieldset input {
    width: 6rem;
  }
  .status {
    font-size: 0.9rem;
  }
  .table-wrap {
    max-height: 26rem;
    overflow-y: auto;
  }
  .sort {
    background: none;
    border: none;
    padding: 0;
    min-height: 0;
    font-weight: 600;
    text-align: inherit;
  }
  tr.selected {
    background: color-mix(in srgb, var(--accent) 14%, transparent);
  }
  tr.failed {
    color: var(--muted);
  }
  input[type='checkbox'] {
    width: 1.25rem;
    height: 1.25rem;
  }
  code {
    font-family: var(--mono);
  }
  .badge {
    font-size: 0.75rem;
    padding: 0.05rem 0.4rem;
    border-radius: 999px;
    border: 1px solid var(--border);
  }
  .badge.failed {
    background: var(--err-bg);
    color: var(--err-text);
    border-color: transparent;
  }
  .err {
    font-size: 0.8rem;
    max-width: 16rem;
    white-space: normal;
  }
</style>
