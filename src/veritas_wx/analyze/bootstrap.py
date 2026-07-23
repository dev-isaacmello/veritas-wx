"""Moving block bootstrap over days (registry: resample_unit = day_blocks).

Pure functions only: DataFrames/arrays in, values out. No I/O, no global state,
no hidden randomness — the RNG is always an explicit ``numpy.random.Generator``
argument supplied by the caller.

Resampling unit
---------------
The unit of resampling is the DAY (unique values of ``day_col``), never the
individual row. All rows of a sampled day — every station and both runs
(00Z/12Z) — move together, preserving both temporal and spatial correlation.
Resampling stations independently would understate the confidence interval
(PLAN.md §2.7, metrics_registry.yaml ``defaults.resample_unit``).

Block length selection follows Politis & White (2004), with the clamp
``[2, 30]`` days taken from ``metrics_registry.yaml``
(``defaults.block_length.clamp_days``).
"""

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np
import polars as pl

REGISTRY_BLOCK_CLAMP_DAYS: tuple[int, int] = (2, 30)


@dataclass(frozen=True)
class BootstrapResult:
    """A point estimate that never travels without its confidence interval.

    Attributes
    ----------
    estimate:
        The statistic evaluated on the full (un-resampled) input.
    ci_low, ci_high:
        Percentile confidence bounds at ``alpha/2`` and ``1 - alpha/2``.
    alpha:
        Two-sided significance level (0.05 => 95% CI).
    n_boot:
        Number of bootstrap draws.
    block_len_days:
        Effective block length used, in days (after any clamp/truncation).
    n_days:
        Number of unique days in the input — the resample sample size.
    draws:
        The ``n_boot`` bootstrap replicates of the statistic, for downstream
        use (paired differences, p-values). Kept as a plain ndarray.
    """

    estimate: float
    ci_low: float
    ci_high: float
    alpha: float
    n_boot: int
    block_len_days: int
    n_days: int
    draws: np.ndarray


def optimal_block_length(x: np.ndarray) -> float:
    """Politis-White (2004) automatic block length for the sample mean.

    Practical version (Politis & White 2004; correction by Patton, Politis &
    White 2009), for the moving/circular block bootstrap:

    1. Autocovariances of the demeaned series::

           R(k) = (1/n) * sum_{t=1}^{n-k} (x_t - xbar) * (x_{t+k} - xbar)

    2. Bandwidth selection: with ``K_n = max(5, ceil(sqrt(log10(n))))`` and
       significance threshold ``c * sqrt(log10(n) / n)`` with ``c = 2`` on the
       autocorrelations ``rho(k) = R(k)/R(0)``, take ``m_hat`` as the smallest
       lag ``m >= 0`` such that all of ``rho(m+1), ..., rho(m+K_n)`` are
       insignificant; the flat-top bandwidth is ``M = 2 * m_hat``.

    3. Flat-top (trapezoidal) lag window::

           lambda(t) = 1                for |t| <= 1/2
           lambda(t) = 2 * (1 - |t|)    for 1/2 < |t| <= 1
           lambda(t) = 0                otherwise

    4. Weighted autocovariance sums::

           g      = sum_{k=-M}^{M} lambda(k/M) * |k| * R(|k|)
           ghat0  = sum_{k=-M}^{M} lambda(k/M) * R(|k|)
           d      = (4/3) * ghat0**2      # moving/circular block variant

    5. Optimal block length for the sample mean::

           b_opt = (2 * g**2 / d)**(1/3) * n**(1/3)

       capped at ``ceil(min(3 * sqrt(n), n / 3))`` and floored at 1.0.

    Degenerate cases: a constant series (zero variance) returns 1.0; an iid
    series yields ``m_hat = 0 => g = 0 => b_opt`` floored to 1.0 (a small
    value, as expected — no dependence, no need for blocks).

    Reference: D.N. Politis, H. White (2004), "Automatic Block-Length
    Selection for the Dependent Bootstrap", Econometric Reviews 23(1), 53-70;
    and A. Patton, D.N. Politis, H. White (2009) correction, Econometric
    Reviews 28(4), 372-375.
    """
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1:
        raise ValueError(f"optimal_block_length expects a 1-D array, got shape {x.shape}")
    n = x.size
    if n < 4:
        return 1.0

    centered = x - x.mean()
    r0 = float(centered @ centered) / n
    if r0 <= 0.0 or not np.isfinite(r0):
        return 1.0

    k_n = max(5, math.ceil(math.sqrt(math.log10(n))))
    m_max = math.ceil(math.sqrt(n)) + k_n
    lag_cap = n - 1
    acv_lags = min(2 * m_max, lag_cap)

    acv = np.empty(acv_lags + 1)
    for k in range(acv_lags + 1):
        acv[k] = float(centered[: n - k] @ centered[k:]) / n
    rho = acv[1:] / acv[0]

    threshold = 2.0 * math.sqrt(math.log10(n) / n)
    insignificant = np.abs(rho) <= threshold

    m_hat = m_max
    for m in range(m_max + 1):
        window = insignificant[m : m + k_n]
        if window.size == k_n and bool(window.all()):
            m_hat = m
            break

    big_m = min(2 * m_hat, lag_cap)
    if big_m == 0:
        return 1.0

    lags = np.arange(1, big_m + 1)
    t = lags / big_m
    lam = np.where(t <= 0.5, 1.0, 2.0 * (1.0 - t))
    lam = np.clip(lam, 0.0, 1.0)

    g = 2.0 * float(np.sum(lam * lags * acv[1 : big_m + 1]))
    ghat0 = acv[0] + 2.0 * float(np.sum(lam * acv[1 : big_m + 1]))
    d = (4.0 / 3.0) * ghat0**2
    if d <= 0.0 or not np.isfinite(d):
        return 1.0

    b_opt = (2.0 * g**2 / d) ** (1.0 / 3.0) * n ** (1.0 / 3.0)
    b_max = math.ceil(min(3.0 * math.sqrt(n), n / 3.0))
    return float(min(max(b_opt, 1.0), b_max))


def resolve_block_len(
    x: np.ndarray,
    clamp: tuple[int, int] = REGISTRY_BLOCK_CLAMP_DAYS,
) -> int:
    """``ceil(optimal_block_length(x))`` clamped to ``clamp`` (registry: [2, 30] days)."""
    lo, hi = clamp
    if not (1 <= lo <= hi):
        raise ValueError(f"invalid clamp {clamp}: need 1 <= lo <= hi")
    b = math.ceil(optimal_block_length(x))
    return int(min(max(b, lo), hi))


def _day_row_groups(df: pl.DataFrame, day_col: str) -> list[np.ndarray]:
    """Row indices of ``df`` grouped by day, ordered by the day value.

    Position ``i`` of the returned list holds the row indices of the i-th
    observed day in ascending ``day_col`` order. Calendar gaps are irrelevant:
    blocks are contiguous in this *observed* sequence.
    """
    if day_col not in df.columns:
        raise ValueError(f"day_col '{day_col}' not found in DataFrame columns {df.columns}")
    idx = (
        df.select(pl.col(day_col))
        .with_row_index("_row")
        .group_by(day_col)
        .agg(pl.col("_row"))
        .sort(day_col)
    )
    return [np.asarray(a, dtype=np.int64) for a in idx["_row"].to_list()]


def _sample_day_positions(n_days: int, block_len: int, rng: np.random.Generator) -> np.ndarray:
    """One moving-block resample of day *positions* ``0..n_days-1``.

    Draws ``ceil(n_days / block_len)`` block start positions uniformly with
    replacement from ``[0, n_days - block_len]``, expands each start into the
    ``block_len`` consecutive positions, concatenates, and truncates to
    exactly ``n_days`` positions. Repeated days stay repeated.
    """
    n_blocks = math.ceil(n_days / block_len)
    starts = rng.integers(0, n_days - block_len + 1, size=n_blocks)
    positions = (starts[:, None] + np.arange(block_len)[None, :]).ravel()
    return positions[:n_days]


def _iter_resample_row_indices(
    groups: list[np.ndarray],
    block_len: int,
    rng: np.random.Generator,
    n_boot: int,
) -> Iterator[np.ndarray]:
    """Yield ``n_boot`` row-index arrays, one moving-block resample each.

    Shared machinery for :func:`moving_block_bootstrap` and for coupled
    multi-statistic resampling (``analyze.decompose``): every consumer of a
    draw sees the SAME set of resampled days, so derived statistics stay
    coupled draw by draw.
    """
    n_days = len(groups)
    for _ in range(n_boot):
        positions = _sample_day_positions(n_days, block_len, rng)
        yield np.concatenate([groups[p] for p in positions])


def _resolve_block_len_from_daily_error(df: pl.DataFrame, day_col: str) -> int:
    """Registry default: block length from the daily mean-error series.

    Requires an ``error`` column (``fcst - obs``); public metric wrappers add
    it automatically. metrics_registry.yaml: ``block_length.series =
    daily_domain_mean_error``, method Politis-White, clamp [2, 30].
    """
    if "error" not in df.columns:
        raise ValueError(
            "block_len=None requires an 'error' column (fcst - obs) to build the "
            "daily mean-error series for Politis-White selection "
            "(metrics_registry.yaml: block_length.series=daily_domain_mean_error). "
            "Add the column or pass block_len explicitly."
        )
    daily = (
        df.group_by(day_col).agg(pl.col("error").mean().alias("_daily_error")).sort(day_col)
    )
    return resolve_block_len(daily["_daily_error"].to_numpy())


def moving_block_bootstrap(
    df: pl.DataFrame,
    statistic: Callable[[pl.DataFrame], float],
    day_col: str,
    rng: np.random.Generator,
    n_boot: int = 1000,
    block_len: int | None = None,
    alpha: float = 0.05,
) -> BootstrapResult:
    """Moving block bootstrap with DAYS as the resampling unit.

    The ordered sequence of *observed* unique days of ``day_col`` (length
    ``n_days``) is resampled in contiguous moving blocks of ``block_len``
    days: block starts are drawn uniformly with replacement from
    ``[0, n_days - block_len]``, blocks are concatenated and truncated to
    exactly ``n_days`` days. For every sampled day, ALL rows of that day enter
    the resample (all stations, both runs — spatial correlation preserved);
    days repeated in the resample contribute their rows repeatedly.

    ``block_len=None`` resolves the length via Politis-White on the daily
    mean-error series (requires an ``error`` column = ``fcst - obs``; see
    :func:`_resolve_block_len_from_daily_error`), clamped to [2, 30] days per
    the registry. A ``block_len`` larger than ``n_days`` is truncated to
    ``n_days`` (and recorded as such in ``block_len_days``).

    Returns a :class:`BootstrapResult` where ``estimate = statistic(df)`` on
    the full input and the CI is the percentile interval of the draws at
    ``alpha/2`` and ``1 - alpha/2``.
    """
    if df.height == 0:
        raise ValueError("moving_block_bootstrap: empty DataFrame (no days to resample)")
    if n_boot < 1:
        raise ValueError(f"n_boot must be >= 1, got {n_boot}")
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")

    groups = _day_row_groups(df, day_col)
    n_days = len(groups)

    if block_len is None:
        block_len = _resolve_block_len_from_daily_error(df, day_col)
    if block_len < 1:
        raise ValueError(f"block_len must be >= 1, got {block_len}")
    effective_block_len = min(block_len, n_days)

    estimate = float(statistic(df))
    draws = np.empty(n_boot, dtype=np.float64)
    for i, rows in enumerate(
        _iter_resample_row_indices(groups, effective_block_len, rng, n_boot)
    ):
        draws[i] = float(statistic(df[rows]))

    ci_low, ci_high = np.quantile(draws, [alpha / 2.0, 1.0 - alpha / 2.0])
    return BootstrapResult(
        estimate=estimate,
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        alpha=alpha,
        n_boot=n_boot,
        block_len_days=effective_block_len,
        n_days=n_days,
        draws=draws,
    )


def iid_bootstrap(
    df: pl.DataFrame,
    statistic: Callable[[pl.DataFrame], float],
    day_col: str,
    rng: np.random.Generator,
    n_boot: int = 1000,
    alpha: float = 0.05,
) -> BootstrapResult:
    """iid bootstrap over days: the ``block_len=1`` special case.

    Documented shortcut through the same machine — with blocks of one day,
    every "block" is a single day drawn uniformly with replacement, which is
    exactly the iid bootstrap on the day level. Given the same RNG state it
    produces draws identical to ``moving_block_bootstrap(..., block_len=1)``.

    Kept public for the calibration comparison (R8): on autocorrelated series
    the iid CI is too narrow; the block CI is the honest one.
    """
    return moving_block_bootstrap(
        df,
        statistic,
        day_col=day_col,
        rng=rng,
        n_boot=n_boot,
        block_len=1,
        alpha=alpha,
    )
