# Auditoria de disponibilidade de dados (T2)

> Gerado por `scripts/audit_sources.py` em 2026-07-23 04:19 (UTC). Janela auditada: **2025-07-01 → 2026-06-30** (runs 00Z, 12Z; leads 6–240 h passo 6 h).

## Sumário executivo

| Fonte | Veredicto | Justificativa em uma linha |
|---|---|---|
| GFS (`noaa-gfs-bdp-pds`) | **GO** | Janela completa, `.idx` presente em 100% das amostras; ~112.9 MB/run nos 4 campos. |
| ECMWF HRES (`ecmwf-forecasts`) | **GO** | Bucket retém desde 2023-01-18; janela completa; `.index` presente; ~132.0 MB/run. |
| ECMWF AIFS (`aifs-single`) | **GO** | Operacional desde 2025-02-25; janela completa; ~112.3 MB/run. |
| GraphCast (`GRAP_v100_GFS`) | **GO com ressalva** | 50 runs ausentes na janela (2026-04, 2026-05, 2026-06); arquivo 5.8 GB/run exige leitura seletiva (sem backend HDF5 no venv hoje). |
| INMET | **GO (via bulk), API degradada** | apitempo horário devolve 2xx sem corpo (204/vazio); zips anuais `dadoshistoricos` cobrem a janela inteira. |
| ISD/NCEI | **NO-GO para a janela completa** | Arquivo público congelado: última obs BR 2025-08-24 21:00; sem arquivos 2026. Cobre só 2025-07→08. |
| Estáticos (DEM, Köppen, ONI) | **GO** | Todos alcançáveis; URLs fixadas abaixo. |
| MJO RMM (BoM) | **NO-GO na fonte primária** | Arquivo do BoM congelado em 2024-02-24; fallback documentado (IRI/PSL). |

**Recomendação de janela: manter 2025-07-01 → 2026-06-30** (evidência na §8).

## 1. Matriz de cobertura fonte × mês

Legenda: ✓ = mês completo · parcial = lacunas identificadas (abaixo) · ✗ = ausente.

| Fonte | 2025-07 | 2025-08 | 2025-09 | 2025-10 | 2025-11 | 2025-12 | 2026-01 | 2026-02 | 2026-03 | 2026-04 | 2026-05 | 2026-06 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| GFS | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ECMWF HRES | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ECMWF AIFS | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| GraphCast | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | parcial | parcial | parcial |
| INMET (bulk) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ISD/NCEI | ✓ | parcial | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |

Critério das células (zero "desconhecido"): modelos GRIB — todos os dias do mês presentes no nível de prefixo de data **e** amostras profundas (dias 1 e 15, 00Z/12Z, 40 leads + índice) 100% completas; GraphCast — inventário file-a-file de TODOS os dias do mês (00Z e 12Z); INMET — mês contido nos zips anuais verificados por HEAD (tamanho + Last-Modified posterior ao fim do mês); ISD — última observação real no arquivo-amostra.

Runs GraphCast ausentes na janela (50 de 730): 20260411-00Z, 20260411-12Z, 20260414-00Z, 20260416-00Z, 20260420-00Z, 20260423-12Z, 20260425-00Z, 20260427-00Z, 20260428-12Z, 20260430-00Z, 20260430-12Z, 20260501-00Z, 20260505-12Z, 20260506-00Z, 20260506-12Z, 20260507-00Z, 20260509-12Z, 20260510-00Z, 20260510-12Z, 20260511-00Z, 20260514-00Z, 20260514-12Z, 20260515-00Z, 20260523-00Z, 20260523-12Z, 20260524-12Z, 20260525-00Z, 20260529-00Z, 20260530-00Z, 20260602-00Z, 20260602-12Z, 20260604-00Z, 20260610-00Z, 20260612-12Z, 20260613-00Z, 20260614-00Z, 20260615-00Z, 20260615-12Z, 20260616-00Z, 20260616-12Z, 20260617-00Z, 20260618-12Z, 20260621-00Z, 20260622-00Z, 20260623-00Z, 20260624-00Z, 20260625-00Z, 20260627-12Z, 20260628-00Z, 20260628-12Z.

Amostras GRIB incompletas: nenhuma.

## 2. Data mais antiga disponível por fonte

| Fonte | Mais antigo | Observação |
|---|---|---|
| GFS | 2021-01-01 | prefixo `gfs.YYYYMMDD` mais antigo do bucket; mais recente: 2026-07-23. |
| ECMWF (bucket) | 2023-01-18 | retenção histórica confirmada — não é bucket rolling; lacunas desde então: 20230427, 20230428, 20230429, 20230430, 20230501, 20230502 (todas fora da janela). |
| ECMWF AIFS `aifs-single` | 2025-02-25 | verificado: 2025-02-24 sem `aifs-single`, 2025-02-25 com (20250225); antes disso o caminho era `aifs/` (fase experimental — nunca misturar, R2). |
| GraphCast GFS-init | 2021-12-31 | README do bucket: regenerado, confiável desde 2022-01; 00Z/12Z na janela. |
| INMET (bulk) | ≤ 2025 (não sondado além da janela) | zips anuais por estação automática; cobertura da janela comprovada pelos zips 2025/2026. |
| ISD/NCEI | histórico longo (BEGIN típico anos 2000) | **fim** em 2025-08-24 21:00 — ver §5. |

## 3. Bytes por run e projeção de disco para M7

Campos: 2t/TMP2m, 10u/UGRD10, 10v/VGRD10, tp/APCP, somados sobre 40 leads (6..240 h). Run representativo: 20250701 00Z.

| Modelo | Bytes/run (medido) | Detalhe por campo (MB) |
|---|---|---|
| GFS | 112.9 MB | apcp_6h=15.0, t2m=20.7, u10=39.1, v10=38.2; orografia 1×=0.5 MB |
| HRES | 132.0 MB | 10u=33.7, 10v=33.6, 2t=25.2, tp=39.5 |
| AIFS | 112.3 MB | 10u=27.4, 10v=28.3, 2t=21.5, tp=35.1 |
| GraphCast | 277.8 MB (estimado pro-rata) | arquivo inteiro 5.8 GB; 4 de 83 campos 2D; teto não-comprimido 681.1 MB |

Projeção M7 — 4 modelos × 12 meses × 2 runs/dia = 730 runs por modelo:

| Item | Total na janela |
|---|---|
| GFS + HRES + AIFS (byte-range GRIB) | **260.8 GB** |
| GraphCast, leitura seletiva (pro-rata) | **202.8 GB** |
| GraphCast, teto não-comprimido dos 4 campos | 497.2 GB |
| GraphCast, arquivos INTEIROS (inviável) | 4,208.4 GB |
| **Total tráfego (cenário seletivo)** | **≈ 463.6 GB** |

Contra o teto de disco de R1 (767 GB livres no HD): o cenário seletivo cabe com folga mesmo sem poda; com `prune_raw: true` (ingest.yaml) o residente em disco é ainda menor — raw é transitório, ficam `staged/` + `fact/` (ordens de MB–poucos GB). Baixar GraphCast inteiro NÃO cabe (4,2 TB) — leitura seletiva é obrigatória, não otimização.

## 4. INMET — latências e estado da API

- `/estacoes/T`: status 200, 262,602 bytes, latência 1.48 s; estações por situação: Operante=477, Pane=196.
- `/estacao/2025-08-01/2025-08-31/A001` (1 estação-mês horário), 3 tentativas com backoff:
  - tentativa 1: status 204, 0 bytes, latência 0.08 s
  - tentativa 2: status 204, 0 bytes, latência 0.41 s
  - tentativa 3: status 204, 0 bytes, latência 0.13 s
- **Diagnóstico: API degradada** — responde 2xx rápido porém SEM corpo (204/vazio) para qualquer consulta de dados (testado também com outras estações e datas de anos anteriores). Não é NO-GO: os metadados funcionam e o caminho bulk cobre a janela.
- Fallback bulk `dadoshistoricos/2025.zip`: status 200, 90,898,634 bytes, Last-Modified Fri, 20 Mar 2026 12:30:31 GMT.
- Fallback bulk `dadoshistoricos/2026.zip`: status 200, 47,253,864 bytes, Last-Modified Wed, 01 Jul 2026 17:31:07 GMT — publicado após 2026-06-30, logo contém a janela até junho.
- BDMEP (`https://bdmep.inmet.gov.br/`): status 200 (alcançável; exportação interativa/por e-mail — uso manual apenas).

## 5. ISD/NCEI — inventário Brasil e congelamento do arquivo

- `isd-history.csv`: Last-Modified **Sat, 30 Aug 2025 00:02:02 GMT**; END máximo global 20250828 — o inventário parou de ser atualizado.
- Estações CTRY==BR: 942 no total; **445** com END ≥ 2025-07-01 (todas com END ≤ 20250824).
- Amostra 833780-99999 (Brasilia): ISD-Lite 2025 status 200 (44,790 bytes — ano parcial); ISD-Lite 2026 status 404; global-hourly 2026 status 404.
- Última observação real no arquivo global-hourly 2025 da amostra: **2025-08-24 21:00**.
- Conclusão: a rede ISD cobre apenas **2025-07-01 → ~2025-08-24** da janela (~1,8 de 12 meses). NO-GO como fonte de observação da janela completa; ver §8.

## 6. Estáticos

- **Copernicus DEM GLO-30** (`s3://copernicus-dem-30m`): alcançável=True; tile de amostra `Copernicus_DSM_COG_10_S16_00_W048_00_DEM/Copernicus_DSM_COG_10_S16_00_W048_00_DEM.tif` (41.2 MB, COG).
- **Köppen Beck et al. 2023**: figshare artigo 21789074 — arquivo `koppen_geiger_tif.zip` (130.6 MB), GET com range status 206 (total confirmado 130.6 MB); **URL que funciona**: `https://ndownloader.figshare.com/files/61012822`; espelho da página: gloh2o.org/koppen (status 200).
- **ONI (CPC)**: OK; primeira linha DJF 1950 24.72 -1.53, última **AMJ 2026 28.71 0.98** — cobre a janela (estação MJJ-2026 sai ~ago/2026; lag de ~1 mês é inerente e não bloqueia o build).
- **MJO RMM (BoM)**: arquivo responde (status 200, 2,597,997 bytes) porém **congelado em 2024-02-24** — anterior à janela ⇒ inutilizável como fonte primária. Fallbacks sondados: espelho IRI do RMM (status 200) e PSL OMI (status 200, Last-Modified Mon, 29 Jun 2026 17:33:58 GMT).

## 7. Licenças por fonte

| Fonte | Licença | Obrigações para o dataset publicado |
|---|---|---|
| ECMWF Open Data (HRES, AIFS) | **CC-BY-4.0** | Atribuição obrigatória ("Contains modified ECMWF data"); o dataset derivado herda CC-BY-4.0 (D9). |
| NOAA GFS, GraphCast/AIWP (CIRA/NOAA), ISD, ONI | **Domínio público** (obra do governo dos EUA) | Sem restrição; AIWP pede citação do paper Radford et al. 2025 (BAMS) — cortesia, incluir. |
| INMET | **Dado público** (gov.br; Lei de Acesso à Informação) | Citar INMET como fonte; sem restrição de redistribuição conhecida. |
| Copernicus DEM GLO-30 | Licença Copernicus (ESA) — uso e redistribuição livres | Nota de crédito "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved". |
| Köppen Beck et al. 2023 | **CC-BY-4.0** (figshare) | Citar Beck et al. 2023, Sci. Data 10, 724. |
| MJO RMM (BoM) | © BoM; uso com atribuição | Irrelevante enquanto congelado; espelho IRI mantém os termos do BoM. |

## 8. GO/NO-GO e recomendação de janela

| Fonte | GO/NO-GO | Justificativa |
|---|---|---|
| GFS | **GO** | 12/12 meses ✓; `.idx` universal; bytes/run medidos. |
| HRES | **GO** | 12/12 meses ✓; retenção desde 2023; `.index` universal. |
| AIFS | **GO** | 12/12 meses ✓; operacional (`aifs-single`) cobre a janela inteira com margem de 4 meses. |
| GraphCast | **GO com ressalva** | 9/12 meses ✓, 3 parciais (50 runs ausentes, ~6.8% da janela); runs ausentes viram simplesmente pares ausentes na view casada (inner join) — sem viés de seleção entre modelos além da redução de N. |
| INMET | **GO** (bulk) | API horária degradada (2xx sem corpo) porém zips anuais íntegros e atuais cobrem 100% da janela; BDMEP alcançável como segundo fallback. |
| ISD | **NO-GO para a janela completa** | Arquivo NCEI congelado ~2025-08 (inventário, ISD-Lite e global-hourly consistentes entre si). Cobre 2025-07→08 apenas. |
| DEM / Köppen / ONI | **GO** | Alcançáveis, URLs e tamanhos fixados. |
| MJO RMM | **NO-GO na fonte BoM** | Congelado pré-janela; estrato MJO fica pendente de ADR (espelho IRI do RMM ou troca para OMI/PSL — muda o registro, exige ADR + flag). |

### Recomendação final de janela

**Manter 2025-07-01 → 2026-06-30.** Evidência:

1. Os 4 sistemas de previsão cobrem a janela inteira (GraphCast com 50 runs ausentes, em 2026-04, 2026-05, 2026-06 — perda ~6.8% dos pares).
2. A espinha dorsal de observação (INMET, prioridade de precip por desenho — ingest.yaml) cobre 100% da janela via bulk zips verificados.
3. Deslocar a janela para trás para "salvar" o ISD é impossível sem quebrar o AIFS: `aifs-single` operacional só existe desde 2025-02-25, e o plano proíbe misturar fase experimental com operacional (R2). A maior janela comum viável INCLUINDO ISD seria **2025-07-01 → 2025-08-24** (< 2 meses) — insuficiente para 12 meses e para estratos sazonais.
4. Portanto: janela mantida SEM o ISD como fonte de janela completa. O ISD entra, no máximo, como enriquecimento opcional dos 2 primeiros meses (validação cruzada de t2m em aeroportos), nunca como rede do dataset principal — a curadoria T3 passa a mirar as ~477 estações automáticas INMET operantes (meta de ~500 estações segue atingível; impacto: menor densidade em aeroportos).

## 9. Pendências explícitas (para os próximos milestones)

- **GraphCast, inspeção interna do netCDF**: formato confirmado HDF5 por bytes-mágicos (✓), mas o venv NÃO tem backend HDF5 (h5py/h5netcdf/netCDF4 ausentes de pyproject, inclusive dos grupos opcionais). Antes de M5: adicionar `h5netcdf` (ou `netCDF4`) ao grupo `grib` e verificar lista de variáveis, chunking e custo real de leitura seletiva dos 4 campos (a estimativa pro-rata assume compressão uniforme entre variáveis). Variáveis esperadas conforme README do bucket (inclui precipitação acumulada de 6 h).
- **Orografia HRES**: disponível no `.index` de 0h (params sfc incluem z) — baixar 1× por modelo junto com o run representativo.
- **Orografia AIFS**: disponível no `.index` de 0h (params sfc incluem z) — baixar 1× por modelo junto com o run representativo.
- **MJO**: ADR para trocar a fonte (espelho IRI do RMM vs. OMI do PSL) — muda `metrics_registry` (estratos), portanto exige ADR antes de M6/M7.
- **INMET**: monitorar se a API volta (re-rodar este script); parsing dos zips anuais entra em M3 com o mesmo contrato OBS_V1.
- **ONI**: estação MJJ-2026 (necessária para estratificar jun/2026) publica ~ago/2026 — verificar no build de M7.

## Apêndice — execução

- Executado em 2026-07-23 04:19 UTC; duração 72 s; 301 requisições HTTP; 22.8 MB baixados (somente listagens, índices, CSVs, HEADs e ranges — nenhum GRIB/netCDF inteiro).
- Amostragem de profundidade: dias 1 e 15 de cada mês × runs 00Z/12Z (24 dias × 2 runs por fonte GRIB).
- Re-executável: `uv run python scripts/audit_sources.py` (ou `--quick` para fumaça).
