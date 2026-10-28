"""Conversa do bot da Carteira: botões, comandos e mensagens por utilizador.

Tudo aqui é lógica pura (sem rede): recebe o estado e uma atualização do Telegram e
devolve a lista de chamadas à API a fazer, como (método, parâmetros). Assim testa-se offline.
"""
from __future__ import annotations

import html
import json
import secrets

import pandas as pd

from . import carteira as cart
from . import saidas, telegram

# Função que dá o preço atual de uma lista de tickers ({ticker: preço}). O bot_carteira.py liga-a a
# precos.atuais; nos testes fica None (usa o preço do sinal).
COTAR = None
FUGIU = 0.5   # já percorreu mais de metade do caminho até ao objetivo -> não sugerir
USERNAME = None   # nome do bot no Telegram (para os links de convite); o bot_carteira.py preenche-o
DONO = ""         # chat do dono, para lhe mostrar o botão 🎟️ Convidar no menu
DIAS_CONVITE = 7  # um convite serve uma vez e caduca ao fim destes dias

AJUDA = """<b>Como usar</b>
Usa o menu de botões em baixo:
💼 Carteira: posições, dinheiro livre e a carteira simulada
📜 Histórico: últimas vendas e resultado
🟢 Comprei / 🔴 Vendi: registar uma compra ou venda feita fora dos botões dos sinais
💵 Depositar / 🏧 Levantar: entradas e saídas de dinheiro
⚙️ Regras: capital e número de posições
↩️ Desfazer: anula a última operação
🔄 Recomeçar: põe a carteira a zero e volta a perguntar o capital e as posições

Os sinais e os avisos de venda trazem os seus próprios botões.
Se o menu desaparecer, envia /start.

Tens dúvidas sobre um tema? Escolhe em baixo:"""

TEMAS = {
    "sinal": ("📈 O que é um sinal", """📈 <b>O que é um sinal</b>
O bot analisa as ações dos EUA de 10 em 10 minutos. Quando uma ação mostra um padrão que no passado costumou dar ganho em poucos dias, envia-te um sinal.

Um sinal diz: que ação comprar, a que preço, quanto investir (já à medida da tua carteira), onde vender com lucro, onde vender para travar a perda e em quanto tempo.

A ⭐ Confiança (0 a 100) junta o histórico do padrão e da própria ação. Recebes só os sinais mais fortes que cabem nos teus lugares livres."""),
    "saidas": ("🎯 Objetivo e stop", """🎯 <b>Objetivo e stop</b>
🎯 <b>Objetivo</b>: o preço a que vendes com lucro.
🛑 <b>Stop</b>: o preço a que vendes para a perda não crescer. Na XTB podes deixá-lo já marcado como ordem "stop loss".
⏳ <b>Prazo</b>: se nenhum dos dois acontecer, vendes ao fim desses dias.

Alguns sinais não têm objetivo fixo: vendes quando o bot avisar (ex. "quando fechar acima da média de 5 dias"). O bot avisa-te sempre que for hora de vender."""),
    "carteira": ("💼 Carteira e simulada", """💼 <b>Carteira e carteira simulada</b>
A tua <b>carteira</b> tem só o que registaste: o que compraste (✅ Comprei), o que vendeste e o dinheiro livre. É o teu registo; ninguém mais o vê.

A <b>carteira simulada</b> faz de conta que entraste em todos os sinais desde o teu início, com as tuas regras. Comparar as duas mostra se as tuas escolhas estão a ajudar ou a atrapalhar."""),
    "riscos": ("⚠️ Riscos", """⚠️ <b>Riscos</b>
Nenhum sinal é garantia. Há semanas em que a maioria perde. O stop limita a perda, mas uma ação pode abrir abaixo do stop (por exemplo depois de resultados) e a perda ser maior.

Investe só dinheiro que podes perder. O bot não mexe no teu dinheiro: decides e compras tu na tua corretora."""),
}


def botoes_ajuda() -> list:
    temas = [(titulo, f"h|{k}") for k, (titulo, _) in TEMAS.items()]
    return [temas[:2], temas[2:], [("📖 Visita guiada (1 min)", "t|1|a")]]


# Visita guiada: 3 ecrãs curtos para quem chega (ou carrega na Ajuda).
VISITA = [
    """👋 <b>Como funciona</b> (1 de 3)
O bot procura ações dos EUA com padrões que no passado deram ganho em 1 a 5 dias. Quando encontra uma, envia-te um sinal.

Tu decides se compras, e compras tu na tua corretora (ex. XTB). O bot não mexe no teu dinheiro: regista o que fizeste e avisa-te quando for hora de vender.""",
    """📈 <b>Um sinal</b> (2 de 3)
Chega assim:

🟢 <b>COMPRAR AAPL</b> a ~230,00
💵 Quanto: 0,0869 ações (~20,00 USD)
🎯 Vender com lucro a 236,00 (+2,6%)
🛑 Vender para travar a perda a 226,00 (−1,7%)
⏳ Prazo: até 5 dias úteis

Se compraste, carrega ✅ <b>Comprei</b> (ou ✏️ <b>Outro preço</b> se pagaste outro valor). Se não, ❌ <b>Ignorar</b>.""",
    """💼 <b>Depois de comprar</b> (3 de 3)
Quando chegar ao objetivo, ao stop ou ao fim do prazo, o bot avisa-te para vender, com o botão ✅ <b>Vendi</b>.

Em 💼 <b>Carteira</b> vês as tuas posições, o dinheiro livre e o resultado. O menu em baixo tem tudo; ❓ <b>Ajuda</b> explica cada tema.

⚠️ Nenhum sinal é garantia. Investe só dinheiro que podes perder.""",
]


def visita(chat: str, passo: int, origem: str) -> tuple[str, dict]:
    """origem "n" = pessoa nova (no fim configura a carteira); "a" = veio da Ajuda."""
    if passo < len(VISITA):
        return _msg(chat, VISITA[passo - 1], [[("Seguinte ➡️", f"t|{passo + 1}|{origem}")]])
    fim = ("Configurar a minha carteira ➡️", "t|fim|n") if origem == "n" else ("✅ Percebi", "t|fim|a")
    return _msg(chat, VISITA[-1], [[fim]])

MENU = [["💼 Carteira", "📜 Histórico"], ["🟢 Comprei", "🔴 Vendi"], ["💵 Depositar", "🏧 Levantar"],
        ["⚙️ Regras", "↩️ Desfazer"], ["🔄 Recomeçar", "❓ Ajuda"]]
MENU_ACOES = {t for linha in MENU for t in linha}
CONVIDAR = "🎟️ Convidar"
MENU_DONO = MENU + [[CONVIDAR]]


def _teclado_menu(menu: list) -> str:
    return json.dumps({"keyboard": [[{"text": t} for t in linha] for linha in menu],
                       "resize_keyboard": True, "is_persistent": True})


TECLADO_MENU, TECLADO_DONO = _teclado_menu(MENU), _teclado_menu(MENU_DONO)


# ---------- botões ----------

def botoes_sinal(ticker: str, data: str) -> list:
    return [[("✅ Comprei", f"c|{ticker}|{data}"), ("✏️ Outro preço", f"o|{ticker}|{data}"),
             ("❌ Ignorar", f"i|{ticker}|{data}")],
            [("ℹ️ Porquê este sinal?", f"q|{ticker}|{data}")]]


def botoes_venda(ticker: str, preco: float) -> list:
    return [[(f"✅ Vendi a {cart.fmt(preco)}", f"v|{ticker}|{preco:.4f}")],
            [("✏️ Outro preço ou parte", f"w|{ticker}"), ("🔒 Mantenho", f"m|{ticker}")]]


def _msg(chat, texto: str, botoes: list | None = None, menu: bool = False) -> tuple[str, dict]:
    p = {"chat_id": str(chat), "text": texto, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if botoes:
        p["reply_markup"] = telegram.teclado(botoes)
    elif menu:
        p["reply_markup"] = TECLADO_DONO if DONO and str(chat) == DONO else TECLADO_MENU
    return "sendMessage", p


def _tirar_botoes(cq: dict) -> tuple[str, dict]:
    m = cq.get("message", {})
    return "editMessageReplyMarkup", {"chat_id": str(m.get("chat", {}).get("id", "")),
                                      "message_id": m.get("message_id"),
                                      "reply_markup": json.dumps({"inline_keyboard": []})}


# ---------- configuração guiada (entrada nova ou recomeço) ----------

def pergunta_capital(chat: str, u: dict, intro: str = "") -> tuple[str, dict]:
    moeda = u["regras"]["moeda"]
    return _msg(chat, f"{intro}💰 <b>Passo 1 de 2:</b> com quanto dinheiro ({moeda}) começa esta carteira?\n"
                      f"Usa o valor que vais mesmo pôr na corretora para estes sinais.",
                [[(f"{v} {moeda}", f"cap|{v}") for v in (100, 250, 500)],
                 [(f"{v} {moeda}", f"cap|{v}") for v in (1000, 2500)] + [("✏️ Outro valor", "cap|outro")]])


def pergunta_posicoes(chat: str, u: dict) -> tuple[str, dict]:
    cap, moeda = u["regras"]["capital_inicial"], u["regras"]["moeda"]

    def por(n):
        return f"{cart.fmt(cap / n)} {moeda}"
    texto = (f"📊 <b>Passo 2 de 2:</b> quantas posições queres ter abertas ao mesmo tempo?\n"
             f"Cada sinal recebe o capital a dividir por esse número.\n\n"
             f"<b>Menos posições</b> (ex. 3, ~{por(3)} cada): cada trade pesa mais, para o bem e para o mal. "
             f"Enches a carteira depressa e ficas mais vezes sem capital quando chegam sinais novos, "
             f"por isso só entras nos primeiros.\n\n"
             f"<b>Mais posições</b> (ex. 10, ~{por(10)} cada): o risco fica mais espalhado e apanhas mais sinais, "
             f"mas cada ganho conta menos e, com pouco capital, as ordens ficam muito pequenas.\n\n"
             f"<b>5</b> é o meio-termo que recomendo para começar.")
    return _msg(chat, texto, [[(f"{n} posições", f"pos|{n}") for n in (3, 5)],
                              [(f"{n} posições", f"pos|{n}") for n in (8, 10)]])


def _fim_configuracao(chat: str, u: dict, hist) -> list:
    r = u["regras"]
    resumo = (f"✅ Carteira pronta: <b>{cart.fmt(r['capital_inicial'])} {r['moeda']}</b>, até "
              f"<b>{r['max_posicoes']}</b> posições de ~{cart.fmt(r['capital_inicial'] / r['max_posicoes'])} "
              f"{r['moeda']} cada. Podes mudar isto em ⚙️ Regras.")
    return [_msg(chat, resumo, menu=True)] + sinais_abertos(chat, u, hist)


def _capital_inicio(u: dict, v: float) -> None:
    """Capital escolhido na configuração (não conta como operação, para o ↩️ Desfazer voltar a antes do recomeço)."""
    u["regras"]["capital_inicial"] = float(v)
    u["caixa"] = float(v) - cart.investido(u)


def definir_posicoes(u: dict, n: int) -> None:
    n = max(int(n), 1)
    u["regras"]["max_posicoes"] = n
    u["regras"]["pct"] = round(100 / n, 2)


# ---------- utilizadores ----------

def garantir_dono(estado: dict, regras: dict, hoje: str, nome: str = "dono") -> None:
    dono = estado.get("dono")
    if dono and dono not in estado["utilizadores"]:
        estado["utilizadores"][dono] = cart.novo_utilizador(nome, regras, "ativo", hoje)


def criar_convite(estado: dict, hoje: str) -> str:
    """Novo código de convite (serve uma vez). Devolve o link para o dono reencaminhar."""
    convites = estado.setdefault("convites", {})
    codigo = secrets.token_urlsafe(9)
    convites[codigo] = {"criado": hoje, "usado_por": None}
    return f"https://t.me/{USERNAME}?start={codigo}" if USERNAME else f"/start {codigo}"


def _convite_valido(estado: dict, codigo: str, hoje: str) -> bool:
    c = (estado.get("convites") or {}).get(codigo)
    if not c or c["usado_por"]:
        return False
    return (pd.Timestamp(hoje) - pd.Timestamp(c["criado"])).days <= DIAS_CONVITE


def _boas_vindas(chat: str, nome: str, aviso: str) -> list:
    return [_msg(chat, f"✅ Bem-vindo, {nome}! Tens a tua própria carteira, só tua.\n<i>{aviso}</i>", menu=True),
            visita(chat, 1, "n")]


def _start(estado, chat, nome, cfg, hoje, codigo: str = "") -> list:
    u = estado["utilizadores"].get(chat)
    aviso = cfg.get("aviso", "")
    if chat == estado.get("dono"):
        garantir_dono(estado, cfg["regras_base"], hoje, nome)
        estado["utilizadores"][chat]["nome"] = nome
        return [_msg(chat, f"Olá {nome}! A tua carteira está ativa.\n{aviso}\n\n{AJUDA}", botoes_ajuda()),
                _msg(chat, "O menu está em baixo.", menu=True)]
    if u and u["estado"] == "ativo":
        return [_msg(chat, AJUDA, botoes_ajuda()), _msg(chat, "O menu está em baixo.", menu=True)]
    if codigo and _convite_valido(estado, codigo, hoje):
        estado["convites"][codigo]["usado_por"] = chat
        novo = cart.novo_utilizador(nome, cfg["regras_base"], "ativo", hoje)
        novo["convidado"] = True  # entrou por convite do dono (gratuito)
        estado["utilizadores"][chat] = novo
        return _boas_vindas(chat, nome, aviso) + [
            _msg(estado["dono"], f"🎟️ <b>{nome}</b> entrou com o teu convite.")]
    if codigo:
        aviso = ("Este convite já foi usado ou caducou. Pede um novo a quem te convidou; "
                 "entretanto, pedi acesso por ti.\n" + aviso)
    if u and u["estado"] == "pendente":
        return [_msg(chat, "O teu pedido ainda está à espera de aprovação.")]
    if u and u["estado"] == "recusado":
        return []
    pendentes = sum(x["estado"] == "pendente" for x in estado["utilizadores"].values())
    if pendentes >= cfg.get("max_pendentes", 20):
        return [_msg(chat, "De momento não há vagas. Tenta mais tarde.")]
    estado["utilizadores"][chat] = cart.novo_utilizador(nome, cfg["regras_base"], "pendente", hoje)
    return [_msg(chat, f"Olá {nome}! Pedido enviado. Aviso quando fores aceite.\n{aviso}"),
            _msg(estado["dono"], f"👤 Novo pedido para usar o bot: <b>{nome}</b> (chat {chat}).",
                 [[("✅ Aceitar", f"a|{chat}"), ("❌ Recusar", f"r|{chat}")]])]


# ---------- sinais no histórico ----------

def _sinal(hist: pd.DataFrame, ticker: str, data: str | None = None) -> dict | None:
    h = hist[hist["ticker"] == ticker]
    if data:
        h = h[h["data_sinal"].astype(str) == data]
    if h.empty:
        return None
    s = h.iloc[-1]

    def num(x):
        return float(x) if pd.notna(x) else None
    return {"chave": cart.chave_sinal(ticker, str(s["data_sinal"])), "data": str(s["data_sinal"]),
            "entrada": num(s["entrada"]), "stop": num(s["stop"]), "objetivo": num(s["objetivo"]),
            "estado": str(s["estado"])}


# ---------- processar uma atualização ----------

def processar(estado: dict, upd: dict, hist: pd.DataFrame, hoje: str, cfg: dict) -> list:
    global DONO
    DONO = str(estado.get("dono") or "")
    if "callback_query" in upd:
        return _callback(estado, upd["callback_query"], hist, hoje, cfg)
    msg = upd.get("message") or {}
    texto = (msg.get("text") or "").strip()
    if not texto:
        return []
    chat = str(msg["chat"]["id"])
    # escapado: as mensagens vão em HTML e um nome com "&" ou "<" fazia o Telegram recusá-las
    nome = html.escape((msg.get("from") or {}).get("first_name") or "amigo")
    partes = texto.split()
    cmd = partes[0].split("@")[0].lower()
    if cmd == "/start":
        return _start(estado, chat, nome, cfg, hoje, partes[1] if len(partes) > 1 else "")
    u = estado["utilizadores"].get(chat)
    if not u or u["estado"] != "ativo":
        return []
    try:
        if texto == CONVIDAR and chat == estado.get("dono"):
            u["aguarda"] = None
            link = criar_convite(estado, hoje)
            return [_msg(chat, f"🎟️ <b>Convite pronto.</b> Reencaminha a mensagem seguinte à pessoa que queres "
                               f"convidar. O link serve uma vez e caduca em {DIAS_CONVITE} dias. Quem entrar por ele "
                               f"fica logo aceite, sem teres de aprovar.", menu=True),
                    _msg(chat, f"Olá! Convido-te a experimentar o meu bot de sinais de ações. Abre este link e "
                               f"carrega em Iniciar:\n{link}")]
        if texto in MENU_ACOES:
            u["aguarda"] = None
            return [_menu(chat, u, texto, hist, hoje)]
        a = u.get("aguarda") or {}
        if a.get("op") == "capital_inicio" and not cmd.startswith("/"):
            v = cart.numero(partes[0])
            if v <= 0:
                raise ValueError(v)
            u["aguarda"] = None
            _capital_inicio(u, v)
            return [pergunta_posicoes(chat, u)]
        if a and not cmd.startswith("/"):
            return [_msg(chat, _resposta(u, partes, hist, hoje), menu=True)]
        return [_msg(chat, _comando(estado, chat, u, cmd, partes[1:], hist, hoje), menu=True)]
    except (ValueError, IndexError):
        return [_msg(chat, "Não percebi os números. Exemplo: 51,20 0,4 (ou /ajuda).")]


def _menu(chat: str, u: dict, botao: str, hist, hoje: str) -> tuple[str, dict]:
    """Um botão do menu fixo: responde já ou pergunta o que falta."""
    moeda = u["regras"]["moeda"]
    if botao == "💼 Carteira":
        return _msg(chat, cart.formatar_carteira(u, cart.simular(hist, u["regras"], u.get("desde", ""))), menu=True)
    if botao == "📜 Histórico":
        return _msg(chat, cart.formatar_historico(u), menu=True)
    if botao == "🔄 Recomeçar":
        return _msg(chat, f"🔄 Recomeçar a carteira do zero? Apaga posições e operações e volta a "
                          f"{cart.fmt(u['regras']['capital_inicial'])} {moeda}. As regras ficam iguais.",
                    [[("✅ Sim, recomeçar", "z|sim"), ("❌ Cancelar", "z|nao")]])
    if botao == "↩️ Desfazer":
        return _msg(chat, cart.desfazer(u), menu=True)
    if botao == "❓ Ajuda":
        return _msg(chat, AJUDA, botoes_ajuda())
    if botao == "🟢 Comprei":
        u["aguarda"] = {"tipo": "ticker"}
        return _msg(chat, "🟢 Que ação compraste? Escreve o ticker (ex. <code>NVDA</code>).")
    if botao == "🔴 Vendi":
        if not u["posicoes"]:
            return _msg(chat, "Não tens posições abertas.", menu=True)
        botoes = [[(f"{t} ({cart.fmt_qtd(p['qtd'])} a {cart.fmt(p['preco'])})", f"w|{t}")]
                  for t, p in sorted(u["posicoes"].items())]
        return _msg(chat, "🔴 Qual vendeste?", botoes)
    if botao in ("💵 Depositar", "🏧 Levantar"):
        op = "deposito" if botao == "💵 Depositar" else "levantamento"
        u["aguarda"] = {"tipo": "valor", "op": op}
        return _msg(chat, f"{botao.split()[0]} Quanto ({moeda})? Ex.: <code>50</code>")
    r = u["regras"]  # ⚙️ Regras
    return _msg(chat, f"⚙️ Agora: capital <b>{cart.fmt(r['capital_inicial'])} {moeda}</b>, até "
                      f"<b>{r['max_posicoes']}</b> posições ({cart.fmt(r['pct'], 0)}% por sinal).\n"
                      f"Menos posições = mais dinheiro em cada sinal e mais risco por trade; "
                      f"mais posições = risco mais espalhado, ordens mais pequenas. Escolhe para mudar:",
                [[(f"{n} posições", f"x|{n}") for n in (3, 5, 8, 10)],
                 [("💰 Mudar capital", "k|")]])


def _resposta(u: dict, partes: list, hist, hoje: str) -> str:
    a = u["aguarda"]
    if a["tipo"] == "ticker":
        t = partes[0].upper()
        sinal = _sinal(hist, t)
        data = sinal["data"] if sinal and sinal["estado"] == "aberto" else None
        u["aguarda"] = {"tipo": "compra", "ticker": t, "data": data}
        return (f"🟢 {t}: a que preço compraste? Podes juntar a quantidade.\n"
                f"Ex.: <code>51,20</code> ou <code>51,20 0,4</code>")
    if a["tipo"] == "valor":
        v = cart.numero(partes[0])
        u["aguarda"] = None
        if a["op"] == "capital":
            return cart.definir_capital(u, v, hoje)
        return cart.movimento(u, abs(v) if a["op"] == "deposito" else -abs(v), hoje)
    preco = cart.numero(partes[0])
    qtd = cart.numero(partes[1]) if len(partes) > 1 else None
    if a["tipo"] == "venda":
        u["aguarda"] = None
        return cart.vender(u, a["ticker"], qtd, preco, hoje)
    sinal = _sinal(hist, a["ticker"], a.get("data"))
    if qtd is None:
        t = cart.tamanho(u, a["ticker"], preco)
        if t["motivo"]:
            return cart.linha_tamanho(u, t) + "\nSe compraste na mesma, escreve preço e quantidade (ex. 51,20 0,4)."
        qtd = t["qtd"]
    u["aguarda"] = None
    return cart.comprar(u, a["ticker"], qtd, preco, hoje, sinal)


def _comando(estado, chat: str, u: dict, cmd: str, args: list, hist, hoje: str) -> str:
    if cmd in ("/ajuda", "/help"):
        return AJUDA
    if cmd == "/cancelar":
        u["aguarda"] = None
        return "Cancelado."
    if cmd == "/carteira":
        return cart.formatar_carteira(u, cart.simular(hist, u["regras"], u.get("desde", "")))
    if cmd == "/historico":
        return cart.formatar_historico(u)
    if cmd == "/desfazer":
        return cart.desfazer(u)
    if cmd == "/comprei":
        t = args[0].upper()
        sinal = _sinal(hist, t)
        if sinal and sinal["estado"] != "aberto":
            sinal = None
        return cart.comprar(u, t, cart.numero(args[1]), cart.numero(args[2]), hoje, sinal)
    if cmd == "/vendi":
        t = args[0].upper()
        qtd = None if args[1].lower() == "tudo" else cart.numero(args[1])
        return cart.vender(u, t, qtd, cart.numero(args[2]), hoje)
    if cmd == "/capital":
        return cart.definir_capital(u, cart.numero(args[0]), hoje)
    if cmd == "/deposito":
        return cart.movimento(u, abs(cart.numero(args[0])), hoje)
    if cmd == "/levantamento":
        return cart.movimento(u, -abs(cart.numero(args[0])), hoje)
    if cmd == "/regras":
        r = u["regras"]
        pares = dict(zip(args[::2], args[1::2]))
        if "pct" in pares:
            r["pct"] = min(max(cart.numero(pares["pct"]), 1.0), 100.0)
        if "max" in pares:
            r["max_posicoes"] = max(int(cart.numero(pares["max"])), 1)
        return f"Regras: {cart.fmt(r['pct'], 0)}% por sinal, máximo {r['max_posicoes']} posições."
    if cmd == "/utilizadores" and chat == estado.get("dono"):
        return "\n".join(f"• {x['nome']} ({c}): {x['estado']}" for c, x in estado["utilizadores"].items())
    return "Não conheço esse comando. Envia /ajuda."


def _callback(estado, cq: dict, hist, hoje: str, cfg: dict) -> list:
    chat = str(cq.get("message", {}).get("chat", {}).get("id") or cq["from"]["id"])
    out = [("answerCallbackQuery", {"callback_query_id": cq["id"]})]
    partes = (cq.get("data") or "").split("|")
    acao = partes[0]
    if acao in ("a", "r"):
        if chat != estado.get("dono"):
            return out
        alvo = estado["utilizadores"].get(partes[1])
        if not alvo or alvo["estado"] != "pendente":
            return out + [_tirar_botoes(cq)]
        alvo["estado"] = "ativo" if acao == "a" else "recusado"
        alvo["desde"] = hoje
        out += [_tirar_botoes(cq), _msg(chat, f"{alvo['nome']}: {'aceite' if acao == 'a' else 'recusado'}.")]
        if acao == "a":
            out += _boas_vindas(partes[1], alvo["nome"], cfg.get("aviso", ""))
        return out
    u = estado["utilizadores"].get(chat)
    if not u or u["estado"] != "ativo":
        return out
    ticker = partes[1] if len(partes) > 1 else ""
    if acao == "h":
        return out + [_msg(chat, TEMAS.get(ticker, TEMAS["sinal"])[1], botoes_ajuda())]
    if acao == "t":
        if ticker != "fim":
            return out + [_tirar_botoes(cq), visita(chat, int(ticker), partes[2])]
        if partes[2] == "n":
            return out + [_tirar_botoes(cq), pergunta_capital(chat, u, "Vamos configurar a tua carteira.\n")]
        return out + [_tirar_botoes(cq), _msg(chat, "👍 Qualquer dúvida, carrega em ❓ Ajuda.", menu=True)]
    if acao == "q":
        return out + [_msg(chat, porque(hist, ticker, partes[2]))]
    if acao in ("p", "x"):
        n = cart.numero(ticker)
        if acao == "p":
            u["regras"]["pct"] = min(max(n, 1.0), 100.0)
        else:
            definir_posicoes(u, int(n))
        r = u["regras"]
        return out + [_tirar_botoes(cq), _msg(chat, f"⚙️ Feito: {cart.fmt(r['pct'], 0)}% por sinal, máximo {r['max_posicoes']} posições.",
                                               menu=True)]
    if acao == "z":
        if ticker != "sim":
            return out + [_tirar_botoes(cq), _msg(chat, "Ficou tudo como estava.", menu=True)]
        cart.recomecar(u, hoje)
        return out + [_tirar_botoes(cq), pergunta_capital(chat, u, "🔄 Carteira a zero (se foi engano, carrega em "
                                                                     "↩️ Desfazer).\n\n")]
    if acao == "cap":
        if ticker == "outro":
            u["aguarda"] = {"tipo": "valor", "op": "capital_inicio"}
            return out + [_tirar_botoes(cq), _msg(chat, f"✏️ Escreve o valor ({u['regras']['moeda']}). Ex.: <code>300</code>")]
        _capital_inicio(u, cart.numero(ticker))
        return out + [_tirar_botoes(cq), pergunta_posicoes(chat, u)]
    if acao == "pos":
        definir_posicoes(u, int(cart.numero(ticker)))
        return out + [_tirar_botoes(cq)] + _fim_configuracao(chat, u, hist)
    if acao == "k":
        u["aguarda"] = {"tipo": "valor", "op": "capital"}
        return out + [_tirar_botoes(cq), _msg(chat, f"💰 Qual é o capital total da carteira ({u['regras']['moeda']})? Ex.: <code>100</code>")]
    if acao in ("c", "o", "i"):
        data = partes[2]
        chave = cart.chave_sinal(ticker, data)
        if u["decisoes"].get(chave) in ("comprei", "ignorei"):
            return out + [_tirar_botoes(cq), _msg(chat, f"Já tinhas respondido a {ticker} ({u['decisoes'][chave]}).")]
        if acao == "i":
            u["decisoes"][chave] = "ignorei"
            return out + [_tirar_botoes(cq), _msg(chat, f"❌ {ticker} ignorado.")]
        if acao == "o":
            u["aguarda"] = {"tipo": "compra", "ticker": ticker, "data": data}
            return out + [_msg(chat, f"✏️ {ticker}: escreve o preço a que compraste e, se quiseres, a quantidade.\n"
                                     f"Ex.: <code>51,20</code> ou <code>51,20 0,4</code>")]
        sinal = _sinal(hist, ticker, data)
        if not sinal:
            return out + [_msg(chat, f"Ainda não tenho o sinal de {ticker}. Tenta outra vez daqui a 1 minuto.")]
        preco = (u.get("precos_mostrados") or {}).get(chave, sinal["entrada"])
        t = cart.tamanho(u, ticker, preco)
        if t["motivo"]:
            return out + [_msg(chat, cart.linha_tamanho(u, t) + "\nSe compraste na mesma, usa ✏️ Outro preço com a quantidade.")]
        return out + [_tirar_botoes(cq), _msg(chat, cart.comprar(u, ticker, t["qtd"], preco, hoje, sinal))]
    if ticker not in u["posicoes"]:
        return out + [_tirar_botoes(cq), _msg(chat, f"Já não tens {ticker} na carteira.")]
    if acao == "v":
        return out + [_tirar_botoes(cq), _msg(chat, cart.vender(u, ticker, None, cart.numero(partes[2]), hoje))]
    if acao == "w":
        u["aguarda"] = {"tipo": "venda", "ticker": ticker}
        return out + [_msg(chat, f"✏️ {ticker}: escreve o preço de venda e, se foi só uma parte, a quantidade.\n"
                                 f"Ex.: <code>53,10</code> ou <code>53,10 0,2</code>")]
    if acao == "m":
        u["posicoes"][ticker]["acompanhar"] = False
        return out + [_tirar_botoes(cq), _msg(chat, f"🔒 Mantens {ticker}. Deixo de avisar; quando venderes usa "
                                                    f"/vendi {ticker} tudo preço.")]
    return out


# ---------- mensagens enviadas pelos sinais (diario.py) ----------

def mensagens_sinais(estado: dict | None, novos: list[dict], cabecalho: str, dono: str | None) -> list:
    """Para cada utilizador ativo: só os sinais mais fortes que cabem nos lugares livres da carteira,
    com tamanho e botões; os restantes numa linha. Sem estado (carteira desligada), o dono recebe todos."""
    out = []
    if not estado:
        if dono:
            out.append((dono, cabecalho, None))
            for s in novos:
                b = botoes_sinal(s["ticker"], s["data_sinal"]) if cart.mercado(s["ticker"]) == "eua" else None
                out.append((dono, telegram.formatar_sinal(s), b))
        return out
    for chat, u in cart.ativos(estado).items():
        out += [(chat, texto, b) for texto, b in _ronda(u, novos, cabecalho)]
    return out


def _confianca(s: dict) -> float:
    try:
        v = float(s.get("pontuacao"))
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(v) else v


def escolher(u: dict, sinais: list[dict]) -> tuple[list, list, int]:
    """Sinais que servem a esta carteira, do mais forte para o mais fraco, partidos em
    (os que cabem nos lugares livres, os que ficam de fora, lugares livres)."""
    elegiveis = [s for s in sinais if cart.mercado(s["ticker"]) in u["regras"]["mercados"]
                 and s["ticker"] not in u["posicoes"]]
    elegiveis.sort(key=_confianca, reverse=True)
    livres = max(u["regras"]["max_posicoes"] - len(u["posicoes"]), 0)
    return elegiveis[:livres], elegiveis[livres:], livres


def _ronda(u: dict, sinais: list[dict], cabecalho: str) -> list:
    """Cabeçalho + os melhores sinais (com botões) + uma linha com os que ficaram de fora."""
    top, resto, livres = escolher(u, sinais)
    if not top and not resto:
        return []
    r = u["regras"]
    ocupadas = f"{len(u['posicoes'])}/{r['max_posicoes']}"
    if top:
        extra = (f"\nPara ti: {'o melhor' if len(top) == 1 else f'os {len(top)} melhores'} "
                 f"(tens {livres} lugar{'es' if livres != 1 else ''} livre{'s' if livres != 1 else ''}, carteira {ocupadas}).")
    else:
        fraca = cart.posicao_mais_fraca(u, "")
        extra = (f"\n💼 A tua carteira está cheia ({ocupadas}), por isso não te mostro estes sinais."
                 + (f" A posição mais antiga é {fraca}; se a venderes, abres lugar." if fraca else ""))
    out = [(cabecalho + extra, None)] + _sinais_para(u, top, len(top) + len(resto))
    if resto:
        nomes = ", ".join(f"{s['ticker']} ({_confianca(s):.0f})" for s in resto[:8])
        mais = f" e mais {len(resto) - 8}" if len(resto) > 8 else ""
        out.append((f"➖ Ficaram de fora {len(resto)} com menos confiança: {nomes}{mais}.", None))
    return out


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(v) else v


def sinal_simples(u: dict, s: dict, t: dict, livre: float, rank: str) -> str:
    """O sinal em linguagem simples: o que comprar, quanto, quando vender e em quanto tempo."""
    moeda, e = u["regras"]["moeda"], s["entrada"]
    obj, stop = _num(s.get("objetivo")), _num(s.get("stop"))
    linhas = [f"🟢 <b>COMPRAR {s['ticker']}</b> a ~{cart.fmt(e)}"]
    if t["motivo"]:
        linhas.append(f"💵 {cart.MOTIVOS[t['motivo']].format(mercados=', '.join(m.upper() for m in u['regras']['mercados']), max=u['regras']['max_posicoes'])}")
    else:
        linhas.append(f"💵 Quanto: <b>{cart.fmt_qtd(t['qtd'])}</b> ações (~{cart.fmt(t['valor'])} {moeda}). "
                      f"Ficas com {cart.fmt(livre)} {moeda} livres.")
    regra = saidas.REGRAS.get(s.get("saida") or "")
    if obj:
        linhas.append(f"🎯 Vender com lucro a <b>{cart.fmt(obj)}</b> ({cart.sinal_fmt((obj / e - 1) * 100, 1)}%)")
    elif regra and regra.sair_se:
        linhas.append(f"🎯 Vender: {regra.texto_alerta()} (eu aviso-te)")
    if stop:
        linhas.append(f"🛑 Vender para travar a perda a <b>{cart.fmt(stop)}</b> ({cart.sinal_fmt((stop / e - 1) * 100, 1)}%)")
    dias = int(round(_num(s.get("max_dias")) or _num(s.get("dias_estimados")) or 5))
    linhas.append(f"⏳ Prazo: até {dias} dia{'s' if dias != 1 else ''} út{'eis' if dias != 1 else 'il'}")
    if s.get("risco_evento"):
        linhas.append("⚠️ A empresa apresenta resultados em breve: o preço pode dar um salto, para cima ou para baixo.")
    linhas.append(rank)
    return "\n".join(linhas)


def _sinais_para(u: dict, sinais: list[dict], total: int | None = None) -> list:
    """Texto e botões de cada sinal para este utilizador; o dinheiro vai sendo reservado pela ordem."""
    out, reservado, ocupadas = [], 0.0, 0
    for k, s in enumerate(sinais, start=1):
        t = cart.tamanho(u, s["ticker"], s["entrada"], reservado, ocupadas)
        if not t["motivo"]:
            reservado, ocupadas = reservado + t["valor"], ocupadas + 1
        rank = f"⭐ Confiança {_confianca(s):.0f}/100 (n.º {k} de {total or len(sinais)})"
        texto = sinal_simples(u, s, t, u["caixa"] - reservado, rank)
        b = None if t["motivo"] in ("fora_mercado", "ja_tem") else botoes_sinal(s["ticker"], s["data_sinal"])
        out.append((texto, b))
    return out


PORQUE = {
    "rompimento": "A ação fechou acima do preço mais alto das últimas 4 semanas, com muito mais volume que o normal, "
                  "e está numa tendência de subida. Quando isto acontece, muitas vezes a subida continua mais uns dias.",
    "pullback": "A ação está numa tendência de subida há meses, mas caiu forte nos últimos 1 ou 2 dias. Nestes casos "
                "costuma recuperar parte da queda em poucos dias. Foi o padrão com melhores resultados até agora.",
    "gap_volume": "A ação abriu com um salto forte e volume muito acima do normal (por exemplo depois de resultados) "
                  "e fechou em alta. Estes saltos às vezes continuam nos dias seguintes.",
    "gap_continuacao": "No dia anterior a ação deu um salto forte com volume e aguentou-o. Isso mostra procura firme.",
}


def porque(hist: pd.DataFrame, ticker: str, data: str) -> str:
    """Explicação simples de um sinal (botão ℹ️ Porquê este sinal?)."""
    h = hist[(hist["ticker"] == ticker) & (hist["data_sinal"].astype(str) == data)]
    if h.empty:
        return f"Ainda não tenho os detalhes de {ticker}. Tenta daqui a 1 minuto."
    s = h.iloc[-1]
    explic = PORQUE.get(s["setup"], telegram.SETUPS_PT.get(s["setup"], s["setup"]))
    regra = saidas.REGRAS.get(s.get("saida") or "")
    linhas = [f"ℹ️ <b>Porquê {ticker}?</b> (sinal de {data})", explic]
    if _num(s.get("pontuacao")) is not None:
        linhas.append(f"\n⭐ Confiança {_num(s['pontuacao']):.0f}/100: junta os resultados passados deste padrão "
                      f"e desta ação. Quanto mais alta, melhor correram sinais parecidos.")
    if regra and regra.sair_se:
        linhas.append(f"🚪 Regra de saída: {regra.texto_alerta()}.")
    linhas.append("\nNenhum sinal é garantia; o stop existe para a perda ficar pequena quando corre mal.")
    return "\n".join(linhas)


def atualizar_precos(sinais: list[dict]) -> list[dict]:
    """Troca o preço de entrada pelo preço de agora e tira os sinais que já não servem:
    sem cotação, já abaixo do stop, já no objetivo ou que já fizeram mais de metade do caminho."""
    if COTAR is None:
        return sinais
    px = COTAR(sorted({s["ticker"] for s in sinais}))
    out = []
    for s in sinais:
        p, e, o, st = px.get(s["ticker"]), s["entrada"], s.get("objetivo"), s.get("stop")
        if p is None or (st and p <= st) or (o and p >= o):
            continue
        alvo = o or (e + (e - st) if st else None)
        if alvo and p > e + FUGIU * (alvo - e):
            continue
        out.append({**s, "entrada": p, "entrada_sinal": e})
    return out


def sinais_abertos(chat: str, u: dict, hist: pd.DataFrame) -> list:
    """Para quem começa (ou recomeça): os sinais em aberto mais fortes que cabem na carteira,
    ao preço de agora, com botões."""
    h = hist[(hist["estado"] == "aberto") & (hist["direcao"] == "compra")]
    h = h[[cart.mercado(t) in u["regras"]["mercados"] and t not in u["posicoes"] for t in h["ticker"]]]
    sinais = [{k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in r.items()}
              for r in h.assign(data_sinal=h["data_sinal"].astype(str),
                                dias_estimados=h["dias_estimados"].fillna(5)).to_dict("records")]
    sinais = atualizar_precos(sinais)
    if COTAR is not None:
        # guardar o preço mostrado: o botão ✅ Comprei regista a esse preço, não ao do dia do sinal
        u["precos_mostrados"] = {cart.chave_sinal(s["ticker"], s["data_sinal"]): s["entrada"] for s in sinais}
        cab = ("📋 <b>Sinais ainda em aberto, ao preço de agora</b>\nSó os que ainda não bateram no stop "
               "nem no objetivo e não subiram demasiado desde o sinal.")
    else:
        cab = ("📋 <b>Sinais ainda em aberto</b>\nO preço é o do dia do sinal; "
               "confirma o preço atual na XTB antes de entrar.")
    ronda = _ronda(u, sinais, cab)
    if not ronda:
        return [_msg(chat, "Não há sinais em aberto que ainda valham a pena agora. Aviso-te quando chegar o próximo.",
                     menu=True)]
    return [_msg(chat, texto, b) for texto, b in ronda]


def mensagens_fechos(estado: dict | None, fechos: list[dict], dono: str | None) -> list:
    """Aviso de venda: personalizado (com botões) para quem tem a ação; o dono recebe sempre."""
    out = []
    for f in fechos:
        base = telegram.formatar_fecho(f)
        donos_avisados = False
        for chat, u in (cart.ativos(estado).items() if estado else []):
            p = u["posicoes"].get(f["ticker"])
            if not p or not p.get("acompanhar", True):
                continue
            pct = (f["preco_saida"] / p["preco"] - 1) * 100
            texto = (f"{base}\n💼 Tens {cart.fmt_qtd(p['qtd'])} compradas a {cart.fmt(p['preco'])} "
                     f"({cart.sinal_fmt(pct, 1)}% a {cart.fmt(f['preco_saida'])}).")
            out.append((chat, texto, botoes_venda(f["ticker"], f["preco_saida"])))
            donos_avisados |= chat == dono
        if dono and not donos_avisados:
            out.append((dono, base, None))
    return out


def lembretes(estado: dict, hist: pd.DataFrame, hoje: str) -> list:
    """Ao fim do dia: sinais de hoje sem resposta, com botões para cada um."""
    out = []
    h = hist[(hist["data_sinal"].astype(str) == hoje) & (hist["direcao"] == "compra")]
    for chat, u in cart.ativos(estado).items():
        falta = [t for t in h["ticker"]
                 if cart.mercado(t) in u["regras"]["mercados"] and hoje >= u.get("desde", "")
                 and cart.chave_sinal(t, hoje) not in u["decisoes"] and t not in u["posicoes"]]
        if not falta:
            continue
        botoes = [[(f"✅ {t}", f"c|{t}|{hoje}"), (f"✏️ {t}", f"o|{t}|{hoje}"), (f"❌ {t}", f"i|{t}|{hoje}")]
                  for t in falta[:10]]
        out.append(("sendMessage", _msg(chat, f"⏰ Ficaram sem resposta hoje: {', '.join(falta)}. Compraste algum?",
                                        botoes)[1]))
        for t in falta:
            u["decisoes"][cart.chave_sinal(t, hoje)] = "sem_resposta"
    return out
