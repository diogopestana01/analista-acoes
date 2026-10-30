# Analista de ações

Modelo de sinais de curto prazo (1 dia a 1 semana) com alertas no Telegram. O dono escreve em português de Portugal; código, comentários e mensagens também em PT-PT.

- Módulos independentes (um a falhar não trava os outros):
  - Sinais: `.github/workflows/diario.yml` -> `diario.py`, config em `config.yaml`. De 10 em 10 min, 07:05-21:55 UTC (lançado pelo Relógio).
  - Resumos de abertura e fecho: `.github/workflows/resumos.yml` -> `resumos_diarios.py`, config em `config_resumos.yaml`. Só lê os preços guardados pelos sinais.
  - Carteira (bot Telegram com botões e comandos, vários utilizadores): `.github/workflows/carteira.yml` -> `bot_carteira.py`, lógica em `src/bot.py` + `src/carteira.py`, config em `config_carteira.yaml`. Sempre ligado (long polling), relança-se a cada ~5h40. É o único que escreve `data/carteira.enc` (cifrado com `src/cofre.py`, o repositório é público); `diario.py` só o lê para pôr tamanho e botões nos sinais. Desliga-se desativando o workflow "Carteira".
  - Relógio: `.github/workflows/relogio.yml` lança os dois (o cron do GitHub não dispara neste repo). Desliga-se desativando o workflow "Relógio".
- Código partilhado em `src/` (notícias, preços, Telegram, calendário de resultados).
- Notícias em dois papéis: analista (`src/sentimento.py`, lê Google Notícias + Nasdaq de `src/noticias.py`) e advogado do diabo (`src/investigador.py`, faz a sua própria pesquisa de risco). O Yahoo e a SEC bloqueiam os servidores do GitHub. Para ver as fontes e os dois papéis sem enviar nada: workflow "Teste das notícias".
- Regra de ouro: só o código cria, verifica e decide sinais; o Claude só lê e anota. Tudo o que o Claude devolve passa por `src/verificador.py` (citações de manchetes numeradas, números confirmados nas fontes, datas contadas pelo código). Se o Claude falhar, fica neutro. As opiniões estão em modo sombra (`sinais.noticias_decidem: false`, `investigador.bloquear: false`) até o registo em papel provar que acertam.
- Força do sinal: a ordem é pelo retorno esperado da ação (`passo3f_forca.py`); a "pontuacao" é só técnica e não serve para escolher.
- Lista de sinais (`src/lista_sinais.py`, gravada pelos sinais em `data/lista_sinais.json`; o bot lê-a): reavaliada a cada corrida com o preço de agora; deixa de dar para comprar acima de `sinais.limite_compra_atr` ATR do preço do sinal (sinais da noite: do fecho, não da abertura; estudo `passo3h_abertura.py`).
- Risco (`src/risco.py`, estudo `passo3i_risco.py`): perda num dia mau ≈ 2,2 x ATR; nível baixo/médio/alto. Ganho esperado, confiança e risco são três coisas diferentes.
- Gestão das posições (stops, saídas, resultados, trocas): passa para a Carteira; `config.yaml` `gestao.no_bot: true` desliga-a no `diario.py`.
- Saúde: `src/saude.py` avisa o dono (preços velhos, Claude a falhar); o Relógio avisa se os sinais pararem ou falharem 2 vezes seguidas.
- Tendências nas redes (`src/tendencias.py`): Reddit via ApeWisdom e StockTwits, só informação no alerta e no resumo da manhã; guardadas no histórico para medir antes de mexerem na pontuação.
- Setups em `src/sinais.py` (`SETUPS` + `detetar`).
- Novo setup: adicionar a `SETUPS`, a regra em `detetar`, o nome em `config.yaml`, e um teste em `tests/`.
- Antes de mudar regras, correr o backtest (`passo3_backtest.py`, ou "Run workflow" no Actions) e comparar com `data/historico_sinais.csv` (resultados reais em papel).
- Testes: `python -m pytest -q tests` (offline, sem internet).
- Nunca guardar tokens no repositório; usam-se GitHub Secrets.
