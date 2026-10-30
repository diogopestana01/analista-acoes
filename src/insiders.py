"""Compras de insiders (diretores e administradores) e short interest, da Nasdaq (grátis, sem chave).

Só informação, em modo sombra: aparece na lista de sinais e fica guardada no registo para medir no fim
da validação se acrescenta alguma coisa. Não mexe na ordem nem na decisão (regra de ouro).
  insiders  compras no mercado aberto (não contam prémios, opções nem vendas automáticas) nos últimos
            90 dias: quantas pessoas diferentes compraram. 3 ou mais = "compra em grupo", o padrão com
            mais estudos a favor.
  short     dias para cobrir (ações vendidas a descoberto / volume médio diário) no último relatório
            quinzenal; só para ações cotadas na Nasdaq.
A SEC bloqueia os servidores do GitHub; a Nasdaq responde (teste de 30/10 no "Teste das notícias").
"""
from __future__ import annotations

import logging
import re

import pandas as pd
import requests

from .resultados import CABECALHOS

log = logging.getLogger(__name__)
URL_INSIDERS = "https://api.nasdaq.com/api/company/{t}/insider-trades"
URL_SHORT = "https://api.nasdaq.com/api/quote/{t}/short-interest"
DIAS = 90
GRUPO = 3


def _num(txt) -> float | None:
    m = re.search(r"-?\d[\d,]*\.?\d*", str(txt or ""))
    return float(m.group().replace(",", "")) if m else None


def compra_mercado(tipo: str) -> bool:
    """'Buy' sim; 'Acquisition (Non Open Market)', 'Option Execute', prémios, não."""
    t = (tipo or "").lower()
    return "buy" in t and "non open market" not in t


def venda_mercado(tipo: str) -> bool:
    t = (tipo or "").lower()
    return "sell" in t and "automatic" not in t and "non open market" not in t


def resumir(linhas: list[dict], hoje: pd.Timestamp, dias: int = DIAS) -> dict:
    """Compras e vendas no mercado aberto nos últimos `dias` a partir da tabela da Nasdaq."""
    desde = pd.Timestamp(hoje).tz_localize(None).normalize() - pd.Timedelta(days=dias)
    compradores, compras, vendas, valor, ultima = set(), 0, 0, 0.0, None
    for r in linhas:
        d = pd.to_datetime(r.get("lastDate"), format="%m/%d/%Y", errors="coerce")
        if pd.isna(d) or d < desde:
            continue
        tipo = r.get("transactionType") or ""
        if compra_mercado(tipo):
            compras += 1
            compradores.add(r.get("insider"))
            valor += (_num(r.get("sharesTraded")) or 0) * (_num(r.get("lastPrice")) or 0)
            ultima = max(ultima, d) if ultima is not None else d
        elif venda_mercado(tipo):
            vendas += 1
    return {"compradores": len(compradores), "compras": compras, "vendas": vendas, "valor_compras": round(valor),
            "ultima_compra": f"{ultima:%Y-%m-%d}" if ultima is not None else None}


def insiders(ticker: str, hoje: pd.Timestamp, sessao=None) -> dict | None:
    if "." in ticker:
        return None
    try:
        r = (sessao or requests).get(URL_INSIDERS.format(t=ticker.replace("-", ".")), headers=CABECALHOS, timeout=15,
                                     params={"limit": 50, "type": "ALL", "sortColumn": "lastDate", "sortOrder": "DESC"})
        r.raise_for_status()
        tabela = (((r.json().get("data") or {}).get("transactionTable") or {}).get("table") or {})
        return resumir(tabela.get("rows") or [], hoje)
    except Exception as e:  # noqa: BLE001
        log.info("Insiders %s falhou: %s", ticker, e)
        return None


def short(ticker: str, sessao=None) -> dict | None:
    """{dias, dias_antes, data} do último relatório de short interest; None se não houver (fora da Nasdaq)."""
    if "." in ticker:
        return None
    try:
        r = (sessao or requests).get(URL_SHORT.format(t=ticker.replace("-", ".")), headers=CABECALHOS, timeout=15,
                                     params={"assetClass": "stocks"})
        r.raise_for_status()
        linhas = (((r.json().get("data") or {}).get("shortInterestTable") or {}).get("rows")) or []
        if not linhas:
            return None
        dias = [_num(x.get("daysToCover")) for x in linhas[:2]]
        return {"dias": dias[0], "dias_antes": dias[1] if len(dias) > 1 else None,
                "data": str(linhas[0].get("settlementDate"))}
    except Exception as e:  # noqa: BLE001
        log.info("Short %s falhou: %s", ticker, e)
        return None


def texto(ins: dict | None, sh: dict | None) -> str:
    """Linhas curtas para o alerta e a lista (vazio se não houver nada a dizer)."""
    partes = []
    if ins and ins.get("compradores"):
        n = ins["compradores"]
        grupo = " (compra em grupo)" if n >= GRUPO else ""
        partes.append(f"👔 {n} {'insider comprou' if n == 1 else 'insiders compraram'} ações nos últimos "
                      f"{DIAS} dias{grupo}")
    if sh and sh.get("dias") is not None and sh["dias"] >= 5:
        sobe = sh.get("dias_antes") is not None and sh["dias"] > sh["dias_antes"]
        partes.append(f"🩳 Muitas vendas a descoberto: {sh['dias']:.1f} dias para cobrir"
                      f"{' (a subir)' if sobe else ''}")
    return "\n".join(partes)
