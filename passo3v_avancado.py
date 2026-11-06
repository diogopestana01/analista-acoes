"""Abordagens mais ricas do que padrões de gráfico, testadas com 10 anos (1500 ações dos EUA mais negociadas).

Pedido do Pestana (6/11): "estamos a usar regras muito simplistas, não há mais coisas a analisar?"
Testa o que a literatura usa para 1 dia - 1 semana, com dados grátis:
  1. Contra o setor        caiu 8 pontos mais do que o setor em 5 dias (reversão "residual", mais forte do que
                           a simples), em tendência.
  2. Líder do ano a recuar  nos 20% que mais subiram em 12 meses (momento) e RSI2 < 10 (recuo curto).
  3. Calendário            compras de recuo (RSI2 < 10 em tendência) na viragem do mês (último dia útil e 3
                           primeiros) contra o resto; e por dia da semana.
  4. Medo (VIX)            recuo com o VIX a disparar (> 1,1 x a média de 20 dias) e com o VIX acima de 30.
  5. Modelo que junta tudo  ranking (gradient boosting) com 20 variáveis da ação e do mercado, treinado numa
                           metade e testado na outra (e ao contrário): as 5 melhores por dia, 5 dias.
  (Surpresa nos resultados: a Nasdaq só dá os últimos 4 trimestres; não chega para 10 anos -> fica de fora.)
Regras 1-4: saída sma5 (stop 2 ATR, 5 dias), líquido de 0,3%, por tipo de mercado e metade (como passo3u).
Modelo 5: retorno de 5 dias fecho a fecho, líquido de 0,3%, comparado com 5 ao acaso e com as regras.
Só mede. Uso: ANOS=10 ACOES=1500 python passo3v_avancado.py
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd
import yaml

import passo3p_regime as p3p
from src import indicadores, saidas

RAIZ, DADOS, CUSTO = p3p.RAIZ, p3p.DADOS, 0.003
MR = saidas.REGRAS["sma5"]
VARS = ["rsi2", "rsi14", "ret_dia", "ret5", "ret20", "ret60", "ret252", "d_sma20", "d_sma50", "d_sma200",
        "atr_pct", "vol_ratio", "pos_fecho", "gap", "rel_setor5", "rank252", "a50", "spy5", "vix", "vix_rel"]


def carregar() -> pd.DataFrame:
    from src import precos as precos_mod
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    eua = filtrado["ticker"][~filtrado["ticker"].str.contains(".", regex=False)]
    precos = precos[precos["ticker"].isin(eua)]
    anos = int(os.environ.get("ANOS", "0") or 0)
    if anos:
        p = precos.assign(dinheiro=precos["close"] * precos["volume"])
        p = p[pd.to_datetime(p["data"]) >= pd.to_datetime(p["data"]).max() - pd.Timedelta(days=90)]
        top = p.groupby("ticker")["dinheiro"].median().nlargest(int(os.environ.get("ACOES", "1500"))).index
        precos = precos_mod.descarregar(sorted(top), periodo=f"{anos}y")
    precos = precos.copy()
    precos["data"] = p3p._sem_fuso(precos["data"])
    return precos


def setores() -> pd.Series:
    try:
        import passo3t_dados
        return passo3t_dados.setores()
    except Exception as e:  # noqa: BLE001
        logging.warning("Setores falharam: %s", e)
        return pd.Series(dtype=str)


def preparar(precos: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    partes, por_ticker = [], {}
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 260:
            continue
        d = indicadores.calcular(df)
        c = d["close"]
        d["ticker"] = ticker
        d["ret60"], d["ret252"] = c.pct_change(60), c.pct_change(252)
        for k in (20, 50, 200):
            d[f"d_sma{k}"] = c / d[f"sma{k}"] - 1
        d["fwd5"] = (c.shift(-5) / c - 1).clip(-0.5, 0.5)
        por_ticker[ticker] = d
        partes.append(d[["data", "ticker", "close", "sma200", "sma50", "fwd5"] + [v for v in VARS if v in d]])
    return pd.concat(partes, ignore_index=True), por_ticker


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    precos = carregar()
    todos, por_ticker = preparar(precos)
    print(f"Ações: {todos['ticker'].nunique()}, linhas {len(todos)}, de {todos['data'].min():%Y-%m-%d} "
          f"a {todos['data'].max():%Y-%m-%d}")
    # Mercado e setor
    todos["acima50"] = (todos["close"] > todos["sma50"]).astype(float)
    todos["a50"] = todos.groupby("data")["acima50"].transform("mean")
    m = p3p.indices(todos["data"].min())
    if not m.empty:
        todos["spy5"] = todos["data"].map(m["spy_ret5"])
        todos["vix"] = todos["data"].map(m["vix"])
        vix20 = m["vix"].rolling(20).mean()
        todos["vix_rel"] = todos["data"].map(m["vix"] / vix20)
    else:
        todos["spy5"] = todos["vix"] = todos["vix_rel"] = np.nan
    sec = setores()
    todos["setor"] = todos["ticker"].map(sec).fillna("?")
    todos["rel_setor5"] = todos["ret5"] - todos.groupby(["data", "setor"])["ret5"].transform("mean")
    todos["rank252"] = todos.groupby("data")["ret252"].rank(pct=True)
    todos["mercado"] = np.where(todos["spy5"] < -0.03, "queda", np.where(todos["a50"] >= 0.5, "forte", "fraco"))
    meio = todos["data"].quantile(0.5)
    todos["metade"] = np.where(todos["data"] < meio, 1, 2)
    print(f"Metades a partir de {meio:%Y-%m-%d}; setores conhecidos {(todos['setor'] != '?').mean() * 100:.0f}%; "
          f"VIX {'OK' if not m.empty else 'SEM DADOS'}")

    # Regras 1-4
    alta = todos["close"] > todos["sma200"]
    recuo = alta & (todos["rsi2"] < 10)
    dia = todos["data"]
    ultimo_mes = dia.groupby(dia.dt.to_period("M")).transform("max") == dia
    pos_mes = dia.groupby(dia.dt.to_period("M")).rank(method="dense")
    viragem = ultimo_mes | (pos_mes <= 3)
    regras = {
        "1 contra o setor (-8 pts em 5d)": alta & (todos["rel_setor5"] < -0.08),
        "1b contra o setor (-15 pts em 5d)": alta & (todos["rel_setor5"] < -0.15),
        "2 líder do ano a recuar": (todos["rank252"] >= 0.8) & (todos["rsi2"] < 10),
        "2b líder do ano a recuar, em tendência": (todos["rank252"] >= 0.8) & recuo,
        "3 recuo na viragem do mês": recuo & viragem,
        "3 recuo no resto do mês": recuo & ~viragem,
        "3 recuo à segunda": recuo & (dia.dt.dayofweek == 0),
        "3 recuo à sexta": recuo & (dia.dt.dayofweek == 4),
        "4 recuo com o VIX a disparar": recuo & (todos["vix_rel"] > 1.1),
        "4 recuo com o VIX > 30": recuo & (todos["vix"] > 30),
        "4b RSI2 < 5 com o VIX > 30": (todos["rsi2"] < 5) & (todos["vix"] > 30),
        "referência: recuo (RSI2 < 10 em tendência)": recuo,
    }
    linhas = []
    base_de = todos.reset_index().groupby("ticker")["index"].min().to_dict()
    for nome, flag in regras.items():
        for t, i in todos.index[flag.fillna(False)].to_series().groupby(todos["ticker"]):
            d = por_ticker[t]
            base = base_de[t]
            for k in (i.to_numpy() - base):
                r = saidas.simular(d, int(k), MR)
                if r is not None:
                    linhas.append((nome, int(base + k), r[0] - CUSTO))
    tr = pd.DataFrame(linhas, columns=["regra", "idx", "ret"])
    tr = tr.join(todos[["data", "mercado", "metade"]], on="idx")
    print("\n### 1-4. Regras (saída sma5, líquido de 0,3%; 1.ª|2.ª metade; * = > 0,15% nas duas, >= 40 trades)")
    for (r, mk), g in tr.groupby(["regra", "mercado"]):
        a, b = g[g["metade"] == 1], g[g["metade"] == 2]
        ok = len(a) >= 40 and len(b) >= 40 and a["ret"].mean() > 0.0015 and b["ret"].mean() > 0.0015
        print(f"  {'*' if ok else ' '} {r:42s} {mk:6s} n={len(g):6d} {g['ret'].mean()*100:+.2f}% "
              f"({a['ret'].mean()*100:+.2f}|{b['ret'].mean()*100:+.2f}) ganha {(g['ret'] > 0).mean()*100:.0f}%")

    # 5. Modelo que junta tudo
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError:
        print("\n### 5. sklearn não instalado: modelo saltado")
        return
    x = todos.dropna(subset=["fwd5", "ret252", "rsi2", "sma200"]).copy()
    print(f"\n### 5. Modelo que junta tudo ({len(x)} dias-ação; retorno de 5 dias fecho a fecho, líquido de 0,3%)")
    for treino_m, teste_m in ((1, 2), (2, 1)):
        tr_x, te_x = x[x["metade"] == treino_m], x[x["metade"] == teste_m]
        amostra = tr_x.sample(min(len(tr_x), 1_500_000), random_state=1)
        mdl = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                                            min_samples_leaf=500, l2_regularization=1.0, random_state=1)
        vs = [v for v in VARS if amostra[v].nunique() > 1]
        mdl.fit(amostra[vs].astype("float32"), amostra["fwd5"].clip(-0.2, 0.2))
        te_x = te_x.assign(prev=mdl.predict(te_x[vs].astype("float32")))
        for filtro, y in (("todos os mercados", te_x), ("só mercado forte", te_x[te_x["mercado"] == "forte"]),
                          ("só mercado fraco", te_x[te_x["mercado"] == "fraco"]),
                          ("só queda", te_x[te_x["mercado"] == "queda"])):
            if y.empty:
                continue
            top = y.sort_values("prev", ascending=False).groupby("data").head(5)
            fundo = y.sort_values("prev").groupby("data").head(5)
            acaso = y.sample(frac=1, random_state=2).groupby("data").head(5)
            print(f"  treino {treino_m}.ª -> teste {teste_m}.ª {filtro:18s} top 5/dia {(top['fwd5'] - CUSTO).mean()*100:+.2f}% "
                  f"(ganha {(top['fwd5'] > CUSTO).mean()*100:.0f}%, pior 10% {np.sort(top['fwd5'])[:max(len(top)//10,1)].mean()*100:+.1f}%) | "
                  f"ao acaso {(acaso['fwd5'] - CUSTO).mean()*100:+.2f}% | piores 5 {(fundo['fwd5'] - CUSTO).mean()*100:+.2f}% "
                  f"| {top['data'].nunique()} dias")
        imp = pd.Series(np.abs(np.corrcoef(np.vstack([te_x[vs].fillna(0).T.to_numpy(), te_x["prev"].to_numpy()]))[-1, :-1]),
                        index=vs).sort_values(ascending=False)
        print(f"    variáveis mais ligadas à previsão: {', '.join(f'{k} {v:.2f}' for k, v in imp.head(8).items())}")
    # Referência para comparar: os setups com o mesmo retorno de 5 dias
    rr = x[(x["close"] > x["sma200"]) & (x["ret5"] < -0.10) & (x["mercado"] == "forte")]
    for mtd in (1, 2):
        g = rr[rr["metade"] == mtd].groupby("data").head(5)
        print(f"  referência caiu 10% (forte), 5/dia, {mtd}.ª metade: {(g['fwd5'] - CUSTO).mean()*100:+.2f}% "
              f"({len(g)} trades)")


if __name__ == "__main__":
    main()
