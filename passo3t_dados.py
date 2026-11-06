"""Dados que faltam ao modelo: reação aos resultados, setor e força contra o mercado. Para compras E vendas.

Pedido do Pestana (6/11): "se calhar é o modelo que não está a analisar bem o mercado, podem faltar coisas".
O modelo só via o gráfico de cada ação. Aqui juntam-se três coisas grátis e testam-se nas duas metades:
  resultados  datas de resultados de cada empresa (calendário da Nasdaq, dia a dia, guardado em
              data/resultados_hist.csv). Dia da reação = o próprio dia (antes da abertura) ou o seguinte
              (depois do fecho). Efeito conhecido: "post-earnings drift", a ação continua na direção da reação.
  setor       setor de cada ação (screener da Nasdaq, data/setores.csv) e um índice por setor (média das
              ações do setor no universo): subida de 20 dias e % de ações do setor acima da média de 50 dias.
  força       subida de 20 dias da ação menos a do setor e menos a do mercado (média do universo).
Testes (entrada ao fecho do dia, 5 dias no máximo, líquido de 0,3% de spread; vendas também 0,02%/noite):
  VENDAS  V1 resultados maus: caiu >= 5% no dia da reação, volume >= 2x, fechou na metade de baixo
          V2 V1 com o mercado fraco (amplitude < 50%)
          V3 fraca no setor fraco: setor no pior quinto dos setores (20 dias), ação 10 pontos pior do que o
             setor, fecho abaixo do mínimo de 20 dias
          V4 V3 com o mercado fraco
  COMPRAS C1 resultados bons: subiu >= 5% no dia da reação, volume >= 2x, fechou na metade de cima
          C2 setups atuais com o mercado fraco, só se o setor da ação tiver >= 50% acima da média de 50 dias
          C3 setups atuais com o mercado fraco, só se a ação estiver mais forte do que o mercado (20 dias)
  Saídas testadas: sem objetivo, stop a 2 ATR, sai ao 5.º dia (fecho_5d com stop) e objetivo 3 ATR/stop 1,5 ATR.
Só EUA. Só mede.
Uso:  python passo3t_dados.py   (depois de passo1_universo.py)
"""
from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd
import requests
import yaml

import passo3p_regime as p3p
from src import indicadores, resultados, saidas, sinais

RAIZ, DADOS = p3p.RAIZ, p3p.DADOS
SPREAD, NOITE = 0.003, 0.0002
log = logging.getLogger(__name__)
SCREENER = "https://api.nasdaq.com/api/screener/stocks"


def historico_resultados(inicio: pd.Timestamp, fim: pd.Timestamp) -> pd.DataFrame:
    """Uma linha por empresa e dia de resultados. Usa a cache e só pede os dias que faltam."""
    f = DADOS / "resultados_hist.csv"
    cache = pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["data", "ticker", "quando"])
    feitos = set(cache["data"].astype(str))
    novos = []
    for d in pd.bdate_range(inicio, fim):
        if f"{d:%Y-%m-%d}" in feitos:
            continue
        try:
            for r in resultados.linhas_do_dia(resultados._pedir(d)):
                novos.append({"data": f"{d:%Y-%m-%d}", "ticker": r["ticker"], "quando": r["quando"]})
            if not novos or novos[-1]["data"] != f"{d:%Y-%m-%d}":
                novos.append({"data": f"{d:%Y-%m-%d}", "ticker": "", "quando": ""})   # dia sem resultados
        except Exception as e:  # noqa: BLE001
            log.warning("Resultados de %s: %s", f"{d:%Y-%m-%d}", e)
        time.sleep(0.2)
    out = pd.concat([cache, pd.DataFrame(novos)], ignore_index=True)
    out.to_csv(f, index=False)
    return out[out["ticker"].astype(str) != ""]


def setores() -> pd.Series:
    f = DADOS / "setores.csv"
    try:
        r = requests.get(SCREENER, params={"tableonly": "true", "limit": 10000, "download": "true"},
                         headers=resultados.CABECALHOS, timeout=60)
        r.raise_for_status()
        linhas = r.json()["data"]["rows"]
        s = pd.DataFrame(linhas)[["symbol", "sector", "industry"]].rename(columns={"symbol": "ticker"})
        s["ticker"] = s["ticker"].str.strip().str.upper().str.replace("/", "-").str.replace(".", "-")
        s.to_csv(f, index=False)
    except Exception as e:  # noqa: BLE001
        log.warning("Screener da Nasdaq falhou (%s); a usar a cache", e)
        s = pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["ticker", "sector", "industry"])
    s = s[s["sector"].astype(str).str.len() > 0]
    return s.set_index("ticker")["sector"]


def simular_venda(d: pd.DataFrame, i: int, regra: saidas.Regra) -> tuple[float, int] | None:
    e, a = d["close"].iat[i], d["atr14"].iat[i]
    stop = e + regra.mult_stop * a
    obj = e - regra.mult_objetivo * a if regra.mult_objetivo else None
    for j in range(i + 1, i + regra.max_dias + 1):
        if j >= len(d):
            return None
        if d["high"].iat[j] >= stop:
            return 1 - max(stop, d["open"].iat[j]) / e, j - i
        if obj is not None and d["low"].iat[j] <= obj:
            return 1 - min(obj, d["open"].iat[j]) / e, j - i
    return 1 - d["close"].iat[i + regra.max_dias] / e, regra.max_dias


SAIDAS = {"5d_stop2": saidas.Regra("5d_stop2", "5 dias, stop 2 ATR", None, 2.0, 5),
          "atr_3_15": saidas.REGRAS["atr_3_15"]}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    eua = filtrado["ticker"][~filtrado["ticker"].str.contains(".", regex=False)]
    precos = precos[precos["ticker"].isin(eua)].copy()
    precos["data"] = p3p._sem_fuso(precos["data"])
    ini, fim = precos["data"].min() + pd.Timedelta(days=300), precos["data"].max()

    res = historico_resultados(ini, fim)
    res["data"] = pd.to_datetime(res["data"])
    print(f"Resultados: {len(res)} anúncios de {res['ticker'].nunique()} empresas")
    sec = setores()
    print(f"Setores: {sec.nunique()} setores para {len(sec)} ações ({precos['ticker'].isin(sec.index).mean()*100:.0f}% das linhas)")

    # Indicadores por ação
    ind = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        d["ticker"] = ticker
        ind.append(d)
    todos = pd.concat(ind, ignore_index=True)
    todos["setor"] = todos["ticker"].map(sec)
    todos["acima50"] = (todos["close"] > todos["sma50"]).where(todos["sma50"].notna())
    mercado20 = todos.groupby("data")["ret20"].mean()
    amp = todos.groupby("data")["acima50"].mean()
    s20 = todos.groupby(["data", "setor"])["ret20"].mean().rename("setor_ret20")
    sa50 = todos.groupby(["data", "setor"])["acima50"].mean().rename("setor_acima50")
    todos = todos.join(s20, on=["data", "setor"]).join(sa50, on=["data", "setor"])
    todos["setor_quinto"] = todos.groupby("data")["setor_ret20"].rank(pct=True, method="dense")
    todos["rs_setor"] = todos["ret20"] - todos["setor_ret20"]
    todos["rs_mercado"] = todos["ret20"] - todos["data"].map(mercado20)
    todos["amp"] = todos["data"].map(amp)

    # Dia da reação aos resultados
    reac = set()
    for r in res.itertuples():
        dias = [r.data] if r.quando == "antes da abertura" else \
            [r.data + pd.offsets.BDay(1)] if r.quando == "depois do fecho" else [r.data, r.data + pd.offsets.BDay(1)]
        for x in dias:
            reac.add((r.ticker, pd.Timestamp(x).normalize()))
    todos["reacao"] = [(t, d) in reac for t, d in zip(todos["ticker"], todos["data"])]
    meio_dia = (todos["pos_fecho"])
    flags = sinais.detetar(todos)   # setups atuais (as colunas existem todas)
    filtros = {s: cfg.get("qualidade", {}).get(s, {}).get("filtros") for s in cfg["setups"]}

    regras = {
        "V1 resultados maus": ("venda", todos["reacao"] & (todos["ret_dia"] <= -0.05) & (todos["vol_ratio"] >= 2) & (meio_dia < 0.5)),
        "V3 fraca no setor fraco": ("venda", (todos["setor_quinto"] <= 0.2) & (todos["rs_setor"] < -0.10) & (todos["close"] < todos["min20"])),
        "C1 resultados bons": ("compra", todos["reacao"] & (todos["ret_dia"] >= 0.05) & (todos["vol_ratio"] >= 2) & (meio_dia >= 0.5)),
    }
    atuais = flags[[s for s in cfg["setups"] if s in flags]].any(axis=1)
    regras["C2 atuais, mercado fraco, setor forte"] = ("compra", atuais & (todos["amp"] < 0.5) & (todos["setor_acima50"] >= 0.5))
    regras["C3 atuais, mercado fraco, mais forte que o mercado"] = ("compra", atuais & (todos["amp"] < 0.5) & (todos["rs_mercado"] > 0))
    regras["C0 atuais, mercado fraco (referência)"] = ("compra", atuais & (todos["amp"] < 0.5))

    linhas = []
    por_ticker = {t: g.index.to_numpy() for t, g in todos.groupby("ticker")}
    for nome, (lado, flag) in regras.items():
        flag = flag.fillna(False).to_numpy()
        for t, idx in por_ticker.items():
            d = todos.loc[idx].reset_index(drop=True)
            for i in np.flatnonzero(flag[idx]):
                for sd, regra in SAIDAS.items():
                    if lado == "compra":
                        r = saidas.simular(d, i, regra)
                        liq = None if r is None else r[0] - SPREAD
                    else:
                        r = simular_venda(d, i, regra)
                        liq = None if r is None else r[0] - SPREAD - NOITE * r[1]
                    if liq is not None:
                        linhas.append((nome, sd, d["data"].iat[i], liq, d["amp"].iat[i]))
    t = pd.DataFrame(linhas, columns=["regra", "saida", "data", "liq", "amp"])
    if t.empty:
        print("Nenhum trade.")
        return
    meio = t["data"].median()
    print(f"\nMetades a partir de {meio:%Y-%m-%d}; líquido de custos")
    for (nome, sd), g in t.groupby(["regra", "saida"]):
        for cond, x in (("sempre", g), ("mercado fraco", g[g["amp"] < 0.5])):
            if x.empty or (cond == "mercado fraco" and nome.startswith("C")):
                continue
            a, b = x[x["data"] < meio], x[x["data"] >= meio]
            print(f"  {nome:48s} {sd:9s} {cond:13s} n={len(x):6d} ({x['data'].nunique()} dias) "
                  f"{x['liq'].mean()*100:+.2f}% ({a['liq'].mean()*100:+.2f}|{b['liq'].mean()*100:+.2f}) "
                  f"ganha {(x['liq'] > 0).mean()*100:.0f}% | pior 10% {np.sort(x['liq'])[:max(len(x)//10, 1)].mean()*100:+.1f}%")


if __name__ == "__main__":
    main()
