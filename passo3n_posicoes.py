"""Quantas posições ter na carteira? O mesmo modelo com 1, 2, 3, 5, 8 e 10 lugares no histórico.

Pedido do Pestana (30/10): ao escolher o número de posições, mostrar o que isso mudou no passado.
Usa as mesmas entradas e saídas do backtest e a ordem da lista (equilibrada, passo3j_ordem.py), nos
últimos 40% do período (as tabelas do esperado e da taxa vêm dos primeiros 60%). Cada lugar vale
1/N do capital; custo de 0,3% por trade. Sem trocas (o estudo das trocas mostrou ganho pequeno).
Mede, por N: resultado total, pior descida da carteira, trades, % que perde, e as duas metades.
Grava data/estudo_posicoes.json, que o bot mostra em ⚙️ Regras (src/bot.py).
Uso:  python passo3n_posicoes.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import yaml

import passo3j_ordem as p3j

RAIZ, DADOS = p3j.RAIZ, p3j.DADOS
LUGARES = (1, 2, 3, 5, 8, 10)
SAIDA = DADOS / "estudo_posicoes.json"


def resumo(teste: pd.DataFrame, lugares: int, chave: str = "equilibrado") -> dict:
    meio = teste["entrada_data"].median()
    r = p3j.carteira(teste, chave, lugares)
    a = p3j.carteira(teste[teste["entrada_data"] < meio], chave, lugares)
    b = p3j.carteira(teste[teste["entrada_data"] >= meio], chave, lugares)
    return {"total": round(r["total"], 4), "descida": round(r["descida"], 4), "trades": int(r["n"]),
            "media": round(r["media"], 4), "perde": round(r["perde"], 3),
            "total_1": round(a["total"], 4), "total_2": round(b["total"], 4)}


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    trades = p3j.recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    corte = pd.Series([t["entrada_data"] for t in trades]).quantile(0.6)
    treino = pd.DataFrame([t for t in trades if t["entrada_data"] < corte])
    media = treino.groupby("setup")["ret"].mean().to_dict()
    ganha = treino.groupby("setup")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    m = p3j.medidas(trades, media, ganha)
    teste = m[m["entrada_data"] >= corte]
    ini, fim = teste["entrada_data"].min(), teste["entrada_data"].max()
    meses = max(round((fim - ini).days / 30.4), 1)
    print(f"Teste: {ini:%Y-%m-%d} a {fim:%Y-%m-%d} ({meses} meses), {len(teste)} sinais")
    out = {"gerado_em": pd.Timestamp.now(tz="UTC").isoformat(), "desde": f"{ini:%Y-%m-%d}", "ate": f"{fim:%Y-%m-%d}",
           "meses": meses, "custo": p3j.t3.CUSTO, "por_n": {}}
    rng = np.random.default_rng(1)
    for n in LUGARES:
        r = resumo(teste, n)
        sorte = sorted(p3j.carteira(teste, "aleatorio", n, rng)["total"] for _ in range(20))
        r["acaso_meio"] = round(sorte[10], 4)
        out["por_n"][str(n)] = r
        print(f"  {n:2d} lugares: total {r['total']*100:+6.1f}% ({r['total_1']*100:+.1f}|{r['total_2']*100:+.1f}) "
              f"pior descida {r['descida']*100:+5.1f}% | {r['trades']} trades {r['media']*100:+.2f}% "
              f"perde {r['perde']*100:.0f}% | ao acaso ~{r['acaso_meio']*100:+.1f}%")
    SAIDA.write_text(json.dumps(out, indent=1))
    print(f"Gravado {SAIDA}")


if __name__ == "__main__":
    main()
