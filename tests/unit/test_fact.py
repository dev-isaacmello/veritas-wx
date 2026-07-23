"""Fact table builder: elevation, precip completeness, drop accounting."""

import datetime as dt

import polars as pl
import pytest

from veritas_wx.contracts import FORECAST_POINTS_V1, OBS_QC_V1, STATIONS_V1, qc_bits
from veritas_wx.match.fact import build_fact, derive_precip_24h_obs
from veritas_wx.match.repr_floor import cell_of

UTC = dt.UTC
INIT = dt.datetime(2025, 8, 10, 0, tzinfo=UTC)
VT = INIT + dt.timedelta(hours=24)


def _stations() -> pl.DataFrame:
    base = {
        "network": "inmet", "name": "x", "uf": "RS", "koppen": "Cfa",
        "cross_ref": None, "status": "included", "exclusion_reason": None,
        "source_meta": "fixture", "ingest_version": "test", "elev_dem": None,
    }
    return pl.DataFrame(
        [
            {**base, "station_id": "inmet:A", "native_id": "A",
             "lat": -30.05, "lon": -51.05, "elev_station": 800.0},
            {**base, "station_id": "inmet:B", "native_id": "B",
             "lat": -30.30, "lon": -51.30, "elev_station": 10.0},
        ],
        schema=STATIONS_V1,
    )


def _fp(rows: list[dict]) -> pl.DataFrame:
    defaults = {
        "station_id": "inmet:A", "model": "gfs",
        "init_time": INIT, "valid_time": VT, "lead_hours": 24,
        "interp_method": "bilinear", "grid_lat": -30.0, "grid_lon": -51.0,
        "grid_elev": 1200.0, "ingest_version": "test",
    }
    return pl.DataFrame([{**defaults, **r} for r in rows], schema=FORECAST_POINTS_V1)


def _hourly_precip(station: str, n_hours: int, flagged: int = 0) -> list[dict]:
    """n_hours of 1.0 mm/h ending exactly at VT; first `flagged` hours RANGE-flagged."""
    rows = []
    for i in range(n_hours):
        t = VT - dt.timedelta(hours=n_hours - 1 - i)
        rows.append(
            {
                "station_id": station, "valid_time": t, "variable": "precip_1h",
                "value": 1.0, "source": "inmet", "source_qc_raw": None,
                "ingest_version": "test",
                "qc_flags": qc_bits.RANGE if i < flagged else 0,
            }
        )
    return rows


def _obs(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=OBS_QC_V1)


def test_derive_precip_24h_complete_window_by_hand():
    out = derive_precip_24h_obs(_obs(_hourly_precip("inmet:A", 24)))
    row = out.filter(pl.col("valid_time") == VT).to_dicts()[0]
    assert row["obs"] == pytest.approx(24.0)
    assert row["n_clean_hours"] == 24 and row["qc_flags"] == 0


def test_derive_precip_flagged_hours_excluded_but_visible():
    out = derive_precip_24h_obs(_obs(_hourly_precip("inmet:A", 24, flagged=2)))
    row = out.filter(pl.col("valid_time") == VT).to_dicts()[0]
    assert row["obs"] == pytest.approx(22.0)
    assert row["n_clean_hours"] == 22
    assert row["qc_flags"] & qc_bits.RANGE


def test_derive_precip_incomplete_window_not_emitted():
    out = derive_precip_24h_obs(_obs(_hourly_precip("inmet:A", 20)))
    assert out.filter(pl.col("valid_time") == VT).height == 0


def test_build_fact_end_to_end_accounting():
    fps = _fp(
        [
            {"variable": "t2m", "value": 290.0},
            {"variable": "wind10m", "value": 5.0},
            {"variable": "precip_24h", "value": 10.0},
            {"variable": "t2m", "value": 291.0, "station_id": "inmet:B"},
            {"variable": "t2m", "value": 292.0,
             "valid_time": VT + dt.timedelta(hours=6), "lead_hours": 30},
        ]
    )
    obs_rows = (
        _hourly_precip("inmet:A", 24)
        + [
            {"station_id": "inmet:A", "valid_time": VT, "variable": "t2m", "value": 291.0,
             "source": "inmet", "source_qc_raw": None, "ingest_version": "test", "qc_flags": 0},
            {"station_id": "inmet:A", "valid_time": VT, "variable": "wind10m", "value": 4.0,
             "source": "inmet", "source_qc_raw": None, "ingest_version": "test", "qc_flags": 0},
            {"station_id": "inmet:B", "valid_time": VT, "variable": "t2m", "value": 300.0,
             "source": "inmet", "source_qc_raw": None, "ingest_version": "test", "qc_flags": 0},
        ]
    )
    fact, dropped = build_fact(fps, _obs(obs_rows), _stations(), ingest_version="v-test")

    assert dropped["delta_z_exceeded"] == 1
    assert dropped["obs_missing_or_incomplete"] == 1
    assert fact.height == 3

    by_var = {r["variable"]: r for r in fact.to_dicts()}
    assert by_var["t2m"]["delta_z"] == pytest.approx(-400.0)
    assert by_var["t2m"]["fcst_elev_adj"] == pytest.approx(292.6)
    assert by_var["wind10m"]["fcst_elev_adj"] is None
    assert by_var["precip_24h"]["obs"] == pytest.approx(24.0)
    assert all(r["ingest_version"] == "v-test" for r in fact.to_dicts())
    assert by_var["t2m"]["repr_floor"] is None


def test_build_fact_attaches_repr_floor_when_available():
    fps = _fp([{"variable": "t2m", "value": 290.0}])
    obs_rows = [
        {"station_id": "inmet:A", "valid_time": VT, "variable": "t2m", "value": 291.0,
         "source": "inmet", "source_qc_raw": None, "ingest_version": "test", "qc_flags": 0}
    ]
    floors = (
        _stations()
        .filter(pl.col("station_id") == "inmet:A")
        .select(*cell_of())
        .with_columns(pl.lit("t2m").alias("variable"), pl.lit(0.5).alias("repr_floor"))
    )
    fact, _ = build_fact(fps, _obs(obs_rows), _stations(), floors=floors)
    assert fact.to_dicts()[0]["repr_floor"] == pytest.approx(0.5)
