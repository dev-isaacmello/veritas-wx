"""T3 — Station curation v0: build data/static/stations_v0.parquet (STATIONS_V1).

Pipeline (every stage runlogged with reconciled counts; the runlog RAISES on
rows_in != rows_out + sum(dropped)):

    fetch_inmet -> fetch_isd_history -> canonical -> curation_bbox -> dedupe
    -> dem -> elev_review -> koppen -> grid_cells (R7 check) -> validate -> write

Curation rules v0 (documented in docs/stations_curation_v0.md, generated here):
- coordinates outside the Brazil bbox (lat -34..6, lon -74..-32) => excluded
  "coords_out_of_brazil" (unparseable coords => "invalid_coords");
- INMET CD_SITUACAO marking a decommissioned station (or DT_FIM_OPERACAO before
  the ingest window start) => excluded "inactive";
- INMET x ISD pair closer than 2 km => same physical site: INMET included,
  ISD twin excluded "duplicate_of:<station_id>", cross_ref on BOTH;
- |elev_station - elev_dem| > configs/qc_params.yaml metadata.max_elev_diff_m
  => status "review" (manual queue, NOT excluded).

No exclusion without a reason; no elevation/Köppen value is ever invented —
lookup failures become NULL plus an explicit pendency in the report.

Usage: uv run python scripts/build_stations.py
"""

import hashlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import yaml

from veritas_wx import __version__
from veritas_wx.contracts.schemas import STATIONS_V1
from veritas_wx.contracts.validate import ContractError, require_non_null, validate
from veritas_wx.ingest.static import dem, koppen, stations
from veritas_wx.runlog import log_stage

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILES = (REPO_ROOT / "configs/ingest.yaml", REPO_ROOT / "configs/qc_params.yaml")
OUT_PARQUET = REPO_ROOT / "data/static/stations_v0.parquet"
RAW_DIR = REPO_ROOT / "data/static/raw"
REPORT_PATH = REPO_ROOT / "docs/stations_curation_v0.md"

BRAZIL_CELLS_REF = 13_600


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
    static_dir = OUT_PARQUET.parent
    if not static_dir.is_dir():
        raise SystemExit(
            f"data root not available: {static_dir} — mount the data HD before building"
        )
    probe = static_dir / ".write_probe"
    probe.write_text("ok")
    probe.unlink()


def status_counts(df: pl.DataFrame) -> dict[str, int]:
    return {
        f"{row['status']}": row["len"]
        for row in df.group_by("status").len().sort("status").iter_rows(named=True)
    }


def reason_counts(df: pl.DataFrame, status: str) -> dict[str, int]:
    sub = df.filter(pl.col("status") == status)
    reasons = sub.with_columns(
        reason=pl.when(pl.col("exclusion_reason").str.starts_with("duplicate_of:"))
        .then(pl.lit("duplicate_of:*"))
        .otherwise(pl.col("exclusion_reason"))
    )
    return {
        str(row["reason"]): row["len"]
        for row in reasons.group_by("reason").len().sort("reason").iter_rows(named=True)
    }


def main() -> None:
    preflight_data_root()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ingest_cfg = yaml.safe_load(CONFIG_FILES[0].read_text())
    qc_cfg = yaml.safe_load(CONFIG_FILES[1].read_text())
    window_start: str = ingest_cfg["window"]["start"]
    max_elev_diff_m: float = float(qc_cfg["metadata"]["max_elev_diff_m"])
    ingest_version = compute_ingest_version()
    build_date = datetime.now(UTC).date().isoformat()

    inmet_records = stations.fetch_inmet_stations()
    log_stage(
        "t3.fetch_inmet",
        rows_in=len(inmet_records),
        rows_out=len(inmet_records),
        source=stations.INMET_STATIONS_URL,
    )

    isd_raw, isd_dropped = stations.fetch_isd_history(min_end="20250701")
    log_stage(
        "t3.fetch_isd_history",
        rows_in=isd_raw.height + sum(isd_dropped.values()),
        rows_out=isd_raw.height,
        dropped=isd_dropped,
        source=stations.ISD_HISTORY_URL,
    )

    df = stations.to_canonical(
        inmet_records,
        isd_raw,
        ingest_version=ingest_version,
        inactive_end_cutoff=window_start,
    )
    log_stage(
        "t3.canonical",
        rows_in=len(inmet_records) + isd_raw.height,
        rows_out=df.height,
        n_inmet=df.filter(pl.col("network") == "inmet").height,
        n_isd=df.filter(pl.col("network") == "isd").height,
        status=status_counts(df),
    )

    n = df.height
    df = stations.flag_out_of_bbox(df)
    log_stage("t3.curation_bbox", rows_in=n, rows_out=df.height, status=status_counts(df))

    n = df.height
    df = stations.dedupe_cross_network(df, max_km=2.0)
    log_stage(
        "t3.dedupe",
        rows_in=n,
        rows_out=df.height,
        n_cross_ref=df.filter(pl.col("cross_ref").is_not_null()).height,
        status=status_counts(df),
    )

    n = df.height
    df = stations.exclude_network_phase1(df, "isd", "isd_archive_frozen")
    log_stage(
        "t3.exclude_isd_phase1",
        rows_in=n,
        rows_out=df.height,
        status=status_counts(df),
    )

    lookup_df = df.filter(
        (pl.col("status") != "excluded")
        & pl.col("lat").is_not_null()
        & pl.col("lon").is_not_null()
    )
    triples = list(lookup_df.select("station_id", "lat", "lon").iter_rows())
    elevations, dem_stats = dem.lookup_dem_elevations(triples)
    elev_df = pl.DataFrame(
        {"station_id": list(elevations), "_elev_dem": list(elevations.values())},
        schema={"station_id": pl.Utf8, "_elev_dem": pl.Float64},
    )
    n = df.height
    df = (
        df.join(elev_df, on="station_id", how="left")
        .with_columns(elev_dem=pl.col("_elev_dem"))
        .drop("_elev_dem")
    )
    log_stage(
        "t3.dem",
        rows_in=n,
        rows_out=df.height,
        n_lookup=dem_stats["n_stations"],
        n_null=dem_stats["n_null"],
        n_tiles=dem_stats["n_tiles"],
        n_tiles_failed=dem_stats["n_tiles_failed"],
        transports=dem_stats["transports"],
    )

    n = df.height
    df = stations.flag_elev_review(df, max_diff_m=max_elev_diff_m)
    log_stage(
        "t3.elev_review",
        rows_in=n,
        rows_out=df.height,
        max_elev_diff_m=max_elev_diff_m,
        status=status_counts(df),
    )

    raster_path, koppen_info = koppen.download_koppen_raster(RAW_DIR)
    if raster_path is not None:
        kop_targets = df.filter(
            (pl.col("status") != "excluded")
            & pl.col("lat").is_not_null()
            & pl.col("lon").is_not_null()
        )
        kop_triples = list(kop_targets.select("station_id", "lat", "lon").iter_rows())
        kop_classes, kop_stats = koppen.lookup_koppen(kop_triples, raster_path)
        kop_df = pl.DataFrame(
            {"station_id": list(kop_classes), "_koppen": list(kop_classes.values())},
            schema={"station_id": pl.Utf8, "_koppen": pl.Utf8},
        )
        n = df.height
        df = (
            df.join(kop_df, on="station_id", how="left")
            .with_columns(koppen=pl.col("_koppen"))
            .drop("_koppen")
        )
    else:
        kop_stats = {"n_stations": 0, "n_null": df.height, "n_window_fallback": 0}
        n = df.height
    log_stage(
        "t3.koppen",
        rows_in=n,
        rows_out=df.height,
        raster_found=raster_path is not None,
        n_lookup=kop_stats["n_stations"],
        n_null=kop_stats["n_null"],
        n_window_fallback=kop_stats["n_window_fallback"],
        sources_attempted=koppen_info.get("attempted", []),
    )

    n_cells_ge2 = stations.count_cells_with_min_stations(df, res=0.25, min_n=2)
    log_stage(
        "t3.grid_cells",
        rows_in=df.height,
        rows_out=df.height,
        n_cells_ge2_included=n_cells_ge2,
        pct_of_brazil_cells=round(100.0 * n_cells_ge2 / BRAZIL_CELLS_REF, 2),
    )

    df = df.select(list(STATIONS_V1)).sort("station_id")
    validate(df, STATIONS_V1, "STATIONS_V1")
    require_non_null(df, ["station_id", "network", "native_id", "status", "ingest_version"],
                     "STATIONS_V1")
    unexplained = df.filter(
        (pl.col("status") == "excluded") & pl.col("exclusion_reason").is_null()
    )
    if unexplained.height:
        raise ContractError(
            f"{unexplained.height} excluded rows without exclusion_reason: "
            f"{unexplained['station_id'].to_list()[:10]}"
        )
    if df["station_id"].n_unique() != df.height:
        raise ContractError("duplicate station_id values in final frame")

    df.write_parquet(
        OUT_PARQUET,
        metadata={"schema_version": "STATIONS_V1", "ingest_version": ingest_version},
    )
    written_meta = pl.read_parquet_metadata(OUT_PARQUET)
    assert written_meta.get("schema_version") == "STATIONS_V1", written_meta
    log_stage(
        "t3.write",
        rows_in=df.height,
        rows_out=df.height,
        path=str(OUT_PARQUET),
        sha256=hashlib.sha256(OUT_PARQUET.read_bytes()).hexdigest(),
        ingest_version=ingest_version,
    )

    write_report(
        df,
        report_path=REPORT_PATH,
        ingest_version=ingest_version,
        build_date=build_date,
        isd_dropped=isd_dropped,
        n_isd_world=isd_raw.height + sum(isd_dropped.values()),
        dem_stats=dem_stats,
        koppen_info=koppen_info,
        kop_stats=kop_stats,
        n_cells_ge2=n_cells_ge2,
        max_elev_diff_m=max_elev_diff_m,
        window_start=window_start,
    )
    print(f"wrote {OUT_PARQUET} ({df.height} rows) and {REPORT_PATH}", file=sys.stderr)


def write_report(
    df: pl.DataFrame,
    *,
    report_path: Path,
    ingest_version: str,
    build_date: str,
    isd_dropped: dict[str, int],
    n_isd_world: int,
    dem_stats: dict,
    koppen_info: dict,
    kop_stats: dict,
    n_cells_ge2: int,
    max_elev_diff_m: float,
    window_start: str,
) -> None:
    """Generate docs/stations_curation_v0.md (pt-BR) from the final frame."""
    inmet = df.filter(pl.col("network") == "inmet")
    isd = df.filter(pl.col("network") == "isd")
    included = df.filter(pl.col("status") == "included")

    def by_status(sub: pl.DataFrame) -> tuple[int, int, int]:
        return tuple(
            sub.filter(pl.col("status") == s).height
            for s in ("included", "review", "excluded")
        )

    inmet_inc, inmet_rev, inmet_exc = by_status(inmet)
    isd_inc, isd_rev, isd_exc = by_status(isd)
    tot_inc, tot_rev, tot_exc = by_status(df)

    def fmt_reasons(status: str) -> str:
        rows = []
        for network in ("inmet", "isd"):
            sub = df.filter(pl.col("network") == network)
            for reason, count in reason_counts(sub, status).items():
                rows.append(f"| {network} | {reason} | {count} |")
        return "\n".join(rows) if rows else "| — | — | 0 |"

    review = (
        df.filter(pl.col("status") == "review")
        .with_columns(delta=(pl.col("elev_station") - pl.col("elev_dem")))
        .sort(pl.col("delta").abs(), descending=True)
    )
    review_rows = "\n".join(
        f"| {r['station_id']} | {r['name']} | {r['uf'] or '—'} | {r['lat']:.4f} | "
        f"{r['lon']:.4f} | {r['elev_station']:.1f} | {r['elev_dem']:.1f} | {r['delta']:+.1f} |"
        for r in review.iter_rows(named=True)
    ) or "| — | — | — | — | — | — | — | — |"

    dem_cov_base = df.filter(pl.col("status") != "excluded").height
    dem_nonnull = df.filter(
        (pl.col("status") != "excluded") & pl.col("elev_dem").is_not_null()
    ).height
    kop_nonnull = df.filter(
        (pl.col("status") != "excluded") & pl.col("koppen").is_not_null()
    ).height

    koppen_dist = (
        included.filter(pl.col("koppen").is_not_null())
        .group_by("koppen")
        .len()
        .sort("len", descending=True)
    )
    koppen_dist_rows = "\n".join(
        f"| {r['koppen']} | {r['len']} |" for r in koppen_dist.iter_rows(named=True)
    ) or "| — | 0 |"

    pendencias: list[str] = []
    if dem_stats.get("n_tiles_failed"):
        failed = ", ".join(sorted(dem_stats["failures"]))
        pendencias.append(
            f"- **DEM**: {dem_stats['n_tiles_failed']} tile(s) GLO-30 inacessíveis "
            f"({failed}) — estações afetadas ficaram com `elev_dem=NULL`."
        )
    if kop_stats.get("n_stations", 0) == 0:
        urls = ", ".join(koppen_info.get("attempted", [])) or "nenhuma"
        pendencias.append(
            f"- **Köppen**: nenhum raster obtido (URLs tentadas: {urls}) — "
            f"`koppen=NULL` para todas as estações."
        )
    elif kop_stats.get("n_null"):
        pendencias.append(
            f"- **Köppen**: {kop_stats['n_null']} estação(ões) sem classe mesmo com o "
            f"fallback de vizinhança 3×3 (pixel oceânico) — `koppen=NULL`."
        )
    if not pendencias:
        pendencias.append("- Nenhuma: DEM e Köppen resolvidos para todas as estações candidatas.")

    isd_drop_rows = "\n".join(
        f"| {reason} | {count} |" for reason, count in isd_dropped.items()
    )
    attempted = koppen_info.get("attempted", [])
    koppen_source = koppen_info.get("url") or (
        attempted[0] if attempted else "cache local (data/static/raw)"
    )

    text = f"""# Curadoria de estações v0 — T3

Gerado por `scripts/build_stations.py` em {build_date}.
`ingest_version = {ingest_version}` · contrato `STATIONS_V1` ·
saída `data/static/stations_v0.parquet` ({df.height} linhas).

## Regras de curadoria v0

1. **Bounding box do Brasil** (lat −34..6, lon −74..−32): coordenada fora ⇒ `excluded`,
   motivo `coords_out_of_brazil` (coordenada não parseável ⇒ `invalid_coords`). Nota: a caixa
   exclui territórios oceânicos distantes (ex.: Arquipélago de São Pedro e São Paulo, lon ≈ −29,3).
2. **INMET inativa**: `CD_SITUACAO` indicando desativação (`desativ*`, `encerr*`, `extint*`,
   `fechad*`) **ou** `DT_FIM_OPERACAO` anterior ao início da janela ({window_start}) ⇒ `excluded`,
   motivo `inactive`. `Pane` **não** exclui: a estação pode ter dados na janela; o corte por
   completude é da curadoria v1 (M4).
3. **Dedupe entre redes**: par INMET×ISD a menos de **2 km** ⇒ mesma localidade física.
   A INMET permanece `included` (pluviômetro horário é a fonte primária de precipitação);
   a gêmea ISD vira `excluded` com motivo `duplicate_of:<station_id>`; `cross_ref`
   preenchido nos **dois** registros.
4. **Divergência de elevação** (risco R6): |`elev_station` − `elev_dem`| > {max_elev_diff_m:g} m
   (config `qc_params.yaml: metadata.max_elev_diff_m`) ⇒ `status="review"` — fila de revisão
   manual, **não** excluída. O campo `exclusion_reason` documenta o motivo da revisão.

Nenhuma exclusão sem motivo registrado; nenhuma linha descartada silenciosamente
(o runlog levanta exceção se `rows_in != rows_out + Σ dropped`).

## Contagens por rede

| Rede | Candidatas | included | review | excluded |
|---|---|---|---|---|
| INMET (automáticas, apitempo) | {inmet.height} | {inmet_inc} | {inmet_rev} | {inmet_exc} |
| ISD (CTRY=BR, END ≥ 20250701) | {isd.height} | {isd_inc} | {isd_rev} | {isd_exc} |
| **Total** | **{df.height}** | **{tot_inc}** | **{tot_rev}** | **{tot_exc}** |

Filtro do inventário ISD mundial (antes do canônico, contado no runlog —
{n_isd_world} linhas no isd-history):

| Motivo do descarte | Linhas |
|---|---|
{isd_drop_rows}

## Excluídas e em revisão, por motivo

| Rede | Motivo | Estações |
|---|---|---|
{fmt_reasons("excluded")}

| Rede | Motivo (review) | Estações |
|---|---|---|
{fmt_reasons("review")}

## Células 0.25° com ≥ 2 estações incluídas (insumo do R7)

- **{n_cells_ge2} células** com ≥ 2 estações `included`
  ({100.0 * n_cells_ge2 / BRAZIL_CELLS_REF:.2f}% das ~{BRAZIL_CELLS_REF:,} células de 0.25°
  do Brasil — referência do PLAN, risco R7).
- Consequência: `repr_floor` só é estimável nessas células; nas demais fica `NULL`
  (sem imputação na Fase 1). Se a fração de pares com piso estimável ficar < 5% na M7,
  registrar a limitação no dataset (extensão geoestatística é Fase 2).

## Cobertura DEM e Köppen (estações não excluídas: {dem_cov_base})

- **DEM (Copernicus GLO-30, leitura COG por janela, 1 pixel/estação)**:
  `elev_dem` preenchido para {dem_nonnull}/{dem_cov_base}
  ({100.0 * dem_nonnull / max(dem_cov_base, 1):.1f}%) — {dem_stats["n_tiles"]} tiles abertos,
  {dem_stats["n_tiles_failed"]} falha(s), transporte {dem_stats["transports"]}.
- **Köppen (Beck et al. 2023, 1991–2020, 1 km)**: classe atribuída para
  {kop_nonnull}/{dem_cov_base} ({100.0 * kop_nonnull / max(dem_cov_base, 1):.1f}%);
  {kop_stats.get("n_window_fallback", 0)} estação(ões) resolvidas pelo fallback de
  vizinhança 3×3 (pixel costeiro oceânico). Raster em `data/static/raw/{koppen.RASTER_FILENAME}`
  (sha256 `{koppen_info.get("raster_sha256", "—")}`), fonte figshare/GloH2O
  `{koppen_source}`.

### Distribuição Köppen das incluídas

| Classe | Estações |
|---|---|
{koppen_dist_rows}

## Fila de revisão manual — |Δelev| > {max_elev_diff_m:g} m ({review.height} estações)

Estações com metadado de elevação divergente do DEM; permanecem `review` até
confirmação humana (coordenada errada? elevação errada? torre em encosta?).

| station_id | Nome | UF | lat | lon | elev_station (m) | elev_dem (m) | Δ (m) |
|---|---|---|---|---|---|---|---|
{review_rows}

## Pendências

{chr(10).join(pendencias)}

## Critérios da curadoria v1 (após M3)

- **Completude ≥ 80%** das horas na janela {window_start}..(fim da janela) por estação/variável —
  só computável depois da ingestão de observações (M3); meta ~500 estações finais.
- Resolução da fila de revisão (aceitar/corrigir/excluir cada |Δelev| > {max_elev_diff_m:g} m).
- Reavaliar estações `Pane` com dados suficientes na janela.
"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
