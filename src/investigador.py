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

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from . import verificador
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
Usa só os dados e manchetes que recebes; não inventes factos, números ou datas. Se uma manchete de risco
for sobre outra empresa com nome parecido, ignora-a. As manchetes vêm numeradas [1], [2], ...: em "fontes"
põe os números das manchetes que provam o teu facto. "duvidoso" ou "rejeitar" sem fontes é descartado,
exceto quando o facto são os resultados dentro do prazo indicados no pedido.
Não faças contas de datas: a data de hoje e os dias úteis até aos resultados já vêm calculados no pedido.
Veredicto: "rejeitar" se há um facto concreto que provavelmente faz o trade perder (ex.: emissão de ações
anunciada, resultados amanhã, investigação nova); "duvidoso" se há um facto concreto que pesa mas não decide;
"manter" se não encontras nenhum facto concreto, o que deve ser o caso mais comum.
Risco de 0 (nada) a 10 (quase certo que corre mal). Escreve o argumento em português de Portugal, numa ou
duas frases curtas, a citar o facto."""


N_ANALISTA, N_RISCO = 15, 10
SEM_FACTO = "Sem facto verificável contra o sinal."


class _Resposta(BaseModel):
    """O que o Claude devolve."""
    veredicto: Literal["manter", "duvidoso", "rejeitar"]
    risco: int = Field(default=0, ge=0, le=10, description="0 = sem factos de risco, 10 = quase certo que corre mal")
    argumento: str = Field(description="A melhor razão para não comprar, em 1-2 frases")
    fontes: list[int] = Field(default_factory=list, description="Números das manchetes que provam o facto")


class Contraditorio(_Resposta):
    """A resposta depois do verificador (src/verificador.py)."""
    verificacao: str = Field(default="", description="ok | numeros_cortados | sem_citacao")


def _resultados_no_prazo(s: dict, agora=None) -> bool:
    r = s.get("resultados_em")
    if not isinstance(r, str) or not r:
        return False
    n = int(np.busday_count(verificador.hoje_utc(agora).date(), pd.Timestamp(r).date()))
    return 0 <= n <= int(s.get("max_dias") or 5)


def verificar(r: _Resposta, s: dict, citavel: pd.DataFrame, pedido: str, agora=None) -> Contraditorio:
    """Sem citação válida (nem resultados no prazo, que o código conhece) o veredicto passa a "manter"."""
    fontes = verificador.citacoes_validas(r.fontes, citavel, agora)
    if r.veredicto != "manter" and not fontes and not _resultados_no_prazo(s, agora):
        return Contraditorio(veredicto="manter", risco=min(r.risco, 2), argumento=SEM_FACTO, verificacao="sem_citacao")
    argumento, inteiro = verificador.limpar_texto(r.argumento, citavel, pedido)
    return Contraditorio(**{**r.model_dump(), "fontes": fontes, "argumento": argumento or SEM_FACTO,
                            "verificacao": "ok" if inteiro else "numeros_cortados"})


def _pedido(s: dict, noticias: pd.DataFrame, riscos: pd.DataFrame | None = None, agora=None) -> str:
    fmt = lambda k, m=100, suf="%": f"{s[k] * m:+.1f}{suf}" if pd.notna(s.get(k)) else "?"
    n_an = 0 if noticias is None else len(noticias.head(N_ANALISTA))
    return (f"{verificador.contexto_datas(agora, s.get('resultados_em'), int(s.get('max_dias') or 5))}\n"
            f"Ação: {s['ticker']} | setup: {s['setup']} | entrada {s['entrada']:.2f}\n"
            f"Últimos 5 dias {fmt('ret5')}, 20 dias {fmt('ret20')}, distância à média de 50 dias "
            f"{fmt('dist_sma50')}, volatilidade diária {fmt('atr_pct')}, RSI14 "
            f"{s.get('rsi14', float('nan')):.0f}, volume {s.get('vol_ratio', float('nan')):.1f}x o normal\n"
            f"Histórico deste setup nesta ação: {s.get('n_historico', 0)} casos, "
            f"retorno esperado {fmt('retorno_esperado_liquido')}\n"
            f"Leitura do analista sobre as notícias: {s.get('impacto_noticias', 'neutro')} — "
            f"{s.get('resumo_noticias', '')}\n"
            f"Resultados no calendário da Nasdaq: {s['resultados_em'] if isinstance(s.get('resultados_em'), str) else 'nenhum nos próximos 5 dias úteis'}\n\n"
            f"Manchetes do analista:\n{verificador.linhas_numeradas(noticias, N_ANALISTA, agora=agora)}\n\n"
            f"A TUA pesquisa de risco (últimos 14 dias):\n"
            f"{verificador.linhas_numeradas(riscos, N_RISCO, inicio=n_an + 1, agora=agora)}")


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
              cliente=None, agora=None) -> Contraditorio | None:
    """None quando não há chave da Anthropic ou o pedido falha (o sinal segue sem contraditório)."""
    if cliente is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        cliente = cliente or anthropic.Anthropic()
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
            if MODELO in _COM_FALLBACK else {}
        pedido = _pedido(s, noticias, riscos, agora)
        resp = cliente.beta.messages.parse(
            model=MODELO, max_tokens=1500, system=INSTRUCOES,
            messages=[{"role": "user", "content": pedido}],
            output_config={"effort": "low"}, output_format=_Resposta, **extra)
        if resp.parsed_output is None:
            return None
        citavel = verificador.tabela_citavel((noticias, N_ANALISTA), (riscos, N_RISCO))
        return verificar(resp.parsed_output, s, citavel, pedido, agora)
    except Exception as e:
        log.warning("Investigador falhou para %s: %s", s.get("ticker"), e)
        return None


INSTRUCOES_POSICAO = """Vigias uma posição de compra já aberta (trade de 1 a 5 dias úteis). Recebes as manchetes de risco
publicadas DEPOIS da compra. Diz se há um facto novo, concreto e desta empresa que justifique vender já, antes do
stop: emissão de ações, investigação ou processo novo, downgrade forte, corte de previsões, saída do CEO, aviso
de exclusão da bolsa, contas não fiáveis. Notícias genéricas, de outras empresas, antigas ou já conhecidas
antes da compra não contam: nesse caso sair = false. As manchetes vêm numeradas: em "fontes" põe os números
das que provam o facto (sair = true sem fontes é descartado). Não inventes factos, números ou datas.
Escreve o motivo em português de Portugal, numa frase."""


class AlertaPosicao(BaseModel):
    sair: bool = Field(description="true só se houver um facto novo e concreto que justifique vender já")
    motivo: str
    fontes: list[int] = Field(default_factory=list, description="Números das manchetes que provam o facto")


def rever_posicao(pos: dict, riscos: pd.DataFrame, cliente=None, agora=None) -> AlertaPosicao | None:
    """None sem chave da Anthropic ou se o pedido falhar."""
    if cliente is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic

        cliente = cliente or anthropic.Anthropic()
        pedido = (f"{verificador.contexto_datas(agora)}\n"
                  f"Posição: {pos['ticker']} comprada a {pos['entrada']:.2f} em {pos['data_sinal']} "
                  f"(setup {pos['setup']}).\n\nManchetes de risco depois da compra:\n"
                  f"{verificador.linhas_numeradas(riscos, N_RISCO, agora=agora)}")
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} \
            if MODELO in _COM_FALLBACK else {}
        resp = cliente.beta.messages.parse(
            model=MODELO, max_tokens=800, system=INSTRUCOES_POSICAO,
            messages=[{"role": "user", "content": pedido}],
            output_config={"effort": "low"}, output_format=AlertaPosicao, **extra)
        r = resp.parsed_output
        if r is None or not r.sair:
            return r
        citavel = riscos.head(N_RISCO).reset_index(drop=True)
        fontes = verificador.citacoes_validas(r.fontes, citavel, agora)
        motivo, _ = verificador.limpar_texto(r.motivo, citavel, pedido)
        if not fontes or not motivo:   # alerta de venda só com facto citado e verificável
            return AlertaPosicao(sair=False, motivo="", fontes=[])
        return AlertaPosicao(sair=True, motivo=motivo, fontes=fontes)
    except Exception as e:
        log.warning("Vigia da posição %s falhou: %s", pos.get("ticker"), e)
        return None
