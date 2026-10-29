"""Verificador: confere por código o que o Claude afirma, antes de chegar ao Telegram ou ao registo.

O Claude (analista de notícias e advogado do diabo) só lê manchetes e dá opinião. Aqui não há
Claude nenhum, só regras fixas, para que nada inventado passe:
  1. Manchetes numeradas: cada leitura que não seja neutra tem de citar números de manchetes
     que existem, e pelo menos uma tem de ser recente (até `DIAS_MAX` dias). Sem isso, passa a neutra.
  2. Números: cada número com 2 ou mais algarismos no texto do Claude tem de aparecer nas fontes
     (manchetes, resumos, dados do pedido). A frase com um número que não aparece é cortada.
  3. Datas: o código diz ao Claude que dia é hoje e quantos dias úteis faltam para cada evento;
     o Claude não faz contas de datas (ver `contexto_datas`).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

DIAS_MAX = 21
SEM_FACTOS = "Sem notícias verificáveis."
_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def hoje_utc(agora: pd.Timestamp | None = None) -> pd.Timestamp:
    return (pd.Timestamp.now(tz="UTC") if agora is None else pd.Timestamp(agora)).tz_convert("UTC")


def contexto_datas(agora: pd.Timestamp | None = None, resultados_em: str | None = None,
                   prazo_dias: int = 5) -> str:
    """Linha com a data de hoje e, se houver, os dias úteis até aos resultados (contados pelo código)."""
    h = hoje_utc(agora)
    dias_pt = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    txt = f"Hoje é {h:%Y-%m-%d} ({dias_pt[h.weekday()]}-feira)".replace("sábado-feira", "sábado") \
        .replace("domingo-feira", "domingo") + f". O trade dura até {prazo_dias} dias úteis."
    if isinstance(resultados_em, str) and resultados_em:
        n = int(np.busday_count(h.date(), pd.Timestamp(resultados_em).date()))
        dentro = "DENTRO" if n <= prazo_dias else "fora"
        txt += (f" Resultados da empresa a {resultados_em}: faltam {n} dias úteis, {dentro} do prazo do trade"
                " (conta já feita, não a refaças).")
    return txt


def idade_dias(data, agora: pd.Timestamp | None = None) -> int | None:
    d = pd.to_datetime(data, utc=True, errors="coerce")
    if pd.isna(d):
        return None
    return max(int((hoje_utc(agora) - d).total_seconds() // 86400), 0)


def linhas_numeradas(manchetes: pd.DataFrame | None, n: int, inicio: int = 1, com_resumo: bool = False,
                     agora: pd.Timestamp | None = None) -> str:
    """'[3] (há 2 dias) Reuters: título' — o número é o que o Claude tem de citar."""
    if manchetes is None or manchetes.empty:
        return "(nada encontrado)"
    out = []
    for k, r in enumerate(manchetes.head(n).itertuples(), start=inicio):
        idade = idade_dias(getattr(r, "data", None), agora)
        quando = f"há {idade} dias" if idade is not None else "sem data"
        resumo = f". {r.resumo}" if com_resumo and isinstance(getattr(r, "resumo", None), str) and r.resumo else ""
        out.append(f"[{k}] ({quando}) {getattr(r, 'fonte', '')}: {r.titulo}{resumo}")
    return "\n".join(out)


def tabela_citavel(*listas: tuple[pd.DataFrame | None, int]) -> pd.DataFrame:
    """Junta as listas na ordem em que foram numeradas no pedido: linha k-1 = manchete [k]."""
    partes = [m.head(n) for m, n in listas if m is not None and not m.empty]
    if not partes:
        return pd.DataFrame(columns=["titulo", "resumo", "data"])
    return pd.concat(partes, ignore_index=True)


def citacoes_validas(fontes: list[int] | None, citavel: pd.DataFrame, agora: pd.Timestamp | None = None) -> list[int]:
    """Números citados que existem; vazio se nenhuma das citadas for recente (ou sem data)."""
    ok = sorted({int(f) for f in (fontes or []) if 1 <= int(f) <= len(citavel)})
    if not ok:
        return []
    idades = [idade_dias(citavel["data"].iat[f - 1], agora) if "data" in citavel else None for f in ok]
    if all(i is not None and i > DIAS_MAX for i in idades):
        return []
    return ok


def _canon(t: str) -> str:
    """'430,9' e '430.9' -> '4309'; '31,0' -> '31' (zeros decimais à direita não contam)."""
    t = re.sub(r"([.,]\d*?)0+$", r"\1", t).rstrip(".,")
    return re.sub(r"[.,]", "", t).lstrip("0") or "0"


def _numeros(texto: str) -> set[str]:
    return {_canon(t) for t in _NUM.findall(texto or "")}


def _conhecidos(texto: str) -> set[str]:
    """Números das fontes, também só com a parte inteira (31.4% nas fontes deixa dizer 31%)."""
    out = set()
    for t in _NUM.findall(texto or ""):
        out.add(_canon(t))
        out.add(_canon(re.split(r"[.,]", t)[0]))
    return out


def numeros_nas_fontes(texto: str, fontes_texto: str) -> list[str]:
    """Frases de `texto` cujos números (2+ algarismos) aparecem todos em `fontes_texto`."""
    conhecidos = _conhecidos(fontes_texto)
    frases = re.split(r"(?<=[.;!?])\s+", (texto or "").strip())
    return [f for f in frases if f and all(len(n) < 2 or n in conhecidos for n in _numeros(f))]


def texto_das_fontes(citavel: pd.DataFrame, extra: str = "") -> str:
    partes = [extra]
    for c in ("titulo", "resumo"):
        if c in citavel:
            partes += citavel[c].fillna("").astype(str).tolist()
    return " ".join(partes)


def limpar_texto(texto: str, citavel: pd.DataFrame, extra: str = "") -> tuple[str, bool]:
    """(texto só com frases verificáveis, True se nada foi cortado)."""
    boas = numeros_nas_fontes(texto, texto_das_fontes(citavel, extra))
    total = [f for f in re.split(r"(?<=[.;!?])\s+", (texto or "").strip()) if f]
    return " ".join(boas), len(boas) == len(total)
