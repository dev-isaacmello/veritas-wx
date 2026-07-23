"""Materialize exactly matched comparison views from the fact table (M7).

    uv run python scripts/build_views.py --fact data/fact/*.parquet

Writes one parquet per registered comparison under data/views/, plus a JSON
manifest per view (comparison_id, qc_mask, counts). Comparisons NEVER read
the fact table directly (non-negotiable #5) — analyses read these views.
"""

import argparse
import itertools
import json
import sys
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from veritas_wx.match.views import comparison_id, matched_view  # noqa: E402
from veritas_wx.runlog import log_stage  # noqa: E402

VIEWS_DIR = REPO_ROOT / "data/views"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fact", nargs="+", required=True)
    parser.add_argument("--out", default=str(VIEWS_DIR))
    args = parser.parse_args()

    fact = pl.concat([pl.read_parquet(p) for p in args.fact])
    models = sorted(fact["model"].unique())
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    comparisons: list[list[str]] = []
    if len(models) >= 2:
        comparisons.extend([list(p) for p in itertools.combinations(models, 2)])
    if len(models) > 2:
        comparisons.append(models)

    if not comparisons:
        raise SystemExit(f"fact has {models} — need >= 2 models for any view")

    for combo in comparisons:
        cid = comparison_id(combo)
        view, manifest = matched_view(fact, combo)
        log_stage(
            f"views.{cid}",
            rows_in=fact.filter(pl.col("model").is_in(combo)).height,
            rows_out=view.height,
            dropped={
                "not_exactly_matched_or_flagged": fact.filter(
                    pl.col("model").is_in(combo)
                ).height
                - view.height
            },
            **{k: v for k, v in manifest.items() if k != "models"},
        )
        view.write_parquet(out_dir / f"matched_{cid}.parquet")
        (out_dir / f"matched_{cid}.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {len(comparisons)} views -> {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
