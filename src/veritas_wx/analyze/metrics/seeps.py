"""SEEPS (Stable Equitable Error in Probability Space) per station, with CI.

EXPLORATORY DIAGNOSTIC — not part of the frozen pre-registration
(metrics_registry.yaml is untouched); results belong outside the confirmatory
family.

Portions derived from WeatherBench-X (Copyright 2023 Google LLC, Apache
License 2.0), adapted from ``weatherbenchX/metrics/categorical.py`` (class
``SEEPS``): the dry/light/heavy categorization (dry: ``x <= dry_threshold``;
light: ``dry_threshold < x < wet_threshold``; heavy: ``x >= wet_threshold``),
the Rodwell et al. (2010) scoring matrix as a function of the climatological
dry probability ``p1``, and the ``p1 in [min_p1, max_p1]`` validity mask.

Scoring matrix (rows: forecast category, columns: observed category, order
dry/light/heavy), exactly as in WBX::

    0.5 * [[0,                    1/(1-p1),   4/(1-p1)],
           [1/p1,                 0,          3/(1-p1)],
           [1/p1 + 3/(2+p1),      3/(2+p1),   0       ]]

Differences from the WBX original, on purpose: long-format polars rows
instead of gridded xarray; native mm units (WBX converts the dry threshold to
meters); per-row ``p1`` / ``wet_threshold`` columns joined upstream from
:func:`veritas_wx.analyze.climatology.station_wet_stats` instead of a
gridded climatology lookup.

Skill convention: SEEPS is a PENALTY — 0 is a perfect score, larger is worse
(1 is the expected score of a naive climatological forecast). The raw value
is reported as-is, never transformed into a ``1 - SEEPS`` skill score.

Rodwell, Richardson, Hewson & Haiden (2010), "A new equitable score suitable
for verifying precipitation in numerical weather prediction", QJRMS 136.
"""

from functools import partial

import numpy as np
import polars as pl

from veritas_wx.analyze.bootstrap import BootstrapResult, moving_block_bootstrap

DRY_THRESHOLD_MM = 0.25
MIN_P1 = 0.1
MAX_P1 = 0.85

_REQUIRED_COLUMNS = ("fcst", "obs", "p1", "wet_threshold")


def _require_columns(df: pl.DataFrame) -> None:
    """Raise if any of the SEEPS input columns is missing."""
    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"SEEPS requires columns {_REQUIRED_COLUMNS}, missing {missing}; "
            "join p1/wet_threshold from analyze.climatology.station_wet_stats"
        )


def seeps_mask(
    df: pl.DataFrame,
    min_p1: float = MIN_P1,
    max_p1: float = MAX_P1,
) -> pl.Series:
    """Boolean Series: rows that enter the SEEPS mean.

    A row is in-mask when all four inputs are non-null and the climatological
    dry probability lies in the closed interval ``[min_p1, max_p1]`` — the
    WBX ``(p1 >= min_p1) & (p1 <= max_p1)`` mask that removes stations where
    SEEPS is unstable (persistently dry or persistently wet climates).
    Rows outside the mask are EXCLUDED from :func:`seeps_stat`; callers count
    them from this Series (``(~mask).sum()``) for run bookkeeping.
    """
    _require_columns(df)
    valid = pl.all_horizontal(pl.col(c).is_not_null() for c in _REQUIRED_COLUMNS)
    in_range = (pl.col("p1") >= min_p1) & (pl.col("p1") <= max_p1)
    return df.select((valid & in_range).alias("in_mask"))["in_mask"]


def _category_flags(col: str, dry_threshold_mm: float) -> tuple[pl.Expr, pl.Expr, pl.Expr]:
    """(dry, light, heavy) boolean expressions for one precipitation column.

    WBX ``_convert_precip_to_seeps_cat`` boundaries: dry is
    ``x <= dry_threshold`` (inclusive, as in WBX — note WB2's climatology
    dry fraction uses a strict ``<``; the tie at exactly the threshold is
    measure-zero in practice), light is strictly between the thresholds,
    heavy is ``x >= wet_threshold``.
    """
    x = pl.col(col)
    wet = pl.col("wet_threshold")
    return (x <= dry_threshold_mm, (x > dry_threshold_mm) & (x < wet), x >= wet)


def _score_expr(dry_threshold_mm: float) -> pl.Expr:
    """Per-row SEEPS penalty from the Rodwell matrix, as a polars expression."""
    f_dry, f_light, f_heavy = _category_flags("fcst", dry_threshold_mm)
    o_dry, o_light, o_heavy = _category_flags("obs", dry_threshold_mm)
    p1 = pl.col("p1")
    return 0.5 * (
        pl.when(f_dry & o_light)
        .then(1.0 / (1.0 - p1))
        .when(f_dry & o_heavy)
        .then(4.0 / (1.0 - p1))
        .when(f_light & o_dry)
        .then(1.0 / p1)
        .when(f_light & o_heavy)
        .then(3.0 / (1.0 - p1))
        .when(f_heavy & o_dry)
        .then(1.0 / p1 + 3.0 / (2.0 + p1))
        .when(f_heavy & o_light)
        .then(3.0 / (2.0 + p1))
        .otherwise(0.0)
    )


def seeps_stat(
    df: pl.DataFrame,
    dry_threshold_mm: float = DRY_THRESHOLD_MM,
    min_p1: float = MIN_P1,
    max_p1: float = MAX_P1,
) -> float:
    """Mean SEEPS penalty over the in-mask rows (bare float, bootstrap block).

    Expects the matched-pairs frame with per-row ``fcst``, ``obs`` (24 h
    precipitation accumulations, mm), ``p1`` and ``wet_threshold`` (mm,
    joined per station from ``station_wet_stats``). Each row is categorized
    dry/light/heavy on both sides, scored by the Rodwell matrix evaluated at
    the row's ``p1``, and the in-mask scores (see :func:`seeps_mask`) are
    averaged. 0 = perfect; larger = worse. Returns NaN when no row survives
    the mask — never a fabricated value.
    """
    _require_columns(df)
    masked = df.filter(seeps_mask(df, min_p1=min_p1, max_p1=max_p1))
    if masked.height == 0:
        return float("nan")
    return float(masked.select(_score_expr(dry_threshold_mm).mean()).item())


def seeps(
    df: pl.DataFrame,
    rng: np.random.Generator,
    day_col: str = "day",
    dry_threshold_mm: float = DRY_THRESHOLD_MM,
    min_p1: float = MIN_P1,
    max_p1: float = MAX_P1,
    n_boot: int = 1000,
    block_len: int | None = None,
    alpha: float = 0.05,
) -> BootstrapResult:
    """SEEPS with a moving-block-bootstrap percentile CI (days as unit).

    Same pattern as the public metrics in ``analyze.metrics.core``: the
    masked rows are resampled by whole days (``day_col``) via
    :func:`veritas_wx.analyze.bootstrap.moving_block_bootstrap` and
    :func:`seeps_stat` is re-evaluated per draw. The mask is applied ONCE up
    front (``p1`` is a per-station constant, so masking commutes with day
    resampling) — ``n_days`` in the result therefore counts in-mask days
    only, and excluded-row counts should be taken from :func:`seeps_mask` on
    the unfiltered frame. Raises on an all-masked (empty) input rather than
    bootstrapping nothing.

    ``block_len=None`` resolves the block length via Politis-White on the
    daily mean-error series; the ``error = fcst - obs`` column is attached
    here when absent. Lower is better; 0 is a perfect forecast.
    """
    _require_columns(df)
    masked = df.filter(seeps_mask(df, min_p1=min_p1, max_p1=max_p1))
    if masked.height == 0:
        raise ValueError(
            "seeps: no rows left after the p1/null mask "
            f"(p1 in [{min_p1}, {max_p1}]); nothing to bootstrap"
        )
    if "error" not in masked.columns:
        masked = masked.with_columns((pl.col("fcst") - pl.col("obs")).alias("error"))
    statistic = partial(
        seeps_stat, dry_threshold_mm=dry_threshold_mm, min_p1=min_p1, max_p1=max_p1
    )
    return moving_block_bootstrap(
        masked,
        statistic,
        day_col,
        rng,
        n_boot=n_boot,
        block_len=block_len,
        alpha=alpha,
    )
