"""Regras de saída dos trades (só compras), para comparar no backtest.

Cada regra recebe os indicadores de uma ação e o dia de entrada (compra no fecho)
e devolve (retorno, dias, motivo). Stops e objetivos são testados com o máximo e o
mínimo do dia; se ambos forem tocados no mesmo dia, assume-se o pior caso (stop).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Regra:
    nome: str
    descricao: str
    mult_objetivo: float | None   # objetivo = entrada + k x ATR (None = sem objetivo fixo)
    mult_stop: float | None       # stop = entrada - k x ATR (None = sem stop)
    max_dias: int
    sair_se: str | None = None    # condição ao fecho: "acima_sma5" | "rsi2_70" | None


REGRAS = {
    "atr_2_1": Regra("atr_2_1", "Objetivo 2xATR, stop 1xATR, 5 dias (a atual)", 2.0, 1.0, 5),
    "atr_1_1": Regra("atr_1_1", "Objetivo 1xATR, stop 1xATR, 5 dias", 1.0, 1.0, 5),
    "atr_3_15": Regra("atr_3_15", "Objetivo 3xATR, stop 1,5xATR, 5 dias", 3.0, 1.5, 5),
    "fecho_5d": Regra("fecho_5d", "Sem objetivo nem stop, vende ao fim de 5 dias", None, None, 5),
    "sma5": Regra("sma5", "Vende quando fecha acima da média de 5 dias; stop 2xATR; 5 dias",
                  None, 2.0, 5, "acima_sma5"),
    "rsi2": Regra("rsi2", "Vende quando o RSI de 2 dias passa 70; stop 2xATR; 5 dias",
                  None, 2.0, 5, "rsi2_70"),
}


def simular(d: pd.DataFrame, i: int, r: Regra) -> tuple[float, int, str] | None:
    """Trade de compra aberto no fecho do dia i. None se ainda não terminou."""
    entrada, a = d["close"].iat[i], d["atr14"].iat[i]
    obj = entrada + r.mult_objetivo * a if r.mult_objetivo else None
    stop = entrada - r.mult_stop * a if r.mult_stop else None
    for j in range(i + 1, i + r.max_dias + 1):
        if j >= len(d):
            return None
        if stop is not None and d["low"].iat[j] <= stop:
            return min(stop, d["open"].iat[j]) / entrada - 1, j - i, "stop"   # abre abaixo = sai na abertura
        if obj is not None and d["high"].iat[j] >= obj:
            return max(obj, d["open"].iat[j]) / entrada - 1, j - i, "objetivo"
        c = d["close"].iat[j]
        if (r.sair_se == "acima_sma5" and c > d["sma5"].iat[j]) or \
                (r.sair_se == "rsi2_70" and d["rsi2"].iat[j] > 70):
            return c / entrada - 1, j - i, "regra"
    return d["close"].iat[i + r.max_dias] / entrada - 1, r.max_dias, "prazo"
