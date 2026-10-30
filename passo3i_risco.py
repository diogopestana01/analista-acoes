"""Risco de cada sinal: que sinais perdem mais quando correm mal? (ganho e confiança são outra coisa)

Para cada sinal do backtest guarda o que se sabe no dia (ATR em %, preço, volume em dólares,
subida em 5 dias, RSI, distância à média de 50) e o que aconteceu:
  ret           retorno até à saída
  stop          saiu pelo stop
  perda_grande  perdeu mais de 5%
  pior10        média dos 10% piores trades do grupo (o "dia mau" típico)
  excesso       quanto a perda no stop passou do stop planeado (gap através do stop)
Por quintil de cada medida, nas duas metades do período. Uma medida só serve para dizer
"arriscado" se separar os piores trades nas duas metades.
Uso:  python passo3i_risco.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

from src import indicadores, saidas, sinais
from passo3e_trocas import DADOS, RAIZ

MEDIDAS = ["atr_pct", "preco", "dolar_vol", "ret5", "rsi14", "dist_sma50"]


def recolher(precos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    out = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        dv = (d["close"] * d["volume"]).rolling(20).mean()
        flags = sinais.detetar(d)
        for nome in setups:
            regra = saidas.regra_do_setup(nome, cfg)
            filtros = cfg.get("qualidade", {}).get(nome, {}).get("filtros")
            for i in np.flatnonzero(flags[nome].to_numpy()):
                if not sinais.passa_filtros(d.iloc[i], filtros):
                    continue
                r = saidas.simular(d, i, regra)
                if r is None:
                    continue
                ret, dias, motivo = r
                c, a = d["close"].iat[i], d["atr14"].iat[i]
                planeado = -regra.mult_stop * a / c if regra.mult_stop else np.nan
                out.append({"ticker": ticker, "setup": nome, "data": d["data"].iat[i], "ret": ret,
                            "stop": motivo == "stop", "excesso": (planeado - ret) if motivo == "stop" else np.nan,
                            "atr_pct": d["atr_pct"].iat[i], "preco": c, "dolar_vol": dv.iat[i],
                            "ret5": d["ret5"].iat[i], "rsi14": d["rsi14"].iat[i],
                            "dist_sma50": c / d["sma50"].iat[i] - 1})
    return pd.DataFrame(out)


def resumo(g: pd.DataFrame) -> str:
    pior = g["ret"].nsmallest(max(len(g) // 10, 1)).mean()
    return (f"ret {g['ret'].mean()*100:+.2f}% stop {g['stop'].mean()*100:3.0f}% "
            f">5% perda {(g['ret'] < -0.05).mean()*100:4.1f}% pior10 {pior*100:+.1f}% "
            f"excesso {g['excesso'].mean()*100:.2f}%")


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    t = recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg).dropna(subset=MEDIDAS)
    meio = t["data"].median()
    print(f"Sinais: {len(t)} ({t['data'].min():%Y-%m-%d} a {t['data'].max():%Y-%m-%d}); geral: {resumo(t)}")
    for s, g in [("todos", t)] + list(t.groupby("setup")):
        print(f"\n######## {s} (n={len(g)}): {resumo(g)}")
        for m in MEDIDAS:
            q = pd.qcut(g[m].rank(method="first"), 5, labels=False)
            print(f"  -- {m} (quintis; limites {', '.join(f'{x:.3g}' for x in g[m].quantile([.2, .4, .6, .8]))})")
            for k, h in g.groupby(q):
                a, b = h[h["data"] < meio], h[h["data"] >= meio]
                pa = a["ret"].nsmallest(max(len(a) // 10, 1)).mean()
                pb = b["ret"].nsmallest(max(len(b) // 10, 1)).mean()
                print(f"    Q{k + 1} {resumo(h)} | pior10 metades {pa*100:+.1f}%/{pb*100:+.1f}% "
                      f"ret metades {a['ret'].mean()*100:+.2f}/{b['ret'].mean()*100:+.2f}")


if __name__ == "__main__":
    main()
