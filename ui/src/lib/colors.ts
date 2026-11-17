/** Okabe-Ito palette (colour-blind safe). Run slots map to CSS vars --run-1..4 in app.css. */
export const OKABE_ITO = {
  black: '#000000',
  orange: '#E69F00',
  skyBlue: '#56B4E9',
  green: '#009E73',
  yellow: '#F0E442',
  blue: '#0072B2',
  vermillion: '#D55E00',
  purple: '#CC79A7',
} as const;

/** Sequential, colour-blind-safe ramp (viridis stops) for continuous values. */
const RAMP = [
  [68, 1, 84],
  [59, 82, 139],
  [33, 145, 140],
  [94, 201, 98],
  [253, 231, 37],
] as const;

/** t in [0, 1] -> "rgb(r,g,b)". */
export function ramp(t: number): string {
  const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const f = x - i;
  const c = RAMP[i].map((v, k) => Math.round(v + (RAMP[i + 1][k] - v) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

export const RAMP_CSS = `linear-gradient(to right, ${RAMP.map(
  (c, i) => `rgb(${c.join(',')}) ${(i / (RAMP.length - 1)) * 100}%`,
).join(', ')})`;

export function runColorVar(slot: number): string {
  return `var(--run-${(slot % 4) + 1})`;
}
