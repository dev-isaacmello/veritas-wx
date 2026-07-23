"""Exactly matched comparison views (non-negotiable #5).

Model comparisons NEVER read the fact table directly: they read a materialized
view where every (init_time, valid_time, station_id, variable) key has valid
data for ALL models in the comparison, under the SAME QC mask. Different
samples => invalid comparison; this module makes the invalid state
unrepresentable.
"""

import polars as pl

_KEY = ["init_time", "valid_time", "station_id", "variable"]


def comparison_id(models: list[str]) -> str:
    """Canonical id: sorted, '+'-joined (e.g. 'aifs+gfs+graphcast+hres')."""
    if len(set(models)) != len(models) or len(models) < 2:
        raise ValueError(f"comparison needs >= 2 distinct models, got {models}")
    return "+".join(sorted(models))


def matched_view(
    fact: pl.DataFrame,
    models: list[str],
    qc_mask: int | None = None,
) -> tuple[pl.DataFrame, dict]:
    """Filter fact to exactly matched pairs across ``models``.

    qc_mask: bits that must be CLEAR for a pair to qualify (None => require
    qc_flags == 0, the strictest rigor). Returns (long-format view, manifest
    dict recording comparison_id, models, qc_mask and n counts).
    """
    cid = comparison_id(models)
    eligible = fact.filter(
        pl.col("model").is_in(models)
        & pl.col("obs").is_not_null()
        & pl.col("fcst_raw").is_not_null()
    )
    if qc_mask is None:
        eligible = eligible.filter(pl.col("qc_flags") == 0)
    else:
        eligible = eligible.filter((pl.col("qc_flags") & qc_mask) == 0)

    complete_keys = (
        eligible.group_by(_KEY)
        .agg(pl.col("model").n_unique().alias("_n_models"))
        .filter(pl.col("_n_models") == len(models))
        .select(_KEY)
    )
    view = eligible.join(complete_keys, on=_KEY, how="inner").sort(
        ["variable", "station_id", "valid_time", "model"]
    )

    manifest = {
        "comparison_id": cid,
        "models": sorted(models),
        "qc_mask": qc_mask,
        "n_rows": view.height,
        "n_matched_keys": complete_keys.height,
        "n_stations": view["station_id"].n_unique() if view.height else 0,
    }
    return view, manifest
