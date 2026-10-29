"""Formata e envia alertas para o Telegram.

Precisa de TELEGRAM_TOKEN (do @BotFather) e TELEGRAM_CHAT_ID (o teu chat).
Sem estas variáveis, as mensagens são só impressas no ecrã.
"""
from __future__ import annotations

import html
import json
import logging
import os

import pandas as pd
import requests

from . import saidas

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
SETUPS_PT = {"rompimento": "Rompimento em alta", "pullback": "Correção em tendência de alta",
             "gap_volume": "Salto na abertura com volume forte",
             "semana_forte": "Semana forte com volume",
             "rompimento_baixa": "Rompimento em baixa"}


def _num(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


def formatar_sinal(s: dict) -> str:
    seta = "🟢 COMPRA" if s["direcao"] == "compra" else "🔴 VENDA (CFD)"
    tem_obj, tem_stop = pd.notna(s.get("objetivo")), pd.notna(s.get("stop"))
    regra = saidas.REGRAS.get(s.get("saida") or "", None)
    dias = int(round(s["dias_estimados"]))
    acerto = (f"{s['taxa_acerto'] * 100:.0f}% a ganhar em {int(s['n_historico'])} casos"
              if s.get("n_historico", 0) and pd.notna(s.get("taxa_acerto")) else "sem histórico suficiente")
    linhas = [
        f"<b>{seta} {html.escape(s['ticker'])}</b>" + ("  📰 notícia forte" if s.get("noticia_forte") else ""),
        f"Entrada: <b>{_num(s['entrada'])}</b>",
    ]
    if tem_obj:
        linhas.append(f"Objetivo: <b>{_num(s['objetivo'])}</b> (+{abs(s['objetivo'] / s['entrada'] - 1) * 100:.1f}%)")
    if regra and regra.sair_se:
        linhas.append(f"Saída: <b>{regra.texto_alerta()}</b> (aviso-te quando for)")
    if tem_stop:
        linhas.append(f"Stop-loss: {_num(s['stop'])} (−{abs(s['stop'] / s['entrada'] - 1) * 100:.1f}%)")
    linhas += [
        f"Prazo estimado: <b>{dias} dia{'s' if dias != 1 else ''} úte{'is' if dias != 1 else 'l'}</b>",
        f"Setup: {SETUPS_PT.get(s['setup'], s['setup'])}",
    ]
    if pd.notna(s.get("retorno_esperado_liquido")):
        custo = " (já desconta o câmbio da XTB)" if s.get("custo", 0) > 0 else ""
        linhas.append(f"Retorno médio esperado: <b>{s['retorno_esperado_liquido'] * 100:+.1f}%</b>{custo}")
    if "n_historico" in s:  # os sinais lidos do histórico não trazem esta estatística
        linhas.append(f"Histórico nesta ação: {acerto}")
    if s.get("resumo_noticias"):
        linhas.append(f"Notícias: {html.escape(str(s['resumo_noticias']))}")
    from .tendencias import linha_alerta
    if (redes := linha_alerta(s)):
        linhas.append(redes)
    if s.get("argumento_inv") and isinstance(s.get("argumento_inv"), str):
        icone = {"rejeitar": "🚩", "duvidoso": "⚠️", "manter": "✔️"}.get(s.get("veredicto_inv"), "🕵️")
        linhas.append(f"{icone} Advogado do diabo ({html.escape(str(s.get('veredicto_inv')))}): "
                      f"{html.escape(s['argumento_inv'])}")
    if isinstance(s.get("resultados_em"), str):
        d = s["resultados_em"]
        linhas.append(f"📅 Resultados a {d[8:10]}/{d[5:7]}, dentro do prazo do trade: considera vender antes.")
    elif s.get("risco_evento"):
        linhas.append("⚠️ Evento próximo (ex. resultados): risco de gap.")
    return "\n".join(linhas)


def formatar_fecho(f: dict) -> str:
    icone = {"objetivo": "✅", "stop": "🛑", "expirou": "⏱️", "regra": "🔔"}[f["resultado"]]
    texto = {"objetivo": "Objetivo atingido: vende/fecha a posição",
             "stop": "Stop-loss atingido: fecha a posição",
             "expirou": "Prazo terminou: fecha a posição",
             "regra": "Sinal de saída ao fecho: vende/fecha a posição"}[f["resultado"]]
    return (f"{icone} <b>{html.escape(f['ticker'])}</b> ({f['direcao']} de {f['data_sinal']})\n"
            f"{texto}. Entrada {_num(f['entrada'])}, último preço {_num(f['preco_atual'])} "
            f"({f['variacao_pct']:+.1f}%).")


def teclado(botoes: list[list[tuple[str, str]]] | None) -> str:
    """Botões por baixo da mensagem: linhas de (texto, dados devolvidos ao bot)."""
    return json.dumps({"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in linha]
                                           for linha in (botoes or [])]})


def api(metodo: str, espera: int = 30, **dados) -> dict | None:
    """Chamada à API do Telegram. Devolve o "result" ou None se falhar."""
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        print(metodo, dados, "\n")
        return None
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{metodo}", data=dados, timeout=espera)
    except requests.RequestException as e:
        log.error("Telegram %s falhou: %s", metodo, e)
        return None
    if not r.ok:
        log.error("Telegram %s falhou: %s %s", metodo, r.status_code, r.text[:200])
        return None
    return r.json().get("result")


def enviar(texto: str, chat_id: str | int | None = None, botoes: list | None = None,
           silencioso: bool = False) -> bool:
    token, chat = os.environ.get("TELEGRAM_TOKEN"), chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(texto, "\n")
        return False
    dados = {"chat_id": chat, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if silencioso:
        dados["disable_notification"] = "true"
    if botoes:
        dados["reply_markup"] = teclado(botoes)
    r = requests.post(API.format(token=token), timeout=30, data=dados)
    if not r.ok:
        log.error("Telegram falhou: %s %s", r.status_code, r.text[:200])
    return r.ok
