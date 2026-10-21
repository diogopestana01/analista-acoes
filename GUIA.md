# Pôr o analista de ações a funcionar (passo a passo)

Tempo total: cerca de 15 minutos. Não precisas de instalar nada no computador.

## 1. Criar o repositório no GitHub
1. Vai a https://github.com/new
2. Repository name: `analista-acoes`
3. Escolhe **Private**.
4. Não marques "Add a README" nem nenhuma outra opção.
5. Carrega em **Create repository**.
6. Diz-me "repositório criado" no chat. Eu envio o código para lá.

## 2. Criar o bot do Telegram
1. No Telegram, procura **@BotFather** e abre a conversa.
2. Envia `/newbot`, escolhe um nome (ex. "Analista Pestana") e um username terminado em `bot`.
3. O BotFather responde com um **token** (algo como `123456:ABC-xyz...`). Guarda-o e não o partilhes.
4. Abre a conversa com o teu novo bot e envia-lhe "olá".
5. No browser, abre `https://api.telegram.org/bot<TOKEN>/getUpdates` (troca `<TOKEN>` pelo teu token).
6. Procura `"chat":{"id":` e copia o número que aparece a seguir. Esse é o teu **chat id**.

## 3. (Opcional) Chave da Anthropic para analisar notícias com o Claude
Sem esta chave, o modelo usa uma análise simples por palavras-chave.
1. Vai a https://console.anthropic.com e cria uma conta.
2. Em **Billing**, adiciona crédito (ex. 10 €).
3. Em **API keys**, cria uma chave e copia-a.

Custo estimado: com o modelo por defeito (Claude Opus 5.5), cerca de 0,5 a 1 € por dia útil. Para gastar
quase nada (cêntimos por mês), cria também o segredo `MODELO_CLAUDE` com o valor `claude-haiku-5-5`
(o workflow já o lê automaticamente).

## 4. Guardar os segredos no GitHub
1. No repositório, vai a **Settings > Secrets and variables > Actions**.
2. Carrega em **New repository secret** e cria, um de cada vez:
   - `TELEGRAM_TOKEN`: o token do passo 2.3
   - `TELEGRAM_CHAT_ID`: o número do passo 2.6
   - `ANTHROPIC_API_KEY`: a chave do passo 3 (opcional)

## 5. Primeiro teste
1. No repositório, abre o separador **Actions**.
2. Se aparecer um aviso, carrega em **I understand my workflows, go ahead and enable them**.
3. Escolhe **Alertas de hora a hora** e carrega em **Run workflow**.
4. A primeira execução demora 10 a 20 minutos (descarrega 2 anos de preços de milhares de ações).
5. Deves receber mensagens no Telegram. Esta execução manual também corre o backtest:
   o resultado fica em **Artifacts > backtest**, no fundo da página da execução.

## A partir daí
- O modelo corre sozinho de hora a hora, das 07:05 às 20:05 UTC (08:05 às 21:05 em Lisboa no inverno), nos dias úteis.
- Só recebes mensagens quando há sinais novos ou quando um sinal anterior chega ao objetivo, ao stop ou ao fim do prazo.
- Às 20h UTC recebes um resumo do dia com a taxa de acerto acumulada.
- O GitHub pode atrasar as execuções agendadas alguns minutos.
- Um repositório privado tem 2 000 minutos grátis de GitHub Actions por mês. Esta configuração deve gastar perto de 1 200.

## Avisos
- Os sinais usam a última cotação do Yahoo Finance. Confirma sempre o preço na XTB antes de entrar.
- Os sinais de venda (rompimento em baixa) são para CFDs (venda a descoberto). Podes desligá-los em `config.yaml`.
- Isto é uma ferramenta de apoio e não aconselhamento financeiro. Começa com valores pequenos ou na conta demo da XTB.
