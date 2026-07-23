"""Fact table builder: forecast points × QC'd observations -> FACT_V1 rows.

Pure: returns (fact_df, dropped_counts) so the orchestrator can enforce the
runlog reconciliation identity. Semantics frozen here (and documented in
PLAN.md §2.5):

  - t2m/wind10m pairs join on the exact hourly valid_time.
  - precip_24h observation = sum of CLEAN (qc_flags == 0) hourly precip_1h in
    (valid_time-24h, valid_time]; requires >= min_clean_hours (default 22).
    Missing hours are never zero-filled.
  - pair qc_flags: t2m/wind10m -> the hour's flags; precip_24h -> bitwise OR
    over ALL hours in the window (flagged-excluded hours still surface their
    bits so a degraded window is visible to consumers).
  - fcst_elev_adj: lapse-rate correction for t2m; Ingleby wind altitude
    factor for wind10m (delta_z = elev_station - grid_elev, adjusted only
    when the station sits > 100 m above the model orography, factor 1.0
    otherwise); NULL for other variables and when delta_z is NULL.
  - |delta_z| > max_delta_z pairs are dropped AND counted.
  - repr_floor attached per (cell, variable); NULL where not estimable.
"""

import polars as pl

from veritas_wx.contracts import FACT_V1, qc_bits, validate
from veritas_wx.match.elevation import (
    LAPSE_RATE_K_PER_M,
    WIND_FACTOR_MAX,
    WIND_FACTOR_PER_M,
    WIND_ONSET_DELTA_Z_M,
)
from veritas_wx.match.repr_floor import attach_repr_floor

_OBS_JOIN_COLS = ["station_id", "valid_time", "variable"]


def derive_precip_24h_obs(obs_qc: pl.DataFrame, min_clean_hours: int = 22) -> pl.DataFrame:
    """Hourly precip_1h -> 24h totals at every observed hourly timestamp.

    Returns (station_id, valid_time, variable='precip_24h', obs, qc_flags,
    n_clean_hours). Rows failing the completeness rule are EXCLUDED here and
    counted by the caller via the returned frame's complement — build_fact
    reports them as dropped['precip_incomplete'].
    """
    p = obs_qc.filter(pl.col("variable") == "precip_1h").sort("station_id", "valid_time")
    if p.height == 0:
        return pl.DataFrame(
            schema={
                "station_id": pl.Utf8,
                "valid_time": pl.Datetime("us", "UTC"),
                "variable": pl.Utf8,
                "obs": pl.Float64,
                "qc_flags": pl.Int32,
                "n_clean_hours": pl.Int32,
            }
        )

    clean = (pl.col("qc_flags") == 0) & pl.col("value").is_not_null()
    p = p.with_columns(
        pl.when(clean).then(pl.col("value")).otherwise(None).alias("_v_clean"),
        clean.cast(pl.Int32).alias("_is_clean"),
    )

    bit_anys = [
        ((pl.col("qc_flags") & bit) != 0).any().alias(f"_b{bit}")
        for bit in qc_bits.ALL_BITS.values()
    ]
    rolled = p.rolling(
        index_column="valid_time", period="24h", closed="right", group_by="station_id"
    ).agg(
        pl.col("_v_clean").sum().alias("obs"),
        pl.col("_is_clean").sum().alias("n_clean_hours"),
        *bit_anys,
    )

    window_flags = pl.lit(0, dtype=pl.Int32)
    for bit in qc_bits.ALL_BITS.values():
        window_flags = window_flags | pl.when(pl.col(f"_b{bit}")).then(bit).otherwise(0)

    return (
        rolled.with_columns(
            window_flags.cast(pl.Int32).alias("qc_flags"),
            pl.lit("precip_24h").alias("variable"),
            pl.col("n_clean_hours").cast(pl.Int32),
        )
        .filter(pl.col("n_clean_hours") >= min_clean_hours)
        .select("station_id", "valid_time", "variable", "obs", "qc_flags", "n_clean_hours")
    )


def build_fact(
    forecast_points: pl.DataFrame,
    obs_qc: pl.DataFrame,
    stations: pl.DataFrame,
    floors: pl.DataFrame | None = None,
    max_delta_z_m: float = 500.0,
    min_clean_hours: int = 22,
    ingest_version: str = "unversioned",
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Assemble FACT_V1 rows. Returns (fact, dropped) for runlog reconciliation.

    ``dropped`` accounts for forecast points that found no valid pair:
    obs_missing, precip_incomplete_or_missing, delta_z_exceeded.
    """
    dropped: dict[str, int] = {}
    n_in = forecast_points.height

    hourly_obs = obs_qc.filter(pl.col("variable").is_in(["t2m", "wind10m"])).select(
        "station_id", "valid_time", "variable",
        pl.col("value").alias("obs"), "qc_flags",
    )
    precip_obs = derive_precip_24h_obs(obs_qc, min_clean_hours).select(
        "station_id", "valid_time", "variable", "obs", "qc_flags"
    )
    all_obs = pl.concat([hourly_obs, precip_obs])

    paired = forecast_points.join(all_obs, on=_OBS_JOIN_COLS, how="left")

    obs_missing = paired.filter(pl.col("obs").is_null())
    dropped["obs_missing_or_incomplete"] = obs_missing.height
    paired = paired.filter(pl.col("obs").is_not_null())

    paired = paired.join(
        stations.select("station_id", "elev_station"), on="station_id", how="left"
    ).with_columns((pl.col("elev_station") - pl.col("grid_elev")).alias("delta_z"))

    exceeded = paired.filter(pl.col("delta_z").abs() > max_delta_z_m)
    dropped["delta_z_exceeded"] = exceeded.height
    paired = paired.filter(
        pl.col("delta_z").abs().le(max_delta_z_m) | pl.col("delta_z").is_null()
    )

    wind_factor = 1.0 + WIND_FACTOR_PER_M * (pl.col("delta_z") - WIND_ONSET_DELTA_Z_M).clip(
        0.0, (WIND_FACTOR_MAX - 1.0) / WIND_FACTOR_PER_M
    )
    paired = paired.with_columns(
        pl.when(pl.col("variable") == "t2m")
        .then(pl.col("value") - LAPSE_RATE_K_PER_M * pl.col("delta_z"))
        .when(pl.col("variable") == "wind10m")
        .then(pl.col("value") * wind_factor)
        .otherwise(None)
        .alias("fcst_elev_adj")
    )

    if floors is not None and floors.height > 0:
        paired = attach_repr_floor(paired, floors, stations)
    else:
        paired = paired.with_columns(pl.lit(None, dtype=pl.Float64).alias("repr_floor"))

    fact = paired.select(
        "station_id",
        "model",
        "variable",
        "init_time",
        "valid_time",
        "lead_hours",
        pl.col("value").alias("fcst_raw"),
        "fcst_elev_adj",
        "obs",
        "delta_z",
        "interp_method",
        "repr_floor",
        pl.col("qc_flags").cast(pl.Int32),
        pl.lit(ingest_version).alias("ingest_version"),
    ).sort("station_id", "valid_time")

    assert n_in == fact.height + sum(dropped.values()), "fact builder lost rows silently"
    return validate(fact, FACT_V1, "fact"), dropped
