"""Carteira de cada utilizador do bot: posições reais, tamanho de posição e carteira simulada.

O estado é um dicionário simples (guardado cifrado por src/cofre.py):
  {"versao", "dono", "offset", "lembrete", "utilizadores": {chat_id: utilizador}}
Cada utilizador tem as suas regras, dinheiro livre ("caixa"), posições e operações.
A carteira simulada não tem estado: calcula-se a partir de data/historico_sinais.csv.
"""
from __future__ import annotations

import copy
import math

import numpy as np
import pandas as pd

REGRAS_BASE = {"capital_inicial": 100.0, "pct": 20.0, "max_posicoes": 5, "moeda": "USD",
               "mercados": ["eua"], "custo_pct": 0.0, "ordem_minima": 5.0}


def novo_estado(dono: str | int | None) -> dict:
    return {"versao": 1, "dono": str(dono or ""), "offset": 0, "lembrete": "", "utilizadores": {}}


def novo_utilizador(nome: str, regras: dict, estado: str = "ativo", hoje: str = "") -> dict:
    regras = {**REGRAS_BASE, **(regras or {})}
    return {"nome": nome, "estado": estado, "regras": regras, "caixa": float(regras["capital_inicial"]),
            "desde": hoje, "posicoes": {}, "operacoes": [], "decisoes": {}, "aguarda": None, "anterior": None}


def ativos(estado: dict) -> dict:
    return {c: u for c, u in estado["utilizadores"].items() if u["estado"] == "ativo"}


# ---------- números e textos ----------

def numero(txt: str) -> float:
    """Aceita "135,40", "135.40" e "1 234,5". Erro se não for número."""
    t = str(txt).strip().replace(" ", "").replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", ".")
    v = float(t)
    if not math.isfinite(v):
        raise ValueError(txt)
    return v


def fmt(x: float, casas: int = 2) -> str:
    return f"{x:,.{casas}f}".replace(",", " ").replace(".", ",")


def sinal_fmt(x: float, casas: int = 2) -> str:
    return ("+" if x >= 0 else "") + fmt(x, casas)


def fmt_qtd(q: float) -> str:
    return fmt(q, 4).rstrip("0").rstrip(",") if q != int(q) else str(int(q))


def mercado(ticker: str) -> str:
    """Tickers sem sufixo de bolsa (".AS", ".DE", ...) são dos EUA."""
    return "europa" if "." in str(ticker) else "eua"


def chave_sinal(ticker: str, data: str) -> str:
    return f"{ticker}|{data}"


# ---------- valor e tamanho ----------

def investido(u: dict) -> float:
    return sum(p["qtd"] * p["preco"] for p in u["posicoes"].values())


def valor_total(u: dict) -> float:
    """Dinheiro livre + posições ao preço de compra (o bot não tem cotações em tempo real)."""
    return u["caixa"] + investido(u)


def tamanho(u: dict, ticker: str, preco: float, reservado: float = 0.0, ocupadas: int = 0) -> dict:
    """Quanto comprar deste sinal. `reservado`/`ocupadas` contam sinais anteriores da mesma ronda."""
    r = u["regras"]
    if mercado(ticker) not in r["mercados"]:
        return {"qtd": 0.0, "valor": 0.0, "motivo": "fora_mercado"}
    if ticker in u["posicoes"]:
        return {"qtd": 0.0, "valor": 0.0, "motivo": "ja_tem"}
    if len(u["posicoes"]) + ocupadas >= r["max_posicoes"]:
        return {"qtd": 0.0, "valor": 0.0, "motivo": "max_posicoes"}
    livre = u["caixa"] - reservado
    valor = min(valor_total(u) * r["pct"] / 100, livre)
    if valor < r["ordem_minima"] or preco <= 0:
        return {"qtd": 0.0, "valor": 0.0, "motivo": "sem_capital"}
    qtd = math.floor(valor / (preco * (1 + r["custo_pct"] / 100)) * 1e4) / 1e4
    return {"qtd": qtd, "valor": qtd * preco, "motivo": None}


MOTIVOS = {"fora_mercado": "fora da tua carteira (só {mercados})",
           "ja_tem": "já tens esta ação na carteira",
           "max_posicoes": "sem capital: já tens o máximo de {max} posições",
           "sem_capital": "sem capital livre para mais uma posição"}


def linha_tamanho(u: dict, t: dict, livre: float | None = None) -> str:
    r = u["regras"]
    if t["motivo"]:
        txt = MOTIVOS[t["motivo"]].format(mercados=", ".join(m.upper() for m in r["mercados"]),
                                          max=r["max_posicoes"])
        return f"💼 Carteira: {txt}."
    return (f"💼 Carteira: compra <b>{fmt_qtd(t['qtd'])}</b> ações (~{fmt(t['valor'])} {r['moeda']}, "
            f"{fmt(r['pct'], 0)}%). Livre depois: {fmt(u['caixa'] - t['valor'] if livre is None else livre)} {r['moeda']}.")


def posicao_mais_fraca(u: dict, hoje: str) -> str | None:
    """A que está há mais tempo aberta (a que tem menos prazo pela frente)."""
    if not u["posicoes"]:
        return None
    return min(u["posicoes"], key=lambda t: u["posicoes"][t].get("data", hoje))


# ---------- operações ----------

def _guardar_anterior(u: dict) -> None:
    u["anterior"] = None
    u["anterior"] = copy.deepcopy({k: v for k, v in u.items() if k != "anterior"})


def comprar(u: dict, ticker: str, qtd: float, preco: float, data: str, sinal: dict | None = None) -> str:
    if qtd <= 0 or preco <= 0:
        return "Quantidade e preço têm de ser maiores que zero."
    custo = qtd * preco * (1 + u["regras"]["custo_pct"] / 100)
    if custo > u["caixa"] + 1e-6:
        return (f"Não registei: custa {fmt(custo)} e só tens {fmt(u['caixa'])} livres. "
                f"Se puseste mais dinheiro na XTB, usa /deposito primeiro.")
    _guardar_anterior(u)
    u["caixa"] -= custo
    p = u["posicoes"].get(ticker)
    if p:  # reforço: preço médio
        total = p["qtd"] + qtd
        p["preco"] = (p["qtd"] * p["preco"] + qtd * preco) / total
        p["qtd"] = total
    else:
        s = sinal or {}
        u["posicoes"][ticker] = {"qtd": qtd, "preco": preco, "data": data, "sinal": s.get("chave"),
                                 "stop": s.get("stop"), "objetivo": s.get("objetivo"),
                                 "entrada_sinal": s.get("entrada"), "acompanhar": True}
    u["operacoes"].append({"tipo": "compra", "ticker": ticker, "qtd": qtd, "preco": preco, "data": data,
                           "sinal": (sinal or {}).get("chave")})
    if sinal and sinal.get("chave"):
        u["decisoes"][sinal["chave"]] = "comprei"
    moeda = u["regras"]["moeda"]
    txt = f"✅ Registei: {fmt_qtd(qtd)} {ticker} a {fmt(preco)} ({fmt(qtd * preco)} {moeda}). Livre: {fmt(u['caixa'])} {moeda}."
    if sinal and sinal.get("stop") and pd.notna(sinal.get("stop")):
        txt += f"\nMete o stop a {fmt(sinal['stop'])} na XTB."
    if sinal and sinal.get("entrada"):
        e, o, st = sinal["entrada"], sinal.get("objetivo"), sinal.get("stop")
        alvo = o if o and pd.notna(o) else (e + (e - st) if st and pd.notna(st) else None)
        if alvo and preco > e + 0.5 * (alvo - e):
            txt += "\n⚠️ Entraste bem acima do preço do sinal: o potencial que sobra é menor e o risco maior."
    return txt


def vender(u: dict, ticker: str, qtd: float | None, preco: float, data: str) -> str:
    p = u["posicoes"].get(ticker)
    if not p:
        return f"Não tens {ticker} na carteira."
    if preco <= 0:
        return "O preço tem de ser maior que zero."
    qtd = p["qtd"] if qtd is None or qtd >= p["qtd"] - 1e-9 else qtd
    if qtd <= 0:
        return "A quantidade tem de ser maior que zero."
    _guardar_anterior(u)
    receita = qtd * preco * (1 - u["regras"]["custo_pct"] / 100)
    lucro = receita - qtd * p["preco"]
    u["caixa"] += receita
    u["operacoes"].append({"tipo": "venda", "ticker": ticker, "qtd": qtd, "preco": preco, "data": data,
                           "preco_compra": p["preco"], "lucro": lucro})
    resto = p["qtd"] - qtd
    if resto <= 1e-9:
        del u["posicoes"][ticker]
        parte = ""
    else:
        p["qtd"] = resto
        parte = f" Ficas com {fmt_qtd(resto)}."
    moeda = u["regras"]["moeda"]
    pct = (preco / p["preco"] - 1) * 100
    return (f"{'🟢' if lucro >= 0 else '🔴'} Vendi {fmt_qtd(qtd)} {ticker} a {fmt(preco)}: "
            f"{sinal_fmt(lucro)} {moeda} ({sinal_fmt(pct, 1)}%).{parte} Livre: {fmt(u['caixa'])} {moeda}.")


def movimento(u: dict, valor: float, data: str) -> str:
    """Depósito (valor > 0) ou levantamento (valor < 0)."""
    if u["caixa"] + valor < -1e-6:
        return f"Só tens {fmt(u['caixa'])} livres."
    _guardar_anterior(u)
    u["caixa"] += valor
    u["regras"]["capital_inicial"] += valor
    u["operacoes"].append({"tipo": "deposito" if valor > 0 else "levantamento", "valor": valor, "data": data})
    return f"Feito. Livre: {fmt(u['caixa'])} {u['regras']['moeda']}."


def definir_capital(u: dict, valor: float, data: str) -> str:
    if valor < investido(u):
        return f"O capital não pode ser menor do que o que já está investido ({fmt(investido(u))})."
    _guardar_anterior(u)
    u["regras"]["capital_inicial"] = valor
    u["caixa"] = valor - investido(u)
    u["operacoes"].append({"tipo": "capital", "valor": valor, "data": data})
    return f"Capital definido: {fmt(valor)} {u['regras']['moeda']}. Livre: {fmt(u['caixa'])}."


def recomecar(u: dict, hoje: str) -> None:
    """Carteira do zero com o mesmo capital e regras. Pode ser desfeito com desfazer()."""
    _guardar_anterior(u)
    u.update({"caixa": float(u["regras"]["capital_inicial"]), "desde": hoje, "posicoes": {},
              "operacoes": [], "decisoes": {}, "aguarda": None})


def desfazer(u: dict) -> str:
    ant = u.get("anterior")
    if not ant:
        return "Não há nada para desfazer."
    for k, v in ant.items():
        u[k] = v
    u["anterior"] = None
    return f"Desfeito. Livre: {fmt(u['caixa'])} {u['regras']['moeda']}."


# ---------- resumos ----------

def realizado(u: dict) -> tuple[float, int, int]:
    vendas = [o for o in u["operacoes"] if o["tipo"] == "venda"]
    return sum(o["lucro"] for o in vendas), len(vendas), sum(o["lucro"] > 0 for o in vendas)


def simular(hist: pd.DataFrame, regras: dict, desde: str = "") -> dict:
    """Carteira simulada: entra em todos os sinais de compra que cabem nas regras, ao preço do sinal,
    e sai ao preço de fecho registado no histórico."""
    r = {**REGRAS_BASE, **regras}
    h = hist[(hist["direcao"] == "compra") & (hist["data_sinal"].astype(str) >= (desde or ""))].copy()
    h = h[h["ticker"].map(lambda t: mercado(t) in r["mercados"]).astype(bool)]  # lista vazia selecionava colunas
    h = h.sort_values(["data_sinal", "pontuacao"], ascending=[True, False])
    caixa, abertas, fechadas, ganhas = float(r["capital_inicial"]), {}, 0, 0

    def fechar_ate(data):
        nonlocal caixa, fechadas, ganhas
        for t in [t for t, p in abertas.items() if p["fecho"] and p["fecho"] <= data]:
            p = abertas.pop(t)
            receita = p["qtd"] * p["saida"] * (1 - r["custo_pct"] / 100)
            caixa += receita
            fechadas += 1
            ganhas += receita > p["custo"]

    for _, s in h.iterrows():
        fechar_ate(str(s["data_sinal"]))
        if s["ticker"] in abertas or len(abertas) >= r["max_posicoes"]:
            continue
        total = caixa + sum(p["custo"] for p in abertas.values())
        valor = min(total * r["pct"] / 100, caixa)
        if valor < r["ordem_minima"]:
            continue
        aberto = str(s["estado"]) == "aberto" or pd.isna(s["preco_fecho"])
        abertas[s["ticker"]] = {"qtd": valor / s["entrada"], "custo": valor,
                                "saida": None if aberto else float(s["preco_fecho"]),
                                "fecho": None if aberto else str(s["data_fecho"])}
        caixa -= valor
    fechar_ate("9999")
    valor = caixa + sum(p["custo"] for p in abertas.values())
    return {"valor": valor, "retorno_pct": (valor / r["capital_inicial"] - 1) * 100,
            "abertas": len(abertas), "fechadas": fechadas, "ganhas": ganhas}


def _barra(px: float, stop: float, obj: float, n: int = 9) -> str:
    """🛑 47,00 ━━━━●━━━━ 55,00 🎯: onde está o preço entre o stop e o objetivo."""
    pos = min(max((px - stop) / (obj - stop), 0.0), 1.0) if obj > stop else 0.5
    k = round(pos * (n - 1))
    return f"🛑 {fmt(stop)} {'━' * k}●{'━' * (n - 1 - k)} {fmt(obj)} 🎯"


def formatar_carteira(u: dict, cot: dict | None = None, hoje: str = "", prazos: dict | None = None,
                      saidas: dict | None = None, hora: str = "") -> str:
    """Carteira ao preço de agora (`cot`): valor total e, por posição, ganho, prazo e onde está o preço
    entre o stop e o objetivo. `saidas[t]` = stop/objetivo já atingido (cotacoes.saida_atingida)."""
    r, moeda = u["regras"], u["regras"]["moeda"]
    cot, prazos, saidas = cot or {}, prazos or {}, saidas or {}
    blocos, em_acoes = [], 0.0
    for t, p in sorted(u["posicoes"].items(), key=lambda x: x[1].get("data", "")):
        agora = cot.get(t)
        px = agora or p["preco"]
        em_acoes += p["qtd"] * px
        lucro, pct = p["qtd"] * (px - p["preco"]), (px / p["preco"] - 1) * 100
        if agora:
            cab = f"{'🟢' if lucro >= 0 else '🔴'} <b>{t}</b>   <b>{sinal_fmt(pct, 1)}%</b> · {sinal_fmt(lucro)} {moeda}"
            l2 = f"{fmt(agora)} (compraste a {fmt(p['preco'])})"
        else:
            cab = f"⚪ <b>{t}</b>   sem preço de agora"
            l2 = f"compraste a {fmt(p['preco'])}"
        prazo = prazos.get(t)
        if prazo and hoje and p.get("data"):
            dia = int(np.busday_count(p["data"], hoje)) + 1
            l2 += f" · dia {min(dia, prazo)}/{prazo}" + (" ⏳ último" if dia >= prazo else "")
        linhas = [cab, l2]
        obj, stop = p.get("objetivo"), p.get("stop")
        obj = obj if obj is not None and pd.notna(obj) else None
        stop = stop if stop is not None and pd.notna(stop) else None
        s = saidas.get(t)
        if s:
            dia_s = f"{s['data'][8:10]}/{s['data'][5:7]}"
            linhas.append(("🛑 <b>Tocou no stop</b>" if s["tipo"] == "stop" else "🎯 <b>Chegou ao objetivo</b>")
                          + f" ({fmt(s['nivel'])}) a {dia_s}: vende e regista em 🔴 Vendi")
        elif stop and obj:
            linhas.append(_barra(px, stop, obj))
        elif stop:
            linhas.append(f"🛑 {fmt(stop)} (a {sinal_fmt((stop / px - 1) * 100, 1)}%) · sai quando o bot avisar")
        if not p.get("acompanhar", True):
            linhas.append("🔕 sem avisos de venda")
        blocos.append("\n".join(linhas))
    total = u["caixa"] + em_acoes
    ganho = total - r["capital_inicial"]
    ganho_pct = ganho / r["capital_inicial"] * 100 if r["capital_inicial"] else 0
    out = [f"💼 <b>A tua carteira</b>",
           f"<b>{fmt(total)} {moeda}</b>   {'🟢' if ganho >= 0 else '🔴'} {sinal_fmt(ganho)} ({sinal_fmt(ganho_pct, 1)}%)",
           f"Em ações {fmt(em_acoes)} · Livre {fmt(u['caixa'])} · {len(u['posicoes'])}/{r['max_posicoes']} lugares"]
    if blocos:
        out += ["", "\n\n".join(blocos)]
    else:
        out.append("\nSem posições abertas. Os próximos sinais chegam aqui com o botão ✅ Comprei.")
    lucro, n, g = realizado(u)
    if n:
        out.append(f"\n✔️ Já fechadas: {sinal_fmt(lucro)} {moeda} em {n} vendas ({g} a ganhar)")
    if blocos and cot:
        out.append(f"\n<i>Preços{' das ' + hora if hora else ''} (atraso até 15 min).</i>")
    return "\n".join(out)


def formatar_historico(u: dict, n: int = 10) -> str:
    vendas = [o for o in u["operacoes"] if o["tipo"] == "venda"][-n:]
    if not vendas:
        return "Ainda não há vendas registadas."
    linhas = ["📜 <b>Últimas vendas</b>"]
    for o in reversed(vendas):
        pct = (o["preco"] / o["preco_compra"] - 1) * 100
        linhas.append(f"• {o['data']} {o['ticker']}: {sinal_fmt(o['lucro'])} ({sinal_fmt(pct, 1)}%)")
    return "\n".join(linhas)
