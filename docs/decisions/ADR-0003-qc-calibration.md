# ADR-0003 — Calibração do QC contra dados reais (M4)

Data: 2026-07-23 · Status: aceito · Evidência: primeira rodada de
`scripts/run_qc.py --qc-only` sobre `obs_inmet_v0.parquet` (9.705.180 linhas).

## Contexto

`configs/qc_params.yaml` nasceu PROVISÓRIO (T1). A M4 exige calibrar cada
limiar contra a janela real antes do corte de estações v1. A primeira rodada
produziu:

| check | flags | taxa | veredito |
|---|---|---|---|
| RANGE | 0 | 0% | são (sentinelas já removidos no parse) |
| STEP | 1.934 | 0,02% | são |
| PERSISTENCE | 3.596 | 0,04% | são |
| SPATIAL | **610.957** | **6,3%** | **sobre-flagando** |
| METADATA | 9.510 | 0,10% | são (1 estação, A772, sem elev_station) |
| DUPLICATE | 0 | 0% | dormente (ISD fora — ADR-0002) |

Inspeção dos flags SPATIAL: valores flagados eram leituras normais
(303 K, vento 0,0 m/s). Causa raiz: com k≤5 vizinhos reportando na resolução
0,1 do INMET, o MAD colapsa a ~0 e o z robusto explode. Segundo fator:
diferenças legítimas de microclima/exposição não são erro de sensor.

## Decisões

1. **Piso de sigma no SPATIAL** — `z = |desvio| / max(1.4826·MAD, sigma_floor)`
   com `t2m: 1.5 K`, `wind10m: 1.5 m/s`. Com `max_mad_z: 5.0`, um flag passa a
   exigir desvio > 7,5 K / 7,5 m/s da mediana dos vizinhos: pega erro
   grosseiro, poupa microclima.
2. **precip_1h ISENTA do SPATIAL** — chuva convectiva horária é espacialmente
   esparsa por natureza; uma célula real de 80 mm sobre vizinhos secos é
   sinal, não defeito. Flagar picos reais enviesaria a verificação CONTRA
   eventos extremos — exatamente o que o sistema quer medir. RANGE (≤200 mm/h),
   STEP e PERSISTENCE continuam cobrindo precip.
3. **Demais limiares mantidos** como estavam: taxas observadas (0–0,04%) são
   compatíveis com falha real de sensor, sem evidência para apertar/afrouxar.
4. `calibrated: true` em `qc_params.yaml`. Refinamento por Köppen×mês fica
   adiado até haver evidência de necessidade (anti-padrão: não sofisticar sem
   dado que justifique).

## Consequências

- Mudança de limiar ⇒ novo `ingest_version` (hash dos configs) — rastreável.
- O corte v1 (completude ≥80% de t2m LIMPA) passa a usar flags calibrados.
- Testes golden novos: colapso de MAD não flaga leitura normal; erro
  grosseiro ainda flaga; precip extrema nunca flaga espacialmente.
