"""Diário de trades: porque é que a pessoa vendeu, e "tu vs modelo".

Cada venda da carteira guarda (u["operacoes"]):
  motivo        o que a pessoa escolheu nos botões (MOTIVOS), "troca" sem perguntar
  motivo_txt    o texto livre de "✍️ Outro"
  sinal         a chave do sinal da compra ("TICKER|AAAA-MM-DD"), se houve sinal
  acima_limite  comprou acima do preço máximo de compra da lista (registado sem perguntar)
  antes_modelo  vendeu com o sinal ainda aberto no registo em papel, ou seja antes da saída do modelo

"Tu vs modelo": nas vendas com sinal, o resultado da pessoa contra o do registo em papel nos mesmos
sinais (só os que o modelo já fechou), e quanto ganhou ou perdeu por motivo.
"""
from __future__ import annotations

import pandas as pd

from . import carteira as cart

MOTIVOS = {"obj": "🎯 Objetivo", "stop": "🛑 Stop", "prazo": "⏱ Prazo/regra", "troca": "🔁 Troca",
           "medo": "😟 Medo/notícia", "dinheiro": "💵 Precisei do dinheiro", "outro": "✍️ Outro"}
PERGUNTA = ["obj", "stop", "prazo", "medo", "dinheiro", "outro"]  # a troca regista-se sozinha
FECHADO = ("stop", "objetivo", "expirou", "regra")


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(v) else v


def linha_sinal(hist: pd.DataFrame | None, chave: str | None) -> dict | None:
    if hist is None or hist.empty or not chave or "|" not in chave:
        return None
    t, data = chave.split("|", 1)
    h = hist[(hist["ticker"] == t) & (hist["data_sinal"].astype(str).str[:10] == data)]
    return h.iloc[-1].to_dict() if not h.empty else None


def ainda_aberto(hist: pd.DataFrame | None, chave: str | None) -> bool:
    r = linha_sinal(hist, chave)
    return bool(r) and str(r.get("estado")) == "aberto"


def retorno_modelo(hist: pd.DataFrame | None, chave: str | None) -> float | None:
    """Resultado do sinal no registo em papel (preço de fecho / entrada − 1), se o modelo já o fechou."""
    r = linha_sinal(hist, chave)
    if not r or str(r.get("estado")) not in FECHADO:
        return None
    e, f = _num(r.get("entrada")), _num(r.get("preco_fecho"))
    return f / e - 1 if e and f else None


def comparar(u: dict, hist: pd.DataFrame | None, desde: str = "") -> dict | None:
    vendas = [o for o in u.get("operacoes") or [] if o.get("tipo") == "venda" and o.get("data", "") >= desde]
    if not vendas:
        return None
    por_motivo: dict[str, list] = {}
    reais, modelo, pendentes = [], [], 0
    for o in vendas:
        m = o.get("motivo") or "?"
        n, lucro = por_motivo.get(m, (0, 0.0))
        por_motivo[m] = (n + 1, lucro + float(o.get("lucro") or 0.0))
        if not o.get("sinal") or not o.get("preco_compra"):
            continue
        rm = retorno_modelo(hist, o["sinal"])
        if rm is None:
            pendentes += 1
            continue
        reais.append(o["preco"] / o["preco_compra"] - 1)
        modelo.append(rm)
    return {"n": len(vendas), "n_comp": len(reais), "pendentes": pendentes,
            "real": sum(reais) / len(reais) if reais else None,
            "modelo": sum(modelo) / len(modelo) if modelo else None, "por_motivo": por_motivo,
            "acima_limite": sum(bool(o.get("acima_limite")) for o in vendas),
            "antes_modelo": sum(bool(o.get("antes_modelo")) for o in vendas)}


def texto(u: dict, hist: pd.DataFrame | None, desde: str = "") -> str | None:
    d = comparar(u, hist, desde)
    if not d:
        return None
    moeda = u["regras"]["moeda"]
    out = ["🆚 <b>Tu vs modelo</b>"]
    if d["n_comp"]:
        dif = (d["real"] - d["modelo"]) * 100
        n = d["n_comp"]
        quais = "No sinal que vendeste e o modelo já fechou" if n == 1 else f"Nos {n} sinais que vendeste e o modelo já fechou"
        out.append(f"{quais}: tu <b>{cart.sinal_fmt(d['real'] * 100, 1)}%</b> "
                   f"por trade, o modelo <b>{cart.sinal_fmt(d['modelo'] * 100, 1)}%</b> "
                   f"({cart.sinal_fmt(dif, 1)} pontos {'a teu favor' if dif >= 0 else 'para o modelo'}).")
    if d["pendentes"]:
        out.append(f"<i>{d['pendentes']} ainda abert{'os' if d['pendentes'] != 1 else 'o'} no modelo: entra{'m' if d['pendentes'] != 1 else ''} quando ele fechar.</i>")
    motivos = sorted(d["por_motivo"].items(), key=lambda kv: -kv[1][0])
    out.append("Por motivo: " + " · ".join(
        f"{MOTIVOS.get(m, '❔ Sem motivo')} {cart.sinal_fmt(lucro)} {moeda} ({n})" for m, (n, lucro) in motivos))
    avisos = []
    if d["acima_limite"]:
        avisos.append(f"{d['acima_limite']} compra{'s' if d['acima_limite'] != 1 else ''} acima do preço máximo")
    if d["antes_modelo"]:
        avisos.append(f"{d['antes_modelo']} venda{'s' if d['antes_modelo'] != 1 else ''} antes da saída do modelo")
    if avisos:
        out.append("⚠️ " + " · ".join(avisos))
    return "\n".join(out)
