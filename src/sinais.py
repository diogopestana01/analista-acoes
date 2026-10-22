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
    "gap_continuacao": Setup("gap_continuacao", "compra",
                             "Dia seguinte a um salto forte com volume (ex. resultados) que se aguentou"),
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
    # Ontem: abriu +4% ou mais, volume 3x, fechou perto do máximo. Hoje: segurou o fecho de ontem.
    ontem_forte = (d["gap"] >= 0.04) & (d["vol_ratio"] >= 3) & (d["pos_fecho"] >= 0.7) & (d["close"] > d["open"])
    s["gap_continuacao"] = ontem_forte.shift(1, fill_value=False) & (d["close"] >= d["close"].shift())
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
    pares = [(i, r) for i, r in zip(idx, res) if r[0] != "aberto"]
    if not pares:
        return {"n_historico": 0, "taxa_acerto": np.nan, "dias_mediana": np.nan, "retorno_medio_hist": np.nan}
    sinal = 1 if direcao == "compra" else -1
    retornos = []
    for i, (r, dias) in pares:
        entrada, a = d["close"].iat[i], d["atr14"].iat[i]
        saida = {"objetivo": entrada + sinal * cfg["mult_objetivo"] * a,
                 "stop": entrada - sinal * cfg["mult_stop"] * a,
                 "expirou": d["close"].iat[i + dias]}[r]
        retornos.append(sinal * (saida / entrada - 1))
    ganhos = [dias for _, (r, dias) in pares if r == "objetivo"]
    return {
        "n_historico": len(pares),
        "taxa_acerto": len(ganhos) / len(pares),
        "dias_mediana": float(np.median(ganhos)) if ganhos else np.nan,
        "retorno_medio_hist": float(np.mean(retornos)),
    }


def sinais_hoje(precos: pd.DataFrame, cfg: dict, setups_ativos: list[str] | None = None) -> pd.DataFrame:
    """Percorre todas as ações e devolve os sinais do último dia disponível."""
    setups_ativos = setups_ativos or list(SETUPS)
    linhas = []
    acima = {"eua": [], "europa": []}
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        flags = detetar(d)
        hoje = d.iloc[-1]
        if pd.notna(hoje["sma50"]):
            acima[regiao(ticker)].append(hoje["close"] > hoje["sma50"])
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
                "dist_sma50": hoje["close"] / hoje["sma50"] - 1,
                "choque_baixa_5d": hoje["choque_baixa_5d"],
                "regiao": regiao(ticker),
                **est,
            })
    out = pd.DataFrame(linhas)
    if not out.empty:
        amp = {r: (float(np.mean(v)) if v else np.nan) for r, v in acima.items()}
        out["amplitude_mercado"] = out["regiao"].map(amp)
    return out


def regiao(ticker: str) -> str:
    return "europa" if "." in ticker else "eua"


def passa_filtros(s: pd.Series, filtros: dict) -> bool:
    """filtros: {caracteristica: [min, max]} (None = sem limite)."""
    for c, (mn, mx) in (filtros or {}).items():
        v = s.get(c)
        if v is None or pd.isna(v):
            return False
        if (mn is not None and v < mn) or (mx is not None and v > mx):
            return False
    return True


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

    # Retorno esperado: média histórica desta ação neste setup, puxada para a média
    # do setup em todo o mercado (backtest) quando há poucos casos; menos custos XTB.
    qualidade = cfg.get("qualidade", {})
    base = s["setup"].map(lambda x: qualidade.get(x, {}).get("retorno_base_pct", 0.0) / 100)
    k = cfg.get("peso_base_casos", 10)
    hist = s.get("retorno_medio_hist", pd.Series(np.nan, index=s.index)).fillna(0.0)
    s["retorno_esperado"] = (hist * n + base * k) / (n + k)
    custo = cfg.get("custo_cambio_pct_por_lado", 0.5) / 100 * 2
    s["custo"] = np.where(s.get("regiao", pd.Series("eua", index=s.index)) == "eua", custo, 0.0)
    s["retorno_esperado_liquido"] = s["retorno_esperado"] - s["custo"]
    s["passa_filtros"] = [passa_filtros(r, qualidade.get(r["setup"], {}).get("filtros"))
                          for _, r in s.iterrows()]
    return s.sort_values(["passa_filtros", "retorno_esperado_liquido", "pont_tecnica"],
                         ascending=False).reset_index(drop=True)
