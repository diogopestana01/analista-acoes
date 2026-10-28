"""Conversa do bot da Carteira: botões, comandos e mensagens por utilizador.

Tudo aqui é lógica pura (sem rede): recebe o estado e uma atualização do Telegram e
devolve a lista de chamadas à API a fazer, como (método, parâmetros). Assim testa-se offline.
"""
from __future__ import annotations

import json

import pandas as pd

from . import carteira as cart
from . import telegram

AJUDA = """<b>Comandos</b>
/carteira: posições, dinheiro livre e a carteira simulada
/comprei TICKER quantidade preço: regista uma compra (ex. /comprei NVDA 0,4 135,20)
/vendi TICKER quantidade preço: regista uma venda (quantidade "tudo" vende tudo)
/capital 100: define o capital da carteira
/deposito 50, /levantamento 50: entradas e saídas de dinheiro
/regras pct 20 max 5: % por sinal e máximo de posições
/desfazer: anula a última operação
/historico: últimas vendas e resultado
/cancelar: cancela uma pergunta pendente

Nos sinais e nos avisos de venda usa os botões."""


# ---------- botões ----------

def botoes_sinal(ticker: str, data: str) -> list:
    return [[("✅ Comprei", f"c|{ticker}|{data}"), ("✏️ Outro preço", f"o|{ticker}|{data}"),
             ("❌ Ignorar", f"i|{ticker}|{data}")]]


def botoes_venda(ticker: str, preco: float) -> list:
    return [[(f"✅ Vendi a {cart.fmt(preco)}", f"v|{ticker}|{preco:.4f}")],
            [("✏️ Outro preço ou parte", f"w|{ticker}"), ("🔒 Mantenho", f"m|{ticker}")]]


def _msg(chat, texto: str, botoes: list | None = None) -> tuple[str, dict]:
    p = {"chat_id": str(chat), "text": texto, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if botoes:
        p["reply_markup"] = telegram.teclado(botoes)
    return "sendMessage", p


def _tirar_botoes(cq: dict) -> tuple[str, dict]:
    m = cq.get("message", {})
    return "editMessageReplyMarkup", {"chat_id": str(m.get("chat", {}).get("id", "")),
                                      "message_id": m.get("message_id"),
                                      "reply_markup": json.dumps({"inline_keyboard": []})}


# ---------- utilizadores ----------

def garantir_dono(estado: dict, regras: dict, hoje: str, nome: str = "dono") -> None:
    dono = estado.get("dono")
    if dono and dono not in estado["utilizadores"]:
        estado["utilizadores"][dono] = cart.novo_utilizador(nome, regras, "ativo", hoje)


def _start(estado, chat, nome, cfg, hoje) -> list:
    u = estado["utilizadores"].get(chat)
    aviso = cfg.get("aviso", "")
    if chat == estado.get("dono"):
        garantir_dono(estado, cfg["regras_base"], hoje, nome)
        estado["utilizadores"][chat]["nome"] = nome
        return [_msg(chat, f"Olá {nome}! A tua carteira está ativa.\n{aviso}\n\n{AJUDA}")]
    if u and u["estado"] == "ativo":
        return [_msg(chat, AJUDA)]
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
    if "callback_query" in upd:
        return _callback(estado, upd["callback_query"], hist, hoje, cfg)
    msg = upd.get("message") or {}
    texto = (msg.get("text") or "").strip()
    if not texto:
        return []
    chat = str(msg["chat"]["id"])
    nome = (msg.get("from") or {}).get("first_name") or "amigo"
    partes = texto.split()
    cmd = partes[0].split("@")[0].lower()
    if cmd == "/start":
        return _start(estado, chat, nome, cfg, hoje)
    u = estado["utilizadores"].get(chat)
    if not u or u["estado"] != "ativo":
        return []
    try:
        if u.get("aguarda") and not cmd.startswith("/"):
            return [_msg(chat, _resposta(u, partes, hist, hoje))]
        return [_msg(chat, _comando(estado, chat, u, cmd, partes[1:], hist, hoje))]
    except (ValueError, IndexError):
        return [_msg(chat, "Não percebi os números. Exemplo: 51,20 0,4 (ou /ajuda).")]


def _resposta(u: dict, partes: list, hist, hoje: str) -> str:
    a = u["aguarda"]
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
        return f"Regras: {r['pct']:g}% por sinal, máximo {r['max_posicoes']} posições."
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
            out.append(_msg(partes[1], f"✅ Foste aceite! A tua carteira começa com "
                                       f"{cart.fmt(alvo['caixa'])} {alvo['regras']['moeda']} (muda com /capital).\n\n{AJUDA}"))
        return out
    u = estado["utilizadores"].get(chat)
    if not u or u["estado"] != "ativo":
        return out
    ticker = partes[1] if len(partes) > 1 else ""
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
        t = cart.tamanho(u, ticker, sinal["entrada"])
        if t["motivo"]:
            return out + [_msg(chat, cart.linha_tamanho(u, t) + "\nSe compraste na mesma, usa ✏️ Outro preço com a quantidade.")]
        return out + [_tirar_botoes(cq), _msg(chat, cart.comprar(u, ticker, t["qtd"], sinal["entrada"], hoje, sinal))]
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
    """Para cada utilizador ativo: cabeçalho + cada sinal com o tamanho sugerido e botões.
    Sem estado (carteira desligada), só o dono recebe, com botões nas ações dos EUA."""
    out = []
    if not estado:
        if dono:
            out.append((dono, cabecalho, None))
            for s in novos:
                b = botoes_sinal(s["ticker"], s["data_sinal"]) if cart.mercado(s["ticker"]) == "eua" else None
                out.append((dono, telegram.formatar_sinal(s), b))
        return out
    for chat, u in cart.ativos(estado).items():
        out.append((chat, cabecalho, None))
        reservado, ocupadas = 0.0, 0
        for s in novos:
            t = cart.tamanho(u, s["ticker"], s["entrada"], reservado, ocupadas)
            if not t["motivo"]:
                reservado, ocupadas = reservado + t["valor"], ocupadas + 1
            texto = telegram.formatar_sinal(s) + "\n" + cart.linha_tamanho(u, t)
            fraca = cart.posicao_mais_fraca(u, s["data_sinal"])
            if t["motivo"] in ("max_posicoes", "sem_capital") and fraca:
                texto += f"\nA posição mais antiga é {fraca} (desde {u['posicoes'][fraca].get('data', '')}); a decisão de trocar é tua."
            b = None if t["motivo"] in ("fora_mercado", "ja_tem") else botoes_sinal(s["ticker"], s["data_sinal"])
            out.append((chat, texto, b))
    return out


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
