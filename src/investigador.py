"""Investigador "advogado do diabo": tenta deitar abaixo cada sinal antes de ele sair.

Trabalha à parte do analista de notícias: recebe o sinal, a leitura do analista e as
manchetes dele, mas faz também a SUA pesquisa, só à procura de risco (downgrades,
processos, emissões de ações, investigações, short sellers) e lê as comunicações
oficiais à SEC. Depois procura razões concretas e novas para NÃO comprar. Por agora só avisa e regista (config
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
para um trade de 1 a 5 dias úteis. O teu trabalho é encontrar razões CONCRETAS e ESPECÍFICAS desta empresa
para não o fazer, sobretudo nas manchetes da tua pesquisa de risco e nas comunicações à SEC:
emissão de ações ou diluição, processo ou investigação, downgrade, corte de previsões, saída de gestores,
contas não fiáveis, aviso de exclusão da bolsa, resultados ou outro evento dentro do prazo do trade,
notícia que explica o movimento e já está no preço, notícia antiga a ser reciclada.
NÃO contam como razão: a ação ter subido muito, RSI alto ou volume alto. O modelo compra força de propósito
e esses números já foram testados no histórico. Riscos genéricos ("pode corrigir", "volatilidade alta",
"mercado incerto") também não contam.
Usa só os dados e manchetes que recebes; não inventes factos. Se uma manchete de risco for sobre outra
empresa com nome parecido, ignora-a.
Veredicto: "rejeitar" se há um facto concreto que provavelmente faz o trade perder (ex.: emissão de ações
anunciada, resultados amanhã, investigação nova); "duvidoso" se há um facto concreto que pesa mas não decide;
"manter" se não encontras nenhum facto concreto, o que deve ser o caso mais comum.
Risco de 0 (nada) a 10 (quase certo que corre mal). Escreve o argumento em português de Portugal, numa ou
duas frases curtas, a citar o facto."""


class Contraditorio(BaseModel):
    veredicto: Literal["manter", "duvidoso", "rejeitar"]
    risco: int = Field(default=0, ge=0, le=10, description="0 = sem factos de risco, 10 = quase certo que corre mal")
    argumento: str = Field(description="A melhor razão para não comprar, em 1-2 frases")


def _linhas(noticias: pd.DataFrame | None, n: int) -> str:
    if noticias is None or noticias.empty:
        return "(nada encontrado)"
    return "\n".join(f"- [{r.data:%Y-%m-%d}] {r.fonte}: {r.titulo}" if pd.notna(r.data) else f"- {r.fonte}: {r.titulo}"
                     for r in noticias.head(n).itertuples())


def _pedido(s: dict, noticias: pd.DataFrame, riscos: pd.DataFrame | None = None) -> str:
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
            f"Manchetes do analista:\n{_linhas(noticias, 15)}\n\n"
            f"A TUA pesquisa de risco (últimos 14 dias):\n{_linhas(riscos, 10)}")


def pesquisar_riscos(ticker: str, nome: str = "") -> pd.DataFrame:
    """Pesquisa própria do investigador. Falhas não param nada: devolve o que conseguir."""
    from . import noticias as fontes

    linhas = []
    for f in (lambda: fontes.riscos_google(ticker, nome), lambda: fontes.noticias_sec(ticker)):
        try:
            linhas += f()
        except Exception as e:
            log.warning("Pesquisa de risco %s falhou: %s", ticker, e)
    df = pd.DataFrame(linhas, columns=["ticker", "titulo", "resumo", "fonte", "data", "url"])
    df["data"] = pd.to_datetime(df["data"], utc=True, errors="coerce", format="mixed")
    # O Google nem sempre respeita o "when:14d": notícias de risco antigas já estão no preço.
    df = df[df["data"].isna() | (df["data"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=21))]
    return df.drop_duplicates("titulo").sort_values("data", ascending=False).reset_index(drop=True)


def contestar(s: dict, noticias: pd.DataFrame, riscos: pd.DataFrame | None = None,
              cliente=None) -> Contraditorio | None:
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
            messages=[{"role": "user", "content": _pedido(s, noticias, riscos)}],
            output_config={"effort": "low"}, output_format=Contraditorio, **extra)
        return resp.parsed_output
    except Exception as e:
        log.warning("Investigador falhou para %s: %s", s.get("ticker"), e)
        return None
