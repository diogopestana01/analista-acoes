"""Day trading a sério: regras dentro do mesmo dia, com velas de 5 minutos (últimos ~60 dias do Yahoo).

Pedido do Pestana (6/11): "O objetivo é ter um bot a fazer day trading autonomamente... travão em todas as
trades, take profits realistas e prováveis" (skills de jakeschincariol/day-trader-skill).
Todas as trades: entram no fecho de uma vela de 5 min, têm stop e take profit desde a entrada e fecham
sempre até ao fecho de NY (nada fica de um dia para o outro). Se o stop e o objetivo calham na mesma vela,
conta o stop (pior caso). Custos: 0,1% (ações muito líquidas) e 0,3% por trade (ida e volta).
Regras (R = distância da entrada ao stop):
  orb15        rompe o máximo dos primeiros 15 min; stop no mínimo desses 15 min; objetivo 2R
  orb15_venda  rompe o mínimo dos primeiros 15 min (CFD); stop no máximo; objetivo 2R
  gap_go       abriu +3% ou mais e rompe o máximo da 1.ª vela; stop no mínimo dela; objetivo 2R
  gap_fade     abriu +3% ou mais e perde o mínimo dos 15 min (CFD); stop no máximo; objetivo o fecho de ontem
  bull_flag    subiu 2%+ na 1.ª meia hora, recua no máximo metade e rompe o topo da bandeira; stop no fundo
               dela; objetivo 2R (skill)
  vwap_reclaim depois das 10:30 volta a fechar acima do VWAP num dia a subir; stop no mínimo das últimas
               6 velas; objetivo 2R (skill "reclaim")
  oversold     RSI(14) de 5 min < 20 depois das 10:00; stop 1% abaixo; objetivo o VWAP (skill)
  fim_do_dia   às 15:30 sobe 2%+ e está a menos de 0,5% do máximo do dia; stop no mínimo da última hora;
               objetivo 1R; sai no fecho
  referência   compra na abertura (09:35) e vende no fecho, sem stop
Mede por regra: retorno médio por trade, % que ganha, trades por dia, em cada metade dos dias (1.ª|2.ª).
Carteira de dia: até 5 trades por dia (as primeiras a dar sinal), 20% do capital cada, somado por mês.
Uso: ACOES=300 python passo3x_daytrade.py
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

import passo3p_regime as p3p

DADOS = p3p.DADOS
CUSTOS = (0.001, 0.003)
log = logging.getLogger(__name__)


def descarregar(tickers: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    out = {}
    for k in range(0, len(tickers), 50):
        grupo = tickers[k:k + 50]
        try:
            bruto = yf.download(grupo, period="60d", interval="5m", group_by="ticker", auto_adjust=True,
                                progress=False, threads=False, prepost=False)
        except Exception as e:  # noqa: BLE001
            log.warning("Lote %d falhou: %s", k, e)
            continue
        for t in grupo:
            try:
                df = bruto[t].dropna(subset=["Open", "High", "Low", "Close"])
            except KeyError:
                continue
            if len(df) > 500:
                df = df.rename(columns=str.lower)
                df.index = df.index.tz_convert("America/New_York")
                out[t] = df[["open", "high", "low", "close", "volume"]]
        log.info("Velas de 5 min: %d de %d", min(k + 50, len(tickers)), len(tickers))
    return out


def sair(h, lo, c, i: int, stop: float, alvo: float, lado: int) -> float:
    """Entra no fecho da vela i; sai no stop, no objetivo ou no fecho do dia. lado 1 = compra, -1 = venda."""
    e = c[i]
    for j in range(i + 1, len(c)):
        if lado == 1:
            if lo[j] <= stop:
                return stop / e - 1
            if h[j] >= alvo:
                return alvo / e - 1
        else:
            if h[j] >= stop:
                return 1 - stop / e
            if lo[j] <= alvo:
                return 1 - alvo / e
    return (c[-1] / e - 1) * lado


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    s = pd.Series(c).diff()
    up = s.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-s.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy()


def regras_do_dia(g: pd.DataFrame, fecho_ontem: float) -> list[tuple[str, int, float]]:
    """(regra, índice da vela de entrada, retorno bruto) para um dia de uma ação."""
    o, h, lo, c, v = (g[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    n = len(c)
    if n < 60 or not np.isfinite(fecho_ontem):
        return []
    hm = g.index.hour * 60 + g.index.minute
    out = []
    tp = (h + lo + c) / 3
    vwap = np.cumsum(tp * v) / np.maximum(np.cumsum(v), 1)
    # orb15: primeiras 3 velas
    orh, orl = h[:3].max(), lo[:3].min()
    for i in range(3, n - 1):
        if c[i] > orh:
            r = c[i] - orl
            if r > 0:
                out.append(("orb15", i, sair(h, lo, c, i, orl, c[i] + 2 * r, 1)))
            break
    for i in range(3, n - 1):
        if c[i] < orl:
            r = orh - c[i]
            if r > 0:
                out.append(("orb15_venda", i, sair(h, lo, c, i, orh, c[i] - 2 * r, -1)))
            break
    gap = o[0] / fecho_ontem - 1
    if gap >= 0.03:
        for i in range(1, n - 1):
            if c[i] > h[0]:
                r = c[i] - lo[0]
                if r > 0:
                    out.append(("gap_go", i, sair(h, lo, c, i, lo[0], c[i] + 2 * r, 1)))
                break
        for i in range(3, n - 1):
            if c[i] < orl:
                if fecho_ontem < c[i]:
                    out.append(("gap_fade", i, sair(h, lo, c, i, orh, fecho_ontem, -1)))
                break
    # bull flag: subida 2%+ nas primeiras 6 velas, depois 3-8 velas de recuo <= 50%, rompe o topo
    topo_i = int(np.argmax(h[:6]))
    subida = h[topo_i] / o[0] - 1
    if subida >= 0.02:
        topo = h[topo_i]
        for i in range(topo_i + 3, min(topo_i + 12, n - 1)):
            fundo = lo[topo_i + 1:i].min()
            if (topo - fundo) > 0.5 * (topo - o[0]):
                break
            if c[i] > topo:
                r = c[i] - fundo
                if r > 0:
                    out.append(("bull_flag", i, sair(h, lo, c, i, fundo, c[i] + 2 * r, 1)))
                break
    # vwap reclaim depois das 10:30 num dia a subir
    for i in range(1, n - 1):
        if hm[i] < 10 * 60 + 30:
            continue
        if c[i] > vwap[i] and c[i - 1] <= vwap[i - 1] and c[i] > fecho_ontem:
            stop = lo[max(0, i - 6):i + 1].min()
            r = c[i] - stop
            if r > 0:
                out.append(("vwap_reclaim", i, sair(h, lo, c, i, stop, c[i] + 2 * r, 1)))
            break
    rs = rsi(c)
    for i in range(1, n - 1):
        if hm[i] >= 10 * 60 and rs[i] < 20 and vwap[i] > c[i] * 1.002:
            out.append(("oversold", i, sair(h, lo, c, i, c[i] * 0.99, vwap[i], 1)))
            break
    idx = np.flatnonzero(hm >= 15 * 60 + 30)
    if len(idx) and idx[0] < n - 1:
        i = int(idx[0])
        if c[i] / fecho_ontem - 1 >= 0.02 and c[i] >= h[:i + 1].max() * 0.995:
            stop = lo[max(0, i - 12):i + 1].min()
            r = c[i] - stop
            if r > 0:
                out.append(("fim_do_dia", i, sair(h, lo, c, i, stop, c[i] + r, 1)))
    out.append(("referência", 0, c[-1] / c[0] - 1))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    precos = pd.read_parquet(DADOS / "precos.parquet")
    filtrado = pd.read_csv(DADOS / "universo_filtrado.csv")
    eua = filtrado["ticker"][~filtrado["ticker"].str.contains(".", regex=False)]
    p = precos[precos["ticker"].isin(eua)].assign(dinheiro=precos["close"] * precos["volume"])
    p = p[pd.to_datetime(p["data"]) >= pd.to_datetime(p["data"]).max() - pd.Timedelta(days=60)]
    top = sorted(p.groupby("ticker")["dinheiro"].median().nlargest(int(os.environ.get("ACOES", "300"))).index)
    velas = descarregar(top)
    linhas = []
    for t, df in velas.items():
        dias = list(df.groupby(df.index.date))
        for k in range(1, len(dias)):
            dia, g = dias[k]
            fecho_ontem = float(dias[k - 1][1]["close"].iat[-1])
            for regra, i, ret in regras_do_dia(g, fecho_ontem):
                linhas.append((t, pd.Timestamp(dia), g.index[i], regra, ret))
    tr = pd.DataFrame(linhas, columns=["ticker", "dia", "hora", "regra", "ret"])
    dias = sorted(tr["dia"].unique())
    meio = dias[len(dias) // 2]
    tr["metade"] = np.where(tr["dia"] < meio, 1, 2)
    print(f"Ações com velas de 5 min: {len(velas)}; dias {len(dias)} ({dias[0]:%Y-%m-%d} a {dias[-1]:%Y-%m-%d}); "
          f"metades a partir de {meio:%Y-%m-%d}; trades {len(tr)}")
    print("\n### Por trade (custo 0,1% | 0,3%; 1.ª|2.ª metade com 0,1%)")
    for r, g in tr.groupby("regra"):
        a, b = g[g["metade"] == 1]["ret"] - CUSTOS[0], g[g["metade"] == 2]["ret"] - CUSTOS[0]
        print(f"  {r:13s} n={len(g):6d} ({len(g) / len(dias):5.1f}/dia) {(g['ret'] - CUSTOS[0]).mean()*100:+.3f}% | "
              f"{(g['ret'] - CUSTOS[1]).mean()*100:+.3f}%  ({a.mean()*100:+.3f}|{b.mean()*100:+.3f}) "
              f"ganha {(g['ret'] > CUSTOS[0]).mean()*100:.0f}%  pior 10% {np.sort(g['ret'])[:max(len(g)//10,1)].mean()*100:+.2f}%")
    print("\n### Carteira de dia: 5 primeiras trades da regra por dia, 20% cada (soma por mês, custo 0,1% | 0,3%)")
    for r, g in tr[tr["regra"] != "referência"].groupby("regra"):
        g = g.sort_values(["dia", "hora"]).groupby("dia").head(5)
        txt = []
        for cst in CUSTOS:
            dia_ret = (g["ret"] - cst).groupby(g["dia"]).sum() * 0.2
            mes = dia_ret.groupby(dia_ret.index.to_period("M")).sum()
            txt.append(" ".join(f"{m}: {v*100:+.1f}%" for m, v in mes.items()))
        print(f"  {r:13s} {txt[0]}  ||  com 0,3%: {txt[1]}")


if __name__ == "__main__":
    main()
