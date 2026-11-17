# Use the project venv when it exists (local), otherwise whatever is on PATH (CI).
BIN ?= $(if $(wildcard .venv/bin/python),.venv/bin/,)

.PHONY: lint test smoke golden export full bench

lint:
	$(BIN)ruff check .
	$(BIN)ruff format --check .
	$(BIN)mypy src

test:
	$(BIN)pytest -q --cov=rksweep

# Smoke sweep (9 runs), physics regression check, JSON export for the UI.
smoke:
	$(BIN)rksweep sweep configs/smoke.toml --out results/

golden:
	$(BIN)python -m rksweep.golden results/ tests/golden/smoke_metrics.json

export:
	$(BIN)rksweep export results/ --out ui/public/data/

# Full Latin-hypercube sweep (300 runs).
full:
	$(BIN)rksweep sweep configs/full.toml --out results-full/

# Benchmarks: solver, storage layouts and parallel speedup (storage needs `make full`).
bench: full
	$(BIN)python benchmarks/solver.py
	$(BIN)python benchmarks/storage.py results-full/
	$(BIN)python benchmarks/parallel.py
