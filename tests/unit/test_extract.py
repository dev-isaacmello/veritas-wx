"""GRIB decode + station extraction tested on a SYNTHETIC in-memory GRIB.

We build a real GRIB2 message with eccodes (4x3 grid, known linear values),
so the whole decode -> reshape -> interp path is verified without network.
"""

import datetime as dt

import eccodes
import numpy as np
import polars as pl
import pytest

from veritas_wx.match.extract import (
    DecodedField,
    by_short_name,
    decode_messages,
    instantaneous_points,
    tp_nearest,
    tp_to_mm,
)

INIT = dt.datetime(2025, 7, 1, 0, tzinfo=dt.UTC)


def _make_message(short_name: str, values: np.ndarray) -> bytes:
    """4 lons x 3 lats regular_ll GRIB2: lats 2..0 (descending), lons 0..3."""
    h = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        eccodes.codes_set(h, "Ni", 4)
        eccodes.codes_set(h, "Nj", 3)
        eccodes.codes_set(h, "latitudeOfFirstGridPointInDegrees", 2.0)
        eccodes.codes_set(h, "longitudeOfFirstGridPointInDegrees", 0.0)
        eccodes.codes_set(h, "latitudeOfLastGridPointInDegrees", 0.0)
        eccodes.codes_set(h, "longitudeOfLastGridPointInDegrees", 3.0)
        eccodes.codes_set(h, "iDirectionIncrementInDegrees", 1.0)
        eccodes.codes_set(h, "jDirectionIncrementInDegrees", 1.0)
        eccodes.codes_set_values(h, values.ravel())
        return eccodes.codes_get_message(h)
    finally:
        eccodes.codes_release(h)


VALUES = np.arange(12, dtype=float).reshape(3, 4)


def test_decode_roundtrip_grid_and_values():
    fields = decode_messages(_make_message("2t", VALUES))
    assert len(fields) == 1
    f = fields[0]
    assert f.values.shape == (3, 4)
    np.testing.assert_allclose(f.lons, [0.0, 1.0, 2.0, 3.0])
    assert f.lats[0] > f.lats[-1]
    np.testing.assert_allclose(f.values, VALUES)


def test_decode_multiple_concatenated_messages():
    blob = _make_message("2t", VALUES) + _make_message("2t", VALUES + 100.0)
    fields = decode_messages(blob)
    assert len(fields) == 2
    np.testing.assert_allclose(fields[1].values, VALUES + 100.0)


def _fake(short: str, values: np.ndarray, units: str = "K") -> DecodedField:
    return DecodedField(
        short_name=short,
        lats=np.array([2.0, 1.0, 0.0]),
        lons=np.array([0.0, 1.0, 2.0, 3.0]),
        values=values,
        units=units,
        step="6",
    )


def _stations() -> pl.DataFrame:
    return pl.DataFrame(
        {"station_id": ["inmet:A"], "lat": [1.0], "lon": [1.0]}
    )


def test_instantaneous_points_wind_speed_at_nodes_before_interp():
    fields = by_short_name(
        [
            _fake("2t", np.full((3, 4), 290.0)),
            _fake("10u", np.full((3, 4), 3.0), units="m s**-1"),
            _fake("10v", np.full((3, 4), 4.0), units="m s**-1"),
        ]
    )
    pts = instantaneous_points(fields, _stations(), "gfs", INIT, 6, "test")
    by_var = {r["variable"]: r for r in pts.to_dicts()}
    assert by_var["t2m"]["value"] == pytest.approx(290.0)
    assert by_var["wind10m"]["value"] == pytest.approx(5.0)
    assert by_var["t2m"]["valid_time"] == INIT + dt.timedelta(hours=6)
    assert by_var["t2m"]["grid_lat"] == pytest.approx(1.0)
    assert by_var["t2m"]["grid_elev"] is None


def test_tp_units_meters_converted_kgm2_passthrough():
    ecmwf = _fake("tp", np.full((3, 4), 0.0123), units="m")
    np.testing.assert_allclose(tp_to_mm(ecmwf), 12.3)
    gfs_style = _fake("tp", np.full((3, 4), 7.5), units="kg m**-2")
    np.testing.assert_allclose(tp_to_mm(gfs_style), 7.5)
    with pytest.raises(ValueError, match="refusing to guess"):
        tp_to_mm(_fake("tp", VALUES, units="furlongs"))


def test_tp_nearest_per_station():
    tp = _fake("tp", VALUES, units="kg m**-2")
    out = tp_nearest({"tp": tp}, _stations())
    assert out["inmet:A"] == pytest.approx(5.0)


def test_duplicate_short_name_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        by_short_name([_fake("2t", VALUES), _fake("2t", VALUES)])
