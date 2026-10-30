"""Spread: quanto custa de verdade entrar e sair, e se deve cortar ou mudar a ordem dos sinais.

Pedido do Pestana (30/10): entrou em 5 sinais e todos abriram a perder ~1% só pelo spread (compra-se
ao preço de cima, vende-se ao de baixo). O backtest usava um custo fixo de 0,3% ida e volta.
Não há histórico de bid/ask; estima-se o spread de cada ação no dia do sinal só com velas diárias
(Abdi & Ranaldo 2017: fecho contra o meio entre máximo e mínimo de dois dias seguidos, média de 21
dias para trás). É uma estimativa com ruído: o registo em papel passa a guardar o spread verdadeiro
da Nasdaq para a confirmar.
Mede, para as mesmas entradas do backtest (últimos 40% do período, tabelas dos primeiros 60%):
  1. retorno antes e depois do spread estimado, por quinto do spread e por setup, nas duas metades
  2. carteira de 5 lugares (custo = spread estimado de cada trade) com a ordem equilibrada:
       base          como hoje
       corte_X       sem sinais com spread estimado acima de X%
       liquido       ordem equilibrada com o ganho esperado já sem o spread
       paga          só sinais cujo ganho esperado paga o spread
     contra 30 ordens ao acaso. Só vale mudar se for melhor nas duas metades.
Uso:  python passo3l_spread.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

import passo3e_trocas as t3
import passo3j_ordem as p3j
from src import indicadores, saidas, sinais

RAIZ, DADOS = t3.RAIZ, t3.DADOS
JANELA = 21
CORTES = (0.005, 0.0075, 0.01, 0.015)


def spread_estimado(d: pd.DataFrame, janela: int = JANELA) -> pd.Series:
    """Abdi-Ranaldo: s² = 4·média[(c_t − η_t)(c_t − η_{t+1})], com c = log do fecho e η = média dos logs
    do máximo e do mínimo. Para o dia i usa só dias até i (o último produto precisa de η_i e c_{i-1})."""
    c = np.log(d["close"])
    eta = (np.log(d["high"]) + np.log(d["low"])) / 2
    prod = (c.shift(1) - eta.shift(1)) * (c.shift(1) - eta)   # par (i-1, i): conhecido ao fecho de i
    m = prod.rolling(janela, min_periods=janela // 2).mean()
    return np.sqrt((4 * m).clip(lower=0))


def recolher(precos: pd.DataFrame, cfg: dict) -> list[dict]:
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    out = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        sp = spread_estimado(d).to_numpy()
        dolares = (d["close"] * d["volume"]).rolling(20).mean().to_numpy()
        flags = sinais.detetar(d)
        for nome in setups:
            regra = saidas.regra_do_setup(nome, cfg)
            filtros = cfg.get("qualidade", {}).get(nome, {}).get("filtros")
            for i in np.flatnonzero(flags[nome].to_numpy()):
                if not sinais.passa_filtros(d.iloc[i], filtros):
                    continue
                r = saidas.simular(d, i, regra)
                if r is None or np.isnan(sp[i]):
                    continue
                out.append({"ticker": ticker, "setup": nome, "entrada_data": d["data"].iat[i],
                            "saida_data": d["data"].iat[i + r[1]], "ret": r[0], "atr_pct": d["atr_pct"].iat[i],
                            "spread": float(sp[i]), "preco": float(d["close"].iat[i]),
                            "dolares": float(dolares[i])})
    return out


def carteira(c: pd.DataFrame, chave: str, lugares: int, rng=None) -> dict:
    """Como passo3j_ordem.carteira, mas cada trade paga o seu spread estimado (mínimo 0,1%)."""
    c = c.assign(_o=rng.random(len(c)) if chave == "aleatorio" else c[chave])
    abertas, trades = {}, []
    for d, g in c.sort_values(["entrada_data", "_o"], ascending=[True, False]).groupby("entrada_data", sort=True):
        for tk in [x for x, s in abertas.items() if s <= d]:
            abertas.pop(tk)
        for r in g.drop_duplicates("ticker").itertuples():
            if len(abertas) >= lugares:
                break
            if r.ticker not in abertas:
                abertas[r.ticker] = r.saida_data
                trades.append((r.saida_data, r.ret - max(r.spread, 0.001)))
    t = pd.DataFrame(trades, columns=["saida", "ret"]).sort_values("saida")
    curva = 1 + (t["ret"] / lugares).cumsum()
    return {"total": curva.iat[-1] - 1, "n": len(t), "media": t["ret"].mean(), "perde": (t["ret"] < 0).mean(),
            "descida": (curva / curva.cummax() - 1).min()}


def variantes(m: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Cada variante = os sinais que ficam + a coluna de ordem."""
    out = {"base": m.assign(_k=m["equilibrado"])}
    for x in CORTES:
        f = m[m["spread"] <= x]
        out[f"corte_{x*100:g}%"] = f.assign(_k=f["equilibrado"])
    liq = m.assign(liq=m["esperado"] - m["spread"])
    g = liq.groupby("entrada_data")
    out["liquido"] = liq.assign(_k=g["liq"].rank(pct=True) + g["taxa"].rank(pct=True))
    p = m[m["esperado"] > m["spread"]]
    out["paga"] = p.assign(_k=p["equilibrado"])
    return out


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    trades = recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    corte = pd.Series([t["entrada_data"] for t in trades]).quantile(0.6)
    treino = pd.DataFrame([t for t in trades if t["entrada_data"] < corte])
    media = treino.groupby("setup")["ret"].mean().to_dict()
    ganha = treino.groupby("setup")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    m = p3j.medidas(trades, media, ganha)
    teste = m[m["entrada_data"] >= corte].copy()
    meio = teste["entrada_data"].median()
    todos = pd.DataFrame(trades)
    print(f"Trades: {len(m)}; teste desde {corte:%Y-%m-%d}: {len(teste)} (metades a partir de {meio:%Y-%m-%d})")
    print(f"Spread estimado (todos): mediana {todos['spread'].median()*100:.2f}% | quartis "
          f"{todos['spread'].quantile(.25)*100:.2f}%-{todos['spread'].quantile(.75)*100:.2f}% | "
          f"90% {todos['spread'].quantile(.9)*100:.2f}%")
    print(f"Correlação (postos) com o preço {todos['spread'].corr(todos['preco'], method='spearman'):+.2f}, "
          f"com os dólares negociados {todos['spread'].corr(todos['dolares'], method='spearman'):+.2f}, "
          f"com o ATR {todos['spread'].corr(todos['atr_pct'], method='spearman'):+.2f}")

    print("\n### 1. Retorno por quinto do spread estimado (todos os trades; antes | depois do spread)")
    todos["q"] = pd.qcut(todos["spread"], 5, labels=False)
    meio_t = todos["entrada_data"].median()
    for s, g in [("todos", todos)] + list(todos.groupby("setup")):
        print(f"  {s}:")
        for q, x in g.groupby("q"):
            a, b = x[x["entrada_data"] < meio_t], x[x["entrada_data"] >= meio_t]
            liq = x["ret"] - x["spread"]
            print(f"    Q{q+1} spread {x['spread'].min()*100:.2f}-{x['spread'].max()*100:.2f}% n={len(x):6d} "
                  f"bruto {x['ret'].mean()*100:+.2f}% ({a['ret'].mean()*100:+.2f}|{b['ret'].mean()*100:+.2f}) "
                  f"líquido {liq.mean()*100:+.2f}% ({(a['ret']-a['spread']).mean()*100:+.2f}|"
                  f"{(b['ret']-b['spread']).mean()*100:+.2f}) ganha {(liq > 0).mean()*100:.0f}%")

    print("\n### 2. Carteira de 5 lugares, ordem equilibrada, cada trade paga o seu spread estimado")
    rng = np.random.default_rng(1)
    for nome, parte in (("1.ª metade", teste[teste["entrada_data"] < meio]),
                        ("2.ª metade", teste[teste["entrada_data"] >= meio])):
        print(f"  {nome}:")
        for v, c in variantes(parte).items():
            r = carteira(c, "_k", 5, rng)
            print(f"    {v:12s} total {r['total']*100:+6.1f}% | pior descida {r['descida']*100:+5.1f}% | "
                  f"{r['n']} trades {r['media']*100:+.2f}% | perde {r['perde']*100:.0f}% | "
                  f"sinais {len(c)} spread médio {c['spread'].mean()*100:.2f}%")
        sorte = sorted(carteira(parte, "aleatorio", 5, rng)["total"] * 100 for _ in range(30))
        print(f"    30 ordens ao acaso: {sorte[0]:+.1f}% a {sorte[-1]:+.1f}% (metade {sorte[7]:+.1f} a {sorte[22]:+.1f})")


if __name__ == "__main__":
    main()
