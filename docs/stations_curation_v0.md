# Curadoria de estações v0 — T3

Gerado por `scripts/build_stations.py` em 2026-07-23.
`ingest_version = 0.1.0+7c72ffa.4fd2f582` · contrato `STATIONS_V1` ·
saída `data/static/stations_v0.parquet` (1118 linhas).

## Regras de curadoria v0

1. **Bounding box do Brasil** (lat −34..6, lon −74..−32): coordenada fora ⇒ `excluded`,
   motivo `coords_out_of_brazil` (coordenada não parseável ⇒ `invalid_coords`). Nota: a caixa
   exclui territórios oceânicos distantes (ex.: Arquipélago de São Pedro e São Paulo, lon ≈ −29,3).
2. **INMET inativa**: `CD_SITUACAO` indicando desativação (`desativ*`, `encerr*`, `extint*`,
   `fechad*`) **ou** `DT_FIM_OPERACAO` anterior ao início da janela (2025-07-01) ⇒ `excluded`,
   motivo `inactive`. `Pane` **não** exclui: a estação pode ter dados na janela; o corte por
   completude é da curadoria v1 (M4).
3. **Dedupe entre redes**: par INMET×ISD a menos de **2 km** ⇒ mesma localidade física.
   A INMET permanece `included` (pluviômetro horário é a fonte primária de precipitação);
   a gêmea ISD vira `excluded` com motivo `duplicate_of:<station_id>`; `cross_ref`
   preenchido nos **dois** registros.
4. **Divergência de elevação** (risco R6): |`elev_station` − `elev_dem`| > 100 m
   (config `qc_params.yaml: metadata.max_elev_diff_m`) ⇒ `status="review"` — fila de revisão
   manual, **não** excluída. O campo `exclusion_reason` documenta o motivo da revisão.

Nenhuma exclusão sem motivo registrado; nenhuma linha descartada silenciosamente
(o runlog levanta exceção se `rows_in != rows_out + Σ dropped`).

## Contagens por rede

| Rede | Candidatas | included | review | excluded |
|---|---|---|---|---|
| INMET (automáticas, apitempo) | 673 | 666 | 6 | 1 |
| ISD (CTRY=BR, END ≥ 20250701) | 445 | 0 | 0 | 445 |
| **Total** | **1118** | **666** | **6** | **446** |

Filtro do inventário ISD mundial (antes do canônico, contado no runlog —
29661 linhas no isd-history):

| Motivo do descarte | Linhas |
|---|---|
| not_country | 28719 |
| end_before_min | 497 |

## Excluídas e em revisão, por motivo

| Rede | Motivo | Estações |
|---|---|---|
| inmet | inactive | 1 |
| isd | coords_out_of_brazil | 1 |
| isd | duplicate_of:* | 262 |
| isd | isd_archive_frozen | 182 |

| Rede | Motivo (review) | Estações |
|---|---|---|
| inmet | elev_diff_gt_100m | 6 |

## Células 0.25° com ≥ 2 estações incluídas (insumo do R7)

- **15 células** com ≥ 2 estações `included`
  (0.11% das ~13,600 células de 0.25°
  do Brasil — referência do PLAN, risco R7).
- Consequência: `repr_floor` só é estimável nessas células; nas demais fica `NULL`
  (sem imputação na Fase 1). Se a fração de pares com piso estimável ficar < 5% na M7,
  registrar a limitação no dataset (extensão geoestatística é Fase 2).

## Cobertura DEM e Köppen (estações não excluídas: 672)

- **DEM (Copernicus GLO-30, leitura COG por janela, 1 pixel/estação)**:
  `elev_dem` preenchido para 672/672
  (100.0%) — 423 tiles abertos,
  0 falha(s), transporte {'s3': 423}.
- **Köppen (Beck et al. 2023, 1991–2020, 1 km)**: classe atribuída para
  672/672 (100.0%);
  2 estação(ões) resolvidas pelo fallback de
  vizinhança 3×3 (pixel costeiro oceânico). Raster em `data/static/raw/koppen_geiger_1991_2020_0p00833333.tif`
  (sha256 `2130f0071dfb2904947d8ec3a0d807fac71004df76e769262004f1602e4d6a13`), fonte figshare/GloH2O
  `cache local (data/static/raw)`.

### Distribuição Köppen das incluídas

| Classe | Estações |
|---|---|
| Aw | 352 |
| Cfa | 128 |
| Am | 57 |
| Cfb | 38 |
| Af | 37 |
| BSh | 34 |
| Cwa | 13 |
| Cwb | 6 |
| BWh | 1 |

## Fila de revisão manual — |Δelev| > 100 m (6 estações)

Estações com metadado de elevação divergente do DEM; permanecem `review` até
confirmação humana (coordenada errada? elevação errada? torre em encosta?).

| station_id | Nome | UF | lat | lon | elev_station (m) | elev_dem (m) | Δ (m) |
|---|---|---|---|---|---|---|---|
| inmet:A911 | SAPEZAL | MT | -13.3039 | -58.7633 | 105.0 | 547.8 | -442.8 |
| inmet:B835 | JOIA | RS | -28.6503 | -54.1128 | 0.0 | 349.5 | -349.5 |
| inmet:B821 | SANTANA DA BOA VISTA | RS | -30.8586 | -53.1556 | 138.0 | 337.1 | -199.1 |
| inmet:B837 | PORTO XAVIER | RS | -27.9028 | -55.1672 | 0.0 | 110.4 | -110.4 |
| inmet:A949 | DIAMANTINO | MT | -14.3778 | -56.3797 | 317.0 | 425.5 | -108.5 |
| inmet:B834 | GARRUCHOS | RS | -28.1969 | -55.6297 | 0.0 | 103.3 | -103.3 |

## Pendências

- Nenhuma: DEM e Köppen resolvidos para todas as estações candidatas.

## Critérios da curadoria v1 (após M3)

- **Completude ≥ 80%** das horas na janela 2025-07-01..(fim da janela) por estação/variável —
  só computável depois da ingestão de observações (M3); meta ~500 estações finais.
- Resolução da fila de revisão (aceitar/corrigir/excluir cada |Δelev| > 100 m).
- Reavaliar estações `Pane` com dados suficientes na janela.
