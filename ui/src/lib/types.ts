/** Shapes of the JSON written by `rksweep export` (schema_version 1). */

export const SCHEMA_VERSION = 1;
export const MAX_SELECTED = 4;

export type Nullable<T> = T | null;

export interface SweepInfo {
  name: string;
  sweep_id: string;
  design: string;
  input: string;
  data_set: string;
  git_sha: string;
  created_at: string;
}

export interface RunSummary {
  run_id: string;
  status: 'ok' | 'failed';
  error: string | null;
  /** All inputs are numbers; the set of keys varies by sweep. */
  inputs: Record<string, number>;
  /** Any metric may be null (JSON has no NaN/Infinity). */
  metrics: Record<string, number | null>;
}

export interface SweepIndex {
  schema_version: number;
  sweep: SweepInfo;
  runs: RunSummary[];
}

export interface RunSeries {
  run_id: string;
  t: number[];
  n: number[];
}

export type SeriesState =
  | { status: 'loading' }
  | { status: 'ok'; data: RunSeries }
  | { status: 'error'; message: string };
