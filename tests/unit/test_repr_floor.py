"""Golden test for the representativeness floor estimator (PLAN.md §2.5)."""

import datetime as dt

import polars as pl
import pytest

from veritas_wx.match.repr_floor import attach_repr_floor, cell_of, repr_floor_by_cell

UTC = dt.UTC
T = [dt.datetime(2025, 8, 1, h, tzinfo=UTC) for h in range(3)]


def _stations() -> pl.DataFrame:
    return pl.DataFrame(
        {
            # A and B share the 0.25° cell; C is alone in another cell
            "station_id": ["inmet:A", "inmet:B", "inmet:C"],
            "lat": [-30.10, -30.20, -12.10],
            "lon": [-51.10, -51.20, -45.10],
        }
    )


def _obs() -> pl.DataFrame:
    rows = []
    # across-station values at each instant (variance by hand, ddof=1):
    #   t0: (1, 3)  -> var 2.0
    #   t1: (2, 2)  -> var 0.0
    #   t2: (0, 4)  -> var 8.0        => temporal median = 2.0
    for t, (va, vb) in zip(T, [(1.0, 3.0), (2.0, 2.0), (0.0, 4.0)], strict=True):
        rows.append({"station_id": "inmet:A", "valid_time": t, "variable": "t2m", "value": va})
        rows.append({"station_id": "inmet:B", "valid_time": t, "variable": "t2m", "value": vb})
        rows.append({"station_id": "inmet:C", "valid_time": t, "variable": "t2m", "value": 10.0})
    return pl.DataFrame(rows)


def test_same_cell_assignment():
    cells = _stations().select("station_id", *cell_of())
    a, b, c = cells.sort("station_id").to_dicts()
    assert (a["cell_y"], a["cell_x"]) == (b["cell_y"], b["cell_x"])
    assert (c["cell_y"], c["cell_x"]) != (a["cell_y"], a["cell_x"])


def test_floor_is_temporal_median_of_across_station_variance():
    floors = repr_floor_by_cell(_obs(), _stations())
    assert floors.height == 1  # only the 2-station cell qualifies
    row = floors.to_dicts()[0]
    assert row["repr_floor"] == pytest.approx(2.0)
    assert row["n_stations"] == 2
    assert row["n_instants"] == 3


def test_lone_station_cell_gets_null_never_imputed():
    floors = repr_floor_by_cell(_obs(), _stations())
    pairs = pl.DataFrame(
        {
            "station_id": ["inmet:A", "inmet:C"],
            "variable": ["t2m", "t2m"],
            "fcst_raw": [1.0, 2.0],
        }
    )
    out = attach_repr_floor(pairs, floors, _stations())
    by_st = {r["station_id"]: r for r in out.to_dicts()}
    assert by_st["inmet:A"]["repr_floor"] == pytest.approx(2.0)
    assert by_st["inmet:C"]["repr_floor"] is None  # NULL, never imputed
    assert "cell_y" not in out.columns  # helper columns cleaned up
