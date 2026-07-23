# TASKS — ledger de execução da Fase 1

> Mantido pelo orquestrador. Estados: `[ ]` pendente · `[>]` em andamento · `[x]` concluído · `[!]` bloqueado
> Regra: nenhuma task é "concluída" sem testes verdes e contagens reconciliadas.

## M0 / T1 — Bootstrap                                   [>]
- [x] git init + estrutura src/ + .gitignore
- [x] pyproject (uv, py3.12, grupos dev/grib/geo/scores)
- [x] contracts/: FACT_V1, OBS_V1, OBS_QC_V1, FORECAST_POINTS_V1, STATIONS_V1, RESULTS_V1
- [x] qc_bits (bitmask congelada) + units (conversões centralizadas)
- [x] runlog com identidade de reconciliação (guard R9)
- [x] match/elevation (lapse rate + golden 800/1200 => +2.6 K)
- [x] metrics_registry.yaml PRÉ-REGISTRADO (H1, H2, H3 + famílias BH)
- [x] configs/ingest.yaml + configs/qc_params.yaml
- [x] CI (GitHub Actions: ruff + pytest sem network/slow)
- [ ] uv sync + suíte de testes verde
- [ ] commit inicial + commit do pré-registro

## M1 / T2 — Auditoria de fontes                         [ ]  → agente background
- [ ] scripts/audit_sources.py (sondagem S3: GFS, ecmwf-forecasts, AIWP)
- [ ] Matriz fonte × mês para 2025-07..2026-06 (zero "desconhecido")
- [ ] INMET apitempo: teste real 1 estação-mês (retry; fallback BDMEP)
- [ ] Inventário ISD p/ Brasil
- [ ] Bytes por run/modelo => projeção de disco p/ M7
- [ ] Licenças por fonte
- [ ] docs/data_audit.md + GO/NO-GO por fonte + janela confirmada

## M2 / T3 — Estações v0                                 [ ]  → agente background
- [ ] ingest/static/stations.py (INMET /estacoes/T + isd-history)
- [ ] Dedupe entre redes (cross_ref)
- [ ] Cross-check elevação vs Copernicus GLO-30 (COG range reads)
- [ ] Köppen (Beck) — ou marcado pendente com fonte documentada
- [ ] Contagem células 0.25° com ≥2 estações (verificação antecipada R7)
- [ ] data/static/stations_v0.parquet validando STATIONS_V1
- [ ] Relatório: incluídas/excluídas POR MOTIVO + fila de revisão |Δelev|>100 m

## M6 — Analyze core (paralelo; puro)                    [ ]  → agente background
- [ ] analyze/bootstrap.py: moving block (blocos de dias), Politis–White clamp [2,30]
- [ ] Property test: cobertura IC 95% em AR(1) φ=0.5 dentro de [0.92, 0.97] (marker slow)
- [ ] Property test: ℓ=1 ≡ bootstrap iid
- [ ] analyze/fdr.py: BH com exemplo golden calculado à mão
- [ ] analyze/metrics/: mae, rmse, bias, variance_ratio, bias_by_percentile (com IC sempre)
- [ ] analyze/decompose.py: mse_total / repr_floor_mean / mse_model_est (+flag clip)
- [ ] Nenhuma função pública retorna estimativa sem IC (teste de API)

## M3 — Obs ingest (INMET + ISD, janela completa)        [ ]  depois de T2/T3
## M4 — QC completo + estações v1 (~500 finais)          [ ]
## M5 — Thin slice GFS (1 mês × t2m × 20 estações)       [ ]
## M7 — Escala: 4 modelos × 12 meses => fact v1          [ ]
## M8 — 3 figuras + notebook + publicação dataset        [ ]
## M9 — serve/ (FastAPI read-only)                       [ ]
