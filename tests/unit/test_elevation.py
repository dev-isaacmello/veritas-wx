"""Golden + property tests for the elevation corrections.

Lapse-rate correction for temperature (non-negotiable #2) and the Ingleby
(2014, section 3.3) wind altitude factor ported from WeatherBench-X
(ADR-0004 item 1).
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from veritas_wx.match.elevation import (
    LAPSE_RATE_K_PER_M,
    WIND_FACTOR_MAX,
    adjust_wind,
    delta_z,
    lapse_adjust,
    wind_altitude_factor,
)


def test_plan_golden_example():
    """The hand-computed example frozen in PLAN.md §2.5.

    Station at 800 m, model cell at 1200 m:
      delta_z = 800 - 1200 = -400 m
      adjustment = -0.0065 * (-400) = +2.6 K  (station sits lower => warmer)
    """
    dz = delta_z(elev_station=800.0, elev_cell=1200.0)
    assert dz == -400.0
    assert lapse_adjust(290.0, dz) == pytest.approx(292.6)


def test_station_above_cell_gets_colder():
    dz = delta_z(1500.0, 500.0)
    assert lapse_adjust(280.0, dz) == pytest.approx(273.5)


def test_lapse_rate_constant_is_standard_atmosphere():
    assert LAPSE_RATE_K_PER_M == pytest.approx(6.5 / 1000.0)


@given(st.floats(min_value=200.0, max_value=330.0))
def test_zero_delta_z_is_identity(t):
    assert lapse_adjust(t, 0.0) == t


@given(
    st.floats(min_value=200.0, max_value=330.0),
    st.floats(min_value=-500.0, max_value=500.0),
    st.floats(min_value=-500.0, max_value=500.0),
)
def test_monotonic_decreasing_in_delta_z(t, dz1, dz2):
    """Higher station relative to cell => colder adjusted forecast."""
    lo, hi = sorted([dz1, dz2])
    assert lapse_adjust(t, hi) <= lapse_adjust(t, lo)


@given(
    st.floats(min_value=200.0, max_value=330.0),
    st.floats(min_value=-500.0, max_value=500.0),
)
def test_adjustment_is_linear_and_invertible(t, dz):
    adjusted = lapse_adjust(t, dz)
    assert lapse_adjust(adjusted, -dz) == pytest.approx(t)


def test_wind_factor_goldens_by_hand():
    """Hand-computed values of 1 + 0.002 * (dz - 100), saturating at 3.0.

    delta_z = elev_station - grid_elev (FACT_V1 convention, positive when
    the station sits above the model orography). The 100 m onset is
    subtracted inside the slope, exactly as in the WeatherBench-X source:
      dz = 600  => 1 + 0.002 * 500  = 2.0
      dz = 300  => 1 + 0.002 * 200  = 1.4
      dz = 1100 => 1 + 0.002 * 1000 = 3.0 (saturation begins exactly here)
    """
    assert wind_altitude_factor(600.0) == pytest.approx(2.0)
    assert wind_altitude_factor(300.0) == pytest.approx(1.4)
    assert wind_altitude_factor(1100.0) == pytest.approx(3.0)


def test_wind_factor_saturates_at_three():
    assert wind_altitude_factor(1500.0) == pytest.approx(3.0)
    assert wind_altitude_factor(10000.0) == pytest.approx(WIND_FACTOR_MAX)


def test_wind_factor_no_adjustment_at_or_below_onset():
    """Stations below, level with, or < 100 m above the grid get factor 1."""
    assert wind_altitude_factor(-200.0) == 1.0
    assert wind_altitude_factor(0.0) == 1.0
    assert wind_altitude_factor(50.0) == 1.0
    assert wind_altitude_factor(100.0) == 1.0


def test_wind_factor_continuous_at_onset():
    assert wind_altitude_factor(100.0 + 1e-9) == pytest.approx(1.0)


def test_adjust_wind_golden():
    """5 m/s at a station 300 m above the model orography => 5 * 1.4 = 7 m/s."""
    assert adjust_wind(5.0, 300.0) == pytest.approx(7.0)
    assert adjust_wind(5.0, -400.0) == pytest.approx(5.0)


@given(st.floats(min_value=-2000.0, max_value=2000.0))
def test_wind_factor_bounded(dz):
    assert 1.0 <= wind_altitude_factor(dz) <= WIND_FACTOR_MAX


@given(
    st.floats(min_value=-2000.0, max_value=2000.0),
    st.floats(min_value=-2000.0, max_value=2000.0),
)
def test_wind_factor_monotonic_nondecreasing(dz1, dz2):
    """Higher station relative to cell => equal or larger speed-up factor."""
    lo, hi = sorted([dz1, dz2])
    assert wind_altitude_factor(lo) <= wind_altitude_factor(hi)


@given(
    st.floats(min_value=0.0, max_value=60.0),
    st.floats(min_value=-2000.0, max_value=2000.0),
)
def test_adjust_wind_never_reduces_speed(speed, dz):
    assert adjust_wind(speed, dz) >= speed
