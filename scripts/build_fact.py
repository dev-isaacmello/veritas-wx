"""Fact table driver: forecast points × QC'd obs -> FACT_V1 (M5 slice / M7 full).

    uv run python scripts/build_fact.py \
        --forecast-points data/staged/thin_slice_gfs/forecast_points_gfs.parquet \
        --out data/fact/fact_slice_gfs.parquet

repr_floor policy (M4 decision): the floor is estimated from ALL stations
with clean observations (a per-instant cross-station variance needs
simultaneity, not completeness) — NOT restricted to the v1 verification set.
Pairs, however, only form on stations present in --stations (v1).
"""

import argparse
import sys
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from veritas_wx.contracts import FACT_V1, validate  # noqa: E402
from veritas_wx.ingest.manifest import sha256_of  # noqa: E402
from veritas_wx.match.fact import build_fact  # noqa: E402
from veritas_wx.match.repr_floor import repr_floor_by_cell  # noqa: E402
from veritas_wx.runlog import log_stage  # noqa: E402

OBS_QC = REPO_ROOT / "data/obs/obs_qc_v0.parquet"
STATIONS_V1 = REPO_ROOT / "data/static/stations_v1.parquet"
STATIONS_V0 = REPO_ROOT / "data/static/stations_v0.parquet"


def run(args: argparse.Namespace) -> None:
    points = pl.concat([pl.read_parquet(p) for p in args.forecast_points])
    obs_qc = pl.read_parquet(OBS_QC)
    v1 = pl.read_parquet(args.stations)

    # Floor from ALL stations with clean obs (v0 included set), never v1-only.
    all_stations = pl.read_parquet(STATIONS_V0).filter(pl.col("status") == "included")
    clean_obs = obs_qc.filter(pl.col("qc_flags") == 0)
    floors = repr_floor_by_cell(clean_obs, all_stations)
    log_stage(
        "fact.repr_floor",
        rows_in=clean_obs.height,
        rows_out=clean_obs.height,
        dropped={},
        n_cells_with_floor=floors.select("cell_y", "cell_x").unique().height,
        variables=sorted(floors["variable"].unique()),
    )

    verification_stations = v1.filter(pl.col("status") == "included")
    fact, dropped = build_fact(
        points,
        obs_qc,
        verification_stations,
        floors=floors,
        max_delta_z_m=args.max_delta_z,
        ingest_version=args.ingest_version,
    )
    log_stage(
        "fact.build",
        rows_in=points.height,
        rows_out=fact.height,
        dropped=dropped,
        n_stations=fact["station_id"].n_unique() if fact.height else 0,
        models=sorted(fact["model"].unique()) if fact.height else [],
    )

    fact = validate(fact, FACT_V1, "FACT_V1")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.parquet")
    fact.write_parquet(tmp)
    tmp.replace(out)
    print(f"wrote {out} rows={fact.height} sha256={sha256_of(out)}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecast-points", nargs="+", required=True)
    parser.add_argument("--stations", default=str(STATIONS_V1))
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-delta-z", type=float, default=500.0)
    parser.add_argument("--ingest-version", default="unversioned")
    run(parser.parse_args())
