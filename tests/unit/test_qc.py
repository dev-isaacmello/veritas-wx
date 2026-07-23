"""Hand-built golden cases for every QC check. Flags, never deletions."""

import datetime as dt

import polars as pl
import yaml

from veritas_wx.contracts import OBS_QC_V1, STATIONS_V1, qc_bits, validate
from veritas_wx.qc import checks
from veritas_wx.qc.runner import run_qc

UTC = dt.UTC
T0 = dt.datetime(2025, 8, 10, 0, tzinfo=UTC)


def _params() -> dict:
    with open("configs/qc_params.yaml") as fh:
        return yaml.safe_load(fh)


def _obs(rows: list[dict]) -> pl.DataFrame:
    defaults = {
        "source": "inmet",
        "source_qc_raw": None,
        "ingest_version": "test",
        "qc_flags": 0,
    }
    full = [{**defaults, **r} for r in rows]
    return pl.DataFrame(full, schema=OBS_QC_V1).sort("station_id", "variable", "valid_time")


def _stations(rows: list[dict]) -> pl.DataFrame:
    defaults = {
        "network": "inmet",
        "native_id": "X",
        "name": "test",
        "uf": "RS",
        "lat": -30.0,
        "lon": -51.0,
        "elev_station": 100.0,
        "elev_dem": 100.0,
        "koppen": "Cfa",
        "cross_ref": None,
        "status": "included",
        "exclusion_reason": None,
        "source_meta": "fixture",
        "ingest_version": "test",
    }
    return pl.DataFrame([{**defaults, **r} for r in rows], schema=STATIONS_V1)


def _flagged(df: pl.DataFrame, bit: int) -> list[bool]:
    return [(f & bit) != 0 for f in df["qc_flags"].to_list()]


# ---------------------------------------------------------------- RANGE

def test_range_flags_physical_impossibility():
    df = _obs(
        [
            {"station_id": "inmet:A1", "variable": "t2m", "valid_time": T0, "value": 400.0},
            {"station_id": "inmet:A1", "variable": "t2m", "valid_time": T0, "value": 298.15},
            {"station_id": "inmet:A1", "variable": "wind10m", "valid_time": T0, "value": -5.0},
            {"station_id": "inmet:A1", "variable": "precip_1h", "valid_time": T0, "value": 250.0},
        ]
    )
    out = checks.range_check(df, _params())
    by_val = dict(zip(out["value"].to_list(), _flagged(out, qc_bits.RANGE), strict=True))
    assert by_val[400.0] and by_val[-5.0] and by_val[250.0]
    assert not by_val[298.15]
    assert out.height == df.height  # never deletes


# ---------------------------------------------------------------- STEP

def test_step_flags_only_consecutive_hours():
    rows = [
        # 10 K jump between consecutive hours -> the LATER reading is flagged
        {"station_id": "inmet:A1", "variable": "t2m", "valid_time": T0, "value": 295.0},
        {"station_id": "inmet:A1", "variable": "t2m",
         "valid_time": T0 + dt.timedelta(hours=1), "value": 305.0},
        # same jump across a 6 h gap -> no evidence, no flag
        {"station_id": "inmet:A2", "variable": "t2m", "valid_time": T0, "value": 295.0},
        {"station_id": "inmet:A2", "variable": "t2m",
         "valid_time": T0 + dt.timedelta(hours=6), "value": 305.0},
    ]
    out = checks.step_check(_obs(rows), _params())
    a1 = out.filter(pl.col("station_id") == "inmet:A1")
    a2 = out.filter(pl.col("station_id") == "inmet:A2")
    assert _flagged(a1, qc_bits.STEP) == [False, True]
    assert _flagged(a2, qc_bits.STEP) == [False, False]


# ---------------------------------------------------------------- PERSISTENCE

def _hourly(station: str, variable: str, values: list[float], gap_at: int | None = None):
    rows = []
    t = T0
    for i, v in enumerate(values):
        if gap_at is not None and i == gap_at:
            t += dt.timedelta(hours=2)  # inject a gap
        rows.append({"station_id": station, "variable": variable, "valid_time": t, "value": v})
        t += dt.timedelta(hours=1)
    return rows


def test_persistence_flags_stuck_sensor():
    out = checks.persistence_check(_obs(_hourly("inmet:A1", "t2m", [297.0] * 7)), _params())
    assert all(_flagged(out, qc_bits.PERSISTENCE))


def test_persistence_short_run_not_flagged():
    out = checks.persistence_check(_obs(_hourly("inmet:A1", "t2m", [297.0] * 5)), _params())
    assert not any(_flagged(out, qc_bits.PERSISTENCE))


def test_persistence_gap_breaks_run():
    # 7 identical readings but a 2 h gap after the 3rd: runs of 3 and 4 -> no flag
    out = checks.persistence_check(
        _obs(_hourly("inmet:A1", "t2m", [297.0] * 7, gap_at=3)), _params()
    )
    assert not any(_flagged(out, qc_bits.PERSISTENCE))


def test_persistence_exemptions_calm_wind_and_dry_spell():
    calm = _hourly("inmet:A1", "wind10m", [0.3] * 10)
    dry = _hourly("inmet:A1", "precip_1h", [0.0] * 48)
    windy_stuck = _hourly("inmet:A2", "wind10m", [5.2] * 10)
    out = checks.persistence_check(_obs(calm + dry + windy_stuck), _params())
    assert not any(_flagged(out.filter(pl.col("station_id") == "inmet:A1"), qc_bits.PERSISTENCE))
    assert all(_flagged(out.filter(pl.col("station_id") == "inmet:A2"), qc_bits.PERSISTENCE))


# ---------------------------------------------------------------- SPATIAL

def test_spatial_flags_outlier_against_neighbors():
    stations = [f"inmet:A{i}" for i in range(6)]
    rows = [
        {"station_id": s, "variable": "t2m", "valid_time": T0, "value": v}
        for s, v in zip(stations, [293.0, 293.1, 292.9, 293.05, 292.95, 308.0], strict=True)
    ]
    # A5 (outlier) and A0 (normal) each have the other five as neighbors
    pairs = pl.DataFrame(
        {
            "station_id": ["inmet:A5"] * 5 + ["inmet:A0"] * 5,
            "neighbor_id": stations[:5] + stations[1:],
        }
    )
    out = checks.spatial_check(_obs(rows), _params(), pairs)
    flags = dict(zip(out["station_id"].to_list(), _flagged(out, qc_bits.SPATIAL), strict=True))
    assert flags["inmet:A5"]  # 15 K above robust neighborhood -> flagged
    assert not flags["inmet:A0"]


def test_spatial_needs_three_neighbors():
    rows = [
        {"station_id": "inmet:A0", "variable": "t2m", "valid_time": T0, "value": 320.0},
        {"station_id": "inmet:A1", "variable": "t2m", "valid_time": T0, "value": 293.0},
        {"station_id": "inmet:A2", "variable": "t2m", "valid_time": T0, "value": 293.0},
    ]
    pairs = pl.DataFrame(
        {"station_id": ["inmet:A0"] * 2, "neighbor_id": ["inmet:A1", "inmet:A2"]}
    )
    out = checks.spatial_check(_obs(rows), _params(), pairs)
    assert not any(_flagged(out, qc_bits.SPATIAL))  # 2 neighbors < 3: no evidence


# ---------------------------------------------------------------- METADATA

def test_metadata_propagates_station_suspicion_to_observations():
    stations = _stations(
        [
            {"station_id": "inmet:BAD", "elev_station": 800.0, "elev_dem": 950.0},  # |diff|=150
            {"station_id": "inmet:OK", "elev_station": 800.0, "elev_dem": 850.0},
            {"station_id": "inmet:NOLL", "lat": None},
        ]
    )
    df = _obs(
        [
            {"station_id": s, "variable": "t2m", "valid_time": T0, "value": 298.0}
            for s in ["inmet:BAD", "inmet:OK", "inmet:NOLL"]
        ]
    )
    out = checks.metadata_check(df, _params(), stations)
    flags = dict(zip(out["station_id"].to_list(), _flagged(out, qc_bits.METADATA), strict=True))
    assert flags["inmet:BAD"] and flags["inmet:NOLL"] and not flags["inmet:OK"]


# ---------------------------------------------------------------- DUPLICATE

def test_duplicate_flags_secondary_network_only():
    stations = _stations(
        [
            {"station_id": "inmet:A801", "network": "inmet", "cross_ref": "isd:829"},
            {"station_id": "isd:829", "network": "isd", "cross_ref": "inmet:A801"},
        ]
    )
    df = _obs(
        [
            {"station_id": "inmet:A801", "variable": "t2m", "valid_time": T0, "value": 298.15},
            {"station_id": "isd:829", "variable": "t2m", "valid_time": T0, "value": 298.17,
             "source": "isd"},
            # differs beyond tolerance at another hour -> independent info, kept clean
            {"station_id": "inmet:A801", "variable": "t2m",
             "valid_time": T0 + dt.timedelta(hours=1), "value": 298.0},
            {"station_id": "isd:829", "variable": "t2m",
             "valid_time": T0 + dt.timedelta(hours=1), "value": 299.0, "source": "isd"},
        ]
    )
    out = checks.duplicate_check(df, _params(), stations).sort("valid_time", "station_id")
    flags = list(
        zip(out["station_id"].to_list(), _flagged(out, qc_bits.DUPLICATE), strict=True)
    )
    assert (("isd:829", True) in flags[:2]) and (("inmet:A801", False) in flags[:2])
    assert flags[2][1] is False and flags[3][1] is False


# ---------------------------------------------------------------- RUNNER

def test_runner_preserves_every_row_and_validates_contract():
    stations = _stations([{"station_id": "inmet:A1"}])
    df = _obs(_hourly("inmet:A1", "t2m", [297.0 + i * 0.1 for i in range(24)]))
    out = run_qc(df.drop("qc_flags"), _params(), stations)
    assert out.height == df.height  # QC NEVER drops rows
    validate(out, OBS_QC_V1, "obs_qc")
