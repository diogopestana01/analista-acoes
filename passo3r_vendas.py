"""Vendas a descoberto (CFD) quando o mercado está fraco: ganham?

Pedido do Pestana (6/11): com as compras em pausa (src/regime.py), aproveitar as quedas com sinais de
venda em CFD na XTB. O rompimento_baixa já foi testado sozinho (-0,33%, desligado). Aqui testa-se cada
setup de venda em todo o período e SÓ com o mercado fraco:
  rompimento_baixa  fecho abaixo do mínimo de 20 dias, volume >= 1,5x, tendência de descida
  pullback_venda    tendência de descida e RSI2 > 90 (ressalto curto), fecho < média de 50 x 1,03
  semana_fraca      caiu 8% ou mais em 5 dias, fecho no fundo do dia, volume >= 1,5x, tendência de descida
Saídas (espelho das compras): pullback_venda sai quando fecha abaixo da média de 5 dias (stop 2 ATR,
5 dias); os outros objetivo 3 ATR abaixo, stop 1,5 ATR acima, 5 dias. Pior caso se tocar nos dois.
Custos: 0,3% de spread + financiamento do CFD de 0,02% por noite (cerca de 7%/ano, conservador).
Mercado fraco: amplitude dos EUA < 50% (a regra das compras), < 40%, e SPY abaixo da média de 50 dias.
Só EUA. Duas metades. Só mede.
Uso:  python passo3r_vendas.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import passo3p_regime as p3p
from src import indicadores

RAIZ, DADOS = p3p.RAIZ, p3p.DADOS
SPREAD, NOITE = 0.003, 0.0002


def detetar(d: pd.DataFrame) -> pd.DataFrame:
    desce = (d["close"] < d["sma50"]) & (d["sma50"] < d["sma200"])
    s = pd.DataFrame(index=d.index)
    s["rompimento_baixa"] = (d["close"] < d["min20"]) & (d["vol_ratio"] >= 1.5) & desce
    s["pullback_venda"] = (d["close"] < d["sma200"]) & (d["sma50"] < d["sma200"]) & (d["rsi2"] > 90) \
        & (d["close"] < d["sma50"] * 1.03)
    s["semana_fraca"] = (d["ret5"] <= -0.08) & (d["pos_fecho"] <= 0.2) & (d["vol_ratio"] >= 1.5) & desce
    return s.fillna(False)


def simular_venda(d: pd.DataFrame, i: int, setup: str) -> tuple[float, int] | None:
    """Venda aberta no fecho do dia i. Retorno do lado vendedor (positivo = ganhou) e noites."""
    e, a = d["close"].iat[i], d["atr14"].iat[i]
    regra_sma5 = setup == "pullback_venda"
    stop = e + (2.0 if regra_sma5 else 1.5) * a
    obj = None if regra_sma5 else e - 3.0 * a
    for j in range(i + 1, i + 6):
        if j >= len(d):
            return None
        if d["high"].iat[j] >= stop:
            return 1 - max(stop, d["open"].iat[j]) / e, j - i
        if obj is not None and d["low"].iat[j] <= obj:
            return 1 - min(obj, d["open"].iat[j]) / e, j - i
        if regra_sma5 and d["close"].iat[j] < d["sma5"].iat[j]:
            return 1 - d["close"].iat[j] / e, j - i
    return 1 - d["close"].iat[i + 5] / e, 5


def main() -> None:
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    eua = filtrado["ticker"][~filtrado["ticker"].str.contains(".", regex=False)]
    precos = precos[precos["ticker"].isin(eua)].copy()
    precos["data"] = p3p._sem_fuso(precos["data"])
    linhas, acima = [], []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        acima.append(pd.DataFrame({"data": d["data"], "a50": (d["close"] > d["sma50"]).where(d["sma50"].notna())}))
        flags = detetar(d)
        for nome in flags.columns:
            for i in np.flatnonzero(flags[nome].to_numpy()):
                r = simular_venda(d, i, nome)
                if r is not None:
                    linhas.append((ticker, nome, d["data"].iat[i], r[0] - SPREAD - NOITE * r[1], r[1]))
    t = pd.DataFrame(linhas, columns=["ticker", "setup", "data", "liq", "noites"])
    amp = pd.concat(acima).dropna().groupby("data")["a50"].mean()
    t["a50"] = t["data"].map(amp)
    m = p3p.indices(t["data"].min())
    t["spy_abaixo50"] = t["data"].map(~m["spy_sma50"]) if not m.empty else np.nan
    meio = t["data"].median()
    print(f"Vendas EUA: {len(t)} de {t['data'].min():%Y-%m-%d} a {t['data'].max():%Y-%m-%d}; metades a partir de "
          f"{meio:%Y-%m-%d}; líquido de 0,3% de spread e 0,02%/noite")
    condicoes = {"sempre": pd.Series(True, index=t.index), "amplitude < 50%": t["a50"] < 0.5,
                 "amplitude < 40%": t["a50"] < 0.4, "SPY < média 50d": t["spy_abaixo50"] == True,  # noqa: E712
                 "amplitude < 50% e SPY < 50d": (t["a50"] < 0.5) & (t["spy_abaixo50"] == True)}  # noqa: E712
    for s, g in [("todos", t)] + list(t.groupby("setup")):
        print(f"\n### {s}")
        for nome, ok in condicoes.items():
            x = g[ok.loc[g.index].fillna(False).astype(bool)]
            if x.empty:
                continue
            a, b = x[x["data"] < meio], x[x["data"] >= meio]
            print(f"  {nome:30s} n={len(x):6d} ({x['data'].nunique()} dias, {len(x) / max(x['data'].nunique(), 1):.1f}/dia) "
                  f"{x['liq'].mean()*100:+.2f}% ({a['liq'].mean()*100:+.2f}|{b['liq'].mean()*100:+.2f}) "
                  f"ganha {(x['liq'] > 0).mean()*100:.0f}% | pior 10% {np.sort(x['liq'])[:max(len(x)//10,1)].mean()*100:+.1f}%")


if __name__ == "__main__":
    main()
