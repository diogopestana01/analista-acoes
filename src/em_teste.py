"""Sinais em teste: regras novas que dão alerta à parte, sem ocupar os lugares do modelo.

Pedido do Pestana (6/11): "posso ter bué regras e só entro nas que, dependendo do mercado, fazem
sentido". Estudo passo3u_biblioteca.py (17 regras conhecidas x tipo de mercado, nas duas metades):
  forte   (>= 50% das ações dos EUA acima da média de 50 dias): "caiu 10% em 5 dias, em tendência"
          +1,29% | +1,03% por trade, ganha 65%.
  queda   (SPY a cair mais de 3% em 5 dias): as compras de "ação boa que caiu" (RSI2, duplo 7, 3 dias a
          cair, IBS, Bollinger, caiu 10%, rompe dia interior) +2% a +4% por trade, ganham 75-83%.
  fraco   nada ganhou -> sem sinais em teste. Vendas CFD: todas perderam -> nenhuma.
Na carteira de 5 lugares estas regras tiravam lugar aos pullbacks e rendiam menos; por isso ficam à
parte: só o dono recebe, marcadas "em teste", e cada uma fica no registo em papel data/sinais_teste.csv
para se ver ao vivo se merece entrar no modelo.
Como no estudo, a entrada é perto do fecho: os alertas saem nos últimos `janela_min` minutos de NY.
Saída: vende ao fechar acima da média de 5 dias; stop 2 ATR; no máximo 5 dias (regra sma5 de src/saidas.py).
Só ações dos EUA. Só o código decide; o Claude não entra aqui.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import acompanhamento, indicadores, saidas

log = logging.getLogger(__name__)
REGRA_SAIDA = saidas.REGRAS["sma5"]
COLUNAS = ["data", "ticker", "regra", "mercado", "entrada", "stop", "atr", "estado", "data_saida",
           "preco_saida", "retorno", "dias", "motivo", "aviso_saida"]

NOMES = {
    "caiu_10_5d": "caiu 10% em 5 dias, em tendência de subida",
    "rsi2_10": "RSI de 2 dias abaixo de 10, em tendência de subida",
    "rsi2_5": "RSI de 2 dias abaixo de 5",
    "duplo_7": "fecho mais baixo de 7 dias, em tendência de subida",
    "tres_quedas": "3 dias seguidos a cair, em tendência de subida",
    "ibs_baixo": "fechou no fundo do dia (a cair), em tendência de subida",
    "bollinger": "abaixo da banda de Bollinger, em tendência de subida",
    "dia_interior": "rompeu o máximo de um dia interior, em tendência de subida",
}
# Resultado no estudo (por trade, líquido de 0,3%; 1.ª | 2.ª metade, % que ganha)
HISTORICO = {
    ("forte", "caiu_10_5d"): "+1,29% | +1,03%, ganha 65%",
    ("queda", "caiu_10_5d"): "+0,34% | +7,52%, ganha 76%",
    ("queda", "rsi2_10"): "+0,90% | +5,25%, ganha 80%",
    ("queda", "rsi2_5"): "+2,64% | +4,47%, ganha 74%",
    ("queda", "duplo_7"): "+0,81% | +5,21%, ganha 81%",
    ("queda", "tres_quedas"): "+0,71% | +4,86%, ganha 82%",
    ("queda", "ibs_baixo"): "+1,21% | +3,80%, ganha 83%",
    ("queda", "bollinger"): "+0,50% | +6,43%, ganha 83%",
    ("queda", "dia_interior"): "+2,22% | +2,06%, ganha 66%",
}
# Com 5 por dia escolhidas pelo RSI de 2 dias mais baixo (secção 3 do estudo): média e o pior 1 em 10
CINCO_POR_DIA = {"forte": "+1,52% | +0,02% por trade; 1 em cada 10 perdeu mais de 13%",
                 "queda": "+3,75% | +5,31% por trade, ganha 74%; 1 em cada 10 perdeu mais de 9% (só 17 dias assim)"}
TEXTO_MERCADO = {"forte": "mercado forte", "queda": "queda forte do mercado (S&P 500 a cair mais de 3% em 5 dias)"}


def detetar(d: pd.DataFrame) -> pd.DataFrame:
    """Uma coluna booleana por regra (mesmas regras do estudo passo3u_biblioteca.py)."""
    c, h = d["close"], d["high"]
    alta = c > d["sma200"]
    std20 = c.rolling(20).std()
    cai3 = (c < c.shift(1)) & (c.shift(1) < c.shift(2)) & (c.shift(2) < c.shift(3))
    interior = (h.shift(1) < h.shift(2)) & (d["low"].shift(1) > d["low"].shift(2))
    s = pd.DataFrame(index=d.index)
    s["caiu_10_5d"] = (d["ret5"] < -0.10) & alta
    s["rsi2_10"] = (d["rsi2"] < 10) & alta
    s["rsi2_5"] = d["rsi2"] < 5
    s["duplo_7"] = (c <= c.rolling(7).min()) & alta
    s["tres_quedas"] = cai3 & alta
    s["ibs_baixo"] = (d["pos_fecho"] < 0.15) & (d["ret_dia"] < 0) & alta
    s["bollinger"] = (c < d["sma20"] - 2 * std20) & alta
    s["dia_interior"] = interior & (c > h.shift(1)) & alta
    return s.fillna(False)


def tipo_mercado(amplitude: float | None, spy_5d: float | None, cfg: dict) -> str | None:
    if spy_5d is not None and spy_5d < float(cfg.get("queda_spy_5d", -0.03)):
        return "queda"
    if amplitude is None:
        return None
    return "forte" if amplitude >= float(cfg.get("amplitude_min", 0.5)) else "fraco"


def na_janela(agora: pd.Timestamp, minutos: int) -> bool:
    """Bolsa de NY aberta e a menos de `minutos` do fecho."""
    from .precos import _horario
    if not acompanhamento.bolsa_aberta("SPY", agora):
        return False
    fim = _horario("SPY", agora)[1]
    return (fim - (agora.hour + agora.minute / 60)) * 60 <= minutos


def carregar(caminho: Path) -> pd.DataFrame:
    if caminho.exists():
        try:
            texto = ["data", "ticker", "regra", "mercado", "estado", "data_saida", "motivo", "aviso_saida"]
            return pd.read_csv(caminho, dtype={c: str for c in texto}, keep_default_na=False,
                               na_values={c: [""] for c in COLUNAS if c not in texto}).reindex(columns=COLUNAS)
        except (pd.errors.EmptyDataError, ValueError):
            pass
    return pd.DataFrame(columns=COLUNAS)


def candidatos(precos: pd.DataFrame, tickers, regras: list[str], ordem: str = "ret5") -> pd.DataFrame:
    """Ações dos EUA com alguma das `regras` no último dia, uma linha por ação."""
    linhas = []
    p = precos[precos["ticker"].isin(set(tickers))]
    for ticker, df in p.sort_values("data").groupby("ticker"):
        df = df.reset_index(drop=True)
        if len(df) < 210:
            continue
        d = indicadores.calcular(df)
        f = detetar(d).iloc[-1]
        quais = [r for r in regras if bool(f.get(r, False))]
        if not quais:
            continue
        u = d.iloc[-1]
        if not (u["atr14"] > 0):
            continue
        linhas.append({"ticker": ticker, "regras": quais, "entrada": float(u["close"]), "atr": float(u["atr14"]),
                       "sma5": float(u["sma5"]), "ret5": float(u["ret5"]), "rsi2": float(u["rsi2"]),
                       "atr_pct": float(u["atr_pct"]), "n_regras": len(quais)})
    c = pd.DataFrame(linhas)
    if c.empty:
        return c
    asc = ordem not in ("n_regras",)
    return c.sort_values(ordem if ordem in c else "ret5", ascending=asc, kind="stable").reset_index(drop=True)


def texto_alerta(mercado: str, escolhidos: pd.DataFrame, nomes: dict | None = None) -> str:
    nomes = nomes or {}
    linhas = [f"🧪 <b>Sinais em teste</b> ({TEXTO_MERCADO.get(mercado, mercado)})",
              "Regras novas a ser medidas: não ocupam os lugares do modelo. Entrada perto do fecho de hoje; "
              "sai quando fechar acima da média de 5 dias, stop a 2 ATR, no máximo 5 dias.",
              f"No histórico, as 5 do dia: {CINCO_POR_DIA.get(mercado, 'sem histórico')}. Risco alto: usa pouco dinheiro."]
    for r in escolhidos.itertuples():
        nome = f" ({nomes[r.ticker]})" if nomes.get(r.ticker) else ""
        regras = "; ".join(f"{NOMES[x]} [{HISTORICO.get((mercado, x), 'sem histórico')}]" for x in r.regras)
        stop = r.entrada - REGRA_SAIDA.mult_stop * r.atr
        linhas.append(f"\n• <b>{r.ticker}</b>{nome}: {r.entrada:.2f} · stop {stop:.2f} "
                      f"({(stop / r.entrada - 1) * 100:.1f}%) · média de 5 dias {r.sma5:.2f}\n  {regras}")
    return "\n".join(linhas)


def atualizar(reg: pd.DataFrame, precos: pd.DataFrame, agora: pd.Timestamp) -> tuple[pd.DataFrame, list[str]]:
    """Fecha em papel os sinais abertos (stop, fecho acima da média de 5 dias, 5 dias), só com velas completas.
    Nos últimos minutos da sessão avisa que é dia de vender (fecho acima da média de 5 dias). Devolve avisos."""
    avisos = []
    abertos = reg.index[reg["estado"] == "aberto"]
    hoje = f"{agora:%Y-%m-%d}"
    aberta = acompanhamento.bolsa_aberta("SPY", agora)
    for i in abertos:
        r = reg.loc[i]
        df = precos[precos["ticker"] == r["ticker"]].sort_values("data").reset_index(drop=True)
        if df.empty:
            continue
        df["dia"] = pd.to_datetime(df["data"]).dt.strftime("%Y-%m-%d")
        d = indicadores.calcular(df.drop(columns="dia")).assign(dia=df["dia"])
        pos = np.flatnonzero(d["dia"].to_numpy() == str(r["data"]))
        if not len(pos):
            continue
        i0 = int(pos[0])
        completas = d[d["dia"] < hoje] if aberta else d
        res = saidas.simular(d.loc[:completas.index.max()], i0, REGRA_SAIDA) if len(completas) > i0 + 1 else None
        if res is not None:   # o estudo entra ao fecho; aqui o retorno conta desde o preço do alerta
            ret, dias, motivo = res
            saida = float(d["close"].iat[i0]) * (1 + ret)
            reg.loc[i, ["estado", "data_saida", "preco_saida", "retorno", "dias", "motivo"]] = \
                ["fechado", d["dia"].iat[i0 + dias], saida, saida / float(r["entrada"]) - 1, dias, motivo]
            continue
        u = d.iloc[-1]
        if aberta and u["dia"] == hoje and len(d) - 1 > i0 and u["close"] > u["sma5"] \
                and str(r.get("aviso_saida")) != hoje:
            avisos.append(f"🧪 <b>{r['ticker']}</b> (em teste) está acima da média de 5 dias ({u['close']:.2f} > "
                          f"{u['sma5']:.2f}): pela regra, vende perto do fecho de hoje "
                          f"({(u['close'] / float(r['entrada']) - 1) * 100:+.1f}% desde a entrada).")
            reg.loc[i, "aviso_saida"] = hoje
    return reg, avisos


def correr(precos: pd.DataFrame, tickers_eua, cfg: dict | None, agora: pd.Timestamp, regime_estado: dict | None,
           caminho: Path, enviar, abertos_modelo: set | None = None, nomes: dict | None = None,
           spy5=None) -> pd.DataFrame | None:
    """Uma corrida: atualiza o registo em papel, avisa saídas e, na janela antes do fecho, envia sinais novos."""
    cfg = cfg or {}
    if not cfg.get("ligado"):
        return None
    reg = carregar(caminho)
    reg, avisos = atualizar(reg, precos, agora)
    for a in avisos:
        enviar(a)
    hoje = f"{agora:%Y-%m-%d}"
    if na_janela(agora, int(cfg.get("janela_min", 40))) and not (reg["data"].astype(str) == hoje).any():
        amp = (regime_estado or {}).get("amplitude")
        s5 = (regime_estado or {}).get("spy_5d")
        if s5 is None and spy5 is not None:
            s5 = spy5()
        mercado = tipo_mercado(amp, s5, cfg)
        regras = (cfg.get("regras") or {}).get(mercado or "", [])
        log.info("Em teste: mercado %s (amplitude %s, SPY 5 dias %s), regras %s", mercado, amp, s5, regras)
        if regras:
            fora = set(reg.loc[reg["estado"] == "aberto", "ticker"]) | set(abertos_modelo or ())
            c = candidatos(precos, [t for t in tickers_eua if t not in fora], regras, cfg.get("ordem", "ret5"))
            esc = c.head(int(cfg.get("max_por_dia", 5))) if not c.empty else c
            if not esc.empty:
                ok = enviar(texto_alerta(mercado, esc, nomes))
                novos = pd.DataFrame([{"data": hoje, "ticker": r.ticker, "regra": "+".join(r.regras), "mercado": mercado,
                                       "entrada": r.entrada, "stop": r.entrada - REGRA_SAIDA.mult_stop * r.atr,
                                       "atr": r.atr, "estado": "aberto", "aviso_saida": ""} for r in esc.itertuples()])
                reg = pd.concat([reg, novos.reindex(columns=COLUNAS)], ignore_index=True)
                log.info("Em teste: %d sinais (%s), enviado %s", len(esc), ", ".join(esc["ticker"]), ok)
            else:
                # marca o dia como visto, para não recalcular a cada corrida
                reg = pd.concat([reg, pd.DataFrame([{"data": hoje, "ticker": "", "estado": "nenhum",
                                                     "mercado": mercado}]).reindex(columns=COLUNAS)],
                                ignore_index=True)
        else:
            reg = pd.concat([reg, pd.DataFrame([{"data": hoje, "ticker": "", "estado": "nenhum",
                                                 "mercado": mercado or ""}]).reindex(columns=COLUNAS)],
                            ignore_index=True)
    reg.to_csv(caminho, index=False)
    return reg


def resumo(reg: pd.DataFrame) -> str | None:
    """Uma linha por tipo de mercado com os resultados fechados em papel (para o resumo de sexta)."""
    f = reg[reg["estado"] == "fechado"]
    if f.empty:
        return None
    partes = [f"{m}: {len(g)} fechados, {g['retorno'].astype(float).mean() * 100 - 0.3:+.2f}% líquido, "
              f"ganha {(g['retorno'].astype(float) > 0.003).mean() * 100:.0f}%" for m, g in f.groupby("mercado")]
    return "🧪 Em teste (papel): " + "; ".join(partes)
