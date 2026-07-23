# veritas-wx

[🇧🇷 Português](README.md) · 🇺🇸 English

**Weather forecast verification against raw surface station observations — not reanalysis.**

Every major AI-weather leaderboard verifies models against reanalysis (ERA5) — which is itself a
model output, not a measurement. ERA5 does not directly assimilate precipitation; WeatherBench has
acknowledged station-based verification as future work since 2023. Studies that measured against
real observations found errors **15–45% larger** than leaderboards report.

There is also a mathematical reason this matters. A model trained on MSE converges to the
conditional mean, and by the law of total variance the conditional mean has *strictly less
variance* than the real process (σ_fcst = ρ·σ_obs < σ_obs for ρ < 1). MSE-trained AI models are
blind to record extremes **by construction** — and the better their leaderboard RMSE, the more
smoothed their fields. veritas-wx measures that footprint directly, against reality.

**The primary deliverable is not a dashboard.** It is a public, reproducible dataset of exactly
matched forecast↔observation pairs in Parquet. Everything else is a query on top of it.

## What makes it different

We audited the source code of WeatherBench 2 and its successor WeatherBench-X before building.
Capabilities marked ✅/❌ were verified in their code, not their papers:

| Capability | WB2 | WBX | veritas-wx |
|---|---|---|---|
| Verification against raw stations | ❌ (0 hits) | partial (METAR loader; no published station results) | ✅ INMET Brazil, 300+ curated stations |
| Confidence interval on every estimate | ❌ (0 hits) | opt-in module | ✅ enforced by test — no public API returns a naked number |
| Independent physical QC of observations | ❌ | ❌ (provider flags only) | ✅ 6 checks, flag-never-delete bitmask |
| Representativeness error decomposition | ❌ | ❌ (0 hits) | ✅ empirical floor from co-located stations |
| Regime stratification (Köppen/ENSO/MJO) | ❌ | ❌ (0 hits) | ✅ pre-registered strata |
| Pre-registration + FDR control | ❌ | ❌ | ✅ registry committed before any data, BH families |
| Per-model precipitation accumulation conventions | single pattern, silent negative clipping | out of scope | ✅ explicit conventions, negatives preserved and counted |
| SEEPS with bootstrap CI, per station | grid-only, no CI | grid climatology only | ✅ station-level p1/wet threshold from INMET history |

What WBX does better, we ported (Apache 2.0, with attribution): Ingleby wind altitude adjustment,
Geer AR(2) + Lazarus HAC t-tests for cheap paired model comparison, Rodwell station-density
weighting. What WB2 computed that WBX dropped, we ported too: rolling day-of-year climatology —
per station instead of per grid cell.

## Phase 1 scope (hard ceiling)

| | |
|---|---|
| Region | Brazil |
| Stations | 304 curated (of 1,118 candidates; ≥80% clean-data completeness) |
| Models | AIFS, GraphCast (operational), IFS HRES, GFS |
| Variables | t2m, 10m wind, 24h precipitation |
| Period | 2025-07-01 → 2026-06-30 |

## Scientific non-negotiables

1. Representativeness error decomposed, never lumped into "model error"
2. Elevation adjustment for temperature (lapse rate) and wind (Ingleby 2014) — raw and adjusted both persisted
3. Every statistic ships with a moving-block-bootstrap confidence interval (day blocks, Politis-White length)
4. Metrics pre-registered (`metrics_registry.yaml`, committed before any analysis), Benjamini-Hochberg FDR control
5. Model comparisons only on exactly matched samples (same keys, same QC mask, materialized views)

Suspicious observations are **flagged, never deleted**. Missing data is **NULL, never imputed**.
Every pipeline stage enforces a row-reconciliation identity: `rows_in == rows_out + itemized drops`.

## Preliminary findings (August 2025 validation slice, GFS, 123k pairs)

- GFS runs **+1.2 to +2.3 °C warm** over Brazil, growing with lead time — worst in the coldest
  hours (+3.7 °C at the 0–10th observed percentile)
- Wind over-predicted by +1.4–1.7 m/s against station anemometers
- Heavy-precipitation bias is negative in the top percentile bins — the direction the
  conditional-mean theory predicts (pre-registered hypothesis H2)
- All numbers carry 95% block-bootstrap CIs; full 4-model × 12-month results land with the
  complete backfill

## How it works

```
ingest/          raw INMET bulk zips, GRIB byte-range fetches (GFS/HRES/AIFS),
                 selective HDF5 chunk reads (GraphCast: ~280 MB/run instead of 5.8 GB)
   ↓ sha256 manifests, idempotent
qc/              6 independent physical checks -> qc_flags bitmask (nothing deleted)
   ↓
match/           bilinear extraction at stations, elevation adjustment, per-model
                 precip conventions, representativeness floor, FACT_V1 pairs
   ↓ exactly-matched views per model comparison
analyze/         pure functions only: block bootstrap, BH FDR, pre-registered metrics
                 (variance_ratio, bias_by_percentile, regime skill), SEEPS+CI,
                 AR(2)/HAC t-tests, station climatology, density weighting
```

## How to run

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), ~500 GB disk and bandwidth for the full
backfill (each stage is resumable; partial runs are fine).

```bash
uv sync --group dev --group grib --group geo --group graphcast --group figures

# tests (no network needed)
uv run pytest -m "not network and not slow"

# the whole pipeline, one command (stages are independent and resumable):
uv run python scripts/build_all.py --stages obs qc forecasts fact views figures
```

Storage layout expects a `data/` directory (or symlink) with room for the lake; every download is
recorded in a sha256 manifest and re-runs skip verified artifacts.

## Data sources & licenses

| Source | Use | License |
|---|---|---|
| INMET (dadoshistoricos bulk) | surface observations | public data |
| NOAA GFS (AWS Open Data) | forecasts | US public domain |
| ECMWF Open Data (HRES, AIFS) | forecasts | CC-BY-4.0 |
| NOAA/CIRA AIWP archive (GraphCast) | forecasts | US public domain (cite Radford et al. 2025) |
| Copernicus GLO-30 DEM | station elevation cross-check | free with attribution |
| Beck et al. 2023 Köppen-Geiger | climate strata | CC-BY-4.0 |

**Code: MIT.** Portions of `analyze/ttest.py`, `analyze/weighting.py`, `analyze/climatology.py`,
`analyze/metrics/seeps.py` and `match/elevation.py` are derived from WeatherBench 2 /
WeatherBench-X (Copyright 2023 Google LLC, Apache License 2.0) — see module docstrings.
**Published dataset: CC-BY-4.0** with per-source attributions.

## Roadmap

- Full 12-month × 4-model fact table + the three pre-registered figures (H1/H2/H3)
- Registry v2 amendment: promote SEEPS/ACC to confirmatory with their own BH families
- Forecast-of-opportunity index: predicting *when* forecasts will be reliable
- Phase 2: more networks (METAR cross-validation), more regions

## Contributing

See [CONTRIBUTING.en.md](CONTRIBUTING.en.md) (🇧🇷 [Português](CONTRIBUTING.md)). The short
version: quality over quantity, no estimate without a CI, and nothing enters the confirmatory
registry without a versioned amendment.
