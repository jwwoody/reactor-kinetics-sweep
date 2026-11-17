// Writes synthetic data in the index.json / runs/<id>.json contract to public/data/ so the UI can
// be developed without the Python exporter. The curves are a toy one-delayed-group model, not
// real reactor physics.
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'data');
rmSync(root, { recursive: true, force: true });
mkdirSync(join(root, 'runs'), { recursive: true });

const BETA = 0.0065;
const LAMBDA = 0.08; // 1/s
const T_END = 200;
const N_POINTS = 300;

const round = (x, d = 6) => Number(x.toPrecision(d));

/** Prompt jump 1/(1 - rho) followed by an exponential rise (toy model, rho in dollars < 1). */
function power(t, rho, gen) {
  const omega = (rho * BETA * LAMBDA) / (BETA - rho * BETA + gen * LAMBDA);
  return Math.max(1e-6, Math.exp(omega * t) / (1 - rho));
}

const grid = [];
for (const rho of [0.2, 0.4, 0.6]) for (const gen of [1e-5, 1e-4, 1e-3]) grid.push({ rho, gen });

const runs = [];
for (const { rho, gen } of grid) {
  const inputs = { rho_dollars: rho, gen_time_s: gen, t_end: T_END };
  const id = createHash('sha1').update(JSON.stringify(inputs)).digest('hex').slice(0, 12);
  const omega = (rho * BETA * LAMBDA) / (BETA - rho * BETA + gen * LAMBDA);
  const t = [0];
  for (let i = 1; i < N_POINTS; i++) t.push(round(1e-6 * Math.pow(T_END / 1e-6, i / (N_POINTS - 1))));
  const n = t.map((x) => round(power(x, rho, gen)));
  writeFileSync(join(root, 'runs', `${id}.json`), JSON.stringify({ run_id: id, t, n }));
  runs.push({
    run_id: id,
    status: 'ok',
    error: null,
    inputs,
    metrics: {
      n_max: round(Math.max(...n)),
      t_peak: T_END,
      period_s: omega > 0 ? round(1 / omega) : null,
      energy: round(n.reduce((s, v, i) => (i ? s + 0.5 * (v + n[i - 1]) * (t[i] - t[i - 1]) : s), 0)),
      // Slow runs never double within the window: demonstrates null handling.
      t_double: omega > 0 && Math.log(2) / omega < T_END ? round(Math.log(2) / omega) : null,
      wall_time_s: round(0.01 + 0.02 * Math.abs(Math.log10(gen)) / 5),
      nfev: 150 + Math.round(40 * Math.abs(Math.log10(gen))),
      njev: 8 + Math.round(rho * 10),
    },
  });
}

runs.push({
  run_id: createHash('sha1').update('failed').digest('hex').slice(0, 12),
  status: 'failed',
  error: 'Integrator failed: step size became too small at t = 12.3 s',
  inputs: { rho_dollars: 1.2, gen_time_s: 1e-5, t_end: T_END },
  metrics: {
    n_max: null, t_peak: null, period_s: null, energy: null,
    t_double: null, wall_time_s: null, nfev: null, njev: null,
  },
});

const index = {
  schema_version: 1,
  sweep: {
    name: 'sample',
    sweep_id: 'sample-20260101T000000Z',
    design: 'grid',
    input: 'step',
    data_set: 'KEEPIN_U235_THERMAL',
    git_sha: 'sample0',
    created_at: '2026-01-01T00:00:00Z',
  },
  runs,
};
writeFileSync(join(root, 'index.json'), JSON.stringify(index, null, 2));
console.log(`Wrote ${runs.length} runs to ui/public/data/`);
