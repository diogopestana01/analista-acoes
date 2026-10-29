"""Saúde da corrida dos sinais: avisa só o dono quando algo deixa de funcionar.

Cada problema é avisado no máximo uma vez por dia (marca na cache das avaliações). O relógio
(.github/workflows/relogio.yml) vigia à parte se as corridas pararam ou falharam.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

log = logging.getLogger(__name__)


def problemas(ultimo_dia_precos, verificacoes: list[str], agora: pd.Timestamp) -> list[tuple[str, str]]:
    """[(código, texto)] dos problemas desta corrida."""
    out = []
    agora = pd.Timestamp(agora).tz_convert("UTC")
    hora = agora.hour + agora.minute / 60
    # Bolsa dos EUA aberta há mais de 30 min num dia útil e os preços ainda não são de hoje.
    if agora.weekday() < 5 and 14.0 <= hora <= 20.0 and pd.Timestamp(ultimo_dia_precos).date() < agora.date():
        out.append(("precos_velhos", f"Os preços guardados são de {pd.Timestamp(ultimo_dia_precos):%d/%m}: "
                                     "o Yahoo pode ter deixado de responder. Os sinais de hoje podem estar errados."))
    falhas = sum(v == "falhou" for v in verificacoes)
    if len(verificacoes) >= 3 and falhas / len(verificacoes) >= 0.5:
        out.append(("claude_falha", f"O Claude falhou em {falhas} de {len(verificacoes)} leituras de notícias. "
                                    "Os sinais seguem sem leitura de notícias (nada é apagado)."))
    return out


def avisar(lista: list[tuple[str, str]], cache: dict, dia: str, enviar=None) -> int:
    """Envia ao dono cada problema novo do dia. Devolve quantos foram enviados."""
    if enviar is None:
        from . import telegram
        enviar = lambda t: telegram.enviar(t, os.environ.get("TELEGRAM_CHAT_ID"))
    n = 0
    for codigo, texto in lista:
        chave = f"saude|{codigo}|{dia}"
        log.warning("Saúde: %s", texto)
        if chave in cache:
            continue
        cache[chave] = True
        try:
            enviar(f"⚠️ <b>Aviso do sistema</b>\n{texto}")
            n += 1
        except Exception as e:  # noqa: BLE001 — o aviso nunca pode parar os sinais
            log.warning("Aviso de saúde não enviado: %s", e)
    return n


NASDAQ = "https://api.nasdaq.com/api/quote/{t}/historical"
REFERENCIA = ["AAPL", "MSFT", "AMZN", "JPM", "XOM"]


def nasdaq_fecho(ticker: str) -> tuple[str, float] | None:
    """(data, fecho) do último dia completo na Nasdaq (fonte gratuita, só para conferir). None se falhar.
    O Stooq foi tentado primeiro mas bloqueia os servidores do GitHub com um desafio de JavaScript."""
    import requests

    from .resultados import CABECALHOS
    try:
        de = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        r = requests.get(NASDAQ.format(t=ticker), params={"assetclass": "stocks", "fromdate": de, "limit": 10},
                         headers=CABECALHOS, timeout=20)
        r.raise_for_status()
        linhas = ((r.json().get("data") or {}).get("tradesTable") or {}).get("rows") or []
        hoje = pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d")
        for l in linhas:   # mais recente primeiro; a de hoje pode estar incompleta
            dia = pd.to_datetime(l["date"], format="%m/%d/%Y").strftime("%Y-%m-%d")
            if dia < hoje:
                return dia, float(str(l["close"]).replace("$", "").replace(",", ""))
    except Exception as e:  # noqa: BLE001
        log.info("Nasdaq (conferência) %s falhou: %s", ticker, e)
    return None


def conferir_precos(precos: pd.DataFrame, buscar=nasdaq_fecho, limite: float = 0.02) -> list[tuple[str, str]]:
    """Compara o fecho de ações de referência no Yahoo (o que guardámos) com a Nasdaq, no mesmo dia.
    Diferença acima de `limite` = problema. Se a segunda fonte não responder, não há problema a reportar."""
    difs, vistos = [], 0
    for t in REFERENCIA:
        ref = buscar(t)
        if ref is None:
            continue
        dia, fecho = ref
        nossa = precos[(precos["ticker"] == t) & (pd.to_datetime(precos["data"]).dt.strftime("%Y-%m-%d") == dia)]
        if nossa.empty:
            continue
        vistos += 1
        d = abs(float(nossa["close"].iat[-1]) / fecho - 1)
        if d > limite:
            difs.append(f"{t} {d*100:.1f}%")
    log.info("Conferência de preços com a Nasdaq: %d ações comparadas, %d diferentes", vistos, len(difs))
    if difs:
        return [("precos_diferentes", "Os preços do Yahoo não batem com uma segunda fonte (Nasdaq): "
                                      + ", ".join(difs) + ". Confirma os preços na XTB antes de entrar.")]
    return []
