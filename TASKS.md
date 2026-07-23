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
- [x] ISD benched pós-ADR-0002: exclusion_reason="isd_archive_frozen" (446 exc.)
- [x] R7 verificado INMET-only: 15 células ≥2 estações (94 de ontem inflado por
      pares INMET↔ISD); efetivas COM dados: 14; com t2m ≥50%: 10 — repr_floor
      estimável (pooling temporal) mas com diversidade espacial limitada
- [x] data/static/stations_v0.parquet validando STATIONS_V1 (1118 total, 666 incluídas)
- [x] Relatório: docs/stations_curation_v0.md + fila de revisão |Δelev|>100 m (6)

## M6 — Analyze core (paralelo; puro)                    [x]
- [x] analyze/bootstrap.py: moving block (blocos de dias), Politis–White clamp [2,30]
- [x] Property test: cobertura IC 95% em AR(1) φ=0.5 dentro de [0.92, 0.97] (marker slow)
- [x] Property test: ℓ=1 ≡ bootstrap iid
- [x] analyze/fdr.py: BH com exemplo golden calculado à mão
- [x] analyze/metrics/: mae, rmse, bias, variance_ratio, bias_by_percentile (com IC sempre)
- [x] analyze/decompose.py: mse_total / repr_floor_mean / mse_model_est (+flag clip)
- [x] analyze/strata.py + ingest/static/indices.py (ONI, MJO RMM via espelho IRI)
- [x] Nenhuma função pública retorna estimativa sem IC (teste de API)

## M3 — Obs ingest (INMET bulk, janela completa)         [x]  INMET-only (ADR-0002)
- [x] ingest/observations/inmet_bulk.py: zips anuais dadoshistoricos (2025+2026)
- [x] Raw no HD com sha256 no manifest; parse → OBS_V1 parquet
- [x] Reconciliação exata em todos os stages (parse/clip/dedupe; 0 conflitos)
- [x] data/obs/obs_inmet_v0.parquet: 9.705.180 linhas, 548 estações com dados
- [x] Formato validado contra zip real (643 CSVs; ',8'; vazio E -9999 = missing)
- [ ] Fallback BDMEP documentado (não implementado — bulk cobriu 100% da janela)
- Nota p/ M4: 127 estações com cobertura t2m <50%; 118 incluídas sem dados na janela

## M4 — QC completo + estações v1                        [x]
- [x] QC calibrado contra janela real (ADR-0003): sigma_floor no SPATIAL
      (610k→11k flags), precip_1h isenta; obs_qc_v0.parquet (9.7M, 0 deleções)
- [x] Corte v1 pré-registrado (t2m limpa ≥80%): 304 incluídas (~500 não
      atingido; critério prevalece) + docs/stations_v1_report.md
- [x] Política repr_floor: floor de TODAS as estações com obs limpa (14
      células), pares só do conjunto v1

## M5 — Thin slice GFS (ago/2025 × 3 vars × 20 estações) [x]
- [x] Bug real capturado: APCP dual-record com metas distintas (0-24 + 18-24)
      → pick_gfs_apcp_bucket força o bucket 6h, raise se ausente
- [x] 62 inits × 40/40 leads, 145.080 pontos, 0 falhas de fetch, reconc. exata
- [x] Mini-fact: 123.008 pares (22.072 obs_missing contados; 0 delta_z)
- [x] Métricas com IC bootstrap (docs/m5_slice_report.md): t2m bias +1.2→+2.3 K,
      RMSE 2.2→3.4 K; wind bias +1.4-1.7 m/s; precip ~0 (estação seca);
      variance_ratio t2m 1.011 [0.977, 1.042]
- Nota: variance_ratio precip degenera em mês seco (anotado no relatório)

## M7 — Escala: 4 modelos × 12 meses => fact v1          [>]
- [ ] Runner retomável por modelo×mês (manifest-driven, background)
- [ ] GFS 12 meses · HRES/AIFS conforme retenção · GraphCast HDF5 seletivo
- [ ] fact v1 particionado year/month/model + matched views
## M8 — 3 figuras + notebook + publicação dataset        [ ]
## M9 — serve/ (FastAPI read-only)                       [ ]
