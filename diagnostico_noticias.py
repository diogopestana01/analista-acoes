"""Diagnóstico das notícias: quantas manchetes dá cada fonte e o que o advogado do diabo diz.

Uso:  python diagnostico_noticias.py [data]   (por defeito o último data/sinais_*.csv)
Não envia nada para o Telegram nem mexe nos dados; só imprime.
"""
import logging
import sys
from pathlib import Path

import pandas as pd

from src import investigador, noticias, sentimento

logging.basicConfig(level=logging.WARNING)
DADOS = Path(__file__).parent / "data"


def comparar_tempo_real(tickers: list[str]) -> None:
    """Preço de agora: Yahoo (velas de 1 min) contra Nasdaq (último negócio), com a hora de cada um."""
    import requests
    import yfinance as yf

    from src.resultados import CABECALHOS
    agora = pd.Timestamp.now(tz="UTC")
    try:
        y = yf.download(tickers, period="1d", interval="1m", group_by="ticker", progress=False, prepost=True)
    except Exception as e:  # noqa: BLE001
        y = None
        print(f"[yahoo 1m] ERRO {e}")
    for t in tickers:
        linha = f"[tempo real {t}] agora {agora:%H:%M:%S} UTC"
        try:
            s = y[t]["Close"].dropna()
            linha += f" | Yahoo {s.iat[-1]:.2f} às {s.index[-1].tz_convert('UTC'):%H:%M}"
        except Exception as e:  # noqa: BLE001
            linha += f" | Yahoo ERRO {e}"
        try:
            r = requests.get(f"https://api.nasdaq.com/api/quote/{t}/info", params={"assetclass": "stocks"},
                             headers=CABECALHOS, timeout=20)
            d = r.json()["data"]
            for k in ("primaryData", "secondaryData"):
                p = d.get(k) or {}
                linha += (f" | Nasdaq {k}: {p.get('lastSalePrice')} {p.get('lastTradeTimestamp')} "
                          f"real={p.get('isRealTime')}")
            linha += f" | estado {d.get('marketStatus')}"
        except Exception as e:  # noqa: BLE001
            linha += f" | Nasdaq ERRO {e}"
        print(linha)


def main(data: str | None = None) -> None:
    ficheiro = DADOS / f"sinais_{data}.csv" if data else sorted(DADOS.glob("sinais_*.csv"))[-1]
    sinais = pd.read_csv(ficheiro)
    try:
        nomes = pd.read_csv(DADOS / "universo_filtrado.csv").set_index("ticker")["nome"].fillna("").to_dict()
    except FileNotFoundError:
        nomes = {}
    print(f"Sinais de {ficheiro.name}: {len(sinais)}")
    import requests
    for nome_url, url, cab in (("yahoo rss", noticias.YAHOO_RSS.format(t="AAPL"), noticias.NAVEGADOR),
                               ("sec", noticias.SEC_ATOM.format(t="AAPL", tipo="8-K"), noticias.SEC_UA),
                               ("sec tickers", "https://www.sec.gov/files/company_tickers.json",
                                {**noticias.SEC_UA, "Accept-Encoding": "gzip, deflate"}),
                               ("sec api", "https://data.sec.gov/submissions/CIK0000320193.json",
                                {**noticias.SEC_UA, "Accept-Encoding": "gzip, deflate"}),
                               ("nasdaq rss", "https://www.nasdaq.com/feed/rssoutbound?symbol=AAPL", noticias.NAVEGADOR),
                               ("stocktwits trending", "https://api.stocktwits.com/api/2/trending/symbols.json",
                                noticias.NAVEGADOR),
                               ("stocktwits AAPL", "https://api.stocktwits.com/api/2/streams/symbol/AAPL.json",
                                noticias.NAVEGADOR),
                               ("apewisdom reddit", "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1",
                                noticias.NAVEGADOR),
                               ("reddit wsb", "https://www.reddit.com/r/wallstreetbets/hot.json?limit=5",
                                {"User-Agent": "analista-acoes/1.0"}),
                               ("google trends rss", "https://trends.google.com/trending/rss?geo=US", noticias.NAVEGADOR)):
        try:
            r = requests.get(url, timeout=20, headers=cab)
            print(f"[{nome_url}] HTTP {r.status_code} {r.headers.get('content-type')} | {r.text[:300]!r}"
                  + "")
        except Exception as e:
            print(f"[{nome_url}] ERRO {e}")
    from src import saude
    print(f"[nasdaq preços, para conferir o Yahoo] AAPL último fecho: {saude.nasdaq_fecho('AAPL')}")
    comparar_tempo_real(["AAPL", "MSFT", "OTEX", "KIDS", "ZETA"])
    if "--so-fontes" in sys.argv:
        return
    for s in sinais.drop_duplicates("ticker").to_dict("records"):
        t, nome = s["ticker"], nomes.get(s["ticker"], "")
        print(f"\n===== {t} ({noticias.limpar_nome(nome) or 'sem nome'}) | {s['setup']}")
        fontes = {"google": lambda: noticias.noticias_google(t, nome), "nasdaq": lambda: noticias.noticias_nasdaq(t, nome),
                  "sec": lambda: noticias.noticias_sec(t)}
        todas = []
        for f, fn in fontes.items():
            try:
                itens = fn()
            except Exception as e:
                print(f"  {f}: ERRO {e}")
                continue
            print(f"  {f}: {len(itens)}")
            for i in itens[:3]:
                print(f"     - {str(i['titulo'])[:110]}")
            todas += itens
        news = pd.DataFrame(todas, columns=["ticker", "titulo", "resumo", "fonte", "data", "url"])
        news["data"] = pd.to_datetime(news["data"], utc=True, errors="coerce", format="mixed")
        riscos = investigador.pesquisar_riscos(t, nome)
        print(f"  pesquisa de risco: {len(riscos)}")
        for r in riscos.head(5).itertuples():
            print(f"     ! {str(r.titulo)[:110]}")
        av = sentimento.avaliar(s, news)
        s = {**s, "impacto_noticias": av.impacto, "resumo_noticias": av.resumo, "risco_evento": av.risco_evento}
        print(f"  ANALISTA: {av.impacto} ({av.sentimento:+.1f}) {av.resumo}")
        c = investigador.contestar(s, news, riscos)
        antes = f"{s.get('veredicto_inv')}" if pd.notna(s.get("veredicto_inv")) else "?"
        if c:
            print(f"  ADVOGADO: {c.veredicto} (risco {c.risco}/10; antes: {antes}) {c.argumento}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else None)
