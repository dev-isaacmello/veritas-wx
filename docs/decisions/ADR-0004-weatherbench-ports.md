# ADR-0004 — Portes seletivos do WeatherBench 2 / WeatherBench-X

Data: 2026-07-23 · Status: aceito · Evidência: auditorias código-a-código dos
dois repos (agentes de exploração, vendor/weatherbench2 @ shallow,
vendor/weatherbenchX @ 984f465)

## Contexto

Auditamos o WeatherBench 2 (deprecado) e seu sucessor WeatherBench-X para
decidir o que reaproveitar. Achados centrais:

- WB2: 100% grade-vs-grade; zero estações, zero IC, zero QC, zero FDR.
- WBX: obs esparsas reais (METAR, grade→ponto, lapse de altitude) e
  inferência estatística superior (t-test Geer AR2, HAC Lazarus, bootstrap
  estacionário). PORÉM: representatividade 0 hits, QC físico 0, estratos de
  regime 0, FDR 0, benchmark público segue grade-vs-ERA5 sem resultado de
  estação publicado; climatologia não é computada (consome zarrs do WB2).

## Decisão: portar 5 itens, como DIAGNÓSTICOS EXPLORATÓRIOS

| # | Item | Origem (Apache 2.0) | Destino |
|---|---|---|---|
| 1 | Ajuste de vento por altitude (Ingleby 2014 §3.3: fator 1+0.002·Δz, teto 3.0, só Δz>100 m) | WBX `interpolations.py` | `match/elevation.py` + `fact.py` (wind10m ganha `fcst_elev_adj`) |
| 2 | t-tests Geer AR(2) + HAC Lazarus (EWC) + pareado estilo Diebold-Mariano | WBX `statistical_inference/t_test.py` | `analyze/ttest.py` |
| 3 | Peso por densidade de estações (Rodwell 2010 eq. 22-23, kernel gaussiano α₀=0.75°) | WBX `weighting.py` | `analyze/weighting.py` + peso opcional nos `*_stat` |
| 4 | Climatologia por estação (doy×hora, janela rolante 61 d, pesos triangulares, wrap circular) | WB2 `utils.py` | `analyze/climatology.py` |
| 5 | SEEPS por estação (Rodwell 2010; p1/limiar úmido POR ESTAÇÃO INMET; máscara p1∈[0.1,0.85]) embrulhado no nosso block bootstrap | WBX `metrics/categorical.py` + WB2 `SEEPSThreshold` | `analyze/metrics/seeps.py` |

Regras da decisão:

1. **Porte de funções, nunca dependência de pacote** — stack deles é
   xarray/Beam/JAX com pin `python<3.12`; o nosso é polars numa máquina.
2. **Atribuição Apache 2.0** no docstring de cada módulo derivado.
3. **Nada entra no registro confirmatório**: `metrics_registry.yaml` permanece
   intocado. Os itens são diagnósticos exploratórios até uma emenda
   versionada do registro (proteção do protocolo FDR). O bootstrap em blocos
   segue sendo o motor primário de IC; os t-tests entram como verificação
   cruzada barata e para comparações pareadas.
4. **Não portamos**: aparato Beam/xarray-beam (escala errada), delta method
   JAX (nosso bootstrap recomputa a estatística por draw, tratando
   não-linearidade sem aproximação), wrappers de composição de métricas
   (conflitam com o pré-registro), suite probabilística (sem ensemble na
   Fase 1 — roubar quando AIFS-ENS entrar).

## O que segue exclusivamente nosso (evidência nas auditorias)

QC físico independente de 6 checks; decomposição de representatividade com
piso empírico; estratos de regime (Köppen/ENSO/MJO); FDR Benjamini-Hochberg
por família pré-registrada; contrato "nenhuma estimativa sem IC" imposto por
teste; INMET/Brasil com convenções de acumulação de precip por modelo; views
exatamente pareadas; reconciliação de linhas com drops itemizados.

## Consequências

- Correção de vento em estações serranas e média nacional sem viés de
  densidade (Sudeste super-representado) — dois vieses reais eliminados.
- SEEPS com IC por estação: resultado que nem ECMWF nem Google publicam.
- Climatologia habilita ACC, baselines (persistência/climatologia) e a
  variante climatológica do bias_by_percentile em trabalhos futuros.
- Emenda futura do registro (v2) poderá promover SEEPS/ACC a confirmatórios,
  com famílias BH próprias, sem contaminar as hipóteses H1–H3 já registradas.
