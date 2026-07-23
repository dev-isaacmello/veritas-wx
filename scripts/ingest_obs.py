"""M3: INMET bulk observation ingest for the full window -> OBS_V1 parquet.

    uv run python scripts/ingest_obs.py            # 2025 + 2026 zips
    uv run python scripts/ingest_obs.py --years 2026

Per ADR-0002 §2 the annual ``dadoshistoricos`` zips are the PRIMARY hourly
source (apitempo is degraded). Raw zips land in the HD lake with sha256 in the
manifest; parsing is restricted to stations 'included' in stations_v0; rows
outside the window [start, end] are dropped WITH accounting. The final frame
validates OBS_V1 and every stage logs the reconciliation identity (guard R9).
"""

import argparse
import hashlib
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from veritas_wx import __version__  # noqa: E402
from veritas_wx.contracts import OBS_V1, validate  # noqa: E402
from veritas_wx.ingest.manifest import sha256_of  # noqa: E402
from veritas_wx.ingest.observations import inmet_bulk  # noqa: E402
from veritas_wx.runlog import log_stage  # noqa: E402

CONFIG_FILES = (REPO_ROOT / "configs/ingest.yaml", REPO_ROOT / "configs/qc_params.yaml")
STATIONS_PARQUET = REPO_ROOT / "data/static/stations_v0.parquet"
OUT_PARQUET = REPO_ROOT / "data/obs/obs_inmet_v0.parquet"
RAW_DIR = REPO_ROOT / "data/obs/raw"
MANIFEST_PATH = REPO_ROOT / "data/obs/manifest.parquet"


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


def preflight_data_root() -> None:
    """data/ is a symlink to the HD data lake — refuse to run against a broken mount."""
    if not STATIONS_PARQUET.parent.is_dir():
        raise SystemExit(
            f"data root not available: {STATIONS_PARQUET.parent} — mount the data HD "
            "(udisksctl mount -b /dev/sda3) before ingesting"
        )
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    probe = OUT_PARQUET.parent / ".write_probe"
    probe.write_text("ok")
    probe.unlink()


def load_window() -> tuple[datetime, datetime]:
    cfg = yaml.safe_load((REPO_ROOT / "configs/ingest.yaml").read_text())
    start = date.fromisoformat(cfg["window"]["start"])
    end = date.fromisoformat(cfg["window"]["end"])  # inclusive
    return (
        datetime(start.year, start.month, start.day, tzinfo=UTC),
        datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1),
    )


def included_inmet_ids() -> set[str]:
    stations = pl.read_parquet(STATIONS_PARQUET)
    ids = stations.filter(
        (pl.col("status") == "included") & (pl.col("network") == "inmet")
    )["native_id"]
    if not len(ids):
        raise SystemExit("stations_v0 has zero included INMET stations — build M2 first")
    return set(ids)


def run(years: list[int]) -> None:
    preflight_data_root()
    ingest_version = compute_ingest_version()
    window_start, window_end = load_window()
    station_filter = included_inmet_ids()
    print(f"ingest_version={ingest_version} stations={len(station_filter)}", file=sys.stderr)

    frames: list[pl.DataFrame] = []
    with httpx.Client(follow_redirects=True) as client:
        for year in years:
            zip_path = inmet_bulk.fetch_year_zip(client, year, RAW_DIR, MANIFEST_PATH)
            df, total, _per_station, n_lines = inmet_bulk.rows_from_zip(
                zip_path, ingest_version, station_filter=station_filter
            )
            row_drops = {k: v for k, v in total.items() if k != "skipped_station_files"}
            log_stage(
                f"inmet_bulk_parse_{year}",
                rows_in=n_lines * len(inmet_bulk.COLUMN_MAP),
                rows_out=df.height,
                dropped=row_drops,
                skipped_station_files=total["skipped_station_files"],
                zip=zip_path.name,
            )
            frames.append(df)

    obs = pl.concat(frames)

    # Window clip: the 2025 zip carries Jan-Jun/2025 (pre-window) — count, never silent.
    in_window = obs.filter(
        (pl.col("valid_time") >= window_start) & (pl.col("valid_time") < window_end)
    )
    log_stage(
        "inmet_bulk_window_clip",
        rows_in=obs.height,
        rows_out=in_window.height,
        dropped={"outside_window": obs.height - in_window.height},
        window=[
            window_start.date().isoformat(),
            (window_end.date() - timedelta(days=1)).isoformat(),
        ],
    )

    # Bulk zips may repeat an hour across builds; identical duplicates collapse,
    # conflicting ones must not pass silently.
    key = ["station_id", "valid_time", "variable"]
    deduped = in_window.unique(subset=[*key, "value"], keep="first")
    conflicts = deduped.filter(deduped.select(key).is_duplicated())
    if conflicts.height:
        raise SystemExit(
            f"{conflicts.height} conflicting duplicate observations (same key, different "
            f"value) — refusing to write. Sample:\n{conflicts.head(10)}"
        )
    log_stage(
        "inmet_bulk_dedupe",
        rows_in=in_window.height,
        rows_out=deduped.height,
        dropped={"exact_duplicate": in_window.height - deduped.height},
    )

    out = deduped.sort(key)
    validate(out, OBS_V1, "OBS_V1")
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PARQUET.with_suffix(".tmp.parquet")
    out.write_parquet(tmp)
    tmp.replace(OUT_PARQUET)

    print(
        f"wrote {OUT_PARQUET} rows={out.height} "
        f"stations={out['station_id'].n_unique()} sha256={sha256_of(OUT_PARQUET)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=[2025, 2026])
    run(parser.parse_args().years)
