# Contribuindo com o veritas-wx

🇧🇷 Português · [🇺🇸 English](CONTRIBUTING.en.md)

Obrigado por considerar contribuir. Este projeto valoriza **disciplina científica acima de
contagem de features** — as regras abaixo existem para o dataset continuar confiável.

## Setup de desenvolvimento

```bash
git clone https://github.com/dev-isaacmello/veritas-wx.git
cd veritas-wx
uv sync --group dev            # + --group grib --group geo --group graphcast conforme necessário
uv run pytest -m "not network and not slow"
uv run ruff check .
```

Testes marcados `network` batem em buckets/APIs reais e ficam fora do CI; rode-os localmente
antes de mexer em qualquer fetcher. Testes `slow` (propriedades de cobertura do bootstrap) rodam
com `uv run pytest -m slow`.

## As regras que não são negociáveis

1. **Nenhuma estimativa sem intervalo de confiança.** Funções públicas em `analyze/` retornam
   `BootstrapResult` (ou `TTestResult`), nunca um float nu. Existe um teste que impõe isso;
   não brigue com ele.
2. **O registro é congelado.** `metrics_registry.yaml` é pré-registrado. Métricas novas entram
   como *diagnósticos exploratórios* (documentado nas docstrings). Promoção a confirmatória exige
   emenda versionada do registro em PR próprio, com família BH própria.
3. **Flagar, nunca deletar.** O QC seta bits; o consumidor escolhe o rigor via máscara. Deletar
   observação é bug, não limpeza.
4. **NULL, nunca imputado.** Hora faltante é hora faltante. Totais de precip 24h exigem ≥22
   leituras horárias limpas; somas parciais são proibidas.
5. **Todo estágio reconcilia.** `linhas_entrada == linhas_saída + soma(drops itemizados)` — o
   runlog levanta exceção caso contrário. Se você adiciona um filtro, adiciona o contador do drop.
6. **Funções puras em `analyze/`.** Sem I/O, sem aleatoriedade escondida — RNGs são argumentos
   explícitos.
7. **Ingestão idempotente.** Downloads passam por manifests sha256; re-rodar precisa ser no-op
   para artefatos verificados.

## Estilo

- Python 3.12, `ruff` (linha 100) — o CI roda
- Docstrings carregam a documentação; evite comentários inline `#` (pragmas funcionais como
  `# noqa` são ok)
- Golden tests: ao portar uma fórmula, calcule à mão pelo menos um caso na docstring do teste
- Código portado (ex.: WeatherBench-X, Apache 2.0) mantém a linha de atribuição no docstring
  do módulo

## Boas primeiras contribuições

- Novos checks de QC (com golden tests e entrada no contrato do bitmask)
- Redes de estações além do INMET (o `duplicate_check` já suporta cross-network)
- Portes de métricas da literatura como diagnósticos exploratórios
- Performance nos fast-paths do bootstrap

## Pull requests

- Uma mudança lógica por PR; testes verdes (suíte `not network and not slow`) e ruff limpo
- Se a mudança afeta semântica de dados (unidades, convenções, limiares), diga explicitamente
  na descrição — isso versiona o `ingest_version`
- Inglês em código/commits; PT-BR bem-vindo em issues e discussões
