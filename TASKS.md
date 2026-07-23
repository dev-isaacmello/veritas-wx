# TASKS — ledger de execução da Fase 1

> Mantido pelo orquestrador. Estados: `[ ]` pendente · `[>]` em andamento · `[x]` concluído · `[!]` bloqueado
> Regra: nenhuma task é "concluída" sem testes verdes e contagens reconciliadas.

## M0 / T1 — Bootstrap                                   [x]
- [x] git init + estrutura src/ + .gitignore
- [x] pyproject (uv, py3.12, grupos dev/grib/geo/scores)
- [x] contracts/: FACT_V1, OBS_V1, OBS_QC_V1, FORECAST_POINTS_V1, STATIONS_V1, RESULTS_V1
- [x] qc_bits (bitmask congelada) + units (conversões centralizadas)
- [x] runlog com identidade de reconciliação (guard R9)
- [x] match/elevation (lapse rate + golden 800/1200 => +2.6 K)
- [x] metrics_registry.yaml PRÉ-REGISTRADO (H1, H2, H3 + famílias BH)
- [x] configs/ingest.yaml + configs/qc_params.yaml
- [x] CI (GitHub Actions: ruff + pytest sem network/slow)
- [x] uv sync + suíte de testes verde
- [x] commit inicial + commit do pré-registro

## M1 / T2 — Auditoria de fontes                         [x]
- [x] scripts/audit_sources.py (sondagem S3: GFS, ecmwf-forecasts, AIWP)
- [x] Matriz fonte × mês para 2025-07..2026-06 (zero "desconhecido")
- [x] INMET apitempo: teste real → API horária DEGRADADA (2xx vazio); bulk zips OK
- [x] Inventário ISD p/ Brasil → NO-GO Fase 1 (arquivo NCEI congelado 2025-08)
- [x] Bytes por run/modelo => ~464 GB projetados p/ M7 (cabe nos 767 GB)
- [x] Licenças por fonte
- [x] docs/data_audit.md + ADR-0002 + janela CONFIRMADA 2025-07-01→2026-06-30

## M2 / T3 — Estações v0                                 [x]
- [x] ingest/static/stations.py (INMET /estacoes/T + isd-history)
- [x] Dedupe entre redes (513 cross_ref; 264 excluídas como duplicatas)
- [x] Cross-check elevação vs Copernicus GLO-30 (444 tiles COG; 1 falha)
- [x] Köppen (Beck 2023 1km, figshare CC-BY-4.0)
- [x] 94 células 0.25° com ≥2 estações (R7 verificado — repr_floor viável)
- [x] data/static/stations_v0.parquet validando STATIONS_V1 (1118 total, 854 incluídas)
- [x] Relatório: docs/stations_curation_v0.md + fila de revisão |Δelev|>100 m (15)

## M6 — Analyze core (paralelo; puro)                    [x]
- [x] analyze/bootstrap.py: moving block (blocos de dias), Politis–White clamp [2,30]
- [x] Property test: cobertura IC 95% em AR(1) φ=0.5 dentro de [0.92, 0.97] (marker slow)
- [x] Property test: ℓ=1 ≡ bootstrap iid
- [x] analyze/fdr.py: BH com exemplo golden calculado à mão
- [x] analyze/metrics/: mae, rmse, bias, variance_ratio, bias_by_percentile (com IC sempre)
- [x] analyze/decompose.py: mse_total / repr_floor_mean / mse_model_est (+flag clip)
- [x] analyze/strata.py + ingest/static/indices.py (ONI, MJO RMM via espelho IRI)
- [x] Nenhuma função pública retorna estimativa sem IC (teste de API)

## M3 — Obs ingest (INMET bulk, janela completa)         [>]  INMET-only (ADR-0002)
- [ ] ingest/observations/inmet_bulk.py: zips anuais dadoshistoricos (2025+2026)
- [ ] Raw no HD com sha256 no manifest; parse → OBS_V1 parquet
- [ ] Reconciliação: linhas lidas = escritas + rejeitadas POR MOTIVO
- [ ] Fallback BDMEP documentado (não implementado se bulk cobrir tudo)

## M4 — QC completo + estações v1 (~500 finais)          [ ]
## M5 — Thin slice GFS (1 mês × t2m × 20 estações)       [>]  runner+fetchers commitados; execução aguarda M3
## M7 — Escala: 4 modelos × 12 meses => fact v1          [ ]
## M8 — 3 figuras + notebook + publicação dataset        [ ]
## M9 — serve/ (FastAPI read-only)                       [ ]
