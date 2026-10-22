"""Radar de resultados: que empresas dos EUA apresentam resultados nos próximos dias.

Usa o calendário público da Nasdaq (grátis, sem chave). Comprar 1 a 2 dias antes
de resultados é uma aposta no número, não no padrão técnico, por isso esses
sinais são retirados. Para a Europa fica o aviso de evento dado pela análise de notícias.
"""
from __future__ import annotations

import logging

import pandas as pd
import requests

log = logging.getLogger(__name__)

URL = "https://api.nasdaq.com/api/calendar/earnings"
CABECALHOS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0 Safari/537.36",
              "Accept": "application/json, text/plain, */*"}


def tickers_do_dia(resposta: dict) -> set[str]:
    linhas = ((resposta or {}).get("data") or {}).get("rows") or []
    return {str(r["symbol"]).strip().upper().replace(".", "-") for r in linhas if r.get("symbol")}


QUANDO = {"time-pre-market": "antes da abertura", "time-after-hours": "depois do fecho"}


def linhas_do_dia(resposta: dict) -> list[dict]:
    out = []
    for r in ((resposta or {}).get("data") or {}).get("rows") or []:
        if not r.get("symbol"):
            continue
        try:
            valor = float(str(r.get("marketCap", "")).replace("$", "").replace(",", "") or 0)
        except ValueError:
            valor = 0.0
        out.append({"ticker": str(r["symbol"]).strip().upper().replace(".", "-"),
                    "nome": str(r.get("name", "")).strip(),
                    "quando": QUANDO.get(r.get("time"), "hora por confirmar"),
                    "valor_mercado": valor})
    return out


def _pedir(d: pd.Timestamp) -> dict:
    r = requests.get(URL, params={"date": f"{d:%Y-%m-%d}"}, headers=CABECALHOS, timeout=20)
    r.raise_for_status()
    return r.json()


def calendario(dia: pd.Timestamp) -> list[dict]:
    """Empresas dos EUA com resultados nesse dia (lista vazia se o calendário falhar)."""
    try:
        return linhas_do_dia(_pedir(dia))
    except Exception as e:
        log.warning("Calendário de resultados indisponível para %s: %s", f"{dia:%Y-%m-%d}", e)
        return []


def proximos(hoje: pd.Timestamp, dias_uteis: int = 2) -> set[str]:
    """Tickers com resultados de hoje até daqui a `dias_uteis` dias úteis."""
    datas = pd.bdate_range(hoje.normalize().tz_localize(None), periods=dias_uteis + 1)
    out: set[str] = set()
    for d in datas:
        try:
            out |= tickers_do_dia(_pedir(d))
        except Exception as e:  # sem calendário o modelo continua, só sem este filtro
            log.warning("Calendário de resultados indisponível para %s: %s", f"{d:%Y-%m-%d}", e)
    log.info("Empresas com resultados nos próximos %d dias úteis: %d", dias_uteis, len(out))
    return out
