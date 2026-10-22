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

    def texto_alerta(self) -> str:
        """Como sair, em linguagem simples, para a mensagem do Telegram."""
        regra = {"acima_sma5": "vende quando fechar acima da média de 5 dias",
                 "rsi2_70": "vende quando o RSI de 2 dias passar 70"}.get(self.sair_se)
        partes = [regra] if regra else []
        if not self.mult_objetivo and not regra:
            partes.append(f"vende ao fim de {self.max_dias} dias")
        else:
            partes.append(f"no máximo {self.max_dias} dias")
        return "; ".join(partes)


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
        if condicao_saida(r, d.iloc[j]):
            return d["close"].iat[j] / entrada - 1, j - i, "regra"
    return d["close"].iat[i + r.max_dias] / entrada - 1, r.max_dias, "prazo"


def condicao_saida(r: Regra, linha: pd.Series) -> bool:
    """A regra de saída ao fecho dispara nesta linha de indicadores?"""
    if r.sair_se == "acima_sma5":
        return bool(linha["close"] > linha["sma5"])
    if r.sair_se == "rsi2_70":
        return bool(linha["rsi2"] > 70)
    return False


def regra_do_setup(setup: str, cfg: dict) -> Regra:
    nome = cfg.get("qualidade", {}).get(setup, {}).get("saida", "atr_2_1")
    return REGRAS[nome]
