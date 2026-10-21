"""Recolhe notícias recentes por ação (Yahoo Finance + Google News RSS).

Só é usado para as ações candidatas (passo 2), porque pedir notícias para
milhares de ações todos os dias seria lento e desnecessário.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import pandas as pd

log = logging.getLogger(__name__)

GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


_LIXO_NOME = re.compile(
    r"\s+-\s+.*$|\b(common stock|ordinary shares?|american depositary shares?|depositary shares?|"
    r"class [a-c]|series [a-c]|inc\.?|corp(oration)?\.?|plc|n\.?v\.?|s\.?a\.?|ag|se|ltd\.?|"
    r"holdings?|co\.?)\b|[,.]",
    re.IGNORECASE,
)


def limpar_nome(nome: str) -> str:
    """'Apple Inc. - Common Stock' -> 'Apple'. Nomes longos falham nas pesquisas de notícias."""
    if not isinstance(nome, str):
        return ""
    limpo = re.sub(r"\s+", " ", _LIXO_NOME.sub(" ", nome)).strip()
    return limpo if len(limpo) >= 3 else ""


def noticias_yahoo(ticker: str) -> list[dict]:
    import yfinance as yf

    out = []
    tk = yf.Ticker(ticker)
    try:
        itens = tk.get_news(count=20)
    except Exception:
        itens = tk.news
    for n in itens or []:
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
    import requests

    base = ticker.split(".")[0]
    nome = limpar_nome(nome)
    termo = f'"{nome}" OR "{base}"' if nome else f'"{base}" stock'
    r = requests.get(GOOGLE_NEWS.format(q=quote_plus(f"{termo} when:7d")), timeout=20,
                     headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"})
    feed = feedparser.parse(r.text)
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
        if not nome:  # ações europeias vêm sem nome; pede-o ao Yahoo
            try:
                import yfinance as yf
                nome = yf.Ticker(t).info.get("shortName", "") or ""
            except Exception:
                nome = ""
        contagem = {}
        for nome_fonte, fonte in (("yahoo", noticias_yahoo), ("google", lambda x: noticias_google(x, nome))):
            try:
                itens = fonte(t)
            except Exception as e:
                log.warning("Notícias %s (%s) falharam: %s", t, nome_fonte, e)
                itens = []
            contagem[nome_fonte] = len(itens)
            linhas += itens
        log.info("Notícias %s: %s", t, contagem)
    df = pd.DataFrame(linhas, columns=["ticker", "titulo", "resumo", "fonte", "data", "url"])
    if df.empty:
        return df
    df["data"] = pd.to_datetime(df["data"], utc=True, errors="coerce", format="mixed")
    limite = datetime.now(timezone.utc) - timedelta(days=dias)
    df = df[df["data"].isna() | (df["data"] >= limite)]
    return df.drop_duplicates(["ticker", "titulo"]).reset_index(drop=True)
