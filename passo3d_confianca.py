"""Treina e valida a confiança calibrada (src/confianca.py) com o backtest dos setups ativos.

1. Junta os trades dos setups ligados em config.yaml, com a regra de saída e os filtros de cada um.
2. Treina nos primeiros 60% do período e testa nos últimos 40%: se as notas altas não ganharem
   mais vezes do que as baixas no teste, a confiança não serve e não se guarda.
3. Se passar, treina com tudo e imprime o modelo entre as linhas MODELO_INICIO / MODELO_FIM
   (é esse JSON que vai para data/modelo_confianca.json). Com --guardar escreve-o logo.
Uso:  python passo3d_confianca.py [--guardar]   (depois de passo1_universo.py)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src import confianca, indicadores, saidas, sinais

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"


def trades(precos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    linhas, acima = [], []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        regiao = sinais.regiao(ticker)
        acima.append(pd.DataFrame({"data": d["data"], "regiao": regiao,
                                   "acima": (d["close"] > d["sma50"]).where(d["sma50"].notna())}))
        flags = sinais.detetar(d)
        for nome in setups:
            regra = saidas.regra_do_setup(nome, cfg)
            filtros = cfg.get("qualidade", {}).get(nome, {}).get("filtros")
            for i in np.flatnonzero(flags[nome].to_numpy()):
                linha = d.iloc[i]
                if not sinais.passa_filtros(linha, filtros):
                    continue
                r = saidas.simular(d, i, regra)
                if r is None:
                    continue
                linhas.append({"ticker": ticker, "data": linha["data"], "setup": nome, "regiao": regiao,
                               "retorno": r[0], "atr_pct": linha["atr_pct"], "vol_ratio": linha["vol_ratio"],
                               "rsi14": linha["rsi14"], "rsi_semanal": linha["rsi_semanal"], "ret5": linha["ret5"],
                               "ret20": linha["ret20"], "dist_sma50": linha["close"] / linha["sma50"] - 1})
    t = pd.DataFrame(linhas)
    amp = pd.concat(acima).dropna().groupby(["data", "regiao"])["acima"].mean().rename("amplitude_mercado")
    t = t.merge(amp.reset_index(), on=["data", "regiao"], how="left")
    # Um sinal por ação e dia, como ao vivo.
    return t.sort_values("data").drop_duplicates(["ticker", "data"]).reset_index(drop=True)


def auc(y: np.ndarray, p: np.ndarray) -> float:
    ordem = pd.Series(p).rank().to_numpy()
    n1, n0 = y.sum(), len(y) - y.sum()
    return float((ordem[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def relatorio(t: pd.DataFrame, p: np.ndarray, titulo: str) -> list[str]:
    y = (t["retorno"] > 0).to_numpy().astype(int)
    q = pd.qcut(p, 5, labels=False, duplicates="drop")
    out = [f"\n### {titulo}: AUC {auc(y, p):.3f} (0,5 = ao acaso) | n={len(t)}",
           "  quintil | confiança média | % ganhou de facto | retorno médio"]
    for k in sorted(np.unique(q)):
        m = q == k
        out.append(f"  {k + 1}       | {p[m].mean()*100:5.1f}           | {y[m].mean()*100:5.1f}%            | "
                   f"{t['retorno'].to_numpy()[m].mean()*100:+.2f}%")
    return out


def main(guardar: bool = False) -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    t = trades(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    setups = sorted(t["setup"].unique())
    print(f"Trades: {len(t)} | " + ", ".join(f"{s} {n}" for s, n in t["setup"].value_counts().items()))
    corte = t["data"].quantile(0.6)
    treino, teste = t[t["data"] < corte], t[t["data"] >= corte]
    y_tr = (treino["retorno"] > 0).astype(int)
    print(f"Treino até {corte:%Y-%m-%d} ({len(treino)}), teste depois ({len(teste)})")

    base = treino.groupby("setup")["retorno"].apply(lambda r: (r > 0).mean())
    out = relatorio(teste, teste["setup"].map(base).fillna(y_tr.mean()).to_numpy(), "Só o setup (referência)")
    m = confianca.treinar(treino, y_tr, setups)
    p_teste = confianca.prever(teste, m)
    out += relatorio(teste, p_teste, "Modelo, no período de teste")
    pesos = dict(zip(confianca.CONTINUAS + [f"setup={s}" for s in setups] + ["eua"], m["pesos"]))
    out.append("  pesos: " + ", ".join(f"{k} {v:+.3f}" for k, v in pesos.items()))
    print("\n".join(out))

    y_te = (teste["retorno"] > 0).to_numpy().astype(int)
    q = pd.qcut(p_teste, 5, labels=False, duplicates="drop")
    melhora = y_te[q == q.max()].mean() - y_te[q == 0].mean()
    if auc(y_te, p_teste) < 0.53 or melhora < 0.05:
        print(f"\nNÃO GUARDAR: no teste a confiança quase não separa ganhadores de perdedores "
              f"(diferença entre o 5.º e o 1.º quintil: {melhora*100:+.1f} pontos).")
        return
    final = confianca.treinar(t, (t["retorno"] > 0).astype(int), setups)
    final["treinado_em"] = f"{pd.Timestamp.now():%Y-%m-%d}"
    final["trades"] = int(len(t))
    texto = json.dumps(final, ensure_ascii=False)
    print(f"\nPASSA: diferença entre o 5.º e o 1.º quintil no teste {melhora*100:+.1f} pontos.")
    print("MODELO_INICIO\n" + texto + "\nMODELO_FIM")
    if guardar:
        confianca.CAMINHO.write_text(texto)


if __name__ == "__main__":
    main("--guardar" in sys.argv)
