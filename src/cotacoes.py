"""Preço de agora das ações que alguém tem na carteira (ou com sinal aberto).

Os sinais (diario.py) correm de 10 em 10 min e já descarregam a vela de hoje de cada ação; guardam aqui
o último preço e as velas dos últimos dias em data/cotacoes.json. O bot da Carteira lê este ficheiro
(faz git pull de minuto a minuto) para mostrar a carteira ao preço de agora sem ir ao Yahoo.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

DIAS = 15  # velas guardadas por ação: chega para o prazo máximo de um sinal


def construir(precos: pd.DataFrame, tickers, hora: pd.Timestamp) -> dict:
    """{"hora": ..., "acoes": {ticker: {"preco": último, "velas": [[data, máx, mín, fecho], ...]}}}"""
    acoes = {}
    p = precos[precos["ticker"].isin(set(tickers))].dropna(subset=["close"])
    if not p.empty:
        p = p.assign(data=pd.to_datetime(p["data"]).dt.strftime("%Y-%m-%d"))
        for t, g in p.sort_values("data").groupby("ticker"):
            g = g.drop_duplicates("data", keep="last").tail(DIAS)
            velas = [[d, round(float(h), 4), round(float(lo), 4), round(float(c), 4)]
                     for d, h, lo, c in zip(g["data"], g["high"].fillna(g["close"]),
                                            g["low"].fillna(g["close"]), g["close"])]
            acoes[t] = {"preco": velas[-1][3], "velas": velas}
    return {"hora": hora.strftime("%Y-%m-%dT%H:%MZ"), "acoes": acoes}


def guardar(caminho: Path, dados: dict) -> None:
    caminho.write_text(json.dumps(dados, separators=(",", ":")))


def ler(caminho: Path) -> dict:
    try:
        return json.loads(Path(caminho).read_text())
    except (OSError, ValueError):
        return {"hora": "", "acoes": {}}


def precos_agora(dados: dict, tickers) -> dict[str, float]:
    a = dados.get("acoes", {})
    return {t: a[t]["preco"] for t in tickers if t in a}


def saida_atingida(p: dict, velas: list) -> dict | None:
    """Primeiro dia, desde a compra, em que o preço tocou no stop ou no objetivo da posição.
    No dia da compra só conta o fecho (o mínimo/máximo desse dia pode ter sido antes de comprar).
    Se no mesmo dia tocou nos dois, conta o stop (não se sabe qual veio primeiro)."""
    stop, obj = p.get("stop"), p.get("objetivo")
    stop = stop if stop is not None and pd.notna(stop) else None
    obj = obj if obj is not None and pd.notna(obj) else None
    if stop is None and obj is None:
        return None
    desde = p.get("data") or ""
    for d, alto, baixo, fecho in velas:
        if d < desde:
            continue
        if d == desde:
            alto = baixo = fecho
        if stop is not None and baixo <= stop:
            return {"tipo": "stop", "nivel": stop, "data": d, "preco": fecho}
        if obj is not None and alto >= obj:
            return {"tipo": "objetivo", "nivel": obj, "data": d, "preco": fecho}
    return None
