# Analista de ações

Modelo de sinais de curto prazo (1 dia a 1 semana) com alertas no Telegram. O dono escreve em português de Portugal; código, comentários e mensagens também em PT-PT.

- Módulos independentes (um a falhar não trava os outros):
  - Sinais: `.github/workflows/diario.yml` -> `diario.py`, config em `config.yaml`. De 30 em 30 min, 07:05-21:35 UTC.
  - Resumos de abertura e fecho: `.github/workflows/resumos.yml` -> `resumos_diarios.py`, config em `config_resumos.yaml`. Só lê os preços guardados pelos sinais.
  - Relógio: `.github/workflows/relogio.yml` lança os dois (o cron do GitHub não dispara neste repo). Desliga-se desativando o workflow "Relógio".
- Código partilhado em `src/` (notícias, preços, Telegram, calendário de resultados).
- Setups em `src/sinais.py` (`SETUPS` + `detetar`).
- Novo setup: adicionar a `SETUPS`, a regra em `detetar`, o nome em `config.yaml`, e um teste em `tests/`.
- Antes de mudar regras, correr o backtest (`passo3_backtest.py`, ou "Run workflow" no Actions) e comparar com `data/historico_sinais.csv` (resultados reais em papel).
- Testes: `python -m pytest -q tests` (offline, sem internet).
- Nunca guardar tokens no repositório; usam-se GitHub Secrets.
