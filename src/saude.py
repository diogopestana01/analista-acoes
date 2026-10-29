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
