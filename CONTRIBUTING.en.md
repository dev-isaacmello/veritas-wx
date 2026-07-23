# Contributing to veritas-wx

Thanks for considering a contribution. This project values **scientific discipline over feature
count** — the rules below exist so the dataset stays trustworthy.

## Dev setup

```bash
git clone https://github.com/dev-isaacmello/veritas-wx.git
cd veritas-wx
uv sync --group dev            # + --group grib --group geo --group graphcast as needed
uv run pytest -m "not network and not slow"
uv run ruff check src/ scripts/ tests/
```

Tests marked `network` hit live buckets/APIs and are excluded from CI; run them locally before
touching any fetcher. Tests marked `slow` (bootstrap coverage properties) run with
`uv run pytest -m slow`.

## The rules that are not negotiable

1. **No estimate without a confidence interval.** Public functions in `analyze/` return
   `BootstrapResult` (or `TTestResult`), never a bare float. There is a test that enforces this;
   do not fight it.
2. **The registry is frozen.** `metrics_registry.yaml` is pre-registered. New metrics enter as
   *exploratory diagnostics* (documented as such in their docstrings). Promotion to confirmatory
   requires a versioned registry amendment in its own PR, with its own BH family.
3. **Flag, never delete.** QC sets bits; consumers choose rigor via mask. Deleting observations
   is a bug, not a cleanup.
4. **NULL, never imputed.** A missing hour is a missing hour. Precip 24h totals require ≥22 clean
   hourly readings; partial sums are forbidden.
5. **Every stage reconciles.** `rows_in == rows_out + sum(itemized drops)` — the runlog raises
   otherwise. If you add a filter, add its drop counter.
6. **Pure functions in `analyze/`.** No I/O, no hidden randomness — RNGs are explicit arguments.
7. **Idempotent ingestion.** Downloads go through sha256 manifests; re-running must be a no-op
   for verified artifacts.

## Style

- Python 3.12, `ruff` (line length 100) — CI runs it
- Docstrings carry the documentation; avoid inline `#` comments (functional pragmas like
  `# noqa` are fine)
- Golden tests: when porting a formula, hand-compute at least one case in the test's docstring
- Ported code (e.g. from WeatherBench-X, Apache 2.0) keeps an attribution line in the module
  docstring

## Good first contributions

- New QC checks (with golden tests and an entry in the bitmask contract)
- Station networks beyond INMET (the `duplicate_check` already supports cross-network)
- Metric ports from the literature as exploratory diagnostics
- Performance work on the bootstrap fast-paths

## Pull requests

- One logical change per PR; tests green (`not network and not slow` suite) and ruff clean
- If your change affects data semantics (units, conventions, thresholds), say so explicitly in
  the PR description — those bump `ingest_version`
- English for code/commits; PT-BR welcome in issues and discussions
