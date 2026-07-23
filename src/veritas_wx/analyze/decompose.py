"""Representativeness decomposition (non-negotiable #1).

Splits the total matched-pair MSE into the part explained by the
station-representativeness floor and the model-attributable remainder,
emitted as three coupled metric rows (registry ``decomp_group:
representativeness``)::

    mse_total       = mean((fcst - obs)^2)          over eligible pairs
    repr_floor_mean = mean(repr_floor)              over the SAME pairs
    mse_model_est   = max(mse_total - repr_floor_mean, 0)

Eligible pairs are ONLY those with a non-null ``repr_floor`` — no imputation,
ever. The three statistics are recomputed on the SAME bootstrap draws (one
shared day-block resample per draw), so ``mse_model_est`` stays coupled to
its components draw by draw; ``clipped_frac`` records the fraction of draws
in which the clip at zero acted (the registered flag for when the floor
exceeds the total error in a resample).
"""

import numpy as np
import polars as pl

from veritas_wx.analyze.bootstrap import (
    _day_row_groups,
    _iter_resample_row_indices,
    _resolve_block_len_from_daily_error,
)

_DECOMP_SCHEMA: dict[str, pl.DataType] = {
    "metric": pl.Utf8,
    "estimate": pl.Float64,
    "ci_low": pl.Float64,
    "ci_high": pl.Float64,
    "alpha": pl.Float64,
    "n_pairs": pl.Int64,
    "n_days": pl.Int32,
    "block_len_days": pl.Int16,
    "n_boot": pl.Int32,
    "clipped_frac": pl.Float64,
}


def _mse_total_stat(df: pl.DataFrame) -> float:
    """mean((fcst - obs)^2) — registry ``mse_total`` (eligible pairs only)."""
    return float(df.select(((pl.col("fcst") - pl.col("obs")) ** 2).mean()).item())


def _repr_floor_mean_stat(df: pl.DataFrame) -> float:
    """mean(repr_floor) — registry ``repr_floor_mean`` (same pairs as mse_total)."""
    return float(df.select(pl.col("repr_floor").mean()).item())


def representativeness_decomposition(
    df: pl.DataFrame,
    rng: np.random.Generator,
    day_col: str = "day",
    n_boot: int = 1000,
    block_len: int | None = None,
    alpha: float = 0.05,
) -> pl.DataFrame:
    """Three-line MSE decomposition with a coupled moving-block-bootstrap CI.

    Parameters mirror :func:`veritas_wx.analyze.bootstrap.moving_block_bootstrap`;
    ``df`` is a matched-pairs frame with ``fcst``, ``obs``, ``repr_floor`` and
    a ``day_col`` column. Only pairs with non-null ``repr_floor`` participate.
    ``block_len=None`` resolves Politis-White on the daily mean-error series
    of the ELIGIBLE pairs (an ``error = fcst - obs`` column is attached when
    absent).

    Per draw ``b`` (one shared day-block resample):
    ``t_b = mse_total``, ``f_b = repr_floor_mean``,
    ``m_b = max(t_b - f_b, 0)``; the clip indicator is ``t_b - f_b < 0``.
    Point estimates come from the full eligible frame with the same clip;
    ``clipped_frac`` (populated on the ``mse_model_est`` row, null on the
    component rows) is the fraction of draws where the clip acted.

    Returns a DataFrame with rows ``mse_total``, ``repr_floor_mean``,
    ``mse_model_est`` and columns
    ``(metric, estimate, ci_low, ci_high, alpha, n_pairs, n_days,
    block_len_days, n_boot, clipped_frac)``. Zero eligible pairs => an EMPTY
    DataFrame with this exact schema — never invented numbers.
    """
    for col in ("fcst", "obs", "repr_floor"):
        if col not in df.columns:
            raise ValueError(f"representativeness_decomposition requires column '{col}'")

    eligible = df.filter(pl.col("repr_floor").is_not_null())
    if eligible.height == 0:
        return pl.DataFrame(schema=_DECOMP_SCHEMA)

    if "error" not in eligible.columns:
        eligible = eligible.with_columns((pl.col("fcst") - pl.col("obs")).alias("error"))

    groups = _day_row_groups(eligible, day_col)
    n_days = len(groups)
    if block_len is None:
        block_len = _resolve_block_len_from_daily_error(eligible, day_col)
    if block_len < 1:
        raise ValueError(f"block_len must be >= 1, got {block_len}")
    effective_block_len = min(block_len, n_days)

    est_total = _mse_total_stat(eligible)
    est_floor = _repr_floor_mean_stat(eligible)
    est_model = max(est_total - est_floor, 0.0)

    draws_total = np.empty(n_boot, dtype=np.float64)
    draws_floor = np.empty(n_boot, dtype=np.float64)
    for i, rows in enumerate(
        _iter_resample_row_indices(groups, effective_block_len, rng, n_boot)
    ):
        sample = eligible[rows]
        draws_total[i] = _mse_total_stat(sample)
        draws_floor[i] = _repr_floor_mean_stat(sample)
    raw_model = draws_total - draws_floor
    clipped = raw_model < 0.0
    draws_model = np.maximum(raw_model, 0.0)
    clipped_frac = float(np.mean(clipped))

    q = [alpha / 2.0, 1.0 - alpha / 2.0]
    records = []
    for metric, estimate, draws, cf in (
        ("mse_total", est_total, draws_total, None),
        ("repr_floor_mean", est_floor, draws_floor, None),
        ("mse_model_est", est_model, draws_model, clipped_frac),
    ):
        lo, hi = np.quantile(draws, q)
        records.append(
            {
                "metric": metric,
                "estimate": estimate,
                "ci_low": float(lo),
                "ci_high": float(hi),
                "alpha": alpha,
                "n_pairs": eligible.height,
                "n_days": n_days,
                "block_len_days": effective_block_len,
                "n_boot": n_boot,
                "clipped_frac": cf,
            }
        )
    return pl.DataFrame(records, schema=_DECOMP_SCHEMA)
