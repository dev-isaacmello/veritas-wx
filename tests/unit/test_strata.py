"""Goldens for stratum expressions and joins (season, Köppen, ENSO, MJO, obs_pct)."""

from datetime import UTC, date, datetime

import polars as pl
import pytest

from veritas_wx.analyze.strata import (
    join_enso,
    join_koppen,
    join_mjo,
    obs_percentile,
    season_of,
)


def _ts(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=UTC)


def test_season_golden():
    df = pl.DataFrame(
        {
            "valid_time": [
                _ts(2026, 1, 15),  # DJF
                _ts(2026, 2, 28),  # DJF
                _ts(2025, 12, 1),  # DJF
                _ts(2026, 4, 10),  # MAM
                _ts(2025, 7, 4),  # JJA
                _ts(2025, 10, 31),  # SON
            ]
        },
        schema={"valid_time": pl.Datetime("us", "UTC")},
    )
    out = df.with_columns(season_of("valid_time"))
    assert out["season"].to_list() == ["DJF", "DJF", "DJF", "MAM", "JJA", "SON"]


def test_season_accepts_expression():
    df = pl.DataFrame(
        {"t": [_ts(2025, 9, 1)]}, schema={"t": pl.Datetime("us", "UTC")}
    )
    assert df.select(season_of(pl.col("t")))["season"].item() == "SON"


def test_obs_percentile_series_1_to_100():
    """obs = 1..100 in one (station, variable) group => obs_pct = rank/n*100 = value."""
    df = pl.DataFrame(
        {
            "station_id": ["s"] * 100,
            "variable": ["t2m"] * 100,
            "obs": [float(v) for v in range(1, 101)],
        }
    )
    out = obs_percentile(df)
    assert out.height == 100  # no rows gained or lost
    assert out["obs_pct"].to_list() == pytest.approx([float(v) for v in range(1, 101)])
    # extremes: min -> 1.0 (not 0), max -> exactly 100 (falls in closed bin 99-100)
    assert out["obs_pct"].min() == pytest.approx(1.0)
    assert out["obs_pct"].max() == pytest.approx(100.0)


def test_obs_percentile_is_per_station_and_variable():
    df = pl.DataFrame(
        {
            "station_id": ["a", "a", "b", "b"],
            "variable": ["t2m"] * 4,
            "obs": [1.0, 2.0, 100.0, 200.0],
        }
    )
    out = obs_percentile(df)
    # each group of 2: ranks 1,2 => 50, 100 — independent of the other station's scale
    assert out["obs_pct"].to_list() == pytest.approx([50.0, 100.0, 50.0, 100.0])


def test_obs_percentile_ties_get_average_rank():
    df = pl.DataFrame(
        {"station_id": ["s"] * 4, "variable": ["t2m"] * 4, "obs": [1.0, 2.0, 2.0, 3.0]}
    )
    out = obs_percentile(df)
    # ranks: 1, 2.5, 2.5, 4 over n=4 => 25, 62.5, 62.5, 100
    assert out["obs_pct"].to_list() == pytest.approx([25.0, 62.5, 62.5, 100.0])


def test_join_koppen_level1():
    df = pl.DataFrame({"station_id": ["inmet:A001", "isd:123", "inmet:X999"]})
    stations = pl.DataFrame(
        {
            "station_id": ["inmet:A001", "isd:123"],
            "koppen": ["Aw", "Cfb"],
        }
    )
    out = join_koppen(df, stations)
    assert out.height == 3  # left join: nothing dropped
    assert out["koppen"].to_list() == ["Aw", "Cfb", None]
    assert out["koppen_level1"].to_list() == ["A", "C", None]


def test_join_enso_thresholds():
    """Registry: el_nino oni >= 0.5; la_nina oni <= -0.5; neutral in between."""
    df = pl.DataFrame(
        {
            "valid_time": [
                _ts(2025, 7, 1),
                _ts(2025, 8, 1),
                _ts(2025, 9, 1),
                _ts(2025, 10, 1),
                _ts(2025, 11, 1),
            ]
        },
        schema={"valid_time": pl.Datetime("us", "UTC")},
    )
    oni = pl.DataFrame(
        {
            "year": [2025, 2025, 2025, 2025],
            "month": [7, 8, 9, 10],
            "oni": [0.5, -0.5, 0.49, -0.49],
        }
    )
    out = join_enso(df, oni)
    assert out.height == 5
    assert out["enso_phase"].to_list() == [
        "el_nino",  # exactly 0.5 => el_nino (>=)
        "la_nina",  # exactly -0.5 => la_nina (<=)
        "neutral",
        "neutral",
        None,  # month absent from the ONI table
    ]


def test_join_mjo_phase_and_inactive():
    df = pl.DataFrame(
        {
            "valid_time": [_ts(2025, 7, 1), _ts(2025, 7, 2), _ts(2025, 7, 3)],
        },
        schema={"valid_time": pl.Datetime("us", "UTC")},
    )
    rmm = pl.DataFrame(
        {
            "date": [date(2025, 7, 1), date(2025, 7, 2)],
            "phase": [3, 8],
            "amplitude": [1.0, 0.99],
        }
    )
    out = join_mjo(df, rmm)
    assert out.height == 3
    # amplitude >= 1 => phase label; < 1 => inactive; missing day => null
    assert out["mjo_phase"].to_list() == ["3", "inactive", None]
