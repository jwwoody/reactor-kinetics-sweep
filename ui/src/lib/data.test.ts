import { describe, expect, it } from 'vitest';
import {
  SchemaError,
  absDiff,
  checkIndex,
  checkSeries,
  compareNullable,
  filterRuns,
  orderedKeys,
  parseRunsParam,
  pctDiff,
  serializeRunsParam,
  seriesForAxis,
} from './data';
import { frac, makeAxis } from './scale';
import { formatPercent, formatValue, DASH } from './format';
import type { RunSummary } from './types';

const run = (id: string, rho: number, status: 'ok' | 'failed' = 'ok'): RunSummary => ({
  run_id: id,
  status,
  error: status === 'failed' ? 'boom' : null,
  inputs: { rho_dollars: rho, t_end: 10 },
  metrics: { n_max: status === 'ok' ? rho * 10 : null },
});

const validIndex = () => ({
  schema_version: 1,
  sweep: { name: 's' },
  runs: [run('a', 0.3), run('b', 0.5, 'failed')],
});

describe('pctDiff / absDiff', () => {
  it('computes percent relative to the base', () => {
    expect(pctDiff(10, 15)).toBeCloseTo(50);
    expect(pctDiff(10, 5)).toBeCloseTo(-50);
    expect(pctDiff(-10, -5)).toBeCloseTo(50);
  });
  it('returns null for null inputs and a zero base', () => {
    expect(pctDiff(null, 1)).toBeNull();
    expect(pctDiff(1, null)).toBeNull();
    expect(pctDiff(0, 3)).toBeNull();
    expect(pctDiff(0, 0)).toBe(0);
  });
  it('computes absolute difference with null handling', () => {
    expect(absDiff(2, 5)).toBe(3);
    expect(absDiff(null, 5)).toBeNull();
    expect(absDiff(2, null)).toBeNull();
  });
});

describe('checkIndex', () => {
  it('accepts a valid index', () => {
    expect(checkIndex(validIndex()).runs).toHaveLength(2);
  });
  it('rejects a wrong or missing schema_version with a clear message', () => {
    expect(() => checkIndex({ ...validIndex(), schema_version: 2 })).toThrow(SchemaError);
    expect(() => checkIndex({ ...validIndex(), schema_version: 2 })).toThrow(/schema_version/);
    const { schema_version: _drop, ...rest } = validIndex();
    expect(() => checkIndex(rest)).toThrow(SchemaError);
  });
  it('rejects malformed content', () => {
    expect(() => checkIndex(null)).toThrow(SchemaError);
    expect(() => checkIndex({ ...validIndex(), runs: 'x' })).toThrow(/runs/);
    expect(() => checkIndex({ ...validIndex(), runs: [{ run_id: 'a', status: 'weird' }] })).toThrow(
      /status/,
    );
  });
});

describe('checkSeries', () => {
  it('accepts matching arrays and rejects bad ones', () => {
    expect(checkSeries({ t: [0, 1], n: [1, 2] }, 'a').run_id).toBe('a');
    expect(() => checkSeries({ t: [0, 1], n: [1] }, 'a')).toThrow(SchemaError);
    expect(() => checkSeries({ t: [], n: [] }, 'a')).toThrow(SchemaError);
    expect(() => checkSeries({}, 'a')).toThrow(SchemaError);
  });
});

describe('query string', () => {
  it('parses, dedupes, trims and caps', () => {
    expect(parseRunsParam('?runs=a,b,a, c ,,d,e')).toEqual(['a', 'b', 'c', 'd']);
    expect(parseRunsParam('?runs=a,b', 1)).toEqual(['a']);
    expect(parseRunsParam('')).toEqual([]);
    expect(parseRunsParam('?runs=')).toEqual([]);
    expect(parseRunsParam('?runs=a%2Cb')).toEqual(['a', 'b']);
  });
  it('serializes with literal commas and keeps other params', () => {
    expect(serializeRunsParam(['a', 'b'])).toBe('?runs=a,b');
    expect(serializeRunsParam([])).toBe('');
    expect(serializeRunsParam(['a'], '?x=1&runs=zzz')).toBe('?x=1&runs=a');
    expect(serializeRunsParam([], '?x=1&runs=zzz')).toBe('?x=1');
  });
  it('round-trips', () => {
    const ids = ['4f1a9c2e77b0', '9c2e11aa00bb'];
    expect(parseRunsParam(serializeRunsParam(ids))).toEqual(ids);
  });
});

describe('null handling', () => {
  it('sorts nulls last in both directions', () => {
    const v = [3, null, 1, 2];
    expect([...v].sort((a, b) => compareNullable(a, b, 'asc'))).toEqual([1, 2, 3, null]);
    expect([...v].sort((a, b) => compareNullable(a, b, 'desc'))).toEqual([3, 2, 1, null]);
  });
  it('sorts strings', () => {
    expect(compareNullable('a', 'b')).toBeLessThan(0);
  });
  it('formats null as an em dash', () => {
    expect(formatValue(null)).toBe(DASH);
    expect(formatPercent(null)).toBe(DASH);
    expect(formatValue(12.345678)).toBe('12.35');
    expect(formatValue(1e-5)).toBe('1e-5');
    expect(formatPercent(12.34)).toBe('+12.3%');
  });
});

describe('series and filters', () => {
  it('drops t=0 only on a log time axis', () => {
    const s = { run_id: 'a', t: [0, 1, 2], n: [1, 2, 3] };
    expect(seriesForAxis(s, true)).toEqual({ t: [1, 2], n: [2, 3] });
    expect(seriesForAxis(s, false).t).toEqual([0, 1, 2]);
  });
  it('filters by status and input range', () => {
    const runs = [run('a', 0.3), run('b', 0.5), run('c', 0.7, 'failed')];
    expect(filterRuns(runs, 'ok', {}).map((r) => r.run_id)).toEqual(['a', 'b']);
    expect(
      filterRuns(runs, 'all', { rho_dollars: { min: 0.4, max: 0.6 } }).map((r) => r.run_id),
    ).toEqual(['b']);
    expect(filterRuns(runs, 'all', { nope: { min: 0, max: null } })).toEqual([]);
  });
  it('orders keys with known first', () => {
    const runs = [{ ...run('a', 1), inputs: { zeta: 1, rho_dollars: 1, alpha: 2 } }];
    expect(orderedKeys(runs, (r) => r.inputs, ['gen_time_s', 'rho_dollars'])).toEqual([
      'rho_dollars',
      'alpha',
      'zeta',
    ]);
  });
});

describe('scatter axes', () => {
  it('builds log axes on whole decades', () => {
    const ax = makeAxis([0.02, 5], true);
    expect(ax.domain).toEqual([0.01, 10]);
    expect(ax.ticks).toEqual([0.01, 0.1, 1, 10]);
    expect(frac(ax, 1)).toBeCloseTo(2 / 3);
  });
  it('builds linear axes and survives a constant series', () => {
    const ax = makeAxis([0, 10], false);
    expect(ax.ticks.length).toBeGreaterThan(2);
    const flat = makeAxis([3, 3], false);
    expect(flat.domain[0]).toBeLessThan(flat.domain[1]);
  });
});
