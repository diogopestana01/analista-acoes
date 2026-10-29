"""Lista dos sinais em aberto, reavaliada a cada corrida (de 10 em 10 min, com o preço de agora).

Cada sinal tem um estado:
  comprar     o preço ainda está abaixo do limite de compra (não fez mais de metade do caminho até ao
              objetivo) e o prazo ainda não acabou
  andamento   já passou o limite: quem comprou mantém, quem não comprou já não entra
Os que bateram no stop ou no objetivo saem da lista (o registo fecha-os na corrida seguinte).

E uma força atualizada: quanto um sinal destes ainda costuma render daqui até à saída, dado o que já
subiu e os dias que já passaram ("valor que falta", src/trocas.py, medido no backtest). Um sinal que já
subiu 3% de 5% vale menos do que quando apareceu. No dia do sinal é a média do setup; no último dia
do prazo é zero (sai ao fecho).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import trocas

FUGIU = 0.5   # já percorreu mais de metade do caminho até ao objetivo -> já não se compra


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(v) else v


def limite(entrada, objetivo, stop) -> float | None:
    """Preço máximo de compra: entrada + metade do caminho até ao objetivo. Sem objetivo (saída pela
    regra), conta como objetivo a mesma distância do stop para cima."""
    e, o, st = _num(entrada), _num(objetivo), _num(stop)
    alvo = o or (e + (e - st) if e and st else None)
    return round(e + FUGIU * (alvo - e), 2) if e and alvo and alvo > e else None


def retorno(s: dict) -> float | None:
    for k in ("retorno_esperado_liquido", "retorno_esperado"):
        v = _num(s.get(k))
        if v is not None:
            return v
    return None


def avaliar(hist: pd.DataFrame | None, precos: dict, hoje: str, modelo: dict | None, cfg: dict) -> list[dict]:
    """Sinais de compra em aberto, cada um com o preço de agora, estado e força; do mais forte ao mais fraco."""
    if hist is None or hist.empty:
        return []
    h = hist[(hist["estado"] == "aberto") & (hist["direcao"] == "compra")]
    out = []
    for r in h.to_dict("records"):
        r = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in r.items()}
        t, data = r["ticker"], str(r["data_sinal"])[:10]
        e, o, st = _num(r.get("entrada")), _num(r.get("objetivo")), _num(r.get("stop"))
        if not e:
            continue
        p = _num(precos.get(t))
        if (p is not None and st and p <= st) or (p is not None and o and p >= o):
            continue
        preco = p or e
        dias = int(np.busday_count(data, hoje)) if hoje >= data else 0
        max_dias = int(_num(r.get("max_dias")) or 5)
        lim = limite(e, o, st)
        estado = "comprar" if (lim is None or preco <= lim) and dias < max_dias else "andamento"
        setup = r.get("setup") if isinstance(r.get("setup"), str) else ""
        forca = None
        if modelo:
            if dias >= max_dias:
                forca = 0.0   # último dia do prazo: sai ao fecho de hoje
            elif dias < 1:
                forca = modelo["media"].get(setup)
            else:
                atr = trocas.atr_pct(setup, e, st, cfg) if st else None
                forca = trocas.valor_que_falta(setup, dias, preco / e - 1, atr, modelo)
        out.append({**r, "ticker": t, "data_sinal": data, "entrada_sinal": e, "entrada": preco, "preco": preco,
                    "sem_preco": p is None, "ganho": preco / e - 1, "dias_usados": dias, "max_dias": max_dias,
                    "limite": lim, "estado_lista": estado, "forca": forca})
    out.sort(key=lambda s: (s["forca"] is None, -(s["forca"] or 0.0), -(retorno(s) or -1.0)))
    return out
