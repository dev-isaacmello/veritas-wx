"""Hand-computed goldens for the core metric statistics and public wrappers."""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from veritas_wx.analyze.bootstrap import BootstrapResult
from veritas_wx.analyze.decompose import representativeness_decomposition
from veritas_wx.analyze.metrics.core import (
    REGISTRY_BINS,
    _station_variance_ratios,
    bias,
    bias_by_percentile,
    bias_stat,
    mae,
    mae_stat,
    rmse,
    rmse_stat,
    variance_ratio,
    variance_ratio_stat,
)


def _days(n: int, start: date = date(2025, 7, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------------------
# Scalar statistics: three known pairs
# ---------------------------------------------------------------------------
@pytest.fixture()
def three_pairs() -> pl.DataFrame:
    """fcst - obs = [-1, 0, 2]  =>  mae = 1, bias = 1/3, rmse = sqrt(5/3)."""
    return pl.DataFrame(
        {
            "fcst": [1.0, 2.0, 3.0],
            "obs": [2.0, 2.0, 1.0],
            "station_id": ["a", "a", "a"],
            "day": _days(3),
        }
    )


def test_mae_stat_golden(three_pairs):
    assert mae_stat(three_pairs) == pytest.approx((1 + 0 + 2) / 3)


def test_bias_stat_golden(three_pairs):
    assert bias_stat(three_pairs) == pytest.approx((-1 + 0 + 2) / 3)


def test_rmse_stat_golden(three_pairs):
    assert rmse_stat(three_pairs) == pytest.approx(np.sqrt((1 + 0 + 4) / 3))


# ---------------------------------------------------------------------------
# variance_ratio: constructed 2 stations x 40 days with exact ratio 0.5
# ---------------------------------------------------------------------------
def _shrunk_frame() -> pl.DataFrame:
    """Per station: fcst = mean(obs) + 0.5*(obs - mean(obs)) => std ratio 0.5."""
    days = _days(40)
    obs_a = np.linspace(10.0, 20.0, 40)
    obs_b = np.linspace(280.0, 300.0, 40)
    frames = []
    for sid, obs in (("sta_a", obs_a), ("sta_b", obs_b)):
        mu = obs.mean()
        frames.append(
            pl.DataFrame(
                {
                    "station_id": [sid] * 40,
                    "day": days,
                    "obs": obs,
                    "fcst": mu + 0.5 * (obs - mu),
                }
            )
        )
    return pl.concat(frames)


def test_variance_ratio_stat_exact_half():
    """std(fcst) = 0.5*std(obs) exactly per station; median of [0.5, 0.5] = 0.5."""
    assert variance_ratio_stat(_shrunk_frame()) == pytest.approx(0.5)


def test_variance_ratio_excludes_zero_obs_std_station():
    """A constant-obs station is excluded from the median, not poisoning it."""
    df = _shrunk_frame()
    degenerate = pl.DataFrame(
        {
            "station_id": ["sta_flat"] * 40,
            "day": _days(40),
            "obs": [5.0] * 40,
            "fcst": np.linspace(0.0, 1.0, 40),
        }
    )
    combined = pl.concat([df, degenerate])
    assert variance_ratio_stat(combined) == pytest.approx(0.5)
    ratios = _station_variance_ratios(combined)
    assert ratios.filter(pl.col("station_id") == "sta_flat")["excluded"].item() is True
    assert ratios["excluded"].sum() == 1


def test_variance_ratio_all_excluded_is_nan():
    df = pl.DataFrame(
        {
            "station_id": ["s"] * 3,
            "day": _days(3),
            "obs": [1.0, 1.0, 1.0],
            "fcst": [1.0, 2.0, 3.0],
        }
    )
    assert np.isnan(variance_ratio_stat(df))


# ---------------------------------------------------------------------------
# Public wrappers: always a BootstrapResult, estimate == stat on the full df
# ---------------------------------------------------------------------------
def test_public_wrappers_return_bootstrap_result(three_pairs):
    for fn, stat in ((mae, mae_stat), (rmse, rmse_stat), (bias, bias_stat)):
        res = fn(three_pairs, rng=np.random.default_rng(0), n_boot=50, block_len=2)
        assert isinstance(res, BootstrapResult)
        assert res.estimate == pytest.approx(stat(three_pairs))
        assert res.ci_low <= res.estimate <= res.ci_high
        assert res.draws.shape == (50,)


def test_variance_ratio_public_wrapper():
    df = _shrunk_frame()
    res = variance_ratio(df, rng=np.random.default_rng(1), n_boot=50, block_len=5)
    assert isinstance(res, BootstrapResult)
    assert res.estimate == pytest.approx(0.5)
    # the construction is exact per station in EVERY resample => degenerate CI at 0.5
    assert res.ci_low == pytest.approx(0.5)
    assert res.ci_high == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# bias_by_percentile with fabricated obs_pct
# ---------------------------------------------------------------------------
def test_bias_by_percentile_golden():
    """20 pairs at obs_pct=5 with error +1; 20 at obs_pct=99.5 with error -1."""
    days = _days(20)
    low = pl.DataFrame(
        {
            "station_id": ["s"] * 20,
            "day": days,
            "obs": [10.0] * 20,
            "fcst": [11.0] * 20,  # error +1
            "obs_pct": [5.0] * 20,
        }
    )
    high = pl.DataFrame(
        {
            "station_id": ["s"] * 20,
            "day": days,
            "obs": [30.0] * 20,
            "fcst": [29.0] * 20,  # error -1
            "obs_pct": [99.5] * 20,
        }
    )
    df = pl.concat([low, high])
    out = bias_by_percentile(df, rng=np.random.default_rng(2), n_boot=50, block_len=3)

    assert out.columns == ["bin", "estimate", "ci_low", "ci_high", "n_pairs"]
    assert out["bin"].to_list() == list(REGISTRY_BINS)  # one row per registry bin, in order

    row_low = out.filter(pl.col("bin") == "0-10")
    assert row_low["estimate"].item() == pytest.approx(1.0)
    assert row_low["n_pairs"].item() == 20
    assert row_low["ci_low"].item() == pytest.approx(1.0)  # constant error => degenerate CI

    row_high = out.filter(pl.col("bin") == "99-100")
    assert row_high["estimate"].item() == pytest.approx(-1.0)
    assert row_high["n_pairs"].item() == 20

    empty = out.filter(pl.col("bin") == "40-50")
    assert empty["n_pairs"].item() == 0
    assert empty["estimate"].item() is None
    assert empty["ci_low"].item() is None and empty["ci_high"].item() is None


def test_bias_by_percentile_top_bin_inclusive_and_boundaries():
    """obs_pct=100 lands in '99-100'; obs_pct=10 in '10-20' (half-open bins)."""
    df = pl.DataFrame(
        {
            "station_id": ["s"] * 10,
            "day": _days(10),
            "obs": [1.0] * 10,
            "fcst": [1.5] * 10,
            "obs_pct": [100.0] * 5 + [10.0] * 5,
        }
    )
    out = bias_by_percentile(df, rng=np.random.default_rng(3), n_boot=20, block_len=2)
    assert out.filter(pl.col("bin") == "99-100")["n_pairs"].item() == 5
    assert out.filter(pl.col("bin") == "10-20")["n_pairs"].item() == 5
    assert out.filter(pl.col("bin") == "0-10")["n_pairs"].item() == 0
    assert out["n_pairs"].sum() == 10  # bins partition the pairs


def test_bias_by_percentile_requires_obs_pct(three_pairs):
    with pytest.raises(ValueError, match="obs_pct"):
        bias_by_percentile(three_pairs, rng=np.random.default_rng(0))


# ---------------------------------------------------------------------------
# Representativeness decomposition (registered metrics mse_total /
# repr_floor_mean / mse_model_est — tested here; no separate test file)
# ---------------------------------------------------------------------------
def test_decomposition_golden_constant_frame():
    """Constant error 1 and constant floor 0.25 => exact, degenerate-CI rows."""
    n = 30
    df = pl.DataFrame(
        {
            "station_id": ["s"] * n,
            "day": _days(n),
            "obs": [10.0] * n,
            "fcst": [11.0] * n,  # squared error 1.0 everywhere
            "repr_floor": [0.25] * n,
        }
    )
    out = representativeness_decomposition(
        df, rng=np.random.default_rng(0), n_boot=50, block_len=5
    )
    assert out["metric"].to_list() == ["mse_total", "repr_floor_mean", "mse_model_est"]
    by = {r["metric"]: r for r in out.to_dicts()}
    assert by["mse_total"]["estimate"] == pytest.approx(1.0)
    assert by["repr_floor_mean"]["estimate"] == pytest.approx(0.25)
    assert by["mse_model_est"]["estimate"] == pytest.approx(0.75)
    for row in by.values():
        assert row["ci_low"] <= row["estimate"] <= row["ci_high"]
        assert row["n_pairs"] == n and row["n_days"] == n
        assert row["n_boot"] == 50 and row["block_len_days"] == 5
    assert by["mse_total"]["clipped_frac"] is None
    assert by["mse_model_est"]["clipped_frac"] == pytest.approx(0.0)  # clip never acts


def test_decomposition_uses_only_pairs_with_repr_floor():
    """Null-floor pairs are excluded from ALL three lines (no imputation)."""
    df = pl.DataFrame(
        {
            "station_id": ["s"] * 20,
            "day": _days(20),
            "obs": [0.0] * 20,
            # eligible half has error 1; ineligible half error 100 — must not leak in
            "fcst": [1.0] * 10 + [100.0] * 10,
            "repr_floor": [0.5] * 10 + [None] * 10,
        }
    )
    out = representativeness_decomposition(
        df, rng=np.random.default_rng(1), n_boot=30, block_len=2
    )
    by = {r["metric"]: r for r in out.to_dicts()}
    assert by["mse_total"]["estimate"] == pytest.approx(1.0)
    assert by["mse_total"]["n_pairs"] == 10


def test_decomposition_clip_acts_and_is_flagged():
    """Floor above total error => mse_model_est clipped to 0, clipped_frac = 1."""
    n = 25
    df = pl.DataFrame(
        {
            "station_id": ["s"] * n,
            "day": _days(n),
            "obs": [10.0] * n,
            "fcst": [10.5] * n,  # squared error 0.25
            "repr_floor": [2.0] * n,  # floor far above total
        }
    )
    out = representativeness_decomposition(
        df, rng=np.random.default_rng(2), n_boot=40, block_len=3
    )
    by = {r["metric"]: r for r in out.to_dicts()}
    assert by["mse_model_est"]["estimate"] == 0.0
    assert by["mse_model_est"]["clipped_frac"] == pytest.approx(1.0)
    assert by["mse_model_est"]["ci_low"] >= 0.0  # draws are clipped too


def test_decomposition_zero_eligible_pairs_returns_empty_with_schema():
    df = pl.DataFrame(
        {
            "station_id": ["s"] * 5,
            "day": _days(5),
            "obs": [1.0] * 5,
            "fcst": [2.0] * 5,
            "repr_floor": pl.Series([None] * 5, dtype=pl.Float64),
        }
    )
    out = representativeness_decomposition(df, rng=np.random.default_rng(3), n_boot=10)
    assert out.height == 0  # never invented
    assert out.columns == [
        "metric",
        "estimate",
        "ci_low",
        "ci_high",
        "alpha",
        "n_pairs",
        "n_days",
        "block_len_days",
        "n_boot",
        "clipped_frac",
    ]
    assert out.schema["estimate"] == pl.Float64
    assert out.schema["n_pairs"] == pl.Int64
