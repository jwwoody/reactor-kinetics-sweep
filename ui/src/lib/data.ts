import {
  MAX_SELECTED,
  SCHEMA_VERSION,
  type RunSeries,
  type RunSummary,
  type SweepIndex,
} from './types';

export class SchemaError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SchemaError';
  }
}

function isRecord(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x);
}

/** Validate the parsed index.json. Throws SchemaError with a readable message. */
export function checkIndex(raw: unknown): SweepIndex {
  if (!isRecord(raw)) throw new SchemaError('index.json is not a JSON object.');
  if (raw.schema_version !== SCHEMA_VERSION) {
    throw new SchemaError(
      `Unsupported data format: schema_version is ${JSON.stringify(raw.schema_version)} ` +
        `but this page understands version ${SCHEMA_VERSION}. ` +
        `Re-export the sweep and rebuild the page with a matching version.`,
    );
  }
  if (!isRecord(raw.sweep)) throw new SchemaError('index.json has no "sweep" object.');
  if (!Array.isArray(raw.runs)) throw new SchemaError('index.json has no "runs" array.');
  for (const [i, r] of raw.runs.entries()) {
    if (!isRecord(r) || typeof r.run_id !== 'string' || !r.run_id) {
      throw new SchemaError(`runs[${i}] has no run_id.`);
    }
    if (r.status !== 'ok' && r.status !== 'failed') {
      throw new SchemaError(`Run ${r.run_id} has an unknown status.`);
    }
    if (!isRecord(r.inputs) || !isRecord(r.metrics)) {
      throw new SchemaError(`Run ${r.run_id} is missing inputs or metrics.`);
    }
  }
  return raw as unknown as SweepIndex;
}

/** Validate a per-run series file. */
export function checkSeries(raw: unknown, runId: string): RunSeries {
  if (!isRecord(raw) || !Array.isArray(raw.t) || !Array.isArray(raw.n)) {
    throw new SchemaError(`Series for ${runId} must have "t" and "n" arrays.`);
  }
  if (raw.t.length !== raw.n.length || raw.t.length === 0) {
    throw new SchemaError(`Series for ${runId} has mismatched or empty arrays.`);
  }
  return { run_id: runId, t: raw.t as number[], n: raw.n as number[] };
}

/** Difference (b - a); null when either side is missing. */
export function absDiff(base: number | null, value: number | null): number | null {
  if (base === null || value === null) return null;
  return value - base;
}

/** Percent difference of value relative to base; null if undefined (null input or zero base). */
export function pctDiff(base: number | null, value: number | null): number | null {
  if (base === null || value === null) return null;
  if (base === 0) return value === 0 ? 0 : null;
  return ((value - base) / Math.abs(base)) * 100;
}

/** Comparator for sorting that always puts null (and NaN) last, regardless of direction. */
export function compareNullable(
  a: number | string | null,
  b: number | string | null,
  dir: 'asc' | 'desc' = 'asc',
): number {
  const aNull = a === null || (typeof a === 'number' && Number.isNaN(a));
  const bNull = b === null || (typeof b === 'number' && Number.isNaN(b));
  if (aNull && bNull) return 0;
  if (aNull) return 1;
  if (bNull) return -1;
  const c = a! < b! ? -1 : a! > b! ? 1 : 0;
  return dir === 'asc' ? c : -c;
}

/** Parse the selected run ids from a query string ("?runs=a,b"). Dedupes and caps at max. */
export function parseRunsParam(search: string, max: number = MAX_SELECTED): string[] {
  const raw = new URLSearchParams(search).get('runs');
  if (!raw) return [];
  const seen = new Set<string>();
  for (const part of raw.split(',')) {
    const id = part.trim();
    if (id) seen.add(id);
  }
  return [...seen].slice(0, max);
}

/**
 * Return a query string (with leading "?", or "" if empty) that carries the given run ids in
 * `runs`, leaving any other parameters untouched. Commas are kept literal for readable links.
 */
export function serializeRunsParam(ids: string[], currentSearch = ''): string {
  const others = new URLSearchParams(currentSearch);
  others.delete('runs');
  const parts: string[] = [];
  const rest = others.toString();
  if (rest) parts.push(rest);
  if (ids.length) parts.push('runs=' + ids.map(encodeURIComponent).join(','));
  return parts.length ? '?' + parts.join('&') : '';
}

/** Points usable on a log time axis: t must be > 0 (the t = 0 sample is dropped). */
export function seriesForAxis(
  series: RunSeries,
  logTime: boolean,
): { t: number[]; n: number[] } {
  const t: number[] = [];
  const n: number[] = [];
  for (let i = 0; i < series.t.length; i++) {
    if (logTime && !(series.t[i] > 0)) continue;
    t.push(series.t[i]);
    n.push(series.n[i]);
  }
  return { t, n };
}

/** Union of keys across runs, known keys first in the given order, the rest alphabetical. */
export function orderedKeys(
  runs: RunSummary[],
  pick: (r: RunSummary) => Record<string, unknown>,
  known: readonly string[],
): string[] {
  const all = new Set<string>();
  for (const r of runs) for (const k of Object.keys(pick(r))) all.add(k);
  const first = known.filter((k) => all.has(k));
  const rest = [...all].filter((k) => !known.includes(k)).sort();
  return [...first, ...rest];
}

export interface RangeFilter {
  min: number | null;
  max: number | null;
}

/** Apply status and per-input range filters. Missing bounds are open. */
export function filterRuns(
  runs: RunSummary[],
  status: 'all' | 'ok' | 'failed',
  ranges: Record<string, RangeFilter>,
): RunSummary[] {
  return runs.filter((r) => {
    if (status !== 'all' && r.status !== status) return false;
    for (const [key, { min, max }] of Object.entries(ranges)) {
      const v = r.inputs[key];
      if (min !== null && !(typeof v === 'number' && v >= min)) return false;
      if (max !== null && !(typeof v === 'number' && v <= max)) return false;
    }
    return true;
  });
}

function dataUrl(path: string): string {
  return `${import.meta.env.BASE_URL}data/${path}`;
}

async function getJson(path: string): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(dataUrl(path));
  } catch (e) {
    throw new Error(`Could not reach ${path} (${e instanceof Error ? e.message : 'network error'}).`);
  }
  if (!res.ok) throw new Error(`Could not load ${path}: HTTP ${res.status}.`);
  try {
    return await res.json();
  } catch {
    throw new Error(`${path} is not valid JSON.`);
  }
}

export async function fetchIndex(): Promise<SweepIndex> {
  return checkIndex(await getJson('index.json'));
}

const seriesCache = new Map<string, Promise<RunSeries>>();

/** Fetch one run's series; successful results are cached, failures are retried next time. */
export function fetchSeries(runId: string): Promise<RunSeries> {
  let p = seriesCache.get(runId);
  if (!p) {
    p = getJson(`runs/${encodeURIComponent(runId)}.json`).then((raw) => checkSeries(raw, runId));
    p.catch(() => seriesCache.delete(runId));
    seriesCache.set(runId, p);
  }
  return p;
}
