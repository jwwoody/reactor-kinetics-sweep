# 01 — Physics kernel: point kinetics

## The model in plain words

A reactor's power is proportional to its neutron population `n`. Most neutrons come straight
from fission ("prompt"). A small fraction, about 0.65% for uranium-235, come later from the
decay of certain fission products ("delayed neutrons", from "precursors"). Those few delayed
neutrons are what make a reactor controllable: they slow the response from microseconds to
seconds.

"Point" kinetics treats the whole core as one point, with no spatial shape. It is the standard
first model in every reactor-physics course.

## Equations

With six precursor groups `C_i`:

```text
dn/dt   = ((ρ(t) − β) / Λ) · n  +  Σ_i λ_i · C_i
dC_i/dt = (β_i / Λ) · n  −  λ_i · C_i          for i = 1..6
```

| Symbol | Meaning | Typical value |
|---|---|---|
| `n` | Neutron population (relative power) | Start at 1.0 |
| `ρ(t)` | Reactivity (dimensionless); `ρ/β` is reactivity "in dollars" | $0.05-$0.9 |
| `β_i` | Delayed-neutron fraction of group i; `β = Σ β_i` | β ≈ 0.0065 (U-235) |
| `λ_i` | Decay constant of group i (1/s) | 0.0124-3.01 |
| `Λ` | Prompt neutron generation time (s) | 1e-4 to 1e-5 thermal; ~1e-7 to 1e-6 fast spectrum |

**Steady-state initial condition** (from setting the derivatives to 0 with ρ = 0):

```text
n(0) = 1
C_i(0) = β_i · n(0) / (λ_i · Λ)
```

## Delayed-neutron data (Keepin, U-235 thermal fission)

Put this in `constants.py` as a frozen dataclass and cite the source in a docstring.

| Group | Relative abundance `a_i = β_i/β` | `λ_i` (1/s) |
|---|---|---|
| 1 | 0.033 | 0.0124 |
| 2 | 0.219 | 0.0305 |
| 3 | 0.196 | 0.111 |
| 4 | 0.395 | 0.301 |
| 5 | 0.115 | 1.14 |
| 6 | 0.042 | 3.01 |

Use `β = 0.0065` and `β_i = a_i · β`. Abundances sum to 1.000. Write a test that checks it.

These are textbook values. Say so in the code and README, and do not present them as
evaluated nuclear data. Store the data set as a named object (`KEEPIN_U235_THERMAL`) so a
second set could be added later without changing the solver.

## Matrix form (use it, it is the key idea)

Stack the state as `x = [n, C_1, …, C_6]`. For constant ρ the system is linear, `dx/dt = A·x`:

```text
A[0, 0] = (ρ − β) / Λ
A[0, i] = λ_i            (i = 1..6)
A[i, 0] = β_i / Λ
A[i, i] = −λ_i
```

This gives you three things:

1. **An exact answer for step inputs:** `x(t) = expm(A·t) · x(0)` with `scipy.linalg.expm`.
   That is your main correctness test for the ODE solver.
2. **The Jacobian for free:** for constant ρ the Jacobian is `A`. Passing `jac=` to the stiff
   solver makes it much faster.
3. **A clear performance story:** measure the solver with and without the Jacobian.

## Why the problem is "stiff", and what to do about it

The fastest time scale is about `Λ/β` (milliseconds for thermal, microseconds for fast). The
slowest is about `1/λ_1 ≈ 80 s`. A ratio of 10^4-10^6 between time scales is what "stiff"
means. Explicit solvers like `RK45` take tiny steps the whole way and crawl.

- Use `solve_ivp(..., method="Radau")` or `"BDF"`, with `jac=`, `rtol=1e-8`, `atol=1e-12`.
- Use `LSODA` as a second option. It switches methods automatically.
- Benchmark `RK45` versus `Radau`, with and without the Jacobian, in `benchmarks/solver.py`.
  Put the table (wall time, steps, function evaluations) in the README. This is the honest
  version of "improve performance of existing models".

Ask for output on a log-spaced time grid (`np.geomspace(1e-6, t_end, 400)`) plus `t = 0`.
The interesting behavior happens at very different scales, and log spacing captures both.

## Suggested module API

```python
# kinetics.py
def rhs(
    t: float, x: np.ndarray, rho: Callable[[float], float], data: DelayedData, gen_time: float
) -> np.ndarray: ...
def jacobian(
    t: float, x: np.ndarray, rho: Callable[[float], float], data: DelayedData, gen_time: float
) -> np.ndarray: ...
def steady_state(data: DelayedData, gen_time: float, n0: float = 1.0) -> np.ndarray: ...
def solve(
    rho: ReactivityInput,
    data: DelayedData,
    gen_time: float,
    t_end: float,
    method: str = "Radau",
    n_points: int = 400,
) -> Solution: ...


# Solution: a small dataclass with t (1-D array), n (1-D array), C (6 x len(t)), solver stats.
```

Write `rhs` vectorized with NumPy (no Python loop over groups). Keep `solve` free of I/O.

## Reactivity inputs (`reactivity.py`)

Model each input as a small class with `__call__(t) -> float` and a `to_dict()` for storage.

| Input | Definition | Notes |
|---|---|---|
| Step | `ρ0` for t ≥ 0 | Exact `expm` answer exists |
| Ramp | `rate · t`, capped at `ρ_max` | Common "rod withdrawal" case |
| Sine | `ρ0 · sin(2π f t)` | Shows the reactor as a low-pass filter |
| Step + feedback (stretch) | `ρ0 + α · (T − T0)` with a lumped temperature `dT/dt = K·n − (T − T0)/τ` | Adds one state variable; makes the problem nonlinear |

With feedback, use a negative `α` (temperature rise lowers reactivity). The power should rise,
peak and settle. That self-limiting curve is the most interesting plot in the project.

Guard against prompt criticality: if `ρ ≥ β` ($1 or more) anywhere in the input, raise or
flag it. The model is still solvable, but the time scales collapse, and a sweep should not
wander there by accident.

## Verification tests (`tests/test_kinetics.py`)

These tests are what make the physics credible. Write them before the sweep code.

1. **Data sanity:** abundances sum to 1; all `λ_i > 0`; `β` matches the sum of `β_i`.
2. **Steady state holds:** with ρ = 0, `n(t)` stays at 1.0 within `1e-9` for 100 s.
3. **Matches the exact solution:** for steps of $0.1, $0.5 and −$0.5, the ODE solution matches
   `expm(A·t)·x0` within `rtol = 1e-6` at every output time.
4. **Prompt jump:** for a small positive step `ρ < β`, right after the fast transient
   (`t ≈ 10·Λ/(β−ρ)`), `n ≈ β / (β − ρ)`. Allow a few percent, since this is an
   approximation.
5. **Stable period (inhour equation):** after the transients die out, `n ∝ exp(ω·t)`, where `ω`
   is the largest root of

   ```text
   ρ = ω·Λ + Σ_i β_i · ω / (ω + λ_i)
   ```

   - For ρ > 0, find `ω` with `scipy.optimize.brentq` on `[0, ρ/Λ]`. `f(0) = −ρ < 0` and
     `f(ρ/Λ) > 0`, so the root is bracketed.
   - For ρ < 0, the root is in `(−λ_1, 0)`. Bracket it as `(−λ_1 + 1e-12, 0)`.
   - Fit the slope of `ln n` over the last 20% of a long run and compare it to `ω` within 1%.
   - Put the root finder in `reference.py`; it is also a useful metric.
6. **Negative step decays:** for ρ < 0, `n` decreases monotonically after the prompt drop.
7. **Property test (Hypothesis):** for random steps in `[−$1, +$0.9]` and random `Λ` in
   `[1e-7, 1e-4]`, the ODE result matches `expm` within tolerance, and `n` stays positive.

## Metrics (`metrics.py`)

| Metric | Definition |
|---|---|
| `n_max` | Peak relative power |
| `t_peak` | Time of the peak |
| `period_s` | `1/ω` from a log-slope fit over the late window (∞ if flat) |
| `energy` | `∫ n dt` with `scipy.integrate.trapezoid` (relative units) |
| `t_double` | First time `n ≥ 2` (NaN if never) |
| `wall_time_s`, `nfev`, `njev` | Solver cost, from `time.perf_counter()` and the result object |

## Done when

- `pytest -q` passes with all seven tests above.
- A notebook or script plots `n(t)` on log-log axes for steps of $0.1, $0.3, $0.5 and −$0.5.
- The solver benchmark table is in the README.
