"""Preço de agora de poucas ações (posições abertas e sinais), o mais fresco possível.

Fonte principal: Nasdaq (api.nasdaq.com, gratuita, sem chave, responde a partir do GitHub).
Dá o último negócio em tempo real ("isRealTime": true) com a hora do negócio. Não é uma API
oficial: se falhar, usa o Yahoo (velas de 1 minuto). Teste de 29/10 às 23:05 UTC: as duas fontes
batiam ao cêntimo e ambas tinham negócios do próprio minuto.
Serve para vigiar stops e objetivos entre as corridas dos sinais; o histórico diário continua no Yahoo.
"""
from __future__ import annotations

import logging
import re

import pandas as pd
import requests

from .resultados import CABECALHOS

log = logging.getLogger(__name__)
URL = "https://api.nasdaq.com/api/quote/{t}/info"


def _num(txt) -> float | None:
    m = re.search(r"-?\d[\d,]*\.?\d*", str(txt or ""))
    return float(m.group().replace(",", "")) if m else None


def nasdaq(ticker: str, sessao=None) -> dict | None:
    """{preco, hora, fonte, estado[, bid, ask]} do último negócio na Nasdaq; None se falhar ou não for dos
    EUA. bid/ask (melhor preço de venda/compra) só quando a bolsa dá os dois e fazem sentido."""
    if "." in ticker:
        return None
    try:
        r = (sessao or requests).get(URL.format(t=ticker.replace("-", ".")), params={"assetclass": "stocks"},
                                     headers=CABECALHOS, timeout=10)
        r.raise_for_status()
        d = r.json().get("data") or {}
        p = d.get("primaryData") or {}
        preco = _num(p.get("lastSalePrice"))
        if preco is None or preco <= 0:
            return None
        hora = str(p.get("lastTradeTimestamp") or "").replace("DT", "").strip()
        bid, ask = _num(p.get("bidPrice")), _num(p.get("askPrice"))
        lados = {"bid": bid, "ask": ask} if bid and ask and 0 < bid <= ask < bid * 1.2 else {}
        return {"preco": preco, "hora": hora, "fonte": "nasdaq", "estado": d.get("marketStatus"),
                "tempo_real": bool(p.get("isRealTime")), **lados}
    except Exception as e:  # noqa: BLE001
        log.info("Nasdaq %s falhou: %s", ticker, e)
        return None


def yahoo(tickers: list[str]) -> dict[str, dict]:
    """Último preço das velas de 1 minuto do Yahoo (reserva)."""
    if not tickers:
        return {}
    import yfinance as yf
    out = {}
    try:
        y = yf.download(tickers, period="1d", interval="1m", group_by="ticker", progress=False)
        for t in tickers:
            try:
                s = (y[t] if len(tickers) > 1 else y)["Close"].dropna()
                s = s.squeeze() if hasattr(s, "columns") else s
                out[t] = {"preco": float(s.iat[-1]), "hora": f"{s.index[-1].tz_convert('UTC'):%H:%M} UTC",
                          "fonte": "yahoo", "estado": None, "tempo_real": True}
            except Exception:  # noqa: BLE001
                continue
    except Exception as e:  # noqa: BLE001
        log.warning("Yahoo 1m falhou: %s", e)
    return out


def agora(tickers: list[str], buscar_nasdaq=nasdaq, buscar_yahoo=yahoo) -> dict[str, dict]:
    """{ticker: {preco, hora, fonte, ...}} para cada ação que alguma fonte conseguiu dar."""
    tickers = sorted(set(tickers))
    out = {}
    with requests.Session() as sessao:
        for t in tickers:
            q = buscar_nasdaq(t, sessao) if buscar_nasdaq is nasdaq else buscar_nasdaq(t)
            if q:
                out[t] = q
    falta = [t for t in tickers if t not in out]
    if falta:
        out.update(buscar_yahoo(falta))
    log.info("Preços de agora: %d de %d (%d da Nasdaq)", len(out), len(tickers),
             sum(v["fonte"] == "nasdaq" for v in out.values()))
    return out
