"""Backtest dos rompimentos com preços de hora a hora (últimos ~2 anos do Yahoo).

O backtest diário assume compra no fecho, mas os alertas saem durante o dia. Aqui
compara-se em que momento do dia vale a pena entrar num rompimento do máximo de 20 dias:

  imediato     primeira hora que fecha acima do máximo (o que os alertas fazem hoje)
  confirma_1h  só se a hora seguinte também fechar acima
  folga        só se fechar acima do máximo + 0,5 x ATR
  depois_2h    só a partir da 3.ª hora da sessão
  fecho        compra no fecho do dia (referência do backtest diário)

Todas exigem tendência de subida e volume projetado >= 1,5x a média. Saída igual
para todas: objetivo 3 x ATR, stop 1,5 x ATR, até 5 dias, avaliada hora a hora.
Uso:  python passo3b_intradia.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src import indicadores

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"
VARIANTES = ["imediato", "confirma_1h", "folga", "depois_2h", "fecho"]
MULT_OBJ, MULT_STOP, MAX_DIAS, VOL_MIN = 3.0, 1.5, 5, 1.5


def eventos(precos: pd.DataFrame) -> pd.DataFrame:
    """Dias em que o máximo do dia passou o máximo dos 20 dias anteriores, em tendência de subida."""
    out = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        ontem_ok = (d["close"].shift() > d["sma50"].shift()) & (d["sma50"].shift() > d["sma200"].shift())
        m = (d["high"] > d["max20"]) & ontem_ok & d["atr14"].shift().notna()
        for i in np.flatnonzero(m.to_numpy()):
            out.append({"ticker": ticker, "dia": d["data"].iat[i].normalize(), "max20": d["max20"].iat[i],
                        "atr": d["atr14"].iat[i - 1], "vol_media": d["volume"].iloc[i - 20:i].mean()})
    return pd.DataFrame(out)


def horarios(tickers: list[str], lote: int = 60) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out = {}
    for k in range(0, len(tickers), lote):
        grupo = tickers[k:k + lote]
        try:
            b = yf.download(grupo, period="730d", interval="1h", group_by="ticker", progress=False,
                            auto_adjust=False, threads=True)
        except Exception as e:
            logging.warning("Lote %d falhou: %s", k, e)
            continue
        for t in grupo:
            try:
                h = (b[t] if isinstance(b.columns, pd.MultiIndex) else b).dropna(subset=["Close"])
            except KeyError:
                continue
            if h.empty:
                continue
            h = h.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
            h.index = pd.to_datetime(h.index, utc=True)
            out[t] = h
        logging.info("Horários: %d/%d", min(k + lote, len(tickers)), len(tickers))
    return out


def entrada(dia: pd.DataFrame, e: dict, variante: str) -> int | None:
    """Índice (na tabela horária do dia) da barra em cujo fecho se compra."""
    n = len(dia)
    c = dia["close"].to_numpy()
    vol_proj = dia["volume"].cumsum().to_numpy() / (np.arange(1, n + 1) / n)
    vol_ok = vol_proj >= VOL_MIN * e["vol_media"]
    acima = c > e["max20"]
    if variante == "fecho":
        return n - 1 if acima[-1] and dia["volume"].sum() >= VOL_MIN * e["vol_media"] else None
    for k in range(n):
        if not (acima[k] and vol_ok[k]):
            continue
        if variante == "imediato":
            return k
        if variante == "confirma_1h":
            return k + 1 if k + 1 < n and acima[k + 1] else None
        if variante == "folga" and c[k] > e["max20"] + 0.5 * e["atr"]:
            return k
        if variante == "depois_2h" and k >= 2:
            return k
    return None


def simular(h: pd.DataFrame, dias: list, pos_dia: int, k: int, atr: float) -> tuple[float, str]:
    """Compra no fecho da barra k do dia pos_dia; percorre as barras seguintes até 5 dias."""
    barras = h[h["dia"].isin(dias[pos_dia:pos_dia + MAX_DIAS + 1])]
    inicio = np.flatnonzero((barras["dia"] == dias[pos_dia]).to_numpy())[k]
    preco = barras["close"].iat[inicio]
    obj, stop = preco + MULT_OBJ * atr, preco - MULT_STOP * atr
    for j in range(inicio + 1, len(barras)):
        if barras["low"].iat[j] <= stop:
            return min(stop, barras["open"].iat[j]) / preco - 1, "stop"
        if barras["high"].iat[j] >= obj:
            return max(obj, barras["open"].iat[j]) / preco - 1, "objetivo"
    return barras["close"].iat[-1] / preco - 1, "prazo"


def main() -> None:
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    ev = eventos(precos[precos["ticker"].isin(filtrado["ticker"])])
    logging.info("Dias de rompimento: %d em %d ações", len(ev), ev["ticker"].nunique())
    hs = horarios(sorted(ev["ticker"].unique()))
    linhas = []
    for t, g in ev.groupby("ticker"):
        if t not in hs:
            continue
        h = hs[t].copy()
        h["dia"] = h.index.tz_convert(None).normalize()
        dias = sorted(h["dia"].unique())
        pos = {d: i for i, d in enumerate(dias)}
        for e in g.to_dict("records"):
            if e["dia"] not in pos or pos[e["dia"]] + MAX_DIAS >= len(dias):
                continue
            dia = h[h["dia"] == e["dia"]]
            for v in VARIANTES:
                k = entrada(dia, e, v)
                if k is not None:
                    ret, motivo = simular(h, dias, pos[e["dia"]], k, e["atr"])
                    linhas.append({"ticker": t, "dia": e["dia"], "variante": v, "hora": k,
                                   "retorno": ret, "motivo": motivo})
    t = pd.DataFrame(linhas)
    t.to_csv(DADOS / "backtest_intradia.csv", index=False)
    if t.empty:
        print("Sem trades (dados horários indisponíveis?)")
        return
    corte = t["dia"].sort_values().iloc[len(t) // 2]
    print("\n### Rompimentos: momento de entrada (retorno médio %, 1.ª | 2.ª metade, % a ganhar, % stop)")
    for v, g in t.groupby("variante"):
        a, b = g[g["dia"] < corte]["retorno"].mean(), g[g["dia"] >= corte]["retorno"].mean()
        print(f"  {v:12s} {g['retorno'].mean()*100:+.2f}% (n={len(g)}) | {a*100:+.2f}% | {b*100:+.2f}% | "
              f"ganha {(g['retorno'] > 0).mean()*100:.0f}% | stop {(g['motivo'] == 'stop').mean()*100:.0f}% "
              f"| hora média {g['hora'].mean():.1f}")


if __name__ == "__main__":
    main()
