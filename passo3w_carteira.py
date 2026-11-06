"""As regras novas do estudo avançado (passo3v) na carteira de 5 lugares, com 10 anos (1500 ações dos EUA).

Pedido do Pestana (6/11): regras mais ricas. Passaram nas duas metades por trade (passo3v_avancado.py):
  VIX > 30 e RSI2 < 5            (todos os mercados, incluindo o fraco)
  recuo na viragem do mês        (fraco e queda)
  caiu muito mais do que o setor (-15 ou -8 pts em 5 dias, em tendência; forte e queda)
  líder do ano a recuar          (20% que mais subiram em 12 meses, RSI2 < 10; forte)
Mais regras a disputar 5 lugares já estragaram a carteira antes (passo3u), por isso mede-se a carteira:
o modelo de hoje (atuais no forte, caiu_10 no forte, pullback e reversao_queda na queda) contra o modelo
de hoje mais cada regra nova. Ordem equilibrada (passo3j), médias da 1.ª metade. Só mede.
Uso: ANOS=10 ACOES=1500 python passo3w_carteira.py
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yaml

import passo3j_ordem as p3j
import passo3p_regime as p3p
import passo3u_biblioteca as p3u
import passo3v_avancado as p3v
from src import saidas

NOVAS = {  # nome -> (regra do passo3v, mercados onde vale)
    "n: VIX>30 e RSI2<5": ("4b RSI2 < 5 com o VIX > 30", ("forte", "fraco", "queda")),
    "n: viragem do mês": ("3 recuo na viragem do mês", ("fraco", "queda")),
    "n: contra o setor -15": ("1b contra o setor (-15 pts em 5d)", ("forte", "queda")),
    "n: contra o setor -8": ("1 contra o setor (-8 pts em 5d)", ("forte", "queda")),
    "n: líder a recuar": ("2b líder do ano a recuar, em tendência", ("forte",)),
}


def trades_novos(todos: pd.DataFrame, por_ticker: dict) -> pd.DataFrame:
    regras = p3v.regras_avancadas(todos)
    base_de = todos.reset_index().groupby("ticker")["index"].min().to_dict()
    linhas = []
    for nome, (regra, _) in NOVAS.items():
        flag = regras[regra].fillna(False)
        for t, i in todos.index[flag].to_series().groupby(todos["ticker"]):
            d = por_ticker[t]
            for k in i.to_numpy() - base_de[t]:
                r = saidas.simular(d, int(k), p3v.MR)
                if r is None:
                    continue
                linhas.append({"ticker": t, "setup": nome, "entrada_data": d["data"].iat[k],
                               "saida_data": d["data"].iat[k + r[1]], "ret": r[0] - p3u.SPREAD,
                               "atr_pct": d["atr_pct"].iat[k], "rsi2": d["rsi2"].iat[k], "ret5": d["ret5"].iat[k],
                               "vol_ratio": d["vol_ratio"].iat[k]})
    return pd.DataFrame(linhas)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load((p3v.RAIZ / "config.yaml").read_text())["sinais"]
    precos = p3v.carregar()
    todos, por_ticker = p3v.preparar(precos)
    todos, m = p3v.enriquecer(todos)
    novos = trades_novos(todos, por_ticker)
    del todos, por_ticker
    t, amp = p3u.recolher(precos, cfg)
    t = t[t["setup"].str.startswith("atual")]
    t = pd.concat([t, novos], ignore_index=True)
    t["a50"] = t["entrada_data"].map(amp)
    t["spy5"] = t["entrada_data"].map(m["spy_ret5"]) if not m.empty else np.nan
    t = t.dropna(subset=["a50"]).reset_index(drop=True)
    t["mercado"] = np.where(t["spy5"] < -0.03, "queda", np.where(t["a50"] >= 0.5, "forte", "fraco"))
    meio = t["entrada_data"].median()
    t["metade"] = np.where(t["entrada_data"] < meio, 1, 2)
    print(f"Trades: {len(t)}; metades a partir de {meio:%Y-%m-%d}; por regra: {t['setup'].value_counts().to_dict()}")

    print("\n### Por trade (líquido; 1.ª|2.ª metade)")
    for (s, mk), g in t.groupby(["setup", "mercado"]):
        a, b = g[g["metade"] == 1]["ret"], g[g["metade"] == 2]["ret"]
        print(f"  {s:28s} {mk:6s} n={len(g):6d} {g['ret'].mean()*100:+.2f}% ({a.mean()*100:+.2f}|{b.mean()*100:+.2f}) "
              f"ganha {(g['ret'] > 0).mean()*100:.0f}%")

    treino = t[t["metade"] == 1]
    media = treino.groupby("setup")["ret"].mean().to_dict()
    ganha = treino.groupby("setup")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    for s in t["setup"].unique():
        media.setdefault(s, 0.0)
        ganha.setdefault(s, 0.5)
    eq = p3j.medidas(t.to_dict("records"), media, ganha)
    eq.index = t.sort_values("entrada_data", kind="stable").index
    t["equilibrado"] = eq["equilibrado"].reindex(t.index)

    s, mk = t["setup"], t["mercado"]
    hoje = (s.isin(["atual: rompimento", "atual: pullback", "atual: semana_forte", "atual: caiu_10"]) & (mk == "forte")) \
        | (s.isin(["atual: pullback", "atual: reversao_queda"]) & (mk == "queda"))
    nova = {n: (s == n) & mk.isin(merc) for n, (_, merc) in NOVAS.items()}
    variantes = {"modelo de hoje": hoje}
    for n, f in nova.items():
        variantes[f"hoje + {n[3:]}"] = hoje | f
    variantes["hoje + VIX + viragem (dão no fraco)"] = hoje | nova["n: VIX>30 e RSI2<5"] | nova["n: viragem do mês"]
    variantes["hoje + todas (setor -15)"] = hoje | nova["n: VIX>30 e RSI2<5"] | nova["n: viragem do mês"] \
        | nova["n: contra o setor -15"] | nova["n: líder a recuar"]
    variantes["só as novas (setor -15)"] = nova["n: VIX>30 e RSI2<5"] | nova["n: viragem do mês"] \
        | nova["n: contra o setor -15"] | nova["n: líder a recuar"]
    print("\n### Carteira de 5 lugares (ordem equilibrada): 1.ª | 2.ª metade")
    for nome, ok in variantes.items():
        txt = []
        for pn in (1, 2):
            seg = t[t["metade"] == pn]
            r = p3p.carteira(seg.assign(peso=ok.loc[seg.index].astype(float), ret=seg["ret"] + p3p.CUSTO))
            txt.append(f"{pn}.ª {r['total']*100:+6.1f}% (desc {r['descida']*100:+5.1f}%, {r['n']} trades)")
        print(f"  {nome:40s} " + " | ".join(txt))


if __name__ == "__main__":
    main()
