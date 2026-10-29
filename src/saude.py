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


STOOQ = "https://stooq.com/q/d/l/?s={t}.us&i=d"
REFERENCIA = ["AAPL", "MSFT", "AMZN", "JPM", "XOM"]


def stooq_fecho(ticker: str) -> tuple[str, float] | None:
    """(data, fecho) do último dia no Stooq (fonte gratuita, só para conferir). None se falhar."""
    import io

    import requests
    try:
        r = requests.get(STOOQ.format(t=ticker.lower()), timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        d = pd.read_csv(io.StringIO(r.text)).dropna(subset=["Close"])
        return str(d["Date"].iat[-1]), float(d["Close"].iat[-1])
    except Exception as e:  # noqa: BLE001
        log.info("Stooq %s falhou: %s", ticker, e)
        return None


def conferir_precos(precos: pd.DataFrame, buscar=stooq_fecho, limite: float = 0.02) -> list[tuple[str, str]]:
    """Compara o fecho de ações de referência no Yahoo (o que guardámos) com o Stooq, no mesmo dia.
    Diferença acima de `limite` = problema. Se o Stooq não responder, não há problema a reportar."""
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
    log.info("Conferência de preços com o Stooq: %d ações comparadas, %d diferentes", vistos, len(difs))
    if difs:
        return [("precos_diferentes", "Os preços do Yahoo não batem com uma segunda fonte (Stooq): "
                                      + ", ".join(difs) + ". Confirma os preços na XTB antes de entrar.")]
    return []
