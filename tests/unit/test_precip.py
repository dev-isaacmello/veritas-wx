"""Golden tests for per-model precipitation accumulation conventions (risk R4)."""

import pytest

from veritas_wx.match.precip import MODEL_CONVENTION, AccumConvention, precip_24h


def test_from_init_difference_by_hand():
    series = {24: 12.3, 48: 20.3}
    assert precip_24h(48, series, AccumConvention.FROM_INIT) == pytest.approx(8.0)


def test_from_init_lead24_uses_accumulation_directly():
    assert precip_24h(24, {24: 12.3}, AccumConvention.FROM_INIT) == pytest.approx(12.3)


def test_per_step_sums_four_chunks_by_hand():
    series = {30: 1.0, 36: 2.0, 42: 0.5, 48: 1.5}
    assert precip_24h(48, series, AccumConvention.PER_STEP_6H) == pytest.approx(5.0)


def test_missing_component_returns_none_never_partial():
    assert precip_24h(48, {24: 12.3}, AccumConvention.FROM_INIT) is None
    assert precip_24h(48, {30: 1.0, 36: 2.0, 42: 0.5}, AccumConvention.PER_STEP_6H) is None


def test_lead_below_24_is_none():
    assert precip_24h(18, {18: 3.0}, AccumConvention.FROM_INIT) is None
    assert precip_24h(6, {6: 3.0}, AccumConvention.PER_STEP_6H) is None


def test_negative_artifact_preserved_not_clipped():
    series = {24: 5.0, 48: 4.8}
    assert precip_24h(48, series, AccumConvention.FROM_INIT) == pytest.approx(-0.2)


def test_all_phase1_models_have_a_convention():
    assert set(MODEL_CONVENTION) == {"aifs", "hres", "gfs", "graphcast"}
    assert MODEL_CONVENTION["aifs"] is AccumConvention.FROM_INIT
    assert MODEL_CONVENTION["gfs"] is AccumConvention.PER_STEP_6H
