"""Stop mais curto ou mais largo, e lugares misturados (longas e curtas), com ganhos reinvestidos, 10 anos.

Pedido do Pestana (6/11): stop o mais baixo possível sem ser tocado à toa; numa carteira de 5 querer umas
posições longas e outras curtas, sem perder frequência de sinais.
  Stops: tendências (10 dias) e ressaltos (sma5, 5 dias) com stop a 1 / 1,5 / 2 / 3 ATR.
  Lugares: sem limite por tipo (hoje), no máximo 3 tendências, no máximo 3 ressaltos, 2+3 fixos.
Por trade: média líquida, % a ganhar, % que sai no stop, perda média no stop. Carteira de 5, reinvestida:
%/mês por metade, maior descida, trades por mês, dias médios.
Uso: ANOS=10 ACOES=1500 python passo3zb_stops.py
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yaml

import passo3v_avancado as p3v
import passo3y_gestao as p3y
from passo3z_composto import TENDENCIA, resumo

STOPS = (1.0, 1.5, 2.0, 3.0)


def curva(c: pd.DataFrame, lugares: int = 5, max_tend: int | None = None, max_ress: int | None = None) -> tuple[pd.Series, int]:
    c = c.assign(rk=c.groupby("entrada_data")["esperado"].rank(pct=True) + c.groupby("entrada_data")["taxa"].rank(pct=True))
    por_dia = dict(tuple(c.sort_values(["entrada_data", "rk"], ascending=[True, False]).groupby("entrada_data")))
    datas = sorted(set(c["entrada_data"]) | set(c["saida_data"]))
    abertas, capital, livre, pontos, n = {}, 1.0, 1.0, {}, 0
    for dt in datas:
        for tk in [x for x, v in abertas.items() if v[0] <= dt]:
            _, aloc, ret, _ = abertas.pop(tk)
            capital += aloc * ret
            livre += aloc * (1 + ret)
        g = por_dia.get(dt)
        if g is not None:
            for r in g.drop_duplicates("ticker").itertuples():
                if len(abertas) >= lugares:
                    break
                if r.ticker in abertas:
                    continue
                tend = r.setup in TENDENCIA
                nt = sum(1 for v in abertas.values() if v[3])
                if tend and max_tend is not None and nt >= max_tend:
                    continue
                if not tend and max_ress is not None and len(abertas) - nt >= max_ress:
                    continue
                aloc = min(capital / lugares, max(livre, 0.0))
                if aloc <= 0:
                    break
                livre -= aloc
                abertas[r.ticker] = (r.saida_data, aloc, r.ret, tend)
                n += 1
        pontos[dt] = capital
    return pd.Series(pontos).sort_index(), n


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sd = {}
    for k in STOPS:
        sd[f"tend_s{k}"] = (None, k, 10, None, None)
        sd[f"ress_s{k}"] = (None, k, 5, "sma5", None)
    p3y.SAIDAS = sd
    cfg = yaml.safe_load((p3v.RAIZ / "config.yaml").read_text())["sinais"]
    t = p3y.recolher(p3v.carregar(), cfg)
    t["tend"] = t["setup"].isin(TENDENCIA)
    t = t[(t["tend"] & t["saida"].str.startswith("tend")) | (~t["tend"] & t["saida"].str.startswith("ress"))].copy()
    t["k"] = t["saida"].str.split("_s").str[1].astype(float)
    meio = t["entrada_data"].median()
    t["metade"] = np.where(t["entrada_data"] < meio, 1, 2)
    t["chave"] = t["setup"] + "|" + t["saida"]
    treino = t[t["metade"] == 1]
    media = treino.groupby("chave")["ret"].mean().to_dict()
    ganha = treino.groupby("chave")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    t = p3y.esperado(t, media, ganha)
    t["no_stop"] = t["ret"] <= -0.95 * t["k"] * t["atr_pct"]
    print(f"Trades: {len(t)}; metades a partir de {meio:%Y-%m-%d}")
    print("\n### Por trade (líquido; 1.ª|2.ª metade)")
    for (tipo, k), g in t.groupby([np.where(t["tend"], "tendência 10d", "ressalto sma5"), "k"]):
        a, b = g[g["metade"] == 1]["ret"], g[g["metade"] == 2]["ret"]
        st = g[g["no_stop"]]
        print(f"  {tipo:14s} stop {k:.1f} ATR: {g['ret'].mean()*100:+.2f}% ({a.mean()*100:+.2f}|{b.mean()*100:+.2f}) "
              f"ganha {(g['ret'] > 0).mean()*100:.0f}% | sai no stop {g['no_stop'].mean()*100:.0f}% "
              f"(perda média {st['ret'].mean()*100:+.1f}%) | {g['dias'].mean():.1f} dias")

    def escolher(kt: float, kr: float) -> pd.DataFrame:
        return t[(t["tend"] & (t["k"] == kt)) | (~t["tend"] & (t["k"] == kr))]

    print("\n### Carteira de 5, reinvestida: stops (tendência / ressalto)")
    for kt, kr in ((2.0, 2.0), (1.0, 1.0), (1.5, 1.5), (3.0, 3.0), (1.5, 2.0), (2.0, 1.5), (3.0, 2.0), (2.0, 1.0)):
        x = escolher(kt, kr)
        partes = []
        for pn in (1, 2):
            v, n = curva(x[x["metade"] == pn])
            partes.append(f"{resumo(v)} | {n / max(len(v.resample('ME').last()), 1):.1f} trades/mês")
        print(f"  {kt}/{kr}\n    1.ª {partes[0]}\n    2.ª {partes[1]}")
    print("\n### Carteira de 5, reinvestida, stops de hoje: lugares por tipo")
    x = escolher(2.0, 2.0)
    for nome, kw in (("sem limite (hoje)", {}), ("máx. 3 tendências", {"max_tend": 3}),
                     ("máx. 3 ressaltos", {"max_ress": 3}), ("2 tendências + 3 ressaltos", {"max_tend": 2, "max_ress": 3}),
                     ("3 tendências + 2 ressaltos", {"max_tend": 3, "max_ress": 2})):
        partes = []
        for pn in (1, 2):
            v, n = curva(x[x["metade"] == pn], **kw)
            partes.append(f"{resumo(v)} | {n / max(len(v.resample('ME').last()), 1):.1f} trades/mês")
        print(f"  {nome}\n    1.ª {partes[0]}\n    2.ª {partes[1]}")


if __name__ == "__main__":
    main()
