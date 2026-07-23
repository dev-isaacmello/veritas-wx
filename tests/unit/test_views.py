"""Matched views: comparisons only ever see exactly matched samples."""

import datetime as dt

import polars as pl
import pytest

from veritas_wx.contracts import FACT_V1, qc_bits
from veritas_wx.match.views import comparison_id, matched_view

UTC = dt.UTC
INIT = dt.datetime(2025, 8, 10, 0, tzinfo=UTC)


def _fact(rows: list[dict]) -> pl.DataFrame:
    defaults = {
        "variable": "t2m", "init_time": INIT, "lead_hours": 24,
        "valid_time": INIT + dt.timedelta(hours=24),
        "fcst_raw": 290.0, "fcst_elev_adj": None, "obs": 291.0, "delta_z": 0.0,
        "interp_method": "bilinear", "repr_floor": None, "qc_flags": 0,
        "ingest_version": "test",
    }
    return pl.DataFrame([{**defaults, **r} for r in rows], schema=FACT_V1)


def _vt(h: int) -> dt.datetime:
    return INIT + dt.timedelta(hours=h)


def test_comparison_id_canonical_sorted():
    assert comparison_id(["gfs", "aifs"]) == "aifs+gfs"
    with pytest.raises(ValueError):
        comparison_id(["gfs"])
    with pytest.raises(ValueError):
        comparison_id(["gfs", "gfs"])


def test_only_fully_matched_keys_survive():
    rows = []
    for st in ["inmet:A", "inmet:B", "inmet:C"]:
        rows.append({"model": "aifs", "station_id": st})
        if st != "inmet:C":  # gfs missing station C
            rows.append({"model": "gfs", "station_id": st})
    view, manifest = matched_view(_fact(rows), ["aifs", "gfs"])
    assert manifest["n_matched_keys"] == 2
    assert view.height == 4  # 2 keys x 2 models
    assert "inmet:C" not in view["station_id"].to_list()


def test_flagged_pair_breaks_the_match_under_strict_qc():
    rows = [
        {"model": "aifs", "station_id": "inmet:A", "qc_flags": qc_bits.RANGE},
        {"model": "gfs", "station_id": "inmet:A"},
        {"model": "aifs", "station_id": "inmet:B"},
        {"model": "gfs", "station_id": "inmet:B"},
    ]
    strict, m_strict = matched_view(_fact(rows), ["aifs", "gfs"])
    assert m_strict["n_matched_keys"] == 1  # station A's key dies entirely

    # consumer relaxes rigor: mask only requires DUPLICATE clear -> A returns
    relaxed, m_relaxed = matched_view(_fact(rows), ["aifs", "gfs"], qc_mask=qc_bits.DUPLICATE)
    assert m_relaxed["n_matched_keys"] == 2
    assert m_relaxed["qc_mask"] == qc_bits.DUPLICATE


def test_null_obs_or_fcst_never_matches():
    rows = [
        {"model": "aifs", "station_id": "inmet:A", "obs": None},
        {"model": "gfs", "station_id": "inmet:A"},
    ]
    view, manifest = matched_view(_fact(rows), ["aifs", "gfs"])
    assert manifest["n_matched_keys"] == 0 and view.height == 0
