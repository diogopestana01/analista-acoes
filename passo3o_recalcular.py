"""Recalcular um sinal "do zero" todos os dias ajuda a saber quanto ainda vale?

Hoje a lista reavalia cada sinal com o "valor que falta" (src/trocas.py): uma tabela por setup, dias que
passaram e ganho desde o sinal em ATR. Este estudo testa se olhar para o estado de HOJE da ação e do
mercado, como se o sinal fosse calculado de novo, diz melhor quanto ainda falta render:
  redetetado   o setup voltava a dar-se hoje (mesmas regras e filtros do dia do sinal)
  rsi2, dist_sma5, ret_dia, vol_ratio   indicadores de hoje da ação
  mercado      subida média das ações do universo nos últimos 5 dias (o "clima" do mercado)
Modelo "do zero": regressão linear do retorno real até à saída, por setup, com o valor que falta e
estas medidas, treinada nos 60% iniciais. Avalia nos 40% finais, nas duas metades, contra o valor que
falta sozinho. Só para medir: nada disto entra no modelo sem aprovação.
Uso:  python passo3o_recalcular.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

import passo3e_trocas as t3
from src import indicadores, saidas, sinais

RAIZ, DADOS = t3.RAIZ, t3.DADOS
MEDIDAS = ["falta", "redetetado", "rsi2", "dist_sma5", "ret_dia", "vol_ratio", "ganho_atr", "mercado"]


def recolher(precos: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.Series]:
    """Uma linha por sinal e dia vivo k (0 = dia do sinal), com o estado de hoje e o retorno real até à
    saída se se entrasse ao fecho desse dia. Devolve também o clima do mercado por dia."""
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    linhas, clima = [], []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        clima.append(d[["data", "ret5"]])
        flags = sinais.detetar(d)
        for nome in setups:
            regra = saidas.regra_do_setup(nome, cfg)
            filtros = cfg.get("qualidade", {}).get(nome, {}).get("filtros")
            vivos = flags[nome].to_numpy()
            passa = {}
            for i in np.flatnonzero(vivos):
                if not sinais.passa_filtros(d.iloc[i], filtros):
                    continue
                r = saidas.simular(d, i, regra)
                if r is None:
                    continue
                ret, dias, _ = r
                entrada, atr = d["close"].iat[i], d["atr_pct"].iat[i]
                fim = entrada * (1 + ret)
                for k in range(0, min(dias, 5)):
                    j = i + k
                    if j not in passa:
                        passa[j] = bool(vivos[j]) and sinais.passa_filtros(d.iloc[j], filtros)
                    preco = d["close"].iat[j]
                    linhas.append({"data": d["data"].iat[j], "ticker": ticker, "setup": nome, "k": k,
                                   "sinal_data": d["data"].iat[i], "ganho": preco / entrada - 1, "atr_pct": atr,
                                   "real": fim / preco - 1, "redetetado": float(passa[j]),
                                   "rsi2": d["rsi2"].iat[j] / 100, "dist_sma5": preco / d["sma5"].iat[j] - 1,
                                   "ret_dia": d["ret_dia"].iat[j], "vol_ratio": min(d["vol_ratio"].iat[j], 5.0)})
    c = pd.concat(clima).groupby("data")["ret5"].mean()
    return pd.DataFrame(linhas), c


def juntar_falta(df: pd.DataFrame, corte) -> pd.DataFrame:
    """Valor que falta (tabela de src/trocas.py) aprendido só com os sinais de antes do corte."""
    treino = df[(df["sinal_data"] < corte) & (df["k"] >= 1)].copy()
    treino["balde"] = [t3.balde(g, a) for g, a in zip(treino["ganho"], treino["atr_pct"])]
    tab = treino.assign(kk=treino["k"].clip(upper=5)).groupby(["setup", "kk", "balde"])["real"] \
        .agg(["mean", "size"])
    tab = tab[tab["size"] >= 30]["mean"].to_dict()
    media = df[(df["sinal_data"] < corte) & (df["k"] == 0)].groupby("setup")["real"].mean().to_dict()
    df = df.copy()
    df["ganho_atr"] = (df["ganho"] / df["atr_pct"].clip(lower=1e-4)).clip(-4, 4)
    df["falta"] = [media.get(s) if k == 0 else tab.get((s, min(k, 5), t3.balde(g, a)), media.get(s))
                   for s, k, g, a in zip(df["setup"], df["k"], df["ganho"], df["atr_pct"])]
    return df


def ajustar(treino: pd.DataFrame) -> np.ndarray:
    x = np.column_stack([np.ones(len(treino))] + [treino[m].to_numpy(float) for m in MEDIDAS])
    coef, *_ = np.linalg.lstsq(x, treino["real"].to_numpy(float), rcond=None)
    return coef


def prever(df: pd.DataFrame, coef: np.ndarray) -> np.ndarray:
    x = np.column_stack([np.ones(len(df))] + [df[m].to_numpy(float) for m in MEDIDAS])
    return x @ coef


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    df, clima = recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    df["mercado"] = df["data"].map(clima).fillna(0.0)
    corte = df.drop_duplicates(["ticker", "setup", "sinal_data"])["sinal_data"].quantile(0.6)
    df = juntar_falta(df, corte).dropna(subset=MEDIDAS + ["real"])
    vivos = df[df["k"] >= 1]   # o dia do sinal já é o sinal; interessa o que muda depois
    treino, teste = vivos[vivos["sinal_data"] < corte], vivos[vivos["sinal_data"] >= corte].copy()
    print(f"Sinais x dias vivos (k>=1): treino {len(treino)}, teste {len(teste)} (desde {corte:%Y-%m-%d})")

    print("\n### O setup volta a dar-se depois do dia do sinal? (teste)")
    for s, g in teste.groupby("setup"):
        sim, nao = g[g["redetetado"] == 1], g[g["redetetado"] == 0]
        print(f"  {s}: {len(sim) / len(g) * 100:.0f}% dos dias; ainda rende {sim['real'].mean() * 100:+.2f}% "
              f"(n={len(sim)}) contra {nao['real'].mean() * 100:+.2f}% quando não (n={len(nao)})")

    print("\n### Coeficientes do modelo 'do zero' por setup (treino), em pontos % por unidade")
    for s, g in treino.groupby("setup"):
        coef = ajustar(g)
        teste.loc[teste["setup"] == s, "zero"] = prever(teste[teste["setup"] == s], coef)
        print(f"  {s}: " + " · ".join(f"{m} {c * 100:+.2f}" for m, c in zip(["const"] + MEDIDAS, coef)))
    teste = teste.dropna(subset=["zero"])

    meio = teste["data"].median()
    print("\n### Quanto acerta no que ainda falta render (correlação de ordem com o real, teste)")
    for chave in ("falta", "zero"):
        a = teste[teste["data"] < meio]
        b = teste[teste["data"] >= meio]
        print(f"  {chave:6s} 1.ª metade {a[chave].rank().corr(a['real'].rank()):+.3f} · "
              f"2.ª metade {b[chave].rank().corr(b['real'].rank()):+.3f}")

    print("\n### Quintis (teste): retorno real médio até à saída")
    for chave in ("falta", "zero"):
        q = pd.qcut(teste[chave].rank(method="first"), 5, labels=False)
        print(f"  {chave:6s} " + " | ".join(f"Q{k + 1} {g['real'].mean() * 100:+.2f}%" for k, g in teste.groupby(q)))

    print("\n### Para trocas: posições que a medida diz que já não dão (< 0), quanto deram de facto")
    for chave in ("falta", "zero"):
        for nome, g in (("1.ª", teste[teste["data"] < meio]), ("2.ª", teste[teste["data"] >= meio])):
            fraco = g[g[chave] < 0]
            print(f"  {chave:6s} {nome} metade: {len(fraco) / len(g) * 100:.0f}% marcadas, deram "
                  f"{fraco['real'].mean() * 100:+.2f}% contra {g[g[chave] >= 0]['real'].mean() * 100:+.2f}% das outras")

    print("\n### Mesmo dia: 3 primeiros de cada medida contra a média do dia (dias com 5+ vivos)")
    dias = teste.groupby("data").filter(lambda g: len(g) >= 5)
    for chave in ("falta", "zero"):
        x = dias.assign(rk=dias.groupby("data")[chave].rank(ascending=False, method="first"),
                        md=dias.groupby("data")["real"].transform("mean"))
        x = x[x["rk"] <= 3]
        dif = x["real"] - x["md"]
        print(f"  {chave:6s} {dif.mean() * 100:+.2f} pontos (1.ª metade {dif[x['data'] < meio].mean() * 100:+.2f}, "
              f"2.ª {dif[x['data'] >= meio].mean() * 100:+.2f})")


if __name__ == "__main__":
    main()
