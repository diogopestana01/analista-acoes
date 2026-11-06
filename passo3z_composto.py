"""O modelo de agora com os ganhos reinvestidos, mês a mês, 10 anos (1500 ações dos EUA).

Pedido do Pestana (6/11): a carteira é sempre a mesma e os ganhos voltam a ser investidos; quer pelo menos 5%
ao mês. Usa as trades do estudo da gestão (passo3y_gestao.py) com as saídas que estão no modelo (tendências
fecho_10, ressaltos sma5_5), ordem equilibrada, custo já descontado. Cada posição = capital de agora / lugares.
Mede, por metade e por ano: subida total, média por mês (composta), meses a ganhar, pior mês, maior descida.
Uso: ANOS=10 ACOES=1500 python passo3z_composto.py
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yaml

import passo3v_avancado as p3v
import passo3y_gestao as p3y

TENDENCIA = {"rompimento", "semana_forte", "pullback", "lider_recuo"}


def curva(c: pd.DataFrame, lugares: int, composto: bool = True) -> pd.Series:
    """Capital ao fim de cada dia (posições contadas quando fecham), começa em 1."""
    c = c.assign(rk=c.groupby("entrada_data")["esperado"].rank(pct=True) + c.groupby("entrada_data")["taxa"].rank(pct=True))
    abertas, capital, livre, pontos = {}, 1.0, 1.0, {}
    datas = sorted(set(c["entrada_data"]) | set(c["saida_data"]))
    por_dia = dict(tuple(c.sort_values(["entrada_data", "rk"], ascending=[True, False]).groupby("entrada_data")))
    for dt in datas:
        for tk in [x for x, v in abertas.items() if v[0] <= dt]:
            _, aloc, ret = abertas.pop(tk)
            capital += aloc * ret
            livre += aloc * (1 + ret)
        g = por_dia.get(dt)
        if g is not None:
            for r in g.drop_duplicates("ticker").itertuples():
                if len(abertas) >= lugares:
                    break
                if r.ticker in abertas:
                    continue
                aloc = min((capital if composto else 1.0) / lugares, max(livre, 0.0))
                if aloc <= 0:
                    break
                livre -= aloc
                abertas[r.ticker] = (r.saida_data, aloc, r.ret)
        pontos[dt] = capital
    return pd.Series(pontos).sort_index()


def resumo(v: pd.Series) -> str:
    v = pd.concat([pd.Series([1.0], index=[v.index[0] - pd.Timedelta(days=1)]), v])
    mes = v.resample("ME").last().ffill()
    rm = mes.pct_change().dropna()
    n = max(len(rm), 1)
    geo = (v.iloc[-1] / v.iloc[0]) ** (1 / n) - 1
    desc = float((v / v.cummax() - 1).min())
    return (f"total {(v.iloc[-1] / v.iloc[0] - 1)*100:+8.1f}% em {n} meses | {geo*100:+.2f}%/mês composto | "
            f"mediana {rm.median()*100:+.1f}% | meses a ganhar {(rm > 0).mean()*100:.0f}% | "
            f">=5%: {(rm >= 0.05).mean()*100:.0f}% | pior mês {rm.min()*100:+.1f}% | maior descida {desc*100:+.1f}%")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load((p3v.RAIZ / "config.yaml").read_text())["sinais"]
    t = p3y.recolher(p3v.carregar(), cfg)
    meio = t["entrada_data"].median()
    t["metade"] = np.where(t["entrada_data"] < meio, 1, 2)
    t["chave"] = t["setup"] + "|" + t["saida"]
    treino = t[t["metade"] == 1]
    media = treino.groupby("chave")["ret"].mean().to_dict()
    ganha = treino.groupby("chave")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    for k in t["chave"].unique():
        media.setdefault(k, 0.0)
        ganha.setdefault(k, 0.5)
    t = p3y.esperado(t, media, ganha)
    agora = {s: ("fecho_10" if s in TENDENCIA else "sma5_5") for s in t["setup"].unique()}
    antes = {s: p3y.HOJE.get(s, "sma5_5") for s in t["setup"].unique()}
    print(f"Trades: {len(t)}; metades a partir de {meio:%Y-%m-%d}")
    for nome, sds in (("modelo de agora", agora), ("modelo de ontem", antes)):
        x = t[t["saida"] == t["setup"].map(sds)]
        print(f"\n### {nome}")
        for lug in (5, 3, 8):
            for comp in (True, False):
                for pn in (1, 2):
                    v = curva(x[x["metade"] == pn], lug, comp)
                    print(f"  {lug} lugares {'reinvestido' if comp else 'sem reinvestir'} {pn}.ª: {resumo(v)}")
        if nome == "modelo de agora":
            v = curva(x, 5, True)
            print("  Por ano (5 lugares, reinvestido):")
            ano = v.resample("YE").last()
            ini = 1.0
            for d, fim in ano.items():
                print(f"    {d.year}: {(fim / ini - 1)*100:+.1f}%")
                ini = fim
            m = p3y.p3p.indices(pd.to_datetime(t["entrada_data"]).min())
            if not m.empty:
                r1 = m["spy_ret1"].dropna()
                for pn, (a0, a1) in ((1, (t["entrada_data"].min(), meio)), (2, (meio, t["saida_data"].max()))):
                    s = r1[(r1.index >= a0) & (r1.index < a1)]
                    meses = max(len(s) / 21, 1)
                    tot = float((1 + s).prod())
                    print(f"  S&P 500 {pn}.ª metade: {(tot - 1)*100:+.1f}% ({(tot ** (1 / meses) - 1)*100:+.2f}%/mês)")


if __name__ == "__main__":
    main()
