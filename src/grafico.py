"""Gráfico de um sinal: velas dos últimos meses com a entrada, o objetivo e o stop marcados.

Vai como imagem no próprio sinal do Telegram (a mesma imagem serve para todas as pessoas).
"""
from __future__ import annotations

import io

import pandas as pd

VERDE, VERMELHO, AZUL, CINZA = "#16a34a", "#dc2626", "#2563eb", "#6b7280"


def _fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def grafico_sinal(precos: pd.DataFrame, s: dict, sessoes: int = 60) -> bytes | None:
    """PNG com as últimas `sessoes` velas diárias de `precos` (colunas data, open, high, low, close) e as
    linhas do sinal `s` (entrada, objetivo, stop). None se não houver dados suficientes."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = precos.dropna(subset=["close"]).sort_values("data").tail(sessoes)
    if len(d) < 10:
        return None
    d = d.assign(open=d["open"].fillna(d["close"]), high=d["high"].fillna(d["close"]),
                 low=d["low"].fillna(d["close"])).reset_index(drop=True)
    entrada = float(s["entrada"])
    obj = s.get("objetivo")
    obj = float(obj) if obj is not None and pd.notna(obj) else None
    stop = s.get("stop")
    stop = float(stop) if stop is not None and pd.notna(stop) else None
    futuro = int(s.get("max_dias") or 5) + 1

    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=110)
    fig.patch.set_facecolor("white")
    x = range(len(d))
    for i, r in d.iterrows():
        cor = VERDE if r["close"] >= r["open"] else VERMELHO
        ax.vlines(i, r["low"], r["high"], color=cor, linewidth=0.8)
        ax.bar(i, max(abs(r["close"] - r["open"]), entrada * 0.0008), bottom=min(r["open"], r["close"]),
               width=0.7, color=cor, linewidth=0)
    ultimo = len(d) - 1
    zona = (ultimo + 0.5, ultimo + futuro)
    if obj is not None:
        ax.fill_between(zona, entrada, obj, color=VERDE, alpha=0.12, linewidth=0)
        ax.hlines(obj, 0, zona[1], color=VERDE, linewidth=1.2)
        ax.text(zona[1], obj, f" Objetivo {_fmt(obj)}", color=VERDE, va="center", fontsize=9, fontweight="bold")
    if stop is not None:
        ax.fill_between(zona, stop, entrada, color=VERMELHO, alpha=0.12, linewidth=0)
        ax.hlines(stop, 0, zona[1], color=VERMELHO, linewidth=1.2)
        ax.text(zona[1], stop, f" Stop {_fmt(stop)}", color=VERMELHO, va="center", fontsize=9, fontweight="bold")
    ax.hlines(entrada, 0, zona[1], color=AZUL, linewidth=1.2, linestyles="--")
    ax.text(zona[1], entrada, f" Entrada {_fmt(entrada)}", color=AZUL, va="center", fontsize=9, fontweight="bold")

    marcas = list(range(0, len(d), max(len(d) // 6, 1)))
    ax.set_xticks(marcas)
    ax.set_xticklabels([pd.Timestamp(d.loc[i, "data"]).strftime("%d/%m") for i in marcas], fontsize=8, color=CINZA)
    ax.tick_params(axis="y", labelsize=8, colors=CINZA)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: _fmt(v)))
    ax.set_xlim(-1, zona[1] + futuro * 0.2)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color("#d1d5db")
    ax.grid(axis="y", color="#f3f4f6", linewidth=0.8)
    ax.set_title(f"{s['ticker']} · últimos {len(d)} dias", loc="left", fontsize=11, fontweight="bold", color="#111827")
    fig.subplots_adjust(left=0.07, right=0.84, top=0.9, bottom=0.1)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
