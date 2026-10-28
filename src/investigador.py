"""Investigador "advogado do diabo": tenta deitar abaixo cada sinal antes de ele sair.

Recebe o sinal técnico, a leitura das notícias já feita e as manchetes, e procura
razões concretas para NÃO comprar. Por agora só avisa e regista (config
investigador.bloquear = false); depois de algumas semanas compara-se o resultado
dos sinais contestados com os outros para decidir se passa a bloquear.
"""
from __future__ import annotations

import logging
import os
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from .sentimento import _COM_FALLBACK, MODELO

log = logging.getLogger(__name__)

INSTRUCOES = """És um investigador cético que trabalha contra o analista. O analista quer comprar esta ação
para um trade de 1 a 5 dias úteis. O teu trabalho é encontrar as melhores razões CONCRETAS para não o fazer:
notícia já descontada no preço, subida demasiado esticada, resultados ou outro evento nos próximos dias,
aumento de capital ou diluição, processo, downgrade, setor ou mercado a cair, notícia antiga a ser reciclada,
pouca liquidez. Usa só os dados e manchetes que recebes; não inventes factos.
Veredicto: "rejeitar" só se houver uma razão forte e concreta; "duvidoso" se há riscos relevantes mas não
decisivos; "manter" se não encontras nada sério. Escreve o argumento em português de Portugal, numa ou
duas frases curtas."""


class Contraditorio(BaseModel):
    veredicto: Literal["manter", "duvidoso", "rejeitar"]
    argumento: str = Field(description="A melhor razão para não comprar, em 1-2 frases")


def _pedido(s: dict, noticias: pd.DataFrame) -> str:
    linhas = "\n".join(f"- [{r.data:%Y-%m-%d}] {r.titulo}" if pd.notna(r.data) else f"- {r.titulo}"
                       for r in noticias.head(15).itertuples()) or "(sem manchetes)"
    fmt = lambda k, m=100, suf="%": f"{s[k] * m:+.1f}{suf}" if pd.notna(s.get(k)) else "?"
    return (f"Ação: {s['ticker']} | setup: {s['setup']} | entrada {s['entrada']:.2f}\n"
            f"Últimos 5 dias {fmt('ret5')}, 20 dias {fmt('ret20')}, distância à média de 50 dias "
            f"{fmt('dist_sma50')}, volatilidade diária {fmt('atr_pct')}, RSI14 "
            f"{s.get('rsi14', float('nan')):.0f}, volume {s.get('vol_ratio', float('nan')):.1f}x o normal\n"
            f"Histórico deste setup nesta ação: {s.get('n_historico', 0)} casos, "
            f"retorno esperado {fmt('retorno_esperado_liquido')}\n"
            f"Leitura do analista sobre as notícias: {s.get('impacto_noticias', 'neutro')} — "
            f"{s.get('resumo_noticias', '')}\n"
            f"Evento próximo assinalado: {'sim' if s.get('risco_evento') else 'não'}\n\n"
            f"Manchetes recentes:\n{linhas}")


def contestar(s: dict, noticias: pd.DataFrame, cliente=None) -> Contraditorio | None:
    """None quando não há chave da Anthropic ou o pedido falha (o sinal segue sem contraditório)."""
    if cliente is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        cliente = cliente or anthropic.Anthropic()
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
            if MODELO in _COM_FALLBACK else {}
        resp = cliente.beta.messages.parse(
            model=MODELO, max_tokens=1500, system=INSTRUCOES,
            messages=[{"role": "user", "content": _pedido(s, noticias)}],
            output_config={"effort": "low"}, output_format=Contraditorio, **extra)
        return resp.parsed_output
    except Exception as e:
        log.warning("Investigador falhou para %s: %s", s.get("ticker"), e)
        return None
