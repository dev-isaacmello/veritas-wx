"""Previsão multi-modelo para uma estação, com leitura calibrada pela verificação.

    uv run python scripts/forecast_point.py --station "PORTO ALEGRE"
    uv run python scripts/forecast_point.py --station inmet:A801 --models gfs aifs

Busca a rodada 00Z de hoje (GFS + HRES + AIFS, ~5 MB/modelo via byte-range),
extrai no ponto da estação e imprime o resumo diário. A "calibração" exibida é
a leitura honesta do que a verificação (M5) mediu: viés sistemático do GFS
sobre o Brasil — o valor cru NUNCA é alterado; o viés medido é mostrado ao
lado, com a ressalva de amostra.

Isto é uma ferramenta de conveniência, não o produto científico: o produto é
o dataset de verificação. O ajuste formal por viés (previsão calibrada) é
item de roadmap e exigirá o fact table completo.
"""

import argparse
import datetime as dt
import subprocess
import sys
import tempfile
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
_LAKE_STATIONS = REPO_ROOT / "data/static/stations_v0.parquet"
_SNAPSHOT = REPO_ROOT / "assets/stations_snapshot.parquet"
STATIONS = _LAKE_STATIONS if _LAKE_STATIONS.exists() else _SNAPSHOT

GFS_BIAS_NOTE = (
    "leitura calibrada (M5, ago/2025, IC95): GFS roda +1.2 a +2.3 K quente no Brasil"
    " (pior nas horas frias) e subestima chuva extrema — para eventos fortes,"
    " prefira o consenso AIFS/HRES"
)


def resolve_station(query: str) -> dict:
    st = pl.read_parquet(STATIONS)
    if ":" in query:
        hit = st.filter(pl.col("station_id") == query)
    else:
        hit = st.filter(pl.col("name").str.contains(query.upper()))
    if hit.height == 0:
        raise SystemExit(f"nenhuma estação bate com {query!r}")
    if hit.height > 1:
        names = hit.select("station_id", "name").to_dicts()
        print(f"[aviso] {hit.height} estações batem; usando a primeira: {names}", file=sys.stderr)
    return hit.row(0, named=True)


def fetch_model(model: str, station_row: dict, out_dir: Path, init_date: dt.date) -> pl.DataFrame:
    one = pl.read_parquet(STATIONS).filter(
        pl.col("station_id") == station_row["station_id"]
    ).with_columns(pl.lit("included").alias("status"))
    stations_path = out_dir / "station.parquet"
    one.write_parquet(stations_path)
    cmd = [
        sys.executable, str(REPO_ROOT / "scripts/thin_slice.py"),
        "--model", model,
        "--start", init_date.isoformat(),
        "--end", init_date.isoformat(),
        "--runs", "0",
        "--stations", str(stations_path),
        "--out", str(out_dir / model),
        "--ingest-version", "forecast-point",
    ]
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[{model}] falhou: {result.stderr.strip().splitlines()[-1]}", file=sys.stderr)
        return pl.DataFrame()
    return pl.read_parquet(out_dir / model / f"forecast_points_{model}.parquet")


def daily_summary(df: pl.DataFrame) -> pl.DataFrame:
    local = df.with_columns(pl.col("valid_time").dt.offset_by("-3h").alias("local"))
    local = local.with_columns(pl.col("local").dt.date().alias("dia"))
    t = (
        local.filter(pl.col("variable") == "t2m")
        .group_by("model", "dia")
        .agg(
            (pl.col("value").min() - 273.15).round(1).alias("tmin"),
            (pl.col("value").max() - 273.15).round(1).alias("tmax"),
        )
    )
    w = (
        local.filter(pl.col("variable") == "wind10m")
        .group_by("model", "dia")
        .agg(pl.col("value").max().round(1).alias("vento_max"))
    )
    p = (
        local.filter(
            (pl.col("variable") == "precip_24h") & (pl.col("valid_time").dt.hour() == 0)
        )
        .with_columns(pl.col("valid_time").dt.date().alias("dia"))
        .select("model", "dia", pl.col("value").round(1).alias("chuva_mm"))
    )
    return (
        t.join(w, on=["model", "dia"], how="left")
        .join(p, on=["model", "dia"], how="left")
        .sort("dia", "model")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", required=True, help="nome (parcial) ou station_id")
    parser.add_argument("--models", nargs="+", default=["gfs", "hres", "aifs"])
    parser.add_argument("--date", default=None, help="data do init 00Z (default: hoje)")
    args = parser.parse_args()

    station = resolve_station(args.station)
    init_date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    print(
        f"estação: {station['station_id']} {station['name']} "
        f"({station['lat']:.3f}, {station['lon']:.3f}) · rodada {init_date} 00Z",
        file=sys.stderr,
    )

    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for model in args.models:
            df = fetch_model(model, station, Path(tmp), init_date)
            if df.height:
                frames.append(df)
                print(f"[{model}] ok: {df.height} pontos", file=sys.stderr)
    if not frames:
        raise SystemExit("nenhum modelo retornou dados")

    summary = daily_summary(pl.concat(frames))
    print(f"\n{'dia':<12}{'modelo':<7}{'tmin':>6}{'tmax':>6}{'vento':>7}{'chuva24h':>10}")
    for r in summary.iter_rows(named=True):
        chuva = f"{r['chuva_mm']:.1f}" if r["chuva_mm"] is not None else "-"
        print(
            f"{r['dia']!s:<12}{r['model']:<7}{r['tmin']:>6}{r['tmax']:>6}"
            f"{r['vento_max']:>7}{chuva:>10}"
        )
    if "gfs" in args.models:
        print(f"\n⚠ {GFS_BIAS_NOTE}")


if __name__ == "__main__":
    main()
