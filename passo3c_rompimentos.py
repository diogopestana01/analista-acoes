"""Estudo dos rompimentos com vários tempos gráficos e setups novos de compra.

Pergunta: que rompimentos valem a pena? E há setups novos que dêem mais sinais bons?
Para cada candidato mede-se o retorno com a regra de saída do rompimento (atr_3_15) e
com sma5, na 1.ª e na 2.ª metade do período. Um filtro só interessa se melhorar as DUAS.

Características testadas (todas conhecidas no fecho do dia do sinal):
  semanal_sobe    fecho semanal acima da média de 10 semanas e média a subir
  rsi_semanal     RSI14 do gráfico semanal
  compressao      amplitude dos 10 dias antes / ATR (baixo = base apertada)
  esticado_atr    quanto o fecho passou o máximo de 20 dias, em ATR
  pos_fecho       onde fechou na vela do dia (1 = no máximo)
  forca_rel       percentil do retorno de 60 dias entre as ações da mesma região nesse dia
  mercado_sobe    % de ações da região acima da média de 50 dias >= 50%
  primeiro        primeiro rompimento em 20 dias (não repetido)
  max52           fecho acima do máximo de 52 semanas
Setups novos:
  max52_volume    fecho no máximo de 52 semanas com volume >= 1,3x, tendência de subida
  nr7_rompe       depois do dia mais estreito de 7 (NR7), fecha acima do máximo desse dia, em tendência
  semana_forte    subiu >= 8% em 5 dias, fecho no topo do dia, volume >= 1,5x, tendência
  rs_pullback     top 10% de força relativa a tocar a média de 20 dias e a fechar acima dela
Uso:  python passo3c_rompimentos.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src import indicadores, saidas, sinais

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"
SAIDAS = ["atr_3_15", "sma5"]


def caracteristicas(df: pd.DataFrame) -> pd.DataFrame:
    d = indicadores.calcular(df)   # já traz semanal_sobe e rsi_semanal
    d["compressao"] = (d["high"].rolling(10).max() - d["low"].rolling(10).min()).shift() / d["atr14"].shift()
    d["esticado_atr"] = (d["close"] - d["max20"]) / d["atr14"]
    d["ret60"] = d["close"].pct_change(60)
    d["max252"] = d["high"].rolling(252, min_periods=240).max().shift()
    d["max52"] = (d["close"] > d["max252"]).astype(float).where(d["max252"].notna())
    rompe = (d["close"] > d["max20"])
    d["primeiro"] = (rompe & ~rompe.shift(fill_value=False).rolling(20, min_periods=1).max().astype(bool)) \
        .astype(float)
    return d


def candidatos(d: pd.DataFrame) -> pd.DataFrame:
    """Setups a estudar: o rompimento atual e os novos (a força relativa entra depois, entre ações)."""
    tendencia = (d["close"] > d["sma50"]) & (d["sma50"] > d["sma200"])
    s = pd.DataFrame(index=d.index)
    s["rompimento"] = sinais.detetar(d)["rompimento"]
    s["max52_volume"] = (d["max52"] == 1) & (d["vol_ratio"] >= 1.3) & tendencia
    amp = d["high"] - d["low"]
    nr7 = amp <= amp.rolling(7).min()
    s["nr7_rompe"] = nr7.shift(fill_value=False) & (d["close"] > d["high"].shift()) & tendencia
    s["semana_forte"] = (d["ret5"] >= 0.08) & (d["pos_fecho"] >= 0.8) & (d["vol_ratio"] >= 1.5) & tendencia
    s["toca_sma20"] = (d["low"] <= d["sma20"]) & (d["close"] > d["sma20"]) & tendencia
    return s.fillna(False)


def recolher(precos: pd.DataFrame) -> pd.DataFrame:
    linhas, acima = [], []
    cols = ["vol_ratio", "rsi14", "atr_pct", "pos_fecho", "semanal_sobe", "rsi_semanal", "compressao",
            "esticado_atr", "ret60", "max52", "primeiro", "ret5", "ret20"]
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 260:
            continue
        d = caracteristicas(df)
        regiao = sinais.regiao(ticker)
        acima.append(pd.DataFrame({"ticker": ticker, "data": d["data"], "regiao": regiao, "ret60": d["ret60"],
                                   "acima": (d["close"] > d["sma50"]).where(d["sma50"].notna())}))
        flags = candidatos(d)
        for i in np.flatnonzero(flags.any(axis=1).to_numpy()):
            res = {r: saidas.simular(d, i, saidas.REGRAS[r]) for r in SAIDAS}
            if any(v is None for v in res.values()):
                continue
            base = {"ticker": ticker, "data": d["data"].iat[i], "regiao": regiao,
                    **{c: d[c].iat[i] for c in cols},
                    **{f"ret_{r}": v[0] for r, v in res.items()},
                    **{f"motivo_{r}": v[2] for r, v in res.items()}}
            for setup in flags.columns[flags.iloc[i].to_numpy()]:
                linhas.append({**base, "setup": setup})
    t = pd.DataFrame(linhas)
    a = pd.concat(acima).dropna(subset=["acima"])
    mercado = a.groupby(["data", "regiao"])["acima"].mean().rename("amplitude").reset_index()
    a["forca_rel"] = a.groupby(["data", "regiao"])["ret60"].rank(pct=True)
    t = t.merge(mercado, on=["data", "regiao"], how="left")
    t = t.merge(a[["ticker", "data", "forca_rel"]], on=["ticker", "data"], how="left")
    t["mercado_sobe"] = (t["amplitude"] >= 0.5).astype(float)
    # rs_pullback: toca na média de 20 dias mas só no top 10% de força relativa.
    rs = t[(t["setup"] == "toca_sma20") & (t["forca_rel"] >= 0.9)].assign(setup="rs_pullback")
    return pd.concat([t[t["setup"] != "toca_sma20"], rs], ignore_index=True)


def linha(g: pd.DataFrame, corte, r: str) -> str:
    a, b = g[g["data"] < corte][f"ret_{r}"], g[g["data"] >= corte][f"ret_{r}"]
    return (f"{g[f'ret_{r}'].mean()*100:+.2f}% (n={len(g)}) | {a.mean()*100:+.2f}% (n={len(a)}) | "
            f"{b.mean()*100:+.2f}% (n={len(b)}) | ganha {(g[f'ret_{r}'] > 0).mean()*100:.0f}%")


FILTROS = {
    "semanal_sobe": lambda t: t["semanal_sobe"] == 1,
    "semanal_nao_sobe": lambda t: t["semanal_sobe"] == 0,
    "rsi_semanal<70": lambda t: t["rsi_semanal"] < 70,
    "rsi_semanal>=70": lambda t: t["rsi_semanal"] >= 70,
    "compressao<4": lambda t: t["compressao"] < 4,
    "compressao>=6": lambda t: t["compressao"] >= 6,
    "esticado<0.5atr": lambda t: t["esticado_atr"] < 0.5,
    "esticado>=1atr": lambda t: t["esticado_atr"] >= 1,
    "pos_fecho>=0.7": lambda t: t["pos_fecho"] >= 0.7,
    "pos_fecho<0.5": lambda t: t["pos_fecho"] < 0.5,
    "forca_rel>=0.8": lambda t: t["forca_rel"] >= 0.8,
    "forca_rel<0.5": lambda t: t["forca_rel"] < 0.5,
    "mercado_sobe": lambda t: t["mercado_sobe"] == 1,
    "mercado_desce": lambda t: t["mercado_sobe"] == 0,
    "primeiro": lambda t: t["primeiro"] == 1,
    "repetido": lambda t: t["primeiro"] == 0,
    "max52": lambda t: t["max52"] == 1,
    "vol>=2.5": lambda t: t["vol_ratio"] >= 2.5,
    "rsi14<70": lambda t: t["rsi14"] < 70,
    "rsi14>=80": lambda t: t["rsi14"] >= 80,
    "atr_pct<3%": lambda t: t["atr_pct"] < 0.03,
    "atr_pct>=5%": lambda t: t["atr_pct"] >= 0.05,
    "eua": lambda t: t["regiao"] == "eua",
    "europa": lambda t: t["regiao"] == "europa",
}
CONTINUAS = ["compressao", "esticado_atr", "pos_fecho", "forca_rel", "rsi_semanal", "vol_ratio", "rsi14",
             "atr_pct", "amplitude", "ret20"]


def relatorio(t: pd.DataFrame) -> str:
    corte = t["data"].sort_values().iloc[len(t) // 2]
    out = [f"Corte entre metades: {corte:%Y-%m-%d}",
           "\n### Setups (retorno médio | 1.ª metade | 2.ª metade | % a ganhar), por regra de saída"]
    for s, g in t.groupby("setup"):
        for r in SAIDAS:
            out.append(f"  {s:13s} {r:9s} {linha(g, corte, r)}")
    for s, g in t.groupby("setup"):
        r = "atr_3_15" if s != "rs_pullback" else "sma5"
        out.append(f"\n### {s}: filtros com saída {r} (só interessam os que melhoram as duas metades)")
        out.append(f"  {'(todos)':18s} {linha(g, corte, r)}")
        for nome, f in FILTROS.items():
            h = g[f(g)]
            if len(h) >= 100:
                out.append(f"  {nome:18s} {linha(h, corte, r)}")
        for c in CONTINUAS:
            x = g.dropna(subset=[c])
            if x[c].nunique() < 5 or len(x) < 250:
                continue
            q = pd.qcut(x[c], 5, duplicates="drop")
            out.append(f"  {c} por quintil: " + " | ".join(
                f"{iv.left:.3g}..{iv.right:.3g}: {h[h['data'] < corte][f'ret_{r}'].mean()*100:+.2f}/"
                f"{h[h['data'] >= corte][f'ret_{r}'].mean()*100:+.2f}%"
                for iv, h in x.groupby(q, observed=True)))
    out.append(combinacoes(t[t["setup"] == "rompimento"], corte))
    return "\n".join(out)


def combinacoes(g: pd.DataFrame, corte, n_min: int = 300) -> str:
    """Pares de filtros escolhidos na 1.ª metade e confirmados na 2.ª."""
    res = []
    nomes = list(FILTROS)
    treino = g["data"] < corte
    for i, a in enumerate(nomes):
        for b in nomes[i:]:
            m = FILTROS[a](g) & FILTROS[b](g)
            if (m & treino).sum() >= n_min and (m & ~treino).sum() >= n_min // 2:
                res.append((g[m & treino]["ret_atr_3_15"].mean(), g[m & ~treino]["ret_atr_3_15"].mean(),
                            (m & treino).sum(), (m & ~treino).sum(), a if a == b else f"{a} & {b}"))
    res.sort(reverse=True)
    return ("\n### rompimento: melhores pares de filtros na 1.ª metade e a 2.ª metade (atr_3_15)\n"
            + "\n".join(f"  {a*100:+.2f}% | {b*100:+.2f}% | n {n1} | {n2} | {nome}"
                        for a, b, n1, n2, nome in res[:20]))


def main() -> None:
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    t = recolher(precos[precos["ticker"].isin(filtrado["ticker"])])
    t.to_csv(DADOS / "estudo_rompimentos.csv", index=False)
    print(relatorio(t))


if __name__ == "__main__":
    main()
