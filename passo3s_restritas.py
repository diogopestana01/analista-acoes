"""Com o mercado fraco: parar tudo, ou só deixar passar as compras mais fortes? E vender o índice?

Pedido do Pestana (6/11): o bot não deve ficar parado; com o mercado a cair, compras mais restritas.
As vendas em CFD de ações perdem mesmo com o mercado fraco (passo3r_vendas.py). Aqui, na carteira de
5 lugares (ordem equilibrada, 0,3% de custo), nas duas metades:
  A  atual: amplitude >= 50% + travão (perda > 5% em 7 dias -> 5 dias sem compras)
  B  como A, mas com amplitude baixa passam os pullbacks de capitulação (SPY caiu > 2% em 5 dias)
  C  como B, e o travão também deixa passar esses pullbacks
  D  como B com capitulação a -1,5%; E  como C com -3%
  F  só capitulação (sem filtro de amplitude) + travão que os deixa passar
E a proteção: vender o S&P 500 (SPY, CFD) 5 dias quando a amplitude está abaixo de 50%, líquido de
0,05% de spread e 0,02%/noite.
Só EUA. Só mede.
Uso:  python passo3s_restritas.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yaml

import passo3j_ordem as p3j
import passo3p_regime as p3p

RAIZ, DADOS, CUSTO = p3j.RAIZ, p3j.DADOS, p3j.t3.CUSTO


def carteira(c: pd.DataFrame, lugares: int = 5, perda: float = 0.05, pausa: int = 5) -> dict:
    """Entra se `pode`; com o travão ligado só entram os `isento`."""
    abertas, fechos, parado_ate, travou = {}, [], pd.Timestamp.min, 0
    for d, g in c.sort_values(["entrada_data", "equilibrado"], ascending=[True, False]).groupby("entrada_data"):
        for tk in [x for x, s in abertas.items() if s <= d]:
            abertas.pop(tk)
        if d >= parado_ate and sum(r for s, r in fechos if d - pd.Timedelta(days=7) < s <= d) < -perda:
            parado_ate, travou = d + pd.Timedelta(days=pausa), travou + 1
        travado = d < parado_ate
        for r in g.drop_duplicates("ticker").itertuples():
            if len(abertas) >= lugares:
                break
            if r.ticker in abertas or not r.pode or (travado and not r.isento):
                continue
            abertas[r.ticker] = r.saida_data
            fechos.append((r.saida_data, (r.ret - CUSTO) / lugares))
    if not fechos:
        return {"total": 0.0, "descida": 0.0, "n": 0, "travou": travou}
    x = pd.DataFrame(fechos, columns=["saida", "ret"]).sort_values("saida")
    curva = 1 + x["ret"].cumsum()
    return {"total": curva.iat[-1] - 1, "descida": (curva / curva.cummax() - 1).min(), "n": len(x), "travou": travou}


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    eua = filtrado["ticker"][~filtrado["ticker"].str.contains(".", regex=False)]
    precos = precos[precos["ticker"].isin(eua)].copy()
    precos["data"] = p3p._sem_fuso(precos["data"])
    trades, amp = p3p.recolher(precos, cfg)
    t = pd.DataFrame(trades).join(amp, on="entrada_data")
    m = p3p.indices(t["entrada_data"].min())
    t["spy5"] = t["entrada_data"].map(m["spy_ret5"]) if not m.empty else np.nan
    corte = t["entrada_data"].quantile(0.6)
    treino = t[t["entrada_data"] < corte]
    media = treino.groupby("setup")["ret"].mean().to_dict()
    ganha = treino.groupby("setup")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    eq = p3j.medidas(t.to_dict("records"), media, ganha)
    eq.index = t.sort_values("entrada_data", kind="stable").index
    t["equilibrado"] = eq["equilibrado"].reindex(t.index)
    meio = t["entrada_data"].median()
    forte = t["a50"] >= 0.5
    pb = t["setup"] == "pullback"
    cap = lambda k: pb & (t["spy5"] < -k)  # noqa: E731
    variantes = {
        "sem filtros nem travão": (pd.Series(True, index=t.index), pd.Series(True, index=t.index), 9.0),
        "A atual": (forte, pd.Series(False, index=t.index), 0.05),
        "B fraco: só capitulação 2%": (forte | cap(0.02), pd.Series(False, index=t.index), 0.05),
        "C B + travão deixa passar": (forte | cap(0.02), cap(0.02), 0.05),
        "D B com capitulação 1,5%": (forte | cap(0.015), pd.Series(False, index=t.index), 0.05),
        "E C com capitulação 3%": (forte | cap(0.03), cap(0.03), 0.05),
        "F só capitulação, sem amplitude": (pd.Series(True, index=t.index), cap(0.02), 0.05),
    }
    partes = (("tudo", t), ("1.ª", t[t["entrada_data"] < meio]), ("2.ª", t[t["entrada_data"] >= meio]))
    print(f"Trades EUA {len(t)}; metades a partir de {meio:%Y-%m-%d}; capitulação 2%: {int(cap(0.02).sum())} pullbacks "
          f"({cap(0.02)[~forte].sum()} com amplitude baixa)")
    x = t[cap(0.02) & ~forte]
    a, b = x[x["entrada_data"] < meio], x[x["entrada_data"] >= meio]
    print(f"Pullbacks de capitulação com amplitude baixa: {(x['ret'] - CUSTO).mean()*100:+.2f}% "
          f"({(a['ret'] - CUSTO).mean()*100:+.2f}|{(b['ret'] - CUSTO).mean()*100:+.2f}) ganha {(x['ret'] > CUSTO).mean()*100:.0f}%")
    print("\n### Carteira de 5 lugares")
    for nome, (pode, isento, perda) in variantes.items():
        rs = [carteira(p.assign(pode=pode.loc[p.index], isento=isento.loc[p.index]), perda=perda) for _, p in partes]
        print(f"  {nome:34s} " + " | ".join(f"{pn} {r['total']*100:+6.1f}% (desc {r['descida']*100:+5.1f}%, {r['n']} trades)"
                                           for (pn, _), r in zip(partes, rs)))

    print("\n### Vender o S&P 500 (SPY) 5 dias com a amplitude abaixo de 50%")
    if m.empty:
        print("  sem dados do SPY")
        return
    import yfinance as yf
    spy = yf.download("SPY", start=t["entrada_data"].min() - pd.Timedelta(days=10), auto_adjust=True,
                      progress=False, threads=False)["Close"].squeeze()
    spy.index = pd.to_datetime(spy.index).tz_localize(None).normalize()
    fwd = 1 - spy.shift(-5) / spy - 0.0005 - 5 * 0.0002
    dias = amp.index[amp["a50"] < 0.5]
    v = fwd.reindex(dias).dropna()
    mm = v.index.to_series().median()
    for nome, sel in (("amplitude < 50%", v), ("amplitude < 40%", fwd.reindex(amp.index[amp["a50"] < 0.4]).dropna()),
                      ("todos os dias", fwd.reindex(amp.index).dropna())):
        a, b = sel[sel.index < mm], sel[sel.index >= mm]
        print(f"  {nome:18s} {len(sel)} dias: {sel.mean()*100:+.2f}% por venda ({a.mean()*100:+.2f}|{b.mean()*100:+.2f}) "
              f"ganha {(sel > 0).mean()*100:.0f}%")


if __name__ == "__main__":
    main()
