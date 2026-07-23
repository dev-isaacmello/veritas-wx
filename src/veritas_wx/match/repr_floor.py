"""Empirical representativeness floor per 0.25° cell (non-negotiable #1).

Estimator (frozen in PLAN.md §2.5): for each (cell, variable) with >= 2
distinct stations reporting at the same valid_time, take the sample variance
across stations at that instant; the floor is the TEMPORAL MEDIAN of those
variances (units: value²). Cells with < 2 stations => no row here, and the
fact builder writes NULL — never imputed.

Honest naming note: the floor includes instrument error + true subgrid
variability; both are "not model error", so subtracting remains a valid
decomposition of total error.
"""

import polars as pl


def cell_of(lat_col: str = "lat", lon_col: str = "lon", res: float = 0.25) -> list[pl.Expr]:
    """Grid cell identity exprs (floor division — stable for negative coords)."""
    return [
        (pl.col(lat_col) / res).floor().cast(pl.Int32).alias("cell_y"),
        (pl.col(lon_col) / res).floor().cast(pl.Int32).alias("cell_x"),
    ]


def repr_floor_by_cell(
    obs: pl.DataFrame,
    stations: pl.DataFrame,
    res: float = 0.25,
    min_stations: int = 2,
) -> pl.DataFrame:
    """(cell_y, cell_x, variable, repr_floor, n_stations, n_instants).

    ``obs``: OBS-shaped rows (station_id, valid_time, variable, value) —
    caller pre-filters by QC rigor. ``stations``: station_id, lat, lon.
    """
    located = obs.join(
        stations.select("station_id", *cell_of()), on="station_id", how="inner"
    )

    per_instant = (
        located.group_by(["cell_y", "cell_x", "variable", "valid_time"])
        .agg(
            pl.col("value").var(ddof=1).alias("_var_across"),
            pl.col("station_id").n_unique().alias("_n_st"),
        )
        .filter(pl.col("_n_st") >= min_stations)
    )

    return (
        per_instant.group_by(["cell_y", "cell_x", "variable"])
        .agg(
            pl.col("_var_across").median().alias("repr_floor"),
            pl.col("_n_st").max().alias("n_stations"),
            pl.len().alias("n_instants"),
        )
        .sort(["cell_y", "cell_x", "variable"])
    )


def attach_repr_floor(
    pairs: pl.DataFrame,
    floors: pl.DataFrame,
    stations: pl.DataFrame,
) -> pl.DataFrame:
    """Left-join floors onto matched pairs; absent cell => repr_floor NULL."""
    with_cell = pairs.join(
        stations.select("station_id", *cell_of()), on="station_id", how="left"
    )
    out = with_cell.join(
        floors.select("cell_y", "cell_x", "variable", "repr_floor"),
        on=["cell_y", "cell_x", "variable"],
        how="left",
    )
    return out.drop("cell_y", "cell_x")
