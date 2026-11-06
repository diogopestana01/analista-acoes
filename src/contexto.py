"""Contexto para os setups que olham para além da própria ação (estudo passo3v_avancado.py, 10 anos).

  vix         fecho do VIX (o "medo" do mercado), do Yahoo
  setor       setor de cada ação dos EUA (screener da Nasdaq; guardado em data/setores.csv, renovado à semana)
  rel_setor5  quanto a ação subiu ou caiu em 5 dias a mais do que a média do seu setor
  rank252     posição da subida de 12 meses entre as ações dos EUA (1 = a que mais subiu)
Sem contexto (fontes em baixo), estes setups simplesmente não dão sinal.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)
SCREENER = "https://api.nasdaq.com/api/screener/stocks"


def vix(periodo: str = "2y") -> pd.Series:
    """Fecho diário do VIX (índice: data sem fuso). Vazio se falhar."""
    try:
        import yfinance as yf
        c = yf.download("^VIX", period=periodo, interval="1d", auto_adjust=True, progress=False,
                        threads=False)["Close"].squeeze().dropna()
        c.index = pd.to_datetime(c.index).tz_localize(None).normalize()
        return c.astype(float)
    except Exception as e:  # noqa: BLE001
        log.warning("VIX não lido: %s", e)
        return pd.Series(dtype=float)


def setores(cache: Path, max_dias: int = 7) -> pd.Series:
    """ticker -> setor. Usa a cache se tiver menos de `max_dias` (coluna 'atualizado'); se o screener falhar,
    a cache velha."""
    velho = None
    if cache.exists():
        try:
            velho = pd.read_csv(cache, dtype=str, keep_default_na=False)
            quando = pd.to_datetime(velho.get("atualizado", pd.Series(dtype=str)), errors="coerce").max()
            # a data vai dentro do ficheiro: no GitHub o checkout muda a data do ficheiro
            if pd.notna(quando) and pd.Timestamp.now() - quando < pd.Timedelta(days=max_dias) and len(velho) > 100:
                return _serie(velho)
        except (ValueError, OSError):
            velho = None
    try:
        from . import resultados
        r = requests.get(SCREENER, params={"tableonly": "true", "limit": 10000, "download": "true"},
                         headers=resultados.CABECALHOS, timeout=60)
        r.raise_for_status()
        s = pd.DataFrame(r.json()["data"]["rows"])[["symbol", "sector"]].rename(columns={"symbol": "ticker"})
        s["ticker"] = s["ticker"].str.strip().str.upper().str.replace("/", "-").str.replace(".", "-")
        s.assign(atualizado=f"{pd.Timestamp.now():%Y-%m-%d}").to_csv(cache, index=False)
        return _serie(s)
    except Exception as e:  # noqa: BLE001
        log.warning("Setores da Nasdaq não lidos (%s); %s", e, "a usar a cache" if velho is not None else "sem setores")
        return _serie(velho) if velho is not None else pd.Series(dtype=str)


def _serie(s: pd.DataFrame) -> pd.Series:
    s = s[s["sector"].astype(str).str.len() > 0]
    return s.drop_duplicates("ticker").set_index("ticker")["sector"]


def _datas(x: pd.Series) -> pd.Series:
    x = pd.to_datetime(x)
    if getattr(x.dt, "tz", None) is not None:
        x = x.dt.tz_localize(None)
    return x.dt.normalize()


def tabela(precos: pd.DataFrame, vix_s: pd.Series | None, setor: pd.Series | None) -> pd.DataFrame:
    """Por ação e dia (índice = posição em `precos` ordenado por ticker e data): vix, rel_setor5, rank252.
    Força contra o setor e de 12 meses só nas ações dos EUA; setores com menos de 5 ações nesse dia não contam."""
    p = precos.sort_values(["ticker", "data"])[["ticker", "data", "close"]].reset_index(drop=True)
    p["dia"] = _datas(p["data"])
    eua = ~p["ticker"].str.contains(".", regex=False)
    c = p.groupby("ticker")["close"]
    p["ret5"], p["ret252"] = c.pct_change(5), c.pct_change(252)
    p.loc[~eua, ["ret5", "ret252"]] = float("nan")
    p["setor"] = p["ticker"].map(setor) if setor is not None and len(setor) else None
    g = p.groupby(["dia", "setor"])["ret5"]          # sem setor (NaN) fica de fora: dá NaN
    p["rel_setor5"] = (p["ret5"] - g.transform("mean")).where(g.transform("count") >= 5)
    p["rank252"] = p.groupby("dia")["ret252"].rank(pct=True)
    v = None if vix_s is None or vix_s.empty else vix_s.sort_index()
    p["vix"] = float("nan") if v is None else v.reindex(p["dia"], method="ffill").to_numpy()
    return p[["ticker", "dia", "vix", "rel_setor5", "rank252"]]


def juntar(d: pd.DataFrame, t: pd.DataFrame) -> pd.DataFrame:
    """Põe as colunas de `t` (só as linhas desta ação) na tabela de indicadores `d`, pelo dia."""
    x = t.drop(columns="ticker").drop_duplicates("dia").set_index("dia")
    dias = _datas(d["data"])
    for col in ("vix", "rel_setor5", "rank252"):
        d[col] = x[col].reindex(dias).to_numpy()
    return d
