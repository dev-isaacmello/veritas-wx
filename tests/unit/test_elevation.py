"""Golden + property tests for the lapse-rate correction (non-negotiable #2)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from veritas_wx.match.elevation import LAPSE_RATE_K_PER_M, delta_z, lapse_adjust


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
    # station 1500 m, cell 500 m => dz = +1000 => -6.5 K
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
