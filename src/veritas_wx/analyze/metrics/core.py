"""Core verification metrics, mirroring metrics_registry.yaml definitions.

Two layers, both pure:

- ``*_stat`` functions: scalar statistics over a matched-pairs DataFrame with
  columns ``(fcst, obs [, station_id, day])``. These are the resample-able
  building blocks handed to the bootstrap; they return bare floats and are
  NOT part of the public estimate API.
- Public functions (``mae``, ``rmse``, ``bias``, ``variance_ratio``,
  ``bias_by_percentile``): every one of them routes through
  :func:`veritas_wx.analyze.bootstrap.moving_block_bootstrap` and returns a
  :class:`BootstrapResult` (or a DataFrame carrying ``ci_low``/``ci_high``).
  No public aggregation ever returns an estimate without a confidence
  interval — non-negotiable #3, guarded by
  ``tests/unit/test_no_estimate_without_ci.py``.

Which forecast column feeds ``fcst`` (raw vs elevation-adjusted,
``fcst_input`` in the registry) is decided upstream when the matched-pairs
view is projected; these functions only ever see ``fcst``/``obs``.
"""

import math

import numpy as np
import polars as pl

from veritas_wx.analyze.bootstrap import BootstrapResult, moving_block_bootstrap

#: Percentile bins registered in metrics_registry.yaml
#: (strata.obs_percentile_bin). Half-open [lo, hi), except the last bin which
#: is closed [99, 100] so that obs_pct == 100 belongs to "99-100".
REGISTRY_BINS: tuple[str, ...] = (
    "0-10",
    "10-20",
    "20-30",
    "30-40",
    "40-50",
    "50-60",
    "60-70",
    "70-80",
    "80-90",
    "90-99",
    "99-100",
)


# ---------------------------------------------------------------------------
# Scalar statistics (bootstrap building blocks — floats, not public API)
# ---------------------------------------------------------------------------
def mae_stat(df: pl.DataFrame) -> float:
    """Registry ``mae``: mean(|fcst - obs|)."""
    return float(df.select((pl.col("fcst") - pl.col("obs")).abs().mean()).item())


def rmse_stat(df: pl.DataFrame) -> float:
    """Registry ``rmse``: sqrt(mean((fcst - obs)^2))."""
    mse = float(df.select(((pl.col("fcst") - pl.col("obs")) ** 2).mean()).item())
    return math.sqrt(mse)


def bias_stat(df: pl.DataFrame) -> float:
    """Registry ``bias``: mean(fcst - obs)."""
    return float(df.select((pl.col("fcst") - pl.col("obs")).mean()).item())


def _station_variance_ratios(df: pl.DataFrame) -> pl.DataFrame:
    """Per-station ``std(fcst)/std(obs)`` (ddof=1) with exclusion bookkeeping.

    Returns one row per station: ``(station_id, ratio, excluded)``. A station
    is excluded (``ratio`` null, ``excluded`` true) when ``std(obs)`` is 0 or
    not computable in the given (re)sample — fewer than 2 pairs, or an
    obs series that is constant within the draw. Diagnostic helper for
    counting exclusions; the median in :func:`variance_ratio_stat` is taken
    over the non-excluded stations only.
    """
    per_station = df.group_by("station_id").agg(
        pl.col("fcst").std(ddof=1).alias("_std_fcst"),
        pl.col("obs").std(ddof=1).alias("_std_obs"),
    )
    return per_station.select(
        pl.col("station_id"),
        pl.when(pl.col("_std_obs") > 0.0)
        .then(pl.col("_std_fcst") / pl.col("_std_obs"))
        .otherwise(None)
        .alias("ratio"),
        (pl.col("_std_obs").is_null() | (pl.col("_std_obs") <= 0.0)).alias("excluded"),
    )


def variance_ratio_stat(df: pl.DataFrame) -> float:
    """Registry ``variance_ratio``: median over stations of std(fcst)/std(obs).

    Per station (requires a ``station_id`` column): sample standard deviation
    ratio with ``ddof=1``, aggregated across stations as the MEDIAN — exactly
    as registered. Stations whose ``std(obs)`` is zero (or not computable:
    fewer than 2 pairs in the draw) are EXCLUDED from the median; they are
    counted per draw via :func:`_station_variance_ratios` for diagnostics.
    Returns NaN when every station is excluded (the bootstrap propagates it
    into the draws rather than inventing a value).
    """
    ratios = _station_variance_ratios(df)["ratio"].drop_nulls().to_numpy()
    if ratios.size == 0:
        return float("nan")
    return float(np.median(ratios))


# ---------------------------------------------------------------------------
# Public API — always through the moving block bootstrap
# ---------------------------------------------------------------------------
def _with_error(df: pl.DataFrame) -> pl.DataFrame:
    """Attach ``error = fcst - obs`` when absent (pure; input untouched).

    The ``error`` column feeds the Politis-White block-length selection on the
    daily mean-error series when ``block_len=None`` (registry:
    ``block_length.series = daily_domain_mean_error``).
    """
    if "error" in df.columns:
        return df
    return df.with_columns((pl.col("fcst") - pl.col("obs")).alias("error"))


def mae(
    df: pl.DataFrame,
    rng: np.random.Generator,
    day_col: str = "day",
    n_boot: int = 1000,
    block_len: int | None = None,
    alpha: float = 0.05,
) -> BootstrapResult:
    """Mean absolute error with a moving-block-bootstrap percentile CI."""
    return moving_block_bootstrap(
        _with_error(df), mae_stat, day_col, rng, n_boot=n_boot, block_len=block_len, alpha=alpha
    )


def rmse(
    df: pl.DataFrame,
    rng: np.random.Generator,
    day_col: str = "day",
    n_boot: int = 1000,
    block_len: int | None = None,
    alpha: float = 0.05,
) -> BootstrapResult:
    """Root mean squared error with a moving-block-bootstrap percentile CI."""
    return moving_block_bootstrap(
        _with_error(df), rmse_stat, day_col, rng, n_boot=n_boot, block_len=block_len, alpha=alpha
    )


def bias(
    df: pl.DataFrame,
    rng: np.random.Generator,
    day_col: str = "day",
    n_boot: int = 1000,
    block_len: int | None = None,
    alpha: float = 0.05,
) -> BootstrapResult:
    """Mean error (fcst - obs) with a moving-block-bootstrap percentile CI."""
    return moving_block_bootstrap(
        _with_error(df), bias_stat, day_col, rng, n_boot=n_boot, block_len=block_len, alpha=alpha
    )


def variance_ratio(
    df: pl.DataFrame,
    rng: np.random.Generator,
    day_col: str = "day",
    n_boot: int = 1000,
    block_len: int | None = None,
    alpha: float = 0.05,
) -> BootstrapResult:
    """Median-over-stations std(fcst)/std(obs), with bootstrap percentile CI.

    Recomputed per draw, as registered: each bootstrap resample re-derives
    the per-station ratios (ddof=1, zero-obs-std stations excluded) and their
    median. Requires ``station_id``.
    """
    return moving_block_bootstrap(
        _with_error(df),
        variance_ratio_stat,
        day_col,
        rng,
        n_boot=n_boot,
        block_len=block_len,
        alpha=alpha,
    )


def _bin_bounds(label: str) -> tuple[float, float]:
    """Parse a registry bin label ``"lo-hi"`` into numeric bounds."""
    lo_s, hi_s = label.split("-")
    return float(lo_s), float(hi_s)


def bias_by_percentile(
    df: pl.DataFrame,
    rng: np.random.Generator,
    bins: tuple[str, ...] = REGISTRY_BINS,
    day_col: str = "day",
    n_boot: int = 1000,
    block_len: int | None = None,
    alpha: float = 0.05,
) -> pl.DataFrame:
    """Registry ``bias_by_percentile``: mean error conditional on obs percentile bin.

    Expects an ``obs_pct`` column: the empirical percentile (0-100] of the
    observation within (station, variable) over the full window,
    pre-computed by :func:`veritas_wx.analyze.strata.obs_percentile`. Rows are
    assigned to the registry bins — half-open ``[lo, hi)`` except the final
    bin, which is closed ``[99, 100]`` — and the bias of each bin gets its own
    moving-block-bootstrap CI (block length resolved per bin when
    ``block_len=None``).

    Returns one row PER BIN, in registry order:
    ``(bin, estimate, ci_low, ci_high, n_pairs)``. Empty bins yield a row
    with null estimate/CI and ``n_pairs = 0`` — recorded as absent, never
    invented.
    """
    if "obs_pct" not in df.columns:
        raise ValueError(
            "bias_by_percentile requires an 'obs_pct' column "
            "(see veritas_wx.analyze.strata.obs_percentile)"
        )
    df = _with_error(df)
    last_label = bins[-1]
    rows: list[dict] = []
    for label in bins:
        lo, hi = _bin_bounds(label)
        cond = pl.col("obs_pct") >= lo
        cond = cond & (pl.col("obs_pct") <= hi if label == last_label else pl.col("obs_pct") < hi)
        sub = df.filter(cond)
        if sub.height == 0:
            rows.append(
                {"bin": label, "estimate": None, "ci_low": None, "ci_high": None, "n_pairs": 0}
            )
            continue
        res = moving_block_bootstrap(
            sub, bias_stat, day_col, rng, n_boot=n_boot, block_len=block_len, alpha=alpha
        )
        rows.append(
            {
                "bin": label,
                "estimate": res.estimate,
                "ci_low": res.ci_low,
                "ci_high": res.ci_high,
                "n_pairs": sub.height,
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "bin": pl.Utf8,
            "estimate": pl.Float64,
            "ci_low": pl.Float64,
            "ci_high": pl.Float64,
            "n_pairs": pl.Int64,
        },
    )
