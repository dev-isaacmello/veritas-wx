"""Hand-computed golden cases for bilinear/nearest interpolation."""

import numpy as np
import pytest

from veritas_wx.match.interp import bilinear, nearest, nearest_index, normalize_lon

LATS = np.array([0.0, 1.0])
LONS = np.array([0.0, 1.0])
FIELD = np.array([[1.0, 2.0], [3.0, 4.0]])  # field[lat_idx, lon_idx]


def test_bilinear_center_by_hand():
    # center of the square: mean of the 4 corners = 2.5
    assert bilinear(0.5, 0.5, LATS, LONS, FIELD) == pytest.approx(2.5)


def test_bilinear_corners_exact():
    assert bilinear(0.0, 0.0, LATS, LONS, FIELD) == 1.0
    assert bilinear(1.0, 1.0, LATS, LONS, FIELD) == 4.0


def test_bilinear_quarter_point_by_hand():
    # lat=0.25, lon=0.75:
    #   bottom row: 1 + 0.75*(2-1) = 1.75 ; top row: 3 + 0.75*(4-3) = 3.75
    #   result: 1.75 + 0.25*(3.75-1.75) = 2.25
    assert bilinear(0.25, 0.75, LATS, LONS, FIELD) == pytest.approx(2.25)


def test_descending_latitudes_give_same_answer():
    # ECMWF-style grid: 90 -> -90. Flip rows accordingly.
    lats_desc = LATS[::-1].copy()
    field_desc = FIELD[::-1, :].copy()
    assert bilinear(0.25, 0.75, lats_desc, LONS, field_desc) == pytest.approx(2.25)
    assert nearest(0.9, 0.1, lats_desc, LONS, field_desc) == 3.0


def test_nearest_by_hand():
    assert nearest(0.4, 0.6, LATS, LONS, FIELD) == 2.0  # closest to (0, 1)
    assert nearest_index(0.9, 0.2, LATS, LONS) == (1, 0)


def test_lon_0_360_grid_accepts_negative_station_lon():
    # Porto Alegre lon -51.2 on a 0..360 grid: -51.2 + 360 = 308.8
    grid_lons = np.array([308.75, 309.0])
    assert normalize_lon(-51.2, grid_lons) == pytest.approx(308.8)
    field = np.array([[10.0, 20.0], [30.0, 40.0]])
    v = bilinear(0.5, -51.05, LATS, grid_lons, field)
    # lon weight: (308.95-308.75)/0.25 = 0.8 -> bottom 18, top 38 -> mid 28
    assert v == pytest.approx(28.0)


def test_outside_grid_raises():
    with pytest.raises(ValueError):
        bilinear(2.0, 0.5, LATS, LONS, FIELD)
    with pytest.raises(ValueError):
        nearest(0.5, 5.0, LATS, LONS, FIELD)
