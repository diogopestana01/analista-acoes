"""Passo 3: testa os setups nos últimos 2 anos de todo o universo filtrado.

Uso:  python passo3_backtest.py   (depois de passo1_universo.py)
Gera data/backtest_trades.csv e data/backtest_resumo.csv.
Só a parte técnica é testada; as notícias antigas não estão disponíveis de graça.
Não inclui comissões nem spread da XTB, por isso os resultados reais serão piores.
"""
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src import indicadores, sinais

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
RAIZ = Path(__file__).parent
DADOS = RAIZ / "data"


def backtest(precos: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    trades = []
    for ticker, df in precos.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        flags = sinais.detetar(d)
        for nome in cfg["setups"]:
            direcao = sinais.SETUPS[nome].direcao
            for i in np.flatnonzero(flags[nome].to_numpy()):
                res, dias = sinais.resultado_trade(d, i, direcao, cfg["mult_objetivo"], cfg["mult_stop"],
                                                   cfg["max_dias"])
                if res == "aberto":
                    continue
                entrada, a = d["close"].iat[i], d["atr14"].iat[i]
                sinal = 1 if direcao == "compra" else -1
                saida = {"objetivo": entrada + sinal * cfg["mult_objetivo"] * a,
                         "stop": entrada - sinal * cfg["mult_stop"] * a,
                         "expirou": d["close"].iat[i + dias]}[res]
                trades.append({"ticker": ticker, "data": d["data"].iat[i], "setup": nome, "resultado": res,
                               "dias": dias, "retorno": sinal * (saida / entrada - 1)})
    return pd.DataFrame(trades)


def resumir(trades: pd.DataFrame) -> pd.DataFrame:
    g = trades.groupby("setup")
    return pd.DataFrame({
        "trades": g.size(),
        "taxa_objetivo": g["resultado"].apply(lambda r: (r == "objetivo").mean()),
        "taxa_stop": g["resultado"].apply(lambda r: (r == "stop").mean()),
        "retorno_medio_pct": g["retorno"].mean() * 100,
        "dias_medios": g["dias"].mean(),
        "pior_trade_pct": g["retorno"].min() * 100,
    }).round(3)


def main() -> None:
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text())["sinais"]
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    trades = backtest(precos[precos["ticker"].isin(filtrado["ticker"])], cfg)
    trades.to_csv(DADOS / "backtest_trades.csv", index=False)
    resumo = resumir(trades)
    resumo.to_csv(DADOS / "backtest_resumo.csv")
    print(resumo.to_string())


if __name__ == "__main__":
    main()
