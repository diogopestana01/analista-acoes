"""Europa: os setups rendem nas ações europeias como nas dos EUA, depois dos custos da XTB?

Pedido do Pestana (30/10): juntar as ações da UE disponíveis na XTB. O universo já tem os índices
europeus (config.yaml universo.europa); a carteira do bot só usa os EUA (conta USD).
Custos da XTB (ações reais): 0% de comissão até 100 mil EUR por mês; 0,5% de conversão em cada lado
quando a moeda da ação não é a da conta. Por isso, com uma subconta em EUR:
  euro   ações em EUR (Alemanha, França, Países Baixos, Espanha, Itália, Portugal, Bélgica...): sem câmbio
  gbp/sek/chf  Londres, Estocolmo, Suíça: +1% ida e volta de câmbio (Londres: +0,5% de imposto do selo)
  eua    na subconta USD: sem câmbio
Mede retorno por zona e setup (antes e depois do custo da zona, mais 0,3% de spread), nas duas metades.
Só vale ligar uma zona se for positiva depois de custos nas duas metades.
Uso:  python passo3m_europa.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import pandas as pd
import yaml

import passo3j_ordem as p3j
from passo3e_trocas import DADOS, RAIZ

SPREAD = 0.003
EURO = {"DE", "PA", "AS", "MC", "MI", "LS", "BR", "HE", "VI", "IR"}
CUSTO_ZONA = {"eua": 0.0, "euro": 0.0, "gbp": 0.015, "sek": 0.01, "chf": 0.01, "outra": 0.01}


def zona(ticker: str) -> str:
    if "." not in ticker:
        return "eua"
    suf = ticker.rsplit(".", 1)[1].upper()
    return "euro" if suf in EURO else {"L": "gbp", "ST": "sek", "SW": "chf"}.get(suf, "outra")


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    t = pd.DataFrame(p3j.recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg))
    t["zona"] = t["ticker"].map(zona)
    t["liq"] = t["ret"] - SPREAD - t["zona"].map(CUSTO_ZONA)
    meio = t["entrada_data"].median()
    print(f"Trades: {len(t)}; metades a partir de {meio:%Y-%m-%d}")
    print("Ações no universo filtrado por zona:", filtrado["ticker"].map(zona).value_counts().to_dict())
    for s, g in [("todos", t)] + list(t.groupby("setup")):
        print(f"\n### {s}")
        for z, x in g.groupby("zona"):
            a, b = x[x["entrada_data"] < meio], x[x["entrada_data"] >= meio]
            print(f"  {z:6s} n={len(x):6d} ({x['ticker'].nunique()} ações, {len(x) / max(x['entrada_data'].nunique(), 1):.1f}/dia) "
                  f"bruto {x['ret'].mean()*100:+.2f}% ({a['ret'].mean()*100:+.2f}|{b['ret'].mean()*100:+.2f}) "
                  f"líquido {x['liq'].mean()*100:+.2f}% ({a['liq'].mean()*100:+.2f}|{b['liq'].mean()*100:+.2f}) "
                  f"ganha {(x['liq'] > 0).mean()*100:.0f}%")


if __name__ == "__main__":
    main()
