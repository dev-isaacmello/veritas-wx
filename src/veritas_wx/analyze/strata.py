"""Stratum joins for regime-stratified analysis (registry ``strata`` section).

Pure polars: expressions and left joins only. A pair whose stratum is unknown
gets a null stratum value — it is never dropped here (row counts are the
concern of the caller's runlog) and never imputed.
"""

import polars as pl

ONI_THRESHOLD = 0.5

MJO_MIN_AMPLITUDE = 1.0


def season_of(valid_time: str | pl.Expr = "valid_time") -> pl.Expr:
    """Meteorological season (DJF/MAM/JJA/SON) of a datetime column.

    Accepts a column name or expression; returns a Utf8 expression aliased
    ``season``. Months {12,1,2} => DJF, {3,4,5} => MAM, {6,7,8} => JJA,
    {9,10,11} => SON (registry: ``strata.season``).
    """
    expr = pl.col(valid_time) if isinstance(valid_time, str) else valid_time
    month = expr.dt.month()
    return (
        pl.when(month.is_in([12, 1, 2]))
        .then(pl.lit("DJF"))
        .when(month.is_in([3, 4, 5]))
        .then(pl.lit("MAM"))
        .when(month.is_in([6, 7, 8]))
        .then(pl.lit("JJA"))
        .otherwise(pl.lit("SON"))
        .alias("season")
    )


def join_koppen(df: pl.DataFrame, stations_df: pl.DataFrame) -> pl.DataFrame:
    """Attach ``koppen`` and ``koppen_level1`` via left join on ``station_id``.

    ``stations_df`` follows STATIONS_V1 (needs ``station_id`` and ``koppen``).
    ``koppen_level1`` is the first letter of the full class ("Aw" => "A"),
    matching the registry stratum ``koppen_level1: [A, B, C]``. Stations
    absent from ``stations_df`` (or with null ``koppen``) yield nulls; the
    left join preserves every input row.
    """
    lookup = stations_df.select("station_id", "koppen")
    return df.join(lookup, on="station_id", how="left").with_columns(
        pl.col("koppen").str.slice(0, 1).alias("koppen_level1")
    )


def join_enso(df: pl.DataFrame, oni_df: pl.DataFrame) -> pl.DataFrame:
    """Attach the monthly ENSO phase from the ONI (CPC), registry thresholds.

    ``oni_df`` is the tiny monthly table ``(year: i32, month: i8/i32,
    oni: f64)``. The join key is (year, month) of ``valid_time``. Phase
    (registry ``strata.enso``)::

        el_nino:  oni >= 0.5
        neutral:  -0.5 < oni < 0.5
        la_nina:  oni <= -0.5

    Months missing from ``oni_df`` get null ``oni`` and null ``enso_phase``.
    """
    lookup = oni_df.select(
        pl.col("year").cast(pl.Int32),
        pl.col("month").cast(pl.Int8),
        pl.col("oni").cast(pl.Float64),
    )
    return (
        df.with_columns(
            pl.col("valid_time").dt.year().cast(pl.Int32).alias("_year"),
            pl.col("valid_time").dt.month().cast(pl.Int8).alias("_month"),
        )
        .join(lookup, left_on=["_year", "_month"], right_on=["year", "month"], how="left")
        .with_columns(
            pl.when(pl.col("oni") >= ONI_THRESHOLD)
            .then(pl.lit("el_nino"))
            .when(pl.col("oni") <= -ONI_THRESHOLD)
            .then(pl.lit("la_nina"))
            .when(pl.col("oni").is_not_null())
            .then(pl.lit("neutral"))
            .otherwise(None)
            .alias("enso_phase")
        )
        .drop("_year", "_month")
    )


def join_mjo(df: pl.DataFrame, rmm_df: pl.DataFrame) -> pl.DataFrame:
    """Attach the daily MJO phase from the RMM index (BoM), registry rule.

    ``rmm_df`` is the daily table ``(date: pl.Date, phase: int 1-8,
    amplitude: f64)``. Joined on the DATE of ``valid_time``. Registry
    ``strata.mjo_phase``: the phase label is ``str(phase)`` ("1".."8") when
    ``amplitude >= 1``, else ``"inactive"``. Days missing from ``rmm_df``
    yield null ``mjo_phase``.
    """
    lookup = rmm_df.select(
        pl.col("date").cast(pl.Date),
        pl.col("phase").cast(pl.Int32),
        pl.col("amplitude").cast(pl.Float64),
    )
    return (
        df.with_columns(pl.col("valid_time").dt.date().alias("_date"))
        .join(lookup, left_on="_date", right_on="date", how="left")
        .with_columns(
            pl.when(pl.col("amplitude") >= MJO_MIN_AMPLITUDE)
            .then(pl.col("phase").cast(pl.Utf8))
            .when(pl.col("amplitude").is_not_null())
            .then(pl.lit("inactive"))
            .otherwise(None)
            .alias("mjo_phase")
        )
        .drop("_date", "phase", "amplitude")
    )


def obs_percentile(df: pl.DataFrame) -> pl.DataFrame:
    """Empirical percentile of ``obs`` within (station, variable), full window.

    Registry ``strata.obs_percentile_bin``: for each (station_id, variable)
    group of the FULL analysis window, ``obs_pct = rank(obs) / n * 100`` with
    average ranks for ties, giving values in (0, 100]. The maximum
    observation of a group sits at exactly 100 and therefore falls in the
    closed top bin "99-100"; a value at the 10th percentile falls in
    "10-20" (bins are half-open [lo, hi) below the top). Adds the ``obs_pct``
    column; row order and count are preserved.
    """
    group = ["station_id", "variable"] if "variable" in df.columns else ["station_id"]
    return df.with_columns(
        (pl.col("obs").rank(method="average").over(group) / pl.len().over(group) * 100.0).alias(
            "obs_pct"
        )
    )
