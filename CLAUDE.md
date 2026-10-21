# Analista de ações

Modelo de sinais de curto prazo (1 dia a 1 semana) com alertas no Telegram. O dono escreve em português de Portugal; código, comentários e mensagens também em PT-PT.

- Corre de hora a hora no GitHub Actions (`.github/workflows/diario.yml` -> `diario.py`).
- Parâmetros em `config.yaml`; setups em `src/sinais.py` (`SETUPS` + `detetar`).
- Novo setup: adicionar a `SETUPS`, a regra em `detetar`, o nome em `config.yaml`, e um teste em `tests/`.
- Antes de mudar regras, correr o backtest (`passo3_backtest.py`, ou "Run workflow" no Actions) e comparar com `data/historico_sinais.csv` (resultados reais em papel).
- Testes: `python -m pytest -q tests` (offline, sem internet).
- Nunca guardar tokens no repositório; usam-se GitHub Secrets.
