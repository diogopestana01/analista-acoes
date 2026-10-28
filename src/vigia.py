"""Vigia das posições abertas: notícias de risco novas sobre ações que já foram compradas.

Em cada corrida dos sinais procura manchetes de risco publicadas depois da compra. Só chama o
Claude quando aparecem manchetes novas (cache por ação, dia e conjunto de manchetes), por isso
o custo é quase nulo. Se o advogado do diabo achar que há um facto concreto para vender já,
devolve um alerta. Quem decide a quem enviar é o diario.py.
"""
from __future__ import annotations

import hashlib
import html
import logging

import pandas as pd

from . import investigador

log = logging.getLogger(__name__)


def riscos_novos(riscos: pd.DataFrame, desde: str) -> pd.DataFrame:
    """Só as manchetes com data a partir do dia da compra (as sem data ficam de fora)."""
    if riscos.empty:
        return riscos
    d = pd.to_datetime(riscos["data"], utc=True, errors="coerce")
    return riscos[d >= pd.Timestamp(desde, tz="UTC")].reset_index(drop=True)


def vigiar(abertos: pd.DataFrame, nomes: dict, cache: dict, dia: str,
           pesquisar=investigador.pesquisar_riscos, rever=investigador.rever_posicao) -> list[dict]:
    """abertos: linhas do histórico em aberto. Devolve [{ticker, motivo, data_sinal}] para vender já."""
    alertas = []
    for pos in abertos.to_dict("records"):
        try:
            novos = riscos_novos(pesquisar(pos["ticker"], nomes.get(pos["ticker"], "")), str(pos["data_sinal"]))
        except Exception as e:  # noqa: BLE001
            log.warning("Vigia %s: pesquisa falhou: %s", pos["ticker"], e)
            continue
        if novos.empty:
            continue
        marca = hashlib.sha1("|".join(sorted(novos["titulo"].astype(str))).encode()).hexdigest()[:10]
        chave = f"vig1|{pos['ticker']}|{marca}|{dia}"
        if chave not in cache:
            r = rever(pos, novos)
            cache[chave] = r.model_dump() if r else {"sair": False, "motivo": ""}
            if cache[chave]["sair"]:
                alertas.append({"ticker": pos["ticker"], "motivo": cache[chave]["motivo"],
                                "data_sinal": pos["data_sinal"]})
    log.info("Vigia: %d posições vistas, %d alertas novos", len(abertos), len(alertas))
    return alertas


def formatar(a: dict) -> str:
    d = str(a["data_sinal"])
    return (f"🕵️ <b>Notícia nova sobre {html.escape(a['ticker'])}</b> (sinal de {d[8:10]}/{d[5:7]}, em aberto)\n"
            f"{html.escape(a['motivo'])}\n"
            "O advogado do diabo sugere vender já, sem esperar pelo stop. Confirma a notícia antes de decidir.")
