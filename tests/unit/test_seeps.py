"""Tests for the per-station SEEPS port (WeatherBench-X, Apache 2.0).

Golden values are hand-computed from the Rodwell (2010) scoring matrix as
implemented in ``weatherbenchX/metrics/categorical.py``; one case reproduces
the numeric check in WBX ``metrics_test.py::test_seeps``.
"""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from veritas_wx.analyze.bootstrap import BootstrapResult
from veritas_wx.analyze.metrics.seeps import seeps, seeps_mask, seeps_stat


def _days(n: int, start: date = date(2025, 7, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _pairs(fcst: list[float], obs: list[float], p1: float, wet: float) -> pl.DataFrame:
    n = len(fcst)
    return pl.DataFrame(
        {
            "station_id": ["s"] * n,
            "day": _days(n),
            "fcst": fcst,
            "obs": obs,
            "p1": [p1] * n,
            "wet_threshold": [wet] * n,
        }
    )


def test_golden_all_nine_categories_p1_half():
    """All 9 (fcst, obs) category combos at p1 = 0.5, wet_threshold = 5 mm.

    Category values: dry = 0.0 (<= 0.25), light = 1.0, heavy = 10.0 (>= 5).
    Rodwell matrix * 0.5 at p1 = 0.5 (rows fcst, cols obs; dry/light/heavy)::

        (dry,   light) = 0.5 * 1/(1-0.5)              = 1.0
        (dry,   heavy) = 0.5 * 4/(1-0.5)              = 4.0
        (light, dry)   = 0.5 * 1/0.5                  = 1.0
        (light, heavy) = 0.5 * 3/(1-0.5)              = 3.0
        (heavy, dry)   = 0.5 * (1/0.5 + 3/(2+0.5))    = 1.6
        (heavy, light) = 0.5 * 3/(2+0.5)              = 0.6
        diagonal                                       = 0.0

    Mean over the 9 rows = (1 + 4 + 1 + 3 + 1.6 + 0.6) / 9 = 11.2 / 9.
    """
    cats = [0.0, 1.0, 10.0]
    fcst = [f for f in cats for _ in cats]
    obs = cats * 3
    df = _pairs(fcst, obs, p1=0.5, wet=5.0)
    assert seeps_stat(df) == pytest.approx(11.2 / 9.0)


def test_perfect_forecast_scores_zero():
    df = _pairs([0.0, 1.0, 10.0, 0.1], [0.0, 1.0, 10.0, 0.1], p1=0.4, wet=5.0)
    assert seeps_stat(df) == pytest.approx(0.0)


def test_wbx_golden_light_forecast_dry_obs():
    """WBX metrics_test.py::test_seeps: fcst light, obs dry, p1 = 0.4 => 1.25.

    Their prediction is target + 0.5 (in meters there, mm here): obs = 0.0 is
    dry, fcst = 0.5 exceeds the 0.25 mm dry threshold but stays below the wet
    threshold => light. Score = 0.5 * 1/p1 = 0.5 / 0.4 = 1.25.
    """
    df = _pairs([0.5], [0.0], p1=0.4, wet=1.0)
    assert seeps_stat(df) == pytest.approx(1.25)


def test_p1_mask_excludes_extreme_climates():
    """Stations at p1 = 0.05 and 0.9 are masked out; boundaries 0.1/0.85 stay in."""
    frames = []
    for sid, p1 in (("arid", 0.9), ("rainforest", 0.05), ("lo", 0.1), ("hi", 0.85)):
        frames.append(
            _pairs([10.0], [0.0], p1=p1, wet=5.0).with_columns(pl.lit(sid).alias("station_id"))
        )
    df = pl.concat(frames)

    mask = seeps_mask(df)
    assert mask.to_list() == [False, False, True, True]

    expected_lo = 0.5 * (1.0 / 0.1 + 3.0 / 2.1)
    expected_hi = 0.5 * (1.0 / 0.85 + 3.0 / 2.85)
    assert seeps_stat(df) == pytest.approx((expected_lo + expected_hi) / 2.0)


def test_null_wet_threshold_rows_are_masked_and_nan_when_empty():
    df = _pairs([1.0, 2.0], [0.0, 3.0], p1=0.5, wet=5.0).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("wet_threshold")
    )
    assert seeps_mask(df).to_list() == [False, False]
    assert np.isnan(seeps_stat(df))


def test_missing_columns_raise():
    with pytest.raises(ValueError, match="wet_threshold"):
        seeps_stat(pl.DataFrame({"fcst": [1.0], "obs": [1.0], "p1": [0.5]}))


def test_bootstrap_integration_returns_valid_ci():
    """Synthetic 40-day series: BootstrapResult with a CI bracketing the estimate."""
    rng_data = np.random.default_rng(7)
    n = 40
    obs = np.where(rng_data.random(n) < 0.4, 0.0, rng_data.gamma(2.0, 3.0, n))
    fcst = np.where(rng_data.random(n) < 0.4, 0.0, rng_data.gamma(2.0, 3.0, n))
    df = _pairs(list(fcst), list(obs), p1=0.4, wet=6.0)

    res = seeps(df, rng=np.random.default_rng(0), n_boot=200, block_len=4)
    assert isinstance(res, BootstrapResult)
    assert res.estimate == pytest.approx(seeps_stat(df))
    assert res.ci_low <= res.estimate <= res.ci_high
    assert res.ci_low < res.ci_high
    assert res.n_days == n
    assert res.draws.shape == (200,)

    auto = seeps(df, rng=np.random.default_rng(1), n_boot=50, block_len=None)
    assert 2 <= auto.block_len_days <= 30


def test_seeps_raises_when_everything_masked():
    df = _pairs([1.0], [0.0], p1=0.95, wet=5.0)
    with pytest.raises(ValueError, match="nothing to bootstrap"):
        seeps(df, rng=np.random.default_rng(0), n_boot=10, block_len=1)


def test_perfect_forecast_bootstrap_ci_is_degenerate_zero():
    df = _pairs([0.0] * 10 + [8.0] * 10, [0.0] * 10 + [8.0] * 10, p1=0.4, wet=5.0)
    res = seeps(df, rng=np.random.default_rng(2), n_boot=50, block_len=2)
    assert res.estimate == pytest.approx(0.0)
    assert res.ci_low == pytest.approx(0.0)
    assert res.ci_high == pytest.approx(0.0)
