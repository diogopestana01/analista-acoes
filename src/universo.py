"""Constrói a lista de ações a analisar (o "universo").

EUA: todas as ações listadas na NASDAQ, NYSE e NYSE American, a partir dos
ficheiros públicos da NASDAQ Trader (sem ETFs, warrants, units, preferenciais).
Europa: constituintes dos principais índices, lidos da Wikipédia, como
aproximação da oferta de ações da XTB.
Extra: qualquer ticker em data/universo_extra.csv (por exemplo exportado da XTB).
"""
from __future__ import annotations

import io
import logging
import re
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Palavras que indicam instrumentos que não são ações ordinárias.
_EXCLUIR = re.compile(
    r"\b(?:warrants?|units?|rights?|preferred|notes?|debentures?|"
    r"subordinated|trust preferred|% )\b",
    re.IGNORECASE,
)

# índice -> (página da Wikipédia, sufixo do Yahoo Finance, moeda)
INDICES_EUROPA: dict[str, tuple[str, str, str]] = {
    "DAX": ("https://en.wikipedia.org/wiki/DAX", ".DE", "EUR"),
    "MDAX": ("https://en.wikipedia.org/wiki/MDAX", ".DE", "EUR"),
    "CAC40": ("https://en.wikipedia.org/wiki/CAC_40", ".PA", "EUR"),
    "FTSE100": ("https://en.wikipedia.org/wiki/FTSE_100_Index", ".L", "GBp"),
    "FTSE250": ("https://en.wikipedia.org/wiki/FTSE_250_Index", ".L", "GBp"),
    "AEX": ("https://en.wikipedia.org/wiki/AEX_index", ".AS", "EUR"),
    "IBEX35": ("https://en.wikipedia.org/wiki/IBEX_35", ".MC", "EUR"),
    "FTSEMIB": ("https://en.wikipedia.org/wiki/FTSE_MIB", ".MI", "EUR"),
    "SMI": ("https://en.wikipedia.org/wiki/Swiss_Market_Index", ".SW", "CHF"),
    "PSI": ("https://en.wikipedia.org/wiki/PSI-20", ".LS", "EUR"),
    "BEL20": ("https://en.wikipedia.org/wiki/BEL_20", ".BR", "EUR"),
    "OMXS30": ("https://en.wikipedia.org/wiki/OMX_Stockholm_30", ".ST", "SEK"),
}

_COLUNAS_TICKER = ("ticker", "symbol", "epic", "ticker symbol", "code")
_HEADERS = {"User-Agent": "Mozilla/5.0 (analista-acoes)"}


def _ler_pipe(texto: str) -> pd.DataFrame:
    linhas = [l for l in texto.splitlines() if not l.startswith("File Creation Time")]
    return pd.read_csv(io.StringIO("\n".join(linhas)), sep="|", dtype=str)


def parse_nasdaq_listed(texto: str) -> pd.DataFrame:
    df = _ler_pipe(texto)
    df = df[(df["Test Issue"] == "N") & (df["ETF"] == "N")]
    df = df[~df["Security Name"].fillna("").str.contains(_EXCLUIR)]
    return pd.DataFrame(
        {"ticker": df["Symbol"], "nome": df["Security Name"], "bolsa": "NASDAQ", "moeda": "USD"}
    )


def parse_other_listed(texto: str) -> pd.DataFrame:
    df = _ler_pipe(texto)
    df = df[(df["Test Issue"] == "N") & (df["ETF"] == "N")]
    df = df[~df["Security Name"].fillna("").str.contains(_EXCLUIR)]
    simbolo = df["ACT Symbol"].fillna("")
    df = df[~simbolo.str.contains(r"[$=^]")]  # preferenciais e afins
    bolsas = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca", "Z": "Cboe", "V": "IEX"}
    return pd.DataFrame(
        {
            # O Yahoo usa "-" para classes de ações (BRK.B -> BRK-B).
            "ticker": df["ACT Symbol"].str.replace(".", "-", regex=False),
            "nome": df["Security Name"],
            "bolsa": df["Exchange"].map(bolsas).fillna(df["Exchange"]),
            "moeda": "USD",
        }
    )


def universo_eua() -> pd.DataFrame:
    partes = []
    for url, parser in ((NASDAQ_LISTED, parse_nasdaq_listed), (OTHER_LISTED, parse_other_listed)):
        r = requests.get(url, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        partes.append(parser(r.text))
    df = pd.concat(partes, ignore_index=True)
    return df[df["ticker"].str.len() > 0]


def tickers_de_tabelas(tabelas: list[pd.DataFrame], sufixo: str) -> list[str]:
    """Encontra a tabela de constituintes e devolve tickers no formato Yahoo."""
    for t in tabelas:
        if isinstance(t.columns, pd.MultiIndex):
            t.columns = [" ".join(map(str, c)) for c in t.columns]
        cols = {str(c).strip().lower(): c for c in t.columns}
        col = next((cols[c] for c in _COLUNAS_TICKER if c in cols), None)
        if col is None or len(t) < 10:
            continue
        out = []
        for v in t[col].dropna().astype(str):
            v = v.strip().split()[0].rstrip(".")
            if not v or len(v) > 12:
                continue
            if "." not in v:
                v = v + sufixo
            out.append(v.upper().replace(".", "-", v.count(".") - 1))
        if out:
            return out
    return []


def universo_europa(indices: list[str]) -> pd.DataFrame:
    linhas = []
    for nome in indices:
        if nome not in INDICES_EUROPA:
            log.warning("Índice desconhecido: %s", nome)
            continue
        url, sufixo, moeda = INDICES_EUROPA[nome]
        try:
            html = requests.get(url, headers=_HEADERS, timeout=30).text
            tickers = tickers_de_tabelas(pd.read_html(io.StringIO(html)), sufixo)
        except Exception as e:  # uma página falhada não deve parar o resto
            log.warning("Falhou %s: %s", nome, e)
            tickers = []
        if not tickers:
            log.warning("Sem tickers para %s", nome)
        linhas += [{"ticker": t, "nome": "", "bolsa": nome, "moeda": moeda} for t in tickers]
    return pd.DataFrame(linhas, columns=["ticker", "nome", "bolsa", "moeda"])


def universo_extra(caminho: Path) -> pd.DataFrame:
    if not caminho.exists():
        return pd.DataFrame(columns=["ticker", "nome", "bolsa", "moeda"])
    df = pd.read_csv(caminho, dtype=str)
    for c in ("nome", "bolsa", "moeda"):
        if c not in df:
            df[c] = ""
    return df[["ticker", "nome", "bolsa", "moeda"]]


def construir_universo(config: dict, pasta_dados: Path) -> pd.DataFrame:
    cfg = config["universo"]
    partes = []
    if cfg.get("eua"):
        partes.append(universo_eua())
    if cfg.get("europa"):
        partes.append(universo_europa(cfg["europa"]))
    partes.append(universo_extra(pasta_dados / "universo_extra.csv"))
    df = pd.concat(partes, ignore_index=True)
    return df.drop_duplicates("ticker").reset_index(drop=True)
