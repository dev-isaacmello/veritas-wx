# PLAN.md — veritas-wx

> Verificação de previsão meteorológica contra observações brutas de estação de superfície.
> Entregável primário: dataset público de pares casados forecast↔observação, em Parquet, reprodutível.
>
> **Status: AGUARDANDO APROVAÇÃO. Nenhum código foi escrito.**
>
> Este documento segue as 5 seções exigidas pelo brief, mais três seções de apoio
> (decisões embutidas para aprovação, critério de pronto, guardas de escopo).

---

## 0. Enquadramento (uma nota honesta antes de tudo)

O que comparamos são **sistemas operacionais end-to-end** (condição inicial + modelo + pós-processamento
de distribuição pública), não arquiteturas em condições controladas. AIFS parte da análise do ECMWF;
GraphCast operacional parte da análise do GFS; HRES e GFS partem das suas próprias. Isso é uma *feature*
do projeto — é a pergunta que o usuário final faz ("qual previsão devo usar?") — mas precisa estar
escrito na documentação do dataset para ninguém ler os números como comparação de arquiteturas.

---

## 1. Estrutura de diretórios

```
veritas-wx/
├── PLAN.md                      # este documento
├── README.md                    # visão pública do projeto
├── LICENSE                      # questão aberta (ver §6)
├── pyproject.toml               # uv, Python 3.12
├── metrics_registry.yaml        # PRÉ-REGISTRO — commit obrigatório ANTES de qualquer análise
├── configs/
│   ├── ingest.yaml              # janela, modelos, runs, leads, paths, max_delta_z, interp defaults
│   └── qc_params.yaml           # limiares dos checks de QC, versionado
├── src/veritas_wx/
│   ├── contracts/               # FONTE DA VERDADE: schemas polars, validadores, bits de QC, unidades
│   │   ├── schemas.py           #   FACT_V1, OBS_V1, FORECAST_POINTS_V1, STATIONS_V1, RESULTS_V1
│   │   ├── qc_bits.py           #   valores da bitmask
│   │   └── validate.py          #   validação com mensagens explícitas; falha ruidosa
│   ├── runlog.py                # log estruturado: stage, rows_in, rows_out, dropped{motivo: n}
│   ├── ingest/
│   │   ├── manifest.py          # checksums sha256, idempotência, status por artefato
│   │   ├── forecasts/
│   │   │   ├── gribidx.py       #   leitura por byte-range via arquivos .idx/.index (comum a GFS/ECMWF)
│   │   │   ├── gfs.py           #   s3://noaa-gfs-bdp-pds
│   │   │   ├── ecmwf_opendata.py#   s3://ecmwf-forecasts (HRES "oper" + AIFS "aifs-single")
│   │   │   └── aiwp.py          #   s3://noaa-oar-mlwp-data (GraphCast operacional, CIRA/NOAA)
│   │   ├── observations/
│   │   │   ├── inmet.py         #   API apitempo.inmet.gov.br + fallback BDMEP
│   │   │   └── isd.py           #   ISD-Lite (NCEI/S3)
│   │   └── static/
│   │       ├── stations.py      #   metadados, dedupe entre redes, curadoria
│   │       ├── dem.py           #   Copernicus GLO-30 (s3://copernicus-dem-30m)
│   │       ├── koppen.py        #   Beck et al. (raster 1 km)
│   │       └── indices.py       #   ONI (CPC) e MJO RMM (BoM) — tabelas mensais/diárias minúsculas
│   ├── qc/
│   │   ├── checks.py            # cada check é função pura: (df, params) -> df com 1 bit a mais
│   │   └── runner.py            # composição dos checks + contagens por bit
│   ├── match/
│   │   ├── extract.py           # grade -> ponto de estação (bilinear/nearest), acumulação de precip
│   │   ├── elevation.py         # correção por lapse rate; delta_z
│   │   ├── repr_floor.py        # piso de representatividade por célula
│   │   ├── fact.py              # montagem da fact table (contrato FACT_V1)
│   │   └── views.py             # views de pares casados exatos por comparison_id
│   ├── analyze/                 # 100% PURO: DataFrame -> DataFrame. Sem I/O, sem estado.
│   │   ├── metrics/             # mae, rmse, bias, variance_ratio, bias_by_percentile, ...
│   │   ├── bootstrap.py         # moving block bootstrap (blocos de dias)
│   │   ├── fdr.py               # Benjamini-Hochberg sobre a família registrada
│   │   ├── decompose.py         # erro_total / piso_representatividade / erro_modelo_estimado
│   │   └── strata.py            # junção de estratos (estação do ano, Köppen, ENSO, MJO)
│   └── serve/
│       ├── api.py               # FastAPI read-only sobre DuckDB (último milestone)
│       └── sql/                 # definições de views DuckDB, versionadas
├── scripts/
│   ├── audit_sources.py         # T2: sondagem de cobertura de cada fonte
│   ├── build_all.py             # O COMANDO ÚNICO: reconstrói a fact table do zero
│   └── make_figures.py          # as 3 figuras do critério de pronto (código testado, não notebook)
├── tests/
│   ├── unit/                    # casos calculados à mão (golden)
│   ├── property/                # hypothesis: invariantes das funções numéricas
│   └── integration/             # thin slice end-to-end com dados pequenos versionados em tests/data/
├── notebooks/                   # SÓ exploração; o notebook do relatório apenas CHAMA funções testadas
├── docs/
│   ├── data_audit.md            # saída de T2
│   ├── contracts.md             # documentação humana dos schemas (gerada a partir de contracts/)
│   └── decisions/               # ADRs curtos para toda decisão que mudar contrato ou método
└── data/                        # .gitignore — SYMLINK → HD 1TB (veritas-wx-data/); espelha layout de object storage
    ├── raw/                     # cache mínimo com manifest (ver R1 — disco é restrição real)
    ├── staged/                  # obs canônicas, obs+qc, forecast_points
    ├── static/                  # stations, DEM recortado, köppen, índices
    ├── fact/                    # year=/month=/model=  (a fonte de verdade publicável)
    ├── views/                   # matched views materializadas por comparison_id
    └── results/                 # saídas de analyze (sempre com IC)
```

`web/` (Next.js) fica **fora** da Fase 1 — nem diretório reservado, para não convidar expansão de escopo.

Todo acesso a arquivo passa por `fsspec` com prefixo configurável — trocar `data/` local por
`s3://veritas-wx/` é mudança de config, não de código.

---

## 2. Contratos de dados por etapa

Convenções globais, válidas em todos os contratos:

- **Tempo**: tudo UTC, `TIMESTAMP` sem timezone implícita. `valid_time = init_time + lead_hours`.
- **Unidades canônicas** (convertidas na ingestão, com teste de caso conhecido):
  - `t2m` → **K** | `wind10m` → **m/s** (escalar, magnitude) | `precip_24h` → **mm** (janela `[valid_time−24h, valid_time]`)
  - `dewpoint` está no enum do contrato, **reservado, fora da Fase 1** (escopo fixa 3 variáveis).
- **`ingest_version`**: `"{semver}+{git_sha7}.{hash8(configs/ingest.yaml)}"` — propagado até a fact
  table; reprocessar com código ou parâmetros diferentes gera versão nova, nunca sobrescreve silenciosamente.
- **Perda de dados**: nenhuma etapa descarta linha sem registrar `dropped{motivo: contagem}` no log
  estruturado. Teste de reconciliação: `rows_in == rows_out + sum(dropped)` em toda etapa.

### 2.1 `ingest/forecasts` — download bruto

| | |
|---|---|
| **Entrada** | Config: janela, modelos, runs `{00Z, 12Z}`, leads `6..240h` passo `6h` |
| **Fontes** | GFS: `s3://noaa-gfs-bdp-pds` (GRIB2 0.25° + `.idx`) · HRES e AIFS: `s3://ecmwf-forecasts` (GRIB2 0.25° + `.index`) · GraphCast operacional: `s3://noaa-oar-mlwp-data` (netCDF, CIRA/NOAA, stream inicializado por GFS) |
| **Saída** | Campos necessários (`2t/tmp2m`, `10u`, `10v`, `tp/apcp`, orografia 1×) via **byte-range pelos índices** — nunca o arquivo global inteiro quando houver índice. `data/raw/{model}/{yyyymmdd}T{HH}/` + `manifest.parquet` |
| **Manifest** | `{url, local_path, sha256, bytes, source, model, init_time, downloaded_at, status}` |
| **Idempotência** | Artefato presente com sha256 confirmado → skip. Mismatch → re-download e log de alerta. Os buckets públicos são o arquivo permanente; o cache local pode ser podado após extração (`--prune-raw`) mantendo o manifest — reprodutibilidade vem de `(url, sha256, ingest_version)`, não de guardar bytes localmente. |

### 2.2 `match/extract` — grade → ponto de estação

| | |
|---|---|
| **Entrada** | `raw/` + `static/stations.parquet` + `static/grid_cells_{model}.parquet` |
| **Saída** | `staged/forecast_points/year=/month=/model=` |

Schema `FORECAST_POINTS_V1`:

```
station_id     VARCHAR     model         VARCHAR     variable    VARCHAR
init_time      TIMESTAMP   valid_time    TIMESTAMP   lead_hours  SMALLINT
value          DOUBLE      -- unidade canônica; precip já como acumulação 24h
interp_method  VARCHAR     -- bilinear (t2m, wind10m) | nearest (precip_24h)
grid_lat       DOUBLE      grid_lon      DOUBLE      grid_elev   DOUBLE
ingest_version VARCHAR
```

Semânticas fixadas aqui (cada uma com teste golden calculado à mão):

- **wind10m**: magnitude calculada **nos nós da grade** (`sqrt(u²+v²)`), depois interpolada.
  Interpolar componentes e tirar magnitude depois enviesa para baixo perto de cisalhamento de direção.
- **precip_24h**: diferença de acumulações na convenção nativa de cada modelo, documentada no módulo:
  AIFS/HRES `tp` acumulado desde init (m → mm); GFS `APCP` em buckets de 6 h (somar 4 buckets);
  GraphCast totais de 6 h (somar 4). `tp` negativo (artefato conhecido de modelos de IA) é **preservado
  como está** em `fcst_raw` e contado no log — nunca clipado silenciosamente; o tratamento é decisão
  do registro de métricas, não da ingestão.
- **`grid_elev`**: orografia do próprio modelo (GFS `orog`; ECMWF `z/g`; GraphCast herda orografia
  do GFS por ser inicializado nele — confirmar na auditoria T2). Fonte registrada em
  `static/grid_cells_{model}.parquet`, com fallback para média do DEM na célula, sinalizada.

### 2.3 `ingest/observations` — obs canônicas

| | |
|---|---|
| **Entrada** | INMET apitempo (horário, estações automáticas; fallback BDMEP) · ISD-Lite (NCEI) |
| **Saída** | `staged/obs/year=/month=` |

Schema `OBS_V1`:

```
station_id      VARCHAR    -- canônico: "inmet:A001", "isd:829830-99999"
valid_time      TIMESTAMP
variable        VARCHAR
value           DOUBLE     -- unidade canônica
source          VARCHAR    -- inmet | isd
source_qc_raw   VARCHAR    -- flag original da fonte, preservada verbatim
ingest_version  VARCHAR
```

Mapeamentos (cada conversão com teste golden): INMET `TEM_INS` °C→K, `VEN_VEL` m/s, `CHUVA` mm/h;
ISD-Lite inteiros escalonados (°C×10 etc., `-9999` = ausente). Precipitação vem **prioritariamente do
INMET** (pluviômetro horário); ISD entra sobretudo por t2m/vento em aeroportos.

**`precip_24h` observada**: soma de 24 valores horários **não flagados**; exige ≥ 22/24 presentes,
senão o par simplesmente não é emitido (contado em `dropped{precip_incompleta}`). Hora ausente
**nunca** vira zero.

### 2.4 `qc/` — bitmask, nunca deleção

| | |
|---|---|
| **Entrada** | `staged/obs/` + `static/stations.parquet` + `configs/qc_params.yaml` |
| **Saída** | `staged/obs_qc/` = `OBS_V1` + `qc_flags INTEGER` |

Bits (contrato `qc_bits.py`, valores congelados):

```
RANGE=1  STEP=2  PERSISTENCE=4  SPATIAL=8  METADATA=16  DUPLICATE=32   (bits 6–15 reservados)
```

- `RANGE`: limites físicos por variável × mês × classe Köppen (tabela em `qc_params.yaml`).
- `STEP`: |Δ| entre leituras consecutivas acima do plausível (por variável).
- `PERSISTENCE`: valor idêntico por N horas consecutivas (N por variável; vento calmo tratado à parte).
- `SPATIAL`: desvio vs. mediana das k vizinhas dentro de raio R, normalizado por MAD.
- `METADATA`: |elev_metadado − elev_DEM| > limiar, coordenada fora do país/imprecisa.
- `DUPLICATE`: mesma leitura física via duas redes (estação INMET também presente no ISD).

Cada check: função pura, testável isolada, um bit. `qc_flags == 0` ⇒ limpa. O consumidor escolhe o
rigor via máscara — o dado suspeito **permanece no dataset**.

### 2.5 `match/fact` — a fact table (contrato FACT_V1, verbatim do brief)

| | |
|---|---|
| **Entrada** | `staged/forecast_points/` + `staged/obs_qc/` + `static/` |
| **Saída** | `data/fact/year=/month=/model=`, arquivos ordenados por `(station_id, valid_time)` |

```
station_id        VARCHAR    -- identificador canônico
model             VARCHAR
variable          VARCHAR    -- t2m | wind10m | precip_24h | dewpoint
init_time         TIMESTAMP  -- UTC
valid_time        TIMESTAMP  -- UTC
lead_hours        SMALLINT
fcst_raw          DOUBLE
fcst_elev_adj     DOUBLE     -- NULL se não aplicável (só t2m na Fase 1)
obs               DOUBLE
delta_z           DOUBLE     -- elev_estacao - elev_celula
interp_method     VARCHAR    -- nearest | bilinear
repr_floor        DOUBLE     -- NULL se não estimável
qc_flags          INTEGER    -- bitmask
ingest_version    VARCHAR
```

Schema **congelado como v1**: evolução só aditiva; quebra exige v2 + ADR. `schema_version` gravado
no metadata key-value do Parquet.

Semânticas:

- **Correção de elevação (t2m)**: `fcst_elev_adj = fcst_raw − 0.0065 · delta_z` (Γ = 6.5 K/km,
  `delta_z` em m). Exemplo golden: estação 800 m, célula 1200 m ⇒ `delta_z = −400` ⇒ ajuste **+2.6 K**
  (estação mais baixa → mais quente). `|delta_z| > 500 m` ⇒ par descartado por default
  (`configs/ingest.yaml: max_delta_z`), contado em `dropped{delta_z_excedido}`.
- **`repr_floor`**: para cada (célula 0.25°, variável) com **≥ 2 estações**: em cada `valid_time`
  com ≥ 2 obs limpas, variância amostral entre estações; piso = **mediana temporal** dessas variâncias
  (unidade: valor²). Célula com < 2 estações ⇒ `NULL`, ponto final — nada de imputação. Honestidade
  no nome: o piso inclui erro instrumental + variabilidade subgrade real; ambos "não são erro do
  modelo", então a decomposição permanece válida.
- **`qc_flags` do par**: flag da hora da obs (t2m, wind10m); **OR bit a bit** das 24 horas
  contribuintes (precip_24h).
- `obs_source` é derivável de `station_id` (prefixo de rede) — sem coluna extra.

### 2.6 `match/views` — pares casados exatos

`views/matched_{comparison_id}/` onde `comparison_id = join("+", sorted(models))`, ex.
`aifs+gfs+graphcast+hres`. **Inner join** sobre `(init_time, valid_time, station_id, variable)`
exigindo dado válido nos **quatro** modelos e obs passando a máscara de QC do run. Materializada em
Parquet com manifest `{comparison_id, models, qc_mask, n_pares, build_id}` + view DuckDB versionada
em `serve/sql/`. Nenhuma comparação entre modelos lê a fact table diretamente — só views casadas.

### 2.7 `analyze/` — resultados (contrato RESULTS_V1)

Funções puras: recebem DataFrame (view casada), devolvem DataFrame tidy. **Não existe função pública
que retorne média sem IC** — a assinatura de toda agregação retorna `(estimate, ci_low, ci_high, ...)`.

```
metric          VARCHAR    comparison_id  VARCHAR    model      VARCHAR
variable        VARCHAR    lead_hours     SMALLINT
stratum_type    VARCHAR    -- global | season | koppen | enso | mjo_phase | obs_percentile_bin
stratum_value   VARCHAR
estimate        DOUBLE     ci_low   DOUBLE   ci_high  DOUBLE   alpha  DOUBLE
n_pairs         BIGINT     n_stations INT    n_days   INT
block_len_days  SMALLINT   n_boot   INT
p_raw           DOUBLE     p_adj    DOUBLE   -- NULL fora de teste de hipótese
family_id       VARCHAR    n_family INT      -- nº de testes na família BH, gravado junto
registry_version VARCHAR   ingest_version VARCHAR
```

- **Bootstrap em blocos** (`bootstrap.py`): unidade de reamostragem = **blocos contíguos de dias de
  `init_time`** — todas as estações e os dois runs (00Z/12Z) daqueles dias entram juntos, preservando
  correlação **temporal e espacial** (reamostrar estações independentemente subestimaria o IC).
  Tamanho de bloco: Politis–White automático sobre a série diária do erro médio no domínio, por
  (modelo, variável, lead), com clamp `[2, 30]` dias. `n_boot = 1000`, IC percentil, α = 0.05.
- **Decomposição (§ não-negociável 1)**: emitida como três linhas de métrica com `decomp_group`
  comum: `mse_total`, `repr_floor_mean` (média do piso sobre os pares contribuintes) e
  `mse_model_est = max(mse_total − repr_floor_mean, 0)` — com flag registrada quando o clip em zero
  atua. Só computada onde `repr_floor` existe; senão `NULL` propagado.
- **FDR**: BH com q = 0.05 sobre a família **pré-registrada** do run (produto
  métricas × pares-de-modelos × variáveis × leads × estratos efetivamente testados). `family_id` =
  hash da seção do registro + build; `n_family` gravado em toda linha.
- **Testes entre modelos**: diferença pareada de score (mesmos pares por construção da view casada),
  p-valor pelo mesmo bootstrap em blocos.
- **Métricas assinatura**:
  - `variance_ratio` = σ(fcst)/σ(obs) sobre os pares reamostrados, por estação, agregado como
    mediana entre estações. Hipótese pré-registrada: razão < 1 e decrescente com lead nos modelos de IA.
  - `bias_by_percentile`: percentil empírico da obs dentro de (estação, variável) na janela completa;
    bins `[0,10) ... [90,99), [99,100]`; viés médio por bin com IC.
  - `regime_stratified_skill`: mesmas métricas × estratos de `strata.py` (estação do ano, Köppen
    nível 1, fase ENSO via ONI ±0.5, fase MJO RMM 1–8 com amplitude ≥ 1).
- **CRPS/Brier/reliability**: entram no registro com `requires: ensemble` e ficam **não computadas**
  na Fase 1 (os 4 sistemas são determinísticos na forma pública que usamos) — registradas agora para
  não haver liberdade de escolha depois.

### 2.8 `metrics_registry.yaml` — pré-registro

Commitado **antes** do primeiro run de análise (o histórico git é a prova de pré-registro — por isso
`git init` acontece na Tarefa 1). Contém: versão, defaults de incerteza, definição formal de cada
métrica, estratos aplicáveis, e a definição das **famílias de teste** para BH. Qualquer métrica nova
depois do primeiro run entra marcada `post_hoc: true` e em família separada.

### 2.9 `serve/` — leitura

FastAPI read-only sobre DuckDB: `/results` (filtros por métrica/modelo/variável/lead/estrato),
`/pairs` (fatias da fact table), `/meta` (registro, builds, estações). **Nenhum endpoint computa
estatística on-the-fly** — só serve `results/` pré-computados (com IC) e dados brutos. Último
milestone; não bloqueia o critério de pronto.

---

## 3. Ordem de implementação, com justificativa

| # | Milestone | Depende de | Por que nesta posição |
|---|---|---|---|
| M0 | Bootstrap do repo + `contracts/` compilando com testes | — | Contratos primeiro: são baratos de mudar agora e caríssimos depois; todo módulo seguinte programa contra eles. |
| M1 | **Auditoria de fontes** (`scripts/audit_sources.py` → `docs/data_audit.md`) | M0 | Mata o maior risco do projeto (R1/R2) antes de construir contra dados-fantasma. Fixa a janela de 12 meses com evidência, não esperança. |
| M2 | Static: curadoria de estações v0 + DEM + Köppen + índices | M0 | Caminho crítico de tudo (o join é por `station_id`) e é o trabalho com maior latência humana (curadoria manual) — começa cedo para amadurecer em paralelo. |
| M3 | Ingestão de observações (INMET → ISD) | M2 | Obs vêm antes de forecasts: o QC precisa de dados reais para calibrar limiares, e a curadoria v1 (por completude) depende de obs ingeridas. |
| M4 | QC completo + curadoria v1 (≈500 finais, por completude ≥80% na janela) | M3 | Funções puras com property tests; fecha a lista de estações antes de qualquer extração de forecast em volume. |
| M5 | **Thin slice vertical**: GFS × t2m × ~20 estações × 1 mês → extract → mini-fact → reconciliação limpa | M2, M4 | Valida a cadeia inteira (download por byte-range, interpolação, correção de elevação, join, contagens) com volume mínimo antes de escalar. GFS primeiro: arquivo mais estável e formato mais conhecido. |
| M6 | `analyze/` core (métricas + bootstrap + FDR + decomposição) | M0 apenas | **Paralelizável com M3–M5** justamente porque é puro: desenvolvido e testado contra dados sintéticos (AR(1) com parâmetros conhecidos, pares N(μ,σ) fabricados) sem esperar dado real. |
| M7 | Escala: 4 modelos × 3 variáveis × 12 meses × ~500 estações → `build_all.py` → fact v1 + matched views | M5, M6 | Só depois do thin slice limpo e do analyze testado. Aqui entra o grosso do volume (e a estratégia de disco de R1). |
| M8 | `make_figures.py` + notebook do relatório + publicação do dataset (dry-run) | M7 | As 3 figuras do critério de pronto, geradas por código testado; o notebook só chama e exibe. |
| M9 | `serve/` (FastAPI) | M7 | Fora do critério de pronto; fecha a Fase 1 mas não a bloqueia. |

Princípios por trás da ordem: (a) contrato antes de implementação; (b) risco maior verificado
primeiro; (c) latência humana (curadoria) disparada cedo; (d) pureza de `analyze/` explorada para
paralelismo; (e) fatia vertical fina antes de qualquer escala — anti-padrão de abstração antecipada
respeitado: zero camada de plugin, quatro módulos de ingestão concretos.

---

## 4. Riscos técnicos e como cada um será verificado

| # | Risco | Impacto | Verificação / mitigação |
|---|---|---|---|
| **R1** | ~~NVMe com só ~8 GB livres~~ **RESOLVIDO 2026-07-23**: data lake movido para HD interno de 1 TB (`/dev/sda3`, NTFS via driver kernel `ntfs3`, **767 GB livres**, 113 MB/s medidos em escrita direta). `weather-project/data` é symlink para `/media/uiliam-isaac-da-silva-mello/F4D45654D4561966/veritas-wx-data/`. Riscos residuais: (a) a partição **não monta sozinha no boot** — `build_all.py` faz preflight `ensure_mounted()` via `udisksctl` (entra em T1); (b) HD 5400 rpm ⇒ DuckDB de análise e `results/` quentes ficam no NVMe. | ~~Bloqueia M7~~ Mitigado | `df` + write-test do data root no preflight de todo build; projeção de volume continua em T2 (agora com teto folgado de 767 GB). |
| **R2** | Cobertura de arquivo dos 12 meses: AIFS operacional só desde 2025-02; retenção histórica do bucket `ecmwf-forecasts` a confirmar; stream exato do GraphCast no AIWP (GFS-init vs IFS-init), formato e tamanho dos netCDF a confirmar. | Redefine janela/escopo | `audit_sources.py` lista prefixos S3 e produz matriz fonte × mês × variável para a janela proposta (2025-07-01 → 2026-06-30). Gate de M1: zero células "desconhecido". Fallback: deslocar janela para a interseção disponível; nunca misturar fases experimentais/operacionais de um modelo sem flag. |
| **R3** | INMET apitempo: limites de taxa, buracos históricos, mudanças silenciosas de formato. | Atrasa M3 | Verificar com 1 estação-mês medindo latência/erros; fallback BDMEP (bulk CSV); raw espelhado com checksum para nunca depender duas vezes da API. |
| **R4** | Convenções de acumulação de precipitação divergem por modelo (buckets GFS, `tp` desde init no ECMWF, totais 6 h no GraphCast; `tp` negativo em modelos de IA). Erro aqui corrompe silenciosamente 1/3 do dataset. | Corrompe precip | Um teste golden **por modelo** com data conhecida, calculado à mão a partir do GRIB cru; validação cruzada de amostra contra o produto diário do INMET; contagem de `tp<0` logada por modelo. |
| **R5** | Unidades/convenções (K vs °C, m vs mm, escala ×10 do ISD-Lite, componentes vs magnitude do vento). | Corrompe tudo | Conversões centralizadas em `contracts/` com golden tests; `RANGE` do QC pega escapes grosseiros (ex.: t2m = 300 °C); validação de contrato roda em todo build. |
| **R6** | Metadados de estação errados (coordenada/elevação) — erro clássico que vira "erro do modelo". | Viés sistemático | Cross-check |elev_meta − elev_DEM| > 100 m ⇒ fila de revisão manual (curadoria); bit `METADATA`; coordenadas fora do polígono do Brasil rejeitadas na curadoria. |
| **R7** | `repr_floor` esparso: com ~500 estações em ~13.600 células de 0.25°, poucas células terão ≥ 2 estações. | Decomposição com pouca cobertura | Contar células elegíveis já na curadoria v0 (M2) e reportar % de pares com piso estimável; se < 5%, registrar limitação no dataset e propor extensão geoestatística como Fase 2 — **sem** imputação na Fase 1. |
| **R8** | Bootstrap em blocos mal calibrado ⇒ ICs errados ⇒ a tese central do projeto (incerteza honesta) cai. | Credibilidade | Property tests com resposta conhecida: cobertura empírica de IC 95% em AR(1) sintético (φ=0.5) dentro de [0.92, 0.97]; ℓ=1 reproduz bootstrap iid; seleção Politis–White validada contra implementação de referência (`arch`). |
| **R9** | Perda silenciosa de linhas em joins (o bug mais caro do domínio, nas palavras do brief). | Dataset inválido | `runlog` obrigatório em toda etapa + teste de reconciliação `rows_in == rows_out + Σ dropped` no CI; build aborta se a identidade falhar. |
| **R10** | Licenciamento/redistribuição: dataset público derivado de ECMWF Open Data (CC-BY-4.0, exige atribuição), NOAA (domínio público), INMET (dado público gov.br). | Bloqueia publicação | Checklist de licença por fonte em `docs/data_audit.md` (parte de T2); strings de atribuição embutidas no metadata do Parquet publicado. |

---

## 5. As três primeiras tarefas concretas

### T1 — Bootstrap do repositório
`git init` (o pré-registro do `metrics_registry.yaml` depende de histórico git provável) · `uv init`
Python 3.12 · deps: `polars duckdb xarray cfgrib zarr scoringrules fastapi httpx fsspec s3fs` + dev
`pytest hypothesis ruff pre-commit` · layout `src/` da §1 · `contracts/schemas.py` com FACT_V1/OBS_V1/
FORECAST_POINTS_V1 + validadores · `runlog.py` · CI (GitHub Actions: ruff + pytest) · primeiro teste
golden: °C→K e o exemplo de correção de elevação (800 m/1200 m ⇒ +2.6 K).
**Aceite**: `uv run pytest` verde; validador rejeita DataFrame com coluna faltante com mensagem clara.

### T2 — Auditoria de disponibilidade de dados (`scripts/audit_sources.py` → `docs/data_audit.md`)
Sondar sem baixar volume: listar prefixos S3 de GFS, `ecmwf-forecasts` (HRES + AIFS) e AIWP/GraphCast
para a janela proposta 2025-07→2026-06 (cobertura por mês, runs, leads, variáveis, **bytes por run**);
1 estação-mês do INMET apitempo; inventário ISD-Lite para o Brasil; termos de licença por fonte.
**Aceite**: matriz fonte × mês sem células "desconhecido"; janela de 12 meses fixada com evidência;
projeção de disco/tráfego para M7 (alimenta a decisão R1); GO/NO-GO por fonte.

### T3 — Curadoria de estações v0 (`static/stations.parquet`)
Candidatas: INMET automáticas (~600) + ISD Brasil; dedupe entre redes (distância + nome + elevação);
cross-check de elevação contra Copernicus GLO-30; atribuição Köppen; contagem de células 0.25° com
≥ 2 estações (verificação antecipada de R7); relatório de curadoria com contagem de
incluídas/excluídas **por motivo**.
**Aceite**: parquet valida contra `STATIONS_V1`; nenhuma exclusão sem motivo registrado; lista de
revisão manual gerada para os casos |Δelev| > 100 m.

*(T4, para contexto: ingestão INMET da janela completa + QC — mas T1–T3 são o compromisso.)*

---

## 6. Decisões embutidas neste plano — aprovar ou ajustar

| # | Decisão | Default proposto |
|---|---|---|
| D1 | Janela de 12 meses | **2025-07-01 → 2026-06-30** (AIFS operacional em toda a janela), sujeita à confirmação de T2 |
| D2 | Runs e leads | **00Z e 12Z**; leads **6–240 h passo 6 h** (precip_24h: 24–240 h) — denominador comum dos 4 sistemas |
| D3 | Unidades canônicas | SI: K, m/s, mm/24h |
| D4 | Interpolação | bilinear (t2m, wind10m), nearest (precip_24h); coluna `interp_method` preserva a escolha por linha |
| D5 | Bootstrap | blocos de **dias de init**, estações juntas (preserva correlação espacial); Politis–White, clamp [2,30]; n_boot=1000 |
| D6 | GraphCast | stream **GFS-init** do arquivo AIWP (CIRA/NOAA) — o operacional em tempo real |
| D7 | FDR | Benjamini-Hochberg, q = 0.05, famílias definidas no registro |
| D8 | **Disco (R1)** | **RESOLVIDO 2026-07-23** (escolha do usuário): HD interno 1 TB NTFS usado como está, sem reparticionar. Data lake no HD via symlink `data/`; arquivos quentes no NVMe; preflight de montagem nos scripts |
| D9 | Licenças | código: MIT ou Apache-2.0 (**escolher**); dataset: CC-BY-4.0 (herda exigência do ECMWF) |
| D10 | Idioma | código + identificadores em inglês; docs do dataset público em inglês; docs internos pt-BR (**confirmar**) |
| D11 | Publicação | Zenodo (DOI, versionado) + espelho Hugging Face Datasets (**confirmar venue**) |

---

## 7. Critério de pronto da Fase 1 (mapeamento explícito)

1. **Um comando**: `uv run scripts/build_all.py --window 2025-07-01:2026-06-30` reconstrói a fact
   table do zero (auditável por `ingest_version` + manifests).
2. **Três figuras defensáveis**, geradas por `make_figures.py` (testado) e exibidas no notebook:
   - `variance_ratio` × lead, IC por bootstrap em blocos, 4 modelos;
   - viés por percentil de precipitação observada;
   - tabela de skill estratificada por regime climático (Köppen × estação do ano, com IC e BH).
3. **Dataset público**: `fact/` + `static/stations` + `metrics_registry.yaml` + documentação de
   contratos publicados com DOI e atribuições.

## 8. Guardas de escopo (o que este plano se recusa a fazer na Fase 1)

Sem frontend. Sem 5º modelo. Sem estação além das ~500 curadas. Sem camada de plugin. Sem Spark/K8s/
feature store. Sem Prefect até existir segundo job recorrente (o build histórico é CLI sequencial).
Sem métrica fora do registro. Sem média sem IC. Sem imputação silenciosa — `NULL` é resposta válida.

---

*Quinhentas estações limpas valem mais que dez mil sujas. O mundo começa pelo Brasil.*
