"""Ideias das skills de day trading (github.com/jakeschincariol/day-trader-skill) testadas no nosso modelo.

Pedido do Pestana (6/11): aproveitar partes dessas skills. Só as que se aplicam a 1 dia - 1 semana com
velas diárias e dados grátis:
  1. Setups do trader-scanner (regras dele em fechos diários), com e sem a nossa tendência
     (fecho > média de 200 dias e média de 50 > média de 200):
       breakout_20d     fecho acima de todos os fechos dos 20 dias anteriores
       bull_flag        +10% nos 10 dias antes dos últimos 5, e os últimos 5 fechos numa faixa de 5%
       reclaim_ma20     ontem fechou abaixo da média de 20 dias, hoje fechou acima
       oversold_bounce  RSI14 ontem abaixo de 30 e hoje fechou a subir
     Cada um com as saídas sma5 e atr_3_15; líquido de 0,3% de spread; duas metades. Só entra se ganhar
     nas DUAS metades e não for igual a um setup que já temos.
  2. Travão de perdas do trader-risk (limite de perda diária/semanal): numa carteira de 5 lugares (ordem
     equilibrada, como o bot), parar de abrir posições durante N dias depois de as vendas dos últimos 7 dias
     somarem uma perda maior do que X% da carteira. Com e sem o filtro de amplitude (passo3p_regime.py).
Só ações dos EUA. Só mede: nada entra no modelo sem aprovação.
Uso:  python passo3q_skills.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

import passo3j_ordem as p3j
import passo3p_regime as p3p
from src import indicadores, saidas

RAIZ, DADOS, CUSTO = p3j.RAIZ, p3j.DADOS, p3j.t3.CUSTO
NOVOS = ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold_bounce"]


def detetar_novos(d: pd.DataFrame) -> pd.DataFrame:
    c = d["close"]
    s = pd.DataFrame(index=d.index)
    s["breakout_20d"] = c > c.shift(1).rolling(20).max()
    s["bull_flag"] = (c.shift(5) / c.shift(15) - 1 > 0.10) & \
        (c.rolling(5).max() / c.rolling(5).min() - 1 <= 0.05)
    s["reclaim_ma20"] = (c.shift(1) < d["sma20"].shift(1)) & (c > d["sma20"])
    s["oversold_bounce"] = (d["rsi14"].shift(1) < 30) & (c > c.shift(1))
    tendencia = (c > d["sma200"]) & (d["sma50"] > d["sma200"])
    for n in NOVOS:
        s[n + "+tendência"] = s[n] & tendencia
    return s.fillna(False)


def setups_novos(precos: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        flags = detetar_novos(d)
        for nome in flags.columns:
            for i in np.flatnonzero(flags[nome].to_numpy()):
                for sd in ("sma5", "atr_3_15"):
                    r = saidas.simular(d, i, saidas.REGRAS[sd])
                    if r is not None:
                        linhas.append((nome, sd, d["data"].iat[i], r[0] - CUSTO))
    return pd.DataFrame(linhas, columns=["setup", "saida", "data", "liq"])


def carteira_travao(c: pd.DataFrame, perda: float | None, pausa: int, lugares: int = 5) -> dict:
    """Carteira de p3p.carteira com travão: vendas dos últimos 7 dias com perda > `perda` da carteira
    -> nada de entradas novas durante `pausa` dias (de calendário). As posições abertas seguem a regra."""
    abertas, trades, fechos, parado_ate, travou = {}, [], [], pd.Timestamp.min, 0
    for d, g in c.sort_values(["entrada_data", "equilibrado"], ascending=[True, False]).groupby("entrada_data"):
        for tk in [x for x, s in abertas.items() if s <= d]:
            abertas.pop(tk)
        if perda is not None and d >= parado_ate:
            recente = sum(r for s, r in fechos if d - pd.Timedelta(days=7) < s <= d)
            if recente < -perda:
                parado_ate, travou = d + pd.Timedelta(days=pausa), travou + 1
        if d < parado_ate:
            continue
        for r in g.drop_duplicates("ticker").itertuples():
            if len(abertas) >= lugares:
                break
            if r.ticker not in abertas and r.peso > 0:
                abertas[r.ticker] = r.saida_data
                ret = (r.ret - CUSTO) * r.peso / lugares
                trades.append((r.saida_data, ret))
                fechos.append((r.saida_data, ret))
    if not trades:
        return {"total": 0.0, "descida": 0.0, "n": 0, "travou": travou}
    x = pd.DataFrame(trades, columns=["saida", "ret"]).sort_values("saida")
    curva = 1 + x["ret"].cumsum()
    return {"total": curva.iat[-1] - 1, "descida": (curva / curva.cummax() - 1).min(), "n": len(x), "travou": travou}


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    eua = filtrado["ticker"][~filtrado["ticker"].str.contains(".", regex=False)]
    precos = precos[precos["ticker"].isin(eua)].copy()
    precos["data"] = p3p._sem_fuso(precos["data"])

    # 1. Setups do scanner
    n = setups_novos(precos)
    meio = n["data"].median()
    print(f"### 1. Setups do trader-scanner (EUA, líquido de 0,3%; metades a partir de {meio:%Y-%m-%d})")
    for (s, sd), g in n.groupby(["setup", "saida"]):
        a, b = g[g["data"] < meio], g[g["data"] >= meio]
        print(f"  {s:28s} {sd:8s} n={len(g):6d} ({len(g) / max(g['data'].nunique(), 1):5.1f}/dia) "
              f"{g['liq'].mean()*100:+.2f}% ({a['liq'].mean()*100:+.2f}|{b['liq'].mean()*100:+.2f}) "
              f"ganha {(g['liq'] > 0).mean()*100:.0f}%")

    # 2. Travão de perdas na carteira de 5 lugares
    trades, amp = p3p.recolher(precos, cfg)
    t = pd.DataFrame(trades).join(amp, on="entrada_data")
    corte = t["entrada_data"].quantile(0.6)
    treino = t[t["entrada_data"] < corte]
    media = treino.groupby("setup")["ret"].mean().to_dict()
    ganha = treino.groupby("setup")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    eq = p3j.medidas(t.to_dict("records"), media, ganha)
    eq.index = t.sort_values("entrada_data", kind="stable").index
    t["equilibrado"] = eq["equilibrado"].reindex(t.index)
    meio = t["entrada_data"].median()
    partes = (("tudo", t), ("1.ª", t[t["entrada_data"] < meio]), ("2.ª", t[t["entrada_data"] >= meio]))
    print("\n### 2. Travão de perdas (carteira de 5 lugares, ordem equilibrada, 0,3% de custo)")
    for filtro, peso in (("sem filtro", pd.Series(1.0, index=t.index)),
                         ("amplitude>=50%", (t["a50"] >= 0.5).astype(float))):
        for perda, pausa in [(None, 0), (0.02, 3), (0.02, 5), (0.03, 3), (0.03, 5), (0.05, 5), (0.05, 10), (0.08, 10)]:
            nome = "sem travão" if perda is None else f"perda>{perda*100:.0f}% pára {pausa}d"
            rs = [carteira_travao(p.assign(peso=peso.loc[p.index]), perda, pausa) for _, p in partes]
            print(f"  {filtro:15s} {nome:20s} " + " | ".join(
                f"{pn} {r['total']*100:+6.1f}% (desc {r['descida']*100:+5.1f}%, travou {r['travou']}x)"
                for (pn, _), r in zip(partes, rs)))


if __name__ == "__main__":
    main()
