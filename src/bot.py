"""Conversa do bot da Carteira: botões, comandos e mensagens por utilizador.

Tudo aqui é lógica pura (sem rede): recebe o estado e uma atualização do Telegram e
devolve a lista de chamadas à API a fazer, como (método, parâmetros). Assim testa-se offline.
"""
from __future__ import annotations

import html
import json
import re
import math
import secrets
from pathlib import Path

import copy

import numpy as np
import pandas as pd

from . import carteira as cart
from . import acompanhamento, cotacoes, diario_trades, lista_sinais, risco, saidas, telegram, tendencias, trocas

# Função que dá o preço atual de uma lista de tickers ({ticker: preço}). O bot_carteira.py liga-a a
# precos.atuais; nos testes fica None (usa o preço do sinal).
COTAR = None
TALAO_URL = ""  # página do talão (docs/talao.html no GitHub Pages) que abre dentro do Telegram
LER_PRINT = None  # (file_id, tipo) -> números do print já conferidos (leitor_print.verificar), ou None
NOMES: dict = {}  # ticker -> nome da empresa (universo), para achar a ação de um print pelo nome
LER_COTACOES = None  # devolve data/cotacoes.json (preço de agora e velas recentes, escrito pelos sinais)
FUGIU = 0.5   # já percorreu mais de metade do caminho até ao objetivo -> não sugerir
USERNAME = None   # nome do bot no Telegram (para os links de convite); o bot_carteira.py preenche-o
DONO = ""         # chat do dono, para lhe mostrar o botão 🎟️ Convidar no menu
DIAS_CONVITE = 7  # um convite serve uma vez e caduca ao fim destes dias
CFG_SINAIS = None  # config.yaml -> sinais (regra de saída de cada setup, para o ATR)
MODELO_TROCAS = None  # data/valor_restante.json (src/trocas.py)
LER_LISTA = None  # () -> dict: data/lista_sinais.json, gravada pelos sinais a cada corrida
COTAR_VIVO = None  # (tickers) -> {ticker: preço} em tempo real (Nasdaq), só com a bolsa aberta; o bot_carteira preenche
VIVO_MAX = 12      # quantos sinais da lista vão buscar o preço do momento quando alguém a abre
LISTA_MAX_MIN = 20  # mais velha do que isto (ex. os sinais pararam), o bot calcula a lista ele próprio
CONF_SEM_SINAL = 50  # confiança assumida para uma compra registada à mão (sem sinal)

AJUDA = """<b>Como usar</b>
💼 Carteira: as tuas posições ao preço de agora
📈 Sinais: os sinais em aberto que ainda dá para comprar
🟢 Comprei / 🔴 Vendi: registar uma compra ou venda feita fora dos botões dos sinais
⚙️ Mais: resultados, depositar e levantar, desfazer, regras e avisos, recomeçar

Os sinais e os avisos de venda trazem os seus próprios botões.
Se o menu desaparecer, envia /start.

Tens dúvidas sobre um tema? Escolhe em baixo:"""

TEMAS = {
    "sinal": ("📈 O que é um sinal", """📈 <b>O que é um sinal</b>
O bot analisa as ações dos EUA de 10 em 10 minutos. Quando uma ação mostra um padrão que no passado costumou dar ganho em poucos dias, envia-te um sinal.

Um sinal diz: que ação comprar, a que preço, quanto investir (já à medida da tua carteira), onde vender com lucro, onde vender para travar a perda e em quanto tempo.

O 📊 Retorno esperado é a média do que sinais parecidos renderam, já sem custos, e a percentagem diz quantos deles ganharam. Recebes só os de maior retorno esperado que cabem nos teus lugares livres."""),
    "saidas": ("🎯 Objetivo e stop", """🎯 <b>Objetivo e stop</b>
🎯 <b>Objetivo</b>: o preço a que vendes com lucro.
🛑 <b>Stop</b>: o preço a que vendes para a perda não crescer. Na XTB podes deixá-lo já marcado como ordem "stop loss".
⏳ <b>Prazo</b>: se nenhum dos dois acontecer, vendes ao fim desses dias.

Alguns sinais não têm objetivo fixo: vendes quando o bot avisar (ex. "quando o preço recuperar"). O bot avisa-te sempre que for hora de vender."""),
    "carteira": ("💼 A carteira", """💼 <b>A carteira</b>
A tua <b>carteira</b> tem só o que registaste: o que compraste (✅ Comprei), o que vendeste e o dinheiro livre. É o teu registo; ninguém mais o vê.

Cada posição aparece ao preço de agora (atualizado de 10 em 10 minutos), com o ganho, o dia do prazo e uma barra que mostra onde está o preço entre o 🛑 stop e o 🎯 objetivo. Se o preço tocar num deles, o bot avisa-te logo."""),
    "riscos": ("⚠️ Riscos", """⚠️ <b>Riscos</b>
Nenhum sinal é garantia. Há semanas em que a maioria perde. O stop limita a perda, mas uma ação pode abrir abaixo do stop (por exemplo depois de resultados) e a perda ser maior.

Investe só dinheiro que podes perder. O bot não mexe no teu dinheiro: decides e compras tu na tua corretora."""),
}


def botoes_ajuda() -> list:
    temas = [(titulo, f"h|{k}") for k, (titulo, _) in TEMAS.items()]
    return [temas[:2], temas[2:], [("📖 Visita guiada (1 min)", "t|1|a")],
            [("💬 Enviar sugestão ou erro", "y|")]]


# Visita guiada: 3 ecrãs curtos para quem chega (ou carrega na Ajuda).
VISITA = [
    """👋 <b>Como funciona</b> (1 de 3)
O bot procura ações dos EUA com padrões que no passado deram ganho em 1 a 5 dias. Quando encontra uma, envia-te um sinal.

Tu decides se compras, e compras tu na tua corretora (ex. XTB). O bot não mexe no teu dinheiro: regista o que fizeste e avisa-te quando for hora de vender.""",
    """📈 <b>Um sinal</b> (2 de 3)
Chega assim:

🟢 <b>COMPRAR AAPL</b> a ~230,00
🔒 Compra só até 231,50: acima disso já não compensa
💵 Quanto: 0,0869 ações (~20,00 USD)
🎯 Vender com lucro a 236,00 (+2,6%)
🛑 Vender para travar a perda a 226,00 (−1,7%)
⏳ Prazo: até 5 dias úteis

Se compraste, carrega ✅ <b>Comprei</b>: abre um talão onde escreves o preço e o valor pago que a XTB te mostra. Se não, ❌ <b>Ignorar</b>.""",
    """💼 <b>Depois de comprar</b> (3 de 3)
Quando chegar ao objetivo, ao stop ou ao fim do prazo, o bot avisa-te para vender, com o botão ✅ <b>Vendi</b>.

Em 💼 <b>Carteira</b> vês as tuas posições, o dinheiro livre e o resultado. Em 📈 <b>Sinais</b> vês a lista dos sinais de agora: os que ainda dá para comprar e os que estão em andamento. O resto (resultados, regras, ajuda) está em ⚙️ <b>Mais</b>.

⚠️ Nenhum sinal é garantia. Investe só dinheiro que podes perder.""",
]


def visita(chat: str, passo: int, origem: str) -> tuple[str, dict]:
    """origem "n" = pessoa nova (no fim configura a carteira); "a" = veio da Ajuda."""
    if passo < len(VISITA):
        return _msg(chat, VISITA[passo - 1], [[("Seguinte ➡️", f"t|{passo + 1}|{origem}")]])
    fim = ("Configurar a minha carteira ➡️", "t|fim|n") if origem == "n" else ("✅ Percebi", "t|fim|a")
    return _msg(chat, VISITA[-1], [[fim]])

MENU = [["💼 Carteira", "📈 Sinais"], ["🟢 Comprei", "🔴 Vendi"], ["⚙️ Mais"]]
# O que se usa pouco fica no ⚙️ Mais (botões na mensagem): Resultados, dinheiro, desfazer, regras, ajuda.
MAIS = [["📊 Resultados", "⚙️ Regras"], ["💵 Depositar", "🏧 Levantar"], ["↩️ Desfazer", "🔄 Recomeçar"],
        ["❓ Ajuda"]]
ANTIGOS = {"📜 Histórico", "📊 Resultados", "💵 Depositar", "🏧 Levantar", "⚙️ Regras", "↩️ Desfazer",
           "🔄 Recomeçar", "❓ Ajuda"}  # botões de teclados antigos continuam a funcionar
MENU_ACOES = {t for linha in MENU for t in linha} | ANTIGOS
CONVIDAR, PESSOAS = "🎟️ Convidar", "👥 Pessoas"
MENU_DONO = MENU


def _teclado_menu(menu: list) -> str:
    return json.dumps({"keyboard": [[{"text": t} for t in linha] for linha in menu],
                       "resize_keyboard": True, "is_persistent": True})


TECLADO_MENU, TECLADO_DONO = _teclado_menu(MENU), _teclado_menu(MENU_DONO)


# ---------- botões ----------

def botoes_sinal(ticker: str, data: str) -> list:
    return [[("✅ Comprei", f"c|{ticker}|{data}"), ("❌ Ignorar", f"i|{ticker}|{data}")],
            [("ℹ️ Porquê este sinal?", f"q|{ticker}|{data}")]]


def ticker_do_sinal(botoes: list | None) -> str | None:
    """A ação de uma mensagem de sinal (a que tem o botão ✅ Comprei), ou None."""
    try:
        d = botoes[0][0][1]
    except (TypeError, IndexError):
        return None
    return d.split("|")[1] if d.startswith("c|") else None


def _copia(x: float) -> str:
    """Número para colar na corretora: ponto decimal, sem zeros a mais."""
    return f"{x:.4f}".rstrip("0").rstrip(".")


def botoes_copiar(qtd: float, s: dict) -> list:
    """Uma linha de botões que copiam o valor a investir, o stop e o objetivo, para colar na XTB."""
    valor = round(qtd * float(s["entrada"]), 2) if _num(s.get("entrada")) else None
    linha = ([(f"📋 Valor {cart.fmt(valor)}", telegram.COPIAR + _copia(valor))] if valor
             else [(f"📋 Qtd {cart.fmt_qtd(qtd)}", telegram.COPIAR + _copia(qtd))])
    for nome, k in (("Stop", "stop"), ("Obj.", "objetivo")):
        v = _num(s.get(k))
        if v:
            linha.append((f"📋 {nome} {cart.fmt(v)}", telegram.COPIAR + _copia(round(v, 2))))
    return linha


def botoes_venda(ticker: str, preco: float) -> list:
    return [[(f"✅ Vendi (~{cart.fmt(preco)})", f"v|{ticker}|{preco:.4f}"), ("🔒 Mantenho", f"m|{ticker}")]]


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
        # lembra o dono (pode não ter visto o primeiro pedido)
        return [_msg(chat, "O teu pedido ainda está à espera de aprovação. Avisei outra vez; "
                           "se tiveres um link de convite, abre-o e entras logo."),
                _pedido_ao_dono(estado, chat, u["nome"], "🔔 Pede outra vez para usar o bot")]
    if u and u["estado"] == "recusado":
        return [_msg(chat, SO_CONVITE)]
    pendentes = sum(x["estado"] == "pendente" for x in estado["utilizadores"].values())
    if pendentes >= cfg.get("max_pendentes", 20):
        return [_msg(chat, "De momento não há vagas. Tenta mais tarde.")]
    estado["utilizadores"][chat] = cart.novo_utilizador(nome, cfg["regras_base"], "pendente", hoje)
    return [_msg(chat, f"Olá {nome}! Pedido enviado. Aviso quando fores aceite.\n{aviso}"),
            _pedido_ao_dono(estado, chat, nome, "👤 Novo pedido para usar o bot")]


SO_CONVITE = ("Este bot é só por convite. Pede um link de convite a quem te falou dele, "
              "abre-o e carrega em Iniciar.")


def _pedido_ao_dono(estado: dict, chat: str, nome: str, titulo: str) -> tuple[str, dict]:
    return _msg(estado["dono"], f"{titulo}: <b>{nome}</b>.",
                [[("✅ Aceitar", f"a|{chat}"), ("❌ Recusar", f"r|{chat}")]])


def _sem_acesso(u: dict | None) -> str:
    """Resposta a quem ainda não pode usar o bot: nunca ficar calado."""
    if not u:
        return "Olá! Carrega em /start para começar."
    if u["estado"] == "pendente":
        return "O teu pedido ainda está à espera de aprovação. Aviso-te logo que fores aceite."
    return SO_CONVITE


def _pessoas(estado: dict) -> tuple[str, dict]:
    """Painel do dono: quem usa o bot e pedidos por responder."""
    nomes = {"ativo": "✅", "pendente": "⏳", "recusado": "⛔"}
    linhas, botoes = ["👥 <b>Pessoas no bot</b>"], []
    for c, x in sorted(estado["utilizadores"].items(), key=lambda kv: (kv[1]["estado"] != "pendente", kv[1]["nome"])):
        extra = " (tu)" if c == estado.get("dono") else (" · convidado" if x.get("convidado") else "")
        uso = ""
        if x["estado"] == "ativo":
            lucro, n, g = cart.realizado(x)
            dec = list(x.get("decisoes", {}).values())
            uso = (f"\n      {len(x['posicoes'])} posições · {n} vendas"
                   + (f" ({cart.sinal_fmt(lucro)} {x['regras']['moeda']}, {g} a ganhar)" if n else "")
                   + f" · seguiu {dec.count('comprei')} de {len(dec)} sinais"
                   + (f" · última vez {cart._dm(x['visto'])}" if x.get("visto") else ""))
        linhas.append(f"{nomes.get(x['estado'], '?')} <b>{x['nome']}</b>{extra} · desde {x.get('desde') or '?'}{uso}")
        if x["estado"] in ("pendente", "recusado"):
            botoes.append([(f"✅ Aceitar {x['nome'][:20]}", f"a|{c}")]
                          + ([(f"❌ Recusar", f"r|{c}")] if x["estado"] == "pendente" else []))
    linhas.append("\n✅ ativo · ⏳ à espera · ⛔ recusado")
    return _msg(estado["dono"], "\n".join(linhas), botoes or None, menu=not botoes)


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
    if not texto and msg.get("web_app_data"):
        chat = str(msg["chat"]["id"])
        u = estado["utilizadores"].get(chat)
        if not u or u["estado"] != "ativo":
            return [_msg(chat, _sem_acesso(u))]
        u["visto"], u["aguarda"] = hoje, None
        return _do_formulario(estado, chat, u, msg["web_app_data"].get("data", ""), hist, hoje)
    if not texto and _imagem(msg):
        return _print(estado, msg, hist, hoje)
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
        return [_msg(chat, _sem_acesso(u))]
    u["visto"] = hoje
    try:
        if texto == PESSOAS and chat == estado.get("dono"):
            u["aguarda"] = None
            return [_pessoas(estado)]
        if texto == CONVIDAR and chat == estado.get("dono"):
            u["aguarda"] = None
            link = criar_convite(estado, hoje)
            return [_msg(chat, f"🎟️ <b>Convite pronto.</b> Reencaminha a mensagem seguinte à pessoa que queres "
                               f"convidar. O link serve uma vez e caduca em {DIAS_CONVITE} dias. Quem entrar por ele "
                               f"fica logo aceite, sem teres de aprovar.", menu=True),
                    _msg(chat, f"Olá! Convido-te a experimentar o meu bot de sinais de ações. Abre este link e "
                               f"carrega em Iniciar:\n{link}")]
        if texto in BOTOES_TALAO:
            u["aguarda"] = None
            if not u.get("talao"):
                return [_msg(chat, "Não há nenhum talão aberto.", menu=True)]
            op = BOTOES_TALAO[texto]
            return _callback_talao(chat, u, None, ["b", "ok"] if op == "ok" else ["b", "x"] if op == "x"
                                   else ["b", "e", op], hist, hoje)
        if texto == "📈 Sinais":
            u["aguarda"] = None
            return lista_sinais_msg(chat, u, hist, hoje)
        if texto in MENU_ACOES:
            u["aguarda"] = None
            return [_menu(chat, u, texto, hist, hoje)]
        a = u.get("aguarda") or {}
        if a.get("tipo") == "sugestao" and not cmd.startswith("/"):
            u["aguarda"] = None
            dono = estado.get("dono")
            out = [_msg(chat, "🙏 Obrigado! Já está entregue.", menu=True)]
            if dono and dono != chat:
                out.append(_msg(dono, f"💬 <b>Sugestão de {html.escape(u['nome'])}</b>:\n{html.escape(texto)}"))
            return out
        if a.get("tipo") == "motivo" and not cmd.startswith("/"):
            u["aguarda"] = None
            op = _venda_do_diario(u, str(a.get("idx")), a.get("t", ""))
            if op:
                op["motivo"], op["motivo_txt"] = "outro", html.escape(texto[:200])
            return [_msg(chat, f"📝 {a.get('t', '')}: registado no teu diário." if op
                         else "Essa venda já não existe (foi desfeita?).", menu=True)]
        if a.get("tipo") == "print" and not cmd.startswith("/"):
            return _do_print(chat, u, partes[0].upper(), a["lido"], hist)
        if a.get("tipo") == "talao" and not cmd.startswith("/") and u.get("talao"):
            v = cart.numero(partes[0])
            b = u["talao"]
            if a["campo"] == "l" and _lucro_talao(b) is not None:
                u["aguarda"] = None
                _acertar_talao(b, "")
                _por_lucro(b, v)
                antigo, b["msg"] = b.get("msg"), None
                return ([("editMessageReplyMarkup", {"chat_id": chat, "message_id": antigo,
                                                     "reply_markup": json.dumps({"inline_keyboard": []})})] if antigo else []) \
                    + [msg_talao(chat, u)]
            if v < 0 or (a["campo"] == "p" and v == 0):
                raise ValueError(v)
            u["aguarda"] = None
            _acertar_talao(b, "")
            campo = {"p": "preco", "v": "valor"}.get(a["campo"], "qtd")
            b[campo] = round(v, 4)
            if campo != "preco":
                b["tudo"] = b["fixa"] = False
            _acertar_talao(b, {"preco": "p", "valor": "v"}.get(campo, "q"))
            antigo = b.get("msg")
            b["msg"] = None
            return ([("editMessageReplyMarkup", {"chat_id": chat, "message_id": antigo,
                                                 "reply_markup": json.dumps({"inline_keyboard": []})})] if antigo else []) \
                + [msg_talao(chat, u)]
        if a.get("tipo") == "ticker" and not cmd.startswith("/"):
            t = partes[0].upper()
            sinal = _sinal(hist, t)
            data = sinal["data"] if sinal and sinal["estado"] == "aberto" else None
            preco = _cotacoes([t]).get(t) or (sinal["entrada"] if data else None)
            if preco:
                u["aguarda"] = None
                return [_abrir_compra(chat, u, t, preco, data)]
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
        return [_msg(chat, "Não percebi o número. Escreve só o número, por exemplo <code>51,20</code>.")]


def _menu(chat: str, u: dict, botao: str, hist, hoje: str) -> tuple[str, dict]:
    """Um botão do menu fixo: responde já ou pergunta o que falta."""
    moeda = u["regras"]["moeda"]
    if botao == "💼 Carteira":
        return _msg(chat, carteira_agora(u, hist, hoje), menu=True)
    if botao == "⚙️ Mais":
        linhas = [[(x, f"mm|{i}|{j}") for j, x in enumerate(linha)] for i, linha in enumerate(MAIS)]
        if DONO and chat == DONO:
            linhas.append([(CONVIDAR, "mm|d|0"), (PESSOAS, "mm|d|1")])
        return _msg(chat, "⚙️ <b>Mais</b>", linhas)
    if botao == "📈 Sinais":
        return None  # tratado em processar (pode ser mais de uma mensagem)
    if botao in ("📊 Resultados", "📜 Histórico"):
        return _msg(chat, resultados(u, hist), menu=True)
    if botao == "🔄 Recomeçar":
        return _msg(chat, "🔄 <b>Recomeçar do zero?</b> Apaga as posições e as operações. Escolhe:\n"
                          f"🧹 <b>Só a carteira</b>: volta a {cart.fmt(u['regras']['capital_inicial'])} {moeda} "
                          "e escolhes o capital e as posições.\n"
                          "👤 <b>Como pessoa nova</b>: tudo como no primeiro dia (boas-vindas, visita guiada, "
                          "configuração e avisos de origem).\n"
                          "Se te enganares, ↩️ Desfazer volta atrás.",
                    [[("🧹 Só a carteira", "z|sim")], [("👤 Como pessoa nova", "z|novo")],
                     [("❌ Cancelar", "z|nao")]])
    if botao == "↩️ Desfazer":
        o = cart.ultima_operacao(u)
        if not o:
            return _msg(chat, "Não há nada para desfazer.", menu=True)
        return _msg(chat, f"↩️ Desfazer {o}?", [[("✅ Sim, desfazer", "un|sim"), ("❌ Não", "un|nao")]])
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
                 [("💰 Mudar capital", "k|"), ("🔔 Avisos", "g|ver")]])


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
        return carteira_agora(u, hist, hoje)
    if cmd in ("/historico", "/resultados"):
        return resultados(u, hist)
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
    return "Não percebi. Usa os botões do menu aqui em baixo 👇 (em ⚙️ Mais está a ❓ Ajuda)."


# ---------- print da XTB: o bot lê e prepara o talão ----------

def _imagem(msg: dict) -> tuple[str, str] | None:
    """(file_id, tipo) da imagem da mensagem: foto, ou ficheiro de imagem."""
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"], "image/jpeg"
    d = msg.get("document") or {}
    if str(d.get("mime_type", "")).startswith("image/"):
        return d["file_id"], d["mime_type"]
    return None


def _print(estado: dict, msg: dict, hist, hoje: str) -> list:
    chat = str(msg["chat"]["id"])
    u = estado["utilizadores"].get(chat)
    if not u or u["estado"] != "ativo":
        return [_msg(chat, _sem_acesso(u))]
    u["visto"] = hoje
    lido = LER_PRINT(*_imagem(msg)) if LER_PRINT else None
    if not lido:
        return [_msg(chat, "📸 Não consegui ler os números deste print. Manda o ecrã da posição na XTB (onde aparece "
                           "o volume, o preço de abertura e o valor), ou usa o talão.", menu=True)]
    from . import leitor_print
    # primeiro as ações que a pessoa tem, depois os sinais em aberto, por fim todas
    abertos = set(hist.loc[hist["estado"] == "aberto", "ticker"]) if hist is not None and len(hist) else set()
    t = None
    for grupo in (set(u["posicoes"]), abertos, set(NOMES)):
        t = leitor_print.achar_ticker(lido, {x: NOMES.get(x, "") for x in grupo})
        if t:
            break
    if not t:
        u["aguarda"] = {"tipo": "print", "lido": lido}
        return [_msg(chat, f"📸 Li o print ({html.escape(lido['nome'] or 'sem nome')}), mas não sei o ticker. "
                           f"Escreve-o (ex. <code>UNH</code>).")]
    return _do_print(chat, u, t, lido, hist)


def _resumo_print(lido: dict) -> str:
    return (f"📸 Li no print: {cart.fmt_qtd(round(lido['volume'], 6))} ações a {cart.fmt(lido['preco'])} "
            f"({cart.fmt(lido['valor'])} USD)")


def _do_print(chat: str, u: dict, t: str, lido: dict, hist) -> list:
    u["aguarda"] = None
    p = u["posicoes"].get(t)
    if p and lido["fechada"]:
        preco = lido["preco_fecho"] or (lido["preco"] + (lido["lucro"] or 0) / lido["volume"])
        abrir_talao(u, "venda", t, preco, p["qtd"])
        if lido["lucro"] is not None and not lido["preco_fecho"]:
            _por_lucro(u["talao"], lido["lucro"])
        return [_msg(chat, f"📸 {t}: posição fechada no print. Confere a venda e confirma."), msg_talao(chat, u)]
    if p:
        igual = abs(p["qtd"] - lido["volume"]) < 1e-4 and abs(p["preco"] - lido["preco"]) < 0.005
        if igual:
            return [_msg(chat, f"{_resumo_print(lido)}.\n✅ {t} já está certo no bot.", menu=True)]
        u["print"] = {"ticker": t, "qtd": lido["volume"], "preco": lido["preco"]}
        return [_msg(chat, f"{_resumo_print(lido)}.\nNo bot tens {t}: {cart.fmt_qtd(p['qtd'])} a {cart.fmt(p['preco'])}. "
                           f"Corrijo para ficar igual à XTB?",
                     [[("✅ Corrigir", f"pc|{t}|sim"), ("❌ Deixar", f"pc|{t}|nao")]])]
    sinal = _sinal(hist, t)
    data = sinal["data"] if sinal and sinal["estado"] == "aberto" else None
    abrir_talao(u, "compra", t, lido["preco"], lido["volume"], data)
    b = u["talao"]
    b["valor"], b["qtd"], b["fixa"] = lido["valor"], lido["volume"], True  # o volume da XTB é exato
    return [_msg(chat, f"{_resumo_print(lido)}. Confere e carrega em ✅ Confirmar."), msg_talao(chat, u)]


def _corrigir_posicao(u: dict, t: str, qtd: float, preco: float) -> str:
    p = u["posicoes"][t]
    cart._guardar_anterior(u)
    k = 1 + u["regras"]["custo_pct"] / 100
    u["caixa"] -= (qtd * preco - p["qtd"] * p["preco"]) * k
    for o in reversed(u["operacoes"]):  # a compra que deu origem à posição fica igual à XTB
        if o["tipo"] == "compra" and o["ticker"] == t:
            o["qtd"], o["preco"] = qtd, preco
            break
    p["qtd"], p["preco"] = qtd, preco
    return (f"✅ {t} corrigido: {cart.fmt_qtd(qtd)} a {cart.fmt(preco)} ({cart.fmt(qtd * preco)} USD). "
            f"Livre: {cart.fmt(u['caixa'])} USD. Se não era isto, carrega em ↩️ Desfazer.")


def _callback(estado, cq: dict, hist, hoje: str, cfg: dict) -> list:
    chat = str(cq.get("message", {}).get("chat", {}).get("id") or cq["from"]["id"])
    out = [("answerCallbackQuery", {"callback_query_id": cq["id"]})]
    partes = (cq.get("data") or "").split("|")
    acao = partes[0]
    if acao in ("a", "r"):
        if chat != estado.get("dono"):
            return out
        alvo = estado["utilizadores"].get(partes[1])
        if not alvo or alvo["estado"] not in ("pendente", "recusado") or (acao == "r" and alvo["estado"] != "pendente"):
            return out + [_tirar_botoes(cq)]
        alvo["estado"] = "ativo" if acao == "a" else "recusado"
        alvo["desde"] = hoje
        out += [_tirar_botoes(cq), _msg(chat, f"{alvo['nome']}: {'aceite' if acao == 'a' else 'recusado'}.")]
        if acao == "a":
            out += _boas_vindas(partes[1], alvo["nome"], cfg.get("aviso", ""))
        return out
    u = estado["utilizadores"].get(chat)
    if not u or u["estado"] != "ativo":
        return out + [_msg(chat, _sem_acesso(u))]
    ticker = partes[1] if len(partes) > 1 else ""
    if acao == "mm":
        if partes[1] == "d":
            if chat != estado.get("dono"):
                return out
            return out + processar(estado, {"message": {"chat": {"id": chat}, "text": (CONVIDAR, PESSOAS)[int(partes[2])],
                                                        "from": cq.get("from") or {}}}, hist, hoje, cfg)
        botao = MAIS[int(partes[1])][int(partes[2])]
        u["aguarda"] = None
        return out + [_menu(chat, u, botao, hist, hoje)]
    if acao == "un":
        if partes[1] != "sim":
            return out + [_editar(cq, "Ficou tudo como estava.", None)]
        return out + [_editar(cq, cart.desfazer(u), None)]
    if acao == "pc":
        pr, u["print"] = u.get("print"), None
        if partes[2] != "sim" or not pr or pr["ticker"] != ticker or ticker not in u["posicoes"]:
            return out + [_editar(cq, "Ficou como estava.", None)]
        return out + [_editar(cq, _corrigir_posicao(u, ticker, pr["qtd"], pr["preco"]), None)]
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
    if acao == "mv":
        op = _venda_do_diario(u, partes[1], partes[2]) if len(partes) > 3 else None
        if not op or partes[3] not in diario_trades.MOTIVOS:
            return out + [_tirar_botoes(cq)]
        if partes[3] == "outro":
            u["aguarda"] = {"tipo": "motivo", "idx": int(partes[1]), "t": partes[2]}
            return out + [_editar(cq, f"✍️ Escreve numa mensagem porque vendeste {partes[2]}.", None)]
        op["motivo"] = partes[3]
        return out + [_editar(cq, f"📝 {partes[2]}: {diario_trades.MOTIVOS[partes[3]]}. Registado no teu diário.", None)]
    if acao == "lst":
        return out + lista_sinais_msg(chat, u, hist, hoje)
    if acao == "car":
        return out + [_msg(chat, carteira_agora(u, hist, hoje), menu=True)]
    if acao == "ls":
        return out + [ver_sinal(chat, u, ticker, partes[2], hist, hoje)]
    if acao == "g":
        a = avisos(u)
        if ticker == "som":
            a["som"] = not a["som"]
        elif ticker == "ret":
            a["ret_min"] = _seguinte_ret(a["ret_min"])
        elif ticker == "fora":
            a["fora"] = not a["fora"]
        elif ticker == "manha":
            a["manha"] = not a["manha"]
        u["avisos"] = a
        txt, botoes = texto_avisos(u)
        return out + [_msg(chat, txt, botoes) if ticker == "ver" else _editar(cq, txt, botoes)]
    if acao == "y":
        u["aguarda"] = {"tipo": "sugestao"}
        return out + [_msg(chat, "💬 Escreve numa mensagem a tua sugestão ou o erro que encontraste. "
                                 "Vai direta para quem gere o bot.")]
    if acao == "s":
        vende, compra, data = ticker, partes[2], partes[3]
        if vende not in u["posicoes"]:
            return out + [_msg(chat, f"Já não tens {vende} na carteira; se ainda quiseres {compra}, usa 🟢 Comprei.")]
        if compra in u["posicoes"]:
            return out + [_msg(chat, f"Já tens {compra} na carteira.")]
        p = u["posicoes"][vende]
        abrir_talao(u, "venda", vende, _cotacoes([vende]).get(vende) or p["preco"], p["qtd"],
                    troca={"compra": compra, "data": data})
        return out + [_tirar_botoes(cq), msg_talao(chat, u)]
    if acao == "n":
        u["decisoes"].setdefault(cart.chave_sinal(ticker, partes[2]), "ignorei")
        return out + [_tirar_botoes(cq), _msg(chat, "🔒 Mantive a carteira como está.")]
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
        if ticker not in ("sim", "novo"):
            return out + [_tirar_botoes(cq), _msg(chat, "Ficou tudo como estava.", menu=True)]
        cart.recomecar(u, hoje)
        if ticker == "novo":  # como no primeiro dia: regras e avisos de origem, boas-vindas e visita guiada
            u["regras"] = {**cart.REGRAS_BASE, **(cfg.get("regras_base") or {})}
            u["caixa"] = float(u["regras"]["capital_inicial"])
            for k in ("avisos", "precos_mostrados", "talao", "print"):
                u.pop(k, None)
            return out + [_tirar_botoes(cq)] + _boas_vindas(chat, u["nome"], cfg.get("aviso", ""))
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
        sinal = _sinal(hist, ticker, data)
        if not sinal:
            return out + [_msg(chat, f"Ainda não tenho o sinal de {ticker}. Tenta outra vez daqui a 1 minuto.")]
        preco = (u.get("precos_mostrados") or {}).get(chave, sinal["entrada"])
        return out + [_abrir_compra(chat, u, ticker, preco, data)]
    if acao == "b":
        return out + _callback_talao(chat, u, cq, partes, hist, hoje)
    if ticker not in u["posicoes"]:
        return out + [_tirar_botoes(cq), _msg(chat, f"Já não tens {ticker} na carteira.")]
    if acao in ("v", "w"):
        p = u["posicoes"][ticker]
        preco = cart.numero(partes[2]) if acao == "v" else (_cotacoes([ticker]).get(ticker) or p["preco"])
        abrir_talao(u, "venda", ticker, preco, p["qtd"])
        return out + [msg_talao(chat, u)]
    if acao == "m":
        u["posicoes"][ticker]["acompanhar"] = False
        return out + [_tirar_botoes(cq), _msg(chat, f"🔒 Mantens {ticker}. Deixo de avisar; quando venderes, "
                                                    f"carrega em 🔴 Vendi no menu.")]
    return out


# ---------- mensagens enviadas pelos sinais (diario.py) ----------

def mensagens_sinais(estado: dict | None, novos: list[dict], cabecalho: str, dono: str | None,
                     hist: pd.DataFrame | None = None, hoje: str = "", trocas_avisadas: set | None = None) -> list:
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
        out += [(chat, texto, b) for texto, b in _ronda(u, novos, cabecalho, hist, hoje, trocas_avisadas, chat)]
    return out


def _confianca(s: dict) -> float:
    try:
        v = float(s.get("pontuacao"))
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if pd.isna(v) else v


def _retorno(s: dict) -> float | None:
    """Retorno esperado do sinal, já sem custos (0,012 = +1,2%). O estudo passo3f_forca.py mostrou que,
    entre sinais do mesmo dia, é isto que escolhe melhor (a pontuação técnica não)."""
    for k in ("retorno_esperado_liquido", "retorno_esperado"):
        v = _num(s.get(k))
        if v is not None:
            return v
    return None


def _txt_ret(s: dict) -> str:
    r = _retorno(s)
    return _pct(r) if r is not None else "?"


def taxas_setup(hist: pd.DataFrame | None) -> dict:
    """{setup: (n fechados, % que ganharam)} do registo real (sinais em papel), só compras."""
    if hist is None or hist.empty:
        return {}
    f = acompanhamento._fechados_compra(hist)
    return {k: (len(g), float((g["ret"] > 0).mean())) for k, g in f.groupby("setup")}


def linha_numeros(s: dict, taxas: dict, extra: str = "") -> str:
    """📋 Este tipo de sinal ganhou 64% de 45 vezes · dia 1 de 5. O lucro esperado e a confiança já estão
    na linha 📊 do sinal (sinal_simples)."""
    partes = []
    n, taxa = taxas.get(s.get("setup"), (0, None))
    if n >= 10:
        partes.append(f"este tipo de sinal ganhou {taxa * 100:.0f}% de {n} vezes")
    txt = " · ".join(partes + ([extra] if extra else []))
    return "📋 " + txt[:1].upper() + txt[1:] if txt else ""


def escolher(u: dict, sinais: list[dict]) -> tuple[list, list, int]:
    """Sinais que servem a esta carteira, do melhor para o pior, partidos em (os que cabem nos lugares
    livres, os que ficam de fora, lugares livres). A ordem é a da lista: equilíbrio entre ganho esperado e
    probabilidade de ganhar (lista_sinais.ordenar, estudo passo3j_ordem.py)."""
    elegiveis = [s for s in sinais if cart.mercado(s["ticker"]) in u["regras"]["mercados"]
                 and s["ticker"] not in u["posicoes"]]
    elegiveis = lista_sinais.ordenar(elegiveis, _cfg_sinais())
    livres = max(u["regras"]["max_posicoes"] - len(u["posicoes"]), 0)
    return elegiveis[:livres], elegiveis[livres:], livres


AVISOS_BASE = {"som": True, "ret_min": 0.0, "fora": True, "manha": True}
RET_OPCOES = [0.0, 0.005, 0.01, 0.015]  # retorno esperado mínimo: qualquer, +0,5%, +1%, +1,5%


def _seguinte_ret(v: float) -> float:
    i = min(range(len(RET_OPCOES)), key=lambda k: abs(RET_OPCOES[k] - v))
    return RET_OPCOES[(i + 1) % len(RET_OPCOES)]


def avisos(u: dict) -> dict:
    return {**AVISOS_BASE, **(u.get("avisos") or {})}


def sem_som(estado: dict | None, chat: str) -> bool:
    """Sinais sem som para quem desligou o som (os avisos de venda tocam sempre)."""
    u = (estado or {}).get("utilizadores", {}).get(str(chat))
    return bool(u) and not avisos(u)["som"]


def texto_avisos(u: dict) -> tuple[str, list]:
    a = avisos(u)
    txt = ("🔔 <b>Avisos</b>\n"
           f"• Som nos sinais: <b>{'ligado' if a['som'] else 'desligado'}</b> (os avisos de venda tocam sempre)\n"
           f"• Retorno esperado mínimo: <b>{_pct(a['ret_min']) if a['ret_min'] else 'qualquer'}</b> "
           "(sinais abaixo disto não te chegam)\n"
           f"• Sinais que não cabem na carteira: <b>{'mostrar numa linha' if a['fora'] else 'não mostrar'}</b>\n"
           f"• Resumo da manhã (antes de Nova Iorque abrir): <b>{'ligado' if a['manha'] else 'desligado'}</b>\n"
           "Carrega para mudar:")
    seguinte = _seguinte_ret(a["ret_min"])
    return txt, [[("🔕 Desligar som" if a["som"] else "🔔 Ligar som", "g|som")],
                 [(f"📊 Retorno mínimo → {_pct(seguinte) if seguinte else 'qualquer'}", "g|ret")],
                 [("🙈 Esconder os que não cabem" if a["fora"] else "👀 Mostrar os que não cabem", "g|fora")],
                 [("🌅 Desligar resumo da manhã" if a["manha"] else "🌅 Ligar resumo da manhã", "g|manha")]]


def _ronda(u: dict, sinais: list[dict], cabecalho: str, hist: pd.DataFrame | None = None, hoje: str = "",
           trocas_avisadas: set | None = None, chat: str = "") -> list:
    """Cabeçalho + os melhores sinais (com botões) + uma linha com os que ficaram de fora.
    Com a carteira cheia: propõe trocar a posição mais fraca pelo melhor sinal, se valer a pena.
    Respeita as preferências de avisos da pessoa (retorno mínimo, mostrar os que não cabem)."""
    pref = avisos(u)
    if pref["ret_min"]:
        sinais = [s for s in sinais if (_retorno(s) or 0.0) >= pref["ret_min"] - 1e-9]
    top, resto, livres = escolher(u, sinais)
    if not top and not resto:
        return []
    r = u["regras"]
    ocupadas = f"{len(u['posicoes'])}/{r['max_posicoes']}"
    nomes = ", ".join(f"{s['ticker']} ({_txt_ret(s)})" for s in resto[:8])
    nomes += f" e mais {len(resto) - 8}" if len(resto) > 8 else ""
    if not top and _gestao_no_bot():
        # As trocas são propostas pela Carteira (bot_carteira.gerir), que guarda as já feitas.
        txt = f"\n💼 A tua carteira está cheia ({ocupadas}). Não entraram: {nomes}. Se compensar trocar, aviso-te à parte."
        return [(cabecalho + txt, None)] if pref["fora"] else []
    if not top:
        troca = proposta_troca(u, resto[0], hist, hoje)
        if troca:
            if trocas_avisadas is not None:  # para mensagens_trocas não a repetir na corrida seguinte
                fraca = posicao_mais_fraca(u, hist, hoje)
                trocas_avisadas.add(f"troca|{chat}|{fraca['ticker']}|{resto[0]['ticker']}|{resto[0]['data_sinal']}")
            return [(cabecalho + f"\n💼 A tua carteira está cheia ({ocupadas}), mas há um sinal melhor do que "
                                 f"uma das tuas posições.", None), troca]
        fraca = posicao_mais_fraca(u, hist, hoje)
        txt = (f"\n💼 A tua carteira está cheia ({ocupadas}). Não entraram: {nomes}.")
        if fraca and fraca["falta"] is not None:
            txt += (f"\nA tua posição mais fraca é {fraca['ticker']} (ainda costuma dar {_pct(fraca['falta'])}); "
                    f"nenhum sinal novo rende o suficiente a mais para pagar o spread da troca.")
        else:
            txt += "\nNão proponho troca: as tuas posições ainda são recentes ou não vêm de um sinal."
        return [(cabecalho + txt, None)] if pref["fora"] else []
    extra = (f"\nPara ti: {'o melhor' if len(top) == 1 else f'os {len(top)} melhores'} "
             f"(tens {livres} lugar{'es' if livres != 1 else ''} livre{'s' if livres != 1 else ''}, carteira {ocupadas}).")
    out = [(cabecalho + extra, None)] + _sinais_para(u, top, len(top) + len(resto), hist)
    if resto and pref["fora"]:
        out.append((f"➖ Ficaram de fora {len(resto)} com menos retorno esperado: {nomes}.", None))
    return out


# ---------- troca pela posição mais fraca ----------

def _cotacoes(tickers: list[str]) -> dict:
    if COTAR is None or not tickers:
        return {}
    try:
        return COTAR(sorted(set(tickers))) or {}
    except Exception:  # noqa: BLE001  sem cotação, usa-se o preço de compra
        return {}


def _conf_prazo(t: str, p: dict, hist: pd.DataFrame | None) -> tuple[float, int]:
    """Confiança e prazo (dias úteis) do sinal que originou a posição."""
    if hist is not None and p.get("sinal") and "|" in p["sinal"]:
        h = hist[(hist["ticker"] == t) & (hist["data_sinal"].astype(str) == p["sinal"].split("|")[1])]
        if not h.empty:
            return (_num(h.iloc[-1].get("pontuacao")) or CONF_SEM_SINAL,
                    int(_num(h.iloc[-1].get("max_dias")) or 5))
    return CONF_SEM_SINAL, 5


def _pct(x: float, sinal: bool = True) -> str:
    return (cart.sinal_fmt if sinal else cart.fmt)(x * 100, 1) + "%"


def _cfg_sinais() -> dict:
    global CFG_SINAIS
    if CFG_SINAIS is None:
        import yaml
        try:
            CFG_SINAIS = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text())["sinais"]
        except (OSError, KeyError, TypeError):
            CFG_SINAIS = {}
    return CFG_SINAIS


def _gestao_no_bot() -> bool:
    """config.yaml gestao.no_bot: a gestão das posições (trocas, saídas, resultados) é feita pela Carteira."""
    import yaml
    try:
        cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return False
    return bool((cfg.get("gestao") or {}).get("no_bot"))


def _modelo_trocas() -> dict | None:
    global MODELO_TROCAS
    if MODELO_TROCAS is None:
        MODELO_TROCAS = trocas.carregar() or {}
    return MODELO_TROCAS or None


def _linha_sinal(t: str, p: dict, hist: pd.DataFrame | None) -> dict | None:
    if hist is not None and p.get("sinal") and "|" in p["sinal"]:
        h = hist[(hist["ticker"] == t) & (hist["data_sinal"].astype(str) == p["sinal"].split("|")[1])]
        if not h.empty:
            return h.iloc[-1].to_dict()
    return None


def forcas(u: dict, hist: pd.DataFrame | None, hoje: str) -> list[dict]:
    """O que cada posição aberta ainda costuma render até à saída ("falta", src/trocas.py): posições
    iguais (mesmo setup, dias já passados e ganho em ATR) no backtest. Da mais fraca para a mais forte;
    sem dados (compra à mão, ou comprada hoje) fica no fim e não se troca."""
    px, modelo = _cotacoes(list(u["posicoes"])), _modelo_trocas()
    out = []
    for t, p in u["posicoes"].items():
        usados = int(np.busday_count(p.get("data") or hoje, hoje)) if hoje and p.get("data") else 0
        preco = px.get(t) or p["preco"]
        ganho = preco / p["preco"] - 1
        sinal = _linha_sinal(t, p, hist)
        falta = None
        if modelo and sinal and isinstance(sinal.get("setup"), str):
            atr = trocas.atr_pct(sinal["setup"], sinal.get("entrada"), sinal.get("stop"), _cfg_sinais())
            falta = trocas.valor_que_falta(sinal["setup"], usados, ganho, atr, modelo)
        out.append({"ticker": t, "usados": usados, "preco": preco, "pct": ganho * 100, "falta": falta})
    return sorted(out, key=lambda x: (x["falta"] is None, x["falta"] or 0, u["posicoes"][x["ticker"]].get("data", "")))


def _ler_cotacoes() -> dict:
    try:
        return (LER_COTACOES() if LER_COTACOES else None) or {}
    except Exception:  # noqa: BLE001
        return {}


def _hora_lisboa(iso: str) -> str:
    try:
        return pd.Timestamp(iso).tz_convert("Europe/Lisbon").strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


def carteira_agora(u: dict, hist: pd.DataFrame, hoje: str) -> str:
    """💼 Carteira com as posições ao preço de agora, o prazo de cada sinal e o stop/objetivo já tocado."""
    prazos = {t: _conf_prazo(t, p, hist)[1] for t, p in u["posicoes"].items()}
    dados = _ler_cotacoes()
    velas = dados.get("acoes", {})
    saidas = {t: s for t, p in u["posicoes"].items()
              if (s := cotacoes.saida_atingida(p, velas.get(t, {}).get("velas", [])))}
    return cart.formatar_carteira(u, _cotacoes(list(u["posicoes"])), hoje, prazos, saidas,
                                  _hora_lisboa(dados.get("hora", "")))


def posicao_mais_fraca(u: dict, hist: pd.DataFrame | None, hoje: str) -> dict | None:
    f = forcas(u, hist, hoje)
    return f[0] if f else None


def proposta_troca(u: dict, s: dict, hist: pd.DataFrame | None, hoje: str) -> tuple | None:
    """(texto, botões) a propor vender a posição mais fraca para comprar o sinal `s`, ou None."""
    fraca = posicao_mais_fraca(u, hist, hoje)
    if not fraca or not u["posicoes"][fraca["ticker"]].get("acompanhar", True):
        return None
    modelo = _modelo_trocas()
    novo = _num(s.get("forca"))
    trocar, _ = trocas.vale_trocar(s.get("setup"), fraca["falta"], modelo, novo) if modelo else (False, None)
    if not trocar:
        return None
    novo = novo if novo is not None else modelo["media"][s["setup"]]
    # Simular a carteira depois da venda para dizer quanto comprar do sinal novo.
    depois = copy.deepcopy(u)
    pos = depois["posicoes"].pop(fraca["ticker"])
    depois["caixa"] += pos["qtd"] * fraca["preco"] * (1 - u["regras"]["custo_pct"] / 100)
    t = cart.tamanho(depois, s["ticker"], s["entrada"])
    if t["motivo"]:
        return None
    rank = linha_numeros(s, taxas_setup(hist))
    texto = (f"🔁 <b>Proposta de troca</b>\n"
             f"Vender <b>{fraca['ticker']}</b> a ~{cart.fmt(fraca['preco'])} ({cart.sinal_fmt(fraca['pct'], 1)}%).\n"
             + (f"📊 Um sinal como o novo costuma render <b>{_pct(novo)}</b>; " if not s.get("dias_usados") else
                f"📊 A {s['ticker']} ainda costuma render <b>{_pct(novo)}</b> até à saída; ")
             + f"a {fraca['ticker']} ainda costuma dar <b>{_pct(fraca['falta'])}</b> até à saída. "
             f"A diferença paga o spread da troca (~{_pct(modelo['custo'], False)}).\n"
             f"Para comprar:\n\n" + sinal_simples(depois, s, t, depois["caixa"] - t["valor"], rank)
             + "\n\nFizeste a troca na XTB? Registo as duas operações ao preço de agora.")
    dados = f"s|{fraca['ticker']}|{s['ticker']}|{s['data_sinal']}"
    return texto, [[("✅ Troquei", dados), ("❌ Manter", f"n|{s['ticker']}|{s['data_sinal']}")],
                   [("ℹ️ Porquê este sinal?", f"q|{s['ticker']}|{s['data_sinal']}")]]


# ---------- talão: confirmar preço e quantidade com setas ----------

def _passos_preco(p: float) -> tuple[float, float]:
    return (0.01, 0.10) if p < 20 else (0.05, 0.50) if p < 200 else (0.10, 1.00)


def _passos_qtd(q: float) -> tuple[float, float]:
    return (0.001, 0.01) if q < 1 else (0.01, 0.1) if q < 10 else (0.1, 1.0)


def _passos_valor(v: float) -> tuple[float, float]:
    return (0.01, 0.10) if v < 20 else (0.10, 1.0) if v < 200 else (1.0, 10.0) if v < 2000 else (10.0, 100.0)


def _acertar_talao(b: dict, mudou: str) -> None:
    """O talão guarda preço, valor (o que pagaste/recebes) e quantidade, sempre coerentes:
    mudar o preço mantém o valor (ou a quantidade, se é "vendi tudo"); mudar o valor muda a quantidade."""
    b.setdefault("valor", round(b["preco"] * b["qtd"], 2))
    if mudou == "q" or (mudou != "v" and (b.get("tudo") or b.get("fixa"))):
        b["valor"] = round(b["preco"] * b["qtd"], 2)
    else:
        b["qtd"] = round(b["valor"] / b["preco"], 6) if b["preco"] > 0 else 0.0


def abrir_talao(u: dict, tipo: str, ticker: str, preco: float, qtd: float, data: str | None = None,
                troca: dict | None = None) -> dict:
    """Talão por confirmar: o preço e a quantidade sugeridos, para acertar com o que saiu na corretora."""
    u["aguarda"] = None
    p = u["posicoes"].get(ticker) if tipo == "venda" else None
    tudo = bool(p) and abs(float(qtd) - p["qtd"]) < 1e-9
    valor = round(float(preco) * float(qtd), 2)
    u["talao"] = {"id": secrets.token_hex(3), "tipo": tipo, "ticker": ticker, "preco": round(float(preco), 4),
                  "qtd": float(qtd) if tudo else round(float(qtd), 6), "valor": valor, "tudo": tudo,
                  "data": data, "troca": troca, "msg": None,
                  "pp": _passos_preco(float(preco)), "pq": _passos_qtd(float(qtd) or 1.0),
                  "pv": _passos_valor(valor or 1.0), "campo": "p",
                  "p_compra": p["preco"] if p else None, "pct": u["regras"]["custo_pct"]}
    return u["talao"]


def _lucro_talao(b: dict) -> float | None:
    """Lucro da venda no talão (já sem taxas); None se não for uma venda de uma posição que tens."""
    if b["tipo"] != "venda" or not b.get("p_compra"):
        return None
    return b["qtd"] * (b["preco"] * (1 - b.get("pct", 0) / 100) - b["p_compra"])


def _por_lucro(b: dict, lucro: float) -> None:
    """Venda pelo lucro final: a quantidade fica e o preço sai do lucro."""
    q, pc, k = b["qtd"], b["p_compra"], 1 - b.get("pct", 0) / 100
    b["preco"] = round(max((lucro / q + pc) / k, 0.0001), 4)
    _acertar_talao(b, "q")


def _valor_campo(b: dict) -> float:
    return {"p": b["preco"], "v": b["valor"]}.get(b.get("campo", "p")) if b.get("campo", "p") != "l" \
        else (_lucro_talao(b) or 0.0)


def _digitos(x: float) -> list[tuple[str, int | None]]:
    """406,46 -> [("4", 2), ("0", 1), ("6", 0), (",", None), ("4", -1), ("6", -2)]"""
    inteiro, dec = f"{abs(x):.2f}".split(".")
    n = len(inteiro)
    return [(c, n - 1 - i) for i, c in enumerate(inteiro)] + [(",", None)] + [(c, -1 - i) for i, c in enumerate(dec)]


def _mexer_digito(b: dict, pot: int, sentido: int) -> None:
    campo = b.get("campo", "p")
    passo = sentido * 10 ** pot
    if campo == "l":
        atual = _lucro_talao(b) or 0.0
        sinal = -1 if atual < 0 else 1
        _por_lucro(b, sinal * round(max(abs(atual) + passo, 0.0), 2))
        return
    k = "preco" if campo == "p" else "valor"
    novo = round(b[k] + passo, 2)
    if novo < 0.01:
        return  # não desce abaixo de 0,01
    b[k] = novo
    if campo == "v":
        b["tudo"] = b["fixa"] = False
    _acertar_talao(b, campo)


def _abrir_compra(chat: str, u: dict, ticker: str, preco: float, data: str | None,
                  troca: dict | None = None) -> tuple[str, dict]:
    t = cart.tamanho(u, ticker, preco)
    qtd = t["qtd"] if not t["motivo"] else math.floor(
        cart.valor_total(u) * u["regras"]["pct"] / 100 / preco * 1e4) / 1e4
    abrir_talao(u, "compra", ticker, preco, qtd, data, troca)
    return msg_talao(chat, u)


def texto_talao(u: dict) -> str:
    b, moeda = u["talao"], u["regras"]["moeda"]
    _acertar_talao(b, "")
    t, preco, qtd = b["ticker"], b["preco"], b["qtd"]
    total = b["valor"] if not b.get("tudo") else preco * qtd
    titulo = ("🟢 Compra" if b["tipo"] == "compra" else "🔴 Venda") + f" de <b>{t}</b>"
    if b["troca"]:
        titulo += f" (troca, passo {1 if b['tipo'] == 'venda' else 2} de 2)"
    venda_lucro = b["tipo"] == "venda" and _lucro_talao(b) is not None
    if venda_lucro:
        linhas = [f"🧾 {titulo}", "", f"📦 {cart.fmt_qtd(qtd)} ações · 💲 preço de venda <b>{cart.fmt(preco)}</b>"]
    else:
        linhas = [f"🧾 {titulo}", "",
                  f"💲 Preço: <b>{cart.fmt(preco)}</b>",
                  f"💵 Valor: <b>{cart.fmt(total)} {moeda}</b>"
                  + ("  (tudo o que tens)" if b.get("tudo") else ""),
                  f"📦 Quantidade: {cart.fmt_qtd(qtd)}  (valor ÷ preço)"]
    pct = u["regras"]["custo_pct"]
    taxa = total * pct / 100
    if not venda_lucro:
        linhas.append(f"🏦 Taxas: {cart.fmt(taxa)} {moeda}"
                      + ("  (XTB: 0% até 100 mil €/mês, conta USD sem câmbio)" if pct == 0 else f"  ({cart.fmt(pct)}%)"))
    if b["tipo"] == "compra":
        linhas.append(f"Pagas: <b>{cart.fmt(total + taxa)} {moeda}</b>")
        livre = u["caixa"] - total - taxa
        linhas.append(f"Livre depois: {cart.fmt(livre)} {moeda}" + (" ⚠️ mais do que tens livre" if livre < -1e-6 else ""))
    else:
        p = u["posicoes"].get(t)
        if p:
            linhas.append(f"Recebes: <b>{cart.fmt(total - taxa)} {moeda}</b>")
            lucro = qtd * (preco - p["preco"]) - taxa
            linhas.append(f"Resultado: <b>{cart.sinal_fmt(lucro)} {moeda}</b> "
                          f"({cart.sinal_fmt((preco / p['preco'] - 1) * 100, 1)}%)"
                          + (" ⚠️ mais do que tens" if qtd > p["qtd"] * 1.01 + 1e-9 else ""))
    if venda_lucro:
        linhas.append("\nDiz-me o lucro (ou prejuízo) que a XTB te deu: eu calculo o resto. Depois ✅ Confirmar.")
    else:
        linhas.append("\nAcerta o preço e o valor pago até ficarem iguais aos da XTB. Depois ✅ Confirmar.")
    return "\n".join(linhas)


def botoes_talao(b: dict) -> list:
    """Escolhe o que acertar (preço, valor ou, na venda, o lucro) e acerta-o dígito a dígito com ▲ e ▼."""
    _acertar_talao(b, "")
    campo = b.get("campo", "p")
    nomes = [("p", "💲 Preço"), ("v", "💵 Valor")] + ([("l", "💰 Lucro")] if _lucro_talao(b) is not None else [])
    linhas = [[(("✅ " if c == campo else "") + nome, f"b|c|{c}") for c, nome in nomes]]
    dig = _digitos(_valor_campo(b))
    linhas.append([("▲", f"b|d|{p}|1") if p is not None else ("·", "b|n") for _, p in dig])
    linhas.append([(c, "b|n") for c, _ in dig])
    linhas.append([("▼", f"b|d|{p}|-1") if p is not None else ("·", "b|n") for _, p in dig])
    extra = [("✏️ Escrever", f"b|e|{campo}")]
    if campo == "l":
        extra.append(("➖ Perda" if (_lucro_talao(b) or 0) >= 0 else "➕ Ganho", "b|s"))
    if b["tipo"] == "venda" and not b.get("tudo"):
        extra.append(("📦 Vendi tudo", "b|t"))
    linhas.append(extra)
    linhas.append([("✅ Confirmar", "b|ok"), ("❌ Cancelar", "b|x")])
    return linhas


T_OK, T_X = "✅ Confirmar", "❌ Cancelar"
T_PRECO, T_VALOR, T_LUCRO = "✏️ Escrever preço", "✏️ Escrever valor", "✏️ Escrever lucro"
BOTOES_TALAO = {T_OK: "ok", T_X: "x", T_PRECO: "p", T_VALOR: "v", T_LUCRO: "l"}


def url_talao(u: dict) -> str:
    """Endereço da página do talão com os números de agora (abre dentro do Telegram)."""
    from urllib.parse import urlencode
    b = u["talao"]
    _acertar_talao(b, "")
    q = {"tipo": b["tipo"], "t": b["ticker"], "id": b["id"], "m": u["regras"]["moeda"], "pct": u["regras"]["custo_pct"]}
    if b["tipo"] == "compra":
        q.update(p=b["preco"], v=round(b["valor"], 2), livre=round(u["caixa"], 2))
    else:
        q.update(l=round(_lucro_talao(b) or 0.0, 2), q=cart.fmt_qtd(b["qtd"]).replace(",", "."),
                 pc=b.get("p_compra") or b["preco"])
    return f"{TALAO_URL}?{urlencode(q)}"


def teclado_talao(u: dict) -> str:
    """Teclado de baixo enquanto há um talão aberto: a página com caixas (se houver) e escrever à mão."""
    b = u["talao"]
    venda = b["tipo"] == "venda" and _lucro_talao(b) is not None
    linhas = []
    if TALAO_URL:
        linhas.append([{"text": "🧾 Dizer o lucro" if venda else "🧾 Acertar preço e valor",
                        "web_app": {"url": url_talao(u)}}])
    linhas.append([{"text": T_LUCRO}] if venda else [{"text": T_PRECO}, {"text": T_VALOR}])
    linhas.append([{"text": T_OK}, {"text": T_X}])
    return json.dumps({"keyboard": linhas, "resize_keyboard": True, "is_persistent": True})


def msg_talao(chat: str, u: dict) -> tuple[str, dict]:
    m, p = _msg(chat, texto_talao(u))
    p["reply_markup"] = teclado_talao(u)
    return m, p


def _do_formulario(estado: dict, chat: str, u: dict, dados: str, hist, hoje: str) -> list:
    """Números que vêm da página do talão (Telegram WebApp sendData): acerta o talão e confirma."""
    b = u.get("talao")
    try:
        d = json.loads(dados)
    except ValueError:
        d = {}
    if not b or d.get("id") != b["id"] or str(d.get("t", "")).upper() != b["ticker"]:
        return [_msg(chat, "Este talão já foi fechado. Abre outro pelo sinal ou pelo menu.", menu=True)]
    try:
        if b["tipo"] == "venda" and "l" in d and _lucro_talao(b) is not None:
            _acertar_talao(b, "")
            _por_lucro(b, float(d["l"]))
        else:
            p, v = float(d["p"]), float(d["v"])
            if p <= 0 or v <= 0:
                raise ValueError
            b.update(preco=round(p, 4), valor=round(v, 2), tudo=False, fixa=False)
            _acertar_talao(b, "v")
    except (KeyError, TypeError, ValueError):
        return [_msg(chat, "Não percebi os números do talão. Tenta outra vez.")]
    return _callback_talao(chat, u, None, ["b", "ok"], hist, hoje)


def _editar(cq: dict, texto: str, botoes: list | None) -> tuple[str, dict]:
    m = cq.get("message", {})
    return "editMessageText", {"chat_id": str(m.get("chat", {}).get("id", "")), "message_id": m.get("message_id"),
                               "text": texto, "parse_mode": "HTML",
                               "reply_markup": telegram.teclado(botoes) if botoes else json.dumps({"inline_keyboard": []})}


def _callback_talao(chat: str, u: dict, cq: dict, partes: list, hist, hoje: str) -> list:
    b = u.get("talao")
    if not b:
        return [_tirar_botoes(cq), _msg(chat, "Este talão já foi fechado.")]
    b["msg"] = cq.get("message", {}).get("message_id") if cq else None

    def mostrar(txt):  # botão inline antigo: edita o talão; teclado de baixo: mensagem nova com o menu
        return _editar(cq, txt, None) if cq else _msg(chat, txt, menu=True)
    op = partes[1] if len(partes) > 1 else ""
    if op in ("p", "q", "v"):
        _acertar_talao(b, "")
        k = int(partes[2])
        passos = {"p": b["pp"], "q": b["pq"], "v": b.get("pv") or _passos_valor(b["valor"] or 1.0)}[op]
        passo = passos[abs(k) - 1] * (1 if k > 0 else -1)
        campo = {"p": "preco", "q": "qtd", "v": "valor"}[op]
        b[campo] = round(max(b[campo] + passo, 0.01 if op == "p" else 0.0), 4)
        if op != "p":
            b["tudo"] = False
        _acertar_talao(b, op)
        return [_editar(cq, texto_talao(u), botoes_talao(b))]
    if op == "n":
        return []
    if op == "c" and len(partes) > 2:
        b["campo"] = partes[2] if partes[2] in ("p", "v", "l") else "p"
        return [_editar(cq, texto_talao(u), botoes_talao(b))]
    if op == "d" and len(partes) > 3:
        _acertar_talao(b, "")
        _mexer_digito(b, int(partes[2]), 1 if int(partes[3]) > 0 else -1)
        return [_editar(cq, texto_talao(u), botoes_talao(b))]
    if op == "s" and _lucro_talao(b) is not None:
        _por_lucro(b, -(_lucro_talao(b) or 0.0))
        return [_editar(cq, texto_talao(u), botoes_talao(b))]
    if op == "t" and b["ticker"] in u["posicoes"]:
        b["qtd"], b["tudo"] = u["posicoes"][b["ticker"]]["qtd"], True
        _acertar_talao(b, "q")
        return [_editar(cq, texto_talao(u), botoes_talao(b))]
    if op == "e":
        u["aguarda"] = {"tipo": "talao", "campo": partes[2]}
        pede = {"p": "o preço (ex. <code>51,20</code>)", "v": "o valor em USD que a XTB mostra (ex. <code>20,15</code>)",
                "q": "o número de ações (ex. <code>0,4</code>)",
                "l": "o lucro em USD que a XTB te deu (ex. <code>0,21</code>, ou <code>-1,30</code> se perdeste)"
                }.get(partes[2], "o número")
        return [_msg(chat, f"✏️ Escreve {pede}.")]
    if op == "x":
        u["talao"] = None
        txt = "❌ Cancelado, não registei nada."
        if b["troca"] and b["tipo"] == "compra":
            txt = f"❌ A compra de {b['ticker']} ficou por registar; a venda já está registada."
        return [mostrar(txt)]
    if op != "ok":
        return []
    _acertar_talao(b, "")
    t, preco, qtd = b["ticker"], b["preco"], b["qtd"]
    if qtd <= 0:
        return [_msg(chat, "O valor tem de ser maior que zero.")]
    p = u["posicoes"].get(t)
    if b["tipo"] == "venda" and p and qtd > p["qtd"] and qtd <= p["qtd"] * 1.01:
        qtd = p["qtd"]  # o valor da XTB vem arredondado ao cêntimo: é tudo o que tens
    if b["tipo"] == "venda":
        if t not in u["posicoes"]:
            u["talao"] = None
            return [mostrar(f"Já não tens {t} na carteira.")]
        antes = dict(u["posicoes"][t])
        txt = cart.vender(u, t, qtd, preco, hoje)
        u["talao"] = None
        idx = _anotar_venda(u, t, antes, hist, "troca" if b["troca"] else None)
        if not b["troca"]:
            return [mostrar(txt)] + ([pergunta_motivo(chat, t, idx)] if idx is not None else [])
        # troca: a seguir, o talão da compra (o ↩️ Desfazer anula as duas)
        compra, data = b["troca"]["compra"], b["troca"]["data"]
        sinal = _sinal(hist, compra, data)
        px = _cotacoes([compra]).get(compra) or (sinal or {}).get("entrada")
        if not px:
            return [mostrar(txt + f"\nNão tenho o preço de {compra}; regista a compra com 🟢 Comprei.")]
        return [mostrar(txt),
                _abrir_compra(chat, u, compra, px, data, troca={"anterior": u["anterior"], "venda": txt})]
    sinal = _sinal(hist, t, b["data"]) if b["data"] else None
    txt = cart.comprar(u, t, qtd, preco, hoje, sinal)
    if t not in u["posicoes"]:  # não registou (ex. sem dinheiro livre): o talão fica aberto
        return [_msg(chat, txt + "\nAcerta o valor no talão e confirma outra vez.")]
    u["talao"] = None
    _anotar_compra(u, t, preco, b["data"], sinal, hist, hoje)
    if b["troca"]:
        u["anterior"] = b["troca"]["anterior"]
        txt = f"🔁 Troca registada.\n{b['troca']['venda']}\n{txt}"
    return [mostrar(txt)]


# ---------- diário de trades (src/diario_trades.py) ----------

def _anotar_compra(u: dict, t: str, preco: float, data: str | None, sinal: dict | None, hist, hoje: str) -> None:
    """Regista, sem perguntar, se a compra foi acima do preço máximo de compra da lista."""
    if not data or not sinal:
        return
    s = next((x for x in _avaliados(u, hist, hoje) if x["ticker"] == t and x["data_sinal"] == data), None)
    lim = _num((s or {}).get("limite")) if s else limite_compra(sinal)
    op = u["operacoes"][-1] if u["operacoes"] else {}
    if lim and preco > lim + 0.005 and op.get("tipo") == "compra" and op.get("ticker") == t:
        op["acima_limite"] = u["posicoes"][t]["acima_limite"] = True


def _anotar_venda(u: dict, t: str, p: dict, hist, motivo: str | None) -> int | None:
    """Junta à venda o sinal da compra, se a compra foi acima do limite e se a venda foi antes da saída do
    modelo (o sinal ainda aberto no registo em papel). Devolve a posição da venda nas operações."""
    if not u["operacoes"] or u["operacoes"][-1].get("tipo") != "venda" or u["operacoes"][-1].get("ticker") != t:
        return None
    op = u["operacoes"][-1]
    op.update(sinal=p.get("sinal"), acima_limite=bool(p.get("acima_limite")),
              antes_modelo=diario_trades.ainda_aberto(hist, p.get("sinal")))
    if motivo:
        op["motivo"] = motivo
    return len(u["operacoes"]) - 1


def pergunta_motivo(chat: str, t: str, idx: int) -> tuple[str, dict]:
    m = diario_trades.MOTIVOS
    k = diario_trades.PERGUNTA
    botoes = [[(m[a], f"mv|{idx}|{t}|{a}") for a in k[i:i + 2]] for i in range(0, len(k), 2)]
    return _msg(chat, f"📝 Porque vendeste <b>{t}</b>? Fica no teu diário de trades e conta no resumo de sexta "
                      f"(tu vs modelo).", botoes)


def _venda_do_diario(u: dict, idx: str, t: str) -> dict | None:
    try:
        op = u["operacoes"][int(idx)]
    except (ValueError, IndexError):
        return None
    return op if op.get("tipo") == "venda" and op.get("ticker") == t else None


def diario(u: dict, hist, desde: str = "") -> str | None:
    return diario_trades.texto(u, hist, desde)


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(v) else v


def limite_compra(s: dict) -> float | None:
    """Preço máximo a que ainda vale a pena entrar: o mesmo corte que tira dos sinais em aberto os que
    já fizeram mais de metade do caminho até ao objetivo (FUGIU)."""
    if "limite" in s:  # já calculado pela lista (src/lista_sinais.avaliar)
        return _num(s["limite"])
    return lista_sinais.limite(_num(s.get("entrada_sinal")) or _num(s.get("entrada")), s.get("objetivo"),
                               s.get("stop"), s.get("setup"), _cfg_sinais(), s.get("preco_sinal"))


def _atr_sinal(s: dict) -> float | None:
    """ATR em % do preço no dia do sinal: o do sinal (atr_pct) ou, nos do registo, a partir do stop."""
    a = _num(s.get("atr_pct"))
    if a:
        return a / 100 if a > 1 else a
    e = _num(s.get("entrada_sinal")) or _num(s.get("entrada"))
    if not e or not isinstance(s.get("setup"), str) or _num(s.get("stop")) is None:
        return None
    return trocas.atr_pct(s["setup"], e, s["stop"], _cfg_sinais())


LIXO_NOME = re.compile(r"\s*[-,]?\s*(Class [A-Z] )?(Common Stock|Ordinary Shares|Common Shares|American Depositary"
                       r" Shares.*|Depositary Shares.*|Units?|Warrants?)$", re.I)


def nome_empresa(t: str, s: dict | None = None) -> str:
    """Nome da empresa para mostrar ao lado do ticker (listas da Nasdaq), sem "Common Stock" e afins."""
    n = (s or {}).get("nome") if isinstance((s or {}).get("nome"), str) else NOMES.get(t, "")
    n = LIXO_NOME.sub("", str(n or "").split(" - ")[0]).strip(" ,-")
    return n if len(n) <= 40 else n[:38].rstrip() + "…"


def _insiders_texto(s: dict) -> str:
    """Compras de insiders e vendas a descoberto (só informação, não mexe na ordem): o texto pronto que os
    sinais gravam em cada item da lista; nos sinais acabados de chegar, vai-se buscar à lista."""
    txt = s.get("insiders_texto")
    if not isinstance(txt, str):
        item = next((x for x in lista_guardada() or [] if x.get("ticker") == s.get("ticker")
                     and str(x.get("data_sinal", ""))[:10] == str(s.get("data_sinal", ""))[:10]), None)
        txt = (item or {}).get("insiders_texto")
    return txt.strip() if isinstance(txt, str) else ""


def sinal_simples(u: dict, s: dict, t: dict, livre: float, rank: str) -> str:
    """O sinal em linguagem simples: o que comprar, quanto, quando vender e em quanto tempo."""
    moeda, e = u["regras"]["moeda"], s["entrada"]
    obj, stop = _num(s.get("objetivo")), _num(s.get("stop"))
    nome = nome_empresa(s["ticker"], s)
    linhas = [f"🟢 <b>COMPRAR {s['ticker']}</b> a ~{cart.fmt(e)}" + (f"\n🏢 {html.escape(nome)}" if nome else "")]
    limite = limite_compra(s)
    if limite and limite > e:
        linhas.append(f"🔒 Compra só até <b>{cart.fmt(limite)}</b>: acima disso já não compensa")
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
    r_txt = risco.texto(_atr_sinal(s))
    if r_txt:
        linhas.append(r_txt)
    modelo = []
    if _retorno(s) is not None:
        modelo.append(f"lucro esperado <b>{_txt_ret(s)}</b>")
    taxa = _num(s.get("taxa")) or lista_sinais.taxa_ganhar(s, _cfg_sinais())
    if taxa:
        modelo.append(f"confiança <b>{taxa * 100:.0f}%</b>")
    if modelo:
        linhas.append("📊 " + " · ".join(modelo))
    if isinstance(s.get("setup"), str):
        linhas.append(f"🧭 Tipo de sinal: {acompanhamento.SETUPS_PT.get(s['setup'], s['setup'])}")
    if isinstance(s.get("resumo_noticias"), str) and s["resumo_noticias"].strip():
        linhas.append(f"📰 Notícias: {html.escape(s['resumo_noticias'].strip())}")
    redes = tendencias.linha_alerta(s)
    if redes:
        linhas.append(html.escape(redes))
    ins = _insiders_texto(s)
    if ins:
        linhas.append(html.escape(ins))
    dias = int(round(_num(s.get("max_dias")) or _num(s.get("dias_estimados")) or 5))
    linhas.append(f"⏳ Prazo: até {dias} dia{'s' if dias != 1 else ''} út{'eis' if dias != 1 else 'il'}")
    if isinstance(s.get("resultados_em"), str):
        d = s["resultados_em"]
        linhas.append(f"📅 Resultados a {d[8:10]}/{d[5:7]}, dentro do prazo: o preço pode dar um salto. "
                      f"Considera vender antes.")
    elif s.get("risco_evento"):
        linhas.append("⚠️ A empresa apresenta resultados em breve: o preço pode dar um salto, para cima ou para baixo.")
    if isinstance(s.get("argumento_inv"), str) and s.get("argumento_inv"):
        icone = {"rejeitar": "🚩", "duvidoso": "⚠️", "manter": "✔️"}.get(s.get("veredicto_inv"), "🕵️")
        linhas.append(f"{icone} Advogado do diabo: {html.escape(s['argumento_inv'])}")
    if rank:
        linhas.append(rank)
    return "\n".join(linhas)


def _sinais_para(u: dict, sinais: list[dict], total: int | None = None, hist: pd.DataFrame | None = None) -> list:
    """Texto e botões de cada sinal para este utilizador; o dinheiro vai sendo reservado pela ordem."""
    out, reservado, ocupadas, taxas = [], 0.0, 0, taxas_setup(hist)
    for k, s in enumerate(sinais, start=1):
        t = cart.tamanho(u, s["ticker"], s["entrada"], reservado, ocupadas)
        if not t["motivo"]:
            reservado, ocupadas = reservado + t["valor"], ocupadas + 1
        n_total = total or len(sinais)
        rank = linha_numeros(s, taxas, "" if n_total == 1 else
                             "o melhor desta ronda" if k == 1 else f"{k}.º melhor de {n_total}")
        texto = sinal_simples(u, s, t, u["caixa"] - reservado, rank)
        b = None if t["motivo"] in ("fora_mercado", "ja_tem") else botoes_sinal(s["ticker"], s["data_sinal"])
        if b and not t["motivo"]:
            b = b + [botoes_copiar(t["qtd"], s)]
        out.append((texto, b))
    return out


PORQUE = {
    "rompimento": "A ação fechou acima do preço mais alto das últimas 4 semanas, com muito mais volume que o normal, "
                  "e está numa tendência de subida. Quando isto acontece, muitas vezes a subida continua mais uns dias.",
    "pullback": "A ação está numa tendência de subida há meses, mas caiu forte nos últimos 1 ou 2 dias. Nestes casos "
                "costuma recuperar parte da queda em poucos dias. Foi o padrão com melhores resultados até agora.",
    "gap_volume": "A ação abriu com um salto forte e volume muito acima do normal (por exemplo depois de resultados) "
                  "e fechou em alta. Estes saltos às vezes continuam nos dias seguintes.",
    "semana_forte": "A ação subiu 8% ou mais nesta semana, fechou perto do máximo do dia com volume forte e está "
                    "numa tendência de subida. Este impulso costuma continuar mais uns dias.",
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
    if _retorno(s) is not None:
        linhas.append(f"\n📊 <b>Retorno esperado {_txt_ret(s)}</b>, já sem custos: a média do que este padrão "
                      f"rendeu no passado, ajustada ao histórico desta ação. É o que ordena os sinais.")
    n, taxa = taxas_setup(hist).get(s["setup"], (0, None))
    if n >= 10:
        linhas.append(f"✔️ Nos sinais reais deste tipo até hoje, {taxa * 100:.0f}% de {n} ganharam.")
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
    h = h[h["ticker"].map(lambda t: cart.mercado(t) in u["regras"]["mercados"] and t not in u["posicoes"]).astype(bool)]
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
    ronda = _ronda(u, sinais, cab, hist)
    if not ronda:
        return [_msg(chat, "Não há sinais em aberto que ainda valham a pena agora. Aviso-te quando chegar o próximo.",
                     menu=True)]
    return [_msg(chat, texto, b) for texto, b in ronda]


# ---------- 📈 Sinais: a lista reavaliada a cada corrida ----------

def lista_guardada(agora: pd.Timestamp | None = None) -> list[dict] | None:
    """Os itens da lista que os sinais gravaram (data/lista_sinais.json), se tiver menos de LISTA_MAX_MIN."""
    if LER_LISTA is None:
        return None
    try:
        d = LER_LISTA() or {}
        idade = (agora or pd.Timestamp.now(tz="UTC")) - pd.Timestamp(d["gerada_em"])
    except Exception:  # noqa: BLE001  sem ficheiro ou estragado: calcula-se
        return None
    if idade > pd.Timedelta(minutes=LISTA_MAX_MIN) or not isinstance(d.get("itens"), list):
        return None
    return [{k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in s.items()} for s in d["itens"]]


def _avaliados(u: dict, hist: pd.DataFrame | None, hoje: str) -> list[dict]:
    """Sinais em aberto do mercado desta pessoa, com estado e força ao preço de agora: a lista que os
    sinais gravam a cada corrida; se estiver velha, calcula-se aqui (src/lista_sinais.py)."""
    itens = lista_guardada()
    if itens is None:
        if hist is None or hist.empty:
            return []
        abertos = sorted(set(hist.loc[hist["estado"] == "aberto", "ticker"]))
        itens = lista_sinais.avaliar(hist, _cotacoes(abertos), hoje, _modelo_trocas(), _cfg_sinais())
    else:
        itens = _ao_vivo(itens, hist, hoje)
    return [s for s in itens if cart.mercado(s["ticker"]) in u["regras"]["mercados"]]


def _ao_vivo(itens: list[dict], hist: pd.DataFrame | None, hoje: str) -> list[dict]:
    """Com a bolsa aberta, os primeiros sinais da lista ao preço deste momento (a lista gravada pode ter
    até 10 min): volta a avaliar estado, limite e força com esse preço. Sem preço vivo, fica a gravada."""
    if COTAR_VIVO is None:
        return itens
    quais = [s["ticker"] for s in itens if s.get("estado_lista") == "comprar"][:VIVO_MAX]
    try:
        vivos = COTAR_VIVO(quais) if quais else {}
    except Exception:  # noqa: BLE001  sem tempo real, serve a lista dos sinais
        vivos = {}
    if not vivos:
        return itens
    modelo, cfg, out = _modelo_trocas(), _cfg_sinais(), []
    for s in itens:  # a ordem fica a da lista (depende do retorno esperado e da taxa, não do preço)
        if s["ticker"] not in vivos:
            out.append(s)
            continue
        novo = lista_sinais.avaliar_um(s, vivos[s["ticker"]], hoje, modelo, cfg)
        if novo:  # None: tocou no stop ou no objetivo neste momento
            out.append({**novo, "ao_vivo": True})
    return out


def _a_espera_da_abertura(s: dict) -> bool:
    """Sinal formado com a bolsa fechada: a entrada ainda é o último fecho, até à abertura."""
    return str(s.get("entrada_na_abertura")).strip().lower() in ("true", "1", "1.0")


def _compraveis(u: dict, itens: list[dict]) -> list[dict]:
    return [s for s in itens if s["estado_lista"] == "comprar" and s["ticker"] not in u["posicoes"]
            and u["decisoes"].get(cart.chave_sinal(s["ticker"], s["data_sinal"])) != "ignorei"]


def _melhor_troca(u: dict, itens: list[dict], hist, hoje: str) -> tuple | None:
    """(sinal, posição a vender, (texto, botões)) se a carteira está cheia e o melhor sinal que ainda dá
    para comprar rende mais do que a posição mais fraca, já com o spread e a margem."""
    if len(u["posicoes"]) < u["regras"]["max_posicoes"]:
        return None
    melhor = next((s for s in _compraveis(u, itens) if not _a_espera_da_abertura(s)), None)
    fraca = posicao_mais_fraca(u, hist, hoje)
    if not melhor or not fraca:
        return None
    prop = proposta_troca(u, melhor, hist, hoje)
    return (melhor, fraca["ticker"], prop) if prop else None


def _dia(s: dict) -> str:
    if s["dias_usados"] >= s["max_dias"]:
        return "último dia, sai ao fecho"
    return f"dia {s['dias_usados'] + 1} de {s['max_dias']}"


RISCO_COR = {"baixo": "🟢", "médio": "🟡", "alto": "🔴"}


def _esperado(s: dict) -> str:
    """Retorno esperado do sinal (nos antigos sem ele, o que ainda costuma render) e o risco."""
    txt = f"esperado <b>{_txt_ret(s)}</b>" if _retorno(s) is not None else _ainda(s["forca"])
    if _num(s.get("taxa")):
        txt += f" · ganha {s['taxa'] * 100:.0f}%"
    if s.get("risco") and s.get("perda_dia_mau"):
        txt += f" · {RISCO_COR[s['risco']]} dia mau −{s['perda_dia_mau'] * 100:.0f}%"
    return txt


def _ainda(x: float | None) -> str:
    return f"ainda dá <b>{_pct(x)}</b>" if x is not None else "sem dados para medir"


def _linha_compra(k: int, s: dict) -> str:
    """'1. AGL até 88,40 · 🎯 92,00 · 🛑 84,00': o que interessa para comprar, sem os números do modelo."""
    partes = [f"{k}. <b>{s['ticker']}</b>"]
    if s.get("limite"):
        partes.append(f"até {cart.fmt(s['limite'])}")
    o, st = _num(s.get("objetivo")), _num(s.get("stop"))
    regra = saidas.REGRAS.get(s.get("saida") or "")
    partes.append(f"🎯 {cart.fmt(o)}" if o else "🎯 quando recuperar" if regra and regra.sair_se else "")
    partes.append(f"🛑 {cart.fmt(st)}" if st else "")
    if _a_espera_da_abertura(s):
        partes.append("à abertura")
    cab, resto = partes[0], [p for p in partes[1:] if p]
    return f"{cab} " + " · ".join(resto) if resto else cab


MEDALHAS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _cartao_ranking(k: int, s: dict) -> str:
    """Um sinal no ranking, em três linhas: posição e risco; preços para a ordem; o que o modelo espera.
    1. <b>KPLT</b> · 🟢 risco baixo
       💵 até 13,01 · 🎯 15,47 (+19%) · 🛑 11,05 (−15%)
       📊 lucro esperado +1,4% · confiança 68%"""
    cab = f"{MEDALHAS.get(k, f'{k}.')} <b>{s['ticker']}</b>"
    nome = nome_empresa(s["ticker"], s)
    if nome:
        cab += f" <i>{html.escape(nome)}</i>"
    if s.get("risco") in RISCO_COR:
        cab += f" · {RISCO_COR[s['risco']]} risco {s['risco']}"
    if _a_espera_da_abertura(s):
        cab += " · ⏰ à abertura"
    base = _num(s.get("preco")) or _num(s.get("entrada"))
    o, st = _num(s.get("objetivo")), _num(s.get("stop"))
    regra = saidas.REGRAS.get(s.get("saida") or "")
    precos = [f"💵 até {cart.fmt(s['limite'])}" if s.get("limite") else ""]
    if o:
        precos.append(f"🎯 {cart.fmt(o)}" + (f" ({cart.sinal_fmt((o / base - 1) * 100, 0)}%)" if base else ""))
    elif regra and regra.sair_se:
        precos.append("🎯 aviso quando vender")
    if st:
        precos.append(f"🛑 {cart.fmt(st)}" + (f" ({cart.sinal_fmt((st / base - 1) * 100, 0)}%)" if base else ""))
    modelo = []
    if _retorno(s) is not None:
        modelo.append(f"lucro esperado <b>{_txt_ret(s)}</b>")
    if _num(s.get("taxa")):
        modelo.append(f"confiança <b>{s['taxa'] * 100:.0f}%</b>")
    linhas = [cab, "    " + " · ".join(p for p in precos if p)]
    if modelo:
        linhas.append("    📊 " + " · ".join(modelo))
    return "\n".join(linhas)


def _bloco_posicoes(u: dict, hist: pd.DataFrame | None, hoje: str) -> list[str]:
    """💼 As posições que já tens, da que está melhor para a pior: como vão e o que ainda costumam dar."""
    if not u["posicoes"]:
        return []
    r = u["regras"]
    linhas = [f"💼 <b>As tuas posições</b> ({len(u['posicoes'])}/{r['max_posicoes']})"]
    for f in sorted(forcas(u, hist, hoje), key=lambda x: -x["pct"]):
        t, p = f["ticker"], u["posicoes"][f["ticker"]]
        nome = nome_empresa(t)
        prazo = _conf_prazo(t, p, hist)[1]
        partes = [f"{'🟢' if f['pct'] >= 0 else '🔴'} <b>{t}</b>" + (f" <i>{html.escape(nome)}</i>" if nome else "")
                  + f" <b>{cart.sinal_fmt(f['pct'], 1)}%</b>"]
        if p.get("sinal"):
            partes.append("último dia" if f["usados"] + 1 >= prazo else f"dia {f['usados'] + 1} de {prazo}")
        if f["falta"] is not None:
            partes.append(f"ainda dá {_pct(f['falta'])}")
        linhas.append(" · ".join(partes))
    return linhas + [""]


RESTO_MAX = 25  # sinais na lista recolhida (o Telegram corta mensagens acima de 4096 caracteres)


def _linha_resto(k: int, s: dict) -> str:
    """'6. EFOR até 38,27 · +1,2% · 66% · 🔴': uma linha por sinal, na lista recolhida."""
    partes = [f"{k}. <b>{s['ticker']}</b>"]
    if s.get("limite"):
        partes[0] += f" até {cart.fmt(s['limite'])}"
    if _retorno(s) is not None:
        partes.append(_txt_ret(s))
    if _num(s.get("taxa")):
        partes.append(f"{s['taxa'] * 100:.0f}%")
    if s.get("risco") in RISCO_COR:
        partes.append(RISCO_COR[s["risco"]])
    return " · ".join(partes)


def lista_sinais_msg(chat: str, u: dict, hist: pd.DataFrame | None, hoje: str) -> list:
    """📈 Sinais, em três partes: as posições que já tens (como vão e o que ainda dão); os próximos sinais
    para os lugares livres, do melhor para o pior segundo o modelo (lista_sinais.ordenar: equilibra o lucro
    esperado e a probabilidade de ganhar), sem os que já tens; e, com a carteira cheia, se compensa trocar.
    O detalhe de cada sinal (quanto comprar) está no botão dele."""
    itens = _avaliados(u, hist, hoje)
    comprar = _compraveis(u, itens)
    r = u["regras"]
    livres = max(r["max_posicoes"] - len(u["posicoes"]), 0)
    hora = _hora_lisboa(_ler_cotacoes().get("hora", ""))
    linhas = _bloco_posicoes(u, hist, hoje)
    if not comprar:
        linhas.append("📈 Agora não há sinais novos para comprar. Aviso-te quando houver.")
        return [_msg(chat, "\n".join(linhas), menu=True)]
    troca = _melhor_troca(u, itens, hist, hoje) if not livres else None
    mostrar = comprar[:livres or 3]          # em destaque: os que cabem nos lugares livres (3 se cheia)
    resto = comprar[len(mostrar):len(mostrar) + RESTO_MAX]   # os outros, numa lista que abre com um toque
    if livres:
        lug = f"{livres} lugar{'es' if livres != 1 else ''} livre{'s' if livres != 1 else ''}"
        cab = f"📈 <b>Próximos sinais</b> · {lug}"
    else:
        cab = "📈 <b>Próximos sinais</b> · carteira cheia"
    linhas += [cab, "<i>Do melhor para o pior, segundo o modelo.</i>", ""]
    for k, s in enumerate(mostrar, start=1):
        linhas += [_cartao_ranking(k, s), ""]
    if resto:
        quais = "O outro sinal" if len(resto) == 1 else f"Os outros {len(resto)} sinais"
        linhas.append(f"➕ <b>{quais}</b> que o modelo dá agora (não cabem na tua carteira; "
                      f"para os apanhar, aumenta as posições em ⚙️ Regras). Toca para abrir; em cada linha: preço máximo · "
                      f"lucro esperado · confiança · risco.")
        corpo = "\n".join(_linha_resto(k, s) for k, s in enumerate(resto, start=len(mostrar) + 1))
        if len(comprar) > len(mostrar) + len(resto):
            corpo += f"\n… e mais {len(comprar) - len(mostrar) - len(resto)}"
        linhas += [f"<blockquote expandable>{corpo}</blockquote>", ""]
    if not livres:
        if troca:
            linhas += [f"🔁 <b>Compensa trocar</b> a {troca[1]} pela {troca[0]['ticker']}: proposta a seguir.", ""]
        else:
            linhas += ["🔒 <b>Nenhuma troca compensa agora</b>: as tuas posições ainda rendem mais do que "
                       "os sinais novos, já contando o spread.", ""]
    rodape = ("Lucro esperado: média de sinais iguais no passado, já com custos e contando os que perdem. "
              "Confiança: hipótese de acabar a ganhar."
              + (" \"Ainda dá\": o que uma posição destas costuma render daqui até à saída." if u["posicoes"] else ""))
    vivo = all(s.get("ao_vivo") for s in mostrar[:max(livres, 1)])
    linhas.append(f"<i>{rodape}" + (" Preços deste momento." if vivo else f" Preços das {hora}." if hora else "")
                  + "</i>")
    linhas.append("👇 Toca num sinal para ver quanto comprar.")
    botoes = [[(f"{MEDALHAS.get(k, f'{k}.')} {s['ticker']}", f"ls|{s['ticker']}|{s['data_sinal']}")
               for k, s in list(enumerate(mostrar, start=1))[i:i + 3]] for i in range(0, len(mostrar), 3)]
    if resto:
        botoes.append([("⚙️ Mudar o número de posições", "mm|0|1")])
    out = [_msg(chat, "\n".join(linhas), botoes)]
    if troca:
        out.append(_msg(chat, *troca[2]))
    return out


def ver_sinal(chat: str, u: dict, ticker: str, data: str, hist, hoje: str) -> tuple[str, dict]:
    """Botão 🟢 Ver da lista: o sinal ao preço de agora, com quanto comprar e os botões."""
    s = next((x for x in _avaliados(u, hist, hoje) if x["ticker"] == ticker and x["data_sinal"] == data), None)
    if not s or s["estado_lista"] != "comprar" or ticker in u["posicoes"]:
        return _msg(chat, f"{ticker} já não está para comprar: passou o limite de compra, acabou ou já o tens. "
                          f"Carrega outra vez em 📈 Sinais para ver a lista de agora.")
    u.setdefault("precos_mostrados", {})[cart.chave_sinal(ticker, data)] = s["entrada"]
    t = cart.tamanho(u, ticker, s["entrada"])
    extra = _dia(s)
    texto = sinal_simples(u, s, t, u["caixa"] - (0 if t["motivo"] else t["valor"]),
                          linha_numeros(s, taxas_setup(hist), extra))
    b = botoes_sinal(ticker, data) + ([botoes_copiar(t["qtd"], s)] if not t["motivo"] else [])
    return _msg(chat, texto, b)


def mensagens_trocas(estado: dict | None, hist: pd.DataFrame | None, hoje: str, avisados: set) -> list:
    """A cada corrida dos sinais: com a carteira cheia, se um sinal que ainda dá para comprar passou a
    render mais do que a posição mais fraca (porque ela já deu o que tinha a dar), propõe a troca.
    Uma vez por par (posição, sinal); `avisados` guarda as já propostas."""
    out = []
    if not estado:
        return out
    for chat, u in cart.ativos(estado).items():
        if len(u["posicoes"]) < u["regras"]["max_posicoes"]:
            continue
        r = _melhor_troca(u, _avaliados(u, hist, hoje), hist, hoje)
        if not r:
            continue
        s, fraca, (texto, botoes) = r
        chave = f"troca|{chat}|{fraca}|{s['ticker']}|{s['data_sinal']}"
        if chave in avisados:
            continue
        avisados.add(chave)
        out.append((chat, "🔄 <b>Os sinais mudaram.</b> Uma das tuas posições já deu quase tudo o que costuma dar.\n\n"
                    + texto, botoes))
    return out


def _chave_saida(chat, t: str, p: dict, tipo: str) -> str:
    return f"saida|{chat}|{t}|{p.get('data', '')}|{tipo}"


FECHADOS = ("stop", "objetivo", "expirou", "regra")


def fechos_do_registo(hist: pd.DataFrame | None, vistos: set, desde: str) -> tuple[list[dict], set]:
    """Sinais que o registo fechou (stop, objetivo, prazo, regra) desde `desde` e ainda não vistos pelo bot,
    no formato de acompanhamento.atualizar. Devolve (fechos novos, chaves de todos os fechos recentes)."""
    if hist is None or hist.empty:
        return [], set()
    h = hist[hist["estado"].isin(FECHADOS) & (hist["data_fecho"].astype(str) >= desde)]
    novos, chaves = [], set()
    for r in h.to_dict("records"):
        chave = f"{r['ticker']}|{r['data_sinal']}|{r['estado']}"
        chaves.add(chave)
        if chave in vistos:
            continue
        preco, e = _num(r.get("preco_fecho")), _num(r.get("entrada"))
        if not preco or not e:
            continue
        compra = r.get("direcao") != "venda"
        novos.append({**r, "data_sinal": str(r["data_sinal"]), "resultado": r["estado"], "preco_saida": preco,
                      "preco_atual": preco, "variacao_pct": (preco / e - 1) * (100 if compra else -100)})
    return novos, chaves


def mensagens_fechos(estado: dict | None, fechos: list[dict], dono: str | None, avisados: set | None = None) -> list:
    """Aviso de venda: personalizado (com botões) para quem tem a ação; o dono recebe sempre.
    Stop e objetivo: quem já recebeu o aviso pela sua posição (mensagens_saidas, mesma chave em `avisados`)
    não recebe outro; quem não recebeu (ex. comprou nesse dia) é avisado aqui."""
    out, avisados = [], (set() if avisados is None else avisados)
    for f in fechos:
        base = telegram.formatar_fecho(f)
        donos_avisados = False
        for chat, u in (cart.ativos(estado).items() if estado else []):
            p = u["posicoes"].get(f["ticker"])
            if not p or not p.get("acompanhar", True):
                continue
            if f["resultado"] in ("stop", "objetivo"):
                chave = _chave_saida(chat, f["ticker"], p, f["resultado"])
                if chave in avisados:
                    donos_avisados |= chat == dono
                    continue
                avisados.add(chave)
            pct = (f["preco_saida"] / p["preco"] - 1) * 100
            texto = (f"{base}\n💼 Tens {cart.fmt_qtd(p['qtd'])} compradas a {cart.fmt(p['preco'])} "
                     f"({cart.sinal_fmt(pct, 1)}% a {cart.fmt(f['preco_saida'])}).")
            out.append((chat, texto, botoes_venda(f["ticker"], f["preco_saida"])))
            donos_avisados |= chat == dono
        if dono and not donos_avisados:
            out.append((dono, base, None))
    return out


def mensagens_resultados(estado: dict | None, agenda: dict, avisados: set, hoje: str) -> list:
    """Quem tem uma ação que apresenta resultados hoje ou no próximo dia útil recebe um aviso (uma vez).
    Um trade de poucos dias não costuma valer o risco de um salto de preço com as contas."""
    out = []
    px = _cotacoes(sorted(agenda)) if agenda else {}
    for chat, u in (cart.ativos(estado).items() if estado else []):
        for t, p in u["posicoes"].items():
            a = agenda.get(t)
            if not a:
                continue
            chave = f"res|{chat}|{t}|{a['data']}"
            if chave in avisados:
                continue
            avisados.add(chave)
            quando = "hoje" if a["data"] == hoje else f"a {cart._dm(a['data'])}"
            agora = px.get(t)
            linha = (f"agora {cart.fmt(agora)} ({cart.sinal_fmt((agora / p['preco'] - 1) * 100, 1)}%)" if agora
                     else f"compradas a {cart.fmt(p['preco'])}")
            texto = (f"📅 <b>{t}</b> apresenta resultados {quando} ({a['quando']}).\n"
                     f"💼 Tens {cart.fmt_qtd(p['qtd'])}, {linha}.\n"
                     "Com as contas o preço pode saltar muito, para cima ou para baixo, e passar o stop. "
                     "Num trade de poucos dias, o mais prudente costuma ser vender antes.")
            botoes = botoes_venda(t, agora) if agora else [[("✏️ Vendi", f"w|{t}"), ("🔒 Mantenho", f"m|{t}")]]
            out.append((chat, texto, botoes))
    return out


def mensagens_saidas(estado: dict | None, dados: dict, avisados: set) -> list:
    """De 10 em 10 min: a cada pessoa, as posições que tocaram no seu stop ou objetivo, uma vez só.
    `avisados` guarda as chaves já enviadas (e recebe as novas)."""
    out, acoes = [], dados.get("acoes", {})
    for chat, u in (cart.ativos(estado).items() if estado else []):
        for t, p in u["posicoes"].items():
            if not p.get("acompanhar", True) or t not in acoes:
                continue
            s = cotacoes.saida_atingida(p, acoes[t]["velas"])
            if not s:
                continue
            chave = _chave_saida(chat, t, p, s["tipo"])
            if chave in avisados:
                continue
            avisados.add(chave)
            agora = acoes[t]["preco"]
            pct = (agora / p["preco"] - 1) * 100
            cab = (f"🛑 <b>{t}</b> tocou no stop ({cart.fmt(s['nivel'])})" if s["tipo"] == "stop"
                   else f"🎯 <b>{t}</b> chegou ao objetivo ({cart.fmt(s['nivel'])})")
            texto = (f"{cab}: vende.\n💼 Tens {cart.fmt_qtd(p['qtd'])} compradas a {cart.fmt(p['preco'])}; "
                     f"agora {cart.fmt(agora)} ({cart.sinal_fmt(pct, 1)}%).\n"
                     "Se a ordem já executou na XTB, carrega em ✅ Vendi e acerta o preço no talão.")
            out.append((chat, texto, botoes_venda(t, s["nivel"])))
    return out


def mensagens_vigia(estado: dict | None, alertas: list[dict], dono: str | None, base) -> list:
    """Notícia de risco sobre uma ação: a cada pessoa que a tem, com o resultado ao preço de agora e
    botões de venda; o dono recebe sempre. `base(a)` dá o texto do alerta (vigia.formatar)."""
    out = []
    px = _cotacoes(sorted({a["ticker"] for a in alertas}))
    for a in alertas:
        t, texto = a["ticker"], base(a)
        dono_avisado = False
        for chat, u in (cart.ativos(estado).items() if estado else []):
            p = u["posicoes"].get(t)
            if not p:
                continue
            agora = px.get(t)
            if agora:
                linha = (f"\n💼 Tens {cart.fmt_qtd(p['qtd'])} compradas a {cart.fmt(p['preco'])}; agora "
                         f"{cart.fmt(agora)} ({cart.sinal_fmt((agora / p['preco'] - 1) * 100, 1)}%).")
                botoes = botoes_venda(t, agora)
            else:
                linha = f"\n💼 Tens {cart.fmt_qtd(p['qtd'])} compradas a {cart.fmt(p['preco'])}."
                botoes = [[("✏️ Vendi (escrever preço)", f"w|{t}"), ("🔒 Mantenho", f"m|{t}")]]
            out.append((chat, texto + linha, botoes))
            dono_avisado |= chat == dono
        if dono and not dono_avisado:
            out.append((dono, texto, None))
    return out


def sinais_no_periodo(hist: pd.DataFrame | None, desde: str) -> dict:
    """Sinais de compra fechados desde `desde` (registo em papel): n, quantos ganharam e retorno médio."""
    if hist is None or hist.empty:
        return {}
    f = acompanhamento._fechados_compra(hist)
    f = f[f["data_sinal"].astype(str) >= (desde or "")]
    return {"n": len(f), "ganhos": int((f["ret"] > 0).sum()), "media": float(f["ret"].mean())} if len(f) else {}


def resultados(u: dict, hist: pd.DataFrame | None) -> str:
    txt = cart.formatar_resultados(u, _cotacoes(list(u["posicoes"])), sinais_no_periodo(hist, u.get("desde", "")))
    d = diario(u, hist, u.get("desde", ""))
    return txt + ("\n\n" + d if d else "")


def resumos_semanais(estado: dict, hoje: str, hist: pd.DataFrame | None = None) -> list:
    """Sexta ao fecho: a semana de cada pessoa (só a quem teve movimento ou tem posições)."""
    segunda = (pd.Timestamp(hoje) - pd.Timedelta(days=pd.Timestamp(hoje).dayofweek)).strftime("%Y-%m-%d")
    ativos = cart.ativos(estado)
    cot = _cotacoes(sorted({t for u in ativos.values() for t in u["posicoes"]}))
    out = []
    for chat, u in ativos.items():
        texto = cart.formatar_semana(u, segunda, cot)
        d = diario(u, hist, segunda) if texto else None
        if d:
            texto += "\n\n" + d
        if texto:
            out.append(("sendMessage", _msg(chat, texto, menu=True)[1]))
    return out


# ---------- resumo da manhã, por pessoa ----------

def resumo_manha(chat: str, u: dict, hist: pd.DataFrame | None, hoje: str, abertura: str = "") -> tuple | None:
    """☀️ Antes de Nova Iorque abrir: a carteira, o que acaba hoje e os melhores da lista para hoje.
    None se a pessoa não tem posições nem nada para comprar (não vale a pena acordá-la)."""
    r, moeda = u["regras"], u["regras"]["moeda"]
    pos = {f["ticker"]: f for f in forcas(u, hist, hoje)}
    comprar = _compraveis(u, _avaliados(u, hist, hoje))
    livres = max(r["max_posicoes"] - len(u["posicoes"]), 0)
    if not pos and not comprar:
        return None
    linhas = [f"☀️ <b>Bom dia, {html.escape(u.get('nome') or '')}!</b>"
              + (f" Nova Iorque abre às {abertura}." if abertura else "")]
    if pos:
        em_acoes = sum(p["qtd"] * pos[t]["preco"] for t, p in u["posicoes"].items())
        total = u["caixa"] + em_acoes
        ganho = total - r["capital_inicial"]
        linhas.append(f"\n💼 <b>{cart.fmt(total)} {moeda}</b> ({cart.sinal_fmt(ganho)} {moeda}) · "
                      f"{len(u['posicoes'])}/{r['max_posicoes']} lugares · livre {cart.fmt(u['caixa'])}")
        acabam = []
        for t, p in sorted(u["posicoes"].items(), key=lambda x: x[1].get("data", "")):
            f, prazo = pos[t], _conf_prazo(t, p, hist)[1]
            dia = f["usados"] + 1
            ultimo = bool(p.get("data")) and dia >= prazo
            acabam += [t] if ultimo and p.get("acompanhar", True) else []
            linhas.append(f"• <b>{t}</b> {cart.sinal_fmt(f['pct'], 1)}% · "
                          + ("⏳ último dia" if ultimo else f"dia {min(dia, prazo)} de {prazo}"))
        if acabam:
            linhas.append(f"⏳ Hoje acaba o prazo de <b>{', '.join(acabam)}</b>: o plano é vender até ao fecho.")
    if comprar and livres:
        linhas.append(f"\n📈 <b>Para hoje</b> ({livres} lugar{'es' if livres != 1 else ''} livre{'s' if livres != 1 else ''})")
        for k, s in enumerate(comprar[:min(livres, 3)], start=1):
            linhas.append(_linha_compra(k, s))
    elif comprar:
        troca = _melhor_troca(u, _avaliados(u, hist, hoje), hist, hoje)
        linhas.append(f"\n🔁 A {troca[0]['ticker']} compensa mais do que a {troca[1]}: vê a proposta em 📈 Sinais."
                      if troca else "\n🔒 Carteira cheia e nenhuma troca compensa agora.")
    linhas.append("\n<i>Preços do último fecho; confirma na XTB.</i>")
    return _msg(chat, "\n".join(linhas), [[("📈 Ver a lista", "lst|"), ("💼 Carteira", "car|")]])


def resumos_manha(estado: dict, hist: pd.DataFrame | None, hoje: str, abertura: str = "") -> list:
    out = []
    for chat, u in cart.ativos(estado).items():
        if not avisos(u)["manha"]:
            continue
        m = resumo_manha(chat, u, hist, hoje, abertura)
        if m:
            out.append(m)
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
        botoes = [[(f"✅ Comprei {t}", f"c|{t}|{hoje}"), (f"❌ Não {t}", f"i|{t}|{hoje}")]
                  for t in falta[:10]]
        out.append(("sendMessage", _msg(chat, f"⏰ Ficaram sem resposta hoje: {', '.join(falta)}. Compraste algum?",
                                        botoes)[1]))
        for t in falta:
            u["decisoes"][cart.chave_sinal(t, hoje)] = "sem_resposta"
    return out
