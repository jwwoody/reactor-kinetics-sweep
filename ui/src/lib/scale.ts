/** Small axis helpers for the SVG scatter plot. */

export interface Axis {
  domain: [number, number];
  ticks: number[];
  log: boolean;
}

function niceStep(raw: number): number {
  const pow = 10 ** Math.floor(Math.log10(raw));
  const f = raw / pow;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * pow;
}

/** Axis for values `vals`. Log axes need all values > 0 (caller guarantees). */
export function makeAxis(vals: number[], log: boolean, count = 5): Axis {
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  if (log) {
    let a = Math.floor(Math.log10(lo));
    let b = Math.ceil(Math.log10(hi));
    if (a === b) b = a + 1;
    const stride = Math.max(1, Math.ceil((b - a) / 8));
    const ticks: number[] = [];
    for (let k = a; k <= b; k += stride) ticks.push(10 ** k);
    return { domain: [10 ** a, 10 ** b], ticks, log: true };
  }
  let a = lo;
  let b = hi;
  if (a === b) {
    const d = Math.abs(a) * 0.1 || 1;
    a -= d;
    b += d;
  } else {
    const pad = (b - a) * 0.06;
    a -= pad;
    b += pad;
  }
  const step = niceStep((b - a) / count);
  const ticks: number[] = [];
  for (let v = Math.ceil(a / step) * step; v <= b + step * 1e-9; v += step) {
    ticks.push(Number(v.toPrecision(12)));
  }
  return { domain: [a, b], ticks, log: false };
}

/** Map v to [0, 1] along the axis. */
export function frac(axis: Axis, v: number): number {
  const [a, b] = axis.domain;
  return axis.log
    ? (Math.log10(v) - Math.log10(a)) / (Math.log10(b) - Math.log10(a))
    : (v - a) / (b - a);
}
