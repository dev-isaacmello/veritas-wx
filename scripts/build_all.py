"""THE single command: rebuild the fact table from zero (Phase 1 done-criterion #1).

    uv run python scripts/build_all.py --window 2025-07-01:2026-06-30 [--stages ...]

Stages land here as milestones complete; asking for an unimplemented stage
fails loudly with the current list. The preflight always runs: storage mount
(the 1TB HD does not auto-mount at boot), free space, and a real write test.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
MIN_FREE_GB = 20.0
MODELS = ("gfs", "hres", "aifs", "graphcast")


def _run(script: str, *args: str) -> None:
    cmd = [sys.executable, str(REPO / "scripts" / script), *args]
    print(f"[build_all] {' '.join(cmd[1:])}", file=sys.stderr)
    subprocess.run(cmd, cwd=REPO, check=True)


def stage_obs(cfg: dict) -> None:
    """M3: INMET bulk zips -> obs_inmet_v0.parquet (idempotent via manifest)."""
    _run("ingest_obs.py")


def stage_qc(cfg: dict) -> None:
    """M4: calibrated QC + stations v1 completeness cut."""
    _run("run_qc.py")


def stage_forecasts(cfg: dict) -> None:
    """M7: all models x all window months (resumable; skips staged months)."""
    for model in MODELS:
        _run("scale_ingest.py", "--model", model, "--all-months")


def stage_fact(cfg: dict) -> None:
    """M7: staged forecast points x obs_qc -> fact v1 (one file per model)."""
    for model in MODELS:
        staged = sorted((REPO / "data/staged" / model).glob("*/forecast_points_*.parquet"))
        if not staged:
            print(f"[build_all] no staged months for {model}; skipping", file=sys.stderr)
            continue
        _run(
            "build_fact.py",
            "--forecast-points", *[str(p) for p in staged],
            "--out", str(REPO / f"data/fact/fact_{model}.parquet"),
        )


def stage_views(cfg: dict) -> None:
    """M7: exactly matched comparison views (non-negotiable #5)."""
    facts = sorted((REPO / "data/fact").glob("fact_*.parquet"))
    _run("build_views.py", "--fact", *[str(p) for p in facts])


def stage_figures(cfg: dict) -> None:
    """M8: the three pre-registered done-criterion figures."""
    facts = sorted((REPO / "data/fact").glob("fact_*.parquet"))
    _run("make_figures.py", "--fact", *[str(p) for p in facts])


# name -> callable(config) — ORDER IS THE PIPELINE
STAGES: dict[str, object] = {
    "obs": stage_obs,
    "qc": stage_qc,
    "forecasts": stage_forecasts,
    "fact": stage_fact,
    "views": stage_views,
    "figures": stage_figures,
}


def load_config() -> dict:
    with open(REPO / "configs" / "ingest.yaml") as fh:
        return yaml.safe_load(fh)


def ensure_mounted(cfg: dict) -> Path:
    """Mount the data disk if needed (udisksctl, no sudo), verify data_root."""
    mount = cfg["storage"]["mount"]
    expected = Path(mount["expected_path"])
    if not expected.is_mount():
        print(f"[preflight] {expected} not mounted; mounting {mount['device']}", file=sys.stderr)
        subprocess.run(
            ["udisksctl", "mount", "-b", mount["device"]],
            check=True,
            capture_output=True,
            text=True,
        )
    data_root = (REPO / cfg["storage"]["data_root"]).resolve()
    if not data_root.exists():
        raise SystemExit(f"[preflight] data_root {data_root} unreachable after mount")
    return data_root


def preflight(cfg: dict) -> Path:
    data_root = ensure_mounted(cfg)

    free_gb = shutil.disk_usage(data_root).free / 1e9
    if free_gb < MIN_FREE_GB:
        raise SystemExit(f"[preflight] only {free_gb:.1f} GB free at {data_root} (< {MIN_FREE_GB})")

    probe = data_root / ".preflight_write_test"
    probe.write_bytes(b"x" * 1024)
    assert probe.read_bytes()[:1] == b"x"
    probe.unlink()

    print(f"[preflight] ok: data_root={data_root} free={free_gb:.0f}GB", file=sys.stderr)
    return data_root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", help="YYYY-MM-DD:YYYY-MM-DD (default: configs/ingest.yaml)")
    parser.add_argument("--stages", nargs="*", default=list(STAGES), help="subset of stages")
    args = parser.parse_args()

    cfg = load_config()
    if args.window:
        start, end = args.window.split(":")
        cfg["window"] = {"start": start, "end": end}

    preflight(cfg)

    unknown = [s for s in args.stages if s not in STAGES]
    if unknown:
        raise SystemExit(
            f"stages not implemented yet: {unknown}. implemented: {list(STAGES) or 'none'}"
        )
    for name in args.stages:
        print(f"[stage] {name}", file=sys.stderr)
        STAGES[name](cfg)


if __name__ == "__main__":
    main()
