# ADR-0001 — Data lake no HD interno 1TB NTFS, sem reparticionar

Data: 2026-07-23 · Status: aceito (decisão do usuário, D8 do PLAN.md)

## Contexto
NVMe do sistema com ~8 GB livres; Fase 1 precisa de dezenas de GB. Existe HD interno
de 1 TB (`/dev/sda3`, WDC WD10SPZX 5400 rpm) com 767 GB livres — porém NTFS, contendo
165 GB de arquivos pessoais insubstituíveis (fotos, documentos) sem backup.

## Decisão
Usar o HD **como está** (NTFS via driver kernel `ntfs3`), em pasta dedicada
`veritas-wx-data/`. `weather-project/data` é symlink para ela. **Nunca** reformatar
ou reparticionar este disco.

## Consequências
- Escrita sequencial medida: 113 MB/s (I/O direto) — suficiente para o padrão Parquet.
- A partição não monta sozinha no boot: scripts de build fazem preflight
  `ensure_mounted()` via `udisksctl` (sem sudo).
- Arquivos quentes (DuckDB de análise, results/) ficam no NVMe.
- Alternativa rejeitada: encolher NTFS + ext4 — risco não-zero sobre a única cópia
  de dados pessoais; ganho de desempenho irrelevante frente ao gargalo do disco físico.
