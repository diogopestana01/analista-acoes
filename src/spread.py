"""Spread: diferença entre o preço a que se compra (ask, o de cima) e o preço a que se vende (bid, o de baixo).

Pedido do Pestana (30/10): entrou em 5 sinais e todos abriram a perder ~1%, só pelo spread. O backtest
contava 0,3% fixos ida e volta; nas ações pequenas e logo à abertura o spread é muito maior.
Aqui mede-se o spread de agora de cada sinal em aberto (Nasdaq em tempo real, src/precos_agora.py):
  - a lista usa o ask para ver se ainda dá para comprar e o spread como custo de entrar
  - o registo guarda o primeiro spread medido de cada sinal (spread_sinal) para comparar no fim da
    validação com a estimativa do backtest (passo3l_spread.py)
  - data/spreads.csv guarda o spread de cada corrida dos sinais que dão para comprar, para ver a que
    horas do dia é mais barato entrar
Só com a bolsa de NY aberta: fora de horas o bid/ask da Nasdaq não serve.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from . import precos_agora

log = logging.getLogger(__name__)


def pct(bid, ask) -> float | None:
    """Spread em percentagem do preço do meio (0,01 = 1%). None se faltar um lado ou não fizer sentido."""
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if not (b > 0 and a >= b):
        return None
    return (a - b) / ((a + b) / 2)


def medir(tickers, buscar=None, fios: int = 8) -> dict[str, dict]:
    """{ticker: {bid, ask, spread_pct}} das ações que a Nasdaq deu com os dois lados."""
    tickers = sorted(set(tickers))
    if not tickers:
        return {}
    out = {}
    with requests.Session() as sessao:
        f = buscar or (lambda t: precos_agora.nasdaq(t, sessao))
        with ThreadPoolExecutor(max_workers=fios) as ex:
            for t, q in zip(tickers, ex.map(f, tickers)):
                s = pct((q or {}).get("bid"), (q or {}).get("ask"))
                if s is not None:
                    out[t] = {"bid": q["bid"], "ask": q["ask"], "spread_pct": round(s, 5)}
    log.info("Spread de agora: %d de %d ações", len(out), len(tickers))
    return out


def registar(hist: pd.DataFrame, medidos: dict[str, dict]) -> pd.DataFrame:
    """Guarda no registo o primeiro spread medido de cada sinal aberto (coluna spread_sinal)."""
    if hist.empty or not medidos:
        return hist
    if "spread_sinal" not in hist.columns:
        hist["spread_sinal"] = pd.NA
    for i in hist.index[hist["estado"] == "aberto"]:
        m = medidos.get(hist.at[i, "ticker"])
        if m and pd.isna(hist.at[i, "spread_sinal"]):
            hist.at[i, "spread_sinal"] = m["spread_pct"]
    return hist


def guardar_historico(caminho: Path, agora: pd.Timestamp, medidos: dict[str, dict], tickers) -> None:
    """Acrescenta uma linha por ação (hora UTC, ticker, spread) a data/spreads.csv."""
    linhas = [{"hora": f"{agora:%Y-%m-%d %H:%M}", "ticker": t, "spread_pct": medidos[t]["spread_pct"]}
              for t in sorted(set(tickers)) if t in medidos]
    if not linhas:
        return
    try:
        pd.DataFrame(linhas).to_csv(caminho, mode="a", header=not caminho.exists(), index=False)
    except OSError as e:
        log.warning("Histórico de spreads não guardado: %s", e)
