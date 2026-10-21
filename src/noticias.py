"""Recolhe notícias recentes por ação (Yahoo Finance + Google News RSS).

Só é usado para as ações candidatas (passo 2), porque pedir notícias para
milhares de ações todos os dias seria lento e desnecessário.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import pandas as pd

log = logging.getLogger(__name__)

GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def noticias_yahoo(ticker: str) -> list[dict]:
    import yfinance as yf

    out = []
    for n in yf.Ticker(ticker).news or []:
        c = n.get("content", n)
        out.append({
            "ticker": ticker,
            "titulo": c.get("title", ""),
            "resumo": c.get("summary", ""),
            "fonte": (c.get("provider") or {}).get("displayName", "Yahoo"),
            "data": c.get("pubDate") or c.get("providerPublishTime"),
            "url": ((c.get("canonicalUrl") or {}).get("url")) or c.get("link", ""),
        })
    return out


def noticias_google(ticker: str, nome: str = "") -> list[dict]:
    import feedparser

    termo = f'"{nome}"' if nome else ticker
    feed = feedparser.parse(GOOGLE_NEWS.format(q=quote_plus(f"{termo} stock when:7d")))
    return [{
        "ticker": ticker,
        "titulo": e.get("title", ""),
        "resumo": "",
        "fonte": (e.get("source") or {}).get("title", "Google News"),
        "data": e.get("published"),
        "url": e.get("link", ""),
    } for e in feed.entries[:15]]


def recolher(tickers: dict[str, str], dias: int = 7) -> pd.DataFrame:
    """tickers: {ticker: nome}. Devolve notícias dos últimos `dias` dias."""
    linhas = []
    for t, nome in tickers.items():
        for fonte in (noticias_yahoo, lambda x: noticias_google(x, nome)):
            try:
                linhas += fonte(t)
            except Exception as e:
                log.warning("Notícias %s falharam: %s", t, e)
    df = pd.DataFrame(linhas, columns=["ticker", "titulo", "resumo", "fonte", "data", "url"])
    if df.empty:
        return df
    df["data"] = pd.to_datetime(df["data"], utc=True, errors="coerce", format="mixed")
    limite = datetime.now(timezone.utc) - timedelta(days=dias)
    df = df[df["data"].isna() | (df["data"] >= limite)]
    return df.drop_duplicates(["ticker", "titulo"]).reset_index(drop=True)
