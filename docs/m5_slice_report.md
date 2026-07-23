# M5 thin slice — métricas com IC bootstrap (validação da cadeia)

Fact: `data/fact/fact_slice_gfs.parquet` · 123008 pares · 20 estações · seed 20260723

NÃO é resultado científico (1 mês, 20 estações): valida a cadeia
fetch→decode→extract→match→métrica+IC antes da escala M7.

## t2m

Pares limpos: 46838 · fallback sem correção de elevação: 0

| lead (h) | n | bias [IC95] | MAE [IC95] | RMSE [IC95] |
|---|---|---|---|---|
| 24 | 1172 | 1.226 [1.014, 1.459] | 1.749 [1.622, 1.889] | 2.238 [2.109, 2.381] |
| 72 | 1168 | 2.052 [1.913, 2.245] | 2.284 [2.151, 2.478] | 2.836 [2.691, 3.035] |
| 120 | 1166 | 2.304 [2.027, 2.611] | 2.534 [2.265, 2.827] | 3.144 [2.858, 3.437] |
| 240 | 1165 | 2.339 [1.860, 2.741] | 2.712 [2.371, 3.025] | 3.382 [2.943, 3.748] |

variance_ratio (leads ≤120h, mediana entre estações): 1.011 [0.977, 1.042]

## wind10m

Pares limpos: 46823

| lead (h) | n | bias [IC95] | MAE [IC95] | RMSE [IC95] |
|---|---|---|---|---|
| 24 | 1168 | 1.433 [1.276, 1.551] | 1.641 [1.526, 1.743] | 1.997 [1.858, 2.121] |
| 72 | 1168 | 1.662 [1.490, 1.793] | 1.827 [1.681, 1.955] | 2.207 [2.055, 2.333] |
| 120 | 1168 | 1.691 [1.479, 1.886] | 1.878 [1.739, 2.026] | 2.266 [2.102, 2.418] |
| 240 | 1167 | 1.663 [1.432, 1.894] | 1.953 [1.795, 2.111] | 2.409 [2.226, 2.590] |

variance_ratio (leads ≤120h, mediana entre estações): 1.123 [0.994, 1.230]

## precip_24h

Pares limpos: 29306

| lead (h) | n | bias [IC95] | MAE [IC95] | RMSE [IC95] |
|---|---|---|---|---|
| 24 | 787 | -0.031 [-0.088, 0.002] | 0.042 [0.005, 0.107] | 0.379 [0.071, 0.643] |
| 72 | 788 | -0.016 [-0.060, 0.011] | 0.047 [0.010, 0.104] | 0.313 [0.096, 0.512] |
| 120 | 789 | -0.016 [-0.097, 0.042] | 0.079 [0.023, 0.162] | 0.516 [0.157, 0.828] |
| 240 | 789 | 0.028 [-0.042, 0.083] | 0.131 [0.046, 0.262] | 0.654 [0.264, 1.015] |

variance_ratio (leads ≤120h, mediana entre estações): 0.774 [0.422, 76418035040380.578] ⚠️ IC degenerado: std(obs)≈0 em parte dos draws (amostra seca/curta) — métrica registrada intacta, estabiliza na M7
