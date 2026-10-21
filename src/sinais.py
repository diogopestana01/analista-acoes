"""Regras de sinais de curto prazo e estimativa de objetivo, stop e prazo.

Cada setup é uma regra simples e conhecida. Para cada sinal de hoje calcula-se,
com o histórico da própria ação, quantas vezes o mesmo setup atingiu o objetivo
antes do stop e em quantos dias (mediana). É daí que vem o "tempo esperado".
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicadores


@dataclass(frozen=True)
class Setup:
    nome: str
    direcao: str          # "compra" ou "venda" (venda = short via CFD)
    descricao: str


SETUPS = {
    "rompimento": Setup("rompimento", "compra",
                        "Fecho acima do máximo de 20 dias com volume forte, em tendência de subida"),
    "pullback": Setup("pullback", "compra",
                      "Queda curta (RSI2 baixo) dentro de uma tendência de subida"),
    "gap_volume": Setup("gap_volume", "compra",
                        "Abertura com salto forte e volume muito acima do normal (ex. resultados), a fechar em alta"),
    "rompimento_baixa": Setup("rompimento_baixa", "venda",
                              "Fecho abaixo do mínimo de 20 dias com volume forte, em tendência de descida"),
}


def detetar(d: pd.DataFrame) -> pd.DataFrame:
    """Colunas booleanas, uma por setup, para cada dia."""
    s = pd.DataFrame(index=d.index)
    s["rompimento"] = (d["close"] > d["max20"]) & (d["vol_ratio"] >= 1.5) & (d["close"] > d["sma50"]) \
        & (d["sma50"] > d["sma200"])
    s["pullback"] = (d["close"] > d["sma200"]) & (d["sma50"] > d["sma200"]) & (d["rsi2"] < 10) \
        & (d["close"] > d["sma50"] * 0.97)
    s["gap_volume"] = (d["gap"] >= 0.03) & (d["vol_ratio"] >= 2.5) & (d["close"] > d["open"]) \
        & (d["close"] > d["sma200"])
    s["rompimento_baixa"] = (d["close"] < d["min20"]) & (d["vol_ratio"] >= 1.5) \
        & (d["close"] < d["sma50"]) & (d["sma50"] < d["sma200"])
    return s.fillna(False)


def resultado_trade(d: pd.DataFrame, i: int, direcao: str, mult_obj: float, mult_stop: float,
                    max_dias: int) -> tuple[str, int]:
    """Simula um trade aberto no fecho do dia i. Devolve (resultado, dias)."""
    entrada = d["close"].iat[i]
    a = d["atr14"].iat[i]
    sinal = 1 if direcao == "compra" else -1
    obj = entrada + sinal * mult_obj * a
    stop = entrada - sinal * mult_stop * a
    fim = min(i + max_dias, len(d) - 1)
    for j in range(i + 1, fim + 1):
        hi, lo = d["high"].iat[j], d["low"].iat[j]
        bateu_stop = lo <= stop if sinal == 1 else hi >= stop
        bateu_obj = hi >= obj if sinal == 1 else lo <= obj
        if bateu_stop:  # se ambos no mesmo dia, assume o pior caso
            return "stop", j - i
        if bateu_obj:
            return "objetivo", j - i
    return ("expirou" if fim == i + max_dias else "aberto"), fim - i


def estatisticas_setup(d: pd.DataFrame, flags: pd.Series, direcao: str, cfg: dict) -> dict:
    """Taxa de acerto e prazo mediano do setup no histórico desta ação."""
    idx = np.flatnonzero(flags.to_numpy()[:-1])  # exclui hoje
    res = [resultado_trade(d, i, direcao, cfg["mult_objetivo"], cfg["mult_stop"], cfg["max_dias"])
           for i in idx]
    res = [r for r in res if r[0] != "aberto"]
    if not res:
        return {"n_historico": 0, "taxa_acerto": np.nan, "dias_mediana": np.nan}
    ganhos = [dias for r, dias in res if r == "objetivo"]
    return {
        "n_historico": len(res),
        "taxa_acerto": len(ganhos) / len(res),
        "dias_mediana": float(np.median(ganhos)) if ganhos else np.nan,
    }


def sinais_hoje(precos: pd.DataFrame, cfg: dict, setups_ativos: list[str] | None = None) -> pd.DataFrame:
    """Percorre todas as ações e devolve os sinais do último dia disponível."""
    setups_ativos = setups_ativos or list(SETUPS)
    linhas = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        flags = detetar(d)
        hoje = d.iloc[-1]
        for nome in setups_ativos:
            if not flags[nome].iat[-1]:
                continue
            st = SETUPS[nome]
            sinal = 1 if st.direcao == "compra" else -1
            est = estatisticas_setup(d, flags[nome], st.direcao, cfg)
            linhas.append({
                "ticker": ticker,
                "data": hoje["data"],
                "setup": nome,
                "direcao": st.direcao,
                "entrada": hoje["close"],
                "objetivo": hoje["close"] + sinal * cfg["mult_objetivo"] * hoje["atr14"],
                "stop": hoje["close"] - sinal * cfg["mult_stop"] * hoje["atr14"],
                "atr_pct": hoje["atr_pct"],
                "rsi14": hoje["rsi14"],
                "vol_ratio": hoje["vol_ratio"],
                "ret20": hoje["ret20"],
                **est,
            })
    return pd.DataFrame(linhas)


def pontuar(sinais: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Pontuação técnica 0-100: histórico do setup, volume e força relativa."""
    if sinais.empty:
        return sinais
    s = sinais.copy()
    # Encolhe a taxa de acerto para 50% quando há poucos casos históricos.
    n = s["n_historico"].fillna(0)
    taxa = s["taxa_acerto"].fillna(0.5)
    s["taxa_ajustada"] = (taxa * n + 0.5 * 5) / (n + 5)
    forca = np.where(s["direcao"] == "compra", s["ret20"], -s["ret20"])
    s["pont_tecnica"] = (
        60 * s["taxa_ajustada"]
        + 20 * np.clip((s["vol_ratio"].fillna(1) - 1) / 2, 0, 1)
        + 20 * np.clip(pd.Series(forca, index=s.index).fillna(0) / 0.2 + 0.5, 0, 1)
    ).round(1)
    # Prazo estimado: mediana histórica, ou 5 dias se não houver histórico.
    s["dias_estimados"] = s["dias_mediana"].fillna(cfg["max_dias"]).clip(1, cfg["max_dias"])
    return s.sort_values("pont_tecnica", ascending=False).reset_index(drop=True)
