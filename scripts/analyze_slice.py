"""M5 slice analysis: first REAL metrics with bootstrap CIs from the mini-fact.

    uv run python scripts/analyze_slice.py --fact data/fact/fact_slice_gfs.parquet

Purpose: validate the entire chain (fetch -> decode -> extract -> match ->
metrics with uncertainty) on minimum volume BEFORE M7 scale. Not a science
result — one month, 20 stations — but every number already carries its CI
(non-negotiable: no estimate without uncertainty).

fcst column policy (registry): t2m uses fcst_elev_adj (lapse-rate corrected;
falls back to raw where delta_z made adjustment inapplicable — counted);
wind10m/precip_24h use fcst_raw. Only clean pairs (qc_flags == 0) enter.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from veritas_wx.analyze.metrics.core import bias, mae, rmse, variance_ratio  # noqa: E402
from veritas_wx.runlog import log_stage  # noqa: E402

REPORT_PATH = REPO_ROOT / "docs/m5_slice_report.md"
SEED = 20260723
REPORT_LEADS = [24, 72, 120, 240]


def metric_frame(fact: pl.DataFrame, variable: str) -> tuple[pl.DataFrame, dict[str, int]]:
    """FACT rows -> (metric-ready frame with fcst/obs/day, dropped counts)."""
    sub = fact.filter(pl.col("variable") == variable)
    dropped = {"qc_flagged": sub.filter(pl.col("qc_flags") != 0).height}
    sub = sub.filter(pl.col("qc_flags") == 0)
    if variable == "t2m":
        fcst = pl.coalesce(pl.col("fcst_elev_adj"), pl.col("fcst_raw"))
        dropped["elev_adj_fallback_raw"] = sub.filter(pl.col("fcst_elev_adj").is_null()).height
    else:
        fcst = pl.col("fcst_raw")
    out = sub.select(
        "station_id",
        "lead_hours",
        fcst.alias("fcst"),
        "obs",
        pl.col("valid_time").dt.date().alias("day"),
    )
    return out, dropped


def fmt(r) -> str:
    return f"{r.estimate:.3f} [{r.ci_low:.3f}, {r.ci_high:.3f}]"


def run(args: argparse.Namespace) -> None:
    fact = pl.read_parquet(args.fact)
    rng = np.random.default_rng(SEED)
    lines = [
        "# M5 thin slice — métricas com IC bootstrap (validação da cadeia)",
        "",
        f"Fact: `{args.fact}` · {fact.height} pares · "
        f"{fact['station_id'].n_unique()} estações · seed {SEED}",
        "",
        "NÃO é resultado científico (1 mês, 20 estações): valida a cadeia",
        "fetch→decode→extract→match→métrica+IC antes da escala M7.",
        "",
    ]

    for variable in ("t2m", "wind10m", "precip_24h"):
        frame, dropped = metric_frame(fact, variable)
        log_stage(
            f"m5.analyze.{variable}",
            rows_in=fact.filter(pl.col("variable") == variable).height,
            rows_out=frame.height,
            dropped={"qc_flagged": dropped["qc_flagged"]},
            **{k: v for k, v in dropped.items() if k != "qc_flagged"},
        )
        if frame.height == 0:
            lines += [f"## {variable}", "", "_sem pares limpos_", ""]
            continue

        lines += [
            f"## {variable}",
            "",
            f"Pares limpos: {frame.height}"
            + (
                f" · fallback sem correção de elevação: {dropped['elev_adj_fallback_raw']}"
                if variable == "t2m"
                else ""
            ),
            "",
            "| lead (h) | n | bias [IC95] | MAE [IC95] | RMSE [IC95] |",
            "|---|---|---|---|---|",
        ]
        for lead in REPORT_LEADS:
            at = frame.filter(pl.col("lead_hours") == lead)
            if at.height < 30:
                lines.append(f"| {lead} | {at.height} | _n insuficiente_ | | |")
                continue
            b = bias(at, rng)
            m = mae(at, rng)
            r = rmse(at, rng)
            lines.append(f"| {lead} | {at.height} | {fmt(b)} | {fmt(m)} | {fmt(r)} |")

        vr_all = frame.filter(pl.col("lead_hours") <= 120)
        if vr_all.height >= 100:
            vr = variance_ratio(vr_all, rng)
            caveat = ""
            if vr.ci_high > 100 * max(1.0, abs(vr.estimate)):
                caveat = (
                    " ⚠️ IC degenerado: std(obs)≈0 em parte dos draws (amostra"
                    " seca/curta) — métrica registrada intacta, estabiliza na M7"
                )
            lines += [
                "",
                f"variance_ratio (leads ≤120h, mediana entre estações): {fmt(vr)}{caveat}",
            ]
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines))
    print(f"wrote {REPORT_PATH}", file=sys.stderr)
    print("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fact", required=True)
    run(parser.parse_args())
