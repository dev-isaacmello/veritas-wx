"""Per-station climatology on a (dayofyear x hour) grid, and SEEPS wet-day stats.

EXPLORATORY DIAGNOSTICS — none of this is part of the frozen pre-registration
(metrics_registry.yaml is untouched). These climatological fields feed
anomaly/ACC-style diagnostics and the SEEPS score, all reported outside the
pre-registered confirmatory family.

Portions derived from WeatherBench 2 (Copyright 2023 Google LLC, Apache
License 2.0), adapted from ``weatherbench2/utils.py``: triangular window
weights (``create_window_weights``), the weighted circular rolling statistic
over dayofyear with year stacking and the day-366 fill-from-day-365 strategy
(``compute_rolling_stat``, ``compute_hourly_stat``); and from
``scripts/compute_climatology.py``: the ``SEEPSThreshold`` dry-fraction /
wet-quantile computation.

Differences from the WB2 originals, on purpose:

- polars long format (``station_id, valid_time, variable, value``) instead of
  gridded xarray; the rolling window is realized by replicating each
  observation across its window offsets and grouping by target dayofyear.
- Units stay in native OBS_V1 units (precip in mm); WB2 converts to meters.
- Bins supported by fewer than ``min_samples`` observations get NULL
  climatology values — recorded as absent, never invented.

All functions are pure: DataFrames in, DataFrames out, no I/O.
"""

import polars as pl

DAYS_IN_LEAP_YEAR = 366


def window_weights(window_days: int) -> list[float]:
    """Triangular (linearly decaying) window weights, WB2-normalized.

    Port of WB2 ``create_window_weights``: for an odd ``window_days`` the
    weights rise linearly 0 -> 1 over the first half and fall 1 -> 0 over the
    second, then are divided by their mean. The two endpoint weights are
    exactly zero, so the effective support is ``window_days - 2`` days.
    Returned as a plain list of length ``window_days`` indexed by
    ``offset + window_days // 2``.
    """
    if window_days % 2 != 1:
        raise ValueError(f"window_days must be odd, got {window_days}")
    if not (3 <= window_days <= 365):
        raise ValueError(f"window_days must be in [3, 365], got {window_days}")
    half = window_days // 2
    up = [i / half for i in range(half + 1)]
    down = [1.0 - (i / half) for i in range(1, half + 1)]
    raw = up + down
    mean = sum(raw) / len(raw)
    return [w / mean for w in raw]


def _is_leap_year(year: pl.Expr) -> pl.Expr:
    """Gregorian leap-year rule as a polars boolean expression."""
    return (year % 4 == 0) & ((year % 100 != 0) | (year % 400 == 0))


def _with_doy_hour(df: pl.DataFrame) -> pl.DataFrame:
    """Attach ``_doy`` (1..366, Int16) and ``_hour`` (0..23, Int8) from ``valid_time``."""
    return df.with_columns(
        pl.col("valid_time").dt.ordinal_day().cast(pl.Int16).alias("_doy"),
        pl.col("valid_time").dt.hour().cast(pl.Int8).alias("_hour"),
    )


def station_climatology(
    obs: pl.DataFrame,
    window_days: int = 61,
    stats: tuple[str, ...] = ("mean", "std"),
    min_samples: int = 30,
) -> pl.DataFrame:
    """Weighted circular rolling climatology per (station, variable, dayofyear, hour).

    Faithful long-format port of WB2 ``compute_hourly_stat``: observations are
    pooled across years onto a 366-day circular calendar, and each
    (dayofyear, hour) bin aggregates every observation within
    ``window_days // 2`` days of it (wrapping across the year boundary) with
    triangular weights peaking at the bin's own day.

    Day 366 (WB2 ``fillna(dayofyear=365)`` strategy): non-leap years have no
    day 366, so their day-365 observations are duplicated onto day 366 before
    windowing — exactly what WB2's fill does on the stacked (year, dayofyear)
    array. Windows spanning both days therefore count those observations
    twice, as in WB2. Day 366 always receives a climatology wherever its
    window is populated.

    The weighted mean is ``sum(w*x)/sum(w)`` and the weighted std is the
    biased ``sqrt(sum(w*(x - wmean)^2)/sum(w))``, matching xarray's
    ``.weighted().mean()/.std()`` used by WB2. Hours are taken as-is from
    ``valid_time`` (OBS_V1 timestamps are top-of-hour UTC); each hour of day
    forms its own bin family, never mixed.

    Bins supported by fewer than ``min_samples`` contributing observations
    (zero-weight window endpoints do not contribute) get NULL ``clim_mean`` /
    ``clim_std`` — a station with 3 observations produces no climatology,
    it is never invented from thin air.

    Parameters
    ----------
    obs:
        OBS_V1/OBS_QC_V1-shaped frame; needs ``station_id, valid_time,
        variable, value``. Null values are dropped.
    window_days:
        Odd circular window width in days (default 61, as WB2).
    stats:
        Subset of ``("mean", "std")`` selecting the output columns.
    min_samples:
        Minimum contributing observations per bin for a non-null value.

    Returns
    -------
    One row per populated (station_id, variable, dayofyear, hour) bin with
    ``clim_mean`` and/or ``clim_std`` plus ``n_samples``, sorted by key.
    """
    allowed = {"mean", "std"}
    if not stats or not set(stats) <= allowed:
        raise ValueError(f"stats must be a non-empty subset of {allowed}, got {stats!r}")

    weights = window_weights(window_days)
    half = window_days // 2
    offsets = pl.DataFrame(
        {
            "_offset": [k for k in range(-half, half + 1) if weights[k + half] > 0.0],
            "_weight": [w for w in weights if w > 0.0],
        }
    )

    base = _with_doy_hour(obs.filter(pl.col("value").is_not_null()))
    day366_fill = base.filter(
        (pl.col("_doy") == 365) & ~_is_leap_year(pl.col("valid_time").dt.year())
    ).with_columns(pl.lit(366, dtype=pl.Int16).alias("_doy"))
    base = pl.concat([base, day366_fill]).select(
        "station_id", "variable", "value", "_doy", "_hour"
    )

    binned = (
        base.join(offsets, how="cross")
        .with_columns(
            ((pl.col("_doy") - 1 + pl.col("_offset") + DAYS_IN_LEAP_YEAR) % DAYS_IN_LEAP_YEAR + 1)
            .cast(pl.Int16)
            .alias("dayofyear")
        )
        .group_by("station_id", "variable", "dayofyear", pl.col("_hour").alias("hour"))
        .agg(
            pl.len().cast(pl.Int64).alias("n_samples"),
            pl.col("_weight").sum().alias("_sw"),
            (pl.col("_weight") * pl.col("value")).sum().alias("_swx"),
            (pl.col("_weight") * pl.col("value") ** 2).sum().alias("_swx2"),
        )
    )

    wmean = pl.col("_swx") / pl.col("_sw")
    wvar = (pl.col("_swx2") / pl.col("_sw") - wmean**2).clip(lower_bound=0.0)
    enough = pl.col("n_samples") >= min_samples
    stat_cols = []
    stat_names = []
    if "mean" in stats:
        stat_cols.append(pl.when(enough).then(wmean).otherwise(None).alias("clim_mean"))
        stat_names.append("clim_mean")
    if "std" in stats:
        stat_cols.append(pl.when(enough).then(wvar.sqrt()).otherwise(None).alias("clim_std"))
        stat_names.append("clim_std")

    return (
        binned.with_columns(stat_cols)
        .select("station_id", "variable", "dayofyear", "hour", *stat_names, "n_samples")
        .sort("station_id", "variable", "dayofyear", "hour")
    )


def station_wet_stats(
    obs: pl.DataFrame,
    dry_threshold_mm: float = 0.25,
    variable: str = "precip_1h",
    min_hours_per_day: int = 24,
    min_days: int = 30,
) -> pl.DataFrame:
    """Per-station SEEPS climatological parameters from 24 h precipitation.

    Port of WB2 ``SEEPSThreshold`` (scripts/compute_climatology.py): the dry
    probability is the fraction of DRY days (``total < dry_threshold_mm``,
    strict, as WB2's ``is_dry = ds < threshold``) and the wet threshold is the
    2/3 quantile (linear interpolation, matching xarray's default) of the WET
    days (``total >= dry_threshold_mm``).

    Aggregation decision: SEEPS is defined on 24 h accumulations (Rodwell
    et al. 2010; the veritas-wx forecast side is ``precip_24h``), so hourly
    ``precip_1h`` observations are first summed into calendar-day (UTC)
    totals. Computing the stats on raw hourly values would inflate the dry
    fraction (most hours are dry even in wet climates) and mismatch the
    forecast accumulation. Days with fewer than ``min_hours_per_day`` hourly
    reports are dropped entirely — a partial-day sum would understate the
    accumulation.

    Stations with fewer than ``min_days`` complete days get NULL
    ``p1_dry_fraction`` and ``wet_threshold``; a station with no wet days at
    all gets a NULL ``wet_threshold``. Absence over invention, always.

    Returns
    -------
    One row per station: ``(station_id, p1_dry_fraction, wet_threshold,
    n_days, n_wet_days)``, thresholds in mm/24h, sorted by ``station_id``.
    """
    daily = (
        obs.filter((pl.col("variable") == variable) & pl.col("value").is_not_null())
        .group_by("station_id", pl.col("valid_time").dt.date().alias("_date"))
        .agg(pl.len().alias("_n_hours"), pl.col("value").sum().alias("_total"))
        .filter(pl.col("_n_hours") >= min_hours_per_day)
    )
    wet = pl.col("_total") >= dry_threshold_mm
    per_station = daily.group_by("station_id").agg(
        pl.len().cast(pl.Int64).alias("n_days"),
        (pl.col("_total") < dry_threshold_mm).mean().alias("_p1"),
        pl.col("_total").filter(wet).quantile(2.0 / 3.0, interpolation="linear").alias("_wet"),
        wet.sum().cast(pl.Int64).alias("n_wet_days"),
    )
    enough = pl.col("n_days") >= min_days
    return per_station.select(
        "station_id",
        pl.when(enough).then(pl.col("_p1")).otherwise(None).alias("p1_dry_fraction"),
        pl.when(enough).then(pl.col("_wet")).otherwise(None).alias("wet_threshold"),
        "n_days",
        "n_wet_days",
    ).sort("station_id")


def attach_climatology(df: pl.DataFrame, clim: pl.DataFrame) -> pl.DataFrame:
    """Left-join climatology values onto any frame carrying ``valid_time``.

    Derives ``(dayofyear, hour)`` from ``valid_time`` with the same
    convention as :func:`station_climatology` (Dec 31 of a leap year is day
    366, which the climatology covers via the WB2 day-365 fill) and
    left-joins ``clim`` on ``(station_id, variable, dayofyear, hour)``,
    attaching whichever of ``clim_mean`` / ``clim_std`` the climatology
    carries. Rows without a matching (or sufficiently sampled) bin get
    nulls; every input row is preserved, in order. Intended for anomaly and
    ACC-style diagnostics downstream.
    """
    value_cols = [c for c in ("clim_mean", "clim_std") if c in clim.columns]
    if not value_cols:
        raise ValueError(
            f"clim has neither clim_mean nor clim_std (columns: {clim.columns}); "
            "did station_climatology run with an empty stats tuple?"
        )
    lookup = clim.select("station_id", "variable", "dayofyear", "hour", *value_cols)
    return (
        _with_doy_hour(df)
        .join(
            lookup,
            left_on=["station_id", "variable", "_doy", "_hour"],
            right_on=["station_id", "variable", "dayofyear", "hour"],
            how="left",
        )
        .drop("_doy", "_hour")
    )
