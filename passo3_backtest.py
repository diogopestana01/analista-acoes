"""Passo 3: testa os setups nos últimos 2 anos de todo o universo filtrado.

Uso:  python passo3_backtest.py   (depois de passo1_universo.py)
Gera data/backtest_trades.csv, data/backtest_resumo.csv e imprime a análise
por característica (volume, RSI, força, volatilidade, mercado) para perceber
que condições dão sinais fortes.
Só a parte técnica é testada; as notícias antigas não estão disponíveis de graça.
Os retornos "liquidos" descontam o custo cambial da XTB nas ações fora do euro.
"""
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src import indicadores, sinais

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"

CARACTERISTICAS = ["vol_ratio", "rsi14", "ret20", "atr_pct", "dist_sma50", "amplitude_mercado"]


def regiao(ticker: str) -> str:
    return "europa" if "." in ticker else "eua"


def backtest(precos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    trades, acima = [], []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        acima.append(pd.DataFrame({"data": d["data"], "regiao": regiao(ticker),
                                   "acima": (d["close"] > d["sma50"]).where(d["sma50"].notna())}))
        flags = sinais.detetar(d)
        for nome in cfg["setups"]:
            direcao = sinais.SETUPS[nome].direcao
            for i in np.flatnonzero(flags[nome].to_numpy()):
                res, dias = sinais.resultado_trade(d, i, direcao, cfg["mult_objetivo"], cfg["mult_stop"],
                                                   cfg["max_dias"])
                if res == "aberto":
                    continue
                entrada, a = d["close"].iat[i], d["atr14"].iat[i]
                sinal = 1 if direcao == "compra" else -1
                saida = {"objetivo": entrada + sinal * cfg["mult_objetivo"] * a,
                         "stop": entrada - sinal * cfg["mult_stop"] * a,
                         "expirou": d["close"].iat[i + dias]}[res]
                trades.append({"ticker": ticker, "data": d["data"].iat[i], "setup": nome, "resultado": res,
                               "dias": dias, "retorno": sinal * (saida / entrada - 1),
                               "regiao": regiao(ticker),
                               "vol_ratio": d["vol_ratio"].iat[i], "rsi14": d["rsi14"].iat[i],
                               "ret20": d["ret20"].iat[i], "atr_pct": d["atr_pct"].iat[i],
                               "dist_sma50": d["close"].iat[i] / d["sma50"].iat[i] - 1})
    t = pd.DataFrame(trades)
    if t.empty:
        return t
    # Amplitude do mercado: % de ações da mesma região acima da média de 50 dias nesse dia.
    amp = pd.concat(acima).dropna().groupby(["data", "regiao"])["acima"].mean().rename("amplitude_mercado")
    t = t.merge(amp.reset_index(), on=["data", "regiao"], how="left")
    custo = cfg.get("custo_cambio_pct_por_lado", 0.5) / 100 * 2
    t["retorno_liquido"] = t["retorno"] - np.where(t["regiao"] == "eua", custo, 0.0)
    return t.replace([np.inf, -np.inf], np.nan)


def resumir(trades: pd.DataFrame) -> pd.DataFrame:
    g = trades.groupby("setup")
    return pd.DataFrame({
        "trades": g.size(),
        "taxa_objetivo": g["resultado"].apply(lambda r: (r == "objetivo").mean()),
        "taxa_stop": g["resultado"].apply(lambda r: (r == "stop").mean()),
        "retorno_medio_pct": g["retorno"].mean() * 100,
        "retorno_liquido_pct": g["retorno_liquido"].mean() * 100,
        "dias_medios": g["dias"].mean(),
        "pior_trade_pct": g["retorno"].min() * 100,
    }).round(3)


def por_caracteristica(trades: pd.DataFrame) -> str:
    """Retorno médio (bruto, %) por quintil de cada característica, por setup."""
    linhas = []
    for setup, t in trades.groupby("setup"):
        linhas.append(f"\n=== {setup} ({len(t)} trades) | por região: "
                      + ", ".join(f"{r} {g['retorno'].mean()*100:+.2f}% (n={len(g)})"
                                  for r, g in t.groupby("regiao")))
        for c in CARACTERISTICAS:
            x = t[[c, "retorno", "resultado"]].dropna()
            if x[c].nunique() < 5:
                continue
            x["q"] = pd.qcut(x[c], 5, duplicates="drop")
            r = x.groupby("q", observed=True).agg(n=("retorno", "size"), ret=("retorno", "mean"),
                                                  obj=("resultado", lambda s: (s == "objetivo").mean()))
            linhas.append(f"{c}: " + " | ".join(
                f"{iv.left:.3g}..{iv.right:.3g}: {row.ret*100:+.2f}% obj {row.obj*100:.0f}%"
                for iv, row in r.iterrows()))
    return "\n".join(linhas)


def melhores_subconjuntos(trades: pd.DataFrame, n_min: int = 150, top: int = 12) -> str:
    """Procura combinações de 2 condições (quintis) com o melhor retorno líquido."""
    linhas = []
    for setup, t in trades.groupby("setup"):
        t = t.copy()
        conds = {}
        for c in CARACTERISTICAS:
            x = t[c]
            if x.nunique() < 5:
                continue
            q = pd.qcut(x, 5, labels=False, duplicates="drop")
            for k in sorted(q.dropna().unique()):
                lo, hi = x[q == k].min(), x[q == k].max()
                conds[f"{c} {lo:.3g}..{hi:.3g}"] = q == k
        conds.update({f"regiao {r}": t["regiao"] == r for r in t["regiao"].unique()})
        nomes = list(conds)
        corte = t["data"].sort_values().iloc[len(t) // 2]  # 1.ª metade escolhe, 2.ª metade confirma
        treino = t["data"] < corte
        res = []
        for a in range(len(nomes)):
            for b in range(a, len(nomes)):
                m = conds[nomes[a]] & conds[nomes[b]]
                if (m & treino).sum() >= n_min and (m & ~treino).sum() >= n_min // 2:
                    tr, te = t[m & treino], t[m & ~treino]
                    res.append((tr["retorno_liquido"].mean(), te["retorno_liquido"].mean(), len(tr), len(te),
                                (te["resultado"] == "objetivo").mean(),
                                nomes[a] if a == b else f"{nomes[a]} & {nomes[b]}"))
        res.sort(reverse=True)
        linhas.append(f"\n### {setup}: melhores combinações no 1.º ano e como se portaram no 2.º "
                      "(líquido treino | líquido teste | n treino | n teste | objetivo teste)")
        linhas += [f"{a*100:+.2f}% | {b*100:+.2f}% | {n1} | {n2} | {obj*100:.0f}% | {nome}"
                   for a, b, n1, n2, obj, nome in res[:top]]
    return "\n".join(linhas)


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    cfg = {**cfg, "setups": list(sinais.SETUPS)}  # testa todos, mesmo os desligados
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    trades = backtest(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    trades.to_csv(DADOS / "backtest_trades.csv", index=False)
    resumo = resumir(trades)
    resumo.to_csv(DADOS / "backtest_resumo.csv")
    print(resumo.to_string())
    print(por_caracteristica(trades))
    print(melhores_subconjuntos(trades))


if __name__ == "__main__":
    main()
