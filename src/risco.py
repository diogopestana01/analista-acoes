"""Risco de um sinal: quanto pode perder num dia mau. É diferente do ganho esperado e da confiança.

Estudo passo3i_risco.py (47 812 sinais, 2 anos, nas duas metades): o que separa os sinais que perdem
muito quando correm mal é a volatilidade da ação (ATR em % do preço). Os 10% piores trades de cada
grupo perdem cerca de 2,2 x ATR, em todos os setups:
  ATR ~1,6%  -> dia mau -3,5%       ATR ~2,8% -> -6,2%       ATR ~6% -> -13,5%
Preço, volume em dólares e RSI quase não mudam a perda num dia mau. O stop "salta" pouco (0,3% em média
além do planeado). As ações mais voláteis também rendem mais em média: risco e ganho andam juntos.
"""
from __future__ import annotations

FATOR_DIA_MAU = 2.2   # perda média dos 10% piores trades ≈ 2,2 x ATR
NIVEIS = ((0.05, "baixo"), (0.09, "médio"))   # perda num dia mau abaixo de 5% / 9%; acima = alto


def perda_dia_mau(atr_pct: float | None) -> float | None:
    """Perda típica de um trade destes num dia mau (1 em cada 10), em fração (0,06 = 6%)."""
    try:
        a = float(atr_pct)
    except (TypeError, ValueError):
        return None
    return FATOR_DIA_MAU * a if a > 0 else None


def nivel(atr_pct: float | None) -> str | None:
    p = perda_dia_mau(atr_pct)
    if p is None:
        return None
    for teto, nome in NIVEIS:
        if p < teto:
            return nome
    return "alto"


def texto(atr_pct: float | None) -> str:
    """'🟡 Risco médio: num dia mau (1 em 10) perde ~6%'."""
    n, p = nivel(atr_pct), perda_dia_mau(atr_pct)
    if n is None:
        return ""
    cor = {"baixo": "🟢", "médio": "🟡", "alto": "🔴"}[n]
    return f"{cor} Risco {n}: num dia mau (1 em 10) perde ~{p * 100:.0f}%"
