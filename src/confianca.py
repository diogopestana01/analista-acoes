"""Confiança calibrada: probabilidade de um sinal acabar a ganhar, aprendida no backtest.

Regressão logística simples (só numpy) sobre características que se conhecem no momento
do sinal. É treinada por passo3d_confianca.py, que a valida no período mais recente antes
de a guardar em data/modelo_confianca.json. Sem esse ficheiro o modelo usa a pontuação antiga.

"Confiança 62" quer dizer: dos sinais parecidos no histórico, 62% acabaram a ganhar.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

CONTINUAS = ["atr_pct", "vol_ratio", "rsi14", "rsi_semanal", "ret5", "ret20", "dist_sma50", "amplitude_mercado"]
# Limites para os valores extremos não dominarem (ex.: volume 50x o normal).
LIMITES = {"atr_pct": (0.005, 0.12), "vol_ratio": (0.2, 6), "ret5": (-0.3, 0.4), "ret20": (-0.4, 0.8),
           "dist_sma50": (-0.3, 0.6), "rsi14": (0, 100), "rsi_semanal": (0, 100), "amplitude_mercado": (0, 1)}
CAMINHO = Path(__file__).resolve().parent.parent / "data" / "modelo_confianca.json"


def matriz(df: pd.DataFrame, modelo: dict) -> np.ndarray:
    """Características normalizadas + uma coluna por setup + região EUA."""
    cols = []
    for c in CONTINUAS:
        lo, hi = LIMITES[c]
        x = pd.to_numeric(df.get(c, pd.Series(np.nan, index=df.index)), errors="coerce").clip(lo, hi)
        x = x.fillna(modelo["media"][c])
        cols.append((x - modelo["media"][c]) / modelo["desvio"][c])
    for s in modelo["setups"]:
        cols.append((df["setup"] == s).astype(float))
    cols.append((df.get("regiao", pd.Series("eua", index=df.index)) == "eua").astype(float))
    return np.column_stack([np.asarray(c, float) for c in cols])


def treinar(df: pd.DataFrame, ganhou: pd.Series, setups: list[str], l2: float = 1.0,
            iteracoes: int = 3000, passo: float = 0.5) -> dict:
    """df com as CONTINUAS, setup e regiao; ganhou 0/1. Devolve o modelo (dicionário JSON)."""
    modelo = {"setups": list(setups), "media": {}, "desvio": {}}
    for c in CONTINUAS:
        lo, hi = LIMITES[c]
        x = pd.to_numeric(df[c], errors="coerce").clip(lo, hi)
        modelo["media"][c] = float(x.mean()) if x.notna().any() else 0.0
        modelo["desvio"][c] = float(x.std()) if x.notna().sum() > 1 and x.std() > 0 else 1.0
    X = matriz(df, modelo)
    y = np.asarray(ganhou, float)
    w, b, n = np.zeros(X.shape[1]), float(np.log(y.mean() / (1 - y.mean()))), len(y)
    for _ in range(iteracoes):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        w -= passo * (X.T @ (p - y) / n + l2 * w / n)
        b -= passo * float(np.mean(p - y))
    modelo["pesos"] = w.round(5).tolist()
    modelo["intercepto"] = round(b, 5)
    return modelo


def prever(df: pd.DataFrame, modelo: dict) -> np.ndarray:
    """Probabilidade de ganhar (0-1) para cada linha."""
    if df.empty:
        return np.array([])
    z = matriz(df, modelo) @ np.asarray(modelo["pesos"]) + modelo["intercepto"]
    return 1 / (1 + np.exp(-z))


def carregar(caminho: Path = CAMINHO) -> dict | None:
    try:
        return json.loads(Path(caminho).read_text())
    except (FileNotFoundError, ValueError):
        return None
