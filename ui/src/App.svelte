<script lang="ts">
  import { onMount } from 'svelte';
  import { fetchIndex, fetchSeries, parseRunsParam, serializeRunsParam } from './lib/data';
  import { shortId, formatValue } from './lib/format';
  import { MAX_SELECTED, type RunSummary, type SeriesState, type SweepIndex } from './lib/types';
  import RunTable from './lib/RunTable.svelte';
  import OverlayPlot, { type PlotItem } from './lib/OverlayPlot.svelte';
  import MetricDiff from './lib/MetricDiff.svelte';
  import ParamScatter from './lib/ParamScatter.svelte';

  let index = $state<SweepIndex | null>(null);
  let loadError = $state<string | null>(null);
  let loading = $state(true);
  let selected = $state<string[]>([]);
  let dropped = $state(0);
  let series = $state<Record<string, SeriesState>>({});

  const runs = $derived(index?.runs ?? []);
  const byId = $derived(new Map(runs.map((r) => [r.run_id, r])));

  /** Inputs that differ between runs, used to label legend entries. */
  const varyingKeys = $derived.by(() => {
    const keys = new Set<string>();
    for (const r of runs) for (const k of Object.keys(r.inputs)) keys.add(k);
    return [...keys].filter((k) => new Set(runs.map((r) => r.inputs[k])).size > 1);
  });

  const selectedRuns = $derived(
    selected.map((id) => byId.get(id)).filter((r): r is RunSummary => r !== undefined),
  );

  const items = $derived.by((): PlotItem[] => {
    const out: PlotItem[] = [];
    selectedRuns.forEach((r, slot) => {
      const s = series[r.run_id];
      if (s?.status !== 'ok') return;
      const inputs = varyingKeys.map((k) => `${k}=${formatValue(r.inputs[k])}`).join(', ');
      out.push({
        id: r.run_id,
        slot,
        label: `${shortId(r.run_id)}${inputs ? ` (${inputs})` : ''}`,
        data: s.data,
      });
    });
    return out;
  });
  const pending = $derived(selected.filter((id) => series[id]?.status === 'loading').length);
  const seriesErrors = $derived(
    selected.flatMap((id) => {
      const s = series[id];
      return s?.status === 'error' ? [`Run ${shortId(id)}: ${s.message}`] : [];
    }),
  );

  function applySelection(ids: string[]) {
    const valid = ids.filter((id) => byId.get(id)?.status === 'ok');
    dropped = ids.length - valid.length;
    selected = valid.slice(0, MAX_SELECTED);
  }

  function writeUrl(ids: string[]) {
    const url = window.location.pathname + serializeRunsParam(ids, window.location.search) + window.location.hash;
    window.history.replaceState(null, '', url);
  }

  function toggle(id: string) {
    const next = selected.includes(id)
      ? selected.filter((x) => x !== id)
      : selected.length < MAX_SELECTED
        ? [...selected, id]
        : selected;
    dropped = 0;
    selected = next;
    writeUrl(next);
  }

  function clear() {
    dropped = 0;
    selected = [];
    writeUrl([]);
  }

  async function load() {
    loading = true;
    loadError = null;
    try {
      index = await fetchIndex();
      applySelection(parseRunsParam(window.location.search));
      writeUrl(selected);
    } catch (e) {
      index = null;
      loadError = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void load();
    const onPop = () => {
      if (index) applySelection(parseRunsParam(window.location.search));
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  });

  // Fetch series for any newly selected run.
  $effect(() => {
    for (const id of selected) {
      if (series[id]?.status === 'ok' || series[id]?.status === 'loading') continue;
      series[id] = { status: 'loading' };
      fetchSeries(id).then(
        (data) => (series[id] = { status: 'ok', data }),
        (e: unknown) =>
          (series[id] = { status: 'error', message: e instanceof Error ? e.message : String(e) }),
      );
    }
  });
</script>

<p class="disclaimer" role="note">
  Educational point-kinetics model using textbook data, not a design or safety tool.
</p>

<main>
  <header>
    <h1>Reactor kinetics sweep: compare runs</h1>
    {#if index}
      <p class="muted meta">
        Sweep <strong>{index.sweep.name}</strong> &middot; {index.sweep.design} design &middot; input: {index.sweep.input}
        &middot; data set: {index.sweep.data_set} &middot; {runs.length} runs
        {#if index.sweep.git_sha}&middot; <code>{index.sweep.git_sha}</code>{/if}
      </p>
    {/if}
  </header>

  {#if loading}
    <p class="notice" role="status">Loading sweep data&hellip;</p>
  {:else if loadError}
    <div class="notice error" role="alert">
      <p><strong>Could not load the sweep.</strong> {loadError}</p>
      <button type="button" onclick={load}>Try again</button>
    </div>
  {:else if index}
    {#if dropped > 0}
      <p class="notice warn" role="status">
        {dropped} run id{dropped === 1 ? '' : 's'} in the link {dropped === 1 ? 'was' : 'were'} not found or
        failed, and {dropped === 1 ? 'was' : 'were'} ignored.
      </p>
    {/if}
    <RunTable {runs} {selected} ontoggle={toggle} onclear={clear} />
    <OverlayPlot {items} {pending} errors={seriesErrors} requested={selected.length} />
    <MetricDiff runs={selectedRuns} />
    <ParamScatter {runs} {selected} ontoggle={toggle} />
  {/if}
</main>

<style>
  .disclaimer {
    margin: 0;
    padding: 0.5rem 1rem;
    background: var(--warn-bg);
    color: var(--warn-text);
    font-size: 0.9rem;
    text-align: center;
  }
  main {
    max-width: 64rem;
    margin: 0 auto;
    padding: 1rem;
  }
  .meta {
    margin: 0;
    font-size: 0.9rem;
  }
  code {
    font-family: var(--mono);
  }
</style>
