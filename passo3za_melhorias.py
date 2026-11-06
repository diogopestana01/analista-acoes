"""Melhorias da investigação de 6/11 (doc "Como analisar o mercado e pôr isso num bot autónomo").
10 anos, 1500 ações dos EUA, entradas e saídas do modelo de hoje, carteira de 5 lugares, sem trocas.

Critérios fixados ANTES de correr (não se afinam depois):
  setor2   no máximo 2 posições do mesmo setor. Passa se a queda máxima for menor nas duas metades e o
           retorno não cair mais de 10% (em pontos relativos) em nenhuma.
  vix      dinheiro investido conforme o medo do mercado no dia da compra: VIX < 20 posição inteira,
           20-30 60%, > 30 40% (medo_vix fica inteiro: é feito para esse momento). Passa se o Sharpe
           (dia a dia) for melhor nas duas metades.
  sorte    Sharpe deflacionado de cada setup (por trade) e da carteira (por dia), com o número de
           tentativas do projeto (data/tentativas.csv). >= 0,95 = provavelmente não é sorte.
Só mede; nada muda no modelo.
Uso: ANOS=10 ACOES=1500 python passo3za_melhorias.py
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yaml

import passo3p_regime as p3p
import passo3v_avancado as p3v
import passo3y_gestao as p3y
from src import contexto, estatistica

ISENTOS_VIX = {"medo_vix"}


def escala_vix(v: float) -> float:
    if not np.isfinite(v) or v < 20:
        return 1.0
    return 0.6 if v <= 30 else 0.4


def carteira(c: pd.DataFrame, lugares: int = 5, setor_max: int | None = None, vix: bool = False) -> dict:
    """Como passo3y.carteira (ordem equilibrada, sem trocas, tamanho igual), mais o limite por setor e a
    escala pelo VIX. Devolve também os retornos dia a dia da carteira."""
    if c.empty:
        return {"total": 0.0, "descida": 0.0, "n": 0, "sharpe": 0.0, "dia": pd.Series(dtype=float)}
    c = c.assign(rk=c.groupby("entrada_data")["esperado"].rank(pct=True) + c.groupby("entrada_data")["taxa"].rank(pct=True))
    abertas, capital, livre, curva_d, curva_v, n = {}, 1.0, 1.0, [], [], 0
    datas = sorted(set(c["entrada_data"]) | set(c["saida_data"]))
    por_dia = dict(tuple(c.sort_values(["entrada_data", "rk"], ascending=[True, False]).groupby("entrada_data")))
    for dt in datas:
        for tk in [x for x, v in abertas.items() if v[0] <= dt]:
            _, aloc, ret, _ = abertas.pop(tk)
            capital += aloc * ret
            livre += aloc * (1 + ret)
        curva_d.append(dt)
        curva_v.append(capital)
        g = por_dia.get(dt)
        if g is None:
            continue
        for r in g.drop_duplicates("ticker").itertuples():
            if len(abertas) >= lugares:
                break
            if r.ticker in abertas:
                continue
            if setor_max and r.setor and sum(1 for v in abertas.values() if v[3] == r.setor) >= setor_max:
                continue
            w = 1.0 if (not vix or r.setup in ISENTOS_VIX) else escala_vix(r.vix)
            aloc = min(1.0 / lugares * w, max(livre, 0.0))
            if aloc <= 0:
                break
            livre -= aloc
            abertas[r.ticker] = (r.saida_data, aloc, r.ret, r.setor)
            n += 1
    for _, aloc, ret, _ in abertas.values():
        capital += aloc * ret
    v = pd.Series(curva_v + [capital], index=pd.to_datetime(curva_d + [curva_d[-1] + pd.Timedelta(days=1)]))
    dia = v.pct_change().dropna()
    sh = float(dia.mean() / dia.std() * np.sqrt(252)) if dia.std() > 0 else 0.0
    return {"total": capital - 1, "descida": float((v / v.cummax() - 1).min()), "n": n, "sharpe": sh, "dia": dia}


def fmt(r: dict) -> str:
    return f"{r['total']*100:+7.1f}% (desc {r['descida']*100:+5.1f}%, Sharpe {r['sharpe']:.2f}, {r['n']} trades)"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load((p3v.RAIZ / "config.yaml").read_text())["sinais"]
    p3y.SAIDAS = {k: p3y.SAIDAS[k] for k in ("fecho_10", "sma5_5")}
    precos = p3v.carregar()
    t = p3y.recolher(precos, cfg)
    saida_de = {s: ("fecho_10" if (cfg.get("qualidade", {}).get(s, {}) or {}).get("saida") == "fecho_10" else "sma5_5")
                for s in t["setup"].unique()}
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
    setor = contexto.setores(p3p.DADOS / "setores.csv")
    t["setor"] = t["ticker"].map(setor).fillna("")
    m = p3p.indices(pd.to_datetime(precos["data"]).min())
    vix = m["vix"] if not m.empty else pd.Series(dtype=float)
    t["vix"] = pd.to_datetime(t["entrada_data"]).map(vix).astype(float)
    print(f"Trades: {len(t)}; metades a partir de {meio:%Y-%m-%d}; setor conhecido em "
          f"{(t['setor'] != '').mean()*100:.0f}%; VIX conhecido em {t['vix'].notna().mean()*100:.0f}%")

    modelo = t[t["saida"] == t["setup"].map(saida_de)]
    meta = {pn: modelo[modelo["metade"] == pn] for pn in (1, 2)}
    base = {pn: carteira(meta[pn]) for pn in (1, 2)}
    print("\n### Carteira de 5 lugares, modelo de hoje")
    print("  base:      " + " | ".join(f"{pn}.ª {fmt(base[pn])}" for pn in (1, 2)))

    def passa_setor(r: dict, b: dict) -> bool:
        menos_queda = r["descida"] > b["descida"]
        if b["total"] > 0:
            return menos_queda and r["total"] >= 0.9 * b["total"]
        return menos_queda and r["total"] >= b["total"]

    for nome, kw, teste in (("setor2", {"setor_max": 2}, passa_setor),
                            ("vix", {"vix": True}, lambda r, b: r["sharpe"] > b["sharpe"]),
                            ("setor2+vix", {"setor_max": 2, "vix": True},
                             lambda r, b: passa_setor(r, b) and r["sharpe"] > b["sharpe"]),
                            ("setor3 (só para ver)", {"setor_max": 3}, passa_setor),
                            ("setor1 (só para ver)", {"setor_max": 1}, passa_setor)):
        res = {pn: carteira(meta[pn], **kw) for pn in (1, 2)}
        ok = all(teste(res[pn], base[pn]) for pn in (1, 2))
        print(f"  {nome:22s} " + " | ".join(f"{pn}.ª {fmt(res[pn])}" for pn in (1, 2))
              + f"  -> {'PASSA' if ok else 'não passa'}")

    # Concentração: quantas vezes a carteira base teve 3+ posições do mesmo setor
    print("\n### Sorte (Sharpe deflacionado)")
    n_tent = estatistica.tentativas(p3v.RAIZ / "data" / "tentativas.csv")
    grupos = {k: g["ret"].to_numpy() for k, g in t.groupby("chave")}
    vs = estatistica.var_sharpes(grupos)
    print(f"  tentativas do projeto: {n_tent}; variação dos Sharpes entre {len(grupos)} setup x saída: {vs:.5f}")
    for s, g in modelo.groupby("setup"):
        r = estatistica.sharpe_deflacionado(g["ret"], n_tent, vs)
        r1 = estatistica.sharpe_deflacionado(g[g["metade"] == 1]["ret"], n_tent, vs)
        r2 = estatistica.sharpe_deflacionado(g[g["metade"] == 2]["ret"], n_tent, vs)
        print(f"  {s:15s} n={r['n']:6d} Sharpe/trade {r['sharpe']:+.3f} (limiar {r['limiar']:.3f}) "
              f"prob {r['prob']:.2f} | metades {r1['prob']:.2f} {r2['prob']:.2f} | média {g['ret'].mean()*100:+.2f}%")
    # Carteira por dia: a variação entre tentativas é a das carteiras com outras saídas
    alt = {f"{k}": carteira(t[(t["saida"] == k)])["dia"].to_numpy() for k in ("fecho_10", "sma5_5")}
    alt["modelo"] = pd.concat([base[1]["dia"], base[2]["dia"]]).to_numpy()
    vsd = estatistica.var_sharpes(alt)
    r = estatistica.sharpe_deflacionado(alt["modelo"], n_tent, vsd)
    print(f"  carteira (por dia) Sharpe/dia {r['sharpe']:+.3f} limiar {r['limiar']:.3f} prob {r['prob']:.2f} "
          f"(n={r['n']})")


if __name__ == "__main__":
    main()
