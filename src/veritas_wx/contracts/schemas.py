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
OBS_VARIABLES: tuple[str, ...] = ("t2m", "wind10m", "precip_1h", "dewpoint")
INTERP_METHODS: tuple[str, ...] = ("nearest", "bilinear")
NETWORKS: tuple[str, ...] = ("inmet", "isd")

FACT_V1: dict[str, pl.DataType] = {
    "station_id": pl.Utf8,
    "model": pl.Utf8,
    "variable": pl.Utf8,
    "init_time": UTC_US,
    "valid_time": UTC_US,
    "lead_hours": pl.Int16,
    "fcst_raw": pl.Float64,
    "fcst_elev_adj": pl.Float64,
    "obs": pl.Float64,
    "delta_z": pl.Float64,
    "interp_method": pl.Utf8,
    "repr_floor": pl.Float64,
    "qc_flags": pl.Int32,
    "ingest_version": pl.Utf8,
}

FORECAST_POINTS_V1: dict[str, pl.DataType] = {
    "station_id": pl.Utf8,
    "model": pl.Utf8,
    "variable": pl.Utf8,
    "init_time": UTC_US,
    "valid_time": UTC_US,
    "lead_hours": pl.Int16,
    "value": pl.Float64,
    "interp_method": pl.Utf8,
    "grid_lat": pl.Float64,
    "grid_lon": pl.Float64,
    "grid_elev": pl.Float64,
    "ingest_version": pl.Utf8,
}

OBS_V1: dict[str, pl.DataType] = {
    "station_id": pl.Utf8,
    "valid_time": UTC_US,
    "variable": pl.Utf8,
    "value": pl.Float64,
    "source": pl.Utf8,
    "source_qc_raw": pl.Utf8,
    "ingest_version": pl.Utf8,
}

OBS_QC_V1: dict[str, pl.DataType] = {**OBS_V1, "qc_flags": pl.Int32}

STATIONS_V1: dict[str, pl.DataType] = {
    "station_id": pl.Utf8,
    "network": pl.Utf8,
    "native_id": pl.Utf8,
    "name": pl.Utf8,
    "uf": pl.Utf8,
    "lat": pl.Float64,
    "lon": pl.Float64,
    "elev_station": pl.Float64,
    "elev_dem": pl.Float64,
    "koppen": pl.Utf8,
    "cross_ref": pl.Utf8,
    "status": pl.Utf8,
    "exclusion_reason": pl.Utf8,
    "source_meta": pl.Utf8,
    "ingest_version": pl.Utf8,
}

RESULTS_V1: dict[str, pl.DataType] = {
    "metric": pl.Utf8,
    "comparison_id": pl.Utf8,
    "model": pl.Utf8,
    "variable": pl.Utf8,
    "lead_hours": pl.Int16,
    "stratum_type": pl.Utf8,
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
    "p_raw": pl.Float64,
    "p_adj": pl.Float64,
    "family_id": pl.Utf8,
    "n_family": pl.Int32,
    "registry_version": pl.Utf8,
    "ingest_version": pl.Utf8,
}
