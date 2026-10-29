"""Tendências nas redes: de que ações se fala no Reddit e no StockTwits (fontes gratuitas, sem chave).

  reddit      ApeWisdom: agrega as menções em r/wallstreetbets, r/stocks, r/investing e outros.
              Dá o número de menções nas últimas 24 h e as das 24 h anteriores.
  stocktwits  API pública: ações em destaque e, por ação, as últimas mensagens com a etiqueta
              Bullish/Bearish que os próprios autores põem.

Por agora é só informação (no alerta e no resumo da manhã) e fica guardada no histórico.
Atenção muito alta das redes num prazo curto tanto empurra como antecede quedas, por isso
não mexe na pontuação até haver um mês de resultados para medir.
O Reddit direto e o X/Twitter não servem: o Reddit bloqueia os servidores do GitHub e o X é pago.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd
import requests

log = logging.getLogger(__name__)

APEWISDOM = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{p}"
ST_TRENDING = "https://api.stocktwits.com/api/2/trending/symbols.json"
ST_SIMBOLO = "https://api.stocktwits.com/api/2/streams/symbol/{t}.json"
CAB = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}


@lru_cache(maxsize=1)
def reddit(paginas: int = 3) -> pd.DataFrame:
    """Ranking de menções no Reddit (uma vez por corrida). Vazio se a fonte falhar."""
    linhas = []
    for p in range(1, paginas + 1):
        try:
            r = requests.get(APEWISDOM.format(p=p), timeout=20, headers=CAB)
            r.raise_for_status()
            linhas += r.json().get("results", [])
        except Exception as e:  # noqa: BLE001
            log.warning("ApeWisdom (Reddit) falhou na página %d: %s", p, e)
            break
    df = pd.DataFrame(linhas, columns=["ticker", "rank", "mentions", "mentions_24h_ago", "upvotes"])
    if df.empty:
        return df
    df["ticker"] = df["ticker"].astype(str).str.upper().str.replace(".", "-", regex=False)
    for c in ("rank", "mentions", "mentions_24h_ago", "upvotes"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["variacao"] = df["mentions"] / df["mentions_24h_ago"].clip(lower=1)
    return df.drop_duplicates("ticker").set_index("ticker")


@lru_cache(maxsize=1)
def stocktwits_destaque() -> frozenset[str]:
    try:
        r = requests.get(ST_TRENDING, timeout=20, headers=CAB)
        r.raise_for_status()
        return frozenset(str(s["symbol"]).upper() for s in r.json().get("symbols", []))
    except Exception as e:  # noqa: BLE001
        log.warning("StockTwits (destaque) falhou: %s", e)
        return frozenset()


def stocktwits(ticker: str) -> dict:
    """{msgs, otimistas, pessimistas, otimismo (0-1 entre as mensagens com etiqueta)}; {} se falhar."""
    try:
        r = requests.get(ST_SIMBOLO.format(t=ticker), timeout=20, headers=CAB)
        r.raise_for_status()
        msgs = r.json().get("messages", [])
    except Exception as e:  # noqa: BLE001
        log.info("StockTwits %s falhou: %s", ticker, e)
        return {}
    etiq = [((m.get("entities") or {}).get("sentiment") or {}).get("basic") for m in msgs]
    bull, bear = etiq.count("Bullish"), etiq.count("Bearish")
    return {"msgs": len(msgs), "otimistas": bull, "pessimistas": bear,
            "otimismo": bull / (bull + bear) if bull + bear else None}


def para_sinal(ticker: str, cache: dict | None = None, chave: str | None = None) -> dict:
    """Colunas das redes para um sinal. Só ações dos EUA (as fontes são americanas)."""
    if "." in ticker:
        return {}
    rd = reddit()
    out = {"st_destaque": ticker in stocktwits_destaque()}
    if ticker in rd.index:
        out.update(reddit_rank=int(rd.at[ticker, "rank"]), reddit_mencoes=int(rd.at[ticker, "mentions"]),
                   reddit_variacao=round(float(rd.at[ticker, "variacao"]), 2))
    if cache is not None and chave in cache:
        st = cache[chave]
    else:
        st = stocktwits(ticker)
        if cache is not None and st:
            cache[chave] = st
    if st:
        out.update(st_msgs=st["msgs"], st_otimismo=st["otimismo"])
    return out


def linha_alerta(s: dict) -> str | None:
    """Uma linha simples para o alerta do Telegram, ou None se não houver nada a dizer."""
    partes = []
    if pd.notna(s.get("reddit_rank")):
        var = s.get("reddit_variacao")
        extra = f", {var:.1f}x as menções de ontem" if pd.notna(var) and var >= 1.5 else ""
        partes.append(f"Reddit: #{int(s['reddit_rank'])} mais falada ({int(s['reddit_mencoes'])} menções{extra})")
    if pd.notna(s.get("st_otimismo")):
        partes.append(f"StockTwits: {s['st_otimismo'] * 100:.0f}% otimistas")
    if s.get("st_destaque") is True:
        partes.append("em destaque no StockTwits")
    return "📣 Redes: " + "; ".join(partes) if partes else None


def em_alta(universo: set[str], n: int = 5, min_mencoes: int = 20) -> list[str]:
    """Ações do universo cujas menções no Reddit mais subiram face às 24 h anteriores."""
    rd = reddit()
    if rd.empty:
        return []
    rd = rd[rd.index.isin(universo) & (rd["mentions"] >= min_mencoes)]
    top = rd.sort_values("variacao", ascending=False).head(n)
    return [f"{t} ({int(r.mentions)} menções, {r.variacao:.1f}x)" for t, r in top.iterrows()]
