"""Tests for the per-station dayofyear x hour climatology and SEEPS wet stats.

Ported behavior under test (WeatherBench 2, Apache 2.0): triangular window
weights, circular dayofyear wrap, day-366 fill from day 365, SEEPSThreshold
dry fraction and 2/3 wet quantile.
"""

import math
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from veritas_wx.analyze.climatology import (
    attach_climatology,
    station_climatology,
    station_wet_stats,
    window_weights,
)


def _utc(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


def _obs_frame(rows: list[tuple[str, datetime, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "station_id": [r[0] for r in rows],
            "valid_time": [r[1] for r in rows],
            "variable": [r[2] for r in rows],
            "value": [r[3] for r in rows],
        },
        schema_overrides={"valid_time": pl.Datetime(time_unit="us", time_zone="UTC")},
    )


def _daily_series(years: list[int], value_fn, hour: int = 12) -> pl.DataFrame:
    rows = []
    for year in years:
        day = date(year, 1, 1)
        while day.year == year:
            ts = _utc(day.year, day.month, day.day, hour)
            rows.append(("sta", ts, "t2m", value_fn(day)))
            day += timedelta(days=1)
    return _obs_frame(rows)


def test_window_weights_match_wb2_shape():
    """WB2 create_window_weights: triangular, zero endpoints, mean-normalized."""
    w = window_weights(5)
    assert len(w) == 5
    assert w[0] == 0.0 and w[-1] == 0.0
    assert w[2] == max(w)
    assert sum(w) / len(w) == pytest.approx(1.0)
    assert w[1] == pytest.approx(w[3])
    with pytest.raises(ValueError, match="odd"):
        window_weights(60)


def test_sine_cycle_recovered_two_years():
    """Two identical sinusoidal years: clim_mean tracks the cycle, std stays low.

    The 61-day triangular window attenuates an annual sinusoid by the factor
    2*(1 - cos(b))/b^2 with b = 2*pi*30/365 ~ 0.977, so amplitude-10 values
    are recovered within a few tenths.
    """

    def sine(day: date) -> float:
        doy = day.timetuple().tm_yday
        return 20.0 + 10.0 * math.sin(2.0 * math.pi * (doy - 1) / 365.0)

    obs = _daily_series([2021, 2022], sine)
    clim = station_climatology(obs, window_days=61, min_samples=30)

    assert clim["hour"].unique().to_list() == [12]
    joined = clim.filter(pl.col("dayofyear") <= 365).with_columns(
        (
            20.0
            + 10.0
            * (2.0 * math.pi * (pl.col("dayofyear") - 1) / 365.0).sin()
        ).alias("truth")
    )
    max_err = joined.select((pl.col("clim_mean") - pl.col("truth")).abs().max()).item()
    assert max_err < 0.5
    assert joined["clim_std"].max() < 3.0


def test_circular_wrap_dayofyear_1_uses_december():
    """Data only in December: day 1 still gets a climatology from the wrap."""
    rows = []
    for year in (2021, 2022):
        for d in range(1, 32):
            rows.append(("sta", _utc(year, 12, d), "t2m", 5.0))
    clim = station_climatology(_obs_frame(rows), window_days=61, min_samples=5)

    day1 = clim.filter(pl.col("dayofyear") == 1)
    assert day1.height == 1
    assert day1["clim_mean"].item() == pytest.approx(5.0)
    assert day1["clim_std"].item() == pytest.approx(0.0, abs=1e-6)
    assert clim.filter(pl.col("dayofyear") == 180).height == 0


def test_leap_year_day_366_has_climatology():
    """A leap plus a non-leap year: no crash, and day 366 carries a value.

    The non-leap 2021 contributes to day 366 through the WB2 fill (its Dec 31
    = day 365 is duplicated onto day 366), pooled with 2020's real Feb 29-
    shifted calendar.
    """
    obs = _daily_series([2020, 2021], lambda d: 7.0)
    clim = station_climatology(obs, window_days=61, min_samples=30)

    day366 = clim.filter(pl.col("dayofyear") == 366)
    assert day366.height == 1
    assert day366["clim_mean"].item() == pytest.approx(7.0)
    assert day366["n_samples"].item() >= 30
    assert clim["dayofyear"].max() == 366


def test_sparse_station_yields_null_climatology():
    """Three observations never reach min_samples: bins exist, values are null."""
    rows = [
        ("lonely", _utc(2023, 6, 1), "t2m", 25.0),
        ("lonely", _utc(2023, 6, 2), "t2m", 26.0),
        ("lonely", _utc(2023, 6, 3), "t2m", 27.0),
    ]
    clim = station_climatology(_obs_frame(rows), window_days=61, min_samples=30)
    assert clim.height > 0
    assert clim["clim_mean"].null_count() == clim.height
    assert clim["clim_std"].null_count() == clim.height
    assert clim["n_samples"].max() <= 3


def test_stats_selection_controls_columns():
    obs = _daily_series([2021], lambda d: 1.0)
    only_mean = station_climatology(obs, stats=("mean",), min_samples=5)
    assert "clim_mean" in only_mean.columns
    assert "clim_std" not in only_mean.columns
    with pytest.raises(ValueError, match="stats"):
        station_climatology(obs, stats=("median",))


def test_attach_climatology_joins_on_doy_hour():
    obs = _daily_series([2021, 2022], lambda d: 3.0)
    clim = station_climatology(obs, min_samples=10)
    target = _obs_frame(
        [
            ("sta", _utc(2023, 5, 10), "t2m", 99.0),
            ("ghost", _utc(2023, 5, 10), "t2m", 99.0),
        ]
    )
    out = attach_climatology(target, clim)
    assert out.height == 2
    assert out.filter(pl.col("station_id") == "sta")["clim_mean"].item() == pytest.approx(3.0)
    assert out.filter(pl.col("station_id") == "ghost")["clim_mean"].item() is None
    assert "_doy" not in out.columns and "_hour" not in out.columns


def _hourly_precip_day(station: str, day: date, total: float, n_hours: int = 24) -> list:
    """One day of hourly precip rows: the whole total at 00 UTC, zeros after."""
    rows = []
    for h in range(n_hours):
        value = total if h == 0 else 0.0
        rows.append((station, _utc(day.year, day.month, day.day, h), "precip_1h", value))
    return rows


def test_wet_stats_golden_hand_computed():
    """Six complete days with totals [0, 0.1, 1, 2, 4, 10] mm.

    Dry days (< 0.25 mm): {0.0, 0.1} => p1 = 2/6 = 1/3. Wet days sorted:
    [1, 2, 4, 10]; the 2/3 quantile with linear interpolation sits at index
    (4-1)*2/3 = 2.0 exactly => wet_threshold = 4.0. A seventh day with only
    3 hourly reports is dropped by the completeness rule and changes nothing.
    """
    totals = [0.0, 0.1, 1.0, 2.0, 4.0, 10.0]
    rows = []
    for i, total in enumerate(totals):
        rows.extend(_hourly_precip_day("sta", date(2023, 7, 1) + timedelta(days=i), total))
    rows.extend(_hourly_precip_day("sta", date(2023, 7, 7), 50.0, n_hours=3))

    out = station_wet_stats(_obs_frame(rows), min_days=1)
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["n_days"] == 6
    assert row["n_wet_days"] == 4
    assert row["p1_dry_fraction"] == pytest.approx(1.0 / 3.0)
    assert row["wet_threshold"] == pytest.approx(4.0)


def test_wet_stats_min_days_and_no_wet_days():
    """Below min_days everything is null; all-dry stations get null threshold."""
    few = []
    for i in range(3):
        few.extend(_hourly_precip_day("few", date(2023, 7, 1) + timedelta(days=i), 5.0))
    all_dry = []
    for i in range(40):
        all_dry.extend(_hourly_precip_day("dry", date(2023, 6, 1) + timedelta(days=i), 0.0))

    out = station_wet_stats(_obs_frame(few + all_dry), min_days=30)
    few_row = out.filter(pl.col("station_id") == "few").row(0, named=True)
    assert few_row["p1_dry_fraction"] is None and few_row["wet_threshold"] is None
    dry_row = out.filter(pl.col("station_id") == "dry").row(0, named=True)
    assert dry_row["p1_dry_fraction"] == pytest.approx(1.0)
    assert dry_row["wet_threshold"] is None
    assert dry_row["n_wet_days"] == 0
