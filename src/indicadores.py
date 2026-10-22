"""Indicadores técnicos calculados por ação a partir dos preços diários."""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    ganho = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    perda = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = ganho / perda.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0).where(perda.notna())


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def calcular(df: pd.DataFrame) -> pd.DataFrame:
    """df: preços de UMA ação ordenados por data. Devolve df com indicadores."""
    d = df.copy()
    c = d["close"]
    d["sma20"] = c.rolling(20).mean()
    d["sma50"] = c.rolling(50).mean()
    d["sma200"] = c.rolling(200).mean()
    d["rsi14"] = rsi(c, 14)
    d["rsi2"] = rsi(c, 2)
    d["atr14"] = atr(d, 14)
    d["atr_pct"] = d["atr14"] / c
    d["vol_ratio"] = d["volume"] / d["volume"].rolling(20).mean().shift()
    d["max20"] = d["high"].rolling(20).max().shift()   # máximo dos 20 dias ANTERIORES
    d["min20"] = d["low"].rolling(20).min().shift()
    d["ret5"] = c.pct_change(5)
    d["ret20"] = c.pct_change(20)
    d["gap"] = d["open"] / c.shift() - 1
    d["ret_dia"] = c.pct_change()
    amplitude = (d["high"] - d["low"]).replace(0, np.nan)
    d["pos_fecho"] = ((c - d["low"]) / amplitude).fillna(0.5)   # 0 = fechou no mínimo, 1 = no máximo
    # Choque de baixa nos últimos 5 dias: gap ou queda forte com volume alto (quase sempre notícia).
    choque = ((d["gap"] <= -0.04) | (d["ret_dia"] <= -0.06)) & (d["vol_ratio"] >= 2)
    d["choque_baixa_5d"] = choque.astype(float).rolling(5, min_periods=1).max()
    return d
