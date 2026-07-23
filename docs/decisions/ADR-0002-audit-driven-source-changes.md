# ADR-0002 — Mudanças de fonte impostas pela auditoria T2

Data: 2026-07-23 · Status: aceito · Evidência: `docs/data_audit.md`

## 1. ISD/NCEI fora da Fase 1

O arquivo público do NCEI está congelado (isd-history de 2025-08-30; última obs
BR 2025-08-24; sem arquivos 2026). Cobre <2 meses da janela. **Decisão:** Fase 1
é INMET-only (~477 automáticas operantes ≈ alvo de ~500). Registros ISD
permanecem na tabela de estações como `excluded` com
`exclusion_reason="isd_archive_frozen"`; o check DUPLICATE do QC fica dormente
(código permanece — ISD pode voltar). Deslocar a janela para trás para
acomodar ISD quebraria o AIFS operacional (2025-02-25) — rejeitado.

## 2. INMET: bulk `dadoshistoricos` como fonte primária (API degradada)

apitempo horário responde 2xx com corpo vazio para qualquer consulta (metadados
funcionam). Os zips anuais `dadoshistoricos` estão íntegros e cobrem a janela
inteira (2026.zip publicado 2026-07-01 → até junho/2026). **Decisão:** ingestão
horária via bulk zip (raw no HD com sha256 no manifest), BDMEP como fallback,
API mantida apenas para metadados de estação. Módulo dedicado
`ingest/observations/inmet_bulk.py`; o parser da API permanece para uso futuro.

## 3. MJO: RMM via espelho IRI (não trocar de índice)

O arquivo RMM do BoM está congelado em 2024-02-24. O stratum `mjo_phase` foi
**pré-registrado** como fases RMM 1–8 com amplitude ≥ 1; trocar para OMI (PSL,
atualizado) mudaria a semântica de um stratum registrado — proibido sem bump de
versão do registro. **Decisão:** manter RMM, fonte primária = espelho IRI
(mesma série), BoM rebaixado a referência de formato. Se o espelho IRI também
congelar dentro da janela, aí sim: registry v2 + ADR novo.

## 4. GraphCast: leitura seletiva HDF5 obrigatória

`GRAP_v100_GFS` confirmado: netCDF4/HDF5 de ~5,8 GB/run (inteiro = 4,2 TB —
inviável e desnecessário). **Decisão:** leitura seletiva por range requests
(h5py/h5netcdf + fsspec; grupo de dependências `graphcast`). 50 runs ausentes
(6,8%, 2026-04..06) são aceitos como estão: ausência vira ausência de par, e as
matched views (não-negociável #5) garantem comparação válida por construção.
Nunca imputados.

## Consequências

- Janela **confirmada**: 2025-07-01 → 2026-06-30 (D1 fechada).
- Tráfego M7 projetado ~464 GB — cabe nos 767 GB do HD sem poda.
- Orografia ECMWF disponível (`z` no índice de 0h) → correção de elevação
  viável também para HRES/AIFS (retira a ressalva do thin-slice).
