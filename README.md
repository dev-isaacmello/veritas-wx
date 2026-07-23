# veritas-wx

🇧🇷 Português · [🇺🇸 English](README.en.md)

**Verificação de previsões do tempo contra observações brutas de estações de superfície — não contra reanálise.**

Todo grande leaderboard de IA meteorológica verifica os modelos contra reanálise (ERA5) — que é
saída de modelo, não medição. O ERA5 não assimila precipitação diretamente; o WeatherBench
reconhece verificação por estações como "trabalho futuro" desde 2023. Estudos que mediram contra
observações reais encontraram erros **15–45% maiores** do que os leaderboards reportam.

E há uma razão matemática para isso importar. Um modelo treinado com MSE converge para a média
condicional e, pela lei da variância total, a média condicional tem *estritamente menos variância*
que o processo real (σ_fcst = ρ·σ_obs < σ_obs quando ρ < 1). Modelos de IA treinados com MSE são
cegos para extremos recordistas **por construção** — e quanto melhor o RMSE no leaderboard, mais
suavizados os campos. O veritas-wx mede essa pegada diretamente, contra a realidade.

**O entregável primário não é um dashboard.** É um dataset público e reproduzível de pares
previsão↔observação exatamente casados, em Parquet. Todo o resto é query em cima dele.

## O que o torna diferente

Auditamos o código-fonte do WeatherBench 2 e do sucessor WeatherBench-X antes de construir.
As capacidades marcadas ✅/❌ foram verificadas no código deles, não nos papers:

| Capacidade | WB2 | WBX | veritas-wx |
|---|---|---|---|
| Verificação contra estações brutas | ❌ (0 ocorrências) | parcial (loader METAR; sem resultado de estação publicado) | ✅ INMET Brasil, 300+ estações curadas |
| Intervalo de confiança em toda estimativa | ❌ (0 ocorrências) | módulo opt-in | ✅ imposto por teste — nenhuma API pública retorna número nu |
| QC físico independente das observações | ❌ | ❌ (só flags do provedor) | ✅ 6 checks, bitmask flag-nunca-deleta |
| Decomposição do erro de representatividade | ❌ | ❌ (0 ocorrências) | ✅ piso empírico de estações co-localizadas |
| Estratificação por regime (Köppen/ENSO/MJO) | ❌ | ❌ (0 ocorrências) | ✅ estratos pré-registrados |
| Pré-registro + controle FDR | ❌ | ❌ | ✅ registro commitado antes de qualquer dado, famílias BH |
| Convenções de acumulação de precip por modelo | padrão único, clipping silencioso de negativos | fora de escopo | ✅ convenções explícitas, negativos preservados e contados |
| SEEPS com IC de bootstrap, por estação | só grade, sem IC | só climatologia de grade | ✅ p1/limiar úmido por estação do histórico INMET |

O que o WBX faz melhor, nós portamos (Apache 2.0, com atribuição): ajuste de vento por altitude
(Ingleby), t-tests Geer AR(2) + HAC Lazarus para comparação pareada barata, peso por densidade de
estações (Rodwell). O que o WB2 computava e o WBX abandonou, portamos também: climatologia rolante
por dia-do-ano — por estação, em vez de por célula de grade.

## Escopo da Fase 1 (teto rígido)

| | |
|---|---|
| Região | Brasil |
| Estações | 304 curadas (de 1.118 candidatas; completude ≥80% de dados limpos) |
| Modelos | AIFS, GraphCast (operacional), IFS HRES, GFS |
| Variáveis | t2m, vento 10m, precipitação 24h |
| Período | 2025-07-01 → 2026-06-30 |

## Não-negociáveis científicos

1. Erro de representatividade decomposto, nunca embutido no "erro do modelo"
2. Ajuste de elevação para temperatura (lapse rate) e vento (Ingleby 2014) — valores cru e ajustado persistidos
3. Toda estatística sai com IC de bootstrap em blocos móveis (blocos de dias, comprimento Politis-White)
4. Métricas pré-registradas (`metrics_registry.yaml`, commitado antes de qualquer análise), FDR Benjamini-Hochberg
5. Comparações entre modelos só em amostras exatamente casadas (mesmas chaves, mesma máscara de QC, views materializadas)

Observações suspeitas são **flagadas, nunca deletadas**. Dado faltante é **NULL, nunca imputado**.
Todo estágio do pipeline impõe a identidade de reconciliação: `linhas_entrada == linhas_saída + drops itemizados`.

## Resultados preliminares (fatia de validação ago/2025, GFS, 123 mil pares)

- GFS roda **+1,2 a +2,3 °C quente** sobre o Brasil, crescendo com o lead — pior nas horas mais
  frias (+3,7 °C no percentil observado 0–10)
- Vento superestimado em +1,4–1,7 m/s contra anemômetros de estação
- Viés de chuva forte é negativo nos bins de percentil altos — a direção que a teoria da média
  condicional prevê (hipótese pré-registrada H2)
- Todos os números carregam IC95 de bootstrap em blocos; os resultados completos
  (4 modelos × 12 meses) chegam com o backfill

## Como funciona

```
ingest/          zips bulk do INMET, fetches GRIB por byte-range (GFS/HRES/AIFS),
                 leitura seletiva de chunks HDF5 (GraphCast: ~280 MB/run em vez de 5,8 GB)
   ↓ manifests sha256, idempotente
qc/              6 checks físicos independentes -> bitmask qc_flags (nada é deletado)
   ↓
match/           extração bilinear nas estações, ajuste de elevação, convenções de
                 precip por modelo, piso de representatividade, pares FACT_V1
   ↓ views exatamente casadas por comparação de modelos
analyze/         só funções puras: bootstrap em blocos, FDR BH, métricas pré-registradas
                 (variance_ratio, bias_by_percentile, skill por regime), SEEPS+IC,
                 t-tests AR(2)/HAC, climatologia por estação, peso por densidade
```

## Experimente em 2 minutos: previsão para a sua cidade

```bash
uv sync --group dev --group grib
uv run python scripts/forecast_point.py --station "PORTO ALEGRE"
```

Busca a rodada 00Z de hoje (GFS + IFS HRES + AIFS, ~15 MB via byte-range) direto dos buckets
oficiais, extrai no ponto da estação INMET e imprime o resumo diário dos 3 modelos lado a lado —
com a leitura calibrada pela verificação (ex.: o viés quente medido do GFS). Funciona para
qualquer uma das 1.118 estações da base (`--station "TORRES"`, `--station inmet:A801`, ...).

Quando os modelos concordam, confiança alta; quando divergem em chuva forte, a verificação já
mostrou em quem confiar. O valor cru dos modelos nunca é alterado.

## Como executar (pipeline científico completo)

Requer Python 3.12, [uv](https://docs.astral.sh/uv/), ~500 GB de disco e banda para o backfill
completo (cada estágio é retomável; execuções parciais funcionam).

```bash
uv sync --group dev --group grib --group geo --group graphcast --group figures

# testes (sem rede)
uv run pytest -m "not network and not slow"

# o pipeline inteiro, um comando (estágios independentes e retomáveis):
uv run python scripts/build_all.py --stages obs qc forecasts fact views figures

# ou por partes:
uv run python scripts/ingest_obs.py                  # bulk INMET -> parquet OBS_V1
uv run python scripts/run_qc.py                      # QC + corte de curadoria de estações
uv run python scripts/scale_ingest.py --model gfs --all-months   # previsões (retomável)
uv run python scripts/build_fact.py --forecast-points ... --out ...
uv run python scripts/build_views.py --fact data/fact/*.parquet
uv run python scripts/make_figures.py --fact data/fact/*.parquet
```

O layout de armazenamento espera um diretório `data/` (ou symlink) com espaço para o lake; todo
download é registrado em manifest sha256 e re-execuções pulam artefatos verificados.

## Fontes de dados e licenças

| Fonte | Uso | Licença |
|---|---|---|
| INMET (bulk dadoshistoricos) | observações de superfície | dado público |
| NOAA GFS (AWS Open Data) | previsões | domínio público (EUA) |
| ECMWF Open Data (HRES, AIFS) | previsões | CC-BY-4.0 |
| Arquivo AIWP NOAA/CIRA (GraphCast) | previsões | domínio público (citar Radford et al. 2025) |
| Copernicus GLO-30 DEM | conferência de elevação das estações | livre com atribuição |
| Beck et al. 2023 Köppen-Geiger | estratos climáticos | CC-BY-4.0 |

**Código: MIT.** Partes de `analyze/ttest.py`, `analyze/weighting.py`, `analyze/climatology.py`,
`analyze/metrics/seeps.py` e `match/elevation.py` derivam do WeatherBench 2 / WeatherBench-X
(Copyright 2023 Google LLC, Apache License 2.0) — ver docstrings dos módulos.
**Dataset publicado: CC-BY-4.0** com atribuições por fonte.

## Roadmap

- Fact table completa 12 meses × 4 modelos + as três figuras pré-registradas (H1/H2/H3)
- Emenda registry v2: promover SEEPS/ACC a confirmatórias com famílias BH próprias
- Índice de oportunidade: prever *quando* a previsão será confiável
- Fase 2: mais redes (validação cruzada METAR), mais regiões

## Contribuindo

Veja [CONTRIBUTING.md](CONTRIBUTING.md) (🇺🇸 [English](CONTRIBUTING.en.md)). A versão curta:
qualidade acima de quantidade, nenhuma estimativa sem IC, e nada entra no registro confirmatório
sem emenda versionada.
