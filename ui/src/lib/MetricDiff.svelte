<script lang="ts">
  import { absDiff, orderedKeys, pctDiff } from './data';
  import {
    DASH,
    KNOWN_INPUTS,
    KNOWN_METRICS,
    formatPercent,
    formatSigned,
    formatValue,
    keyHeading,
    shortId,
  } from './format';
  import { runColorVar } from './colors';
  import type { RunSummary } from './types';

  interface Props {
    /** Selected runs in selection order; the first is the baseline. */
    runs: RunSummary[];
  }
  let { runs }: Props = $props();

  const inputKeys = $derived(orderedKeys(runs, (r) => r.inputs, KNOWN_INPUTS));
  const metricKeys = $derived(orderedKeys(runs, (r) => r.metrics, KNOWN_METRICS));

  function inputOf(r: RunSummary, k: string): number | null {
    return r.inputs[k] ?? null;
  }
  function metricOf(r: RunSummary, k: string): number | null {
    return r.metrics[k] ?? null;
  }
</script>

<section aria-labelledby="cmp-h">
  <h2 id="cmp-h">Comparison</h2>
  {#if runs.length < 2}
    <p class="notice">Select two or more runs to see how their inputs and metrics differ.</p>
  {:else}
    <p class="muted small">
      Differences are relative to the first selected run
      (<span class="swatch" style:background={runColorVar(0)}></span><code>{shortId(runs[0].run_id)}</code>):
      absolute difference, then percent of that run's value.
    </p>
    <div class="scroll-x">
      <table>
        <caption class="sr-only">Inputs and metrics of the selected runs, with differences from the first run.</caption>
        <thead>
          <tr>
            <th scope="col">Quantity</th>
            {#each runs as r, i (r.run_id)}
              <th scope="col" class="num">
                <span class="swatch" style:background={runColorVar(i)}></span><code>{shortId(r.run_id)}</code>
                {#if i === 0}<span class="muted">(base)</span>{/if}
              </th>
            {/each}
          </tr>
        </thead>
        {#each [{ title: 'Inputs', keys: inputKeys, get: inputOf }, { title: 'Metrics', keys: metricKeys, get: metricOf }] as group (group.title)}
          <tbody>
            <tr class="group"><th scope="rowgroup" colspan={runs.length + 1}>{group.title}</th></tr>
            {#each group.keys as key (key)}
              {@const base = group.get(runs[0], key)}
              <tr>
                <th scope="row">{keyHeading(key)}</th>
                {#each runs as r, i (r.run_id)}
                  {@const v = group.get(r, key)}
                  <td class="num">
                    <div>{r.status === 'failed' ? DASH : formatValue(v)}</div>
                    {#if i > 0}
                      {@const d = absDiff(base, v)}
                      {@const p = pctDiff(base, v)}
                      <div class="diff" class:pos={d !== null && d > 0} class:neg={d !== null && d < 0}>
                        <span class="sr-only">difference </span>{formatSigned(d)}
                        <span class="pct">({formatPercent(p)})</span>
                      </div>
                    {/if}
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        {/each}
      </table>
    </div>
  {/if}
</section>

<style>
  section {
    margin: 1.5rem 0;
  }
  .small {
    font-size: 0.85rem;
  }
  .diff {
    font-size: 0.8rem;
    color: var(--muted);
  }
  .group th {
    background: var(--surface);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  th[scope='row'] {
    font-weight: 500;
    white-space: normal;
    min-width: 9rem;
  }
  code {
    font-family: var(--mono);
  }
</style>
