"""Acompanha os sinais já enviados e avisa quando é hora de vender/fechar.

O histórico fica em data/historico_sinais.csv (guardado no repositório), o que
também serve de registo do desempenho real do modelo (passo 3, em papel).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import indicadores, saidas

COLUNAS = ["data_sinal", "ticker", "setup", "direcao", "entrada", "objetivo", "stop",
           "dias_estimados", "max_dias", "pontuacao", "estado", "data_fecho", "preco_fecho", "dias", "saida",
           "veredicto_inv", "risco_inv", "impacto_noticias", "sentimento", "reddit_mencoes", "reddit_variacao",
           "st_otimismo", "retorno_esperado", "verif_noticias", "verif_inv"]


def carregar(caminho: Path) -> pd.DataFrame:
    # Colunas de texto lidas como texto: vazias, o pandas lia-as como números e falhava ao fechar.
    if caminho.exists():
        h = pd.read_csv(caminho, dtype={"data_sinal": str, "estado": str, "data_fecho": object, "saida": object,
                                       "veredicto_inv": object, "impacto_noticias": object,
                                       "verif_noticias": object, "verif_inv": object})
        return h.reindex(columns=COLUNAS)
    return pd.DataFrame(columns=COLUNAS)


def registar(hist: pd.DataFrame, novos: pd.DataFrame, data: str, max_dias: int) -> pd.DataFrame:
    if novos.empty:
        return hist
    n = novos.reindex(columns=["ticker", "setup", "direcao", "entrada", "objetivo", "stop", "dias_estimados",
                                "pontuacao", "saida", "max_dias", "veredicto_inv", "risco_inv",
                                "impacto_noticias", "sentimento", "reddit_mencoes", "reddit_variacao",
                                "st_otimismo", "retorno_esperado_liquido", "verif_noticias", "verif_inv"]) \
        .rename(columns={"retorno_esperado_liquido": "retorno_esperado"}).copy()
    n.insert(0, "data_sinal", data)
    n["max_dias"] = n["max_dias"].fillna(max_dias)
    n["estado"] = "aberto"
    novos_abertos = hist[(hist["estado"] == "aberto")][["ticker", "direcao"]]
    chave = set(map(tuple, novos_abertos.to_numpy()))
    n = n[[(t, d) not in chave for t, d in zip(n["ticker"], n["direcao"])]]  # não repete posição aberta
    return pd.concat([hist, n], ignore_index=True)[COLUNAS]


def sessao_fechada(ticker: str, dia: str, agora: pd.Timestamp) -> bool:
    """A vela deste dia já é a final? (a de hoje só depois do fecho da bolsa, com 10 min de folga)."""
    from .precos import _horario
    agora = pd.Timestamp(agora).tz_convert("UTC") if pd.Timestamp(agora).tzinfo else pd.Timestamp(agora)
    hoje = f"{agora:%Y-%m-%d}"
    if dia < hoje:
        return True
    if dia > hoje:
        return False
    return agora.hour + agora.minute / 60 >= _horario(ticker)[1] + 10 / 60


def atualizar(hist: pd.DataFrame, precos: pd.DataFrame,
              agora: pd.Timestamp | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Verifica cada sinal aberto contra os preços desde a data do sinal.
    Stop e objetivo contam a qualquer hora; as saídas "ao fecho" (regra do setup e fim do prazo)
    só depois de a bolsa fechar, como no backtest. Antes disso a vela de hoje ainda vai mudar."""
    agora = pd.Timestamp.now(tz="UTC") if agora is None else agora
    fechos = []
    hist = hist.copy()
    hist["data_fecho"] = hist["data_fecho"].astype(object)
    abertos = hist[hist["estado"] == "aberto"]
    precos = precos[precos["ticker"].isin(abertos["ticker"])]
    # Indicadores (média de 5 dias, RSI2) para as regras de saída ao fecho.
    if not precos.empty:
        precos = pd.concat([g.sort_values("data").assign(sma5=lambda x: x["close"].rolling(5).mean(),
                                                         rsi2=lambda x: indicadores.rsi(x["close"], 2))
                            for _, g in precos.groupby("ticker")], ignore_index=True)
    precos = precos.assign(data=pd.to_datetime(precos["data"]).dt.strftime("%Y-%m-%d"))
    for i, s in abertos.iterrows():
        p = precos[(precos["ticker"] == s["ticker"]) & (precos["data"] > s["data_sinal"])].sort_values("data")
        if p.empty:
            continue
        compra = s["direcao"] == "compra"
        regra = saidas.REGRAS.get(s["saida"]) if isinstance(s["saida"], str) else None
        resultado, linha = None, None
        for k, (_, r) in enumerate(p.iterrows(), start=1):
            stop = pd.notna(s["stop"]) and (r["low"] <= s["stop"] if compra else r["high"] >= s["stop"])
            obj = pd.notna(s["objetivo"]) and (r["high"] >= s["objetivo"] if compra else r["low"] <= s["objetivo"])
            if stop or obj:
                resultado, linha = ("stop" if stop else "objetivo"), (k, r)
                break
            if not sessao_fechada(s["ticker"], r["data"], agora):
                break   # vela de hoje incompleta: a saída ao fecho decide-se depois do fecho
            if regra is not None and compra and saidas.condicao_saida(regra, r):
                resultado, linha = "regra", (k, r)
                break
            if k >= s["max_dias"]:
                resultado, linha = "expirou", (k, r)
                break
        if resultado is None:
            continue
        k, r = linha
        preco = {"stop": s["stop"], "objetivo": s["objetivo"], "expirou": r["close"], "regra": r["close"]}[resultado]
        if resultado == "stop" and pd.notna(r.get("open")):   # abriu já abaixo do stop: sai à abertura (pior)
            preco = min(preco, r["open"]) if compra else max(preco, r["open"])
        hist.loc[i, ["estado", "data_fecho", "preco_fecho", "dias"]] = [resultado, r["data"], preco, k]
        var = (preco / s["entrada"] - 1) * (100 if compra else -100)
        fechos.append({**s.to_dict(), "resultado": resultado, "preco_atual": r["close"], "preco_saida": preco,
                       "variacao_pct": var})
    return hist, fechos


def resumo(hist: pd.DataFrame) -> str:
    f = hist[hist["estado"] != "aberto"]
    if f.empty:
        return "Ainda sem sinais fechados."
    compra = f["direcao"] == "compra"
    ret = (f["preco_fecho"] / f["entrada"] - 1).where(compra, 1 - f["preco_fecho"] / f["entrada"])
    return (f"{len(f)} sinais fechados: {(ret > 0).mean() * 100:.0f}% a ganhar, "
            f"retorno médio {ret.mean() * 100:+.2f}% por sinal, {f['dias'].mean():.1f} dias em média.")


def _fechados_compra(hist: pd.DataFrame) -> pd.DataFrame:
    f = hist[(hist["estado"] != "aberto") & (hist["direcao"] == "compra")].copy()
    f["ret"] = pd.to_numeric(f["preco_fecho"], errors="coerce") / pd.to_numeric(f["entrada"], errors="coerce") - 1
    return f.dropna(subset=["ret"])


def resultados_reais(hist: pd.DataFrame) -> dict[str, tuple[int, float]]:
    """{setup: (n fechados, retorno médio)} do registo em papel, só compras."""
    f = _fechados_compra(hist)
    return {s: (len(g), float(g["ret"].mean())) for s, g in f.groupby("setup")}


SETUPS_PT = {"rompimento": "Rompimento", "pullback": "Correção em tendência", "semana_forte": "Semana forte",
             "gap_volume": "Salto com volume", "gap_continuacao": "Continuação de salto"}


def placar(hist: pd.DataFrame, hoje: str, dias_uteis: int = 5) -> str:
    """Placar por estratégia: últimos `dias_uteis` dias úteis e desde o início (texto simples)."""
    f = _fechados_compra(hist)
    if f.empty:
        return "Placar semanal: ainda sem sinais fechados."
    limite = (pd.Timestamp(hoje) - pd.offsets.BDay(dias_uteis)).strftime("%Y-%m-%d")
    semana = f[f["data_fecho"].astype(str) > limite]
    linhas = ["🏆 Placar por estratégia (esta semana | desde o início)"]
    for setup, g in f.groupby("setup"):
        w = semana[semana["setup"] == setup]
        sem = (f"{len(w)} fechados, {(w['ret'] > 0).mean()*100:.0f}% a ganhar, {w['ret'].mean()*100:+.1f}%"
               if len(w) else "nenhum fechado")
        linhas.append(f"• {SETUPS_PT.get(setup, setup)}: {sem} | {len(g)} fechados, "
                      f"{(g['ret'] > 0).mean()*100:.0f}% a ganhar, {g['ret'].mean()*100:+.1f}% em média")
    return "\n".join(linhas)


def em_pausa(hist: pd.DataFrame, hoje: str, dias_uteis: int) -> set[str]:
    """Ações fechadas há menos de `dias_uteis` dias úteis: não voltam a dar sinal logo a seguir."""
    if not dias_uteis or hist.empty:
        return set()
    limite = (pd.Timestamp(hoje) - pd.offsets.BDay(dias_uteis)).strftime("%Y-%m-%d")
    f = hist[(hist["estado"] != "aberto") & (hist["data_fecho"].astype(str) > limite)]
    return set(f["ticker"])
