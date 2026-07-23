"""M4: full QC over the observation window + stations v1 completeness cut.

    uv run python scripts/run_qc.py                 # QC + v1 cut
    uv run python scripts/run_qc.py --qc-only       # stop after obs_qc_v0

Stage 1 — QC: applies the six pure checks to obs_inmet_v0 (flag, never
delete: rows_in == rows_out enforced). Neighbor pairs for the spatial check
are k nearest included stations within radius_km (configs/qc_params.yaml).
Writes data/obs/obs_qc_v0.parquet (OBS_QC_V1) + per-check flag rates.

Stage 2 — stations v1 (PLAN M4): stations keep status 'included' only with
t2m completeness >= --min-completeness (default 0.80) counting CLEAN rows
(qc_flags == 0) over the window hours. Everything else becomes excluded with
an itemized reason. Writes data/static/stations_v1.parquet (STATIONS_V1) and
docs/stations_v1_report.md.
"""

import argparse
import hashlib
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from veritas_wx import __version__  # noqa: E402
from veritas_wx.contracts import STATIONS_V1, validate  # noqa: E402
from veritas_wx.ingest.manifest import sha256_of  # noqa: E402
from veritas_wx.ingest.static.stations import haversine_km  # noqa: E402
from veritas_wx.qc.runner import run_qc  # noqa: E402
from veritas_wx.runlog import log_stage  # noqa: E402

CONFIG_FILES = (REPO_ROOT / "configs/ingest.yaml", REPO_ROOT / "configs/qc_params.yaml")
OBS_IN = REPO_ROOT / "data/obs/obs_inmet_v0.parquet"
STATIONS_IN = REPO_ROOT / "data/static/stations_v0.parquet"
OBS_QC_OUT = REPO_ROOT / "data/obs/obs_qc_v0.parquet"
STATIONS_OUT = REPO_ROOT / "data/static/stations_v1.parquet"
REPORT_PATH = REPO_ROOT / "docs/stations_v1_report.md"


def compute_ingest_version() -> str:
    """"{semver}+{git_sha7}.{hash8(configs)}" — configs hashed in fixed order."""
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    h = hashlib.sha256()
    for path in CONFIG_FILES:
        h.update(path.read_bytes())
    return f"{__version__}+{sha}.{h.hexdigest()[:8]}"


def load_params() -> dict:
    return yaml.safe_load((REPO_ROOT / "configs/qc_params.yaml").read_text())


def window_hours() -> int:
    cfg = yaml.safe_load((REPO_ROOT / "configs/ingest.yaml").read_text())
    start = date.fromisoformat(cfg["window"]["start"])
    end = date.fromisoformat(cfg["window"]["end"])  # inclusive
    return int(((end - start).days + 1) * 24)


def build_neighbor_pairs(stations: pl.DataFrame, k: int, radius_km: float) -> pl.DataFrame:
    """k nearest included stations within radius_km, as (station_id, neighbor_id).

    666 stations -> a 666x666 haversine matrix is trivial; no spatial index
    needed at this scale (anti-pattern guard: no premature abstraction).
    """
    inc = stations.filter(pl.col("status") == "included").select("station_id", "lat", "lon")
    ids = inc["station_id"].to_list()
    lat = np.radians(inc["lat"].to_numpy())
    lon = np.radians(inc["lon"].to_numpy())
    # haversine, vectorized full matrix
    dlat = lat[:, None] - lat[None, :]
    dlon = lon[:, None] - lon[None, :]
    a = np.sin(dlat / 2) ** 2 + np.cos(lat[:, None]) * np.cos(lat[None, :]) * np.sin(dlon / 2) ** 2
    dist = 2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    np.fill_diagonal(dist, np.inf)

    pairs: list[tuple[str, str]] = []
    order = np.argsort(dist, axis=1)
    for i, sid in enumerate(ids):
        for j in order[i, :k]:
            if dist[i, j] <= radius_km:
                pairs.append((sid, ids[j]))
    df = pl.DataFrame(
        {"station_id": [p[0] for p in pairs], "neighbor_id": [p[1] for p in pairs]},
        schema={"station_id": pl.Utf8, "neighbor_id": pl.Utf8},
    )
    # spot-check the vectorization against the scalar haversine (guard, not test)
    if pairs:
        s0, n0 = pairs[0]
        r0 = inc.filter(pl.col("station_id") == s0).row(0, named=True)
        r1 = inc.filter(pl.col("station_id") == n0).row(0, named=True)
        ref = haversine_km(r0["lat"], r0["lon"], r1["lat"], r1["lon"])
        i0, j0 = ids.index(s0), ids.index(n0)
        assert abs(dist[i0, j0] - ref) < 0.5, "vectorized haversine disagrees with scalar"
    return df


def cut_v1(
    stations: pl.DataFrame,
    obs_qc: pl.DataFrame,
    min_completeness: float,
    n_hours: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Stations v1: keep 'included' only with clean-t2m completeness >= threshold.

    Returns (stations_v1, per-station completeness table used by the report).
    Never touches rows already excluded/review — first cause wins, as always.
    """
    clean_t2m = (
        obs_qc.filter((pl.col("variable") == "t2m") & (pl.col("qc_flags") == 0))
        .group_by("station_id")
        .len()
        .with_columns((pl.col("len") / n_hours).alias("completeness_t2m"))
    )
    completeness = (
        stations.filter(pl.col("status") == "included")
        .select("station_id")
        .join(clean_t2m, on="station_id", how="left")
        .with_columns(
            pl.col("len").fill_null(0).alias("n_clean_t2m"),
            pl.col("completeness_t2m").fill_null(0.0),
        )
        .drop("len")
    )
    passing = set(
        completeness.filter(pl.col("completeness_t2m") >= min_completeness)["station_id"]
    )
    fail = (pl.col("status") == "included") & ~pl.col("station_id").is_in(list(passing))
    reason = f"t2m_clean_completeness_lt_{min_completeness:g}"
    v1 = stations.with_columns(
        exclusion_reason=pl.when(fail).then(pl.lit(reason)).otherwise(pl.col("exclusion_reason")),
        status=pl.when(fail).then(pl.lit("excluded")).otherwise(pl.col("status")),
    )
    return v1, completeness


def md_table(df: pl.DataFrame, float_fmt: str = "{:.4f}") -> str:
    """Small manual markdown table (no pandas/tabulate dependency)."""
    cols = df.columns
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.iter_rows():
        cells = [float_fmt.format(v) if isinstance(v, float) else str(v) for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_report(
    v1: pl.DataFrame,
    completeness: pl.DataFrame,
    flag_rates: pl.DataFrame,
    min_completeness: float,
    n_hours: int,
    ingest_version: str,
) -> None:
    inc = v1.filter(pl.col("status") == "included")
    comp_sorted = completeness.sort("completeness_t2m")
    lines = [
        "# Estações v1 — corte por completude (M4)",
        "",
        f"Gerado por `scripts/run_qc.py` · ingest_version `{ingest_version}`",
        "",
        f"Critério (PLAN M4): completude de t2m **limpa** (qc_flags == 0) ≥ "
        f"{min_completeness:.0%} das {n_hours} horas da janela.",
        "",
        f"- Candidatas (incluídas na v0): **{completeness.height}**",
        f"- Aprovadas (v1 included): **{inc.height}**",
        f"- Reprovadas: **{completeness.height - inc.height}** "
        f"(`t2m_clean_completeness_lt_{min_completeness:g}`)",
        "",
        "## Taxas de flag por check (obs_qc_v0, todas as variáveis)",
        "",
        md_table(flag_rates),
        "",
        "## 20 piores completudes (fila de inspeção)",
        "",
        md_table(comp_sorted.head(20)),
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines))


def run(args: argparse.Namespace) -> None:
    ingest_version = compute_ingest_version()
    params = load_params()
    obs = pl.read_parquet(OBS_IN)
    stations = pl.read_parquet(STATIONS_IN)
    n_hours = window_hours()
    print(f"ingest_version={ingest_version} obs={obs.height} hours={n_hours}", file=sys.stderr)

    pairs = build_neighbor_pairs(
        stations, k=params["spatial"]["neighbors_k"], radius_km=params["spatial"]["radius_km"]
    )
    log_stage(
        "qc.neighbor_pairs",
        rows_in=stations.filter(pl.col("status") == "included").height,
        rows_out=stations.filter(pl.col("status") == "included").height,
        dropped={},
        n_pairs=pairs.height,
    )

    obs_qc = run_qc(obs, params, stations, neighbor_pairs=pairs)
    tmp = OBS_QC_OUT.with_suffix(".tmp.parquet")
    obs_qc.write_parquet(tmp)
    tmp.replace(OBS_QC_OUT)
    print(f"wrote {OBS_QC_OUT} sha256={sha256_of(OBS_QC_OUT)}", file=sys.stderr)

    # per-check x variable flag rates (calibration evidence for ADR-0003)
    from veritas_wx.contracts import qc_bits

    rates = obs_qc.group_by("variable").agg(
        pl.len().alias("n"),
        *[
            ((pl.col("qc_flags") & bit) != 0).sum().alias(name)
            for name, bit in qc_bits.ALL_BITS.items()
        ],
        (pl.col("qc_flags") == 0).sum().alias("clean"),
    ).sort("variable")
    print(rates, file=sys.stderr)

    if args.qc_only:
        return

    v1, completeness = cut_v1(stations, obs_qc, args.min_completeness, n_hours)
    log_stage(
        "m4.stations_v1_cut",
        rows_in=v1.height,
        rows_out=v1.height,
        dropped={},
        included=v1.filter(pl.col("status") == "included").height,
        cut=completeness.height - v1.filter(pl.col("status") == "included").height,
        min_completeness=args.min_completeness,
    )
    v1 = validate(v1, STATIONS_V1, "STATIONS_V1")
    tmp = STATIONS_OUT.with_suffix(".tmp.parquet")
    v1.write_parquet(tmp)
    tmp.replace(STATIONS_OUT)
    write_report(v1, completeness, rates, args.min_completeness, n_hours, ingest_version)
    print(
        f"wrote {STATIONS_OUT} included={v1.filter(pl.col('status') == 'included').height} "
        f"sha256={sha256_of(STATIONS_OUT)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qc-only", action="store_true")
    parser.add_argument("--min-completeness", type=float, default=0.80)
    run(parser.parse_args())
