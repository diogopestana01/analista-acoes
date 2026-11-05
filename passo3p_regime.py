"""Regime do mercado: os sinais rendem pior quando o mercado todo está a cair?

Pedido do Pestana (5/11): o registo em papel caiu muito desde 29/10 (venda forte da tecnologia) e o
modelo não olha para o mercado. Testa as abordagens mais usadas (ver a pesquisa no pedido):
  tendência do índice   SPY/QQQ acima das médias de 20, 50 e 200 dias (Faber, Connors, Arthur Hill)
  queda recente         SPY nos últimos 1 e 5 dias
  medo                  VIX (nível e se está a subir face à sua média de 20 dias)
  amplitude (breadth)   % das ações dos EUA do universo acima da média de 50 dias, e se está a cair
  força relativa        a ação subiu mais do que o SPY nos últimos 20 dias
Para cada filtro (só entra se a regra passar): retorno médio por trade depois de 0,3% de spread, %
que ganha, nas duas metades do período, e o que rendem os trades que o filtro TIRAVA (se esses forem
bons, o filtro só tira dinheiro). Depois, carteira de 5 lugares (ordem equilibrada, como o bot) em
cada metade: resultado e pior descida, com o filtro a cortar entradas ou a pôr metade do tamanho.
Mostra no fim o regime dos últimos 15 dias, para ver o que os filtros teriam feito ao vivo.
Só ações dos EUA (a carteira do bot é a conta USD). Só para medir: nada entra no modelo sem aprovação.
Uso:  python passo3p_regime.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import yaml

import passo3j_ordem as p3j
from src import indicadores, saidas, sinais

RAIZ, DADOS, CUSTO = p3j.RAIZ, p3j.DADOS, p3j.t3.CUSTO
log = logging.getLogger(__name__)


def _sem_fuso(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s)
    return (s.dt.tz_localize(None) if s.dt.tz is not None else s).dt.normalize()


def recolher(precos: pd.DataFrame, cfg: dict) -> tuple[list[dict], pd.DataFrame]:
    """Trades de compra (mesmas regras de p3j.recolher) com ret20 da ação, e a amplitude por dia."""
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    out, acima = [], []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        acima.append(pd.DataFrame({"data": d["data"], "a50": (d["close"] > d["sma50"]).where(d["sma50"].notna()),
                                   "a20": (d["close"] > d["sma20"]).where(d["sma20"].notna())}))
        flags = sinais.detetar(d)
        for nome in setups:
            regra = saidas.regra_do_setup(nome, cfg)
            filtros = cfg.get("qualidade", {}).get(nome, {}).get("filtros")
            for i in np.flatnonzero(flags[nome].to_numpy()):
                if not sinais.passa_filtros(d.iloc[i], filtros):
                    continue
                r = saidas.simular(d, i, regra)
                if r is None:
                    continue
                out.append({"ticker": ticker, "setup": nome, "entrada_data": d["data"].iat[i],
                            "saida_data": d["data"].iat[i + r[1]], "ret": r[0], "atr_pct": d["atr_pct"].iat[i],
                            "ret20": d["ret20"].iat[i]})
    a = pd.concat(acima).dropna().groupby("data")[["a50", "a20"]].mean()
    return out, a


def indices(inicio: pd.Timestamp) -> pd.DataFrame:
    """SPY, QQQ e VIX do Yahoo (funciona no GitHub). Sem dados, devolve vazio e os filtros saltam."""
    try:
        import yfinance as yf
        bruto = yf.download(["SPY", "QQQ", "^VIX"], start=inicio - pd.Timedelta(days=320), interval="1d",
                            auto_adjust=True, progress=False)["Close"]
    except Exception as e:  # noqa: BLE001
        log.warning("Índices falharam: %s", e)
        return pd.DataFrame()
    bruto.index = pd.to_datetime(bruto.index).tz_localize(None).normalize()
    m = pd.DataFrame(index=bruto.index)
    for t, n in (("SPY", "spy"), ("QQQ", "qqq")):
        c = bruto[t]
        for k in (20, 50, 200):
            m[f"{n}_sma{k}"] = c > c.rolling(k).mean()
        m[f"{n}_ret1"], m[f"{n}_ret5"], m[f"{n}_ret20"] = c.pct_change(), c.pct_change(5), c.pct_change(20)
    v = bruto["^VIX"]
    m["vix"], m["vix_sobe"] = v, v > 1.1 * v.rolling(20).mean()
    return m


def regras(t: pd.DataFrame) -> dict[str, pd.Series]:
    r = {"todos": pd.Series(True, index=t.index)}
    if "spy_sma50" in t:
        r |= {"SPY > média 200d": t["spy_sma200"], "SPY > média 50d": t["spy_sma50"],
              "SPY > média 20d": t["spy_sma20"], "QQQ > média 50d": t["qqq_sma50"],
              "SPY 5d > -2%": t["spy_ret5"] > -0.02, "SPY no dia > -1%": t["spy_ret1"] > -0.01,
              "VIX < 25": t["vix"] < 25, "VIX < 20": t["vix"] < 20, "VIX não a subir": ~t["vix_sobe"],
              "força relativa > 0": t["ret20"] > t["spy_ret20"],
              "semáforo (SPY>50d e VIX não sobe)": t["spy_sma50"] & ~t["vix_sobe"],
              "semáforo + SPY 5d > -2%": t["spy_sma50"] & ~t["vix_sobe"] & (t["spy_ret5"] > -0.02)}
    r |= {"amplitude >= 40%": t["a50"] >= 0.4, "amplitude >= 50%": t["a50"] >= 0.5,
          "amplitude não a cair": t["a50_var5"] > -0.10, "amplitude 20d >= 40%": t["a20"] >= 0.4}
    return {k: v.fillna(False).astype(bool) for k, v in r.items()}


def carteira(c: pd.DataFrame, lugares: int = 5) -> dict:
    """Como p3j.carteira (ordem equilibrada, sem trocas) mas com o peso de cada trade na coluna 'peso'."""
    abertas, trades = {}, []
    for d, g in c.sort_values(["entrada_data", "equilibrado"], ascending=[True, False]).groupby("entrada_data"):
        for tk in [x for x, s in abertas.items() if s <= d]:
            abertas.pop(tk)
        for r in g.drop_duplicates("ticker").itertuples():
            if len(abertas) >= lugares:
                break
            if r.ticker not in abertas and r.peso > 0:
                abertas[r.ticker] = r.saida_data
                trades.append((r.saida_data, r.ret - CUSTO, r.peso))
    if not trades:
        return {"total": 0.0, "descida": 0.0, "n": 0}
    x = pd.DataFrame(trades, columns=["saida", "ret", "peso"]).sort_values("saida")
    curva = 1 + (x["ret"] * x["peso"] / lugares).cumsum()
    return {"total": curva.iat[-1] - 1, "descida": (curva / curva.cummax() - 1).min(), "n": len(x)}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    eua = filtrado["ticker"][~filtrado["ticker"].str.contains(".", regex=False)]
    precos = precos[precos["ticker"].isin(eua)]
    precos["data"] = _sem_fuso(precos["data"])
    trades, amp = recolher(precos, cfg)
    amp["a50_var5"] = amp["a50"] - amp["a50"].shift(5)
    t = pd.DataFrame(trades)
    m = indices(t["entrada_data"].min())
    mercado = amp.join(m, how="left") if not m.empty else amp
    t = t.join(mercado, on="entrada_data")
    t["liq"] = t["ret"] - CUSTO
    meio = t["entrada_data"].median()
    print(f"Trades EUA: {len(t)} de {t['entrada_data'].min():%Y-%m-%d} a {t['entrada_data'].max():%Y-%m-%d}; "
          f"metades a partir de {meio:%Y-%m-%d}; índices {'OK' if not m.empty else 'SEM DADOS (só amplitude)'}")

    filtros = regras(t)
    for s, g in [("todos os setups", t)] + list(t.groupby("setup")):
        print(f"\n### {s} (líquido de 0,3%; 1.ª|2.ª metade)  -> e os que o filtro tirava")
        for nome, ok in filtros.items():
            ok = ok.loc[g.index]
            x, fora = g[ok], g[~ok]
            a, b = x[x["entrada_data"] < meio], x[x["entrada_data"] >= meio]
            fa, fb = fora[fora["entrada_data"] < meio], fora[fora["entrada_data"] >= meio]
            linha = (f"  {nome:36s} fica {len(x) / max(len(g), 1) * 100:3.0f}% {x['liq'].mean()*100:+.2f}% "
                     f"({a['liq'].mean()*100:+.2f}|{b['liq'].mean()*100:+.2f}) ganha {(x['liq'] > 0).mean()*100:.0f}%")
            if len(fora):
                linha += (f"  | tirados n={len(fora)} {fora['liq'].mean()*100:+.2f}% "
                          f"({fa['liq'].mean()*100:+.2f}|{fb['liq'].mean()*100:+.2f}) ganha {(fora['liq'] > 0).mean()*100:.0f}%")
            print(linha)

    # Ordem equilibrada como no bot: tabelas do esperado/taxa com os primeiros 60%, carteira no resto.
    corte = t["entrada_data"].quantile(0.6)
    treino = t[t["entrada_data"] < corte]
    media = treino.groupby("setup")["ret"].mean().to_dict()
    ganha = treino.groupby("setup")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    eq = p3j.medidas(t.to_dict("records"), media, ganha)
    eq.index = t.sort_values("entrada_data", kind="stable").index   # medidas ordena por data
    t["equilibrado"] = eq["equilibrado"].reindex(t.index)
    t["equilibrado"] = t.groupby("entrada_data")["equilibrado"].transform(lambda x: x.fillna(x.mean()))
    print("\n### Carteira de 5 lugares (ordem equilibrada, 0,3% de custo), período todo e cada metade")
    partes = (("tudo", t), ("1.ª", t[t["entrada_data"] < meio]), ("2.ª", t[t["entrada_data"] >= meio]))
    for nome, ok in filtros.items():
        txt = []
        for modo, peso in (("corta", ok.astype(float)), ("metade", ok.astype(float) * 0.5 + 0.5)):
            if nome == "todos" and modo == "metade":
                continue
            rs = [carteira(p.assign(peso=peso.loc[p.index])) for _, p in partes]
            txt.append(f"{modo}: " + " ".join(f"{pn} {r['total']*100:+6.1f}% (desc {r['descida']*100:+5.1f}%)"
                                               for (pn, _), r in zip(partes, rs)))
        print(f"  {nome:36s} " + " || ".join(txt))

    print("\n### Regime dos últimos 15 dias")
    cols = [c for c in ["a50", "a50_var5", "spy_sma20", "spy_sma50", "spy_sma200", "spy_ret1", "spy_ret5",
                        "qqq_sma50", "vix", "vix_sobe"] if c in mercado]
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(mercado[cols].dropna(subset=["a50"]).tail(15).round(3).to_string())


if __name__ == "__main__":
    main()
