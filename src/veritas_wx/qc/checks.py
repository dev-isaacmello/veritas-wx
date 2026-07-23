"""Independent, composable, pure QC checks. Flag, never delete (anti-pattern guard).

Every check: (df, params, ...) -> df with the SAME height, OR-ing exactly one
bit into ``qc_flags``. Checks assume df sorted by (station_id, variable,
valid_time) — the runner enforces this once. All thresholds come from
configs/qc_params.yaml; nothing is hardcoded.
"""

import polars as pl

from veritas_wx.contracts import qc_bits

_EPS = 1e-9
_MAD_TO_SIGMA = 1.4826  # consistent estimator of sigma under normality


def _or_bit(df: pl.DataFrame, mask: pl.Expr, bit: int) -> pl.DataFrame:
    """OR ``bit`` into qc_flags where ``mask`` is true (null-safe: null -> no flag)."""
    return df.with_columns(
        pl.when(mask.fill_null(False))
        .then(pl.col("qc_flags") | pl.lit(bit, dtype=pl.Int32))
        .otherwise(pl.col("qc_flags"))
        .alias("qc_flags")
    )


def range_check(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """RANGE: value outside physical plausibility bounds for its variable."""
    bounds = params["range"]
    mask = pl.lit(False)
    for variable, b in bounds.items():
        mask = mask | (
            (pl.col("variable") == variable)
            & ((pl.col("value") < b["min"]) | (pl.col("value") > b["max"]))
        )
    return _or_bit(df, mask, qc_bits.RANGE)


def step_check(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """STEP: implausible change between CONSECUTIVE-HOUR readings.

    Only flags when the previous reading of the same (station, variable) is
    exactly 1 h earlier — a jump across a data gap is not evidence of a bad
    sensor. The later reading of the pair is flagged.
    """
    over = ["station_id", "variable"]
    prev_val = pl.col("value").shift(1).over(over)
    prev_time = pl.col("valid_time").shift(1).over(over)
    is_consecutive = (pl.col("valid_time") - prev_time) == pl.duration(hours=1)

    mask = pl.lit(False)
    for variable, max_step in params["step"].items():
        mask = mask | (
            (pl.col("variable") == variable)
            & is_consecutive
            & ((pl.col("value") - prev_val).abs() > max_step)
        )
    return _or_bit(df, mask, qc_bits.STEP)


def persistence_check(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """PERSISTENCE: identical value repeated >= min_repeats consecutive readings.

    Exemptions (physically normal persistence, NOT stuck sensors):
      - wind10m below the calm threshold (prolonged calm is real weather)
      - precip_1h == 0 when precip_zero_exempt (dry spells are the normal state)
    All members of a qualifying run are flagged.
    """
    p = params["persistence"]
    min_repeats = p["min_repeats_hours"]
    over = ["station_id", "variable"]

    # A run breaks when the value changes OR the time series has a gap (> 1 h):
    # a stuck sensor reports continuously; identical values across a gap are
    # separate episodes, not one long run.
    value_changed = (pl.col("value") != pl.col("value").shift(1).over(over)).fill_null(True)
    gapped = (
        (pl.col("valid_time") - pl.col("valid_time").shift(1).over(over)) != pl.duration(hours=1)
    ).fill_null(True)
    run_start = value_changed | gapped

    out = df.with_columns(run_start.cast(pl.Int32).cum_sum().over(over).alias("_run_id"))
    out = out.with_columns(pl.len().over(["station_id", "variable", "_run_id"]).alias("_run_len"))

    exempt = (
        (pl.col("variable") == "wind10m") & (pl.col("value") < p["wind_calm_exempt_below"])
    ) | (
        pl.lit(bool(p.get("precip_zero_exempt", True)))
        & (pl.col("variable") == "precip_1h")
        & (pl.col("value") == 0.0)
    )
    mask = (pl.col("_run_len") >= min_repeats) & pl.col("value").is_not_null() & ~exempt
    return _or_bit(out, mask, qc_bits.PERSISTENCE).drop("_run_id", "_run_len")


def spatial_check(df: pl.DataFrame, params: dict, neighbor_pairs: pl.DataFrame) -> pl.DataFrame:
    """SPATIAL: robust deviation vs neighboring stations at the same instant.

    ``neighbor_pairs`` (station_id, neighbor_id) is precomputed from curated
    station coordinates (k nearest within radius_km) — passed in to keep this
    function pure. Flags |value - median(neighbors)| / max(1.4826*MAD, floor)
    > max_mad_z, requiring >= 3 reporting neighbors; fewer neighbors -> no
    evidence, no flag.

    Calibration (ADR-0003): with k<=5 neighbors reporting at 0.1 resolution
    the MAD collapses to ~0 and the z-score explodes on perfectly normal
    readings, so each variable carries a ``sigma_floor`` (a lower bound on
    the robust sigma). Variables in ``exempt_variables`` are never flagged —
    hourly convective precipitation is spatially spotty by nature, and
    flagging real peaks would bias verification against extreme events.
    """
    z_max = params["spatial"]["max_mad_z"]
    floors: dict[str, float] = params["spatial"].get("sigma_floor", {})
    exempt: list[str] = params["spatial"].get("exempt_variables", [])

    neighbor_obs = df.select(
        pl.col("station_id").alias("neighbor_id"),
        "variable",
        "valid_time",
        pl.col("value").alias("nb_value"),
    )
    nb = (
        neighbor_pairs.join(neighbor_obs, on="neighbor_id", how="inner")
        .group_by(["station_id", "variable", "valid_time"])
        .agg(
            pl.col("nb_value").median().alias("_nb_med"),
            (pl.col("nb_value") - pl.col("nb_value").median()).abs().median().alias("_nb_mad"),
            pl.col("nb_value").count().alias("_nb_n"),
        )
    )
    out = df.join(nb, on=["station_id", "variable", "valid_time"], how="left")

    floor_expr = pl.lit(0.0)
    for variable, floor in floors.items():
        floor_expr = (
            pl.when(pl.col("variable") == variable).then(pl.lit(floor)).otherwise(floor_expr)
        )
    robust_sigma = pl.max_horizontal(_MAD_TO_SIGMA * pl.col("_nb_mad"), floor_expr) + _EPS
    robust_z = (pl.col("value") - pl.col("_nb_med")).abs() / robust_sigma
    mask = (
        (pl.col("_nb_n") >= 3) & (robust_z > z_max) & ~pl.col("variable").is_in(exempt)
    )
    return _or_bit(out, mask, qc_bits.SPATIAL).drop("_nb_med", "_nb_mad", "_nb_n")


def metadata_check(df: pl.DataFrame, params: dict, stations: pl.DataFrame) -> pl.DataFrame:
    """METADATA: station coordinates/elevation missing or contradicted by DEM.

    Station-level evidence propagates to every observation of that station.
    """
    max_diff = params["metadata"]["max_elev_diff_m"]
    suspect = stations.select(
        "station_id",
        (
            pl.col("lat").is_null()
            | pl.col("lon").is_null()
            | pl.col("elev_station").is_null()
            | (
                pl.col("elev_dem").is_not_null()
                & ((pl.col("elev_station") - pl.col("elev_dem")).abs() > max_diff)
            )
        ).alias("_meta_suspect"),
    )
    out = df.join(suspect, on="station_id", how="left")
    return _or_bit(out, pl.col("_meta_suspect"), qc_bits.METADATA).drop("_meta_suspect")


def duplicate_check(df: pl.DataFrame, params: dict, stations: pl.DataFrame) -> pl.DataFrame:
    """DUPLICATE: same physical reading arriving via two networks.

    Uses curation cross_ref (same physical site in the other network). The
    NON-primary record (network != inmet) is flagged when values agree within
    tolerance at the same (variable, valid_time). The primary stays clean —
    the information is kept once.
    """
    tol = params["duplicate"]["value_tolerance"]
    xref = stations.filter(
        (pl.col("network") != "inmet") & pl.col("cross_ref").is_not_null()
    ).select("station_id", pl.col("cross_ref").alias("_primary_id"))

    primary_obs = df.select(
        pl.col("station_id").alias("_primary_id"),
        "variable",
        "valid_time",
        pl.col("value").alias("_primary_value"),
    ).unique(subset=["_primary_id", "variable", "valid_time"], keep="first")
    out = (
        df.join(xref, on="station_id", how="left")
        .join(primary_obs, on=["_primary_id", "variable", "valid_time"], how="left")
    )
    mask = (
        pl.col("_primary_id").is_not_null()
        & pl.col("_primary_value").is_not_null()
        & ((pl.col("value") - pl.col("_primary_value")).abs() <= tol)
    )
    return _or_bit(out, mask, qc_bits.DUPLICATE).drop("_primary_id", "_primary_value")
