# 04 — CI pipeline: GitHub Actions and GitLab CI

## Goal

On every push: lint, type-check, run the tests, run the smoke sweep, keep its results as an
artifact, and (on the main branch) build and publish the compare UI. Do it on both GitHub
Actions and GitLab CI.

## Running both from one repo (free)

GitLab's "CI/CD for external GitHub repos" is a paid feature, so push the same repo to both
hosts instead:

```bash
git remote add origin git@github.com:jwwoody/reactor-kinetics-sweep.git
git remote set-url --add --push origin git@github.com:jwwoody/reactor-kinetics-sweep.git
git remote set-url --add --push origin git@gitlab.com:jwoody/reactor-kinetics-sweep.git
git push origin main        # pushes to both; each host runs its own pipeline
```

GitHub is the public home. GitLab runs the same pipeline from
`.gitlab-ci.yml`. Link the GitLab pipeline from the README.

## Stages

| Stage | Job | Runs | Fails the build when |
|---|---|---|---|
| lint | `ruff check .`, `ruff format --check .`, `mypy src` | every push | style or type error |
| test | `pytest -q --cov=rksweep` | every push, Python 3.11 and 3.12 | any test fails |
| sweep | `rksweep sweep configs/smoke.toml --out results/` then `rksweep export ...` | every push | any run fails, or a metric leaves its expected band |
| pages | build `ui/` with the exported data, publish | main branch only | build fails |

**A physics regression check in the sweep stage:** commit a small `tests/golden/smoke_metrics.json`
(the expected `period_s` and `n_max` for the smoke runs). After the sweep, compare against it
with a relative tolerance (for example 1e-4). A solver or data change that moves the physics
then fails CI, with a clear diff in the log. This is the most useful job in the pipeline.

## GitHub Actions (`.github/workflows/ci.yml`)

```yaml
name: ci
on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
          cache: pip
      - run: pip install -e ".[dev]"
      - run: ruff check . && ruff format --check .
      - run: mypy src
      - run: pytest -q

  sweep:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -e ".[dev]"
      - run: rksweep sweep configs/smoke.toml --out results/
      - run: python -m rksweep.golden results/ tests/golden/smoke_metrics.json
      - run: rksweep export results/ --out ui/public/data/
      - uses: actions/upload-artifact@v4
        with:
          name: smoke-results
          path: |
            results/
            ui/public/data/

  pages:
    if: github.ref == 'refs/heads/main'
    needs: sweep
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          name: smoke-results
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: ui/package-lock.json
      - run: npm ci && npm run build
        working-directory: ui
      - uses: actions/upload-pages-artifact@v3
        with:
          path: ui/dist
      - uses: actions/deploy-pages@v4
```

Enable Pages in the repo settings with "Source: GitHub Actions".

## GitLab CI (`.gitlab-ci.yml`)

```yaml
stages: [lint, test, sweep, pages]

default:
  image: python:3.12-slim
  cache:
    key: pip
    paths: [.cache/pip]

variables:
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"

before_script:
  - pip install -e ".[dev]"

lint:
  stage: lint
  script:
    - ruff check .
    - ruff format --check .
    - mypy src

test:
  stage: test
  parallel:
    matrix:
      - PY: ["3.11", "3.12"]
  image: python:${PY}-slim
  script:
    - pytest -q --junitxml=report.xml
  artifacts:
    reports:
      junit: report.xml

sweep:
  stage: sweep
  script:
    - rksweep sweep configs/smoke.toml --out results/
    - python -m rksweep.golden results/ tests/golden/smoke_metrics.json
    - rksweep export results/ --out ui/public/data/
  artifacts:
    paths: [results/, ui/public/data/]
    expire_in: 2 weeks

pages:
  stage: pages
  image: node:20
  before_script: []
  needs: [sweep]
  script:
    - cd ui && npm ci && npm run build && cd ..
    - rm -rf public && cp -r ui/dist public
  artifacts:
    paths: [public]
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
```

The JUnit report makes test results show up in GitLab's merge-request UI.

## Things worth getting right

- **Same commands locally and in CI.** Put them in a `Makefile` or `justfile` (`make lint`,
  `make test`, `make smoke`) and call those from both pipelines.
- **Pin tool versions** in `pyproject.toml` dev extras, so CI does not break when Ruff changes.
- **Keep the smoke sweep under a minute.** The full sweep is a manual job
  (`workflow_dispatch` on GitHub, `when: manual` on GitLab), not a per-push job.
- **Badges** for both pipelines at the top of the README.

## Done when

A push to `main` turns both pipelines green, the smoke results are downloadable as an
artifact, and the Pages site updates with the new data.
