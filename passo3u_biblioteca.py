"""Biblioteca de regras: muitas regras de compra e de venda (CFD), cada uma medida em cada tipo de mercado.

Pedido do Pestana (6/11): "não podiam haver mais regras a dar sinais? ... até posso ter bué regras que depois
só vou para aquelas que, dependendo das condições de mercado, é que entro".
Regras de curto prazo conhecidas (Connors/Alvarez: RSI de 2 dias, "double 7s", 3 dias a cair, IBS, Bollinger;
gaps que falham; dia interior; máximos de 52 semanas), mais as três atuais, em velas diárias.
Tipos de mercado no dia da entrada (como no src/regime.py):
  forte   pelo menos 50% das ações dos EUA acima da média de 50 dias
  fraco   menos de 50%, sem queda forte
  queda   o SPY caiu mais de 3% em 5 dias
Cada regra x tipo de mercado: retorno médio líquido (compra 0,3%; venda 0,3% + 0,02%/noite) em cada metade.
Para não escolher à sorte: as regras são ESCOLHIDAS só com a 1.ª metade (média > 0,15% e pelo menos 40 trades)
e a carteira de 5 lugares é medida na 2.ª metade, contra as regras de hoje (atuais no mercado forte, pullbacks
na queda). Só EUA. Só mede.
Uso:  python passo3u_biblioteca.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

import passo3j_ordem as p3j
import passo3p_regime as p3p
from src import indicadores, saidas, sinais

RAIZ, DADOS = p3p.RAIZ, p3p.DADOS
SPREAD, NOITE = 0.003, 0.0002
MR = saidas.REGRAS["sma5"]        # reversão à média: sai ao fechar acima da média de 5 dias (stop 2 ATR)
MOM = saidas.REGRAS["atr_3_15"]   # momento: objetivo 3 ATR, stop 1,5 ATR


def regras_novas(d: pd.DataFrame) -> dict[str, tuple[str, pd.Series]]:
    """nome -> (lado, sinal). Lado 'compra_mr', 'compra_mom', 'venda_mr' ou 'venda_mom'."""
    c, o, h = d["close"], d["open"], d["high"]
    alta = c > d["sma200"]
    baixa = (c < d["sma200"]) & (d["sma50"] < d["sma200"])
    std20 = c.rolling(20).std()
    ibs = d["pos_fecho"]
    cai3 = (c < c.shift(1)) & (c.shift(1) < c.shift(2)) & (c.shift(2) < c.shift(3))
    sobe3 = (c > c.shift(1)) & (c.shift(1) > c.shift(2)) & (c.shift(2) > c.shift(3))
    interior = (h.shift(1) < h.shift(2)) & (d["low"].shift(1) > d["low"].shift(2))
    return {
        "rsi2<10 em alta": ("compra_mr", (d["rsi2"] < 10) & alta),
        "rsi2<5 sem tendência": ("compra_mr", d["rsi2"] < 5),
        "duplo 7 em alta": ("compra_mr", (c <= c.rolling(7).min()) & alta),
        "3 dias a cair em alta": ("compra_mr", cai3 & alta),
        "IBS<0,15 em alta": ("compra_mr", (ibs < 0.15) & (d["ret_dia"] < 0) & alta),
        "abaixo da Bollinger em alta": ("compra_mr", (c < d["sma20"] - 2 * std20) & alta),
        "caiu 10% em 5 dias em alta": ("compra_mr", (d["ret5"] < -0.10) & alta),
        "gap de baixa recuperado": ("compra_mr", (d["gap"] < -0.02) & (c > o) & (ibs > 0.7)),
        "rompe dia interior em alta": ("compra_mom", interior & (c > h.shift(1)) & alta),
        "máximo de 52 semanas": ("compra_mom", (c >= c.rolling(252).max()) & (d["vol_ratio"] >= 1.5)),
        "V rsi2>90 em baixa": ("venda_mr", (d["rsi2"] > 90) & baixa),
        "V duplo 7 em baixa": ("venda_mr", (c >= c.rolling(7).max()) & baixa),
        "V 3 dias a subir em baixa": ("venda_mr", sobe3 & baixa),
        "V IBS>0,85 em baixa": ("venda_mr", (ibs > 0.85) & (d["ret_dia"] > 0) & baixa),
        "V acima da Bollinger em baixa": ("venda_mr", (c > d["sma20"] + 2 * std20) & baixa),
        "V gap de alta falhado": ("venda_mr", (d["gap"] > 0.02) & (c < o) & (ibs < 0.3)),
        "V rompe mínimo 20d em baixa": ("venda_mom", (c < c.shift(1).rolling(20).min()) & (d["vol_ratio"] >= 1.5) & baixa),
    }


def simular_venda(d: pd.DataFrame, i: int, mult_obj: float | None, mult_stop: float, mr: bool) -> tuple[float, int] | None:
    """Venda a descoberto ao fecho do dia i; 5 dias no máximo. mr: sai ao fechar abaixo da média de 5 dias."""
    e, a = d["close"].iat[i], d["atr14"].iat[i]
    stop, obj = e + mult_stop * a, (e - mult_obj * a if mult_obj else None)
    for j in range(i + 1, i + 6):
        if j >= len(d):
            return None
        if d["high"].iat[j] >= stop:
            return 1 - max(stop, d["open"].iat[j]) / e, j - i
        if obj is not None and d["low"].iat[j] <= obj:
            return 1 - min(obj, d["open"].iat[j]) / e, j - i
        if mr and d["close"].iat[j] < d["sma5"].iat[j]:
            return 1 - d["close"].iat[j] / e, j - i
    return 1 - d["close"].iat[i + 5] / e, 5


def recolher(precos: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.Series]:
    atuais = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    linhas, acima = [], []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        acima.append(pd.DataFrame({"data": d["data"], "a50": (d["close"] > d["sma50"]).where(d["sma50"].notna())}))
        flags = sinais.detetar(d)
        todas = {f"atual: {s}": ("atual", flags[s]) for s in atuais} | regras_novas(d)
        for nome, (lado, sinal) in todas.items():
            for i in np.flatnonzero(sinal.fillna(False).to_numpy()):
                if lado == "atual":
                    s = nome.split(": ")[1]
                    if not sinais.passa_filtros(d.iloc[i], cfg.get("qualidade", {}).get(s, {}).get("filtros")):
                        continue
                    r = saidas.simular(d, i, saidas.regra_do_setup(s, cfg))
                    liq = None if r is None else (r[0] - SPREAD, r[1])
                elif lado.startswith("compra"):
                    r = saidas.simular(d, i, MR if lado == "compra_mr" else MOM)
                    liq = None if r is None else (r[0] - SPREAD, r[1])
                else:
                    mr = lado == "venda_mr"
                    r = simular_venda(d, i, None if mr else 3.0, 2.0 if mr else 1.5, mr)
                    liq = None if r is None else (r[0] - SPREAD - NOITE * r[1], r[1])
                if liq is None:
                    continue
                linhas.append({"ticker": ticker, "setup": nome, "entrada_data": d["data"].iat[i],
                               "saida_data": d["data"].iat[i + liq[1]], "ret": liq[0], "atr_pct": d["atr_pct"].iat[i],
                               "rsi2": d["rsi2"].iat[i], "ret5": d["ret5"].iat[i], "vol_ratio": d["vol_ratio"].iat[i]})
    amp = pd.concat(acima).dropna().groupby("data")["a50"].mean()
    return pd.DataFrame(linhas), amp


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    eua = filtrado["ticker"][~filtrado["ticker"].str.contains(".", regex=False)]
    precos = precos[precos["ticker"].isin(eua)].copy()
    precos["data"] = p3p._sem_fuso(precos["data"])

    t, amp = recolher(precos, cfg)
    m = p3p.indices(t["entrada_data"].min())
    t["a50"] = t["entrada_data"].map(amp)
    t["spy5"] = t["entrada_data"].map(m["spy_ret5"]) if not m.empty else np.nan
    t = t.dropna(subset=["a50"]).reset_index(drop=True)
    t["mercado"] = np.where(t["spy5"] < -0.03, "queda", np.where(t["a50"] >= 0.5, "forte", "fraco"))
    meio = t["entrada_data"].median()
    t["metade"] = np.where(t["entrada_data"] < meio, 1, 2)
    dias = t.groupby("mercado")["entrada_data"].nunique()
    print(f"Trades EUA: {len(t)}; metades a partir de {meio:%Y-%m-%d}; SPY {'OK' if not m.empty else 'SEM DADOS'}; "
          f"dias por mercado: {dias.to_dict()}")

    # 1. Cada regra em cada mercado
    print("\n### 1. Regras x mercado (líquido; 1.ª|2.ª metade; * = > 0,15% nas DUAS metades com >= 40 trades em cada)")
    escolhidas, tabela = set(), []
    for (s, mk), g in t.groupby(["setup", "mercado"]):
        a, b = g[g["metade"] == 1], g[g["metade"] == 2]
        ok1 = len(a) >= 40 and a["ret"].mean() > 0.0015
        if ok1:
            escolhidas.add((s, mk))
        duas = ok1 and len(b) >= 40 and b["ret"].mean() > 0.0015
        tabela.append((s, mk, len(g), g["ret"].mean(), a["ret"].mean() if len(a) else np.nan,
                       b["ret"].mean() if len(b) else np.nan, (g["ret"] > 0).mean(), len(a), len(b), duas))
    for s, mk, n, med, ma, mb, w, na, nb, duas in sorted(tabela, key=lambda x: (x[0], x[1])):
        print(f"  {'*' if duas else ' '} {s:30s} {mk:6s} n={n:6d} ({na}|{nb}) {med*100:+.2f}% "
              f"({ma*100:+.2f}|{mb*100:+.2f}) ganha {w*100:.0f}%")

    # 2. Carteira de 5 lugares: regras escolhidas só com a 1.ª metade, medidas na 2.ª
    treino = t[t["metade"] == 1]
    media = treino.groupby("setup")["ret"].mean().to_dict()
    ganha = treino.groupby("setup")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    for s in t["setup"].unique():
        media.setdefault(s, 0.0)
        ganha.setdefault(s, 0.5)
    eq = p3j.medidas(t.to_dict("records"), media, ganha)
    eq.index = t.sort_values("entrada_data", kind="stable").index
    t["equilibrado"] = eq["equilibrado"].reindex(t.index)
    atual = t["setup"].str.startswith("atual") & ((t["mercado"] == "forte") |
                                                   ((t["mercado"] == "queda") & (t["setup"] == "atual: pullback")))
    bib = pd.Series([(s, mk) in escolhidas for s, mk in zip(t["setup"], t["mercado"])], index=t.index)
    forte, queda = t["mercado"] == "forte", t["mercado"] == "queda"
    caiu10 = (t["setup"] == "caiu 10% em 5 dias em alta") & forte
    mr = t["setup"].isin(["rsi2<10 em alta", "rsi2<5 sem tendência", "duplo 7 em alta", "3 dias a cair em alta",
                          "IBS<0,15 em alta", "abaixo da Bollinger em alta", "caiu 10% em 5 dias em alta"])
    m1 = treino.groupby(["setup", "mercado"])["ret"].agg(["mean", "size"])
    fortes1 = {k for k, v in m1.iterrows() if v["size"] >= 40 and v["mean"] > 0.005}
    bib5 = pd.Series([(s, mk) in fortes1 for s, mk in zip(t["setup"], t["mercado"])], index=t.index)
    variantes = {"hoje (atuais no forte + pullbacks na queda)": atual,
                 "hoje + caiu 10% em 5 dias (forte)": atual | caiu10,
                 "hoje + caiu 10% + reversão na queda": atual | caiu10 | (mr & queda),
                 "hoje + reversão na queda": atual | (mr & queda),
                 "escolhidas na 1.ª com > 0,5%": bib5,
                 "escolhidas na 1.ª com > 0,15%": bib,
                 "sem filtros (todas as regras sempre)": pd.Series(True, index=t.index)}
    print(f"\n### 2. Carteira de 5 lugares (ordem equilibrada), 1.ª e 2.ª metade. Escolhidas na 1.ª (>0,15%): "
          f"{len(escolhidas)}; com > 0,5%: {sorted(fortes1)}")
    for nome, ok in variantes.items():
        txt = []
        for pn in (1, 2):
            seg = t[t["metade"] == pn]
            r = p3p.carteira(seg.assign(peso=ok.loc[seg.index].astype(float), ret=seg["ret"] + p3p.CUSTO))
            txt.append(f"{pn}.ª {r['total']*100:+6.1f}% (desc {r['descida']*100:+5.1f}%, {r['n']} trades, "
                       f"{seg[ok.loc[seg.index]]['entrada_data'].nunique()}/{seg['entrada_data'].nunique()} dias)")
        print(f"  {nome:42s} " + " | ".join(txt))

    # 3. Sinais à parte (em teste): no máximo 5 ações por dia; que ordem escolhe melhor?
    print("\n### 3. Em teste: 5 ações por dia (uma linha por ação, com o melhor resultado das regras que deu)")
    conjuntos = {"forte": (forte, ["caiu 10% em 5 dias em alta", "rsi2<5 sem tendência"]),
                 "forte só caiu 10%": (forte, ["caiu 10% em 5 dias em alta"]),
                 "queda": (queda, ["rsi2<10 em alta", "rsi2<5 sem tendência", "duplo 7 em alta", "3 dias a cair em alta",
                                   "IBS<0,15 em alta", "abaixo da Bollinger em alta", "caiu 10% em 5 dias em alta",
                                   "rompe dia interior em alta", "atual: pullback"])}
    for nome, (mk, regras) in conjuntos.items():
        x = t[mk & t["setup"].isin(regras)]
        x = x.assign(n_regras=x.groupby(["entrada_data", "ticker"])["setup"].transform("size"))
        x = x.sort_values("ret").drop_duplicates(["entrada_data", "ticker"])   # pior caso entre as regras da ação
        for ordem, col, asc in (("mais caiu em 5 dias", "ret5", True), ("RSI2 mais baixo", "rsi2", True),
                                ("mais regras", "n_regras", False), ("mais volátil", "atr_pct", False),
                                ("menos volátil", "atr_pct", True), ("ao acaso", None, True)):
            y = x.sample(frac=1, random_state=1) if col is None else x.sort_values(col, ascending=asc, kind="stable")
            top = y.groupby("entrada_data").head(5)
            a, b = top[top["metade"] == 1], top[top["metade"] == 2]
            print(f"  {nome:18s} {ordem:20s} n={len(top):5d} ({top['entrada_data'].nunique()} dias) "
                  f"{top['ret'].mean()*100:+.2f}% ({a['ret'].mean()*100:+.2f}|{b['ret'].mean()*100:+.2f}) "
                  f"ganha {(top['ret'] > 0).mean()*100:.0f}% | pior 10% {p3j.pior10(top['ret'])*100:+.1f}%")


if __name__ == "__main__":
    main()
