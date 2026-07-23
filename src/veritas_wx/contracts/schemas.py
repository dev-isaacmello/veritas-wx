"""Frozen polars schemas for every inter-stage contract (v1).

FACT_V1 reproduces the brief's contract verbatim. Evolution is additive-only;
breaking changes require a v2 constant plus an ADR. `schema_version` and
`ingest_version` are also written into Parquet file-level metadata by writers.

All timestamps are UTC, microsecond precision. valid_time = init_time + lead_hours.
"""

import polars as pl

UTC_US = pl.Datetime(time_unit="us", time_zone="UTC")

MODELS: tuple[str, ...] = ("aifs", "gfs", "graphcast", "hres")
VARIABLES: tuple[str, ...] = ("t2m", "wind10m", "precip_24h", "dewpoint")
INTERP_METHODS: tuple[str, ...] = ("nearest", "bilinear")
NETWORKS: tuple[str, ...] = ("inmet", "isd")

# ---------------------------------------------------------------------------
# Fact table — the publishable source of truth (brief contract, verbatim)
# Partitioning: year=/month=/model= ; files sorted by (station_id, valid_time)
# ---------------------------------------------------------------------------
FACT_V1: dict[str, pl.DataType] = {
    "station_id": pl.Utf8,  # canonical: "{network}:{native_id}", e.g. "inmet:A001"
    "model": pl.Utf8,
    "variable": pl.Utf8,
    "init_time": UTC_US,
    "valid_time": UTC_US,
    "lead_hours": pl.Int16,
    "fcst_raw": pl.Float64,  # canonical units; NEVER silently clipped/adjusted
    "fcst_elev_adj": pl.Float64,  # NULL when not applicable (Phase 1: t2m only)
    "obs": pl.Float64,
    "delta_z": pl.Float64,  # elev_station - elev_cell (m)
    "interp_method": pl.Utf8,  # nearest | bilinear
    "repr_floor": pl.Float64,  # variance units (value^2); NULL when not estimable
    "qc_flags": pl.Int32,  # bitmask, see contracts.qc_bits
    "ingest_version": pl.Utf8,
}

# ---------------------------------------------------------------------------
# Forecasts extracted at station points (match/extract output)
# ---------------------------------------------------------------------------
FORECAST_POINTS_V1: dict[str, pl.DataType] = {
    "station_id": pl.Utf8,
    "model": pl.Utf8,
    "variable": pl.Utf8,
    "init_time": UTC_US,
    "valid_time": UTC_US,
    "lead_hours": pl.Int16,
    "value": pl.Float64,  # canonical units; precip already as 24h accumulation
    "interp_method": pl.Utf8,
    "grid_lat": pl.Float64,
    "grid_lon": pl.Float64,
    "grid_elev": pl.Float64,  # model orography at the cell (m)
    "ingest_version": pl.Utf8,
}

# ---------------------------------------------------------------------------
# Canonical observations (ingest/observations output; before QC)
# ---------------------------------------------------------------------------
OBS_V1: dict[str, pl.DataType] = {
    "station_id": pl.Utf8,
    "valid_time": UTC_US,
    "variable": pl.Utf8,
    "value": pl.Float64,  # canonical units
    "source": pl.Utf8,  # inmet | isd
    "source_qc_raw": pl.Utf8,  # original source flag, preserved verbatim (nullable)
    "ingest_version": pl.Utf8,
}

# QC output: same rows, one extra column. Observations are never deleted.
OBS_QC_V1: dict[str, pl.DataType] = {**OBS_V1, "qc_flags": pl.Int32}

# ---------------------------------------------------------------------------
# Curated station metadata (ingest/static output)
# ---------------------------------------------------------------------------
STATIONS_V1: dict[str, pl.DataType] = {
    "station_id": pl.Utf8,  # canonical "{network}:{native_id}"
    "network": pl.Utf8,  # inmet | isd
    "native_id": pl.Utf8,
    "name": pl.Utf8,
    "uf": pl.Utf8,  # state code; NULL for non-INMET when unknown
    "lat": pl.Float64,
    "lon": pl.Float64,
    "elev_station": pl.Float64,  # from source metadata (m)
    "elev_dem": pl.Float64,  # Copernicus GLO-30 at station coords (m); NULL if unavailable
    "koppen": pl.Utf8,  # Köppen-Geiger class (e.g. "Aw"); NULL if unassigned
    "cross_ref": pl.Utf8,  # station_id of same physical site in another network (nullable)
    "status": pl.Utf8,  # included | excluded | review
    "exclusion_reason": pl.Utf8,  # REQUIRED when status == excluded (no silent loss)
    "source_meta": pl.Utf8,  # provenance of the metadata record
    "ingest_version": pl.Utf8,
}

# ---------------------------------------------------------------------------
# Analysis results (analyze output) — no estimate without uncertainty
# ---------------------------------------------------------------------------
RESULTS_V1: dict[str, pl.DataType] = {
    "metric": pl.Utf8,
    "comparison_id": pl.Utf8,  # sorted "+"-joined models of the matched view
    "model": pl.Utf8,
    "variable": pl.Utf8,
    "lead_hours": pl.Int16,
    "stratum_type": pl.Utf8,  # global | season | koppen | enso | mjo_phase | obs_percentile_bin
    "stratum_value": pl.Utf8,
    "estimate": pl.Float64,
    "ci_low": pl.Float64,
    "ci_high": pl.Float64,
    "alpha": pl.Float64,
    "n_pairs": pl.Int64,
    "n_stations": pl.Int32,
    "n_days": pl.Int32,
    "block_len_days": pl.Int16,
    "n_boot": pl.Int32,
    "p_raw": pl.Float64,  # NULL outside hypothesis tests
    "p_adj": pl.Float64,  # Benjamini-Hochberg adjusted; NULL outside tests
    "family_id": pl.Utf8,
    "n_family": pl.Int32,  # number of tests in the BH family, recorded with the result
    "registry_version": pl.Utf8,
    "ingest_version": pl.Utf8,
}
