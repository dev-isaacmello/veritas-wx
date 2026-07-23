"""M7 orchestrator: resumable model x month forecast ingestion at full scale.

    uv run python scripts/scale_ingest.py --model gfs --months 2025-07 2025-08
    uv run python scripts/scale_ingest.py --model hres --all-months

One subprocess (scripts/thin_slice.py) per model x month, ALL v1 stations.
Resumable: a month whose output parquet already exists and reads cleanly is
skipped — re-running after a crash or dirty unmount only redoes broken work.
Output layout: data/staged/{model}/{yyyymm}/forecast_points_{model}.parquet
"""

import argparse
import calendar
import subprocess
import sys
from datetime import date
from pathlib import Path

import polars as pl
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGED = REPO_ROOT / "data/staged"
STATIONS = REPO_ROOT / "data/static/stations_v1.parquet"


def window_months() -> list[str]:
    cfg = yaml.safe_load((REPO_ROOT / "configs/ingest.yaml").read_text())
    start = date.fromisoformat(cfg["window"]["start"])
    end = date.fromisoformat(cfg["window"]["end"])
    months, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        months.append(f"{cur.year:04d}-{cur.month:02d}")
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return months


def month_done(out_dir: Path, model: str) -> bool:
    """Done == parquet exists AND reads cleanly (dirty-unmount paranoia)."""
    parquet = out_dir / f"forecast_points_{model}.parquet"
    if not parquet.exists():
        return False
    try:
        pl.read_parquet(parquet, n_rows=1)
        return True
    except Exception:
        print(f"[resume] {parquet} unreadable — redoing month", file=sys.stderr)
        return False


def run_month(model: str, month: str, ingest_version: str) -> bool:
    year, mon = map(int, month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    out_dir = STAGED / model / f"{year:04d}{mon:02d}"
    if month_done(out_dir, model):
        print(f"[skip] {model} {month}: already staged", file=sys.stderr)
        return True

    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts/thin_slice.py"),
        "--model", model,
        "--start", f"{year:04d}-{mon:02d}-01",
        "--end", f"{year:04d}-{mon:02d}-{last:02d}",
        "--stations", str(STATIONS),
        "--out", str(out_dir),
        "--ingest-version", ingest_version,
    ]
    print(f"[run] {model} {month}", file=sys.stderr)
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    return result.returncode == 0 and month_done(out_dir, model)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=["gfs", "hres", "aifs", "graphcast"])
    parser.add_argument("--months", nargs="*", help="YYYY-MM list")
    parser.add_argument("--all-months", action="store_true")
    parser.add_argument("--ingest-version", default="0.1.0+m7")
    args = parser.parse_args()

    months = window_months() if args.all_months else (args.months or [])
    if not months:
        raise SystemExit("give --months or --all-months")

    failed = [m for m in months if not run_month(args.model, m, args.ingest_version)]
    done = [m for m in months if m not in failed]
    print(f"[summary] {args.model}: ok={done} failed={failed}", file=sys.stderr)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
