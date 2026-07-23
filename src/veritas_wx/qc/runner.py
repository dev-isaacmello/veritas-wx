"""QC runner: composes the pure checks, accounts for every row and every flag.

QC never drops rows: rows_in == rows_out at every step (runlog enforces it).
What changes is qc_flags; per-bit flag counts are logged as extra fields so a
sudden spike in any check is visible immediately.
"""

import polars as pl

from veritas_wx.contracts import OBS_QC_V1, qc_bits, validate
from veritas_wx.qc import checks
from veritas_wx.runlog import log_stage


def _flag_counts(df: pl.DataFrame) -> dict[str, int]:
    return {
        name: int(df.filter((pl.col("qc_flags") & bit) != 0).height)
        for name, bit in qc_bits.ALL_BITS.items()
    }


def run_qc(
    obs: pl.DataFrame,
    params: dict,
    stations: pl.DataFrame,
    neighbor_pairs: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Apply all QC checks to canonical observations (OBS_V1 -> OBS_QC_V1)."""
    df = obs.sort("station_id", "variable", "valid_time").with_columns(
        pl.lit(0, dtype=pl.Int32).alias("qc_flags")
    )
    n = df.height

    stages: list[tuple[str, callable]] = [
        ("qc.range", lambda d: checks.range_check(d, params)),
        ("qc.step", lambda d: checks.step_check(d, params)),
        ("qc.persistence", lambda d: checks.persistence_check(d, params)),
        ("qc.metadata", lambda d: checks.metadata_check(d, params, stations)),
        ("qc.duplicate", lambda d: checks.duplicate_check(d, params, stations)),
    ]
    if neighbor_pairs is not None and neighbor_pairs.height > 0:
        stages.insert(3, ("qc.spatial", lambda d: checks.spatial_check(d, params, neighbor_pairs)))

    for stage_name, fn in stages:
        df = fn(df)
        log_stage(stage_name, rows_in=n, rows_out=df.height, dropped={}, **_flag_counts(df))

    return validate(df, OBS_QC_V1, "obs_qc")
