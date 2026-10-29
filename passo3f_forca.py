"""O que deve ditar a força de um sinal? Estudo com 2 anos de histórico (sem olhar para o futuro).

Compara, nos últimos 40% do período, as medidas que hoje ordenam os sinais:
  esperado_acao   retorno esperado do alerta: média da própria ação neste setup, puxada para a
                  média do setup (é o que decide a fasquia de 1% e a ordem em passo2_sinais.py)
  pontuacao       "Confiança" 0-100 do alerta e do bot: 60 x taxa de acerto da ação (encolhida)
                  + 20 x volume + 20 x força de 20 dias (sinais.pontuar)
  media_setup     só o retorno médio do setup (aprendido nos primeiros 60%)
Para cada medida: retorno real por quintil (todos os candidatos) e uma carteira de 5 e 10 lugares
que enche os lugares livres pela ordem dessa medida (sem trocas), mais uma ordem ao acaso.
Uso:  python passo3f_forca.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
import yaml

import passo3e_trocas as t3
from src import indicadores, saidas, sinais

RAIZ, DADOS = t3.RAIZ, t3.DADOS


def recolher(precos: pd.DataFrame, cfg: dict) -> list[dict]:
    """Trades como em passo3e, mais volume e força de 20 dias no dia do sinal."""
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
                ret, dias, _ = r
                out.append({"ticker": ticker, "setup": nome, "entrada_data": d["data"].iat[i],
                            "saida_data": d["data"].iat[i + dias], "ret": ret,
                            "vol_ratio": d["vol_ratio"].iat[i], "ret20": d["ret20"].iat[i]})
    return out


def medidas(trades: list[dict], media: dict, k: int = 10) -> pd.DataFrame:
    """Cada medida calculada só com trades da mesma ação já fechados antes do sinal."""
    passado = defaultdict(list)
    linhas = []
    for t in sorted(trades, key=lambda x: x["entrada_data"]):
        h = [r for s, r in passado[(t["ticker"], t["setup"])] if s < t["entrada_data"]]
        n = len(h)
        taxa = ((np.sum(np.array(h) > 0) if n else 0) + 0.5 * 5) / (n + 5)
        pont = (60 * taxa + 20 * np.clip((np.nan_to_num(t["vol_ratio"], nan=1) - 1) / 2, 0, 1)
                + 20 * np.clip(np.nan_to_num(t["ret20"]) / 0.2 + 0.5, 0, 1))
        linhas.append({**t, "n": n, "esperado_acao": (np.sum(h) + media[t["setup"]] * k) / (n + k),
                       "pontuacao": pont, "media_setup": media[t["setup"]]})
        passado[(t["ticker"], t["setup"])].append((t["saida_data"], t["ret"]))
    return pd.DataFrame(linhas)


def carteira(c: pd.DataFrame, chave: str, lugares: int, rng=None) -> tuple[float, int, float]:
    c = c.assign(_o=rng.random(len(c)) if chave == "aleatorio" else c[chave])
    abertas, rets = {}, []
    for d, g in c.sort_values(["entrada_data", "_o"], ascending=[True, False]).groupby("entrada_data", sort=True):
        for tk in [x for x, s in abertas.items() if s <= d]:
            abertas.pop(tk)
        for r in g.drop_duplicates("ticker").itertuples():
            if len(abertas) >= lugares:
                break
            if r.ticker not in abertas:
                abertas[r.ticker] = r.saida_data
                rets.append(r.ret - t3.CUSTO)
    r = np.array(rets)
    return r.sum() / lugares, len(r), r.mean()


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    trades = recolher(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    corte = pd.Series([t["entrada_data"] for t in trades]).quantile(0.6)
    media = pd.DataFrame([t for t in trades if t["entrada_data"] < corte]).groupby("setup")["ret"].mean().to_dict()
    m = medidas(trades, media)
    teste = m[m["entrada_data"] >= corte]
    print(f"Trades: {len(m)}; teste desde {corte:%Y-%m-%d}: {len(teste)}")
    print("\n### Retorno real por quintil de cada medida (período de teste, antes de custos)")
    for chave in ("esperado_acao", "pontuacao"):
        q = pd.qcut(teste[chave].rank(method="first"), 5, labels=False)
        print(f"{chave}: " + " | ".join(
            f"Q{k + 1} {g[chave].mean() * (100 if chave != 'pontuacao' else 1):.2f}: real {g['ret'].mean()*100:+.2f}% "
            f"ganha {(g['ret'] > 0).mean()*100:.0f}%" for k, g in teste.groupby(q)))
        for s, g in teste.groupby("setup"):
            q = pd.qcut(g[chave].rank(method="first"), 5, labels=False)
            print(f"  só {s}: " + " | ".join(f"Q{k + 1} {h['ret'].mean()*100:+.2f}%" for k, h in g.groupby(q)))
    print("\n  por setup: " + " | ".join(f"{s} {g['ret'].mean()*100:+.2f}% ganha {(g['ret']>0).mean()*100:.0f}% "
                                         f"(n={len(g)})" for s, g in teste.groupby("setup")))
    for fasquia in (0.005, 0.01):
        a = teste[teste["esperado_acao"] >= fasquia]
        print(f"  fasquia {fasquia*100:.1f}% no esperado_acao: {len(a)} sinais, real {a['ret'].mean()*100:+.2f}% "
              f"| abaixo: {len(teste) - len(a)}, real {teste[teste['esperado_acao'] < fasquia]['ret'].mean()*100:+.2f}%")
    print("\n### No mesmo dia: o 1.º e os 3 primeiros de cada medida contra a média desse dia (dias com 5+ sinais)")
    dias = teste.groupby("entrada_data").filter(lambda g: len(g) >= 5)
    for chave in ("esperado_acao", "pontuacao", "media_setup"):
        d1 = dias.assign(rk=dias.groupby("entrada_data")[chave].rank(ascending=False, method="first"),
                         md=dias.groupby("entrada_data")["ret"].transform("mean"))
        for n in (1, 3):
            x = d1[d1["rk"] <= n]
            dif = (x["ret"] - x["md"])
            print(f"  {chave:13s} top {n}: {dif.mean()*100:+.2f} pontos face à média do dia "
                  f"(1.ª metade do teste {dif[x['entrada_data'] < x['entrada_data'].median()].mean()*100:+.2f}, "
                  f"2.ª {dif[x['entrada_data'] >= x['entrada_data'].median()].mean()*100:+.2f}) | {dias['entrada_data'].nunique()} dias")
    print("\n### Carteira sem trocas: enche os lugares livres pela ordem de cada medida (custo 0,3% descontado)")
    rng = np.random.default_rng(1)
    for lugares in (5, 10):
        for chave in ("esperado_acao", "pontuacao", "media_setup", "aleatorio"):
            tot, n, med = carteira(teste, chave, lugares, rng)
            print(f"  {lugares:2d} lugares | {chave:13s} | total {tot*100:+6.1f}% | {n} trades | {med*100:+.2f}%/trade")
        sorte = sorted(carteira(teste, "aleatorio", lugares, rng)[0] * 100 for _ in range(30))
        print(f"  {lugares:2d} lugares | 30 ordens ao acaso: de {sorte[0]:+.1f}% a {sorte[-1]:+.1f}% "
              f"(metade entre {sorte[7]:+.1f}% e {sorte[22]:+.1f}%)")


if __name__ == "__main__":
    main()
