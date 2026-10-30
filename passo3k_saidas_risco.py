"""Saídas para perder menos: prazo mais curto e stop que sobe para a entrada (sem perda).

Pedido do Pestana: não chegar ao fim com perda. Para cada setup, com as mesmas entradas do backtest,
compara a regra atual com:
  prazo_N      a mesma regra mas no máximo N dias (os sinais perdem força depois do 3.º dia)
  sem_perda_K  quando o preço chega a entrada + K x ATR, o stop sobe para a entrada (break-even)
Mede retorno médio, % que perde, perda média dos 10% piores, retorno por dia de capital preso,
nas duas metades do período. Só vale mudar se for melhor nas duas.
Uso:  python passo3k_saidas_risco.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

from src import indicadores, saidas, sinais
from passo3e_trocas import DADOS, RAIZ

VARIANTES = {"atual": {}, "prazo_3": {"max_dias": 3}, "prazo_4": {"max_dias": 4}, "prazo_7": {"max_dias": 7},
             "sem_perda_1": {"sem_perda": 1.0}, "sem_perda_15": {"sem_perda": 1.5},
             "sem_perda_1_prazo_4": {"sem_perda": 1.0, "max_dias": 4}}


def simular(d: pd.DataFrame, i: int, r: saidas.Regra, max_dias: int | None = None,
            sem_perda: float | None = None) -> tuple[float, int] | None:
    """Como saidas.simular, com prazo à escolha e stop que sobe para a entrada depois de +K ATR
    (só a partir do dia seguinte a tocar no gatilho: no próprio dia não se sabe a ordem)."""
    n = max_dias or r.max_dias
    if i + n >= len(d):
        return None
    e, a = d["close"].iat[i], d["atr14"].iat[i]
    obj = e + r.mult_objetivo * a if r.mult_objetivo else None
    stop = e - r.mult_stop * a if r.mult_stop else None
    subir = False
    for j in range(i + 1, i + n + 1):
        if subir:
            stop = max(stop, e) if stop is not None else e
        o = d["open"].iat[j]
        if stop is not None and d["low"].iat[j] <= stop:
            return min(stop, o) / e - 1, j - i
        if obj is not None and d["high"].iat[j] >= obj:
            return max(obj, o) / e - 1, j - i
        if saidas.condicao_saida(r, d.iloc[j]):
            return d["close"].iat[j] / e - 1, j - i
        if sem_perda is not None and d["high"].iat[j] >= e + sem_perda * a:
            subir = True
    return d["close"].iat[i + n] / e - 1, n


def recolher(precos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    out = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        flags = sinais.detetar(d)
        for nome in setups:
            regra = saidas.regra_do_setup(nome, cfg)
            filtros = cfg.get("qualidade", {}).get(nome, {}).get("filtros")
            for i in np.flatnonzero(flags[nome].to_numpy()):
                if not sinais.passa_filtros(d.iloc[i], filtros):
                    continue
                linha = {"ticker": ticker, "setup": nome, "data": d["data"].iat[i]}
                ok = True
                for v, p in VARIANTES.items():
                    r = simular(d, i, regra, **p)
                    if r is None:
                        ok = False
                        break
                    linha[v], linha[v + "_dias"] = r
                if ok:
                    out.append(linha)
    return pd.DataFrame(out)


def pior10(r: pd.Series) -> float:
    return r.nsmallest(max(len(r) // 10, 1)).mean()


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    t = recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    meio = t["data"].median()
    print(f"Sinais: {len(t)}; metades a partir de {meio:%Y-%m-%d}. Antes de custos (0,3% por trade).")
    for s, g in [("todos", t)] + list(t.groupby("setup")):
        print(f"\n### {s} (n={len(g)})")
        a, b = g[g["data"] < meio], g[g["data"] >= meio]
        for v in VARIANTES:
            print(f"  {v:20s} ret {g[v].mean()*100:+.2f}% ({a[v].mean()*100:+.2f}|{b[v].mean()*100:+.2f}) "
                  f"perde {(g[v] < 0).mean()*100:.0f}% ({(a[v] < 0).mean()*100:.0f}|{(b[v] < 0).mean()*100:.0f}) "
                  f"pior10 {pior10(g[v])*100:+.1f}% ({pior10(a[v])*100:+.1f}|{pior10(b[v])*100:+.1f}) "
                  f"dias {g[v + '_dias'].mean():.1f} | por dia {g[v].mean() / g[v + '_dias'].mean()*100:+.3f}%")


if __name__ == "__main__":
    main()
