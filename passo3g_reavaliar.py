"""Reavaliar a força de um sinal com o tempo: entrar hoje num sinal que já tem k dias ainda compensa?

Para cada sinal do backtest e cada dia k (0 = dia do sinal) em que continua vivo, simula entrar ao
fecho desse dia e sair pela regra original. O retorno desde esse dia é o que um sinal "da lista"
ainda dá. Compara medidas de força (todas sem olhar para o futuro, tabelas aprendidas nos 60% iniciais):
  falta           valor que falta da tabela (setup, dias, ganho em ATR) - src/trocas.py
  esperado        retorno esperado da ação no dia do sinal (não muda com o preço)
  esperado_x      esperado x (falta / média do setup): o esperado da ação, gasto à medida que o
                  preço anda e os dias passam
Avalia no período de teste: no mesmo dia, os 3 primeiros de cada medida contra a média desse dia.
Uso:  python passo3g_reavaliar.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
import yaml

import passo3e_trocas as t3

RAIZ, DADOS = t3.RAIZ, t3.DADOS


def esperados(trades: list[dict], media: dict, k: int = 10) -> list[float]:
    """Retorno esperado da ação no dia do sinal, só com trades da mesma ação já fechados."""
    passado, out = defaultdict(list), {}
    for j, t in sorted(enumerate(trades), key=lambda x: x[1]["entrada_data"]):
        h = [r for s, r in passado[(t["ticker"], t["setup"])] if s < t["entrada_data"]]
        out[j] = (np.sum(h) + media[t["setup"]] * k) / (len(h) + k)
        passado[(t["ticker"], t["setup"])].append((t["saida_data"], t["ret"]))
    return [out[j] for j in range(len(trades))]


def linhas(trades: list[dict], esp: list[float], valor: dict, media: dict) -> pd.DataFrame:
    out = []
    for t, e in zip(trades, esp):
        fim = t["entrada"] * (1 + t["ret"])
        for k, (dia, preco) in enumerate(sorted(t["caminho"].items())):
            if k > 4:
                break
            g = preco / t["entrada"] - 1
            b = t3.balde(g, t["atr_pct"])
            falta = media[t["setup"]] if k == 0 else valor.get((t["setup"], min(k, 5), b), media[t["setup"]])
            out.append({"data": dia, "ticker": t["ticker"], "setup": t["setup"], "k": k, "ganho": g, "balde": b,
                        "real": fim / preco - 1, "falta": falta, "esperado": e,
                        "esperado_x": e * falta / media[t["setup"]] if media[t["setup"]] > 0 else e})
    return pd.DataFrame(out)


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    trades = t3.recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    corte = pd.Series([t["entrada_data"] for t in trades]).quantile(0.6)
    valor, media, _ = t3.tabelas([t for t in trades if t["entrada_data"] < corte])
    d = linhas(trades, esperados(trades, media), valor, media)
    teste = d[d["data"] >= corte].drop_duplicates(["data", "ticker"], keep="last")
    print(f"Sinais vivos (ação x dia): {len(d)}; teste desde {corte:%Y-%m-%d}: {len(teste)}")

    print("\n### Retorno real de entrar no dia k (teste), por dias passados e ganho desde o sinal")
    nomes = ["< -1 ATR", "-1..-0,25", "~0", "0,25..1", "1..2", "> 2 ATR"]
    for s, g in teste.groupby("setup"):
        print(f"{s}:")
        for k, h in g.groupby("k"):
            cel = [f"{nomes[b]} {x['real'].mean()*100:+.2f}% (n={len(x)})" for b, x in h.groupby("balde") if len(x) >= 50]
            print(f"  dia {k}: " + " | ".join(cel))

    print("\n### Mesmo dia: os 3 primeiros de cada medida contra a média do dia (dias com 5+ sinais vivos)")
    dias = teste.groupby("data").filter(lambda g: len(g) >= 5)
    meio = dias["data"].median()
    for chave in ("falta", "esperado", "esperado_x"):
        x = dias.assign(rk=dias.groupby("data")[chave].rank(ascending=False, method="first"),
                        md=dias.groupby("data")["real"].transform("mean"))
        x = x[x["rk"] <= 3]
        dif = x["real"] - x["md"]
        print(f"  {chave:11s} {dif.mean()*100:+.2f} pontos (1.ª metade {dif[x['data'] < meio].mean()*100:+.2f}, "
              f"2.ª {dif[x['data'] >= meio].mean()*100:+.2f}) | {dias['data'].nunique()} dias")

    print("\n### Quintis de cada medida (teste): retorno real médio de entrar nesse dia")
    for chave in ("falta", "esperado", "esperado_x"):
        q = pd.qcut(teste[chave].rank(method="first"), 5, labels=False)
        print(f"  {chave:11s} " + " | ".join(f"Q{k + 1} {g['real'].mean()*100:+.2f}%" for k, g in teste.groupby(q)))


if __name__ == "__main__":
    main()
