"""Descarrega preços diários (Yahoo Finance) e aplica o filtro de liquidez."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

COLUNAS = ["data", "ticker", "open", "high", "low", "close", "volume"]

# Moeda -> par do Yahoo com a taxa "1 EUR = x moeda".
PARES_FX = {"USD": "EURUSD=X", "GBp": "EURGBP=X", "CHF": "EURCHF=X", "SEK": "EURSEK=X"}


def _para_formato_longo(bruto: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    if bruto is None or bruto.empty:
        return pd.DataFrame(columns=COLUNAS)
    if not isinstance(bruto.columns, pd.MultiIndex):  # só um ticker
        bruto = pd.concat({tickers[0]: bruto}, axis=1)
    df = bruto.stack(level=0, future_stack=True).reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    df = df.rename(columns={"date": "data", "level_1": "ticker", "price": "ticker"})
    df = df[[c for c in COLUNAS if c in df.columns]].dropna(subset=["close"])
    return df


def descarregar(tickers: list[str], periodo: str = "2y", lote: int = 200) -> pd.DataFrame:
    import yfinance as yf

    partes = []
    for i in range(0, len(tickers), lote):
        grupo = tickers[i : i + lote]
        log.info("Preços %d-%d de %d", i + 1, i + len(grupo), len(tickers))
        for tentativa in range(3):  # o Yahoo por vezes limita pedidos; espera e tenta de novo
            try:
                bruto = yf.download(
                    grupo, period=periodo, interval="1d", group_by="ticker",
                    auto_adjust=True, threads=True, progress=False,
                )
                partes.append(_para_formato_longo(bruto, grupo))
                break
            except Exception as e:
                log.warning("Lote %d falhou (%s), tentativa %d", i, e, tentativa + 1)
                time.sleep(10 * (tentativa + 1))
    if not partes:
        return pd.DataFrame(columns=COLUNAS)
    return pd.concat(partes, ignore_index=True)


def atuais(tickers: list[str]) -> dict[str, float]:
    """Último preço de cada ação (velas de 5 min de hoje; fora de horas, o último fecho)."""
    import yfinance as yf

    if not tickers:
        return {}
    out: dict[str, float] = {}
    try:
        bruto = yf.download(tickers, period="5d", interval="5m", group_by="ticker",
                            auto_adjust=True, threads=True, progress=False)
    except Exception as e:  # noqa: BLE001
        log.warning("Cotações atuais falharam: %s", e)
        return out
    for t in tickers:
        serie = None
        for chave in ((t, "Close"), ("Close", t), "Close"):
            try:
                serie = bruto[chave]
                break
            except KeyError:
                continue
        if serie is None:
            continue
        serie = pd.Series(serie.squeeze() if hasattr(serie, "squeeze") else serie).dropna()
        if len(serie):
            out[t] = float(serie.iloc[-1])
    return out


def taxas_eur() -> dict[str, float]:
    """Quantas unidades de cada moeda vale 1 EUR (último fecho)."""
    import yfinance as yf

    taxas = {"EUR": 1.0}
    bruto = yf.download(list(PARES_FX.values()), period="5d", progress=False, auto_adjust=True)
    for moeda, par in PARES_FX.items():
        try:
            taxas[moeda] = float(bruto["Close"][par].dropna().iloc[-1])
        except Exception:
            log.warning("Sem taxa para %s", moeda)
    if "GBp" in taxas:
        taxas["GBp"] *= 100  # preços de Londres vêm em pence
    return taxas


def filtrar_liquidez(precos: pd.DataFrame, universo: pd.DataFrame, taxas: dict[str, float],
                     cfg: dict) -> pd.DataFrame:
    """Mantém ações com preço, volume em EUR e histórico suficientes."""
    moeda = universo.set_index("ticker")["moeda"]
    p = precos.sort_values(["ticker", "data"])
    g = p.groupby("ticker")
    resumo = pd.DataFrame({
        "dias": g.size(),
        "ultimo_preco": g["close"].last(),
        "valor_medio_20d": g.apply(
            lambda d: (d["close"] * d["volume"]).tail(20).mean(), include_groups=False
        ),
    })
    resumo["moeda"] = moeda.reindex(resumo.index).fillna("USD")
    resumo["taxa"] = resumo["moeda"].map(taxas)
    resumo["valor_medio_20d_eur"] = resumo["valor_medio_20d"] / resumo["taxa"]
    preco_local = resumo["ultimo_preco"].where(resumo["moeda"] != "GBp", resumo["ultimo_preco"] / 100)
    ok = (
        (resumo["dias"] >= cfg["dias_historico_minimo"])
        & (preco_local >= cfg["preco_minimo"])
        & (resumo["valor_medio_20d_eur"] >= cfg["volume_medio_minimo_eur"])
    )
    out = resumo[ok].drop(columns=["taxa"]).reset_index()
    return out.sort_values("valor_medio_20d_eur", ascending=False).reset_index(drop=True)


def guardar(precos: pd.DataFrame, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    precos.to_parquet(caminho, index=False)


def juntar(antigos: pd.DataFrame, novos: pd.DataFrame) -> pd.DataFrame:
    """Junta preços novos aos guardados; o valor mais recente de cada dia ganha."""
    df = pd.concat([antigos, novos], ignore_index=True)
    df["data"] = pd.to_datetime(df["data"]).dt.tz_localize(None).dt.normalize()
    return df.drop_duplicates(["data", "ticker"], keep="last").sort_values(["ticker", "data"]) \
        .reset_index(drop=True)


# Horário de negociação (UTC) para estimar o volume do dia ainda em curso.
_HORARIO_EUA = (13.5, 20.0)
_HORARIO_EUROPA = (7.0, 15.5)


def _horario(ticker: str) -> tuple[float, float]:
    return _HORARIO_EUROPA if "." in ticker else _HORARIO_EUA


def projetar_volume_hoje(precos: pd.DataFrame, agora: pd.Timestamp) -> pd.DataFrame:
    """Durante a sessão, o volume de hoje está incompleto. Projeta-o para o dia
    inteiro, para que o rácio de volume não fique artificialmente baixo."""
    agora = pd.Timestamp(agora).tz_localize(None) if pd.Timestamp(agora).tzinfo else pd.Timestamp(agora)
    hoje = agora.normalize()
    hora = agora.hour + agora.minute / 60
    p = precos.copy()
    mask = pd.to_datetime(p["data"]).dt.normalize() == hoje
    for ticker in p.loc[mask, "ticker"].unique():
        ini, fim = _horario(ticker)
        if hora >= fim:
            continue
        frac = max((hora - ini) / (fim - ini), 0.1)
        m = mask & (p["ticker"] == ticker)
        p.loc[m, "volume"] = p.loc[m, "volume"] / frac
    return p
