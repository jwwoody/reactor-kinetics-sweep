/** Labels and number formatting. Unknown keys fall back to the raw key. */

export const DASH = '—';

interface Label {
  label: string;
  unit?: string;
}

export const INPUT_LABELS: Record<string, Label> = {
  rho_dollars: { label: 'Reactivity', unit: '$' },
  gen_time_s: { label: 'Generation time', unit: 's' },
  ramp_rate_dps: { label: 'Ramp rate', unit: '$/s' },
  rho_max_dollars: { label: 'Max reactivity', unit: '$' },
  freq_hz: { label: 'Frequency', unit: 'Hz' },
  alpha_fb: { label: 'Feedback coeff.', unit: '' },
  t_end: { label: 'End time', unit: 's' },
};

export const METRIC_LABELS: Record<string, Label> = {
  n_max: { label: 'Peak power n(max)', unit: 'rel.' },
  t_peak: { label: 'Time of peak', unit: 's' },
  period_s: { label: 'Stable period', unit: 's' },
  energy: { label: 'Energy', unit: 'rel.' },
  t_double: { label: 'Doubling time', unit: 's' },
  wall_time_s: { label: 'Wall time', unit: 's' },
  nfev: { label: 'RHS evaluations', unit: '' },
  njev: { label: 'Jacobian evaluations', unit: '' },
};

export const KNOWN_INPUTS = Object.keys(INPUT_LABELS);
export const KNOWN_METRICS = Object.keys(METRIC_LABELS);

export function keyLabel(key: string): string {
  return (INPUT_LABELS[key] ?? METRIC_LABELS[key])?.label ?? key;
}

export function keyUnit(key: string): string {
  return (INPUT_LABELS[key] ?? METRIC_LABELS[key])?.unit ?? '';
}

/** "Reactivity ($)" style heading text. */
export function keyHeading(key: string): string {
  const u = keyUnit(key);
  return u ? `${keyLabel(key)} (${u})` : keyLabel(key);
}

/** Format a number compactly with 4 significant digits; null becomes an em dash. */
export function formatValue(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH;
  if (!Number.isFinite(v)) return v > 0 ? '∞' : '-∞';
  if (v === 0) return '0';
  if (Number.isInteger(v) && Math.abs(v) < 1e6) return String(v);
  const a = Math.abs(v);
  if (a >= 1e5 || a < 1e-3) return v.toExponential(3).replace(/\.?0+e/, 'e').replace('e+', 'e');
  return String(Number(v.toPrecision(4)));
}

export function formatSigned(v: number | null): string {
  if (v === null) return DASH;
  const s = formatValue(v);
  return v > 0 ? `+${s}` : s;
}

export function formatPercent(v: number | null): string {
  if (v === null) return DASH;
  const s = Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);
  return `${v > 0 ? '+' : ''}${s}%`;
}

/** Short form of an id for tight spaces. */
export function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}
