"""Golden tests for unit conversions — every value computed by hand (risk R5)."""

import pytest

from veritas_wx.contracts import units


def test_c_to_k_golden():
    assert units.c_to_k(25.0) == 298.15
    assert units.c_to_k(0.0) == 273.15
    assert units.c_to_k(-45.0) == pytest.approx(228.15)


def test_k_to_c_roundtrip():
    assert units.k_to_c(units.c_to_k(31.7)) == pytest.approx(31.7)


def test_tp_m_to_mm_golden():
    # 0.0123 m of water equivalent = 12.3 mm
    assert units.tp_m_to_mm(0.0123) == pytest.approx(12.3)
    assert units.tp_m_to_mm(0.0) == 0.0


def test_wind_speed_golden():
    # 3-4-5 triangle, by hand
    assert units.wind_speed(3.0, 4.0) == pytest.approx(5.0)
    assert units.wind_speed(0.0, 0.0) == 0.0
    # symmetry: direction never changes speed
    assert units.wind_speed(-3.0, 4.0) == units.wind_speed(3.0, -4.0)


def test_isd_lite_scaling_golden():
    # ISD-Lite stores -12.3 degC as -123
    assert units.isd_lite_scaled(-123) == pytest.approx(-12.3)
    assert units.isd_lite_scaled(215) == pytest.approx(21.5)


def test_isd_lite_missing_is_none_never_zero():
    # anti-pattern guard: missing must be None, NEVER coerced to 0.0
    assert units.isd_lite_scaled(-9999) is None
