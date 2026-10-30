"""Sinais da noite: comprar à abertura seguinte ainda compensa se a ação abrir a subir (ou a descer)?

O backtest entra ao fecho do dia do sinal. Um sinal formado com a bolsa fechada só se compra na
abertura seguinte, e há ações que "sobem logo à entrada". Para cada sinal do backtest mede o salto
na abertura do dia seguinte (abertura - fecho do sinal, em ATR) e o retorno de comprar nessa abertura:
  deslocado   stop e objetivo andam com a abertura (regra atual do registo: mesma distância em ATR)
  fixo        stop e objetivo ficam onde o sinal os pôs (o salto come parte do caminho);
              se abrir já acima do objetivo ou abaixo do stop, não entra
  fecho       referência: comprar ao fecho do dia do sinal (o backtest)
Por faixa de salto, com as duas metades do período, para escolher a partir de quanto o sinal da
noite deixa de compensar. Uso:  python passo3h_abertura.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

from src import indicadores, saidas, sinais
from passo3e_trocas import CUSTO, DADOS, RAIZ

FAIXAS = [-np.inf, -1.0, -0.5, -0.2, 0.2, 0.5, 1.0, np.inf]
NOMES = ["< -1 ATR", "-1..-0,5", "-0,5..-0,2", "~0", "0,2..0,5", "0,5..1", "> 1 ATR"]


def da_abertura(d: pd.DataFrame, i: int, r: saidas.Regra, fixo: bool) -> float | None:
    """Compra na abertura do dia i+1 e sai pela regra do setup. None se não entra ou não acabou."""
    if i + r.max_dias >= len(d):
        return None
    fecho, a, ab = d["close"].iat[i], d["atr14"].iat[i], d["open"].iat[i + 1]
    base = fecho if fixo else ab
    obj = base + r.mult_objetivo * a if r.mult_objetivo else None
    stop = base - r.mult_stop * a if r.mult_stop else None
    if fixo and ((obj is not None and ab >= obj) or (stop is not None and ab <= stop)):
        return None
    for j in range(i + 1, i + r.max_dias + 1):
        o = ab if j == i + 1 else d["open"].iat[j]
        if stop is not None and d["low"].iat[j] <= stop:
            return min(stop, o) / ab - 1
        if obj is not None and d["high"].iat[j] >= obj:
            return max(obj, o) / ab - 1
        if saidas.condicao_saida(r, d.iloc[j]):
            return d["close"].iat[j] / ab - 1
    return d["close"].iat[i + r.max_dias] / ab - 1


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
                if i + 1 >= len(d) or not sinais.passa_filtros(d.iloc[i], filtros):
                    continue
                r = saidas.simular(d, i, regra)
                if r is None:
                    continue
                a = d["atr14"].iat[i]
                out.append({"ticker": ticker, "setup": nome, "data": d["data"].iat[i],
                            "salto": (d["open"].iat[i + 1] - d["close"].iat[i]) / a if a > 0 else np.nan,
                            "fecho": r[0], "deslocado": da_abertura(d, i, regra, False),
                            "fixo": da_abertura(d, i, regra, True)})
    return pd.DataFrame(out)


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    t = recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg).dropna(subset=["salto", "deslocado"])
    t["faixa"] = pd.cut(t["salto"], FAIXAS, labels=NOMES)
    meio = t["data"].median()
    print(f"Sinais: {len(t)} (de {t['data'].min():%Y-%m-%d} a {t['data'].max():%Y-%m-%d}); "
          f"custo da XTB por trade {CUSTO*100:.1f}% (não descontado)")
    for s, g in [("todos", t)] + list(t.groupby("setup")):
        print(f"\n### {s}: retorno médio % por salto na abertura (1.ª metade | 2.ª metade), n")
        for f, h in g.groupby("faixa", observed=True):
            if len(h) < 50:
                continue
            m1, m2 = h[h["data"] < meio], h[h["data"] >= meio]
            cel = []
            for c in ("fecho", "deslocado", "fixo"):
                x, x1, x2 = h[c].dropna(), m1[c].dropna(), m2[c].dropna()
                cel.append(f"{c} {x.mean()*100:+.2f} ({x1.mean()*100:+.2f}|{x2.mean()*100:+.2f})")
            print(f"  {f:11s} n={len(h):6d} ({len(h)/len(g)*100:4.1f}%)  " + "  ".join(cel)
                  + f"  entra fixo {h['fixo'].notna().mean()*100:.0f}%")


if __name__ == "__main__":
    main()
