"""API contract tests for the moving block bootstrap and block-length selection."""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from veritas_wx.analyze.bootstrap import (
    BootstrapResult,
    iid_bootstrap,
    moving_block_bootstrap,
    optimal_block_length,
    resolve_block_len,
)


def _mean_error(df: pl.DataFrame) -> float:
    return float(df.select(pl.col("error").mean()).item())


def _frame(n_days: int, rows_per_day: int = 3, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    days = [date(2025, 7, 1) + timedelta(days=i) for i in range(n_days)]
    return pl.DataFrame(
        {
            "day": [d for d in days for _ in range(rows_per_day)],
            "error": rng.normal(0.0, 1.0, n_days * rows_per_day),
        }
    )


def test_constant_series_gives_one():
    assert optimal_block_length(np.full(200, 3.14)) == 1.0


def test_iid_series_gives_small_value():
    rng = np.random.default_rng(7)
    b = optimal_block_length(rng.normal(size=500))
    assert b <= 3.0


def test_ar1_series_gives_larger_block_than_iid():
    rng = np.random.default_rng(7)
    n = 500
    x = np.empty(n)
    x[0] = rng.normal()
    for t in range(1, n):
        x[t] = 0.7 * x[t - 1] + rng.normal()
    b_ar = optimal_block_length(x)
    b_iid = optimal_block_length(rng.normal(size=n))
    assert b_ar > b_iid
    assert b_ar > 3.0


def test_tiny_series_degenerates_to_one():
    assert optimal_block_length(np.array([1.0, 2.0])) == 1.0


def test_resolve_block_len_clamps_to_registry_range():
    rng = np.random.default_rng(1)
    assert resolve_block_len(rng.normal(size=300)) == 2
    assert resolve_block_len(np.full(100, 5.0)) == 2
    n = 2000
    x = np.empty(n)
    x[0] = 0.0
    eps = rng.normal(size=n)
    for t in range(1, n):
        x[t] = 0.995 * x[t - 1] + eps[t]
    assert resolve_block_len(x) <= 30
    assert resolve_block_len(x, clamp=(2, 10)) <= 10


def test_ci_brackets_estimate_on_random_data():
    df = _frame(60, rows_per_day=4, seed=42)
    res = moving_block_bootstrap(
        df, _mean_error, "day", np.random.default_rng(0), n_boot=400, block_len=5
    )
    assert isinstance(res, BootstrapResult)
    assert res.ci_low <= res.estimate <= res.ci_high
    assert res.estimate == pytest.approx(_mean_error(df))
    assert res.n_days == 60
    assert res.block_len_days == 5
    assert res.draws.shape == (400,)
    assert res.alpha == 0.05


def test_block_len_one_identical_to_iid_shortcut():
    """Same seed => byte-identical draws via the block_len=1 path and iid_bootstrap."""
    df = _frame(40, seed=3)
    res_block1 = moving_block_bootstrap(
        df, _mean_error, "day", np.random.default_rng(123), n_boot=200, block_len=1
    )
    res_iid = iid_bootstrap(df, _mean_error, "day", np.random.default_rng(123), n_boot=200)
    assert np.array_equal(res_block1.draws, res_iid.draws)
    assert res_block1.ci_low == res_iid.ci_low
    assert res_block1.ci_high == res_iid.ci_high
    assert res_iid.block_len_days == 1


def test_reproducibility_same_seed_same_result():
    df = _frame(50, seed=5)
    kw = {"n_boot": 150, "block_len": 4}
    a = moving_block_bootstrap(df, _mean_error, "day", np.random.default_rng(9), **kw)
    b = moving_block_bootstrap(df, _mean_error, "day", np.random.default_rng(9), **kw)
    assert np.array_equal(a.draws, b.draws)
    assert (a.estimate, a.ci_low, a.ci_high) == (b.estimate, b.ci_low, b.ci_high)


def test_different_seeds_differ():
    df = _frame(50, seed=5)
    kw = {"n_boot": 150, "block_len": 4}
    a = moving_block_bootstrap(df, _mean_error, "day", np.random.default_rng(1), **kw)
    b = moving_block_bootstrap(df, _mean_error, "day", np.random.default_rng(2), **kw)
    assert not np.array_equal(a.draws, b.draws)


def test_repeated_days_enter_repeated_uniform_rows():
    """Every day has exactly r rows => every draw has exactly r * n_days rows.

    This pins two behaviors at once: the resample is truncated to exactly
    n_days days, and a day drawn k times contributes its rows k times.
    """
    n_days, r = 30, 4
    df = _frame(n_days, rows_per_day=r, seed=11)
    stat = lambda d: float(d.height) / n_days  # noqa: E731
    res = moving_block_bootstrap(
        df, stat, "day", np.random.default_rng(21), n_boot=100, block_len=7
    )
    assert np.all(res.draws == pytest.approx(float(r)))


def test_repeated_days_visible_with_unequal_rows_per_day():
    """One fat day (10 rows) among thin days (1 row): draw sizes must vary."""
    days = [date(2025, 7, 1) + timedelta(days=i) for i in range(20)]
    rows = []
    for i, d in enumerate(days):
        for _ in range(10 if i == 0 else 1):
            rows.append({"day": d, "error": float(i)})
    df = pl.DataFrame(rows)
    stat = lambda d: float(d.height)  # noqa: E731
    res = moving_block_bootstrap(
        df, stat, "day", np.random.default_rng(31), n_boot=200, block_len=1
    )
    assert len(np.unique(res.draws)) > 1
    assert res.draws.max() > df.height


def test_block_len_none_requires_error_column():
    df = _frame(30).rename({"error": "residual"})
    with pytest.raises(ValueError, match="error"):
        moving_block_bootstrap(df, lambda d: 0.0, "day", np.random.default_rng(0), n_boot=10)


def test_block_len_none_resolves_from_daily_error_series():
    df = _frame(120, rows_per_day=2, seed=8)
    res = moving_block_bootstrap(
        df, _mean_error, "day", np.random.default_rng(4), n_boot=50
    )
    assert 2 <= res.block_len_days <= 30


def test_block_len_longer_than_series_is_truncated():
    df = _frame(10)
    res = moving_block_bootstrap(
        df, _mean_error, "day", np.random.default_rng(0), n_boot=20, block_len=50
    )
    assert res.block_len_days == 10


def test_empty_frame_raises():
    df = pl.DataFrame({"day": [], "error": []})
    with pytest.raises(ValueError, match="empty"):
        moving_block_bootstrap(df, _mean_error, "day", np.random.default_rng(0))


def test_missing_day_col_raises():
    df = _frame(10)
    with pytest.raises(ValueError, match="day_col"):
        moving_block_bootstrap(df, _mean_error, "date", np.random.default_rng(0), block_len=2)


def test_blocks_are_contiguous_days():
    """With block_len == n_days there is a single possible block: the full series.

    Every draw must then equal the full-sample statistic — direct evidence
    that blocks are contiguous runs of the ordered observed days.
    """
    df = _frame(15, rows_per_day=2, seed=13)
    res = moving_block_bootstrap(
        df, _mean_error, "day", np.random.default_rng(2), n_boot=30, block_len=15
    )
    assert np.all(res.draws == pytest.approx(res.estimate))
