"""Testes contra a sorte: o rácio de Sharpe deflacionado (Bailey e López de Prado, 2014).

Quantas mais regras se testam, mais provável é uma parecer boa por acaso. O Sharpe deflacionado dá a
probabilidade de o Sharpe verdadeiro ser maior que zero, descontando:
  - o número de tentativas (regras e variantes já testadas no projeto, data/tentativas.csv);
  - a variação dos Sharpes entre essas tentativas;
  - caudas gordas e assimetria dos retornos.
Acima de 0,95 = provavelmente não é sorte.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

EULER = 0.5772156649


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _phi_inv(p: float) -> float:
    """Inversa da normal (Acklam), sem scipy."""
    a = [-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2, 1.383577518672690e2,
         -3.066479806614716e1, 2.506628277459239e0]
    b = [-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2, 6.680131188771972e1,
         -1.328068155288572e1]
    c = [-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838e0, -2.549732539343734e0,
         4.374664141464968e0, 2.938163982698783e0]
    d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996e0, 3.754408661907416e0]
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - 0.02425:
        return -_phi_inv(1 - p)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def sharpe_esperado_maximo(tentativas: int, var_sharpe: float) -> float:
    """Sharpe que a melhor de N tentativas sem vantagem nenhuma teria só por sorte."""
    n = max(int(tentativas), 2)
    return math.sqrt(max(var_sharpe, 0.0)) * ((1 - EULER) * _phi_inv(1 - 1 / n)
                                              + EULER * _phi_inv(1 - 1 / (n * math.e)))


def sharpe_deflacionado(retornos, tentativas: int, var_sharpe: float) -> dict:
    """{sharpe, limiar, prob, n}: prob = probabilidade de a vantagem não ser sorte (0 a 1).
    retornos = um por trade ou por dia (Sharpe não anualizado)."""
    r = pd.Series(retornos, dtype=float).dropna().to_numpy()
    n = len(r)
    if n < 10 or r.std(ddof=1) == 0:
        return {"sharpe": 0.0, "limiar": 0.0, "prob": 0.0, "n": n}
    sr = r.mean() / r.std(ddof=1)
    x = (r - r.mean()) / r.std(ddof=0)
    assim, curt = float((x ** 3).mean()), float((x ** 4).mean())
    sr0 = sharpe_esperado_maximo(tentativas, var_sharpe)
    den = 1 - assim * sr + (curt - 1) / 4 * sr * sr
    prob = _phi((sr - sr0) * math.sqrt(n - 1) / math.sqrt(max(den, 1e-9)))
    return {"sharpe": float(sr), "limiar": float(sr0), "prob": float(prob), "n": n}


def tentativas(caminho: Path) -> int:
    """Total de regras e variantes já testadas (data/tentativas.csv, coluna variantes)."""
    try:
        return int(pd.read_csv(caminho)["variantes"].sum())
    except Exception:  # noqa: BLE001
        return 300


def var_sharpes(grupos: dict) -> float:
    """Variação dos Sharpes (não anualizados) entre as tentativas que temos à mão."""
    s = [np.mean(v) / np.std(v, ddof=1) for v in grupos.values() if len(v) > 10 and np.std(v, ddof=1) > 0]
    return float(np.var(s, ddof=1)) if len(s) > 1 else 0.0
