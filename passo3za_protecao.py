"""Proteger a carteira nas crises (2018, 2020, 2021-22), com os ganhos reinvestidos, 10 anos (1500 ações dos EUA).

Pedido do Pestana (6/11): menos risco nos anos e meses maus, pode vender CFD (proteção) e alargar o prazo.
Base = modelo de agora (tendências fecho_10, ressaltos sma5_5), 5 lugares, ordem equilibrada, reinvestido.
Variantes (todas fixas, não afinadas):
  travão_15 / travão_10  carteira cai 15% (10%) desde o máximo -> 20 dias úteis sem compras novas
  meio_200               S&P 500 abaixo da média de 200 dias -> cada posição com metade do tamanho
  vix                    tamanho x min(1, 20/VIX), nunca menos de 40%
  cfd_200                S&P 500 abaixo da média de 200 dias -> venda CFD do S&P com 50% da carteira
  cfd_sempre             venda CFD do S&P igual a metade do que está investido (tira metade do "beta")
  prazo 15 / 20 / segue  tendências até 15 dias (stop 2 ATR), 20 dias (stop 2,5 ATR), 20 dias com stop que sobe
O CFD paga ~0,02%/dia de financiamento (estimativa conservadora). Mede por metade: %/mês composto,
pior mês, maior descida, e os anos de crise.
Uso: ANOS=10 ACOES=1500 python passo3za_protecao.py
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yaml

import passo3v_avancado as p3v
import passo3y_gestao as p3y
from passo3z_composto import TENDENCIA, resumo

FIN_CFD = 0.0002


def curva(c: pd.DataFrame, m: pd.DataFrame, lugares: int = 5, travao: float | None = None, tamanho=None,
          cobertura=None) -> pd.Series:
    """Capital ao fecho de cada dia de bolsa. tamanho(dia) -> fração do lugar; cobertura(dia, investido, capital)
    -> valor vendido em CFD do S&P (decidido no fecho, ganha/perde no dia seguinte)."""
    c = c.assign(rk=c.groupby("entrada_data")["esperado"].rank(pct=True) + c.groupby("entrada_data")["taxa"].rank(pct=True))
    por_dia = dict(tuple(c.sort_values(["entrada_data", "rk"], ascending=[True, False]).groupby("entrada_data")))
    ini, fim = c["entrada_data"].min(), c["saida_data"].max()
    datas = sorted(set(c["entrada_data"]) | set(c["saida_data"]) | set(m.index[(m.index >= ini) & (m.index <= fim)]))
    r1 = m["spy_ret1"].fillna(0.0)
    abertas, capital, livre, pico, pausa, cfd, pontos = {}, 1.0, 1.0, 1.0, 0, 0.0, {}
    for dt in datas:
        if cfd:
            ganho = -cfd * float(r1.get(dt, 0.0)) - cfd * FIN_CFD
            capital += ganho
            livre += ganho
        for tk in [x for x, v in abertas.items() if v[0] <= dt]:
            _, aloc, ret = abertas.pop(tk)
            capital += aloc * ret
            livre += aloc * (1 + ret)
        pico = max(pico, capital)
        if travao is not None and capital / pico - 1 < -travao and pausa == 0:
            pausa, pico = 20, capital      # recomeça a contar a partir de agora
        g = por_dia.get(dt)
        if g is not None and pausa == 0:
            f = tamanho(dt) if tamanho else 1.0
            for r in g.drop_duplicates("ticker").itertuples():
                if len(abertas) >= lugares:
                    break
                if r.ticker in abertas:
                    continue
                aloc = min(capital / lugares * f, max(livre, 0.0))
                if aloc <= 0:
                    break
                livre -= aloc
                abertas[r.ticker] = (r.saida_data, aloc, r.ret)
        pausa = max(pausa - 1, 0)
        investido = sum(v[1] for v in abertas.values())
        cfd = cobertura(dt, investido, capital) if cobertura else 0.0
        pontos[dt] = capital
    return pd.Series(pontos).sort_index()


def anos(v: pd.Series, quais=(2018, 2020, 2021, 2022)) -> str:
    a = v.resample("YE").last()
    prev = a.shift(1).fillna(1.0)
    r = a / prev - 1
    return " ".join(f"{d.year}:{x*100:+.0f}%" for d, x in r.items() if d.year in quais)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p3y.SAIDAS = {k: p3y.SAIDAS[k] for k in ("sma5_5", "fecho_10", "fecho_15", "fecho_20", "segue_25_20")}
    cfg = yaml.safe_load((p3v.RAIZ / "config.yaml").read_text())["sinais"]
    precos = p3v.carregar()
    t = p3y.recolher(precos, cfg)
    m = p3y.p3p.indices(pd.to_datetime(precos["data"]).min())
    del precos
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
    abaixo = (~m["spy_sma200"].astype(bool)) if "spy_sma200" in m else pd.Series(False, index=m.index)
    vix = m["vix"].ffill()

    def x_saida(tend: str) -> pd.DataFrame:
        sds = {s: (tend if s in TENDENCIA else "sma5_5") for s in t["setup"].unique()}
        return t[t["saida"] == t["setup"].map(sds)]

    base = x_saida("fecho_10")
    variantes = {
        "base (modelo de agora)": (base, {}),
        "travão_15": (base, {"travao": 0.15}),
        "travão_10": (base, {"travao": 0.10}),
        "meio_200": (base, {"tamanho": lambda d: 0.5 if bool(abaixo.get(d, False)) else 1.0}),
        "vix": (base, {"tamanho": lambda d: float(np.clip(20 / vix.get(d, 20), 0.4, 1.0))}),
        "cfd_200": (base, {"cobertura": lambda d, inv, cap: 0.5 * cap if bool(abaixo.get(d, False)) else 0.0}),
        "cfd_sempre": (base, {"cobertura": lambda d, inv, cap: 0.5 * inv}),
        "travão_15 + cfd_200": (base, {"travao": 0.15,
                                        "cobertura": lambda d, inv, cap: 0.5 * cap if bool(abaixo.get(d, False)) else 0.0}),
        "prazo 15 dias": (x_saida("fecho_15"), {}),
        "prazo 20 dias (stop 2,5)": (x_saida("fecho_20"), {}),
        "prazo 20 dias, stop que sobe": (x_saida("segue_25_20"), {}),
    }
    print(f"Trades: {len(t)}; metades a partir de {meio:%Y-%m-%d}; S&P abaixo da média de 200 dias "
          f"{abaixo.mean()*100:.0f}% dos dias")
    print("\n### 5 lugares, reinvestido (1.ª | 2.ª metade; anos de crise com a curva inteira)")
    for nome, (x, kw) in variantes.items():
        partes = [resumo(curva(x[x["metade"] == pn], m, **kw)) for pn in (1, 2)]
        print(f"  {nome}\n    1.ª {partes[0]}\n    2.ª {partes[1]}\n    {anos(curva(x, m, **kw))}")


if __name__ == "__main__":
    main()
