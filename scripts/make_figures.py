"""M8: the three pre-registered done-criterion figures, from the fact table.

    uv run python scripts/make_figures.py --fact data/fact/*.parquet --out docs/figures

F1 (H1): variance_ratio x lead per model, CI band, one panel per variable.
F2 (H2): bias_by_percentile (t2m, precip_24h), CI bars, models side by side.
F3 (H3): regime_stratified_skill — a TABLE (csv + md), not a chart: mae/rmse/
         bias/variance_ratio x (season, koppen_level1[, enso, mjo]) strata.

Design rules (dataviz): colors are assigned to MODELS in fixed order and
never repainted when a model is absent; one axis per chart; direct labels at
line ends plus a legend; recessive grid; CIs always drawn — no naked point
estimates anywhere (non-negotiable #4 lives in figures too).
"""

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from veritas_wx.analyze.metrics.core import (  # noqa: E402
    bias,
    bias_by_percentile,
    mae,
    rmse,
    variance_ratio,
)
from veritas_wx.analyze.strata import join_koppen, obs_percentile, season_of  # noqa: E402

SEED = 20260723
# Fixed entity->hue assignment (validated palette; never reassigned on subsets)
MODEL_COLORS = {
    "aifs": "#2a78d6",
    "gfs": "#eb6834",
    "graphcast": "#1baf7a",
    "hres": "#eda100",
}
VARIABLES = ("t2m", "wind10m", "precip_24h")
TABLE_LEADS = (24, 72, 120, 168, 240)  # registry family phase1_regimes


def load_fact(paths: list[str]) -> pl.DataFrame:
    fact = pl.concat([pl.read_parquet(p) for p in paths])
    return fact.filter(pl.col("qc_flags") == 0).with_columns(
        pl.coalesce(pl.col("fcst_elev_adj"), pl.col("fcst_raw")).alias("fcst_adj"),
        pl.col("valid_time").dt.date().alias("day"),
    )


def metric_ready(fact: pl.DataFrame, variable: str, fcst_input: str) -> pl.DataFrame:
    col = "fcst_raw" if fcst_input == "raw" else "fcst_adj"
    return fact.filter(pl.col("variable") == variable).select(
        "model",
        "station_id",
        "lead_hours",
        pl.col(col).alias("fcst"),
        "obs",
        "day",
        "valid_time",
    )


def style_axes(ax) -> None:
    ax.grid(True, axis="y", linewidth=0.5, alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)


def fig_variance_ratio(fact: pl.DataFrame, out_dir: Path, n_boot: int) -> None:
    rng = np.random.default_rng(SEED)
    models = sorted(fact["model"].unique())
    fig, axes = plt.subplots(1, len(VARIABLES), figsize=(13, 4), sharex=True)
    for ax, variable in zip(axes, VARIABLES, strict=True):
        # registry: variance_ratio uses RAW forecast
        frame = metric_ready(fact, variable, "raw")
        all_hi: list[float] = []
        for model in models:
            sub = frame.filter(pl.col("model") == model)
            leads = sorted(sub["lead_hours"].unique())
            est, lo, hi = [], [], []
            for lead in leads:
                at = sub.filter(pl.col("lead_hours") == lead)
                r = variance_ratio(at, rng, n_boot=n_boot)
                est.append(r.estimate)
                lo.append(r.ci_low)
                hi.append(r.ci_high)
            color = MODEL_COLORS[model]
            all_hi.extend(hi)
            ax.plot(leads, est, color=color, linewidth=1.8, label=model)
            ax.fill_between(leads, lo, hi, color=color, alpha=0.15, linewidth=0)
            if leads:  # direct label at line end (contrast relief for light hues)
                ax.annotate(
                    model, (leads[-1], est[-1]), xytext=(4, 0),
                    textcoords="offset points", fontsize=8, color="#333333", va="center",
                )
        ax.axhline(1.0, color="#888888", linewidth=0.8, linestyle="--")
        # Degenerate draws (std(obs)~0, dry samples) explode the CI to 1e15
        # and make the panel unreadable; cap the AXIS (never the data) and
        # say so — an honest zoom, not a clip of the registered metric.
        finite_hi = [h for h in all_hi if np.isfinite(h)]
        if finite_hi and max(finite_hi) > 5.0:
            ax.set_ylim(0.0, 3.0)
            ax.annotate(
                "IC excede o eixo (draws degenerados,\nstd(obs)≈0 — ver relatório)",
                (0.02, 0.95), xycoords="axes fraction", fontsize=7,
                color="#666666", va="top",
            )
        ax.set_title(variable, fontsize=10)
        ax.set_xlabel("lead (h)")
        style_axes(ax)
    axes[0].set_ylabel("variance_ratio  σ(fcst)/σ(obs), mediana entre estações")
    axes[0].legend(frameon=False, fontsize=8, loc="lower left")
    fig.suptitle("H1 — variance_ratio por lead (IC95 bootstrap em blocos)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out_dir / "fig1_variance_ratio.png", dpi=180)
    plt.close(fig)


def fig_bias_by_percentile(fact: pl.DataFrame, out_dir: Path, n_boot: int) -> None:
    rng = np.random.default_rng(SEED)
    models = sorted(fact["model"].unique())
    variables = ("t2m", "precip_24h")  # registry scope
    fig, axes = plt.subplots(1, len(variables), figsize=(11, 4))
    for ax, variable in zip(axes, variables, strict=True):
        # single-variable frame: percentile groups by station_id alone
        frame = obs_percentile(metric_ready(fact, variable, "elev_adj_when_available"))
        n_models = len(models)
        for k, model in enumerate(models):
            sub = frame.filter(pl.col("model") == model)
            if sub.height == 0:
                continue
            table = bias_by_percentile(sub, rng, n_boot=n_boot)
            present = table.filter(pl.col("n_pairs") > 0)
            x = np.arange(len(table))
            xs = [i for i, r in enumerate(table.iter_rows(named=True)) if r["n_pairs"] > 0]
            offset = (k - (n_models - 1) / 2) * 0.16
            color = MODEL_COLORS[model]
            ax.errorbar(
                [i + offset for i in xs],
                present["estimate"],
                yerr=[
                    (present["estimate"] - present["ci_low"]).to_list(),
                    (present["ci_high"] - present["estimate"]).to_list(),
                ],
                fmt="o", markersize=4, linewidth=1.4, capsize=2,
                color=color, label=model,
            )
            ax.set_xticks(x, table["bin"].to_list(), rotation=45, fontsize=7)
        ax.axhline(0.0, color="#888888", linewidth=0.8, linestyle="--")
        ax.set_title(variable, fontsize=10)
        ax.set_xlabel("bin de percentil da obs")
        style_axes(ax)
    axes[0].set_ylabel("bias (fcst − obs)")
    axes[0].legend(frameon=False, fontsize=8)
    fig.suptitle("H2 — bias condicionado ao percentil observado (IC95)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out_dir / "fig2_bias_by_percentile.png", dpi=180)
    plt.close(fig)


def table_regime_skill(
    fact: pl.DataFrame, stations: pl.DataFrame, out_dir: Path, n_boot: int
) -> None:
    rng = np.random.default_rng(SEED)
    rows: list[dict] = []
    enriched = join_koppen(fact, stations).with_columns(season_of().alias("season"))
    strata = [("season", enriched), ("koppen_level1", enriched)]
    for stratum_col, base in strata:
        for stratum in sorted(base[stratum_col].drop_nulls().unique()):
            for variable in VARIABLES:
                frame = (
                    base.filter(pl.col(stratum_col) == stratum)
                    .filter(pl.col("variable") == variable)
                    .filter(pl.col("lead_hours").is_in(TABLE_LEADS))
                    .select(
                        "model", "station_id", "lead_hours",
                        pl.col("fcst_adj").alias("fcst"), "obs", "day",
                    )
                )
                for model in sorted(frame["model"].unique()):
                    sub = frame.filter(pl.col("model") == model)
                    if sub.height < 50:
                        continue
                    for name, fn in (("mae", mae), ("rmse", rmse), ("bias", bias),
                                     ("variance_ratio", variance_ratio)):
                        r = fn(sub, rng, n_boot=n_boot)
                        rows.append(
                            {
                                "stratum_type": stratum_col, "stratum": stratum,
                                "variable": variable, "model": model, "metric": name,
                                "estimate": r.estimate, "ci_low": r.ci_low,
                                "ci_high": r.ci_high, "n_pairs": sub.height,
                            }
                        )
    table = pl.DataFrame(rows)
    table.write_csv(out_dir / "fig3_regime_stratified_skill.csv")
    md = [
        "| stratum | variable | model | metric | estimate [IC95] | n |",
        "|---|---|---|---|---|---|",
    ]
    for r in table.iter_rows(named=True):
        md.append(
            f"| {r['stratum_type']}={r['stratum']} | {r['variable']} | {r['model']} "
            f"| {r['metric']} | {r['estimate']:.3f} [{r['ci_low']:.3f}, {r['ci_high']:.3f}] "
            f"| {r['n_pairs']} |"
        )
    (out_dir / "fig3_regime_stratified_skill.md").write_text("\n".join(md))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fact", nargs="+", required=True)
    parser.add_argument("--stations", default=str(REPO_ROOT / "data/static/stations_v1.parquet"))
    parser.add_argument("--out", default=str(REPO_ROOT / "docs/figures"))
    parser.add_argument("--n-boot", type=int, default=1000)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    fact = load_fact(args.fact)
    stations = pl.read_parquet(args.stations)
    print(f"fact rows (clean): {fact.height}", file=sys.stderr)

    fig_variance_ratio(fact, out_dir, args.n_boot)
    print("fig1 done", file=sys.stderr)
    fig_bias_by_percentile(fact, out_dir, args.n_boot)
    print("fig2 done", file=sys.stderr)
    table_regime_skill(fact, stations, out_dir, args.n_boot)
    print("fig3 done", file=sys.stderr)


if __name__ == "__main__":
    main()
