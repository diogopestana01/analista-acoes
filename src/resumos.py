"""Resumos diários no Telegram: abertura (antes da Europa abrir) e fecho (depois de Wall Street fechar).

Abertura: como fecharam os mercados ontem, futuros agora, notícias da noite e
empresas grandes que apresentam resultados hoje.
Fecho: como correu o dia, as ações do universo que mais subiram e desceram e porquê.
O texto é escrito pelo Claude a partir de dados e manchetes; sem chave, vai a lista simples.
"""
from __future__ import annotations

import html
import logging
import os
from urllib.parse import quote_plus

import pandas as pd

from . import noticias, resultados, tendencias

log = logging.getLogger(__name__)

INSTRUCOES = """És o analista de mercados de um investidor português que faz operações de 1 dia a 1 semana.
Escreve em português de Portugal, direto e curto, para ler no telemóvel.
Usa só os dados e as manchetes que recebes; não inventes números nem notícias.
Formato: no máximo 8 linhas, cada uma a começar por "• ". Sem títulos, sem markdown, sem asteriscos."""


def variacoes(tickers: dict[str, str]) -> pd.DataFrame:
    """Último preço e variação face ao fecho anterior, por mercado."""
    import yfinance as yf

    try:
        bruto = yf.download(list(tickers.values()), period="7d", interval="1d", progress=False,
                            auto_adjust=False, threads=True)
    except Exception as e:
        log.warning("Preços dos mercados falharam: %s", e)
        return pd.DataFrame(columns=["mercado", "preco", "var_pct"])
    linhas = []
    for nome, t in tickers.items():
        try:
            c = bruto["Close"][t].dropna()
            linhas.append({"mercado": nome, "preco": c.iat[-1], "var_pct": (c.iat[-1] / c.iat[-2] - 1) * 100})
        except Exception:
            continue
    return pd.DataFrame(linhas)


def maiores_movimentos(precos: pd.DataFrame, filtrado: pd.DataFrame, n_sobe: int = 6,
                       n_desce: int = 4) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Maiores subidas e descidas do último dia no universo líquido."""
    p = precos.sort_values("data")
    ultimo = p["data"].max()
    ret = p.groupby("ticker")["close"].pct_change()
    hoje = p.assign(var_pct=ret * 100)[p["data"] == ultimo].dropna(subset=["var_pct"])
    nomes = filtrado.set_index("ticker")["nome"].fillna("").to_dict() if "nome" in filtrado else {}
    hoje = hoje.assign(nome=hoje["ticker"].map(nomes).fillna("").map(noticias.limpar_nome))
    cols = ["ticker", "nome", "close", "var_pct"]
    return (hoje.nlargest(n_sobe, "var_pct")[cols].reset_index(drop=True),
            hoje.nsmallest(n_desce, "var_pct")[cols].reset_index(drop=True))


def manchetes_gerais(pesquisas: list[str], max_por: int = 8) -> list[str]:
    import feedparser
    import requests

    out = []
    for q in pesquisas:
        try:
            r = requests.get(noticias.GOOGLE_NEWS.format(q=quote_plus(f"{q} when:1d")), timeout=20,
                             headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/126 Safari/537.36"})
            out += [e.get("title", "") for e in feedparser.parse(r.text).entries[:max_por]]
        except Exception as e:
            log.warning("Manchetes '%s' falharam: %s", q, e)
    return list(dict.fromkeys(t for t in out if t))


def manchetes_acao(ticker: str, nome: str, n: int = 3) -> list[str]:
    try:
        itens = noticias.noticias_google(ticker, nome)
    except Exception:
        return []
    return [i["titulo"] for i in itens[:n]]


def escrever(pedido: str) -> str | None:
    """Texto do Claude, ou None sem chave ou em caso de erro."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        from .sentimento import MODELO
        resp = anthropic.Anthropic().messages.create(
            model=MODELO, max_tokens=1500, system=INSTRUCOES,
            messages=[{"role": "user", "content": pedido}])
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
    except Exception as e:
        log.warning("Resumo pelo Claude falhou: %s", e)
        return None


def _linha_mercados(m: pd.DataFrame) -> str:
    if m.empty:
        return "Sem dados de mercados."
    return " | ".join(f"{r.mercado} {r.var_pct:+.1f}%" for r in m.itertuples())


def _linhas_movimentos(df: pd.DataFrame) -> list[str]:
    return [f"{html.escape(r.ticker)} {r.var_pct:+.1f}%" + (f" ({html.escape(r.nome)})" if r.nome else "")
            for r in df.itertuples()]


def resumo_manha(hoje: pd.Timestamp, filtrado: pd.DataFrame, cfg: dict) -> str:
    max_resultados = cfg["manha"]["max_resultados"]
    m = variacoes(cfg["mercados"])
    titulos = manchetes_gerais(cfg["manha"]["pesquisas"])
    cal = resultados.calendario(hoje)
    no_universo = set(filtrado["ticker"])
    cal = [c for c in cal if c["ticker"] in no_universo]
    cal = sorted(cal, key=lambda c: -c["valor_mercado"])[:max_resultados]
    linhas_res = [f"{c['ticker']} ({c['nome']}, {c['quando']})" for c in cal]

    pedido = (f"Resumo de abertura de {hoje:%d/%m/%Y}.\nMercados (último fecho; futuros agora): "
              f"{_linha_mercados(m)}\nResultados hoje: {', '.join(linhas_res) or 'nenhum relevante'}\n"
              "Manchetes da noite:\n" + "\n".join(f"- {t}" for t in titulos[:30]) +
              "\n\nDiz o tom do mercado para hoje, as 3 a 5 notícias que mais podem mexer com ações "
              "e que resultados vale a pena acompanhar.")
    texto = escrever(pedido)
    corpo = html.escape(texto) if texto else "\n".join(f"• {html.escape(t)}" for t in titulos[:8])
    try:
        redes = tendencias.em_alta(no_universo)
    except Exception:  # noqa: BLE001
        redes = []
    return (f"🌅 <b>Abertura {hoje:%d/%m}</b>\n{html.escape(_linha_mercados(m))}\n\n{corpo}"
            + (f"\n\n<b>Resultados hoje:</b> {html.escape(', '.join(linhas_res))}" if linhas_res else "")
            + (f"\n\n📣 <b>A ganhar atenção no Reddit</b> (menções nas últimas 24 h): {html.escape(', '.join(redes))}"
               if redes else ""))[:4000]


def resumo_fecho(hoje: pd.Timestamp, precos: pd.DataFrame, filtrado: pd.DataFrame, carteira: str,
                 cfg: dict) -> str:
    m = variacoes({k: v for k, v in cfg["mercados"].items() if not k.startswith("Futuros")})
    sobe, desce = maiores_movimentos(precos, filtrado, cfg["fecho"]["mais_subiram"], cfg["fecho"]["mais_desceram"])
    motivos = []
    for r in pd.concat([sobe, desce]).itertuples():
        tit = manchetes_acao(r.ticker, r.nome)
        motivos.append(f"{r.ticker} {r.var_pct:+.1f}%: " + (" / ".join(tit) if tit else "sem manchetes"))

    pedido = (f"Resumo de fecho de {hoje:%d/%m/%Y}.\nMercados hoje: {_linha_mercados(m)}\n"
              "Maiores movimentos do dia no universo e manchetes de cada uma:\n" +
              "\n".join(f"- {x}" for x in motivos) +
              "\n\nDiz como correu o dia e, para cada uma das maiores subidas e descidas, o motivo provável "
              "numa frase (se as manchetes não explicarem, diz que não há notícia clara).")
    texto = escrever(pedido)
    corpo = html.escape(texto) if texto else (
        "<b>Mais subiram:</b>\n" + "\n".join(_linhas_movimentos(sobe)) +
        "\n<b>Mais desceram:</b>\n" + "\n".join(_linhas_movimentos(desce)))
    return (f"🌙 <b>Fecho {hoje:%d/%m}</b>\n{html.escape(_linha_mercados(m))}\n\n{corpo}\n\n"
            f"<b>Sinais em papel:</b> {html.escape(carteira)}")[:4000]
