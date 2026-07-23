# veritas-wx

**Weather forecast verification against raw surface station observations — not reanalysis.**

Global forecast leaderboards verify against reanalysis (ERA5). ERA5 does not directly assimilate
precipitation, and station-based verification has been on WeatherBench 2's future-work list since
2023. Studies that measured against real observations found errors 15–45% larger than leaderboards
report. This project fills that gap for Brazil first.

**The primary deliverable is not a dashboard.** It is a public, reproducible dataset of exactly
matched forecast↔observation pairs in Parquet. Everything else is a query on top of it.

## Phase 1 scope (hard ceiling)

| | |
|---|---|
| Region | Brazil |
| Stations | ~500, manually curated, verified metadata |
| Models | AIFS, GraphCast (operational), IFS HRES, GFS |
| Variables | t2m, 10m wind, 24h precipitation |
| Period | 12 months |

## Scientific non-negotiables

1. Representativeness error decomposed, never lumped into "model error"
2. Elevation correction for temperature (lapse rate, both values persisted)
3. Every statistic ships with a moving-block-bootstrap confidence interval
4. Metrics pre-registered (`metrics_registry.yaml`), Benjamini-Hochberg FDR control
5. Model comparisons only on exactly matched samples (materialized views)

Suspicious observations are **flagged, never deleted**. Missing data is **NULL, never imputed**.

## Layout

See [`PLAN.md`](PLAN.md) (pt-BR) for the full plan: directory structure, data contracts per stage,
implementation order, risks, and task breakdown. Task ledger in [`TASKS.md`](TASKS.md).

## Development

```bash
uv sync --group dev
uv run pytest -m "not network and not slow"
```

Code: MIT. Published dataset: CC-BY-4.0 with per-source attributions (ECMWF CC-BY-4.0, NOAA public
domain, INMET public data).
