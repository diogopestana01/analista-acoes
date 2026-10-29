"""Análise das notícias de cada ação candidata com o Claude.

Precisa da variável de ambiente ANTHROPIC_API_KEY. Sem ela, ou se o pedido falhar, a leitura
fica neutra e o sinal segue igual (nunca há uma leitura "de recurso" que possa mudar o sinal).
Tudo o que o Claude devolve passa pelo verificador (src/verificador.py) antes de ser usado.
"""
from __future__ import annotations

import logging
import os
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from . import verificador

log = logging.getLogger(__name__)

N_MANCHETES = 20
MODELO = os.environ.get("MODELO_CLAUDE") or "claude-haiku-5-5"
# Só alguns modelos aceitam o "fallback" automático do servidor quando recusam um pedido.
_COM_FALLBACK = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")

INSTRUCOES = """És um analista de ações focado em operações de 1 dia a 1 semana.
Recebes um sinal técnico e as manchetes recentes de uma ação, numeradas [1], [2], ...
Avalia se as manchetes apoiam, contrariam ou são neutras para esse sinal no horizonte do trade.
Dá mais peso a resultados, guidance, upgrades/downgrades, fusões, regulação e processos judiciais.
Ignora notícias genéricas de mercado e manchetes sobre outra empresa com nome parecido.
Diz também se o movimento recente do preço tem uma causa concreta nas manchetes (causa_fundamental).
Regras para não errar:
- Usa SÓ o que está nas manchetes. Não acrescentes factos, números ou datas que lá não estejam.
- Em "fontes" põe os números das manchetes em que te baseias. Leitura "apoia" ou "contraria" sem
  fontes é descartada.
- Não faças contas de datas: a data de hoje e os dias úteis até aos eventos vêm no pedido.
Se não houver manchetes relevantes, o sentimento é 0, o impacto "neutro" e as fontes ficam vazias.
Escreve o resumo em português de Portugal, numa frase curta."""


class _Leitura(BaseModel):
    """O que o Claude devolve."""
    sentimento: float = Field(description="-1 (muito negativo) a 1 (muito positivo)")
    impacto: Literal["apoia", "contraria", "neutro"]
    risco_evento: bool = Field(description="Há um evento próximo (resultados, decisão) que pode causar um gap")
    causa_fundamental: bool = Field(default=False, description=(
        "O movimento recente do preço (a subida ou a queda dos últimos dias) é explicado por uma notícia "
        "concreta da empresa: resultados, previsões, recomendação de analista, processo, regulação, fusão"))
    resumo: str
    fontes: list[int] = Field(default_factory=list, description="Números das manchetes em que te baseias")


class Avaliacao(_Leitura):
    """A leitura depois de passar pelo verificador (src/verificador.py)."""
    verificacao: str = Field(default="", description="ok | numeros_cortados | sem_citacao | sem_claude | falhou")


def neutra(resumo: str, verificacao: str) -> Avaliacao:
    return Avaliacao(sentimento=0, impacto="neutro", risco_evento=False, resumo=resumo, verificacao=verificacao)


def verificar(leitura: _Leitura, noticias: pd.DataFrame, pedido: str, agora=None) -> Avaliacao:
    """Regras fixas: sem citação válida não há opinião; frases com números que não estão nas fontes caem."""
    citavel = noticias.head(N_MANCHETES).reset_index(drop=True)
    fontes = verificador.citacoes_validas(leitura.fontes, citavel, agora)
    if not fontes and (leitura.impacto != "neutro" or leitura.causa_fundamental):
        return neutra(verificador.SEM_FACTOS, "sem_citacao")
    resumo, inteiro = verificador.limpar_texto(leitura.resumo, citavel, pedido)
    return Avaliacao(**{**leitura.model_dump(), "fontes": fontes, "resumo": resumo or verificador.SEM_FACTOS,
                        "verificacao": "ok" if inteiro else "numeros_cortados"})


def avaliar(sinal: dict, noticias: pd.DataFrame, cliente=None, agora=None, registo: dict | None = None) -> Avaliacao:
    """Leitura verificada das notícias. Se o Claude não estiver disponível ou falhar, fica neutra:
    uma falha nunca muda o sinal. `registo` (opcional) recebe o pedido, a resposta bruta e a verificada."""
    if noticias.empty:
        return neutra("Sem notícias recentes.", "ok")
    if cliente is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return neutra("", "sem_claude")

    import anthropic

    cliente = cliente or anthropic.Anthropic()
    pedido = (f"{verificador.contexto_datas(agora, sinal.get('resultados_em'), int(sinal.get('max_dias') or 5))}\n"
              f"Ação: {sinal['ticker']}\nSinal técnico: {sinal['direcao']} ({sinal['setup']}), "
              f"entrada {sinal['entrada']:.2f}\n\n"
              f"Manchetes:\n{verificador.linhas_numeradas(noticias, N_MANCHETES, com_resumo=True, agora=agora)}")
    try:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
            if MODELO in _COM_FALLBACK else {}
        resp = cliente.beta.messages.parse(
            model=MODELO,
            max_tokens=2000,
            system=INSTRUCOES,
            messages=[{"role": "user", "content": pedido}],
            output_config={"effort": "low"},
            output_format=_Leitura,
            **extra,
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise RuntimeError(f"sem resposta utilizável ({resp.stop_reason})")
        final = verificar(resp.parsed_output, noticias, pedido, agora)
        if registo is not None:
            registo.update(pedido=pedido, bruto=resp.parsed_output.model_dump(), verificado=final.model_dump())
        return final
    except Exception as e:
        log.warning("Claude falhou para %s (%s); segue sem leitura de notícias", sinal["ticker"], e)
        if registo is not None:
            registo.update(pedido=pedido, erro=str(e)[:300])
        return neutra("", "falhou")
