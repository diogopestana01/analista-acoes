# Analista de ações (curto prazo)

Modelo que analisa preços e notícias e envia alertas de compra/venda para o Telegram,
com preço de entrada, objetivo, stop-loss e prazo estimado (1 dia a 1 semana).
As ordens são executadas manualmente na XTB.

## Estado
- [x] Passo 1: universo de ações, preços e notícias
- [x] Passo 2: modelo de sinais (técnico + notícias analisadas pelo Claude)
- [x] Passo 3: backtest (`passo3_backtest.py`) e registo em papel (`data/historico_sinais.csv`)
- [x] Passo 4: alertas no Telegram e execução diária no GitHub Actions
- [ ] Correr com dados reais e afinar

## Como funciona
`diario.py` corre de hora a hora, das 07:05 às 20:05 UTC nos dias úteis (`.github/workflows/diario.yml`):
1. na primeira execução do dia reconstrói o universo; nas outras só atualiza os últimos preços;
2. procura setups técnicos em todas as ações líquidas;
3. analisa as notícias das 40 melhores com o Claude;
4. envia para o Telegram até 10 sinais com entrada, objetivo, stop e prazo estimado;
5. avisa quando um sinal anterior atinge o objetivo, o stop ou o prazo (hora de vender).

De hora a hora só recebes mensagens quando há novidades; às 20h UTC chega um resumo do dia.
O volume do dia em curso é projetado para o dia inteiro, para os sinais intradiários serem comparáveis.

## Carteira (bot do Telegram)
Módulo à parte (`bot_carteira.py`, workflow "Carteira"). Cada sinal chega com botões
**Comprei / Outro preço / Ignorar** e a quantidade sugerida (por defeito 20% de uma carteira de 100 USD,
máximo 5 posições, só ações dos EUA). Os avisos de venda chegam com **Vendi / Outro preço ou parte / Mantenho**.
Ao lado da tua carteira há uma **simulada**, que entra em todos os sinais, para comparar.
Menu de botões fixo no fundo do chat (Carteira, Histórico, Comprei, Vendi, Depositar, Levantar,
Regras, Desfazer, Ajuda); os comandos `/carteira`, `/comprei`, `/vendi`, etc. continuam a funcionar. Outras pessoas entram com `/start` e o dono aprova.
O estado fica cifrado em `data/carteira.enc`. Para ligar: Actions > Carteira > Run workflow.

### Segredos no GitHub (Settings > Secrets and variables > Actions)
- `TELEGRAM_TOKEN`: token do bot criado no @BotFather
- `TELEGRAM_CHAT_ID`: o teu chat id (envia uma mensagem ao bot e abre
  `https://api.telegram.org/bot<TOKEN>/getUpdates`)
- `ANTHROPIC_API_KEY` (opcional): análise de notícias com o Claude; sem ela usa palavras-chave

O backtest corre quando carregas em "Run workflow" no separador Actions.

## Passo 1
```bash
pip install -r requirements.txt
python passo1_universo.py
```
Resultados em `data/`:
- `universo.csv`: todas as ações encontradas (EUA completos + principais índices europeus)
- `precos.parquet`: 2 anos de preços diários
- `universo_filtrado.csv`: ações com liquidez suficiente (preço >= 5, >= 2 M€/dia negociados)

Para usar exatamente a lista da XTB, coloca os tickers (formato Yahoo, ex. `SAP.DE`, `AAPL`)
num ficheiro `data/universo_extra.csv` com a coluna `ticker`.

Filtros e índices ajustam-se em `config.yaml`.

## Testes
```bash
python -m pytest -q tests
```
