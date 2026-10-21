"""Análise das notícias de cada ação candidata com o Claude.

Precisa da variável de ambiente ANTHROPIC_API_KEY. Sem ela, usa uma análise
simples por palavras-chave para o modelo continuar a funcionar.
"""
from __future__ import annotations

import logging
import os
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

MODELO = os.environ.get("MODELO_CLAUDE") or "claude-haiku-5-5"
# Só alguns modelos aceitam o "fallback" automático do servidor quando recusam um pedido.
_COM_FALLBACK = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")

INSTRUCOES = """És um analista de ações focado em operações de 1 dia a 1 semana.
Recebes um sinal técnico e as notícias recentes de uma ação. Avalia se as notícias
apoiam, contrariam ou são neutras para esse sinal no horizonte de 1 a 5 dias úteis.
Dá mais peso a resultados, guidance, upgrades/downgrades, fusões, regulação e
processos judiciais. Ignora notícias genéricas de mercado. Se não houver notícias
relevantes, o sentimento é 0 e o impacto "neutro". Escreve o resumo em português de Portugal,
numa frase curta."""


class Avaliacao(BaseModel):
    sentimento: float = Field(description="-1 (muito negativo) a 1 (muito positivo)")
    impacto: Literal["apoia", "contraria", "neutro"]
    risco_evento: bool = Field(description="Há um evento próximo (resultados, decisão) que pode causar um gap")
    resumo: str


_POS = ("beat", "beats", "upgrade", "raises", "record", "surge", "jumps", "approval", "wins", "buyback")
_NEG = ("miss", "misses", "downgrade", "cuts", "lawsuit", "probe", "plunge", "falls", "recall", "fraud")


def _por_palavras(sinal: dict, noticias: pd.DataFrame) -> Avaliacao:
    texto = " ".join(noticias["titulo"].fillna("")).lower()
    pos = sum(texto.count(p) for p in _POS)
    neg = sum(texto.count(p) for p in _NEG)
    sent = 0.0 if pos + neg == 0 else (pos - neg) / (pos + neg)
    direcao = 1 if sinal["direcao"] == "compra" else -1
    impacto = "neutro" if abs(sent) < 0.2 else ("apoia" if sent * direcao > 0 else "contraria")
    return Avaliacao(sentimento=sent, impacto=impacto, risco_evento="earnings" in texto,
                     resumo=f"Análise por palavras-chave: {pos} positivas, {neg} negativas.")


def avaliar(sinal: dict, noticias: pd.DataFrame, cliente=None) -> Avaliacao:
    if noticias.empty:
        return Avaliacao(sentimento=0, impacto="neutro", risco_evento=False, resumo="Sem notícias recentes.")
    if cliente is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return _por_palavras(sinal, noticias)

    import anthropic

    cliente = cliente or anthropic.Anthropic()
    linhas = "\n".join(
        f"- [{r.data:%Y-%m-%d}] {r.fonte}: {r.titulo}. {r.resumo or ''}" if pd.notna(r.data)
        else f"- {r.fonte}: {r.titulo}. {r.resumo or ''}"
        for r in noticias.head(20).itertuples()
    )
    pedido = (f"Ação: {sinal['ticker']}\nSinal técnico: {sinal['direcao']} ({sinal['setup']}), "
              f"entrada {sinal['entrada']:.2f}, objetivo {sinal['objetivo']:.2f}, stop {sinal['stop']:.2f}\n\n"
              f"Notícias:\n{linhas}")
    try:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
            if MODELO in _COM_FALLBACK else {}
        resp = cliente.beta.messages.parse(
            model=MODELO,
            max_tokens=2000,
            system=INSTRUCOES,
            messages=[{"role": "user", "content": pedido}],
            output_config={"effort": "low"},
            output_format=Avaliacao,
            **extra,
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise RuntimeError(f"sem resposta utilizável ({resp.stop_reason})")
        return resp.parsed_output
    except Exception as e:
        log.warning("Claude falhou para %s (%s); uso palavras-chave", sinal["ticker"], e)
        return _por_palavras(sinal, noticias)
