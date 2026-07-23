"""Empirical CI coverage on synthetic AR(1) — the R8 calibration gate.

If the block bootstrap is miscalibrated, the project's central thesis
(honest uncertainty) falls. Ground truth here is analytic: an AR(1) series
``x_t = phi * x_{t-1} + eps_t`` with ``eps ~ N(0, 1)`` has true mean 0, so a
95% CI for the mean should contain 0 in ~95% of replicates. The iid
bootstrap ignores the autocorrelation, understates the variance of the mean
by a factor ``(1 + phi) / (1 - phi)`` (= 3 at phi = 0.5) and must therefore
undercover — demonstrating exactly why the block method exists.
"""

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from veritas_wx.analyze.bootstrap import iid_bootstrap, moving_block_bootstrap

TRUE_MEAN = 0.0


def _ar1_series(rng: np.random.Generator, n: int, phi: float, sigma: float = 1.0) -> np.ndarray:
    """Stationary AR(1) with mean 0: x_t = phi * x_{t-1} + eps_t."""
    x = np.empty(n)
    x[0] = rng.normal(0.0, sigma / np.sqrt(1.0 - phi**2))
    eps = rng.normal(0.0, sigma, n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + eps[t]
    return x


def _daily_frame(x: np.ndarray) -> pl.DataFrame:
    days = [date(2025, 7, 1) + timedelta(days=i) for i in range(x.size)]
    return pl.DataFrame({"day": days, "error": x})


def _mean_error(df: pl.DataFrame) -> float:
    return float(df["error"].mean())


def _covers(res) -> bool:
    return res.ci_low <= TRUE_MEAN <= res.ci_high


@pytest.mark.slow
def test_block_bootstrap_covers_and_beats_iid_on_ar1():
    """AR(1) phi=0.5, n=180 days, ~200 replicates x n_boot=500.

    Block length resolved per replicate by Politis-White on the daily series
    (block_len=None — the registered pipeline). Acceptance:
      - empirical coverage of the 95% block CI in [0.90, 0.98];
      - iid bootstrap (block_len=1) coverage strictly LOWER on the same
        replicates — the raison d'etre of the block method.
    """
    n_days, phi, n_boot, n_replicates = 180, 0.5, 500, 200
    rng = np.random.default_rng(20250723)

    block_hits = 0
    iid_hits = 0
    for _ in range(n_replicates):
        df = _daily_frame(_ar1_series(rng, n_days, phi))
        res_block = moving_block_bootstrap(
            df, _mean_error, "day", rng, n_boot=n_boot, block_len=None
        )
        res_iid = iid_bootstrap(df, _mean_error, "day", rng, n_boot=n_boot)
        block_hits += _covers(res_block)
        iid_hits += _covers(res_iid)

    cov_block = block_hits / n_replicates
    cov_iid = iid_hits / n_replicates

    assert 0.90 <= cov_block <= 0.98, f"block coverage {cov_block:.3f} outside [0.90, 0.98]"
    assert cov_iid < cov_block, (
        f"iid coverage {cov_iid:.3f} not below block coverage {cov_block:.3f} — "
        "blocks are not buying anything"
    )
    assert cov_iid < 0.90, f"iid coverage {cov_iid:.3f} suspiciously high on AR(1)"


def test_iid_series_sanity_coverage():
    """Fast gate (not slow): on a truly iid series the CI covers ~95%.

    50 replicates x n_boot=200, block length resolved by Politis-White
    (degenerates to the registry floor of 2 on iid data). Wide acceptance
    band [0.85, 1.0] keeps the test cheap yet able to catch gross
    miscalibration (e.g. quantiles swapped, truncation dropping days).
    """
    n_days, n_boot, n_replicates = 100, 200, 50
    rng = np.random.default_rng(42)

    hits = 0
    for _ in range(n_replicates):
        df = _daily_frame(rng.normal(0.0, 1.0, n_days))
        res = moving_block_bootstrap(df, _mean_error, "day", rng, n_boot=n_boot, block_len=None)
        hits += _covers(res)

    coverage = hits / n_replicates
    assert 0.85 <= coverage <= 1.0, f"iid-series coverage {coverage:.3f} outside [0.85, 1.0]"
