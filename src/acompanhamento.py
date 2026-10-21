"""Acompanha os sinais já enviados e avisa quando é hora de vender/fechar.

O histórico fica em data/historico_sinais.csv (guardado no repositório), o que
também serve de registo do desempenho real do modelo (passo 3, em papel).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

COLUNAS = ["data_sinal", "ticker", "setup", "direcao", "entrada", "objetivo", "stop",
           "dias_estimados", "max_dias", "pontuacao", "estado", "data_fecho", "preco_fecho", "dias"]


def carregar(caminho: Path) -> pd.DataFrame:
    if caminho.exists():
        return pd.read_csv(caminho)
    return pd.DataFrame(columns=COLUNAS)


def registar(hist: pd.DataFrame, novos: pd.DataFrame, data: str, max_dias: int) -> pd.DataFrame:
    if novos.empty:
        return hist
    n = novos[["ticker", "setup", "direcao", "entrada", "objetivo", "stop", "dias_estimados", "pontuacao"]].copy()
    n.insert(0, "data_sinal", data)
    n["max_dias"] = max_dias
    n["estado"] = "aberto"
    novos_abertos = hist[(hist["estado"] == "aberto")][["ticker", "direcao"]]
    chave = set(map(tuple, novos_abertos.to_numpy()))
    n = n[[(t, d) not in chave for t, d in zip(n["ticker"], n["direcao"])]]  # não repete posição aberta
    return pd.concat([hist, n], ignore_index=True)[COLUNAS]


def atualizar(hist: pd.DataFrame, precos: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Verifica cada sinal aberto contra os preços desde a data do sinal."""
    fechos = []
    hist = hist.copy()
    precos = precos.assign(data=pd.to_datetime(precos["data"]).dt.strftime("%Y-%m-%d"))
    for i, s in hist[hist["estado"] == "aberto"].iterrows():
        p = precos[(precos["ticker"] == s["ticker"]) & (precos["data"] > s["data_sinal"])].sort_values("data")
        if p.empty:
            continue
        compra = s["direcao"] == "compra"
        resultado, linha = None, None
        for k, (_, r) in enumerate(p.iterrows(), start=1):
            stop = r["low"] <= s["stop"] if compra else r["high"] >= s["stop"]
            obj = r["high"] >= s["objetivo"] if compra else r["low"] <= s["objetivo"]
            if stop or obj:
                resultado, linha = ("stop" if stop else "objetivo"), (k, r)
                break
            if k >= s["max_dias"]:
                resultado, linha = "expirou", (k, r)
                break
        if resultado is None:
            continue
        k, r = linha
        preco = {"stop": s["stop"], "objetivo": s["objetivo"], "expirou": r["close"]}[resultado]
        hist.loc[i, ["estado", "data_fecho", "preco_fecho", "dias"]] = [resultado, r["data"], preco, k]
        var = (preco / s["entrada"] - 1) * (100 if compra else -100)
        fechos.append({**s.to_dict(), "resultado": resultado, "preco_atual": r["close"], "variacao_pct": var})
    return hist, fechos


def resumo(hist: pd.DataFrame) -> str:
    f = hist[hist["estado"] != "aberto"]
    if f.empty:
        return "Ainda sem sinais fechados."
    compra = f["direcao"] == "compra"
    ret = (f["preco_fecho"] / f["entrada"] - 1).where(compra, 1 - f["preco_fecho"] / f["entrada"])
    return (f"{len(f)} sinais fechados: {(f['estado'] == 'objetivo').mean() * 100:.0f}% no objetivo, "
            f"retorno médio {ret.mean() * 100:+.2f}% por sinal, {f['dias'].mean():.1f} dias em média.")
