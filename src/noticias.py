"""Recolhe notícias recentes por ação, só de fontes gratuitas e fiáveis.

  yahoo   desligado: a partir do GitHub o Yahoo responde "Too Many Requests" (429)
  google  Google Notícias (RSS), pesquisa pelo nome da empresa e pelo ticker
  sec     comunicações oficiais à SEC (ações dos EUA): 8-K (factos relevantes) e
          prospetos de emissão de ações (diluição). É a fonte mais fiável de todas.

Só é usado para as ações candidatas (passo 2), porque pedir notícias para
milhares de ações todos os dias seria lento e desnecessário.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import pandas as pd

log = logging.getLogger(__name__)

GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US"
SEC_ATOM = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={t}&type={tipo}&dateb=&owner=include"
            "&count=15&output=atom")
NAVEGADOR = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
# A SEC pede um User-Agent que identifique quem pede (pode ser mudado com a variável SEC_USER_AGENT).
# Sem um e-mail no User-Agent a SEC recusa (403); usa-se o endereço noreply do bot do repositório.
SEC_UA = {"User-Agent": os.environ.get("SEC_USER_AGENT", "analista-acoes analista-bot@users.noreply.github.com")}
# Páginas de cotação e previsões que o Google devolve como "notícia" mas não dizem nada.
_RUIDO = re.compile(r"stock price, news, quote|stock quote|quote & history|estimates [–-]|forecast [—-] price target|"
                    r"balance sheet & financial health|symbol__", re.IGNORECASE)

# Itens dos 8-K que interessam num trade de dias, em linguagem simples.
ITENS_8K = {"1.01": "acordo relevante", "1.02": "fim de acordo relevante", "1.03": "falência",
            "2.01": "compra ou venda de ativos", "2.02": "resultados", "2.05": "reestruturação/despedimentos",
            "2.06": "imparidades", "3.01": "aviso de exclusão da bolsa", "3.02": "venda de ações não registada",
            "4.01": "troca de auditor", "4.02": "contas anteriores não fiáveis", "5.02": "saída/entrada de gestores",
            "7.01": "comunicação ao mercado", "8.01": "outro facto relevante"}
# Formulários que costumam anunciar emissão de ações nova (diluição).
EMISSAO = ("S-1", "S-3", "424B")


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
    """Feed RSS do Yahoo; se vier vazio, tenta pela biblioteca yfinance."""
    import feedparser
    import requests

    try:
        r = requests.get(YAHOO_RSS.format(t=ticker), timeout=20, headers=NAVEGADOR)
        feed = feedparser.parse(r.text)
        out = [{"ticker": ticker, "titulo": e.get("title", ""), "resumo": e.get("summary", ""),
                "fonte": "Yahoo Finance", "data": e.get("published"), "url": e.get("link", "")}
               for e in feed.entries[:20]]
        if out:
            return out
    except Exception as e:
        log.info("Yahoo RSS %s falhou: %s", ticker, e)
    return _yahoo_yfinance(ticker)


def _yahoo_yfinance(ticker: str) -> list[dict]:
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


def termo_pesquisa(ticker: str, nome: str = "") -> str:
    """Nome da empresa, ou o ticker acompanhado de "stock"/"shares": um ticker sozinho como KIDS,
    WPP ou ALL apanha notícias que não têm nada a ver com a ação."""
    base = ticker.split(".")[0]
    nome = limpar_nome(nome)
    ticker_q = f'"{base} stock" OR "{base} shares" OR "NASDAQ:{base}" OR "NYSE:{base}"'
    return f'"{nome}" OR {ticker_q}' if nome else ticker_q


def pesquisar_google(ticker: str, consulta: str, n: int = 15) -> list[dict]:
    import feedparser
    import requests

    r = requests.get(GOOGLE_NEWS.format(q=quote_plus(consulta)), timeout=20, headers=NAVEGADOR)
    feed = feedparser.parse(r.text)
    entradas = [e for e in feed.entries if not _RUIDO.search(e.get("title", ""))]
    return [{
        "ticker": ticker,
        "titulo": e.get("title", ""),
        "resumo": "",
        "fonte": (e.get("source") or {}).get("title", "Google News"),
        "data": e.get("published"),
        "url": e.get("link", ""),
    } for e in entradas[:n]]


def noticias_google(ticker: str, nome: str = "") -> list[dict]:
    return pesquisar_google(ticker, f"({termo_pesquisa(ticker, nome)}) when:7d")


PALAVRAS_RISCO = ('downgrade OR lawsuit OR "class action" OR offering OR dilution OR investigation OR probe '
                  'OR "short seller" OR recall OR delisting OR "going concern" OR "cuts guidance" OR resigns')


def riscos_google(ticker: str, nome: str = "") -> list[dict]:
    """Pesquisa própria do advogado do diabo: só notícias de risco dos últimos 14 dias."""
    return pesquisar_google(ticker, f"({termo_pesquisa(ticker, nome)}) ({PALAVRAS_RISCO}) when:14d", n=10)


def noticias_sec(ticker: str, dias: int = 14) -> list[dict]:
    """Comunicações oficiais à SEC (só EUA): 8-K com os itens explicados e prospetos de emissão."""
    if "." in ticker:
        return []
    import feedparser
    import requests

    out = []
    for tipo in ("8-K", "424B", "S-3", "S-1"):
        r = requests.get(SEC_ATOM.format(t=ticker.replace("-", "."), tipo=tipo), timeout=20, headers=SEC_UA)
        for e in feedparser.parse(r.text).entries:
            data = pd.to_datetime(e.get("updated"), utc=True, errors="coerce")
            if pd.isna(data) or data < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=dias):
                continue
            forma = (e.get("category") or e.get("title", "").split(" - ")[0]).strip()
            if tipo == "8-K":
                itens = re.findall(r"Item (\d\.\d\d)", e.get("summary", ""))
                desc = ", ".join(ITENS_8K[i] for i in dict.fromkeys(itens) if i in ITENS_8K) or "facto relevante"
                titulo = f"Comunicado oficial à SEC ({forma}): {desc}"
            else:
                titulo = f"Comunicado oficial à SEC ({forma}): prospeto de emissão de ações (possível diluição)"
            out.append({"ticker": ticker, "titulo": titulo, "resumo": "", "fonte": "SEC EDGAR",
                        "data": data, "url": e.get("link", "")})
    return out


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
        fontes = (("google", lambda x: noticias_google(x, nome)), ("sec", noticias_sec))
        for nome_fonte, fonte in fontes:
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
