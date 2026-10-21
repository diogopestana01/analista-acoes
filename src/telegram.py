"""Formata e envia alertas para o Telegram.

Precisa de TELEGRAM_TOKEN (do @BotFather) e TELEGRAM_CHAT_ID (o teu chat).
Sem estas variáveis, as mensagens são só impressas no ecrã.
"""
from __future__ import annotations

import html
import logging
import os

import pandas as pd
import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
SETUPS_PT = {"rompimento": "Rompimento em alta", "pullback": "Correção em tendência de alta",
             "gap_volume": "Salto na abertura com volume forte",
             "rompimento_baixa": "Rompimento em baixa"}


def _num(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


def formatar_sinal(s: dict) -> str:
    seta = "🟢 COMPRA" if s["direcao"] == "compra" else "🔴 VENDA (CFD)"
    ganho = abs(s["objetivo"] / s["entrada"] - 1) * 100
    perda = abs(s["stop"] / s["entrada"] - 1) * 100
    dias = int(round(s["dias_estimados"]))
    acerto = (f"{s['taxa_acerto'] * 100:.0f}% em {int(s['n_historico'])} casos"
              if s.get("n_historico", 0) and pd.notna(s.get("taxa_acerto")) else "sem histórico suficiente")
    linhas = [
        f"<b>{seta} {html.escape(s['ticker'])}</b>" + ("  📰 notícia forte" if s.get("noticia_forte") else ""),
        f"Entrada: <b>{_num(s['entrada'])}</b>",
        f"Objetivo: <b>{_num(s['objetivo'])}</b> (+{ganho:.1f}%)",
        f"Stop-loss: {_num(s['stop'])} (−{perda:.1f}%)",
        f"Prazo estimado: <b>{dias} dia{'s' if dias != 1 else ''} úte{'is' if dias != 1 else 'l'}</b>",
        f"Setup: {SETUPS_PT.get(s['setup'], s['setup'])}",
    ]
    if pd.notna(s.get("retorno_esperado_liquido")):
        custo = " (já desconta o câmbio da XTB)" if s.get("custo", 0) > 0 else ""
        linhas.append(f"Retorno médio esperado: <b>{s['retorno_esperado_liquido'] * 100:+.1f}%</b>{custo}")
    linhas += [
        f"Histórico deste setup: {acerto}",
    ]
    if s.get("resumo_noticias"):
        linhas.append(f"Notícias: {html.escape(str(s['resumo_noticias']))}")
    if s.get("risco_evento"):
        linhas.append("⚠️ Evento próximo (ex. resultados): risco de gap.")
    return "\n".join(linhas)


def formatar_fecho(f: dict) -> str:
    icone = {"objetivo": "✅", "stop": "🛑", "expirou": "⏱️"}[f["resultado"]]
    texto = {"objetivo": "Objetivo atingido: vende/fecha a posição",
             "stop": "Stop-loss atingido: fecha a posição",
             "expirou": "Prazo terminou sem objetivo: considera fechar"}[f["resultado"]]
    return (f"{icone} <b>{html.escape(f['ticker'])}</b> ({f['direcao']} de {f['data_sinal']})\n"
            f"{texto}. Entrada {_num(f['entrada'])}, último preço {_num(f['preco_atual'])} "
            f"({f['variacao_pct']:+.1f}%).")


def enviar(texto: str) -> bool:
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(texto, "\n")
        return False
    r = requests.post(API.format(token=token), timeout=30, data={
        "chat_id": chat, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": "true"})
    if not r.ok:
        log.error("Telegram falhou: %s %s", r.status_code, r.text[:200])
    return r.ok
