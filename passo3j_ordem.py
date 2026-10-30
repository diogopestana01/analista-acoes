"""Que ordem escolhe os N melhores sinais: maior ganho esperado, ou ganho ajustado ao risco?

Pedido do Pestana: com 5 lugares livres quer as 5 melhores posições, não as que podem dar mais mas
com mais risco de acabar a perder. Compara, nos últimos 40% do período (tabelas dos primeiros 60%):
  esperado        retorno esperado da ação (a ordem de hoje)
  taxa            probabilidade de ganhar (taxa de acerto da ação no setup, puxada para o setup)
  por_risco       esperado / perda num dia mau (2,2 x ATR, src/risco.py)
  sem_alto        esperado, mas os de risco alto (dia mau > 9%) só entram se não houver outros
  esperado_taxa   esperado x taxa (ganho que se espera nas vezes que corre bem)
  aleatorio       ordem ao acaso
Mede, nos 3 primeiros de cada dia e numa carteira de 5 lugares: retorno, % que perde, perda média
dos 10% piores, e a pior descida da carteira (máximo de quanto chegou a cair desde o topo).
Notícias e redes não entram: não há histórico de 2 anos delas (só o registo em papel desde 28/10).
Uso:  python passo3j_ordem.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
import yaml

import passo3e_trocas as t3
from src import indicadores, risco, saidas, sinais

RAIZ, DADOS = t3.RAIZ, t3.DADOS
ORDENS = ["esperado", "taxa", "por_risco", "sem_alto", "esperado_taxa"]


def recolher(precos: pd.DataFrame, cfg: dict) -> list[dict]:
    setups = [s for s in cfg["setups"] if sinais.SETUPS[s].direcao == "compra"]
    out = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
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
                            "saida_data": d["data"].iat[i + r[1]], "ret": r[0], "atr_pct": d["atr_pct"].iat[i]})
    return out


def medidas(trades: list[dict], media: dict, ganha: dict, k: int = 10) -> pd.DataFrame:
    """Só com trades da mesma ação já fechados antes do sinal."""
    passado, linhas = defaultdict(list), []
    for t in sorted(trades, key=lambda x: x["entrada_data"]):
        h = np.array([r for s, r in passado[(t["ticker"], t["setup"])] if s < t["entrada_data"]])
        n = len(h)
        esp = (h.sum() + media[t["setup"]] * k) / (n + k)
        taxa = ((h > 0).sum() + ganha[t["setup"]] * k) / (n + k)
        dia_mau = risco.perda_dia_mau(t["atr_pct"]) or 0.05
        linhas.append({**t, "esperado": esp, "taxa": taxa, "por_risco": esp / dia_mau,
                       "sem_alto": esp - (1.0 if dia_mau >= 0.09 else 0.0), "esperado_taxa": esp * taxa,
                       "dia_mau": dia_mau})
        passado[(t["ticker"], t["setup"])].append((t["saida_data"], t["ret"]))
    return pd.DataFrame(linhas)


def pior10(r: pd.Series | np.ndarray) -> float:
    r = np.sort(np.asarray(r))
    return r[: max(len(r) // 10, 1)].mean()


def carteira(c: pd.DataFrame, chave: str, lugares: int, rng=None) -> dict:
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
                trades.append((r.saida_data, r.ret - t3.CUSTO))
    t = pd.DataFrame(trades, columns=["saida", "ret"]).sort_values("saida")
    curva = 1 + (t["ret"] / lugares).cumsum()   # cada posição = 1/lugares do capital
    descida = (curva / curva.cummax() - 1).min()
    return {"total": curva.iat[-1] - 1, "n": len(t), "media": t["ret"].mean(), "perde": (t["ret"] < 0).mean(),
            "pior10": pior10(t["ret"]), "descida": descida}


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    trades = recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    corte = pd.Series([t["entrada_data"] for t in trades]).quantile(0.6)
    treino = pd.DataFrame([t for t in trades if t["entrada_data"] < corte])
    media = treino.groupby("setup")["ret"].mean().to_dict()
    ganha = treino.groupby("setup")["ret"].apply(lambda r: (r > 0).mean()).to_dict()
    m = medidas(trades, media, ganha)
    teste = m[m["entrada_data"] >= corte]
    meio = teste["entrada_data"].median()
    print(f"Trades: {len(m)}; teste desde {corte:%Y-%m-%d}: {len(teste)} (metades a partir de {meio:%Y-%m-%d})")

    print("\n### Os 3 primeiros de cada dia (dias com 5+ sinais), antes de custos")
    dias = teste.groupby("entrada_data").filter(lambda g: len(g) >= 5)
    print(f"  média do dia: ret {dias['ret'].mean()*100:+.2f}% perde {(dias['ret'] < 0).mean()*100:.0f}% "
          f"pior10 {pior10(dias['ret'])*100:+.1f}% | {dias['entrada_data'].nunique()} dias")
    for chave in ORDENS:
        x = dias.assign(rk=dias.groupby("entrada_data")[chave].rank(ascending=False, method="first"))
        x = x[x["rk"] <= 3]
        a, b = x[x["entrada_data"] < meio], x[x["entrada_data"] >= meio]
        print(f"  {chave:13s} ret {x['ret'].mean()*100:+.2f}% ({a['ret'].mean()*100:+.2f}|{b['ret'].mean()*100:+.2f}) "
              f"perde {(x['ret'] < 0).mean()*100:.0f}% ({(a['ret'] < 0).mean()*100:.0f}|{(b['ret'] < 0).mean()*100:.0f}) "
              f"pior10 {pior10(x['ret'])*100:+.1f}% ({pior10(a['ret'])*100:+.1f}|{pior10(b['ret'])*100:+.1f}) "
              f"ret/risco {x['ret'].mean() / x['ret'].std():.3f}")

    print("\n### Carteira de 5 lugares sem trocas (custo 0,3% descontado), cada metade do teste")
    rng = np.random.default_rng(1)
    for nome, parte in (("1.ª metade", teste[teste["entrada_data"] < meio]), ("2.ª metade", teste[teste["entrada_data"] >= meio])):
        print(f"  {nome}:")
        for chave in ORDENS + ["aleatorio"]:
            r = carteira(parte, chave, 5, rng)
            print(f"    {chave:13s} total {r['total']*100:+6.1f}% | pior descida {r['descida']*100:+5.1f}% | "
                  f"{r['n']} trades {r['media']*100:+.2f}% | perde {r['perde']*100:.0f}% | pior10 {r['pior10']*100:+.1f}%")
        sorte = sorted(carteira(parte, "aleatorio", 5, rng)["total"] * 100 for _ in range(30))
        desc = sorted(carteira(parte, "aleatorio", 5, rng)["descida"] * 100 for _ in range(30))
        print(f"    30 ordens ao acaso: total {sorte[0]:+.1f}% a {sorte[-1]:+.1f}% (metade {sorte[7]:+.1f} a {sorte[22]:+.1f}); "
              f"pior descida {desc[0]:+.1f}% a {desc[-1]:+.1f}%")


if __name__ == "__main__":
    main()
