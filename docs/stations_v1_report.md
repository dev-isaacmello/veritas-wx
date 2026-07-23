# Estações v1 — corte por completude (M4)

Gerado por `scripts/run_qc.py` · ingest_version `0.1.0+1f908ca.2278325d`

Critério (PLAN M4): completude de t2m **limpa** (qc_flags == 0) ≥ 80% das 8760 horas da janela.

- Candidatas (incluídas na v0): **666**
- Aprovadas (v1 included): **304**
- Reprovadas: **362** (`t2m_clean_completeness_lt_0.8`)

## Taxas de flag por check (obs_qc_v0, todas as variáveis)

| variable | n | RANGE | STEP | PERSISTENCE | SPATIAL | METADATA | DUPLICATE | clean |
|---|---|---|---|---|---|---|---|---|
| precip_1h | 3081164 | 0 | 0 | 1714 | 0 | 3170 | 0 | 3076280 |
| t2m | 3459511 | 0 | 1933 | 829 | 7763 | 3170 | 0 | 3446096 |
| wind10m | 3164505 | 0 | 1 | 1053 | 3222 | 3170 | 0 | 3157087 |

## 20 piores completudes (fila de inspeção)

| station_id | completeness_t2m | n_clean_t2m |
|---|---|---|
| inmet:A043 | 0.0000 | 0 |
| inmet:A051 | 0.0000 | 0 |
| inmet:A102 | 0.0000 | 0 |
| inmet:A109 | 0.0000 | 0 |
| inmet:A113 | 0.0000 | 0 |
| inmet:A117 | 0.0000 | 0 |
| inmet:A120 | 0.0000 | 0 |
| inmet:A122 | 0.0000 | 0 |
| inmet:A124 | 0.0000 | 0 |
| inmet:A126 | 0.0000 | 0 |
| inmet:A136 | 0.0000 | 0 |
| inmet:A204 | 0.0000 | 0 |
| inmet:A207 | 0.0000 | 0 |
| inmet:A214 | 0.0000 | 0 |
| inmet:A217 | 0.0000 | 0 |
| inmet:A218 | 0.0000 | 0 |
| inmet:A221 | 0.0000 | 0 |
| inmet:A228 | 0.0000 | 0 |
| inmet:A230 | 0.0000 | 0 |
| inmet:A232 | 0.0000 | 0 |
